"""interview_sessions 加 resume_count 与 trace_json（S1 图编排指标）

Revision ID: e5ab510f2fef
Revises: bbbff7091c1c
Create Date: 2026-09-29 14:25:42.108834

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5ab510f2fef"
down_revision: str | None = "bbbff7091c1c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # S1 面试图编排指标（方案 §11.4 看板量化）：断点续跑次数 + 节点 trace 落库
    op.add_column(
        "interview_sessions",
        sa.Column("resume_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "interview_sessions",
        sa.Column("trace_json", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("interview_sessions", "trace_json")
    op.drop_column("interview_sessions", "resume_count")
