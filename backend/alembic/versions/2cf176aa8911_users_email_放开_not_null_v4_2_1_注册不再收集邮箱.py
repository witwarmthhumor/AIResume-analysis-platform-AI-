"""users.email 放开 NOT NULL（v4.2.1 注册不再收集邮箱）

Revision ID: 2cf176aa8911
Revises: d90f4b8c1f3d
Create Date: 2026-09-29 22:24:11.118274

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2cf176aa8911"
down_revision: Union[str, None] = "d90f4b8c1f3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # v4.2.1：注册不再收集邮箱——email 变可空；唯一索引保留（多 NULL 共存）
    op.alter_column("users", "email", existing_type=sa.String(255), nullable=True)


def downgrade() -> None:
    # 回滚前要求 email 无 NULL（若已有无邮箱账号需先补值才能降）
    op.alter_column("users", "email", existing_type=sa.String(255), nullable=False)
