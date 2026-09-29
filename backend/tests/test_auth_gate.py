"""强制登录闸门真身测试（v4.1 A3）。

conftest 默认关闸门（auth_gate_enabled=False）保住匿名业务流用例；
本文件的 autouse fixture（模块级，晚于 conftest 执行）把生产语义显式打开——
闸门开启 + 停止签发匿名 cookie——在真实状态下验证默认拒绝行为。
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


@pytest.fixture(autouse=True)
def _gate_enabled(monkeypatch):
    """打开生产语义：闸门开、匿名 cookie 停签（覆盖 conftest 的测试缺省）。"""
    monkeypatch.setattr(settings, "auth_gate_enabled", True)
    monkeypatch.setattr(settings, "issue_anonymous_cookie", False)
    yield


def _email() -> str:
    return f"gate-{uuid.uuid4().hex[:10]}@example.com"


def test_anonymous_gets_401_on_every_api_path():
    """未登录：任意 /api/** 一律 401，包括不存在的路径（不泄露路径存在性）。"""
    client = TestClient(app)
    for path in ("/api/resumes", "/api/analyses", "/api/definitely-not-a-path"):
        resp = client.get(path)
        assert resp.status_code == 401, path


def test_health_outside_gate():
    """/health 在 /api 前缀之外，是基础设施探针，必须保持匿名可达。"""
    assert TestClient(app).get("/health").status_code == 200


def test_whitelist_allows_register_and_login():
    """白名单：注册/登录匿名可达；登录后即可访问业务接口。"""
    client = TestClient(app)
    address = _email()
    registered = client.post(
        "/api/auth/register", json={"email": address, "password": "correct-horse-123"}
    )
    assert registered.status_code == 201

    fresh = TestClient(app)  # 新客户端验证登录本身匿名可达
    logged = fresh.post(
        "/api/auth/login", json={"email": address, "password": "correct-horse-123"}
    )
    assert logged.status_code == 200
    # 带上会话 cookie 后业务接口放行
    fresh.cookies.update(logged.cookies)
    assert fresh.get("/api/auth/me").status_code == 200


def test_options_preflight_passes_gate():
    """OPTIONS 预检不携带 cookie，闸门必须放行（否则跨域/预检场景全挂）。"""
    client = TestClient(app)
    resp = client.options("/api/resumes")
    assert resp.status_code != 401  # 405（方法不允许）即证明闸门没拦


def test_no_anonymous_cookie_issued():
    """A3 起停止签发匿名 cookie：任何响应都不应再出现 anonymous_id。"""
    client = TestClient(app)
    client.post(
        "/api/auth/register", json={"email": _email(), "password": "correct-horse-123"}
    )
    client.get("/api/resumes")
    assert "anonymous_id" not in client.cookies
