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
    yield
    with engine.begin() as conn:
        for tbl in (
            "usage_logs",
            "analyses",
            "interview_sessions",
            "resumes",
        ):
            conn.execute(text(f"DELETE FROM {tbl}"))
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
