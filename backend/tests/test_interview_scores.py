"""v3.5 面试评分列表接口测试：面试报告雷达图的历史对比数据源。

要点：
- 路径 `/api/interviews/scores` 必须不被 `/api/interviews/{session_id}` 吞掉（否则 422）
- 只返回本人、已结束、且有结束评价报告的场次
- 数据按时间倒序，带四个维度评分

数据隔离：测试数据打固定标记（resume_id=888801 / 邮箱前缀 scores-），只清自己造的。
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import SessionLocal, engine
from app.main import app
from app.models.interview import InterviewSession

_MARKER = 888801


def _register_client() -> tuple[TestClient, int]:
    """注册并登录，返回带 cookie 的 client 与 user_id。"""
    client = TestClient(app)
    addr = f"scores-{uuid.uuid4().hex[:10]}@example.com"
    client.post("/api/auth/register", json={"email": addr, "password": "correct-horse-123"})
    with SessionLocal() as db:
        uid = db.scalar(text("SELECT id FROM users WHERE email = :e"), params={"e": addr})
    return client, uid


@pytest.fixture(autouse=True)
def _clean_scores():
    """前后各清一次：前置清理防止上一轮异常中断留下的脏数据干扰断言。"""
    def _purge() -> None:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM interview_sessions WHERE resume_id = :m"), {"m": _MARKER}
            )
            conn.execute(text("DELETE FROM users WHERE email LIKE 'scores-%'"))

    _purge()
    yield
    _purge()


def _report(**overrides) -> dict:
    base = {
        "technical_depth": 7,
        "communication": 6,
        "project_authenticity": 8,
        "overall": 7,
        "summary": "表现扎实。",
        "highlights": ["项目讲得清楚"],
        "improvements": ["补充量化数据"],
    }
    base.update(overrides)
    return base


def _add_session(
    user_id: int,
    *,
    status: str = "finished",
    report: dict | None = None,
    position_type: str | None = "senior",
    turn_count: int = 8,
) -> int:
    with SessionLocal() as db:
        session = InterviewSession(
            resume_id=_MARKER,
            user_id=user_id,
            status=status,
            stage="wrapup",
            turn_count=turn_count,
            position_type=position_type,
            final_report_json=report,
        )
        db.add(session)
        db.commit()
        return session.id


def test_scores_path_not_shadowed_by_session_id_route() -> None:
    """空数据也必须返回 200 + 空列表：证明路由顺序正确（没被 {session_id} 吃掉）。"""
    client, _uid = _register_client()
    resp = client.get("/api/interviews/scores")
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


def test_returns_finished_sessions_with_four_dimensions() -> None:
    client, uid = _register_client()
    _add_session(uid, report=_report())
    _add_session(uid, report=_report(overall=9), position_type="intern", turn_count=4)

    items = client.get("/api/interviews/scores").json()["items"]

    assert len(items) == 2
    # 倒序：后插入的（intern）在前
    assert items[0]["position_type"] == "intern"
    assert items[0]["scores"]["overall"] == 9
    assert set(items[1]["scores"]) == {
        "technical_depth",
        "communication",
        "project_authenticity",
        "overall",
    }
    assert items[1]["summary"] == "表现扎实。"
    assert items[1]["created_at"]  # 前端用来格式化日期


def test_excludes_unfinished_and_reportless_sessions() -> None:
    client, uid = _register_client()
    _add_session(uid, status="in_progress", report=None)
    _add_session(uid, status="abandoned", report=None)
    _add_session(uid, status="finished", report=None)  # 结束了但没报告

    items = client.get("/api/interviews/scores").json()["items"]

    assert items == []


def test_limit_is_clamped() -> None:
    client, uid = _register_client()
    for _ in range(3):
        _add_session(uid, report=_report())

    assert len(client.get("/api/interviews/scores?limit=2").json()["items"]) == 2
    # 非法值回落到安全范围而不是 500
    assert len(client.get("/api/interviews/scores?limit=0").json()["items"]) == 3


def test_isolates_other_users() -> None:
    _other_client, other_uid = _register_client()
    _add_session(other_uid, report=_report())

    client, _uid = _register_client()
    assert client.get("/api/interviews/scores").json()["items"] == []
