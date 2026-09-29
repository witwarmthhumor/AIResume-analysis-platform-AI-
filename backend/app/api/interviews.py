"""模拟面试路由：建会话 / 恢复 / SSE 流式回答 / 结束评价。挂 /api 前缀。

流式协议（POST /messages 响应，text/event-stream）：
  event: meta  → {"turn": n, "stage": "..."}        本轮开始
  event: delta → {"content": "文字片段"}             AI 回复逐段推送
  event: done  → {"content": 全文, tokens..., duration_ms}  本轮完成
  event: error → {"content": 用户话术}               AI 调用失败（重试也没用）
"""

import json
import time
from collections.abc import Iterator

from app.core.logging import get_logger

logger = get_logger(__name__)
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user, get_optional_current_user
from app.api.deps import enforce_daily_limit, get_anonymous_id
from app.core.config import settings
from app.db.session import get_db
from app.models.interview import InterviewMessage, InterviewSession
from app.models.question_bank import QuestionBank
from app.models.resume import Resume
from app.models.usage_log import UsageLog
from app.models.user import User
from app.schemas.interview import (
    InterviewReport,
    MessageOut,
    SessionOut,
    StartSessionOut,
)
from app.services import interview_graph
from app.services.ai_client import AIError, chat_json, stream_chat
from app.services.interview_prompts import (
    INTERVIEW_PROMPT_VERSION,
    OPENING_MESSAGE,
    build_final_report_system_prompt,
    build_interviewer_system_prompt,
    stage_for_turn,
)

router = APIRouter(prefix="/api", tags=["interviews"])


class SendMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class StartInterviewIn(BaseModel):
    position_type: str | None = Field(
        default=None, description="intern/fresh/senior，空=通用（P5 智能出题）"
    )
    bank_id: int | None = Field(
        default=None, description="v4.2 题库驱动：开局加载的题库 id（仅图路径生效）"
    )


def _touch_last_session(db: Session, user: User | None, session_id: int) -> None:
    """记录「最近活跃会话」到 users.last_active_session_id（v4.1 A4·L2）。

    「继续上次会话」快捷入口的数据源；匿名请求无用户可挂，跳过。
    同事务内随业务提交，不额外 commit。
    """
    if user is not None and user.last_active_session_id != session_id:
        user.last_active_session_id = session_id


def _get_session(
    db: Session,
    session_id: int,
    user: User | None = None,
    anonymous_id: str | None = None,
) -> InterviewSession:
    """取归属者自己的面试会话：匿名必须同时满足 user_id 为空且 anonymous_id 相等，
    否则任意匿名访客可凭自增 ID 读/写/结束他人会话（横向越权）。"""
    session = db.get(InterviewSession, session_id)
    if session is None:
        raise HTTPException(404, "面试会话不存在")
    if user is not None and session.user_id != user.id:
        raise HTTPException(404, "面试会话不存在")
    if user is None and (
        session.user_id is not None or session.anonymous_id != anonymous_id
    ):
        raise HTTPException(404, "面试会话不存在")
    return session


def _abandon_if_stale(db: Session, session: InterviewSession) -> bool:
    """会话超时无活动则置 abandoned（P1）。返回是否已置为 abandoned。"""
    if session.status != "in_progress":
        return False
    last_message = db.scalar(
        select(InterviewMessage.created_at)
        .where(InterviewMessage.session_id == session.id)
        .order_by(InterviewMessage.id.desc())
        .limit(1)
    )
    last_active = last_message or session.created_at
    idle_minutes = (datetime.now(timezone.utc) - last_active).total_seconds() / 60
    if idle_minutes >= settings.interview_abandon_minutes:
        session.status = "abandoned"
        db.commit()
        return True
    return False


def _session_out(db: Session, session: InterviewSession) -> SessionOut:
    messages = list(
        db.scalars(
            select(InterviewMessage)
            .where(InterviewMessage.session_id == session.id)
            .order_by(InterviewMessage.id)
        )
    )
    return SessionOut(
        id=session.id,
        resume_id=session.resume_id,
        status=session.status,
        stage=session.stage,
        turn_count=session.turn_count,
        max_turns=settings.max_interview_turns,
        position_type=session.position_type,
        messages=[MessageOut.model_validate(m) for m in messages],
        final_report=session.final_report_json,
    )


@router.post(
    "/resumes/{resume_id}/interviews", response_model=StartSessionOut, status_code=201
)
def start_interview(
    resume_id: int,
    body: StartInterviewIn | None = None,
    request: Request = None,  # 图路径记账取客户端 IP（FastAPI 注入 Request，恒非空）
    db: Session = Depends(get_db),  # noqa: B008  FastAPI 依赖注入官方惯用法
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> StartSessionOut:
    """基于已成功解析的简历开一场面试。开场白为固定话术，不耗 AI 调用。"""
    resume = db.get(Resume, resume_id)
    if resume is None or resume.deleted_at is not None:
        raise HTTPException(404, "简历记录不存在或已删除")
    if user is not None and resume.user_id != user.id:
        raise HTTPException(404, "简历记录不存在或已删除")
    if user is None and resume.user_id is not None:
        raise HTTPException(404, "简历记录不存在或已删除")
    if resume.parse_status != "success" or not resume.raw_text:
        raise HTTPException(400, "该简历未成功解析出文本，无法开始面试")

    existing = db.scalar(
        select(InterviewSession)
        .where(
            InterviewSession.resume_id == resume_id,
            InterviewSession.status == "in_progress",
            (
                InterviewSession.user_id == user.id
                if user is not None
                else InterviewSession.user_id.is_(None)
            ),
            (InterviewSession.anonymous_id == anonymous_id if user is None else True),
        )
        .order_by(InterviewSession.created_at.desc(), InterviewSession.id.desc())
    )
    if existing is not None:
        # 如果已有进行中会话但超时→废弃它，走新建流程（P1 abandoned）
        if not _abandon_if_stale(db, existing):
            _touch_last_session(db, user, existing.id)
            db.commit()  # 恢复路径也要落 last_active_session_id（原路径无写操作）
            return StartSessionOut(
                interview_prompt_version=INTERVIEW_PROMPT_VERSION,
                session=_session_out(db, existing),
            )
        existing = None

    session = InterviewSession(
        resume_id=resume_id,
        user_id=user.id if user is not None else None,
        anonymous_id=anonymous_id if user is None else None,
        position_type=(body.position_type if body else None),
    )
    db.add(session)
    db.flush()  # 拿到 session.id
    _touch_last_session(db, user, session.id)

    # v4.2 题库解析（先于图分支）：归属校验 + 取题；仅图路径支持（legacy 明确拒绝）
    bank_questions: list = []
    if body and body.bank_id:
        bank = db.get(QuestionBank, body.bank_id)
        if bank is None or user is None or bank.user_id != user.id:
            raise HTTPException(404, "题库不存在或已删除")
        bank_questions = [
            q
            for q in bank.questions_json.get("questions", [])
            if str(q.get("question") or "").strip()
        ]
        if not bank_questions:
            raise HTTPException(400, "该题库没有可用题目")
        if not settings.interview_graph_enabled:
            raise HTTPException(400, "题库加载需要面试图编排开启（interview_graph_enabled）")
    if settings.interview_graph_enabled:
        # S1 图路径：开场白与第一问由图节点落库（第一问经 LLM 生成，起点即"已提问"），
        # checkpoint 挂在 wait_answer 等候选人作答；崩溃后从 checkpoint 续跑
        db.commit()  # 先落会话行（图节点用同一会话写消息与状态）
        tokens_used = interview_graph.graph_start(db, session, bank_questions)
        if tokens_used:
            db.add(
                UsageLog(
                    user_id=user.id if user else None,
                    anonymous_id=anonymous_id,
                    action_type="interview_message",
                    model_name=settings.ai_model,
                    tokens_total=tokens_used,
                    ip_address=request.client.host if request.client else None,
                )
            )
            db.commit()
        db.refresh(session)
        return StartSessionOut(
            interview_prompt_version=INTERVIEW_PROMPT_VERSION,
            session=_session_out(db, session),
        )

    db.add(
        InterviewMessage(
            session_id=session.id, role="interviewer", content=OPENING_MESSAGE
        )
    )
    db.commit()
    db.refresh(session)
    return StartSessionOut(
        interview_prompt_version=INTERVIEW_PROMPT_VERSION,
        session=_session_out(db, session),
    )


@router.get("/interviews/scores")
def list_interview_scores(
    limit: int = 10,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> dict:
    """本人已结束场次的分维度评分列表（v3.5：面试报告雷达图的历史对比数据源）。

    只返回当前归属者自己的场次（登录按 user_id、匿名按 anonymous_id），按时间倒序；
    只挑有结束评价报告的场次——没报告的场次没有评分可比。
    路径必须声明在 `/interviews/{session_id}` 之前，否则 scores 会被当成 session_id。
    """
    owner = (
        InterviewSession.user_id == user.id
        if user is not None
        else InterviewSession.anonymous_id == anonymous_id
    )
    # limit 容错：非正数回落默认 10，上限 30（雷达图对比一次用不了更多）
    safe_limit = 10 if limit <= 0 else min(limit, 30)
    rows = db.scalars(
        select(InterviewSession)
        .where(
            owner,
            InterviewSession.status == "finished",
            InterviewSession.final_report_json.is_not(None),
        )
        .order_by(InterviewSession.created_at.desc(), InterviewSession.id.desc())
        .limit(safe_limit)
    ).all()

    items = []
    for session in rows:
        report = session.final_report_json
        # 兜底：JSONB 列写入 Python None 会落成 JSON null 字面量（不是 SQL NULL），
        # 光靠 is_not(None) 过滤不掉，这里再判一次类型，避免把空报告当成绩返回
        if not isinstance(report, dict):
            continue
        items.append(
            {
                "id": session.id,
                "created_at": session.created_at.isoformat()
                if session.created_at
                else None,
                "position_type": session.position_type,
                "turn_count": session.turn_count,
                "summary": report.get("summary"),
                "scores": {
                    key: report.get(key)
                    for key in (
                        "technical_depth",
                        "communication",
                        "project_authenticity",
                        "overall",
                    )
                },
            }
        )
    return {"items": items}


@router.get("/interviews/last", response_model=SessionOut)
def get_last_interview(
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> SessionOut:
    """「继续上次会话」数据源（v4.1 A4·L2）：最近一场面试的完整消息。

    优先取 users.last_active_session_id（指针指向的场次必须仍归属本人），
    指针为空/失效回落「最近一场」；一场都没有 → 404（前端据此隐藏入口）。
    路径声明必须在 /interviews/{session_id} 之前，否则 last 被当成 session_id。
    """
    session = None
    if user.last_active_session_id is not None:
        candidate = db.get(InterviewSession, user.last_active_session_id)
        if candidate is not None and candidate.user_id == user.id:
            session = candidate
    if session is None:
        session = db.scalar(
            select(InterviewSession)
            .where(InterviewSession.user_id == user.id)
            .order_by(InterviewSession.created_at.desc(), InterviewSession.id.desc())
        )
    if session is None:
        raise HTTPException(404, "还没有面试记录")
    return _session_out(db, session)


@router.get("/interviews/{session_id}", response_model=SessionOut)
def get_interview(
    session_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
) -> SessionOut:
    """会话详情（含全部消息）：刷新页面后靠它恢复。"""
    return _session_out(db, _get_session(db, session_id, user, anonymous_id))


@router.post("/interviews/{session_id}/messages")
def send_message(
    session_id: int,
    body: SendMessageIn,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> StreamingResponse:
    """用户回答 → AI 回复 SSE 流式返回。每条 AI 回复记 usage_logs 并受每日限流。"""
    session = _get_session(db, session_id, user, anonymous_id)
    if session.status != "in_progress":
        raise HTTPException(400, "该面试已结束")
    if _abandon_if_stale(db, session):
        raise HTTPException(
            400,
            f"该面试会话因超过 {settings.interview_abandon_minutes} 分钟无活动已自动结束，"
            "请返回重新开始",
        )
    if session.turn_count >= settings.max_interview_turns:
        raise HTTPException(400, "已达到最大轮次，请结束面试查看评价报告")

    enforce_daily_limit(
        db,
        anonymous_id,
        settings.daily_interview_message_limit,
        "interview_message",
        user_id=user.id if user else None,
    )

    # 用户消息先落库（AI 失败也不丢用户的回答）
    db.add(
        InterviewMessage(session_id=session_id, role="candidate", content=body.content)
    )
    _touch_last_session(db, user, session_id)  # 每轮活跃都刷新「继续上次会话」指针
    db.commit()

    def sse(event: str, payload: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    # —— S1 图路径：图会话（有 checkpoint）从 checkpoint 续跑作答 ——
    # 每次续跑都从 PostgresSaver 恢复状态：进程重启后同一入口即可续跑（崩溃恢复同源）
    if settings.interview_graph_enabled and interview_graph.has_checkpoint(session_id):
        try:
            last = interview_graph.graph_answer(db, session, body.content)
        except AIError as fail:
            # 异常对象在 except 块外会被解绑，先取值再进闭包（F821 防线）
            error_message = fail.message

            def graph_error_stream() -> Iterator[str]:
                yield sse("error", {"content": error_message})

            return StreamingResponse(
                graph_error_stream(), media_type="text/event-stream"
            )
        question = last.get("last_question") or ""
        tokens_used = last.get("tokens_used") or 0
        db.add(
            UsageLog(
                user_id=user.id if user else None,
                anonymous_id=anonymous_id,
                action_type="interview_message",
                model_name=settings.ai_model,
                tokens_total=tokens_used,
                ip_address=request.client.host if request.client else None,
            )
        )
        db.commit()
        ask_turn = session.turn_count
        ask_stage = stage_for_turn(ask_turn, settings.max_interview_turns)

        def graph_event_stream() -> Iterator[str]:
            # 前端协议保持 meta/delta/done 不变：图内生成的问题按块推送（协议兼容）
            yield sse("meta", {"turn": ask_turn, "stage": ask_stage})
            step = 24
            for i in range(0, len(question), step):
                yield sse("delta", {"content": question[i : i + step]})
            yield sse(
                "done",
                {
                    "turn": ask_turn,
                    "stage": ask_stage,
                    "content": question,
                    "tokens_prompt": None,
                    "tokens_completion": None,
                    "duration_ms": None,
                },
            )

        return StreamingResponse(
            graph_event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # 对话历史截断（P1）：保留最近 6 条，控制 token 用量
    _RECENT_KEEP = 6
    history = list(
        db.scalars(
            select(InterviewMessage)
            .where(InterviewMessage.session_id == session_id)
            .order_by(InterviewMessage.id.desc())
            .limit(_RECENT_KEEP)
        )
    )
    history = list(reversed(history))
    resume = db.get(Resume, session.resume_id)
    next_turn = session.turn_count + 1
    stage = stage_for_turn(next_turn, settings.max_interview_turns)
    messages = [
        {
            "role": "system",
            "content": build_interviewer_system_prompt(
                resume.raw_text,
                stage,
                next_turn,
                settings.max_interview_turns,
                session.position_type,
            ),
        }
    ] + [
        {"role": "user" if m.role == "candidate" else "assistant", "content": m.content}
        for m in history
    ]

    def event_stream():
        started = time.monotonic()
        yield sse("meta", {"turn": next_turn, "stage": stage})

        usage: dict = {}
        chunks: list[str] = []
        try:
            for delta in stream_chat(messages, settings, usage):
                chunks.append(delta)
                yield sse("delta", {"content": delta})
        except GeneratorExit:
            # 客户端中途断开：补一条 0-token 账，已消耗的 token 不白嫖
            # （对齐 playground/agent 的断开补账范式；正常完成不会进这个分支）
            try:
                db.add(
                    UsageLog(
                        user_id=user.id if user else None,
                        anonymous_id=anonymous_id,
                        action_type="interview_message",
                        model_name=settings.ai_model,
                        tokens_total=0,
                        ip_address=request.client.host if request.client else None,
                    )
                )
                db.commit()
            except Exception:
                logger.warning("interview 断开路径记账失败", exc_info=True)
            raise
        except AIError as exc:
            yield sse("error", {"content": exc.message})
            return

        full_text = "".join(chunks)
        if not full_text.strip():  # 流正常结束但没内容：按异常处理，不留空消息
            yield sse("error", {"content": "AI 返回了空回复，请重试"})
            return

        duration_ms = int((time.monotonic() - started) * 1000)
        tokens_p, tokens_c = usage.get("tokens_prompt"), usage.get("tokens_completion")
        db.add(
            InterviewMessage(
                session_id=session_id,
                role="interviewer",
                content=full_text,
                tokens=tokens_c,
            )
        )
        session.turn_count = next_turn
        # stage 语义固定为"下一问所处阶段"（建会话时 intro = stage_for_turn(1) 一致）
        session.stage = stage_for_turn(
            session.turn_count + 1, settings.max_interview_turns
        )
        db.add(
            UsageLog(
                # user_id 必须写：v3.3 使用日志接口按 user_id 过滤，漏写会让登录用户的面试记录不可见
                user_id=user.id if user else None,
                anonymous_id=anonymous_id,
                action_type="interview_message",
                model_name=settings.ai_model,
                tokens_total=(tokens_p or 0) + (tokens_c or 0),
                ip_address=request.client.host if request.client else None,
            )
        )
        db.commit()
        yield sse(
            "done",
            {
                "turn": next_turn,
                "stage": stage,
                "content": full_text,
                "tokens_prompt": tokens_p,
                "tokens_completion": tokens_c,
                "duration_ms": duration_ms,
            },
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # 禁止 Nginx 等反向代理缓冲本响应，否则 SSE 逐段推送会退化成块状
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/interviews/{session_id}/finish", response_model=SessionOut)
def finish_interview(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> SessionOut:
    """结束面试：基于全部对话生成分维度评价报告，status=finished。"""
    session = _get_session(db, session_id, user, anonymous_id)
    if session.status != "in_progress":
        raise HTTPException(400, "该面试已结束，报告以现有内容为准")
    if _abandon_if_stale(db, session):
        raise HTTPException(
            400,
            f"该面试会话因超过 {settings.interview_abandon_minutes} 分钟无活动已自动结束，"
            "无法生成评价",
        )

    history = list(
        db.scalars(
            select(InterviewMessage)
            .where(InterviewMessage.session_id == session_id)
            .order_by(InterviewMessage.id)
        )
    )
    if not any(m.role == "candidate" for m in history):
        raise HTTPException(400, "还没有任何回答，无法生成评价报告")

    resume = db.get(Resume, session.resume_id)
    transcript = "\n".join(
        f"{'面试官' if m.role == 'interviewer' else '候选人'}：{m.content}"
        for m in history
    )

    # —— S1 图路径：图会话经 Command(resume={"finish": True}) 续跑出报告 ——
    if settings.interview_graph_enabled and interview_graph.has_checkpoint(session_id):
        try:
            last = interview_graph.graph_answer(db, session, {"finish": True})
        except AIError as exc:
            raise HTTPException(502, exc.message) from exc
        db.add(
            UsageLog(
                user_id=user.id if user else None,
                anonymous_id=anonymous_id,
                action_type="interview_message",
                model_name=settings.ai_model,
                tokens_total=last.get("tokens_used") or 0,
                ip_address=request.client.host if request.client else None,
            )
        )
        db.commit()
        db.refresh(session)
        return _session_out(db, session)

    try:
        result = chat_json(
            build_final_report_system_prompt(resume.raw_text, transcript),
            "请输出结束评价 JSON",
            settings,
            InterviewReport.model_validate,
        )
    except AIError as exc:
        raise HTTPException(502, exc.message) from exc

    session.status = "finished"
    session.final_report_json = result.report
    db.add(
        UsageLog(
            # user_id 必须写：v3.3 使用日志接口按 user_id 过滤，漏写会让登录用户的面试记录不可见
            user_id=user.id if user else None,
            anonymous_id=anonymous_id,
            action_type="interview_message",
            model_name=result.model_name,
            tokens_total=(result.tokens_prompt or 0) + (result.tokens_completion or 0),
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(session)
    return _session_out(db, session)
