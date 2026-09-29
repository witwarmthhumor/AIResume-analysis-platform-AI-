"""认证测试：注册（username）、登录（用户名/邮箱兼容）、改密、锁定、当前用户。

v4.1 A2 后本文件覆盖：
- 旧口径回归：email 注册（服务端派生 username）、email 字段登录——保证老客户端零感知；
- 新口径：自选 username 校验与 409、用户名登录、change-password 全端下线；
- 锁定迁 Redis 后的 429 语义与 fail-open。
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import engine
from app.main import app
from app.services.login_throttle import reset_failures

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_auth_users():
    """前后双清 auth- 前缀用户：前清防上一轮异常残留脏数据，后清不留垃圾行。

    本文件此前没有清理 fixture，注册用户靠其他文件的宽匹配"顺带打扫"——
    单跑 test_auth 必残留（v3.7 审计 P1 修复；一文件一前缀约定）。
    """
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE email LIKE 'auth-%'"))
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE email LIKE 'auth-%'"))


def email() -> str:
    return f"auth-{uuid.uuid4().hex[:10]}@example.com"


def _throttle_key(address: str) -> str:
    """TestClient 的 client.host 恒为 testclient，与 auth.login 的 key 拼法一致。"""
    return f"testclient:{address}"


def test_register_login_me_logout() -> None:
    address = email()
    credentials = {"email": address, "password": "correct-horse-123"}
    response = client.post("/api/auth/register", json=credentials)
    assert response.status_code == 201
    assert response.json()["user"]["email"] == address
    assert response.json()["user"]["username"]  # 过渡期派生的用户名已进响应
    assert "access_token" in response.cookies

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == address

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401

    login = client.post("/api/auth/login", json=credentials)
    assert login.status_code == 200
    assert client.get("/api/auth/me").json()["email"] == address

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT password_hash FROM users WHERE email = :email"),
            {"email": address},
        ).scalar_one()
    assert row != credentials["password"]


def test_duplicate_email_and_bad_login() -> None:
    address = email()
    credentials = {"email": address, "password": "correct-horse-123"}
    assert client.post("/api/auth/register", json=credentials).status_code == 201
    assert client.post("/api/auth/register", json=credentials).status_code == 409
    assert (
        client.post(
            "/api/auth/login", json={"email": address, "password": "wrong-pass-123"}
        ).status_code
        == 401
    )


def test_register_with_explicit_username() -> None:
    """自选用户名：合法可注册；重复 409；非法（大写/保留字/过短）422。"""
    address = email()
    ok = client.post(
        "/api/auth/register",
        json={
            "username": "auth_taker",
            "email": address,
            "password": "correct-horse-123",
        },
    )
    assert ok.status_code == 201
    assert ok.json()["user"]["username"] == "auth_taker"

    # 同 username 不同邮箱 → 用户名撞唯一键，409 且文案区分于邮箱冲突
    taken = client.post(
        "/api/auth/register",
        json={
            "username": "auth_taker",
            "email": email(),
            "password": "correct-horse-123",
        },
    )
    assert taken.status_code == 409
    assert "用户名" in taken.json()["message"]  # 全局错误处理器把 detail 收敛为 message

    for bad in ("Admin", "admin", "ab", "带空格 name"):
        resp = client.post(
            "/api/auth/register",
            json={
                "username": bad,
                "email": email(),
                "password": "correct-horse-123",
            },
        )
        assert resp.status_code == 422, bad


def test_login_by_username_and_legacy_email_field() -> None:
    """登录三种写法等价：username 字段填用户名 / 填邮箱 / 旧 email 字段。"""
    address = email()
    client.post(
        "/api/auth/register",
        json={
            "username": "auth_lby",
            "email": address,
            "password": "correct-horse-123",
        },
    )
    for payload in (
        {"username": "auth_lby", "password": "correct-horse-123"},
        {"username": address, "password": "correct-horse-123"},
        {"email": address, "password": "correct-horse-123"},  # 老客户端兼容
    ):
        resp = client.post("/api/auth/login", json=payload)
        assert resp.status_code == 200, payload
        assert resp.json()["user"]["username"] == "auth_lby"
    # 用户名大小写归一：大写输入也能命中
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "AUTH_LBY", "password": "correct-horse-123"},
        ).status_code
        == 200
    )


def test_change_password_invalidates_old_tokens() -> None:
    """改密 → 旧 cookie 立即 401（ver 失配）→ 新密码可登录、旧密码被拒。"""
    address = email()
    client.post(
        "/api/auth/register",
        json={"email": address, "password": "correct-horse-123"},
    )
    assert client.get("/api/auth/me").status_code == 200

    wrong_old = client.post(
        "/api/auth/change-password",
        json={"old_password": "not-the-old-pass", "new_password": "brand-new-pass-456"},
    )
    assert wrong_old.status_code == 400

    changed = client.post(
        "/api/auth/change-password",
        json={
            "old_password": "correct-horse-123",
            "new_password": "brand-new-pass-456",
        },
    )
    assert changed.status_code == 200
    # 改密响应清了 cookie；即便手动带着旧 cookie 来，ver 失配也必须 401
    assert client.get("/api/auth/me").status_code == 401

    assert (
        client.post(
            "/api/auth/login", json={"email": address, "password": "correct-horse-123"}
        ).status_code
        == 401
    )
    relogin = client.post(
        "/api/auth/login", json={"email": address, "password": "brand-new-pass-456"}
    )
    assert relogin.status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_login_lockout_after_max_failures() -> None:
    """连续失败达到上限后 429 锁定（Redis 计数）；锁定期间密码正确也拒绝。"""
    from app.core.config import settings

    address = email()
    credentials = {"email": address, "password": "correct-horse-123"}
    assert client.post("/api/auth/register", json=credentials).status_code == 201

    wrong = {"email": address, "password": "wrong-pass-123"}
    try:
        for _ in range(settings.login_max_failures):
            assert client.post("/api/auth/login", json=wrong).status_code == 401
        assert client.post("/api/auth/login", json=wrong).status_code == 429
        assert client.post("/api/auth/login", json=credentials).status_code == 429

        reset_failures(_throttle_key(address))  # 等价窗口过期/管理员解锁
        assert client.post("/api/auth/login", json=credentials).status_code == 200
    finally:
        reset_failures(_throttle_key(address))  # 清理 Redis 计数，不污染其他用例


def test_login_lock_fail_open_when_redis_down(monkeypatch) -> None:
    """Redis 挂掉：计数读写全部 fail-open，正常登录不受影响（评审 P2-5 裁定）。"""
    from app.services import login_throttle

    def _boom(*_args, **_kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(login_throttle, "_client", _boom)
    address = email()
    credentials = {"email": address, "password": "correct-horse-123"}
    assert client.post("/api/auth/register", json=credentials).status_code == 201
    # 先失败多次——若计数可用必触发 429；fail-open 下不应锁定
    for _ in range(15):
        client.post(
            "/api/auth/login", json={"email": address, "password": "wrong-pass-123"}
        )
    assert client.post("/api/auth/login", json=credentials).status_code == 200
