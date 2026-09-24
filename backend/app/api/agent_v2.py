"""Agent v2（LangGraph 试点）API：一键求职准备任务的发起/重连/审批/重试/放弃。

前缀 /api/agent-v2，与 v1（/api/agent）并存（PRD §4.1）。所有查询走
deps.owner_clause / matches_owner 归属隔离；agent_v2_enabled=false 时接口 503。
事件协议见 PRD §7.3；断线重连先发 DB 快照再订阅事件总线（D1）。
"""

import json
import threading
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.admin import _admin_only
from app.api.auth_deps import get_optional_current_user
from app.api.deps import get_anonymous_id, matches_owner, owner_clause
from app.core.config import settings
from app.db.session import get_db
from app.models.agent_v2 import AgentApproval, AgentRun, AgentSpan, AuditLog
from app.models.user import User
from app.services.agent_v2 import event_bus
from app.services.agent_v2.graph import resume_job_prep, run_job_prep

router = APIRouter(prefix="/api/agent-v2", tags=["agent_v2"])

_TERMINAL_EVENTS = {"done", "fatal", "stream_closed"}


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
    approval = db.scalar(
        select(AgentApproval)
        .where(AgentApproval.run_id == run_id, AgentApproval.status == "pending")
        .order_by(AgentApproval.id.desc())
    )
    if approval is None:
        return None
    if approval.expires_at and approval.expires_at < datetime.now(timezone.utc):
        approval.status = "expired"
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

    def event_stream():
        try:
            yield _sse(
                "meta",
                {
                    "run_id": run.id,
                    "trace_id": trace_id,
                    "thread_id": run.thread_id,
                    "status": "running",
                },
            )
            import time

            idle = 0
            while True:
                try:
                    event = buf.popleft()
                except IndexError:
                    time.sleep(0.05)
                    idle += 1
                    if idle > 600:  # 30s 无事件且未结束：防御性断流，客户端可重连
                        yield _sse("stream_closed", {"reason": "timeout"})
                        return
                    continue
                idle = 0
                yield _sse(event.pop("type"), event)
                if (
                    event.get("type") in _TERMINAL_EVENTS
                    or event["type"] in _TERMINAL_EVENTS
                ):
                    return
        finally:
            event_bus.unsubscribe(run.id, buf)

    return StreamingResponse(
        event_stream(),
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
    """本人 run 列表（归属隔离，分页）。"""
    stmt = (
        select(AgentRun)
        .where(owner_clause(AgentRun, user, anonymous_id))
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
    )
    rows = db.scalars(stmt).all()
    total = len(rows)
    start = (max(1, page) - 1) * min(50, max(1, page_size))
    items = rows[start : start + min(50, max(1, page_size))]
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
            for r in items
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

    def event_stream():
        run_state = db.get(AgentRun, run.id)
        yield _sse(
            "snapshot",
            {
                "run_id": run.id,
                "status": run_state.status,
                "plan": json.loads(run_state.plan_json) if run_state.plan_json else [],
                "output": json.loads(run_state.output_json)
                if run_state.output_json
                else None,
                "error": run_state.error,
            },
        )
        approval = _pending_approval(db, run.id)
        if run_state.status == "waiting_approval" and approval:
            yield _sse(
                "approval_required",
                {
                    "approval_id": approval.id,
                    "action_key": approval.action_key,
                    "summary": "存在待审批的风险动作",
                },
            )
        if run_state.status in ("completed", "failed", "aborted"):
            yield _sse(
                "fatal" if run_state.status != "completed" else "done",
                {"run_id": run.id, "status": run_state.status},
            )
            return

        buf = event_bus.subscribe_with_replay(run.id)
        import time

        idle = 0
        while True:
            try:
                event = buf.popleft()
            except IndexError:
                time.sleep(0.05)
                idle += 1
                if idle > 600:
                    yield _sse("stream_closed", {"reason": "timeout"})
                    return
                continue
            idle = 0
            etype = event.pop("type")
            yield _sse(etype, event)
            if etype in _TERMINAL_EVENTS:
                return

    return StreamingResponse(
        event_stream(),
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
        raise HTTPException(410, "审批单已过期，请重新发起任务")

    decision = (body or {}).get("decision")
    if decision not in ("approved", "rejected"):
        raise HTTPException(422, "decision 必须是 approved 或 rejected")

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


@router.post("/runs/{run_id}/retry-node", status_code=202)
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

    def event_stream():
        yield _sse("meta", {"run_id": new_run.id, "retried_from": run.id})
        import time

        idle = 0
        while True:
            try:
                event = buf.popleft()
            except IndexError:
                time.sleep(0.05)
                idle += 1
                if idle > 600:
                    yield _sse("stream_closed", {"reason": "timeout"})
                    return
                continue
            idle = 0
            etype = event.pop("type")
            yield _sse(etype, event)
            if etype in _TERMINAL_EVENTS:
                return

    return StreamingResponse(
        event_stream(),
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
    run.status = "aborted"
    run.completed_at = datetime.now(timezone.utc)
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
    rows = db.scalars(
        select(AgentRun).where(AgentRun.status == status)
        if status
        else select(AgentRun)
    ).all()
    rows = sorted(rows, key=lambda r: r.id, reverse=True)
    start = (max(1, page) - 1) * 20
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
            for r in rows[start : start + 20]
        ],
        "total": len(rows),
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
    """近 7 日 v2 运行指标（PRD FR-11）：成功率/P50/P95/token/重试率/审批口径。"""
    runs = db.scalars(select(AgentRun)).all()
    recent = [
        r
        for r in runs
        if r.created_at and (datetime.now(timezone.utc) - r.created_at).days < 7
    ]
    completed = [r for r in recent if r.status == "completed"]
    failed = [r for r in recent if r.status == "failed"]
    durations = sorted(r.duration_ms or 0 for r in completed)

    def _pct(seq, p):
        if not seq:
            return None
        idx = min(len(seq) - 1, int(len(seq) * p))
        return seq[idx]

    spans = db.scalars(select(AgentSpan)).all()
    retried = [s for s in spans if s.status == "retried"]
    approvals = db.scalars(select(AgentApproval)).all()
    return {
        "window_days": 7,
        "total_runs": len(recent),
        "completed": len(completed),
        "failed": len(failed),
        "success_rate": round(len(completed) / len(recent), 4) if recent else None,
        "p50_ms": _pct(durations, 0.5),
        "p95_ms": _pct(durations, 0.95),
        "tokens_total": sum(r.tokens_total or 0 for r in recent),
        "retry_rate": round(len(retried) / len(spans), 4) if spans else None,
        "approvals": {
            "pending": sum(1 for a in approvals if a.status == "pending"),
            "approved": sum(1 for a in approvals if a.status == "approved"),
            "rejected": sum(1 for a in approvals if a.status == "rejected"),
            "expired": sum(1 for a in approvals if a.status == "expired"),
        },
    }
