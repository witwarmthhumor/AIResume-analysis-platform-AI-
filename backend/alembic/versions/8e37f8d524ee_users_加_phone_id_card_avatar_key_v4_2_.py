"""users 加 phone id_card avatar_key（v4.2 个人信息）

Revision ID: 8e37f8d524ee
Revises: e5ab510f2fef
Create Date: 2026-09-29 15:34:05.280098

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8e37f8d524ee'
down_revision: Union[str, None] = 'e5ab510f2fef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # phone 唯一可空：Postgres 唯一索引允许多行 NULL（未绑手机号的存量账号共存）
    op.add_column(
        "users", sa.Column("phone", sa.String(length=20), nullable=True)
    )
    op.create_index("ix_users_phone", "users", ["phone"], unique=True)
    op.add_column("users", sa.Column("id_card", sa.String(length=32), nullable=True))
    op.add_column(
        "users", sa.Column("avatar_key", sa.String(length=30), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("users", "avatar_key")
    op.drop_column("users", "id_card")
    op.drop_index("ix_users_phone", table_name="users")
    op.drop_column("users", "phone")
