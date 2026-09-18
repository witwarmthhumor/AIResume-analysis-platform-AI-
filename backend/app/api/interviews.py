"""模拟面试路由：建会话 / 恢复 / SSE 流式回答 / 结束评价。挂 /api 前缀。

流式协议（POST /messages 响应，text/event-stream）：
  event: meta  → {"turn": n, "stage": "..."}        本轮开始
  event: delta → {"content": "文字片段"}             AI 回复逐段推送
  event: done  → {"content": 全文, tokens..., duration_ms}  本轮完成
  event: error → {"content": 用户话术}               AI 调用失败（重试也没用）
"""

import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth_deps import get_optional_current_user
from app.api.deps import enforce_daily_limit, get_anonymous_id
from app.core.config import settings
from app.db.session import get_db
from app.models.interview import InterviewMessage, InterviewSession
from app.models.resume import Resume
from app.models.usage_log import UsageLog
from app.models.user import User
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


class StartInterviewIn(BaseModel):
    position_type: str | None = Field(
        default=None, description="intern/fresh/senior，空=通用（P5 智能出题）"
    )


def _get_session(
    db: Session, session_id: int, user: User | None = None
) -> InterviewSession:
    session = db.get(InterviewSession, session_id)
    if session is None:
        raise HTTPException(404, "面试会话不存在")
    if user is not None and session.user_id != user.id:
        raise HTTPException(404, "面试会话不存在")
    if user is None and session.user_id is not None:
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


@router.get("/interviews/{session_id}", response_model=SessionOut)
def get_interview(
    session_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> SessionOut:
    """会话详情（含全部消息）：刷新页面后靠它恢复。"""
    return _session_out(db, _get_session(db, session_id, user))


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
    session = _get_session(db, session_id, user)
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
        db, anonymous_id, settings.daily_interview_message_limit, "interview_message"
    )

    # 用户消息先落库（AI 失败也不丢用户的回答）
    db.add(
        InterviewMessage(session_id=session_id, role="candidate", content=body.content)
    )
    db.commit()

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
    session = _get_session(db, session_id, user)
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
