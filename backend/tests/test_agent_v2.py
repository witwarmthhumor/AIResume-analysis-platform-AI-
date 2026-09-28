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
        conn.execute(
            text(
                "DELETE FROM chat_messages WHERE session_id IN (SELECT id FROM chat_sessions "
                "WHERE user_id IN (SELECT id FROM users WHERE email LIKE 'av2-%'))"
            )
        )
        conn.execute(
            text(
                "DELETE FROM chat_sessions WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'av2-%')"
            )
        )
        # 业务数据：图节点（load_resume / analyzer / questioner / 审批副作用）会写
        # resumes、analyses、interview_sessions、interview_messages。这几张表此前**完全
        # 没有清理**，而本文件又会先删 users —— 残留下来就是孤儿行（实测一次全量测试
        # 留下 8 份 av2 简历 + 8 条分析 + 2 个场次，且 user_id 指向已删用户）。
        # 顺序必须遵循「子表先于父表」，简历最后删。
        conn.execute(
            text(
                "DELETE FROM interview_messages WHERE session_id IN "
                "(SELECT id FROM interview_sessions WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'av2-%') "
                "OR resume_id IN (SELECT id FROM resumes WHERE filename LIKE 'av2-%'))"
            )
        )
        conn.execute(
            text(
                "DELETE FROM interview_sessions WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'av2-%') "
                "OR resume_id IN (SELECT id FROM resumes WHERE filename LIKE 'av2-%')"
            )
        )
        conn.execute(
            text(
                "DELETE FROM analyses WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'av2-%') "
                "OR resume_id IN (SELECT id FROM resumes WHERE filename LIKE 'av2-%')"
            )
        )
        conn.execute(text("DELETE FROM resumes WHERE filename LIKE 'av2-%'"))
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
        # 同步 POST 等流自然结束（waiting_approval 时服务端主动关流）后返回全部事件文本
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


# —— v4.0 质量审计 P1/P2 回归（2026-09-26）——


def test_event_bus_multi_subscriber_isolated_copies():
    """多订阅者各拿独立事件拷贝：消费端 pop("type") 不得互相影响。

    回归：publish/重连回放曾共享同一 dict 引用，双连接并存时第二个消费者必崩。
    """
    from app.services.agent_v2 import event_bus

    run_id = 900_000_000 + uuid.uuid4().int % 1_000_000
    first = event_bus.subscribe_with_replay(run_id)
    event_bus.publish(run_id, {"type": "node_start", "node": "planner"})
    second = event_bus.subscribe_with_replay(run_id)  # 重连：回放既有事件
    event_bus.publish(run_id, {"type": "node_end", "node": "planner"})
    try:
        consumed = []
        for buf in (first, second):
            got = []
            while buf:
                event = buf.popleft()
                got.append(event.pop("type"))  # 与 SSE 消费端同款破坏性修改
            consumed.append(got)
    finally:
        event_bus.unsubscribe(run_id, first)
        event_bus.unsubscribe(run_id, second)
    assert consumed[0] == ["node_start", "node_end"]
    assert consumed[1] == ["node_start", "node_end"]


def test_approve_twice_and_abort_conflict_409(_fake_llm):
    """审批竞态：重复 approve / 批准后 abort 一律 409，决定不被覆盖、不产生重复审批单。"""
    marker = _marker()
    c, uid, _ = _register("av2-")
    _create_resume_for(uid, marker)
    resp = c.post("/api/agent-v2/runs", json={"jd_text": "招后端"})
    _drain_events(resp, until=("approval_required",))
    run_id = _last_run_id()

    first = c.post(
        f"/api/agent-v2/runs/{run_id}/approve", json={"decision": "approved"}
    )
    assert first.status_code == 202
    second = c.post(
        f"/api/agent-v2/runs/{run_id}/approve", json={"decision": "rejected"}
    )
    assert second.status_code == 409
    abort = c.post(f"/api/agent-v2/runs/{run_id}/abort")
    assert abort.status_code == 409
    assert _wait_status(run_id, "completed") == "completed"
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT status FROM agent_approvals WHERE run_id = :i ORDER BY id"),
            {"i": run_id},
        ).scalars()
        statuses = list(rows)
    assert statuses == ["approved"]  # resume 重执行未再建单，拒绝请求也没写进去


def test_approval_expiry_terminates_run_and_retry(_fake_llm, monkeypatch):
    """审批过期：approve 410、run 终止为 failed（不再卡 waiting_approval）、可重试。"""
    from app.core.config import settings

    marker = _marker()
    c, uid, _ = _register("av2-")
    _create_resume_for(uid, marker)
    monkeypatch.setattr(settings, "agent_approval_ttl_minutes", 0)
    resp = c.post("/api/agent-v2/runs", json={"jd_text": "招后端"})
    _drain_events(resp, until=("approval_required",))
    run_id = _last_run_id()

    r = c.post(f"/api/agent-v2/runs/{run_id}/approve", json={"decision": "approved"})
    assert r.status_code == 410
    assert _wait_status(run_id, "failed") == "failed"
    detail = c.get(f"/api/agent-v2/runs/{run_id}").json()
    assert detail["status"] == "failed"
    assert "审批超时" in (detail["error"] or "")
    assert detail["approval"] is None
    with engine.begin() as conn:
        approval_status = conn.execute(
            text(
                "SELECT status FROM agent_approvals WHERE run_id = :i "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"i": run_id},
        ).scalar()
    assert approval_status == "expired"

    retry = c.post(f"/api/agent-v2/runs/{run_id}/retry-node")
    # retry-node 与 POST /runs 同款直出 SSE 流：StreamingResponse 决定状态码（200）
    assert retry.status_code == 200
    new_run_id = _last_run_id()
    assert new_run_id != run_id
    assert _wait_status(new_run_id, "waiting_approval") == "waiting_approval"


def test_stream_snapshot_includes_approval(_fake_llm, monkeypatch):
    """/stream 重连快照带审批卡数据（summary/expires_at），空闲断流发 stream_closed。

    _SSE_IDLE_TICKS 打小测防御性断流路径（默认 1800 会真等 90s）。
    """
    import app.api.agent_v2 as agent_v2_api

    marker = _marker()
    c, uid, _ = _register("av2-")
    _create_resume_for(uid, marker)
    resp = c.post("/api/agent-v2/runs", json={"jd_text": "招后端"})
    _drain_events(resp, until=("approval_required",))
    run_id = _last_run_id()

    monkeypatch.setattr(agent_v2_api, "_SSE_IDLE_TICKS", 2)
    resp = c.get(f"/api/agent-v2/runs/{run_id}/stream")
    events = _drain_events(resp, until=("stream_closed",))
    types = [e for e, _ in events]
    assert "snapshot" in types and "stream_closed" in types
    snap = dict(events)["snapshot"]
    assert snap["status"] == "waiting_approval"
    assert snap["approval"]["summary"]
    assert snap["approval"]["expires_at"]


def test_stream_snapshot_terminal_run_closes_immediately():
    """终态 run 重连：快照 + 带话术的 fatal 后立即关流（不等空闲断流）。"""
    c, _, _ = _register("av2-")
    resp = c.post("/api/agent-v2/runs", json={"jd_text": "招后端"})  # 无简历 → failed
    _drain_events(resp, until=("fatal",))
    run_id = _last_run_id()
    assert _wait_status(run_id, "failed") == "failed"

    resp = c.get(f"/api/agent-v2/runs/{run_id}/stream")
    events = _drain_events(resp, until=("fatal",))
    payloads = dict(events)
    assert payloads["snapshot"]["status"] == "failed"
    assert "上传" in (payloads["fatal"].get("content") or "")


def test_list_runs_pagination():
    """本人列表 SQL 分页：total 为该用户全部 run，倒序切片正确。"""
    c, _, _ = _register("av2-")
    for _ in range(3):
        _drain_events(
            c.post("/api/agent-v2/runs", json={"jd_text": "x"}), until=("fatal",)
        )
    page1 = c.get("/api/agent-v2/runs?page=1&page_size=2").json()
    page2 = c.get("/api/agent-v2/runs?page=2&page_size=2").json()
    assert page1["total"] == 3 and page2["total"] == 3
    assert len(page1["items"]) == 2 and len(page2["items"]) == 1
    ids = [i["id"] for i in page1["items"]]
    assert ids == sorted(ids, reverse=True)
    assert page1["items"][-1]["id"] > page2["items"][0]["id"]


def test_create_run_session_id_ownership():
    """session_id 归属校验：挂他人会话 404 且不建 run；本人会话正常关联。"""
    a, uid_a, _ = _register("av2-")
    session_id = a.post("/api/chat/sessions", json={"title": "我的问答"}).json()["id"]
    b, uid_b, _ = _register("av2-")

    r = b.post(
        "/api/agent-v2/runs", json={"jd_text": "招后端", "session_id": session_id}
    )
    assert r.status_code == 404
    with engine.begin() as conn:
        leaked = conn.execute(
            text("SELECT count(*) FROM agent_runs WHERE user_id = :u"), {"u": uid_b}
        ).scalar()
    assert leaked == 0  # 校验先于限额与建 run

    resp = a.post(
        "/api/agent-v2/runs", json={"jd_text": "招后端", "session_id": session_id}
    )
    assert resp.status_code == 200
    _drain_events(resp, until=("fatal",))
    with engine.begin() as conn:
        linked = conn.execute(
            text(
                "SELECT session_id FROM agent_runs WHERE user_id = :u "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"u": uid_a},
        ).scalar()
    assert linked == session_id


def test_admin_run_endpoints_smoke():
    """管理端 run 列表/统计 SQL 化后结构可用（全局数值不断言，只验形状与过滤）。"""
    c, _, _ = _register("av2-", role="admin")
    runs = c.get("/api/agent-v2/admin/runs?page=1").json()
    assert isinstance(runs["items"], list) and runs["total"] >= 0
    filtered = c.get("/api/agent-v2/admin/runs?status=completed").json()
    assert all(i["status"] == "completed" for i in filtered["items"])
    stats = c.get("/api/agent-v2/admin/runs/stats").json()
    assert stats["window_days"] == 7
    assert set(stats["approvals"]) == {"pending", "approved", "rejected", "expired"}
    assert stats["total_runs"] >= 0


def test_reap_orphan_runs_marks_failed():
    """启动回收孤儿 run（lifespan 钩子调用）：planning/running → failed，审批单同步作废；
    waiting_approval 不动——图挂在 checkpoint 上，续跑仍然有效。"""
    from app.services.agent_v2.graph import reap_orphan_runs

    _, uid, _ = _register("av2-")
    suffix = uuid.uuid4().hex[:8]

    def _insert_run(status: str, tag: str) -> int:
        with engine.begin() as conn:
            return conn.execute(
                text(
                    "INSERT INTO agent_runs (user_id, trace_id, thread_id, run_type, status)"
                    " VALUES (:uid, :trace, :thread, 'job_prep_pipeline', :status) RETURNING id"
                ),
                {
                    "uid": uid,
                    "trace": f"av2-trace-{suffix}-{tag}",
                    "thread": f"av2-thread-{suffix}-{tag}",
                    "status": status,
                },
            ).scalar()

    planning_id = _insert_run("planning", "p")
    running_id = _insert_run("running", "r")
    waiting_id = _insert_run("waiting_approval", "w")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO agent_approvals (run_id, trace_id, action_key, status)"
                " VALUES (:rid, :trace, 'create_interview_session', 'pending')"
            ),
            {"rid": waiting_id, "trace": f"av2-trace-{suffix}-w"},
        )

    assert reap_orphan_runs() >= 2  # 可能含其他遗留行，至少回收本次两条

    with engine.begin() as conn:
        statuses = dict(
            conn.execute(
                text("SELECT id, status FROM agent_runs WHERE id IN (:a, :b, :c)"),
                {"a": planning_id, "b": running_id, "c": waiting_id},
            ).all()
        )
        error = conn.execute(
            text("SELECT error FROM agent_runs WHERE id = :i"), {"i": planning_id}
        ).scalar()
        approval_status = conn.execute(
            text("SELECT status FROM agent_approvals WHERE run_id = :i"),
            {"i": waiting_id},
        ).scalar()
    assert statuses[planning_id] == "failed"
    assert statuses[running_id] == "failed"
    assert statuses[waiting_id] == "waiting_approval"
    assert error and "服务重启" in error
    assert approval_status == "pending"  # 挂起中 run 的审批单不被回收波及
