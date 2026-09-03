"""v3.3 使用日志明细接口测试：认证、归属隔离、分页、筛选、倒序。"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import engine
from app.main import app


def _email() -> str:
    return f"test-usage-{uuid.uuid4().hex[:10]}@example.com"


def _register(client: TestClient) -> str:
    addr = _email()
    client.post("/api/auth/register", json={"email": addr, "password": "correct-horse-123"})
    return addr


def _user_id(conn, email: str) -> int:
    return conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email}).scalar()


def _insert_log(conn, *, user_id, action_type, model_name=None, tokens=0, ip=None, created_at) -> None:
    conn.execute(
        text(
            """INSERT INTO usage_logs
               (user_id, anonymous_id, action_type, model_name, tokens_total, ip_address, created_at)
               VALUES (:uid, NULL, :at, :mn, :tok, :ip, :ca)"""
        ),
        {
            "uid": user_id,
            "at": action_type,
            "mn": model_name,
            "tok": tokens,
            "ip": ip,
            "ca": created_at,
        },
    )


@pytest.fixture(autouse=True)
def _clean():
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM usage_logs WHERE user_id IN (SELECT id FROM users WHERE email LIKE 'test-usage-%')"))
        conn.execute(text("DELETE FROM users WHERE email LIKE 'test-usage-%'"))


def test_anonymous_401() -> None:
    resp = TestClient(app).get("/api/usage/logs")
    assert resp.status_code == 401


def test_only_returns_own_logs() -> None:
    client_a = TestClient(app)
    client_b = TestClient(app)
    addr_a = _register(client_a)
    addr_b = _register(client_b)
    with engine.begin() as conn:
        id_a = _user_id(conn, addr_a)
        id_b = _user_id(conn, addr_b)
        _insert_log(conn, user_id=id_a, action_type="analysis", model_name="qwen-plus",
                    tokens=100, ip="1.1.1.1", created_at="2026-09-01T10:00:00+08:00")
        _insert_log(conn, user_id=id_b, action_type="parse", tokens=0,
                    ip="2.2.2.2", created_at="2026-09-01T11:00:00+08:00")

    data = client_a.get("/api/usage/logs").json()
    assert data["total"] == 1
    assert data["items"][0]["action_type"] == "analysis"
    assert data["items"][0]["ip_address"] == "1.1.1.1"

    data_b = client_b.get("/api/usage/logs").json()
    assert data_b["total"] == 1
    assert data_b["items"][0]["action_type"] == "parse"


def test_pagination() -> None:
    client = TestClient(app)
    addr = _register(client)
    with engine.begin() as conn:
        uid = _user_id(conn, addr)
        for i in range(3):
            _insert_log(conn, user_id=uid, action_type="playground", model_name=None,
                        tokens=i, ip=None, created_at=f"2026-09-01T1{i}:00:00+08:00")

    p1 = client.get("/api/usage/logs?page=1&page_size=2").json()
    assert p1["total"] == 3
    assert p1["page"] == 1
    assert p1["page_size"] == 2
    assert len(p1["items"]) == 2

    p2 = client.get("/api/usage/logs?page=2&page_size=2").json()
    assert len(p2["items"]) == 1


def test_order_created_at_desc() -> None:
    client = TestClient(app)
    addr = _register(client)
    with engine.begin() as conn:
        uid = _user_id(conn, addr)
        _insert_log(conn, user_id=uid, action_type="parse",
                    created_at="2026-09-01T08:00:00+08:00")
        _insert_log(conn, user_id=uid, action_type="analysis",
                    created_at="2026-09-02T08:00:00+08:00")
        _insert_log(conn, user_id=uid, action_type="playground",
                    created_at="2026-09-03T08:00:00+08:00")

    items = client.get("/api/usage/logs").json()["items"]
    actions = [it["action_type"] for it in items]
    # created_at 倒序：playground(9/3) → analysis(9/2) → parse(9/1)
    assert actions == ["playground", "analysis", "parse"]


def test_filter_action_type() -> None:
    client = TestClient(app)
    addr = _register(client)
    with engine.begin() as conn:
        uid = _user_id(conn, addr)
        _insert_log(conn, user_id=uid, action_type="analysis",
                    created_at="2026-09-01T08:00:00+08:00")
        _insert_log(conn, user_id=uid, action_type="parse",
                    created_at="2026-09-01T09:00:00+08:00")
        _insert_log(conn, user_id=uid, action_type="analysis",
                    created_at="2026-09-01T10:00:00+08:00")

    data = client.get("/api/usage/logs?action_type=analysis").json()
    assert data["total"] == 2
    assert all(it["action_type"] == "analysis" for it in data["items"])


def test_filter_model_name_ilike() -> None:
    client = TestClient(app)
    addr = _register(client)
    with engine.begin() as conn:
        uid = _user_id(conn, addr)
        _insert_log(conn, user_id=uid, action_type="analysis", model_name="qwen-plus",
                    created_at="2026-09-01T08:00:00+08:00")
        _insert_log(conn, user_id=uid, action_type="analysis", model_name="deepseek-chat",
                    created_at="2026-09-01T09:00:00+08:00")

    data = client.get("/api/usage/logs?model_name=qwen").json()
    assert data["total"] == 1
    assert data["items"][0]["model_name"] == "qwen-plus"


def test_filter_ip_exact() -> None:
    client = TestClient(app)
    addr = _register(client)
    with engine.begin() as conn:
        uid = _user_id(conn, addr)
        _insert_log(conn, user_id=uid, action_type="parse", ip="127.0.0.1",
                    created_at="2026-09-01T08:00:00+08:00")
        _insert_log(conn, user_id=uid, action_type="parse", ip="192.168.1.1",
                    created_at="2026-09-01T09:00:00+08:00")

    data = client.get("/api/usage/logs?ip_address=127.0.0.1").json()
    assert data["total"] == 1
    assert data["items"][0]["ip_address"] == "127.0.0.1"


def test_filter_date_range_inclusive() -> None:
    """日期范围含当天：start 00:00:00 ~ end 23:59:59，边界当天记录应命中。"""
    client = TestClient(app)
    addr = _register(client)
    with engine.begin() as conn:
        uid = _user_id(conn, addr)
        _insert_log(conn, user_id=uid, action_type="parse",
                    created_at="2026-08-31T23:00:00+08:00")  # 范围外
        _insert_log(conn, user_id=uid, action_type="parse",
                    created_at="2026-09-01T00:30:00+08:00")  # 开始当天
        _insert_log(conn, user_id=uid, action_type="parse",
                    created_at="2026-09-02T12:00:00+08:00")  # 范围内
        _insert_log(conn, user_id=uid, action_type="parse",
                    created_at="2026-09-03T23:30:00+08:00")  # 结束当天
        _insert_log(conn, user_id=uid, action_type="parse",
                    created_at="2026-09-04T01:00:00+08:00")  # 范围外

    data = client.get("/api/usage/logs?start_date=2026-09-01&end_date=2026-09-03").json()
    assert data["total"] == 3


def test_empty_result() -> None:
    client = TestClient(app)
    _register(client)
    data = client.get("/api/usage/logs").json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["page"] == 1


def test_page_size_capped_at_50() -> None:
    """page_size 超过 50 被 Query 约束拒绝（422）。"""
    client = TestClient(app)
    _register(client)
    assert client.get("/api/usage/logs?page_size=100").status_code == 422


def test_time_granularity_filter() -> None:
    """v3.3 契约修复回归：start_time/end_time 覆盖整天默认值，时间粒度生效。"""
    client = TestClient(app)
    addr = _register(client)
    with engine.begin() as conn:
        uid = _user_id(conn, addr)
        # 同一天：08:00 和 12:00 各一条
        _insert_log(conn, user_id=uid, action_type="parse", tokens=1,
                    created_at="2026-09-01T08:00:00+08:00")
        _insert_log(conn, user_id=uid, action_type="parse", tokens=2,
                    created_at="2026-09-01T12:00:00+08:00")

    # 不传时间：整天 → 2 条
    all_day = client.get("/api/usage/logs?start_date=2026-09-01&end_date=2026-09-01").json()
    assert all_day["total"] == 2

    # 10:00 之后 → 只有 12:00 那条
    after = client.get(
        "/api/usage/logs?start_date=2026-09-01&end_date=2026-09-01&start_time=10:00"
    ).json()
    assert after["total"] == 1
    assert after["items"][0]["tokens_total"] == 2

    # 08:00 ~ 09:00 → 只有 08:00 那条
    early = client.get(
        "/api/usage/logs?start_date=2026-09-01&end_date=2026-09-01"
        "&start_time=08:00&end_time=09:00"
    ).json()
    assert early["total"] == 1
    assert early["items"][0]["tokens_total"] == 1

    # 非法时间 → 容错忽略该条件（不抛 422），回到整天语义
    bad = client.get(
        "/api/usage/logs?start_date=2026-09-01&end_date=2026-09-01&start_time=25:99"
    ).json()
    assert bad["total"] == 2
