"""add session_type and tool_steps for v3.4 agent

Revision ID: f3a91c2b407d
Revises: cacaa643224a
Create Date: 2026-09-04 14:20:00.000000

v3.4 AI 客服：chat_sessions/chat_messages 两表在线对话与 AI 客服共用。
- chat_sessions.session_type：chat=在线对话（默认，兼容旧数据）/ agent=AI 客服，带索引供列表按类型过滤。
- chat_messages.tool_steps：JSONB，记录 Agent 工具调用过程（仅 agent 的 assistant 消息有值）。
NOT NULL 列带 server_default，旧行自动填 'chat'，无需手工回填。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a91c2b407d"
down_revision: str | None = "cacaa643224a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 旧在线对话行由 server_default 自动填 'chat'
    op.add_column(
        "chat_sessions",
        sa.Column(
            "session_type",
            sa.String(length=20),
            nullable=False,
            server_default="chat",
        ),
    )
    op.create_index(
        op.f("ix_chat_sessions_session_type"), "chat_sessions", ["session_type"]
    )

    op.add_column(
        "chat_messages",
        sa.Column("tool_steps", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "tool_steps")
    op.drop_index(op.f("ix_chat_sessions_session_type"), table_name="chat_sessions")
    op.drop_column("chat_sessions", "session_type")
