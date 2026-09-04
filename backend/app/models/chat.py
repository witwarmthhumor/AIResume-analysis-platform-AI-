"""chat_sessions + chat_messages：在线对话（v3.1）/ AI 客服（v3.4）。

一场对话 = 一行 session + 多行 message；消息逐条落库 → 刷新可恢复。
归属与 interview_sessions 一致：登录用户按 user_id，匿名用户按 anonymous_id。
session 软删除（deleted_at），message 保留可审计（通过 session 过滤自动排除）。

v3.4：两形态共用这两张表——session_type 区分在线对话(chat)/AI 客服(agent)；
chat_messages.tool_steps 记录 Agent 工具调用过程（仅 agent 的 assistant 消息有值）。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# v3.4 会话类型：在线对话 / AI 客服
SESSION_TYPE_CHAT = "chat"
SESSION_TYPE_AGENT = "agent"


class ChatSession(Base, TimestampMixin):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    anonymous_id: Mapped[str | None] = mapped_column(String(64), index=True)

    # v3.4：chat=在线对话（默认，兼容旧数据）；agent=AI 客服。Python/DB 双端默认值兜底
    session_type: Mapped[str] = mapped_column(
        String(20),
        default=SESSION_TYPE_CHAT,
        server_default=SESSION_TYPE_CHAT,
        index=True,
    )
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
    tool_steps: Mapped[list | None] = mapped_column(JSONB)  # v3.4：Agent 工具调用过程
    tokens: Mapped[int | None]
