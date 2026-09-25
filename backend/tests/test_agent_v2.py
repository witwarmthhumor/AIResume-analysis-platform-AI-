"""Agent v2 API 与图链路测试（PRD M2/M3 验收）。

LLM 能力在 capabilities 层打桩（graph.py 的 run_job_match / run_question_generation /
analyze_resume 模块引用，测试 patch app.services.agent_v2.graph.* 即整图离线）。
归属用 marker 前缀清理（av2-）；空库语义见各用例注释。
"""

import json
import time
import uuid
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import engine
from app.main import app

client = TestClient(app)


def _marker() -> str:
    return f"av2-{uuid.uuid4().hex[:10]}"


def _register(prefix: str, role: str | None = None) -> tuple[TestClient, int, str]:
    c = TestClient(app)
    email = f"{prefix}{uuid.uuid4().hex[:10]}@example.com"
    c.post("/api/auth/register", json={"email": email, "password": "correct-horse-123"})
    with engine.begin() as conn:
        if role:
            conn.execute(
                text("UPDATE users SET role = :r WHERE email = :e"),
                {"r": role, "e": email},
            )
        uid = conn.execute(
            text("SELECT id FROM users WHERE email = :e"), {"e": email}
        ).scalar()
    return c, uid, email


@pytest.fixture(autouse=True)
def _seed_placeholder_and_clean():
    """空库首用户提权占位 + av2 标记数据清理。"""
    c = TestClient(app)
    c.post(
        "/api/auth/register",
        json={
            "email": f"av2-ph{uuid.uuid4().hex[:8]}@example.com",
            "password": "correct-horse-123",
        },
    )
    yield
    with engine.begin() as conn:
        # 记账行先于用户删除（图 analyzer/工具会写 usage_logs，漏删会污染他文件断言）
        conn.execute(
            text(
                "DELETE FROM usage_logs WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'av2-%')"
            )
        )
        conn.execute(
            text(
                "DELETE FROM audit_logs WHERE run_id IN (SELECT id FROM agent_runs "
                "WHERE anonymous_id LIKE 'av2-%' OR user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'av2-%'))"
            )
        )
        conn.execute(
            text(
                "DELETE FROM agent_approvals WHERE run_id IN (SELECT id FROM agent_runs "
                "WHERE anonymous_id LIKE 'av2-%' OR user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'av2-%'))"
            )
        )
        conn.execute(
            text(
                "DELETE FROM agent_spans WHERE run_id IN (SELECT id FROM agent_runs "
                "WHERE anonymous_id LIKE 'av2-%' OR user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'av2-%'))"
            )
        )
        conn.execute(
            text(
                "DELETE FROM agent_runs WHERE anonymous_id LIKE 'av2-%' OR user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'av2-%')"
            )
        )
        conn.execute(text("DELETE FROM users WHERE email LIKE 'av2-%'"))


@pytest.fixture()
def _fake_llm(monkeypatch):
    """能力层打桩：matcher/questioner 返回固定产物，analyze 返回有效报告。"""
    import app.services.agent_v2.graph as g

    fake_match = {
        "match_score": 80,
        "matched_keywords": ["Python"],
        "missing_keywords": ["K8s"],
        "suggestions": ["补容器经验"],
    }
    fake_questions = {
        "questions": ["讲讲你的项目", "Python GIL 是什么", "如何设计一个缓存"],
        "level": "通用",
        "kb_backed": False,
        "sources": [],
    }
    monkeypatch.setattr(
        g, "run_job_match", lambda *a, **k: ("匹配文本", dict(fake_match))
    )
    monkeypatch.setattr(
        g, "run_question_generation", lambda *a, **k: ("出题文本", dict(fake_questions))
    )

    class _R:
        report: ClassVar[dict] = {"target_position": "后端"}
        valid = True
        model_name = "fake"
        tokens_prompt = 10
        tokens_completion = 20
        duration_ms = 1

    monkeypatch.setattr(g, "analyze_resume", lambda *a, **k: _R())


def _create_resume_for(uid: int, marker: str) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO resumes (user_id, filename, file_hash, storage_path, raw_text, "
                "page_count, file_size, parse_status) "
                "VALUES (:u, :fn, :h, :p, :rt, 1, 100, 'success') RETURNING id"
            ),
            {
                "u": uid,
                "fn": f"{marker}.pdf",
                "h": uuid.uuid4().hex,
                "p": "uploads/x.pdf",
                "rt": "Av2 测试简历正文。",
            },
        ).scalar()


def _drain_events(resp, until=()):
    """读 SSE 流直到遇到 until 中的事件类型，返回 [(类型, payload)]。

    TestClient 的流式读取对长连接不可靠（实测只收到部分事件就断开），
    改用同步 POST 收完整响应体后按 SSE 格式解析。
    """
    body = resp.text
    events = []
    last = None
    for line in body.split(chr(10)):
        if line.startswith("event: "):
            last = line[len("event: ") :]
        elif line.startswith("data: ") and last:
            payload = json.loads(line[len("data: ") :])
            events.append((last, payload))
            if last in until:
                return events
    return events


def _last_run_id() -> int:
    # 登录用户的 run 记 user_id（anonymous_id 为空），匿名 run 记 anonymous_id；
    # 测试进程独占 agent_runs 新增行，取全局最新即本用例的 run
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT id FROM agent_runs ORDER BY id DESC LIMIT 1")
        ).scalar()


def _wait_status(run_id: int, target: str, timeout_s: float = 15.0) -> str:
    status = None
    for _ in range(int(timeout_s / 0.2)):
        with engine.begin() as conn:
            status = conn.execute(
                text("SELECT status FROM agent_runs WHERE id = :i"), {"i": run_id}
            ).scalar()
        if status == target:
            break
        time.sleep(0.2)
    return status


def test_run_happy_path_with_hitl_approval(_fake_llm):
    """主链路端到端：正常完成 → HITL 挂起 → 批准 → 创建面试场次（M2+M3 核心）。"""
    marker = _marker()
    c, uid, _ = _register("av2-")
    _create_resume_for(uid, marker)

    resp = c.post(
        "/api/agent-v2/runs",
        json={"jd_text": "招后端，要求 Python 与 MySQL", "position_type": "fresh"},
        # 同步 POST 等 30s 超时后返回全部事件文本（v2 stream 会自动关流）
    )
    assert resp.status_code == 200  # StreamingResponse 忽略装饰器 status_code
    events = _drain_events(resp, until=("approval_required", "fatal"))

    types = [e for e, _ in events]
    assert "plan" in types and "approval_required" in types, types
    with engine.begin() as conn:
        run_id = _last_run_id()
        status = conn.execute(
            text("SELECT status FROM agent_runs WHERE id = :i"), {"i": run_id}
        ).scalar()
        approval = conn.execute(
            text(
                "SELECT action_key, status FROM agent_approvals "
                "WHERE run_id = :i ORDER BY id DESC LIMIT 1"
            ),
            {"i": run_id},
        ).fetchone()
        sessions_before = conn.execute(
            text("SELECT count(*) FROM interview_sessions")
        ).scalar()
    assert status == "waiting_approval"
    assert approval == (
        "create_interview_session",
        "pending",
    )  # 零副作用门禁：未批准无场次

    r = c.post(f"/api/agent-v2/runs/{run_id}/approve", json={"decision": "approved"})
    assert r.status_code == 202
    assert _wait_status(run_id, "completed") == "completed"

    run = c.get(f"/api/agent-v2/runs/{run_id}").json()
    assert run["output"]["match"]["match_score"] == 80
    assert len(run["output"]["questions"]) == 3
    assert run["output"]["session_id"]  # 批准后创建了面试场次
    with engine.begin() as conn:
        sessions_after = conn.execute(
            text("SELECT count(*) FROM interview_sessions")
        ).scalar()
        audit = conn.execute(
            text(
                "SELECT action FROM audit_logs WHERE run_id = :i AND action LIKE 'approval%'"
            ),
            {"i": run_id},
        ).scalar()
    assert sessions_after == sessions_before + 1
    assert audit == "approval_approved"


def test_run_rejected_creates_no_session(_fake_llm):
    """拒绝审批：run 照常交付（无场次），审批单 rejected，零副作用。"""
    marker = _marker()
    c, uid, _ = _register("av2-")
    _create_resume_for(uid, marker)
    resp = c.post("/api/agent-v2/runs", json={"jd_text": "招后端"})
    _drain_events(resp, until=("approval_required",))
    run_id = _last_run_id()
    with engine.begin() as conn:
        before = conn.execute(text("SELECT count(*) FROM interview_sessions")).scalar()

    c.post(f"/api/agent-v2/runs/{run_id}/approve", json={"decision": "rejected"})
    assert _wait_status(run_id, "completed") == "completed"
    run = c.get(f"/api/agent-v2/runs/{run_id}").json()
    assert run["output"]["session_id"] is None  # 拒绝 → 未创建
    with engine.begin() as conn:
        after = conn.execute(text("SELECT count(*) FROM interview_sessions")).scalar()
    assert after == before


def test_run_without_resume_fails_with_guidance():
    """无简历：load_resume 直接 fail，给引导话术（PRD：不调模型不耗额度）。"""
    c, _, _ = _register("av2-")
    resp = c.post("/api/agent-v2/runs", json={"jd_text": "招后端"})
    events = _drain_events(resp, until=("fatal",))
    types = [e for e, _ in events]
    assert "fatal" in types
    payload = dict(events)["fatal"]
    assert "上传" in payload["content"]
    # 登录用户的 run 记 user_id（anonymous_id 为空），按全局最新 run 查状态
    assert _wait_status(_last_run_id(), "failed") == "failed"


def test_verifier_retries_then_recovers(_fake_llm, monkeypatch):
    """坏输出回环：matcher 前两次返回空产物，第三次恢复 → retried span ×2。"""
    import app.services.agent_v2.graph as g

    marker = _marker()
    c, uid, _ = _register("av2-")
    _create_resume_for(uid, marker)
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] <= 2:
            return ("匹配失败文本", None)
        return (
            "匹配文本",
            {
                "match_score": 60,
                "matched_keywords": [],
                "missing_keywords": [],
                "suggestions": [],
            },
        )

    monkeypatch.setattr(g, "run_job_match", flaky)
    resp = c.post("/api/agent-v2/runs", json={"jd_text": "招后端"})
    events = _drain_events(resp, until=("approval_required", "fatal"))
    types = [e for e, _ in events]
    assert types.count("retry") == 2, types
    assert "approval_required" in types  # 恢复后走到 HITL
    with engine.begin() as conn:
        run_id = _last_run_id()
        retried = conn.execute(
            text(
                "SELECT count(*) FROM agent_spans WHERE run_id = :i AND status = 'retried'"
            ),
            {"i": run_id},
        ).scalar()
    assert retried == 2


def test_abort_waiting_run(_fake_llm):
    """waiting_approval 可放弃：run aborted、审批单 expired。"""
    marker = _marker()
    c, uid, _ = _register("av2-")
    _create_resume_for(uid, marker)
    resp = c.post("/api/agent-v2/runs", json={"jd_text": "x"})
    _drain_events(resp, until=("approval_required",))
    run_id = _last_run_id()
    r = c.post(f"/api/agent-v2/runs/{run_id}/abort")
    assert r.status_code == 200
    with engine.begin() as conn:
        status = conn.execute(
            text("SELECT status FROM agent_runs WHERE id = :i"), {"i": run_id}
        ).scalar()
        approval_status = conn.execute(
            text(
                "SELECT status FROM agent_approvals WHERE run_id = :i "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"i": run_id},
        ).scalar()
    assert status == "aborted"
    assert approval_status == "expired"


def test_run_owner_isolation(_fake_llm):
    """越权 404：用户 B 不能读用户 A 的 run（matches_owner 口径）。"""
    a, uid_a, _ = _register("av2-")
    _create_resume_for(uid_a, _marker())
    resp = a.post("/api/agent-v2/runs", json={"jd_text": "x"})
    _drain_events(resp, until=("fatal", "done"))
    with engine.begin() as conn:
        run_id = conn.execute(
            text(
                "SELECT id FROM agent_runs WHERE user_id = :u ORDER BY id DESC LIMIT 1"
            ),
            {"u": uid_a},
        ).scalar()
    b, _, _ = _register("av2-")
    assert b.get(f"/api/agent-v2/runs/{run_id}").status_code == 404


def test_agent_v2_disabled_returns_503(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "agent_v2_enabled", False)
    r = client.post("/api/agent-v2/runs", json={"jd_text": "x"})
    assert r.status_code == 503
    assert "对话框" in r.json()["message"]


def test_run_daily_limit_429(monkeypatch):
    """v2 独立限额：达 daily_agent_v2_run_limit 后发起返回 429（与 v1 口径分开）。"""
    from app.core.config import settings

    c, _, _ = _register("av2-")
    monkeypatch.setattr(settings, "daily_agent_v2_run_limit", 0)
    r = c.post("/api/agent-v2/runs", json={"jd_text": "x"})
    assert r.status_code == 429
    assert "每日上限" in r.json()["message"]
