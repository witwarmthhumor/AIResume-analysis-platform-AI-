"""question_banks 面试题库表（v4.2）

Revision ID: 855d9731b3ad
Revises: 8e37f8d524ee
Create Date: 2026-09-29 15:51:20.535267

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '855d9731b3ad'
down_revision: Union[str, None] = '8e37f8d524ee'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "question_banks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("resume_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("questions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_question_banks_user_id", "question_banks", ["user_id"])
    op.create_index("ix_question_banks_resume_id", "question_banks", ["resume_id"])
    op.create_index("ix_question_banks_created_at", "question_banks", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_question_banks_created_at", table_name="question_banks")
    op.drop_index("ix_question_banks_resume_id", table_name="question_banks")
    op.drop_index("ix_question_banks_user_id", table_name="question_banks")
    op.drop_table("question_banks")
