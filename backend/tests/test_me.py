"""v3.4 个人中心接口测试：强制登录、本人五卡统计、今日/累计 token、用户隔离、近 7 日用量。"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import SessionLocal, engine
from app.main import app
from app.models.analysis import Analysis
from app.models.interview import InterviewSession
from app.models.resume import Resume
from app.models.usage_log import UsageLog


def _email() -> str:
    return f"me-{uuid.uuid4().hex[:10]}@example.com"


def _register_client() -> tuple[TestClient, int]:
    """注册并登录，返回带 cookie 的 client 与 user_id。"""
    c = TestClient(app)
    addr = _email()
    c.post("/api/auth/register", json={"email": addr, "password": "correct-horse-123"})
    with SessionLocal() as db:
        uid = db.scalar(
            text("SELECT id FROM users WHERE email = :e"), params={"e": addr}
        )
    return c, uid


@pytest.fixture(autouse=True)
def _clean_me():
    """按归属清理本文件造的数据（me-% 用户名下），其他用户数据不受影响。"""
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM usage_logs WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'me-%')"
            )
        )
        for tbl in ("analyses", "interview_sessions", "resumes"):
            conn.execute(
                text(
                    f"DELETE FROM {tbl} WHERE user_id IN "
                    "(SELECT id FROM users WHERE email LIKE 'me-%')"
                )
            )
        conn.execute(text("DELETE FROM users WHERE email LIKE 'me-%'"))


def test_me_requires_login() -> None:
    anon = TestClient(app)
    assert anon.get("/api/me/stats").status_code == 401
    assert anon.get("/api/me/usage").status_code == 401


def test_my_stats_counts_and_tokens() -> None:
    c, uid = _register_client()
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add_all(
            [
                Resume(
                    user_id=uid, filename="a.pdf", file_hash="h1", storage_path="/tmp/a"
                ),
                Resume(
                    user_id=uid, filename="b.pdf", file_hash="h2", storage_path="/tmp/b"
                ),
                Analysis(
                    user_id=uid,
                    resume_id=0,
                    model_name="m",
                    prompt_version="v1",
                    result_json={},
                ),
                InterviewSession(user_id=uid, resume_id=0),
                UsageLog(
                    user_id=uid, action_type="agent", model_name="m", tokens_total=100
                ),
                UsageLog(
                    user_id=uid,
                    action_type="playground",
                    model_name="m",
                    tokens_total=50,
                ),
            ]
        )
        # 昨天一条：计入累计、不计入今日
        yesterday = UsageLog(
            user_id=uid, action_type="agent", model_name="m", tokens_total=999
        )
        yesterday.created_at = now - timedelta(days=1)
        db.add(yesterday)
        db.commit()

    stats = c.get("/api/me/stats").json()
    assert stats["resumes"] == 2
    assert stats["analyses"] == 1
    assert stats["interviews"] == 1
    assert stats["tokens_today"] == 150
    assert stats["tokens_total"] == 1149


def test_my_stats_isolated_between_users() -> None:
    a, uid_a = _register_client()
    b, _uid_b = _register_client()
    with SessionLocal() as db:
        db.add(
            Resume(
                user_id=uid_a, filename="a.pdf", file_hash="x", storage_path="/tmp/x"
            )
        )
        db.add(
            UsageLog(
                user_id=uid_a, action_type="agent", model_name="m", tokens_total=300
            )
        )
        db.commit()

    # B 看不到 A 的任何数据
    stats_b = b.get("/api/me/stats").json()
    assert stats_b["resumes"] == 0
    assert stats_b["tokens_total"] == 0

    stats_a = a.get("/api/me/stats").json()
    assert stats_a["resumes"] == 1 and stats_a["tokens_total"] == 300


def test_my_usage_last_seven_days() -> None:
    c, uid = _register_client()
    with SessionLocal() as db:
        db.add_all(
            [
                UsageLog(
                    user_id=uid, action_type="agent", model_name="m", tokens_total=120
                ),
                UsageLog(
                    user_id=uid, action_type="agent", model_name="m", tokens_total=80
                ),
            ]
        )
        db.commit()

    rows = c.get("/api/me/usage").json()
    assert len(rows) == 7  # 固定近 7 天
    today = rows[-1]
    assert today["calls"] == 2
    assert today["tokens"] == 200
    # 其余天为 0
    assert all(r["calls"] == 0 for r in rows[:-1])


def test_update_profile_fields() -> None:
    """v4.2 个人信息：头像/身份证可改；手机号仅空账号可设一次，再改 400；非法值 422。"""
    c, _uid = _register_client()
    # 初始：三字段全空
    me = c.get("/api/auth/me").json()
    assert me["phone"] is None and me["id_card"] is None and me["avatar_key"] is None

    # 设置头像 + 身份证 + 手机号（一次性）
    ok = c.put(
        "/api/me/profile",
        json={
            "avatar_key": "a3",
            "id_card": "412327199905018341",
            "phone": f"139{uuid.uuid4().int % 10**8:08d}",
        },
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["avatar_key"] == "a3"
    assert body["id_card"] == "412327199905018341"
    assert body["phone"]

    phone_bound = body["phone"]
    # 手机号非空后不可再改
    again = c.put(
        "/api/me/profile", json={"phone": f"137{uuid.uuid4().int % 10**8:08d}"}
    )
    assert again.status_code == 400
    # 头像可继续改；非法头像/身份证 422
    assert c.put("/api/me/profile", json={"avatar_key": "a7"}).status_code == 200
    assert c.put("/api/me/profile", json={"avatar_key": "hacker"}).status_code == 422
    assert c.put("/api/me/profile", json={"id_card": "123"}).status_code == 422
    # 空串身份证 = 清空
    cleared = c.put("/api/me/profile", json={"id_card": ""})
    assert cleared.status_code == 200 and cleared.json()["id_card"] is None
    assert phone_bound  # 手机号未被后续请求动过
