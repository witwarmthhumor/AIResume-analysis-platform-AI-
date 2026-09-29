"""admin/me 近 7 日用量聚合直测（v3.7 审计缺口：v3.7.1 改的 GROUP BY day_idx 聚合无直测）。

口径要点：日期窗口以**服务器本地时区**的"今天 0 点"为基准切成 7 个桶（旧实现逐日
14 次查询，现为单条 GROUP BY——本文件是那次改写的回归锚点）。
admin 侧是全局统计（可能含其他测试残留），断言用"前后差值"防噪声；
me 侧按 user_id 归属精确断言。
"""

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import engine
from app.main import app

client = TestClient(app)

# 服务器本地时区偏移（如 "+0800"）：聚合窗口按服务端时区计算，时间戳必须同口径
_TZO = datetime.now().astimezone().strftime("%z")


def _email(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}@example.com"


def _register(prefix: str, role: str | None = None) -> tuple[TestClient, int]:
    c = TestClient(app)
    addr = _email(prefix)
    c.post("/api/auth/register", json={"email": addr, "password": "correct-horse-123"})
    with engine.begin() as conn:
        if role:
            conn.execute(
                text("UPDATE users SET role = :r WHERE email = :e"),
                {"r": role, "e": addr},
            )
        uid = conn.execute(
            text("SELECT id FROM users WHERE email = :e"), {"e": addr}
        ).scalar()
    return c, uid


def _insert_log(conn, user_id: int, created_at: str, tokens: int = 0) -> None:
    conn.execute(
        text(
            "INSERT INTO usage_logs "
            "(user_id, anonymous_id, action_type, tokens_total, created_at) "
            "VALUES (:u, NULL, 'analysis', :t, :c)"
        ),
        {"u": user_id, "t": tokens, "c": created_at},
    )


def _local_ts(days_ago: int, hour: int = 12, minute: int = 0) -> str:
    """本地时区时间戳：days_ago=0 即今天。"""
    day = (datetime.now().astimezone() - timedelta(days=days_ago)).date()
    return f"{day.isoformat()}T{hour:02d}:{minute:02d}:00{_TZO}"


@pytest.fixture(autouse=True)
def _clean_usage_data():
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM usage_logs WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'admusage-%' OR email LIKE 'musage-%')"
            )
        )
        conn.execute(
            text(
                "DELETE FROM users WHERE email LIKE 'admusage-%' OR email LIKE 'musage-%'"
            )
        )
        conn.execute(
            text("DELETE FROM usage_logs WHERE anonymous_id = 'admusage-noise'")
        )


# —— admin /api/admin/usage ——


def test_admin_usage_shape_and_today_delta():
    """结构：7 条、日期升序、末条为今天；今天插入的行只进最后一个桶（差值断言防全局噪声）。"""
    admin, _ = _register("admusage-", role="admin")

    before = admin.get("/api/admin/usage").json()
    assert len(before) == 7
    dates = [r["date"] for r in before]
    assert dates == sorted(dates)  # 升序
    today = datetime.now().astimezone().date().isoformat()
    assert dates[-1] == today

    # admin 统计是全局口径：插两条匿名行即可（归属不影响聚合）
    with engine.begin() as conn:
        for tokens in (100, 200):
            conn.execute(
                text(
                    "INSERT INTO usage_logs "
                    "(user_id, anonymous_id, action_type, tokens_total, created_at) "
                    "VALUES (NULL, 'admusage-noise', 'analysis', :t, :c)"
                ),
                {"t": tokens, "c": _local_ts(0)},
            )

    after = admin.get("/api/admin/usage").json()
    assert after[-1]["calls"] == before[-1]["calls"] + 2
    assert after[-1]["tokens"] == before[-1]["tokens"] + 300
    # 前 6 桶不受影响
    assert [r["calls"] for r in after[:6]] == [r["calls"] for r in before[:6]]


def test_admin_usage_requires_admin():
    """普通用户 403、匿名 401（_admin_only 守卫）。"""
    user, _ = _register("musage-")
    assert user.get("/api/admin/usage").status_code == 403
    anon = TestClient(app)
    assert anon.get("/api/admin/usage").status_code == 401


# —— me /api/me/usage ——


def test_me_usage_buckets_exact():
    """精确落位：今天/昨天各 1 条入对应桶；8 天前不计入；午夜边界归当天。"""
    user, uid = _register("musage-")
    with engine.begin() as conn:
        _insert_log(conn, uid, _local_ts(0, hour=0, minute=0))  # 本地今天 00:00：属今天
        _insert_log(
            conn, uid, _local_ts(1, hour=23, minute=59), tokens=50
        )  # 昨天 23:59
        _insert_log(conn, uid, _local_ts(8), tokens=999)  # 8 天前：窗口外

    rows = user.get("/api/me/usage").json()
    assert len(rows) == 7
    today = datetime.now().astimezone().date().isoformat()
    assert rows[-1]["date"] == today
    assert rows[-1]["calls"] == 1  # 00:00 边界行落今天
    assert rows[-2]["calls"] == 1  # 昨天 23:59
    assert rows[-2]["tokens"] == 50
    assert sum(r["calls"] for r in rows) == 2  # 8 天前的行被窗口排除
    assert sum(r["tokens"] for r in rows) == 50


def test_me_usage_isolates_other_users():
    """归属隔离：他人的用量不进我的桶。"""
    me, _ = _register("musage-")
    other, other_uid = _register("musage-")
    with engine.begin() as conn:
        _insert_log(conn, other_uid, _local_ts(0), tokens=12345)

    rows = me.get("/api/me/usage").json()
    assert sum(r["calls"] for r in rows) == 0
    assert sum(r["tokens"] for r in rows) == 0

    other_rows = other.get("/api/me/usage").json()
    assert other_rows[-1]["calls"] == 1
    assert other_rows[-1]["tokens"] == 12345


def test_me_usage_requires_login():
    anon = TestClient(app)
    assert anon.get("/api/me/usage").status_code == 401
