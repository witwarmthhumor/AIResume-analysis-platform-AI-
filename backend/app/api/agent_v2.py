"""Agent v2（LangGraph 试点）API：一键求职准备任务的发起/重连/审批/重试/放弃。

前缀 /api/agent-v2，与 v1（/api/agent）并存（PRD §4.1）。所有查询走
deps.owner_clause / matches_owner 归属隔离；agent_v2_enabled=false 时接口 503。
事件协议见 PRD §7.3；断线重连先发 DB 快照再订阅事件总线（D1）。
"""

import json
import threading
import time
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.admin import _admin_only
from app.api.auth_deps import get_optional_current_user
from app.api.deps import (
    enforce_daily_limit,
    get_anonymous_id,
    get_owned_chat_session,
    matches_owner,
    owner_clause,
)
from app.core.config import settings
from app.db.session import get_db
from app.models.agent_v2 import AgentApproval, AgentRun, AgentSpan, AuditLog
from app.models.user import User
from app.services.agent_v2 import event_bus
from app.services.agent_v2.graph import (
    APPROVAL_SUMMARY,
    resume_job_prep,
    run_job_prep,
)
from app.services.usage_service import write_usage

router = APIRouter(prefix="/api/agent-v2", tags=["agent_v2"])

_TERMINAL_EVENTS = {"done", "fatal", "stream_closed"}
# v2 run 的记账口径（usage_logs.action_type）：限额与用量统计都按它聚合
V2_RUN_ACTION = "agent_run_v2"
# SSE 无事件防御性断流预算：0.05s × 1800 = 90s（客户端可重连；
# 90s 是为 CI 全新库冷启动建 checkpoint 基础设施留的余量，实测踩坑值）
_SSE_IDLE_TICKS = 1800


def _sse_event_stream(run_id: int, buf, pre_events: Iterable[str]):
    """SSE 事件循环公共实现（create/stream/retry 三端点共用）。

    先发送 pre_events（调用方预构造的 meta/snapshot 等首帧），再消费事件总线
    缓冲直至终态事件；_SSE_IDLE_TICKS 内无事件则发 stream_closed 防御性断流。
    结束（含异常）时退订。buf 中的事件是订阅者私有拷贝，pop 消费安全。
    """

    def event_stream():
        try:
            yield from pre_events
            idle = 0
            while True:
                try:
                    event = buf.popleft()
                except IndexError:
                    time.sleep(0.05)
                    idle += 1
                    if idle > _SSE_IDLE_TICKS:
                        yield _sse("stream_closed", {"reason": "timeout"})
                        return
                    continue
                idle = 0
                etype = event.pop("type")
                yield _sse(etype, event)
                if etype in _TERMINAL_EVENTS:
                    return
        finally:
            event_bus.unsubscribe(run_id, buf)

    return event_stream()


def _require_enabled() -> None:
    if not settings.agent_v2_enabled:
        raise HTTPException(
            503, "任务功能已关闭，请直接在对话框提问（AI 客服不受影响）"
        )


def _require_enabled_dep() -> None:
    _require_enabled()


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


class RunIn(BaseModel):
    """发起 run 的入参；resume_hint 预留（当前定位本人最新简历）。"""

    jd_text: str | None = Field(default=None, max_length=20000)
    topic: str | None = Field(default=None, max_length=200)
    position_type: str | None = Field(default=None, max_length=20)
    resume_hint: str | None = Field(default=None, max_length=200)
    session_id: int | None = None


def _get_owned_run(
    db: Session, run_id: int, user: User | None, anonymous_id: str
) -> AgentRun:
    run = db.get(AgentRun, run_id)
    if run is None or not matches_owner(run, user, anonymous_id):
        raise HTTPException(404, "任务不存在")
    return run


def _pending_approval(db: Session, run_id: int) -> AgentApproval | None:
    """取有效待审批单；TTL 到点则作废审批单并把 run 终止为 failed。

    过期后 approve 一律 410、图线程又已挂起无处续跑——不终止 run 的话任务会
    永远卡在 waiting_approval（列表仍显示可恢复，但已无审批卡可用）。
    终止走条件更新：与 approve 的乐观锁互斥，并发时只有一方能挪走 run。
    """
    approval = db.scalar(
        select(AgentApproval)
        .where(AgentApproval.run_id == run_id, AgentApproval.status == "pending")
        .order_by(AgentApproval.id.desc())
    )
    if approval is None:
        return None
    if approval.expires_at and approval.expires_at < datetime.now(timezone.utc):
        approval.status = "expired"
        db.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id, AgentRun.status == "waiting_approval")
            .values(
                status="failed",
                error="审批超时，任务已自动终止；可重新发起或从失败节点重试。",
                current_node="hitl_gate",
                completed_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
        return None
    return approval


def _audit(
    db: Session,
    *,
    action: str,
    user: User | None,
    anonymous_id: str | None,
    ip: str | None,
    run_id: int | None = None,
    trace_id: str | None = None,
    after: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_user_id=user.id if user else None,
            actor_anonymous_id=None if user else anonymous_id,
            ip=ip,
            action=action,
            target_type="agent_run",
            target_id=str(run_id) if run_id else None,
            after_snapshot=json.dumps(after, ensure_ascii=False)[:500]
            if after
            else None,
            run_id=run_id,
            trace_id=trace_id,
        )
    )
    db.commit()


@router.post("/runs", status_code=201)
def create_run(
    body: RunIn,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
):
    """发起一键求职准备：建 run → 后台线程跑图 → 返回 SSE 流（事件含 plan/节点流转/结果）。"""
    _require_enabled()
    if body.session_id is not None:
        # 会话归属校验：非本人会话一律 404（与 chat/playground 同口径），
        # 防脏数据/越权关联；校验在限额记账之前，失败请求不计额度
        get_owned_chat_session(db, body.session_id, user, anonymous_id)
    # v2 独立限额（daily_agent_v2_run_limit，与 v1 的 agent/analysis 口径分开）；
    # 按发起次数计（含后续失败的 run），lock→count→记账 与 run 落库同事务串行化
    enforce_daily_limit(
        db,
        anonymous_id,
        settings.daily_agent_v2_run_limit,
        action_type=V2_RUN_ACTION,
        user_id=user.id if user else None,
    )
    write_usage(
        db,
        anonymous_id=anonymous_id,
        user_id=user.id if user else None,
        action_type=V2_RUN_ACTION,
        model_name=None,
        tokens_total=0,
        ip_address=request.client.host if request.client else None,
    )
    trace_id = uuid.uuid4().hex
    run = AgentRun(
        user_id=user.id if user else None,
        anonymous_id=None if user else anonymous_id,
        trace_id=trace_id,
        thread_id="pending",  # flush 拿到 id 后回填 run-{id}
        input_json=json.dumps(
            {
                "jd_text": body.jd_text,
                "topic": body.topic,
                "position_type": body.position_type,
                "resume_hint": body.resume_hint,
            },
            ensure_ascii=False,
        ),
        session_id=body.session_id,
        status="planning",
    )
    db.add(run)
    db.flush()
    run.thread_id = f"run-{run.id}"
    db.commit()
    db.refresh(run)
    # 必须结束本次请求事务（refresh 后会话处于 idle in transaction）：
    # 流式响应期间依赖不会收尾，图线程 setup() 的 CREATE INDEX CONCURRENTLY
    # 会等在途事务结束 → 与本会话互等死锁（CI 冷库实测踩坑）
    db.commit()

    # 订阅必须先于线程启动：否则线程早期发布的事件会丢失（D1）
    buf = event_bus.subscribe_with_replay(run.id)
    thread = threading.Thread(
        target=run_job_prep,
        args=(
            run.id,
            trace_id,
            user.id if user else None,
            None if user else anonymous_id,
            {
                "jd_text": body.jd_text or "",
                "topic": body.topic or "",
                "position_type": body.position_type or "",
                "session_id": body.session_id,
            },
        ),
        daemon=True,
    )
    thread.start()

    return StreamingResponse(
        _sse_event_stream(
            run.id,
            buf,
            [
                _sse(
                    "meta",
                    {
                        "run_id": run.id,
                        "trace_id": trace_id,
                        "thread_id": run.thread_id,
                        "status": "running",
                    },
                )
            ],
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs")
def list_runs(
    page: int = 1,
    page_size: int = 10,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
):
    """本人 run 列表（归属隔离，SQL 分页，不整表载入）。"""
    limit = min(50, max(1, page_size))
    offset = (max(1, page) - 1) * limit
    owner = owner_clause(AgentRun, user, anonymous_id)
    total = db.scalar(select(func.count()).select_from(AgentRun).where(owner))
    rows = db.scalars(
        select(AgentRun)
        .where(owner)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "status": r.status,
                "run_type": r.run_type,
                "current_node": r.current_node,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "tokens_total": r.tokens_total,
                "error": r.error,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/runs/{run_id}")
def get_run(
    run_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
):
    """run 详情：状态 / plan / 最终产物 / 待审批信息（断点续跑的审批卡数据源）。"""
    run = _get_owned_run(db, run_id, user, anonymous_id)
    approval = _pending_approval(db, run.id)
    if run.status == "waiting_approval":
        db.refresh(run)  # 审批超时会被 _pending_approval 终止为 failed，重读拿最新状态
    spans = db.scalars(
        select(AgentSpan).where(AgentSpan.run_id == run.id).order_by(AgentSpan.id.asc())
    ).all()
    return {
        "id": run.id,
        "status": run.status,
        "plan": json.loads(run.plan_json) if run.plan_json else [],
        "output": json.loads(run.output_json) if run.output_json else None,
        "error": run.error,
        "tokens_total": run.tokens_total,
        "duration_ms": run.duration_ms,
        "approval": (
            {
                "approval_id": approval.id,
                "action_key": approval.action_key,
                "summary": APPROVAL_SUMMARY,
                "payload": json.loads(approval.payload_json or "{}"),
                "expires_at": approval.expires_at.isoformat()
                if approval.expires_at
                else None,
            }
            if approval
            else None
        ),
        "spans": [
            {
                "name": sp.name,
                "span_type": sp.span_type,
                "status": sp.status,
                "attempt": sp.attempt,
                "duration_ms": sp.duration_ms,
                "error_type": sp.error_type,
            }
            for sp in spans
        ],
    }


@router.get("/runs/{run_id}/stream")
def stream_run(
    run_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
):
    """SSE 重连：先发 DB 快照（状态/审批/产物），再订阅事件总线收增量（D1）。"""
    run = _get_owned_run(db, run_id, user, anonymous_id)
    # 快照与待审批单必须在进入流式响应前查完并结束请求事务：
    # 流式期间 FastAPI 依赖不收尾，事务会一直 idle in transaction
    # （阻碍 vacuum；未来任何并发 DDL 会复现 CONCURRENTLY 死锁，实测踩坑）
    approval = _pending_approval(db, run.id)
    if run.status == "waiting_approval":
        db.refresh(run)  # 审批超时会被 _pending_approval 终止为 failed，重读拿最新状态
    snapshot = {
        "run_id": run.id,
        "status": run.status,
        "plan": json.loads(run.plan_json) if run.plan_json else [],
        "output": json.loads(run.output_json) if run.output_json else None,
        "error": run.error,
    }
    if approval and run.status == "waiting_approval":
        snapshot["approval"] = {
            "approval_id": approval.id,
            "action_key": approval.action_key,
            "summary": APPROVAL_SUMMARY,
            "expires_at": approval.expires_at.isoformat()
            if approval.expires_at
            else None,
        }
    pre: list[str] = [_sse("snapshot", snapshot)]
    if run.status in ("completed", "failed", "aborted"):
        pre.append(
            _sse(
                "done" if run.status == "completed" else "fatal",
                {"run_id": run.id, "status": run.status, "content": run.error},
            )
        )
    buf = event_bus.subscribe_with_replay(run.id)
    db.commit()  # 结束请求事务（订阅不依赖 db，先订后退订不丢事件）

    return StreamingResponse(
        _sse_event_stream(run.id, buf, pre),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/runs/{run_id}/approve", status_code=202)
def approve_run(
    run_id: int,
    body: dict,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
):
    """审批风险动作：approved → Command(resume) 续跑；rejected → 续跑但跳过副作用。"""
    run = _get_owned_run(db, run_id, user, anonymous_id)
    if run.status != "waiting_approval":
        raise HTTPException(409, "该任务当前没有待审批的动作")
    approval = _pending_approval(db, run.id)
    if approval is None:
        raise HTTPException(
            410, "审批已超时，任务已自动终止；可重新发起或从失败节点重试"
        )

    decision = (body or {}).get("decision")
    if decision not in ("approved", "rejected"):
        raise HTTPException(422, "decision 必须是 approved 或 rejected")

    # 条件更新乐观锁：approve 与 abort 可能并发（客户端双击/双标签页）。
    # 只有真正把 run 从 waiting_approval 挪走的请求才允许继续，
    # 输家（0 行受影响）拿到 409——避免审批留痕与 run 终态互相矛盾
    moved = db.execute(
        update(AgentRun)
        .where(AgentRun.id == run.id, AgentRun.status == "waiting_approval")
        .values(status="running")
    ).rowcount
    db.commit()
    if not moved:
        raise HTTPException(409, "该任务状态已变更（可能刚被审批或放弃），请刷新查看")

    approval.status = decision
    approval.decided_by = user.id if user else None
    approval.decided_at = datetime.now(timezone.utc)
    approval.decision_note = (body or {}).get("note")
    db.commit()
    _audit(
        db,
        action=f"approval_{decision}",
        user=user,
        anonymous_id=anonymous_id,
        ip=request.client.host if request.client else None,
        run_id=run.id,
        trace_id=run.trace_id,
        after={"action_key": approval.action_key, "note": approval.decision_note},
    )

    thread = threading.Thread(
        target=resume_job_prep,
        args=(
            run.id,
            run.trace_id,
            user.id if user else None,
            None if user else anonymous_id,
            {"approved": decision == "approved", "action_key": approval.action_key},
        ),
        daemon=True,
    )
    thread.start()
    return {"run_id": run.id, "status": "running"}


# 与 POST /runs 同款：直接返回 SSE 事件流，状态码由 StreamingResponse 决定（固定 200），
# 装饰器上的 status_code 对此无效——不要在此声明 202，OpenAPI 会与实际不符
@router.post("/runs/{run_id}/retry-node")
def retry_run(
    run_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
):
    """从失败节点重试（评审 D2 定稿语义）：新建 run，已完成产物注入初始 state，
    图按「产物已存在」跳过对应节点；retry 计新 run 额度。"""
    run = _get_owned_run(db, run_id, user, anonymous_id)
    if run.status != "failed":
        raise HTTPException(409, "仅失败的任务可以重试")
    _require_enabled()

    output = json.loads(run.output_json) if run.output_json else {}
    input_state = json.loads(run.input_json or "{}")
    input_state.update(
        {
            k: output[k]
            for k in ("match_report", "analysis_summary", "questions")
            if output.get(k) is not None
        }
    )
    if output.get("match_text"):
        input_state["match_text"] = output["match_text"]

    trace_id = uuid.uuid4().hex
    new_run = AgentRun(
        user_id=user.id if user else None,
        anonymous_id=None if user else anonymous_id,
        trace_id=trace_id,
        thread_id="pending",
        run_type=run.run_type,
        input_json=json.dumps(input_state, ensure_ascii=False),
        status="planning",
        session_id=run.session_id,
    )
    db.add(new_run)
    db.flush()
    new_run.thread_id = f"run-{new_run.id}"
    db.commit()
    db.refresh(new_run)

    buf = event_bus.subscribe_with_replay(new_run.id)
    thread = threading.Thread(
        target=run_job_prep,
        args=(
            new_run.id,
            trace_id,
            user.id if user else None,
            None if user else anonymous_id,
            input_state,
        ),
        daemon=True,
    )
    thread.start()

    return StreamingResponse(
        _sse_event_stream(
            new_run.id,
            buf,
            [_sse("meta", {"run_id": new_run.id, "retried_from": run.id})],
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/runs/{run_id}/abort", status_code=200)
def abort_run(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
):
    """主动放弃（仅 waiting_approval：图已挂起无后台线程；running 中的任务不可安全中止）。"""
    run = _get_owned_run(db, run_id, user, anonymous_id)
    if run.status != "waiting_approval":
        raise HTTPException(409, "仅等待审批中的任务可以放弃")
    # 条件更新乐观锁（与 approve 对称）：并发时只有一方能从 waiting_approval 挪走，
    # 另一方 409——防 abort 覆盖已批准的 run / resume 线程再覆盖 aborted
    moved = db.execute(
        update(AgentRun)
        .where(AgentRun.id == run.id, AgentRun.status == "waiting_approval")
        .values(status="aborted", completed_at=datetime.now(timezone.utc))
    ).rowcount
    if not moved:
        db.commit()
        raise HTTPException(409, "该任务状态已变更（可能刚被审批），请刷新查看")
    approval = _pending_approval(db, run.id)
    if approval:
        approval.status = "expired"
    db.commit()
    _audit(
        db,
        action="run_abort",
        user=user,
        anonymous_id=anonymous_id,
        ip=request.client.host if request.client else None,
        run_id=run.id,
        trace_id=run.trace_id,
    )
    return {"run_id": run.id, "status": "aborted"}


# —— 管理端（PRD §15.6 拍板：先落库 + JSON 查看，span 树页面后置 v4.1）——


@router.get("/admin/runs")
def admin_list_runs(
    status: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(_admin_only),  # noqa: B008
):
    """全量 run 列表（管理端）：SQL 分页 + 状态过滤，不整表载入。"""
    filters = [AgentRun.status == status] if status else []
    total = db.scalar(select(func.count()).select_from(AgentRun).where(*filters))
    rows = db.scalars(
        select(AgentRun)
        .where(*filters)
        .order_by(AgentRun.id.desc())
        .offset((max(1, page) - 1) * 20)
        .limit(20)
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "trace_id": r.trace_id,
                "status": r.status,
                "run_type": r.run_type,
                "user_id": r.user_id,
                "anonymous_id": r.anonymous_id,
                "tokens_total": r.tokens_total,
                "duration_ms": r.duration_ms,
                "error": r.error,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "total": total,
    }


@router.get("/admin/runs/{run_id}/spans")
def admin_run_spans(
    run_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(_admin_only),  # noqa: B008
):
    run = db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(404, "任务不存在")
    spans = db.scalars(
        select(AgentSpan).where(AgentSpan.run_id == run_id).order_by(AgentSpan.id.asc())
    ).all()
    approvals = db.scalars(
        select(AgentApproval).where(AgentApproval.run_id == run_id)
    ).all()
    audits = db.scalars(
        select(AuditLog).where(AuditLog.run_id == run_id).order_by(AuditLog.id.asc())
    ).all()
    return {
        "run": {
            "id": run.id,
            "trace_id": run.trace_id,
            "status": run.status,
            "plan": json.loads(run.plan_json) if run.plan_json else [],
            "tokens_total": run.tokens_total,
            "duration_ms": run.duration_ms,
            "error": run.error,
        },
        "spans": [
            {
                "id": sp.id,
                "span_type": sp.span_type,
                "name": sp.name,
                "status": sp.status,
                "attempt": sp.attempt,
                "duration_ms": sp.duration_ms,
                "error_type": sp.error_type,
            }
            for sp in spans
        ],
        "approvals": [
            {
                "id": a.id,
                "action_key": a.action_key,
                "status": a.status,
                "decided_at": a.decided_at.isoformat() if a.decided_at else None,
            }
            for a in approvals
        ],
        "audit": [
            {
                "id": a.id,
                "action": a.action,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in audits
        ],
    }


@router.get("/admin/runs/stats")
def admin_run_stats(
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(_admin_only),  # noqa: B008
):
    """近 7 日 v2 运行指标（PRD FR-11）：成功率/P50/P95/token/重试率/审批口径。

    全部走 SQL 聚合（此前整表载入内存统计，run/span 表增长后 O(n) 内存与延迟）。
    重试率与审批口径沿用全量口径（不限 7 日窗口），与改前语义一致。
    """
    window = AgentRun.created_at >= datetime.now(timezone.utc) - timedelta(days=7)
    counts = dict(
        db.execute(
            select(AgentRun.status, func.count())
            .where(window)
            .group_by(AgentRun.status)
        ).all()
    )
    total = sum(counts.values())
    completed = counts.get("completed", 0)
    failed = counts.get("failed", 0)
    # 百分位只取已完成 run 的 duration 标量列（不整行 ORM）
    durations = sorted(
        value or 0
        for (value,) in db.execute(
            select(AgentRun.duration_ms).where(window, AgentRun.status == "completed")
        ).all()
    )

    def _pct(seq, p):
        if not seq:
            return None
        idx = min(len(seq) - 1, int(len(seq) * p))
        return seq[idx]

    spans_total = db.scalar(select(func.count()).select_from(AgentSpan)) or 0
    retried_total = (
        db.scalar(
            select(func.count())
            .select_from(AgentSpan)
            .where(AgentSpan.status == "retried")
        )
        or 0
    )
    approval_counts = dict(
        db.execute(
            select(AgentApproval.status, func.count()).group_by(AgentApproval.status)
        ).all()
    )
    tokens_total = (
        db.scalar(
            select(func.coalesce(func.sum(AgentRun.tokens_total), 0)).where(window)
        )
        or 0
    )
    return {
        "window_days": 7,
        "total_runs": total,
        "completed": completed,
        "failed": failed,
        "success_rate": round(completed / total, 4) if total else None,
        "p50_ms": _pct(durations, 0.5),
        "p95_ms": _pct(durations, 0.95),
        "tokens_total": int(tokens_total),
        "retry_rate": round(retried_total / spans_total, 4) if spans_total else None,
        "approvals": {
            "pending": approval_counts.get("pending", 0),
            "approved": approval_counts.get("approved", 0),
            "rejected": approval_counts.get("rejected", 0),
            "expired": approval_counts.get("expired", 0),
        },
    }
