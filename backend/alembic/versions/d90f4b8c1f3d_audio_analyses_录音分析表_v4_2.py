"""audio_analyses 录音分析表（v4.2）

Revision ID: d90f4b8c1f3d
Revises: 855d9731b3ad
Create Date: 2026-09-29 21:23:18.288387

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd90f4b8c1f3d'
down_revision: Union[str, None] = '855d9731b3ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audio_analyses",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("duration_seconds", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("asr_model", sa.String(length=50), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("role_review_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("interview_review_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audio_analyses_user_id", "audio_analyses", ["user_id"])
    op.create_index("ix_audio_analyses_created_at", "audio_analyses", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audio_analyses_created_at", table_name="audio_analyses")
    op.drop_index("ix_audio_analyses_user_id", table_name="audio_analyses")
    op.drop_table("audio_analyses")
