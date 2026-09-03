"""add chat sessions and messages

Revision ID: cacaa643224a
Revises: d292ca7b478b
Create Date: 2026-09-03 22:54:37.935584

v3.1 在线对话：建 chat_sessions（会话元信息，软删除）+ chat_messages（消息，含引用来源）。
顺带补全 kb_documents / kb_chunks 缺失的 created_at 索引（历史遗留：建表迁移未带索引）。
HNSW 向量索引是手写 SQL 创建的，autogenerate 误报为 removed，本迁移不碰它。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cacaa643224a"
down_revision: str | None = "d292ca7b478b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # —— chat_messages ——
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages")),
    )
    op.create_index(op.f("ix_chat_messages_created_at"), "chat_messages", ["created_at"])
    op.create_index(op.f("ix_chat_messages_session_id"), "chat_messages", ["session_id"])

    # —— chat_sessions ——
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("anonymous_id", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_sessions")),
    )
    op.create_index(op.f("ix_chat_sessions_anonymous_id"), "chat_sessions", ["anonymous_id"])
    op.create_index(op.f("ix_chat_sessions_created_at"), "chat_sessions", ["created_at"])
    op.create_index(op.f("ix_chat_sessions_updated_at"), "chat_sessions", ["updated_at"])
    op.create_index(op.f("ix_chat_sessions_user_id"), "chat_sessions", ["user_id"])

    # —— 补全 kb 表缺失的 created_at 索引（历史遗留）——
    op.create_index(op.f("ix_kb_chunks_created_at"), "kb_chunks", ["created_at"])
    op.create_index(op.f("ix_kb_documents_created_at"), "kb_documents", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_kb_documents_created_at"), table_name="kb_documents")
    op.drop_index(op.f("ix_kb_chunks_created_at"), table_name="kb_chunks")
    op.drop_index(op.f("ix_chat_sessions_user_id"), table_name="chat_sessions")
    op.drop_index(op.f("ix_chat_sessions_updated_at"), table_name="chat_sessions")
    op.drop_index(op.f("ix_chat_sessions_created_at"), table_name="chat_sessions")
    op.drop_index(op.f("ix_chat_sessions_anonymous_id"), table_name="chat_sessions")
    op.drop_table("chat_sessions")
    op.drop_index(op.f("ix_chat_messages_session_id"), table_name="chat_messages")
    op.drop_index(op.f("ix_chat_messages_created_at"), table_name="chat_messages")
    op.drop_table("chat_messages")
