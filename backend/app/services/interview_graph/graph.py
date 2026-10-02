"""面试图编排服务（S1，方案 §11）：把模拟面试的每轮问答放进 LangGraph 状态图。

为什么上图：单轮 API 流在进程崩溃后只能"重放消息"，无法从 checkpoint 续跑；
上图后每个状态（已提问/已评分/已出报告）都持久化在 PostgresSaver（langgraph schema，
表已由迁移预建），进程重启后从 checkpoint 续跑，不重放、不重复计费——
标准 3「失败恢复」的具体落地。

图结构（intro 只走一次，之后每轮作答从 wait_answer 续跑）：
    START → intro → ask_question → wait_answer(interrupt)
                  ↑                     │ resume(答案 / finish)
                  │                     ▼
                  └──────────── score_answer ──(finish/上限)──→ generate_report → END

实战坑对齐（AGENTS.md 三条）：
① invoke 返回最终状态 dict，不 for 遍历；
② 条件边映射键与 route 返回值严格一致（"ask_question"/"generate_report"）；
③ interrupt 所在节点 resume 时从头重执行——wait_answer 内**零副作用**（纯 interrupt），
   ask_question 的落库不会在续跑时重复发生。
"""

import logging
import time
from dataclasses import dataclass, field
from typing import TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.interview import InterviewMessage, InterviewSession
from app.schemas.interview import InterviewReport
from app.services.ai_client import AIError, AnalysisResult, chat_json
from app.services.interview_prompts import (
    INTERVIEW_GRAPH_PROMPT_VERSION,
    OPENING_MESSAGE,
    build_answer_score_system_prompt,
    build_final_report_system_prompt,
    build_follow_up_system_prompt,
    build_interviewer_system_prompt,
    stage_for_turn,
)

logger = logging.getLogger(__name__)

# 追问生成失败时的兜底追问：路由已进入 follow_up，节点必须产出追问语（不挂死、不空转）
FALLBACK_FOLLOW_UP = (
    "你刚才提到的那部分能再展开讲讲吗？具体说说是怎么实现的，以及你个人负责了哪些部分。"
)

# 出题失败时的兜底问题（工具层同口径：吞异常给自然语言兜底，不炸整场面试）
FALLBACK_QUESTION = "刚才的网络有点问题。我们继续：请再讲讲你简历里最有挑战的一个项目，你具体负责了哪部分？"

_FOLLOW_UP_INSTRUCTION = (
    '请针对候选人刚才的回答输出一个追问。只输出 JSON：{"question": "追问全文"}'
)
_QUESTION_INSTRUCTION = (
    '请基于简历与对话进度提出下一轮面试问题。只输出 JSON：{"question": "问题全文"}'
)
_SCORE_INSTRUCTION = "请对本轮回答输出评分 JSON。"
_REPORT_INSTRUCTION = "请输出结束评价 JSON。"


class NextQuestion(BaseModel):
    """出题契约：模型只需回一个问题全文。"""

    question: str


class AnswerScore(BaseModel):
    """逐轮评分契约（轻量 judge，喂给路由与 trace）。"""

    score: int
    depth_signal: str  # strong / medium / weak
    comment: str


class InterviewState(TypedDict, total=False):
    session_id: int
    resume_text: str
    position_type: str | None
    turn_count: int
    stage: str
    last_question: str
    user_answer: str | None
    finish_requested: bool
    last_score: dict
    report: dict
    # v4.2 题库驱动：加载题库后 ask_question 按序取题（不调 LLM），问完回落 AI 出题。
    # 随 checkpoint 持久化，session 表无需加列
    bank_questions: list
    bank_index: int
    # v4.5 面试官 Agent：当前主问题的追问次数（追问不占主问题轮次）
    follow_up_count: int


@dataclass
class _Deps:
    """图执行线程运行期依赖：节点闭包构造一次，db 会话由调用方（API 层）持有。"""

    db: Session
    session: InterviewSession
    resume_text: str
    nodes: dict = field(init=False)
    trace: list = field(default_factory=list)
    tokens_used: int = 0  # 本轮 LLM 消耗合计（API 层据此写 usage_logs）

    def __post_init__(self) -> None:
        self.nodes = _make_nodes(self)
        # trace 跨轮续接：每次 API 请求都是新的 deps（新图对象），上一轮的节点记录
        # 存在 session.trace_json 里——从这里恢复而不是从空列表覆盖
        existing = self.session.trace_json if self.session is not None else None
        if isinstance(existing, dict):
            self.trace = list(existing.get("nodes") or [])

    def _record(
        self, node: str, turn: int, result: AnalysisResult | None, **extra
    ) -> None:
        """节点级 trace：进 session.trace_json（报告与看板可回看），≤200 字预览不落正文。"""
        if result is not None:
            self.tokens_used += (result.tokens_prompt or 0) + (
                result.tokens_completion or 0
            )
        self.trace.append(
            {
                "node": node,
                "turn": turn,
                "ts_ms": int(time.monotonic() * 1000) % 10**10,
                "duration_ms": getattr(result, "duration_ms", None),
                "tokens": (
                    (result.tokens_prompt or 0) + (result.tokens_completion or 0)
                    if result
                    else 0
                ),
                **extra,
            }
        )
        self.session.trace_json = {
            "version": INTERVIEW_GRAPH_PROMPT_VERSION,
            "nodes": self.trace[-40:],  # 防无限增长：只留最近 40 条
        }

    def _flush(self) -> None:
        self.db.commit()


def _make_nodes(deps: _Deps) -> dict:
    db = deps.db
    session = deps.session

    def intro(state: InterviewState) -> dict:
        """开场白：固定话术、不耗 AI。幂等守卫（坑 #3 的防御面）——已有消息就不再写。"""
        count = (
            db.scalar(
                select(func.count())
                .select_from(InterviewMessage)
                .where(InterviewMessage.session_id == session.id)
            )
            or 0
        )
        if count == 0:
            db.add(
                InterviewMessage(
                    session_id=session.id, role="interviewer", content=OPENING_MESSAGE
                )
            )
            deps._record("intro", 0, None)
            db.commit()
        return {"session_id": session.id, "turn_count": 0, "stage": "intro"}

    def ask_question(state: InterviewState) -> dict:
        """出题：题库模式按序取题（零 LLM 调用），问完回落 AI 出题；落库并推进轮次。

        stage 口径与单轮路径对齐：**刚问的问题**属于 stage_for_turn(turn)（进 state，
        供路由与 SSE meta），而 session.stage 存"下一问所处阶段"= stage_for_turn(turn+1)
        （与 legacy send_message 的落库语义一致，报告与前端展示不漂移）。
        """
        started = time.monotonic()
        turn = (state.get("turn_count") or 0) + 1
        ask_stage = stage_for_turn(turn, settings.max_interview_turns)

        # v4.2 题库优先：bank_questions 里还有题就直接取，省一次 LLM 调用
        bank = state.get("bank_questions") or []
        idx = state.get("bank_index") or 0
        source = "ai"
        result: AnalysisResult | None = None
        if idx < len(bank):
            question = str(bank[idx].get("question") or "").strip()[:2000]
            source = "bank"
            if not question:
                question = FALLBACK_QUESTION
        else:
            system = build_interviewer_system_prompt(
                deps.resume_text,
                ask_stage,
                turn,
                settings.max_interview_turns,
                session.position_type,
            )
            try:
                result = chat_json(
                    system, _QUESTION_INSTRUCTION, settings, NextQuestion.model_validate
                )
                question = (result.report.get("question") or "").strip()[
                    :2000
                ] or FALLBACK_QUESTION
            except AIError:
                logger.warning(
                    "面试图出题失败 session=%s，使用兜底问题", session.id, exc_info=True
                )
                question = FALLBACK_QUESTION
        db.add(
            InterviewMessage(
                session_id=session.id,
                role="interviewer",
                content=question,
                tokens=result.tokens_completion if result else None,
            )
        )
        session.turn_count = turn
        session.stage = stage_for_turn(turn + 1, settings.max_interview_turns)
        deps._record(
            "ask_question",
            turn,
            result,
            duration_ms=int((time.monotonic() - started) * 1000),
            source=source,
        )
        db.commit()
        patch: dict = {
            "turn_count": turn,
            "stage": ask_stage,
            "last_question": question,
            "follow_up_count": 0,  # 新主问题：追问预算重置
        }
        if source == "bank":
            patch["bank_index"] = idx + 1  # 题库游标随 checkpoint 前进
        return patch

    def wait_answer(state: InterviewState) -> dict:
        """挂起等作答。**纯 interrupt、零副作用**——resume 时本节点会从头重执行（坑 #3），
        任何落库放这里都会重复发生。resume 值：答案字符串，或 {"finish": true} / {"answer": ...}。"""
        payload = interrupt(
            {
                "question": state.get("last_question") or "",
                "turn": state.get("turn_count") or 0,
            }
        )
        if isinstance(payload, dict):
            return {
                "user_answer": payload.get("answer") or None,
                "finish_requested": bool(payload.get("finish")),
            }
        return {
            "user_answer": str(payload) if payload else None,
            "finish_requested": False,
        }

    def score_answer(state: InterviewState) -> dict:
        """逐轮评分：轻量 judge，结果进 trace；AI 失败不阻断面试（记 unknown 继续）。"""
        if state.get("finish_requested") or not state.get("user_answer"):
            return {}
        system = build_answer_score_system_prompt(
            deps.resume_text, state.get("last_question") or "", state["user_answer"]
        )
        result: AnalysisResult | None = None
        try:
            result = chat_json(
                system, _SCORE_INSTRUCTION, settings, AnswerScore.model_validate
            )
            score = result.report
        except AIError:
            logger.warning("面试图评分失败 session=%s", session.id, exc_info=True)
            score = {
                "score": None,
                "depth_signal": "unknown",
                "comment": "本轮评分不可用",
            }
        deps._record(
            "score_answer",
            state.get("turn_count") or 0,
            result,
            score=score.get("score"),
        )
        db.commit()
        return {"last_score": score}

    def follow_up(state: InterviewState) -> dict:
        """v4.5 面试官 Agent：对单薄回答生成针对性追问，再次挂起等作答。

        追问**不推进 turn_count**（主问题轮次口径不变，报告 transcript 仍全量含追问轮）；
        预算由 route_after_score 按 settings.interview_max_follow_ups 把关，本节点只管生成。
        生成失败不挂死面试：路由已进入本节点，兜底追问语保证行为一致。
        """
        started = time.monotonic()
        system = build_follow_up_system_prompt(
            deps.resume_text,
            state.get("last_question") or "",
            state.get("user_answer") or "",
            str((state.get("last_score") or {}).get("comment") or ""),
        )
        result: AnalysisResult | None = None
        try:
            result = chat_json(
                system, _FOLLOW_UP_INSTRUCTION, settings, NextQuestion.model_validate
            )
            follow = (result.report.get("question") or "").strip()[:2000]
        except AIError:
            logger.warning(
                "面试图追问生成失败 session=%s，使用兜底追问", session.id, exc_info=True
            )
            follow = ""
        if not follow:
            follow = FALLBACK_FOLLOW_UP
        db.add(
            InterviewMessage(
                session_id=session.id,
                role="interviewer",
                content=follow,
                tokens=result.tokens_completion if result else None,
            )
        )
        deps._record(
            "follow_up",
            state.get("turn_count") or 0,
            result,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        db.commit()
        return {
            "last_question": follow,
            "follow_up_count": (state.get("follow_up_count") or 0) + 1,
            # 清空上一答：追问轮是新作答，score_answer 评分的是追问的回答
            "user_answer": None,
            "finish_requested": False,
        }

    def generate_report(state: InterviewState) -> dict:
        """结束评价：全量对话 → 分维度评分 JSON，session 置 finished。"""
        history = list(
            db.scalars(
                select(InterviewMessage)
                .where(InterviewMessage.session_id == session.id)
                .order_by(InterviewMessage.id)
            )
        )
        transcript = "\n".join(
            f"{'面试官' if m.role == 'interviewer' else '候选人'}：{m.content}"
            for m in history
        )
        result = chat_json(
            build_final_report_system_prompt(deps.resume_text, transcript),
            _REPORT_INSTRUCTION,
            settings,
            InterviewReport.model_validate,
        )
        session.status = "finished"
        session.final_report_json = result.report
        deps._record("generate_report", state.get("turn_count") or 0, result)
        db.commit()
        return {"report": result.report}

    return {
        "intro": intro,
        "ask_question": ask_question,
        "wait_answer": wait_answer,
        "score_answer": score_answer,
        "follow_up": follow_up,
        "generate_report": generate_report,
    }


def route_after_score(state: InterviewState) -> str:
    """评分后路由：结束/轮次上限/收尾阶段 → 出报告；回答单薄且追问预算未尽 → 智能追问；
    否则继续出下一题。

    ⚠️ 返回值必须与 build_interview_graph 条件边映射的键严格一致（坑 #2）。
    追问不占主问题轮次（turn_count 不变），总量由 max_interview_turns 与
    interview_max_follow_ups 两个预算共同约束。
    """
    if state.get("finish_requested"):
        return "generate_report"
    if (state.get("turn_count") or 0) >= settings.max_interview_turns:
        return "generate_report"
    if state.get("stage") == "wrapup":
        return "generate_report"
    if not state.get("user_answer"):
        # 无作答（空 resume 值）：没内容可追问，直接下一题
        return "ask_question"
    score = state.get("last_score") or {}
    weak = score.get("depth_signal") == "weak" or (
        isinstance(score.get("score"), int) and score["score"] <= 4
    )
    if weak and (state.get("follow_up_count") or 0) < settings.interview_max_follow_ups:
        return "follow_up"
    return "ask_question"


def build_interview_graph(checkpointer, deps: _Deps):
    """编译面试图。interrupt/resume 经 PostgresSaver 支持（checkpoint 落 langgraph schema）。

    v4.5 拓扑：score_answer 后条件路由可进 follow_up（面试官 Agent 智能追问），
    follow_up → wait_answer 再次挂起——同一主问题的追问链在 checkpoint 上闭环。
    """
    builder = StateGraph(InterviewState)
    for name in (
        "intro",
        "ask_question",
        "wait_answer",
        "score_answer",
        "follow_up",
        "generate_report",
    ):
        builder.add_node(name, deps.nodes[name])
    builder.add_edge(START, "intro")
    builder.add_edge("intro", "ask_question")
    builder.add_edge("ask_question", "wait_answer")
    builder.add_edge("wait_answer", "score_answer")
    builder.add_conditional_edges(
        "score_answer",
        route_after_score,
        {
            "ask_question": "ask_question",
            "follow_up": "follow_up",
            "generate_report": "generate_report",
        },
    )
    builder.add_edge("follow_up", "wait_answer")
    builder.add_edge("generate_report", END)
    return builder.compile(checkpointer=checkpointer)


def _dsn() -> str:
    base = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    return f"{base}?options=-csearch_path%3Dlanggraph%2Cpublic"


def _thread(session_id: int) -> dict:
    return {"configurable": {"thread_id": f"interview-{session_id}"}}


def has_checkpoint(session_id: int) -> bool:
    """会话是否有图 checkpoint（区分图会话与旧行为会话，决定走图还是回退老路径）。"""
    with PostgresSaver.from_conn_string(_dsn()) as checkpointer:
        checkpointer.setup()
        return checkpointer.get_tuple(_thread(session_id)) is not None


def _fresh_deps(db: Session, session: InterviewSession) -> _Deps:
    resume_text = ""
    from app.models.resume import Resume

    resume = db.get(Resume, session.resume_id)
    if resume is not None:
        resume_text = resume.raw_text or ""
    return _Deps(db=db, session=session, resume_text=resume_text)


def graph_start(
    db: Session, session: InterviewSession, bank_questions: list | None = None
) -> int:
    """开局：跑 intro + 第一问，图挂在 wait_answer 等候选人作答。返回消耗 token 数。"""
    deps = _fresh_deps(db, session)
    with PostgresSaver.from_conn_string(_dsn()) as checkpointer:
        checkpointer.setup()
        graph = build_interview_graph(checkpointer, deps)
        graph.invoke(
            {
                "session_id": session.id,
                "resume_text": deps.resume_text,
                "position_type": session.position_type,
                "turn_count": 0,
                "stage": "intro",
                "bank_questions": bank_questions or [],
                "bank_index": 0,
                "follow_up_count": 0,
            },
            _thread(session.id),
        )
    db.commit()
    return deps.tokens_used


def graph_answer(db: Session, session: InterviewSession, resume_value) -> dict:
    """作答/结束：Command(resume=...) 从 checkpoint 续跑（崩溃恢复同一条路），返回最终状态。"""
    deps = _fresh_deps(db, session)
    with PostgresSaver.from_conn_string(_dsn()) as checkpointer:
        checkpointer.setup()
        graph = build_interview_graph(checkpointer, deps)
        # invoke 的返回值就是最终状态 dict（坑 #1）；挂起时同样返回当前状态
        last: dict = graph.invoke(Command(resume=resume_value), _thread(session.id))
    session.resume_count = (session.resume_count or 0) + 1
    db.commit()
    last["tokens_used"] = deps.tokens_used
    return last


def graph_pending(session_id: int) -> bool:
    """图是否停在 wait_answer（还有未完成的轮次）。"""
    with SessionLocal() as db, PostgresSaver.from_conn_string(_dsn()) as checkpointer:
        checkpointer.setup()
        # get_state 只读 checkpoint、不执行节点；_Deps 的 session 传 None 安全（闭包不触发）
        deps = _Deps(db=db, session=None, resume_text="")
        graph = build_interview_graph(checkpointer, deps)
        state = graph.get_state(_thread(session_id))
        return bool(state.next)
