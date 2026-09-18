"""v3.4 AI 客服接口测试：会话 CRUD、与在线对话类型隔离、/ask SSE 持久化、归属、限流。

不打真实大模型：monkeypatch api 层的 build_chat_llm/make_tools/build_agent_executor，
并用固定事件序列替换 stream_agent_events（LangChain 运行时本身由 test_agent.py 覆盖）。
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import ANONYMOUS_COOKIE
from app.db.session import engine
from app.main import app

client = TestClient(app)


def _email() -> str:
    return f"agent-{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture(autouse=True)
def _clean_agent():
    """按归属清理本文件造的数据（匿名 aid + agent-% 用户），在线对话数据不受影响。"""
    yield
    aids = [c.value for c in client.cookies.jar if c.name == ANONYMOUS_COOKIE]
    owner_sql = (
        "anonymous_id = ANY(:a) OR user_id IN "
        "(SELECT id FROM users WHERE email LIKE 'agent-%')"
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM chat_messages WHERE session_id IN "
                f"(SELECT id FROM chat_sessions WHERE {owner_sql})"
            ),
            {"a": aids},
        )
        conn.execute(text(f"DELETE FROM chat_sessions WHERE {owner_sql}"), {"a": aids})
        conn.execute(
            text(
                "DELETE FROM usage_logs WHERE action_type IN ('agent', 'agent_create') "
                f"AND ({owner_sql})"
            ),
            {"a": aids},
        )


def _fake_events(*_args, **_kwargs):
    """替换 stream_agent_events：按固定顺序产出工具过程+逐字+final。"""
    return iter(
        [
            {"type": "action", "tool": "kb_search", "input": "RAG"},
            {"type": "observation", "preview": "【来源：RAG入门】..."},
            {"type": "delta", "content": "RAG"},
            {"type": "delta", "content": " 是检索增强生成"},
            {
                "type": "final",
                "output": "RAG 是检索增强生成",
                "steps": [
                    {
                        "tool": "kb_search",
                        "input": "RAG",
                        "preview": "【来源：RAG入门】...",
                    }
                ],
                "tokens_total": 88,
                "tokens_prompt": 60,
                "tokens_completion": 28,
            },
        ]
    )


@pytest.fixture
def _mock_runtime(monkeypatch):
    """把 LangChain 运行时全部替换为假对象，只走 API 编排。"""
    monkeypatch.setattr("app.api.agent.build_chat_llm", lambda *a, **k: object())
    monkeypatch.setattr("app.api.agent.make_tools", lambda *a, **k: [])
    monkeypatch.setattr("app.api.agent.build_agent_executor", lambda *a, **k: object())
    monkeypatch.setattr("app.api.agent.stream_agent_events", _fake_events)


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    events = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        etype, data = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                etype = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        events.append((etype, data))
    return events


# —— 会话 CRUD 与类型隔离 ——


def test_agent_session_create_list_delete() -> None:
    created = client.post("/api/agent/sessions", json={"title": "客服对话"}).json()
    assert created["session_type"] == "agent"
    sid = created["id"]

    listing = client.get("/api/agent/sessions").json()
    assert any(s["id"] == sid for s in listing)

    deleted = client.delete(f"/api/agent/sessions/{sid}")
    assert deleted.status_code == 200
    assert not any(s["id"] == sid for s in client.get("/api/agent/sessions").json())


def test_agent_sessions_isolated_from_chat() -> None:
    """agent 列表不含在线对话(chat)会话，两类互不串。"""
    chat_s = client.post("/api/chat/sessions", json={}).json()
    agent_s = client.post("/api/agent/sessions", json={}).json()

    agent_list = client.get("/api/agent/sessions").json()
    agent_ids = [s["id"] for s in agent_list]
    assert agent_s["id"] in agent_ids
    assert chat_s["id"] not in agent_ids

    # 用 chat 会话 id 取 agent 消息 → 404（类型不符）
    resp = client.get(f"/api/agent/sessions/{chat_s['id']}/messages")
    assert resp.status_code == 404


# —— /ask 流式与持久化 ——


def test_ask_persists_messages_and_steps(_mock_runtime) -> None:
    session = client.post("/api/agent/sessions", json={}).json()
    sid = session["id"]

    resp = client.post(
        "/api/agent/ask", json={"content": "解释一下RAG", "session_id": sid}
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    etypes = [e[0] for e in events]
    assert etypes == ["meta", "action", "observation", "delta", "delta", "done"]

    done = events[-1][1]
    assert done["content"] == "RAG 是检索增强生成"
    assert done["iterations"] == 1
    assert done["tokens_total"] == 88
    assert done["message_id"] is not None

    # user + assistant 落库；assistant 带 tool_steps
    msgs = client.get(f"/api/agent/sessions/{sid}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["tool_steps"] == [
        {"tool": "kb_search", "input": "RAG", "preview": "【来源：RAG入门】..."}
    ]
    assert msgs[1]["tokens"] == 88

    # 标题被首条问题更新
    updated = next(
        s for s in client.get("/api/agent/sessions").json() if s["id"] == sid
    )
    assert updated["title"] == "解释一下RAG"


def test_ask_without_session_does_not_persist(_mock_runtime) -> None:
    resp = client.post("/api/agent/ask", json={"content": "你好"})
    assert resp.status_code == 200
    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM chat_messages")).scalar()
    assert count == 0


def test_ask_with_chat_typed_session_404(_mock_runtime) -> None:
    chat_s = client.post("/api/chat/sessions", json={}).json()
    resp = client.post(
        "/api/agent/ask", json={"content": "q", "session_id": chat_s["id"]}
    )
    assert resp.status_code == 404


def test_ask_owner_isolation(_mock_runtime) -> None:
    uc = TestClient(app)
    uc.post(
        "/api/auth/register", json={"email": _email(), "password": "correct-horse-123"}
    )
    mine = uc.post("/api/agent/sessions", json={}).json()
    uc.post("/api/auth/logout")

    # 匿名 client 用登录用户的会话提问 → 404
    resp = client.post(
        "/api/agent/ask", json={"content": "q", "session_id": mine["id"]}
    )
    assert resp.status_code == 404


def test_ask_rate_limited(_mock_runtime, monkeypatch) -> None:
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "daily_agent_limit", 0)
    resp = client.post("/api/agent/ask", json={"content": "q"})
    assert resp.status_code == 429


def test_ask_llm_not_configured_503(monkeypatch) -> None:
    def _raise():
        raise ValueError("AI 服务未配置")

    monkeypatch.setattr("app.api.agent.build_chat_llm", _raise)
    resp = client.post("/api/agent/ask", json={"content": "q"})
    assert resp.status_code == 503
