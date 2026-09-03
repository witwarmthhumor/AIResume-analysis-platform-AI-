"""chat_sessions + chat_messages：在线对话（v3.1）。

一场对话 = 一行 session + 多行 message；消息逐条落库 → 刷新可恢复。
归属与 interview_sessions 一致：登录用户按 user_id，匿名用户按 anonymous_id。
session 软删除（deleted_at），message 保留可审计（通过 session 过滤自动排除）。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ChatSession(Base, TimestampMixin):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    anonymous_id: Mapped[str | None] = mapped_column(String(64), index=True)

    title: Mapped[str] = mapped_column(String(100), default="新对话")
    # 列表排序依据：最近活跃的对话排前面
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )  # 软删除，与 kb_documents / resumes 同约定


class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 逻辑关联，不建物理外键（与项目惯例一致：interview_messages 也无外键）
    session_id: Mapped[int] = mapped_column(BigInteger, index=True)

    role: Mapped[str] = mapped_column(String(20))  # user / assistant
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[dict | None] = mapped_column(JSONB)  # AI 消息的引用来源
    tokens: Mapped[int | None]
