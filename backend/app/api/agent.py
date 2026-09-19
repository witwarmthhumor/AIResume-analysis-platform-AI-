"""AI 客服 Agent 接口（v3.4）：会话 CRUD + 全程 SSE 流式问答。挂 /api/agent 前缀。

与在线对话（api/chat.py + playground.py）平级、共用 chat_sessions/chat_messages 两表，
靠 session_type='agent' 隔离，互不串数据。归属规则一致：登录按 user_id，匿名按 anonymous_id。

/ask 流式事件：meta →（reset → action → observation）* → delta* → done；异常走 error。
工具过程（action/observation）与最终回答（delta）都实时下发，done 落库并带引用来源。
reset：模型在工具决策轮同时吐出的文本 token 不是最终回答，前端收到后清空累计的回答内容。
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth_deps import get_optional_current_user
from app.api.deps import enforce_daily_limit, get_anonymous_id, get_owned_chat_session
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.chat import (
    SESSION_TYPE_AGENT,
    ChatMessage,
    ChatSession,
)
from app.models.user import User
from app.services.agent import (
    ToolContext,
    build_agent_executor,
    build_chat_llm,
    build_history,
    make_tools,
    stream_agent_events,
)
from app.services.usage_service import write_usage

router = APIRouter(prefix="/api/agent", tags=["agent"])

logger = get_logger(__name__)


# —— 会话归属（限定 agent 类型，避免与在线对话串）——


def _owner_filter(user: User | None, anonymous_id: str):
    return (
        ChatSession.user_id == user.id
        if user
        else ChatSession.anonymous_id == anonymous_id
    )


def _get_owned_agent_session(
    db: Session, session_id: int, user: User | None, anonymous_id: str
) -> ChatSession:
    """取 AI 客服会话并校验归属与类型；不存在/无权限/类型不符一律 404。"""
    return get_owned_chat_session(
        db, session_id, user, anonymous_id, session_type=SESSION_TYPE_AGENT
    )


def _session_out(s: ChatSession) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "session_type": s.session_type,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


# —— 会话 CRUD ——


@router.get("/sessions")
def list_sessions(
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> list[dict]:
    """当前用户的 AI 客服会话列表（仅 agent 类型，按最近活跃倒序）。"""
    stmt = (
        select(ChatSession)
        .where(
            _owner_filter(user, anonymous_id),
            ChatSession.deleted_at.is_(None),
            ChatSession.session_type == SESSION_TYPE_AGENT,
        )
        .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
    )
    return [_session_out(s) for s in db.scalars(stmt)]


class AgentSessionCreateIn(BaseModel):
    title: str = Field(default="新对话", max_length=100)


@router.post("/sessions")
def create_session(
    body: AgentSessionCreateIn,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> dict:
    """新建 AI 客服会话（建会话占行，沿用建会话级防刷限额，独立 agent_create 记账）。"""
    enforce_daily_limit(
        db,
        anonymous_id,
        settings.daily_chat_session_limit,
        "agent_create",
        user_id=user.id if user else None,
    )
    session = ChatSession(
        user_id=user.id if user else None,
        anonymous_id=anonymous_id if not user else None,
        session_type=SESSION_TYPE_AGENT,
        title=body.title.strip() or "新对话",
    )
    db.add(session)
    write_usage(
        db,
        anonymous_id if user is None else None,
        user.id if user else None,
        "agent_create",
        None,
        None,
        None,
    )
    db.commit()
    db.refresh(session)
    return _session_out(session)


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> dict:
    """软删除 AI 客服会话。"""
    session = _get_owned_agent_session(db, session_id, user, anonymous_id)
    session.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "id": session_id}


@router.get("/sessions/{session_id}/messages")
def list_messages(
    session_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> list[dict]:
    """取 AI 客服会话历史消息（正序，含工具过程 tool_steps）。"""
    _get_owned_agent_session(db, session_id, user, anonymous_id)
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
    )
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "citations": m.citations,
            "tool_steps": m.tool_steps,
            "tokens": m.tokens,
            "created_at": m.created_at,
        }
        for m in db.scalars(stmt)
    ]


# —— 流式问答 ——


class AgentAskIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    session_id: int | None = Field(
        default=None, description="AI 客服会话 ID，传入则持久化"
    )


@router.post("/ask")
def ask(
    body: AgentAskIn,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> StreamingResponse:
    """AI 客服提问：Agent 自主调 kb_search，工具过程与最终回答全程流式。"""
    enforce_daily_limit(
        db,
        anonymous_id,
        settings.daily_agent_limit,
        "agent",
        user_id=user.id if user else None,
    )

    session: ChatSession | None = None
    if body.session_id is not None:
        session = _get_owned_agent_session(db, body.session_id, user, anonymous_id)

    # 构造 LLM/工具/执行器（未配置 AI 时在开流前直接 503，不耗资源）
    try:
        llm = build_chat_llm()
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    tool_ctx = ToolContext()
    tools = make_tools(db, user.id if user else None, anonymous_id, tool_ctx)
    executor = build_agent_executor(llm, tools)

    # 取最近 N 轮历史作为多轮上下文（工具步骤不进上下文）
    history = []
    if session is not None:
        rows = list(
            db.scalars(
                select(ChatMessage.role, ChatMessage.content)
                .where(ChatMessage.session_id == session.id)
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(settings.agent_history_turns * 2)
            )
        )
        rows.reverse()
        history = build_history(
            [(r[0], r[1]) for r in rows], settings.agent_history_turns
        )

    def _touch_and_save_user() -> None:
        """存用户消息；首条消息把会话标题改为问题前 20 字。"""
        if session is None:
            return
        try:
            is_first = (
                db.scalar(
                    select(ChatMessage.id)
                    .where(ChatMessage.session_id == session.id)
                    .limit(1)
                )
                is None
            )
            db.add(
                ChatMessage(session_id=session.id, role="user", content=body.content)
            )
            if is_first and session.title == "新对话":
                title = body.content.strip()[:20]
                if title:
                    session.title = title
            session.updated_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            logger.warning("agent 保存用户消息失败", exc_info=True)
            db.rollback()

    _touch_and_save_user()

    def _log_usage(tokens_total: int) -> None:
        write_usage(
            db,
            anonymous_id,
            user.id if user else None,
            "agent",
            settings.ai_model,
            tokens_total,
            request.client.host if request.client else None,
        )
        db.commit()

    def event_stream():
        yield _sse("meta", {"session_id": session.id if session else None})
        answer: list[str] = []
        final_event: dict | None = None
        accounted = False
        try:
            for ev in stream_agent_events(executor, body.content, history):
                etype = ev.get("type")
                if etype == "action":
                    yield _sse("action", {"tool": ev["tool"], "input": ev["input"]})
                elif etype == "reset":
                    # 工具决策轮的文本 token 不是最终回答：清累计，通知前端同步清空
                    answer.clear()
                    yield _sse("reset", {})
                elif etype == "observation":
                    yield _sse("observation", {"preview": ev["preview"]})
                elif etype == "error":
                    yield _sse("error", {"content": ev["content"]})
                elif etype == "delta":
                    answer.append(ev["content"])
                    yield _sse("delta", {"content": ev["content"]})
                elif etype == "fatal":
                    _log_usage(0)
                    accounted = True
                    yield _sse("error", {"content": ev["content"]})
                    return
                elif etype == "final":
                    final_event = ev

            # 走到这里后台 worker 已结束（队列哨兵已消费），可安全用 db 落库
            if final_event is None:
                _log_usage(0)
                accounted = True
                yield _sse("error", {"content": "AI 客服未返回结果，请重试"})
                return

            output = final_event["output"] or "".join(answer).strip()
            steps = final_event.get("steps", [])
            tokens_total = final_event.get("tokens_total", 0)
            if not output:
                _log_usage(0)
                accounted = True
                yield _sse("error", {"content": "AI 返回了空回复，请重试"})
                return

            message_id = None
            if session is not None:
                try:
                    ai_msg = ChatMessage(
                        session_id=session.id,
                        role="assistant",
                        content=output,
                        citations=tool_ctx.citations,
                        tool_steps=steps,
                        tokens=tokens_total,
                    )
                    db.add(ai_msg)
                    session.updated_at = datetime.now(timezone.utc)
                    db.commit()
                    db.refresh(ai_msg)
                    message_id = ai_msg.id
                except Exception:
                    logger.warning("agent 保存 AI 消息失败", exc_info=True)
                    db.rollback()

            _log_usage(tokens_total)
            accounted = True
            yield _sse(
                "done",
                {
                    "content": output,
                    "iterations": len(steps),
                    "tokens_total": tokens_total,
                    "citations": tool_ctx.citations,
                    "message_id": message_id,
                },
            )
        finally:
            if not accounted:
                # 客户端中途断开等未走完路径：补 0-token 账，防失败重试白嫖
                try:
                    _log_usage(0)
                except Exception:
                    logger.warning("agent 断开路径记账失败", exc_info=True)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
