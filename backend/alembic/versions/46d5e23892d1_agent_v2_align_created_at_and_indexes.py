"""agent v2 四表与模型对齐：created_at 补 NOT NULL + 索引补齐

Revision ID: 46d5e23892d1
Revises: 08c3d31e023c
Create Date: 2026-09-26

背景：b7e4a1c90d52 是手写迁移，漏掉了 TimestampMixin 约定的 created_at
NOT NULL 与 created_at 索引（其余表都由迁移建齐），session_id 索引也只写在
模型里。模型登记进 app/models/__init__.py 后由 `alembic check` 暴露——
本迁移一次补齐，让模型与库结构一致，避免下次 autogenerate 产出
"补索引/改可空性"的假迁移噪声。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "46d5e23892d1"
down_revision: str | None = "08c3d31e023c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("agent_runs", "agent_approvals", "agent_spans", "audit_logs")


def upgrade() -> None:
    # created_at 全由 server_default=now() 填充，历史行不可能为 NULL，直接收紧可空性
    for table in _TABLES:
        op.alter_column(
            table,
            "created_at",
            existing_type=postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            existing_server_default=sa.text("now()"),
        )
        op.create_index(f"ix_{table}_created_at", table, ["created_at"])
    # 模型里 session_id index=True（按会话定位 run），此前库里漏建
    op.create_index("ix_agent_runs_session_id", "agent_runs", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_session_id", table_name="agent_runs")
    for table in _TABLES:
        op.drop_index(f"ix_{table}_created_at", table_name=table)
        op.alter_column(
            table,
            "created_at",
            existing_type=postgresql.TIMESTAMP(timezone=True),
            nullable=True,
            existing_server_default=sa.text("now()"),
        )
