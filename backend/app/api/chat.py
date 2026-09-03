"""在线对话会话管理（v3.1）：会话 CRUD + 消息查询。挂 /api/chat 前缀。

归属与 playground/ask 一致：登录用户按 user_id，匿名用户按 anonymous_id。
会话软删除（deleted_at），消息保留可审计（通过 session 过滤自动排除已删会话）。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth_deps import get_optional_current_user
from app.api.deps import enforce_daily_limit, get_anonymous_id
from app.core.config import settings
from app.db.session import get_db
from app.models.chat import ChatMessage, ChatSession
from app.models.user import User
from app.services.usage_service import write_usage

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _owner_filter(user: User | None, anonymous_id: str):
    """构造归属过滤条件：登录用户匹配 user_id，匿名匹配 anonymous_id。"""
    if user:
        return ChatSession.user_id == user.id
    return ChatSession.anonymous_id == anonymous_id


def _get_owned_session(db: Session, session_id: int, user: User | None, anonymous_id: str) -> ChatSession:
    """取会话并校验归属，不存在或无权限 → 404（不泄露存在性）。"""
    session = db.get(ChatSession, session_id)
    if session is None or session.deleted_at is not None:
        raise HTTPException(404, "对话不存在")
    if user:
        if session.user_id != user.id:
            raise HTTPException(404, "对话不存在")
    else:
        if session.anonymous_id != anonymous_id:
            raise HTTPException(404, "对话不存在")
    return session


@router.get("/sessions")
def list_sessions(
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> list[dict]:
    """当前用户的对话列表（未删除，按最近活跃倒序）。"""
    stmt = (
        select(ChatSession)
        .where(_owner_filter(user, anonymous_id), ChatSession.deleted_at.is_(None))
        .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
    )
    sessions = list(db.scalars(stmt))
    return [
        {
            "id": s.id,
            "title": s.title,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        }
        for s in sessions
    ]


class SessionCreateIn(BaseModel):
    title: str = Field(default="新对话", max_length=100)


@router.post("/sessions")
def create_session(
    body: SessionCreateIn,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> dict:
    """新建对话，返回会话对象。

    建会话本身要占库表行，与提问同等级别限额：每日次数上限 + 记账
    （chat_create 用量作为限流依据；提问另有 playground 配额）。
    """
    enforce_daily_limit(
        db,
        anonymous_id,
        settings.daily_chat_session_limit,
        "chat_create",
        user_id=user.id if user else None,
    )
    session = ChatSession(
        user_id=user.id if user else None,
        anonymous_id=anonymous_id if not user else None,
        title=body.title.strip() or "新对话",
    )
    db.add(session)
    write_usage(
        db,
        anonymous_id if user is None else None,
        user.id if user else None,
        "chat_create",
        None,
        None,
        None,
    )
    db.commit()
    db.refresh(session)
    return {
        "id": session.id,
        "title": session.title,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> dict:
    """删除对话（软删除，消息保留可审计）。"""
    session = _get_owned_session(db, session_id, user, anonymous_id)
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
    """获取对话的历史消息（按时间正序）。"""
    _get_owned_session(db, session_id, user, anonymous_id)
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
    )
    messages = list(db.scalars(stmt))
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "citations": m.citations,
            "tokens": m.tokens,
            "created_at": m.created_at,
        }
        for m in messages
    ]
