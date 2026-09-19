"""阶段4认证测试：注册、登录、当前用户与登出。"""

import uuid

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import engine
from app.main import app

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


def test_register_login_me_logout() -> None:
    address = email()
    credentials = {"email": address, "password": "correct-horse-123"}
    response = client.post("/api/auth/register", json=credentials)
    assert response.status_code == 201
    assert response.json()["user"]["email"] == address
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


def test_login_lockout_after_max_failures() -> None:
    """连续失败达到上限后 429 锁定；锁定期间密码正确也拒绝（防爆破语义）。"""
    from app.api import auth as auth_module

    address = email()
    credentials = {"email": address, "password": "correct-horse-123"}
    assert client.post("/api/auth/register", json=credentials).status_code == 201

    wrong = {"email": address, "password": "wrong-pass-123"}
    for _ in range(auth_module.settings.login_max_failures):
        assert client.post("/api/auth/login", json=wrong).status_code == 401
    assert client.post("/api/auth/login", json=wrong).status_code == 429
    assert client.post("/api/auth/login", json=credentials).status_code == 429

    auth_module._login_failures.clear()  # 清理内存计数，避免影响其他用例
    assert client.post("/api/auth/login", json=credentials).status_code == 200
