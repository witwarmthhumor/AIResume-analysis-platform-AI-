"""users 加 username（回填）与 token_version / last_active_session_id

v4.1 企业级改造·A1（方案 §5）：登录标识 email → username。
三段式而非 server_default（评审裁定）：NOT NULL 新列带共享默认值会在回填后
留下第二行即撞唯一约束的 `''` 残留默认——正确姿势是加 nullable 列 → 回填 →
收紧 NOT NULL + 唯一索引，全程不落 server_default。

回填规则与 app/services/username_service.py 同一套（小写化、剔非法字符、
保留字与短名加 user_ 前缀、重名 _2/_3）；迁移**自含一份快照**而不 import
应用代码——迁移是冻结的历史快照，应用侧规则日后演进不影响已执行的迁移。

Revision ID: bbbff7091c1c
Revises: 46d5e23892d1
Create Date: 2026-09-29 13:06:29.925828

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bbbff7091c1c"
down_revision: str | None = "46d5e23892d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RESERVED = {"admin", "administrator", "root", "system"}


def _derive_username(email: str, used: set) -> str:
    """email 前缀 → 合法用户名（与 username_service.derive_username 同规则快照）。"""
    import re

    base = email.split("@", 1)[0].strip().lower()
    base = re.compile(r"[^a-z0-9_]").sub("", base)
    if len(base) > 64:
        base = base[:64]
    if not base or base in _RESERVED or len(base) < 3:
        base = f"user_{base}" if base else "user"
    candidate, n = base, 1
    while candidate in used:
        n += 1
        candidate = f"{base}_{n}"
    used.add(candidate)
    return candidate


def upgrade() -> None:
    conn = op.get_bind()
    # ① 三列全以可回填形态加入（token_version 是 int NOT NULL，带 '0' 默认值安全：
    #    它语义就是计数器，无"残值撞唯一"问题；username 决不能这么干）
    op.add_column("users", sa.Column("username", sa.String(length=64), nullable=True))
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "users", sa.Column("last_active_session_id", sa.BigInteger(), nullable=True)
    )
    # ② 回填：按 id 顺序派生，打印 email → username 对照表（方案 R4 验收项）
    used: set = set(
        conn.execute(sa.text("SELECT username FROM users WHERE username IS NOT NULL"))
        .scalars()
        .all()
    )
    rows = conn.execute(sa.text("SELECT id, email FROM users ORDER BY id")).fetchall()
    if rows:
        print(f"\n[backfill] 共 {len(rows)} 个账号，email → username 对照表：")
    for uid, email in rows:
        username = _derive_username(email, used)
        conn.execute(
            sa.text("UPDATE users SET username = :u WHERE id = :i"),
            {"u": username, "i": uid},
        )
        print(f"  {email} -> {username}")
    # ③ 收紧：NOT NULL + 唯一索引（命名与模型 unique/index 默认 ix_users_username 一致，
    #    否则 alembic check 会报模型与库漂移）
    op.alter_column("users", "username", existing_type=sa.String(64), nullable=False)
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "last_active_session_id")
    op.drop_column("users", "token_version")
    op.drop_column("users", "username")
