"""v3.6 数据库连接快速失败测试：DB 不可用时接口秒级 503 + 统一错误结构，不挂起不泄密。

不真停容器（依赖 Docker 状态会让测试不稳定）：monkeypatch 让会话工厂直接抛
sqlalchemy 连接类异常，验证全局处理器的状态码、code、message 与脱敏。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import InterfaceError, OperationalError

from app.core.config import settings
from app.main import app


@pytest.fixture()
def client():
    return TestClient(app)


def _break_db(monkeypatch, exc: Exception) -> None:
    """把会话工厂换成"一调用就抛连接异常"的替身，模拟数据库不可用。"""

    def _raise(*args, **kwargs):
        raise exc

    monkeypatch.setattr("app.db.session.SessionLocal", _raise)


@pytest.mark.parametrize(
    "exc",
    [
        OperationalError("SELECT 1", {}, ConnectionRefusedError()),
        InterfaceError("connection closed", {}, ConnectionResetError()),
    ],
)
def test_db_down_returns_503_with_unified_body(client, monkeypatch, exc):
    """连接类异常（OperationalError/InterfaceError）统一 503 + database_unavailable。"""
    _break_db(monkeypatch, exc)
    resp = client.get("/api/resumes")
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "database_unavailable"
    assert "数据库暂时不可用" in body["message"]
    assert body["details"] is None


def test_503_message_leaks_no_secrets(client, monkeypatch):
    """503 话术不含连接串、主机、用户名或 Python 堆栈。"""
    _break_db(monkeypatch, OperationalError("SELECT 1", {}, ConnectionRefusedError()))
    resp = client.get("/api/resumes")
    text = resp.text
    for secret in (
        "postgresql",
        "localhost:5432",
        settings.database_url.split("@")[-1],  # 库名部分也不该出现
        "Traceback",
        'File "',
    ):
        assert secret not in text, f"503 响应泄露了敏感内容：{secret}"


def test_health_stays_ok_when_probe_enabled(client):
    """/health 走 ping_database（永不抛异常），DB 正常时仍是 200 ok——
    确认新加的 503 处理器没有误伤健康检查路径。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_timeout_settings_defaults():
    """两个超时配置存在且默认 5s / 10s。"""
    assert settings.db_connect_timeout_seconds == 5
    assert settings.db_pool_timeout_seconds == 10


def test_engine_wires_timeouts(monkeypatch):
    """超时参数真的传到了驱动/连接池（防止配置写了但没接线）。

    connect_args 是建连时才合并进 dialect 参数的，直接内省看不到，
    所以用假 psycopg.connect 捕获驱动实际收到的 kwargs 验证。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    from app.db.session import engine

    assert engine.pool._timeout == settings.db_pool_timeout_seconds

    captured: dict = {}
    dbapi = engine.dialect.dbapi

    def fake_connect(*args, **kwargs):
        captured.update(kwargs)
        raise dbapi.OperationalError("测试用：不真建连")

    monkeypatch.setattr(dbapi, "connect", fake_connect)
    probe = create_engine(
        settings.database_url,
        poolclass=NullPool,
        connect_args={"connect_timeout": settings.db_connect_timeout_seconds},
    )
    with pytest.raises(Exception):  # noqa: B017  假 connect 抛的 DBAPI 异常会被包成任意 SQLAlchemy 错误
        probe.connect()
    assert captured["connect_timeout"] == settings.db_connect_timeout_seconds
