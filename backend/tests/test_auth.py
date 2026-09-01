"""阶段4认证测试：注册、登录、当前用户与登出。"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import engine
from app.main import app

client = TestClient(app)


def email() -> str:
    return f"test-{uuid.uuid4().hex[:10]}@example.com"


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
            text("SELECT password_hash FROM users WHERE email = :email"), {"email": address}
        ).scalar_one()
    assert row != credentials["password"]


def test_duplicate_email_and_bad_login() -> None:
    address = email()
    credentials = {"email": address, "password": "correct-horse-123"}
    assert client.post("/api/auth/register", json=credentials).status_code == 201
    assert client.post("/api/auth/register", json=credentials).status_code == 409
    assert client.post(
        "/api/auth/login", json={"email": address, "password": "wrong-pass-123"}
    ).status_code == 401
