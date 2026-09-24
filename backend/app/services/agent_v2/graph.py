"""job_prep_pipeline 图（PRD v4.0 §4.3）：一键求职准备的多节点编排。

    START → planner(规则式) → load_resume → need_analysis(条件) → [analyzer] → matcher
          → questioner → verifier(回环 ≤2) → hitl_gate(interrupt?) → deliver → END
          verifier 超限 / planner 拒绝 → fail

设计约束：
- 节点只调 service 层（agent_capabilities / ai_client / interview_service），不碰 api；
- LLM 能力全部经 agent_capabilities（v1 工具同源），测试在该层打桩即可整图离线；
- 图执行线程 + SSE 的断开/记账约定与 v1 executor 相同（PRD §4.4）；
- risk 动作（create_interview_session）经 hitl_gate interrupt，批准后由 deliver 调
  interview_service.create_session 执行——零审批零副作用（PRD §10.3）。
- 依赖注入：LangGraph 节点只接 (state, config)，运行期依赖经 config["configurable"]["deps"]
  传入（db/recorder/publish/身份），节点内 deps_from_config(config) 取回。
"""

import json
import time
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.agent_v2 import AgentApproval, AgentSpan
from app.models.analysis import Analysis
from app.models.resume import Resume
from app.services.agent_capabilities import run_job_match, run_question_generation
from app.services.agent_v2 import event_bus
from app.services.ai_client import analyze_resume
from app.services.interview_service import create_session
from app.services.prompts import PROMPT_VERSION
from app.services.usage_service import write_usage

logger = get_logger(__name__)

MAX_RETRIES_PER_NODE = settings.agent_v2_max_retries_per_node
RISK_CREATE_SESSION = "create_interview_session"


class JobPrepState(dict):
    """共享状态：普通 dict（LangGraph 默认覆盖语义；retry_counts 等整体回写）。"""


def _preview(value, limit: int = 200) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:limit]


class RunRecorder:
    """run 的落库工具：状态流转 + span 写入 + token 累计（图执行线程独占 db）。"""

    def __init__(self, run_id: int, trace_id: str, db) -> None:
        self.run_id = run_id
        self.trace_id = trace_id
        self.db = db
        self.tokens = 0
        self._t0 = time.monotonic()

    def status(
        self, status: str, node: str | None = None, error: str | None = None
    ) -> None:
        self.db.execute(
            "UPDATE agent_runs SET status = :s, current_node = :n, error = :e, "
            "updated_at = now() WHERE id = :id",
            {"s": status, "n": node, "e": error, "id": self.run_id},
        )
        self.db.commit()

    def span(
        self,
        span_type: str,
        name: str,
        *,
        attempt: int = 1,
        status: str = "ok",
        input_preview: str | None = None,
        output_preview: str | None = None,
        duration_ms: int | None = None,
        error_type: str | None = None,
    ) -> None:
        self.db.add(
            AgentSpan(
                trace_id=self.trace_id,
                run_id=self.run_id,
                span_type=span_type,
                name=name,
                status=status,
                attempt=attempt,
                input_preview=_preview(input_preview),
                output_preview=_preview(output_preview),
                duration_ms=duration_ms,
                error_type=error_type,
            )
        )
        self.db.commit()

    def finish(
        self, status: str, error: str | None = None, output: dict | None = None
    ) -> None:
        self.db.execute(
            "UPDATE agent_runs SET status = :s, error = :e, output_json = :o, "
            "tokens_total = :t, duration_ms = :d, iterations = "
            "(SELECT count(*) FROM agent_spans WHERE run_id = :id AND span_type = 'agent_node'), "
            "completed_at = now(), updated_at = now() WHERE id = :id",
            {
                "s": status,
                "e": error,
                "o": json.dumps(output, ensure_ascii=False) if output else None,
                "t": self.tokens,
                "d": int((time.monotonic() - self._t0) * 1000),
                "id": self.run_id,
            },
        )
        self.db.commit()


def deps_from_config(config: dict) -> Any:
    """节点内取回运行期依赖（db/recorder/publish/身份）。"""
    return config["configurable"]["deps"]


def _make_nodes(deps):
    """真实节点实现（闭包注入 deps）。"""

    def planner(state: JobPrepState) -> dict:
        # 规则式 Planner（PRD 定稿）：单链路固定计划 + 入口意图识别；
        # 无 JD 且无主题 → fail，引导走 v1 问答（不做自然语言分流）。
        jd = (state.get("jd_text") or "").strip()
        topic = (state.get("topic") or "").strip()
        if not jd and not topic:
            return {
                "error": "请提供岗位 JD 或想练习的主题；普通问答可以直接在对话框提问。",
                "plan": [],
            }
        plan = [
            {"node": "load_resume", "status": "pending"},
            {"node": "analyzer", "status": "pending"},
            {"node": "matcher", "status": "pending"},
            {"node": "questioner", "status": "pending"},
            {"node": "deliver", "status": "pending"},
        ]
        risk = [RISK_CREATE_SESSION]  # US-3：结果可一键开面试，创建前 HITL 确认
        deps.publish({"type": "plan", "plan": plan, "risk_actions": risk})
        deps.recorder.span(
            "agent_node", "planner", output_preview=json.dumps(plan, ensure_ascii=False)
        )
        return {"plan": plan, "risk_actions": risk, "retry_counts": {}}

    def load_resume(state: JobPrepState) -> dict:
        from app.services.agent_capabilities import find_latest_resume

        if state.get("resume_id"):
            return {}  # retry 注入：产物已存在，跳过（D2 语义）
        resume = find_latest_resume(deps.db, deps.user_id, deps.anonymous_id)
        if resume is None or not (resume.raw_text or "").strip():
            return {
                "error": "没有可用简历：请先上传文本型 PDF 简历并完成解析后再发起任务。"
            }
        return {"resume_id": resume.id, "resume_filename": resume.filename}

    def analyzer(state: JobPrepState) -> dict:
        existing = (
            deps.db.query(Analysis)
            .filter(
                Analysis.resume_id == state["resume_id"],
                Analysis.valid_json.is_(True),
            )
            .order_by(Analysis.id.desc())
            .first()
        )
        if existing is not None:
            return {"analysis_summary": existing.result_json}

        resume = deps.db.get(Resume, state["resume_id"])
        try:
            result = analyze_resume(resume.raw_text, settings)
        except Exception as exc:  # noqa: BLE001  AIError/网络异常都转成可回环的失败原因
            return {"analyzer_error": str(getattr(exc, "message", exc))}

        analysis = Analysis(
            resume_id=resume.id,
            user_id=deps.user_id,
            anonymous_id=deps.anonymous_id,
            model_name=result.model_name,
            prompt_version=PROMPT_VERSION,
            result_json=result.report,
            valid_json=result.valid,
            tokens_prompt=result.tokens_prompt,
            tokens_completion=result.tokens_completion,
            duration_ms=result.duration_ms,
        )
        tokens = (result.tokens_prompt or 0) + (result.tokens_completion or 0)
        deps.db.add(analysis)
        write_usage(
            deps.db,
            anonymous_id=deps.anonymous_id,
            user_id=deps.user_id,
            action_type="analysis",
            model_name=result.model_name,
            tokens_total=tokens,
            ip_address=None,
        )
        deps.db.commit()
        deps.recorder.tokens += tokens
        if not result.valid:
            return {"analyzer_error": "AI 返回的分析报告格式不合格，请重试。"}
        return {"analysis_summary": result.report}

    def matcher(state: JobPrepState) -> dict:
        if state.get("match_report"):
            return {}  # retry 注入：匹配报告已存在，跳过（D2 语义）
        text, report = run_job_match(
            deps.db, deps.user_id, deps.anonymous_id, state.get("jd_text") or ""
        )
        return {
            "match_text": text,
            "match_report": report,
            "match_error": None if report else text,
        }

    def questioner(state: JobPrepState) -> dict:
        if (state.get("questions") or {}).get("questions"):
            return {}  # retry 注入：题目已存在，跳过（D2 语义）
        topic = (state.get("topic") or "").strip() or (
            state.get("jd_text") or ""
        ).strip()[:50]
        text, product = run_question_generation(
            deps.db,
            deps.user_id,
            deps.anonymous_id,
            topic or "通用面试题",
            state.get("position_type") or "",
        )
        return {
            "questions_text": text,
            "questions": product or {},
            "questions_error": None if product else text,
        }

    def verifier(state: JobPrepState) -> dict:
        """产物校验：缺字段/空产物 → 回环产出节点（≤2 次）；通过 → HITL/交付。"""
        retry = dict(state.get("retry_counts") or {})
        problems: list[str] = []
        retry_node: str | None = None

        if state.get("analyzer_error"):
            retry_node, problems = "analyzer", problems + [state["analyzer_error"]]
        if not state.get("match_report"):
            retry_node = retry_node or "matcher"
            problems.append(state.get("match_error") or "缺少匹配报告")
        if not (state.get("questions") or {}).get("questions"):
            retry_node = retry_node or "questioner"
            problems.append(state.get("questions_error") or "缺少面试题")

        if not problems:
            return {"verifier_result": {"ok": True}, "retry_counts": retry}

        node = retry_node or "matcher"
        count = retry.get(node, 0)
        if count >= MAX_RETRIES_PER_NODE:
            return {
                "verifier_result": {
                    "ok": False,
                    "exhausted": True,
                    "problems": problems,
                },
                "error": f"节点 {node} 重试 {count} 次后仍失败：" + "；".join(problems),
            }
        retry[node] = count + 1
        deps.publish(
            {"type": "retry", "node": node, "attempt": count + 1, "reasons": problems}
        )
        deps.recorder.span(
            "agent_node",
            f"verifier→{node}",
            attempt=count + 1,
            status="retried",
            input_preview="；".join(problems),
        )
        return {
            "verifier_result": {"ok": False, "retry_node": node},
            "retry_counts": retry,
        }

    def hitl_gate(state: JobPrepState) -> dict:
        """风险动作门禁：建 pending 审批单 → interrupt 挂起；resume 时带决定回写。"""
        if RISK_CREATE_SESSION in (state.get("risk_actions") or []):
            approval = AgentApproval(
                run_id=deps.run_id,
                trace_id=deps.trace_id,
                user_id=deps.user_id,
                anonymous_id=deps.anonymous_id,
                action_key=RISK_CREATE_SESSION,
                payload_json=json.dumps(
                    {
                        "resume_id": state.get("resume_id"),
                        "position_type": state.get("position_type"),
                    },
                    ensure_ascii=False,
                ),
                status="pending",
            )
            deps.db.add(approval)
            deps.db.commit()
            deps.db.refresh(approval)
            payload = {
                "approval_id": approval.id,
                "action_key": RISK_CREATE_SESSION,
                "summary": "按本次出题结果创建模拟面试场次（产生持久数据并消耗面试额度）",
            }
            deps.publish({"type": "approval_required", **payload})
            decision = interrupt(payload)  # 图在此挂起；resume 值即审批决定
            if isinstance(decision, dict) and decision:
                return {"approval_decision": decision}
        return {}

    def deliver(state: JobPrepState) -> dict:
        decision = state.get("approval_decision") or {}
        session_created = None
        if (
            decision.get("approved")
            and decision.get("action_key") == RISK_CREATE_SESSION
        ):
            try:
                session = create_session(
                    deps.db,
                    state["resume_id"],
                    user_id=deps.user_id,
                    anonymous_id=deps.anonymous_id,
                    position_type=state.get("position_type") or None,
                )
                session_created = session.id
                deps.publish({"type": "session_created", "session_id": session.id})
            except ValueError as exc:
                deps.publish({"type": "error", "content": str(exc)})

        output = {
            "analysis": state.get("analysis_summary") or {},
            "match": state.get("match_report") or {},
            "questions": (state.get("questions") or {}).get("questions") or [],
            "question_sources": (state.get("questions") or {}).get("sources") or [],
            "session_id": session_created,
            "match_text": state.get("match_text"),
            "questions_text": state.get("questions_text"),
        }
        deps.publish({"type": "done", "output": output})
        return {"output": output, "approval_decision": decision}

    def fail(state: JobPrepState) -> dict:
        deps.publish({"type": "fatal", "content": state.get("error") or "任务失败"})
        # 保留已完成节点的产物：retry-node 用它们注入新 run 的初始 state（D2）
        return {
            "failed": True,
            "partial_output": {
                k: state[k]
                for k in ("match_report", "match_text", "analysis_summary", "questions")
                if state.get(k) is not None
            },
        }

    return {
        "planner": planner,
        "load_resume": load_resume,
        "analyzer": analyzer,
        "matcher": matcher,
        "questioner": questioner,
        "verifier": verifier,
        "hitl_gate": hitl_gate,
        "deliver": deliver,
        "fail": fail,
    }


def route_after_planner(state: JobPrepState) -> str:
    return "load_resume" if state.get("plan") else "fail"


def need_analysis(state: JobPrepState, config: dict) -> dict:
    """pass-through 节点：查"是否已有有效分析"，写 skip_analysis 标志供条件边路由。"""

    deps = deps_from_config(config)
    existing = (
        deps.db.query(Analysis)
        .filter(
            Analysis.resume_id == state["resume_id"],
            Analysis.valid_json.is_(True),
        )
        .first()
    )
    return {"skip_analysis": existing is not None}


def route_need_analysis(state: JobPrepState) -> str:
    return "matcher" if state.get("skip_analysis") else "analyzer"


def route_after_verifier(state: JobPrepState) -> str:
    v = state.get("verifier_result") or {}
    if v.get("ok"):
        return "hitl_gate"
    if v.get("exhausted"):
        return "fail"
    return v.get("retry_node") or "fail"


def _node(name: str, fn):
    """节点薄壳：发布 node_start/end、写 span、维护 run.current_node。"""

    def _inner(state: JobPrepState, config: dict) -> dict:
        deps = deps_from_config(config)
        t0 = time.monotonic()
        deps.publish({"type": "node_start", "node": name})
        deps.recorder.status("running", node=name)
        try:
            result = fn(state)
        except Exception as exc:
            deps.recorder.span(
                "agent_node",
                name,
                status="error",
                error_type=type(exc).__name__,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            raise
        deps.recorder.span(
            "agent_node",
            name,
            duration_ms=int((time.monotonic() - t0) * 1000),
            output_preview=_preview(result),
        )
        deps.publish(
            {"type": "node_end", "node": name, "preview": _preview(result) or ""}
        )
        return result

    return _inner


def build_job_prep_graph(checkpointer):
    """编译主链路图。interrupt/resume 经 checkpointer 支持（PRD FR-8）。"""
    builder = StateGraph(JobPrepState)

    def wrapped(name: str):
        def _inner(state: JobPrepState, config: dict) -> dict:
            deps = deps_from_config(config)
            t0 = time.monotonic()
            deps.publish({"type": "node_start", "node": name})
            deps.recorder.status("running", node=name)
            try:
                result = _make_nodes(deps)[name](state)
            except Exception as exc:
                deps.recorder.span(
                    "agent_node",
                    name,
                    status="error",
                    error_type=type(exc).__name__,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
                raise
            deps.recorder.span(
                "agent_node",
                name,
                duration_ms=int((time.monotonic() - t0) * 1000),
                output_preview=_preview(result),
            )
            deps.publish(
                {"type": "node_end", "node": name, "preview": _preview(result) or ""}
            )
            return result

        return _inner

    for name in (
        "planner",
        "load_resume",
        "need_analysis",
        "analyzer",
        "matcher",
        "questioner",
        "verifier",
        "hitl_gate",
        "deliver",
        "fail",
    ):
        builder.add_node(name, wrapped(name))

    builder.add_edge(START, "planner")
    builder.add_conditional_edges(
        "planner", route_after_planner, {"load_resume": "load_resume", "fail": "fail"}
    )
    builder.add_edge("load_resume", "need_analysis")
    builder.add_conditional_edges(
        "need_analysis",
        route_need_analysis,
        {"skip": "matcher", "analyzer": "analyzer"},
    )
    builder.add_edge("analyzer", "matcher")
    builder.add_edge("matcher", "questioner")
    builder.add_edge("questioner", "verifier")
    builder.add_conditional_edges(
        "verifier",
        route_after_verifier,
        {
            "hitl_gate": "hitl_gate",
            "fail": "fail",
            "analyzer": "analyzer",
            "matcher": "matcher",
            "questioner": "questioner",
        },
    )
    builder.add_edge("hitl_gate", "deliver")
    builder.add_edge("deliver", END)
    builder.add_edge("fail", END)
    return builder.compile(checkpointer=checkpointer)


def run_job_prep(
    run_id: int,
    trace_id: str,
    user_id: int | None,
    anonymous_id: str | None,
    input_state: dict,
    config_extra: dict | None = None,
) -> None:
    """图执行线程入口：跑 stream 并把事件发布到 event_bus（PRD §4.4 / D1）。

    挂起（interrupt）时流正常结束，run 置 waiting_approval，等 approve/reject
    用 Command(resume) 续跑；断开的消费者靠 event_bus 缓冲 + /stream 重连补发。
    """
    base_dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    dsn = f"{base_dsn}?options=-csearch_path%3Dlanggraph%2Cpublic"
    thread_id = f"run-{run_id}"

    with SessionLocal() as db:
        recorder = RunRecorder(run_id, trace_id, db)

        class _Deps:
            pass

        deps = _Deps()
        deps.db = db
        deps.user_id = user_id
        deps.anonymous_id = anonymous_id
        deps.run_id = run_id
        deps.trace_id = trace_id
        deps.recorder = recorder
        deps.ip = None
        deps.publish = lambda event: event_bus.publish(run_id, event)

        config = {"configurable": {"thread_id": thread_id, "deps": deps}}

        with PostgresSaver.from_conn_string(dsn) as checkpointer:
            checkpointer.setup()
            graph = build_job_prep_graph(checkpointer)
            try:
                last: dict = {}
                for chunk in graph.stream(input_state, config, stream_mode="values"):
                    last = chunk
                st = graph.get_state(config)
                if st.next:
                    recorder.status("waiting_approval", node="hitl_gate")
                    event_bus.publish(
                        run_id, {"type": "stream_closed", "reason": "waiting_approval"}
                    )
                elif last.get("failed"):
                    recorder.finish(
                        "failed",
                        error=last.get("error") or "任务失败",
                        output=last.get("partial_output") or {},
                    )
                    event_bus.publish(
                        run_id,
                        {"type": "fatal", "content": last.get("error") or "任务失败"},
                    )
                else:
                    recorder.status(
                        "completed",
                        output={
                            "analysis": last.get("analysis_summary") or {},
                            "match": last.get("match_report") or {},
                            "questions": (last.get("questions") or {}).get("questions")
                            or [],
                        },
                    )
            except Exception as exc:
                logger.exception("agent_v2 run %s 执行失败", run_id)
                recorder.status("failed", error=f"任务执行失败：{type(exc).__name__}")
                event_bus.publish(
                    run_id, {"type": "fatal", "content": "任务执行失败，请稍后重试。"}
                )


def resume_job_prep(
    run_id: int,
    trace_id: str,
    user_id: int | None,
    anonymous_id: str | None,
    decision: dict,
) -> None:
    """审批后续跑：Command(resume=decision) 从 hitl_gate 的断点继续（PRD FR-8）。"""
    base_dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    dsn = f"{base_dsn}?options=-csearch_path%3Dlanggraph%2Cpublic"
    thread_id = f"run-{run_id}"

    with SessionLocal() as db:
        recorder = RunRecorder(run_id, trace_id, db)

        class _Deps:
            pass

        deps = _Deps()
        deps.db = db
        deps.user_id = user_id
        deps.anonymous_id = anonymous_id
        deps.run_id = run_id
        deps.trace_id = trace_id
        deps.recorder = recorder
        deps.ip = None
        deps.publish = lambda event: event_bus.publish(run_id, event)
        config = {"configurable": {"thread_id": thread_id, "deps": deps}}

        with PostgresSaver.from_conn_string(dsn) as checkpointer:
            checkpointer.setup()
            graph = build_job_prep_graph(checkpointer)
            try:
                last: dict = {}
                for chunk in graph.invoke(
                    Command(resume=decision), config, stream_mode="values"
                ):
                    last = chunk
                if last.get("failed"):
                    recorder.finish(
                        "failed",
                        error=last.get("error") or "任务失败",
                        output=last.get("partial_output") or {},
                    )
                    event_bus.publish(
                        run_id,
                        {"type": "fatal", "content": last.get("error") or "任务失败"},
                    )
                    return
                recorder.status(
                    "completed",
                    output={
                        "analysis": last.get("analysis_summary") or {},
                        "match": last.get("match_report") or {},
                        "questions": (last.get("questions") or {}).get("questions")
                        or [],
                        "session_id": (last.get("output") or {}).get("session_id"),
                    },
                )
            except Exception as exc:
                logger.exception("agent_v2 run %s 续跑失败", run_id)
                recorder.status("failed", error=f"续跑失败：{type(exc).__name__}")
                event_bus.publish(
                    run_id, {"type": "fatal", "content": "续跑失败，请稍后重试。"}
                )
