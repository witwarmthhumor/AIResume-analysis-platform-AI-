"""v4.1 认证改造 A1：username 派生/校验规则与内置 admin 播种的单元测试。"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import engine
from app.main import app
from app.services.username_service import (
    RESERVED_USERNAMES,
    derive_username,
    is_valid_username,
)

client = TestClient(app)

_UNAME_PREFIX = "uname-"  # 一文件一前缀约定：本文件造的账号全部带此标记


@pytest.fixture(autouse=True)
def _clean_username_users():
    """前后双清 uname- 前缀账号（前清防残留让重名断言不稳定）。"""
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE email LIKE 'uname-%'"))
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE email LIKE 'uname-%'"))


# —— 纯函数：派生规则 ——


def test_derive_sanitizes_email_prefix() -> None:
    """现实邮箱前缀普遍含 . + - 与大写：清洗后只剩 [a-z0-9_]（非法字符直接剔除）。"""
    used: set = set()
    assert derive_username("Zhang.San+resume@x.com", used) == "zhangsanresume"
    assert derive_username("li-lei@x.com", used) == "lilei"


def test_derive_avoids_reserved_and_short_names() -> None:
    """保留字与清洗后过短的前缀都要加 user_ 前缀，不许注册出 admin。"""
    used: set = set()
    for reserved in RESERVED_USERNAMES:
        name = derive_username(f"{reserved}@x.com", used)
        assert name not in RESERVED_USERNAMES
        assert name.startswith("user_")
    assert derive_username("ab@x.com", used).startswith("user_")  # 不足 3 位
    assert derive_username("...@x.com", used) == "user"  # 清洗后为空


def test_derive_resolves_collisions_with_suffix() -> None:
    """重名追加 _2/_3；同一批回填循环连续调用天然去重（used 会累积）。"""
    used: set = {"zhangsan"}
    assert derive_username("zhangsan@x.com", used) == "zhangsan_2"
    assert derive_username("zhangsan@y.com", used) == "zhangsan_3"
    assert len(used) == 3


def test_is_valid_username_rejects_bad_input() -> None:
    assert is_valid_username("zhang_san_01")
    assert not is_valid_username("ab")  # 过短
    assert not is_valid_username("Zhang")  # 大写
    assert not is_valid_username("zhang san")  # 空格
    assert not is_valid_username("admin")  # 保留字


# —— 数据库路径：注册派生 + 内置 admin 播种 ——


def test_register_allocates_username_from_email() -> None:
    """A1 过渡期：注册入参只有 email，服务端按前缀分配 username 并入响应。"""
    address = f"{_UNAME_PREFIX}{uuid.uuid4().hex[:10]}@example.com"
    resp = client.post(
        "/api/auth/register", json={"email": address, "password": "correct-horse-123"}
    )
    assert resp.status_code == 201
    body = resp.json()["user"]
    assert body["username"].startswith("uname")  # 前缀清洗保留


def test_ensure_builtin_admin_is_idempotent(monkeypatch) -> None:
    """幂等：首跑创建，二跑不覆盖口令；role 被降级时会被补修回 admin。

    用 monkeypatch 换成一次性用户名/邮箱——绝不动真实内置 admin（admin@airesume.internal），
    测试造的数据按 uname- 前缀清理。
    """
    import scripts.seed_admin as seed_mod

    uname = f"admin_{uuid.uuid4().hex[:8]}"
    mail = f"uname-admin-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setattr(seed_mod, "ADMIN_USERNAME", uname)
    monkeypatch.setattr(seed_mod, "ADMIN_EMAIL", mail)

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        user, created, fixed = seed_mod.ensure_builtin_admin(db, "first-pass-123")
        assert created and user.role == "admin" and not fixed
        first_hash = user.password_hash

        user2, created2, fixed2 = seed_mod.ensure_builtin_admin(db, "second-pass-456")
        assert not created2 and not fixed2
        assert user2.password_hash == first_hash  # 二跑不改口令

        with engine.begin() as conn:  # 模拟管理员 role 被误改 → 脚本应补修
            conn.execute(
                text("UPDATE users SET role = 'user' WHERE username = :u"), {"u": uname}
            )
        db.expire_all()  # 裸 SQL 绕过了身份映射，过期缓存让第三次查询看到真实 role
        user3, created3, fixed3 = seed_mod.ensure_builtin_admin(db, "third-pass-789")
        assert not created3 and fixed3 and user3.role == "admin"
    finally:
        db.close()
        with engine.begin() as conn:  # 清理本用例造的账号
            conn.execute(
                text("DELETE FROM users WHERE username = :u OR email = :e"),
                {"u": uname, "e": mail},
            )
