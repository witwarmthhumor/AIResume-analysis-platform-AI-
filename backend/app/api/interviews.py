"""模拟面试路由：建会话 / 恢复 / SSE 流式回答 / 结束评价。挂 /api 前缀。

流式协议（POST /messages 响应，text/event-stream）：
  event: meta  → {"turn": n, "stage": "..."}        本轮开始
  event: delta → {"content": "文字片段"}             AI 回复逐段推送
  event: done  → {"content": 全文, tokens..., duration_ms}  本轮完成
  event: error → {"content": 用户话术}               AI 调用失败（重试也没用）
"""

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import enforce_daily_limit, get_anonymous_id
from app.core.config import settings
from app.db.session import get_db
from app.models.interview import InterviewMessage, InterviewSession
from app.models.resume import Resume
from app.models.usage_log import UsageLog
from app.schemas.interview import (
    InterviewReport,
    MessageOut,
    SessionOut,
    StartSessionOut,
)
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


def _get_session(db: Session, session_id: int) -> InterviewSession:
    session = db.get(InterviewSession, session_id)
    if session is None:
        raise HTTPException(404, "面试会话不存在")
    return session


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
        messages=[MessageOut.model_validate(m) for m in messages],
        final_report=session.final_report_json,
    )


@router.post(
    "/resumes/{resume_id}/interviews", response_model=StartSessionOut, status_code=201
)
def start_interview(
    resume_id: int,
    db: Session = Depends(get_db),  # noqa: B008  FastAPI 依赖注入官方惯用法
    anonymous_id: str = Depends(get_anonymous_id),
) -> StartSessionOut:
    """基于已成功解析的简历开一场面试。开场白为固定话术，不耗 AI 调用。"""
    resume = db.get(Resume, resume_id)
    if resume is None or resume.deleted_at is not None:
        raise HTTPException(404, "简历记录不存在或已删除")
    if resume.parse_status != "success" or not resume.raw_text:
        raise HTTPException(400, "该简历未成功解析出文本，无法开始面试")

    session = InterviewSession(resume_id=resume_id, anonymous_id=anonymous_id)
    db.add(session)
    db.flush()  # 拿到 session.id 给开场白用
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


@router.get("/interviews/{session_id}", response_model=SessionOut)
def get_interview(
    session_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> SessionOut:
    """会话详情（含全部消息）：刷新页面后靠它恢复。"""
    return _session_out(db, _get_session(db, session_id))


@router.post("/interviews/{session_id}/messages")
def send_message(
    session_id: int,
    body: SendMessageIn,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
) -> StreamingResponse:
    """用户回答 → AI 回复 SSE 流式返回。每条 AI 回复记 usage_logs 并受每日限流。"""
    session = _get_session(db, session_id)
    if session.status != "in_progress":
        raise HTTPException(400, "该面试已结束")
    if session.turn_count >= settings.max_interview_turns:
        raise HTTPException(400, "已达到最大轮次，请结束面试查看评价报告")

    enforce_daily_limit(
        db, anonymous_id, settings.daily_interview_message_limit, "interview_message"
    )

    # 用户消息先落库（AI 失败也不丢用户的回答）
    db.add(
        InterviewMessage(session_id=session_id, role="candidate", content=body.content)
    )
    db.commit()

    # 组装对话：system（简历+阶段）+ 全部历史
    history = list(
        db.scalars(
            select(InterviewMessage)
            .where(InterviewMessage.session_id == session_id)
            .order_by(InterviewMessage.id)
        )
    )
    resume = db.get(Resume, session.resume_id)
    next_turn = session.turn_count + 1
    stage = stage_for_turn(next_turn, settings.max_interview_turns)
    messages = [
        {
            "role": "system",
            "content": build_interviewer_system_prompt(
                resume.raw_text, stage, next_turn, settings.max_interview_turns
            ),
        }
    ] + [
        {"role": "user" if m.role == "candidate" else "assistant", "content": m.content}
        for m in history
    ]

    def sse(event: str, payload: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def event_stream():
        started = time.monotonic()
        yield sse("meta", {"turn": next_turn, "stage": stage})

        usage: dict = {}
        chunks: list[str] = []
        try:
            for delta in stream_chat(messages, settings, usage):
                chunks.append(delta)
                yield sse("delta", {"content": delta})
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
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/interviews/{session_id}/finish", response_model=SessionOut)
def finish_interview(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
) -> SessionOut:
    """结束面试：基于全部对话生成分维度评价报告，status=finished。"""
    session = _get_session(db, session_id)
    if session.status != "in_progress":
        raise HTTPException(400, "该面试已结束，报告以现有内容为准")

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
