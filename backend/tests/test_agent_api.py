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
from app.db.session import SessionLocal, engine
from app.main import app

client = TestClient(app)


def _email() -> str:
    return f"agent-{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture(autouse=True)
def _clean_agent():
    """按归属清理本文件造的数据（匿名 aid + agent-% 用户），在线对话数据不受影响。

    usage_logs 用 id 快照兜底：本文件的类型隔离用例会创建 chat 会话并产生
    chat_create 记账，按动作类型过滤会漏；顺序执行下按「测试期间新增」过滤绝对精确。
    """
    with engine.begin() as conn:
        snap = conn.execute(
            text("SELECT COALESCE(MAX(id), 0) FROM usage_logs")
        ).scalar()
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
        conn.execute(text("DELETE FROM usage_logs WHERE id > :s"), {"s": snap})


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
        aids = [c.value for c in client.cookies.jar if c.name == ANONYMOUS_COOKIE]
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM chat_messages WHERE session_id IN "
                "(SELECT id FROM chat_sessions WHERE anonymous_id = ANY(:a))"
            ),
            {"a": aids},
        ).scalar()
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


def test_ask_disconnect_logs_zero_tokens(monkeypatch) -> None:
    """v3.7.1 断开补账回归：消费方中途 aclose（= 客户端断开）→ GeneratorExit 补 0-token 账。

    直驱实现：TestClient 的取消机制不会及时向线程池托管的同步生成器投递
    GeneratorExit（实测断开后拿不到 0-token 行），所以直接调用路由函数，
    手动消费 body_iterator 两个事件后 aclose，再强制 GC 关闭被包装的同步生成器。
    """
    import asyncio
    import gc
    import threading

    from fastapi import Request

    from app.api.agent import AgentAskIn
    from app.api.agent import ask as agent_ask

    release = threading.Event()

    def _blocking_events(*_a, **_k):
        yield {"type": "delta", "content": "断开前"}
        release.wait(timeout=5)  # 卡住：保证生成器停在 delta 处等消费者
        yield {"type": "delta", "content": "断开后"}

    monkeypatch.setattr("app.api.agent.stream_agent_events", _blocking_events)

    async def _drive():
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/agent/ask",
            "headers": [],
            "client": ("127.0.0.1", 50000),
            "query_string": b"",
        }
        request = Request(scope)
        with SessionLocal() as db:
            resp = agent_ask(
                body=AgentAskIn(content="断开测试"),
                request=request,
                db=db,
                anonymous_id="test-anon-disc",
                user=None,
            )
            it = resp.body_iterator
            meta = await it.__anext__()
            delta = await it.__anext__()
            assert "delta" in delta and "meta" in meta  # 包装器透传原始 str
            await it.aclose()  # 模拟客户端断开：向包装生成器投递 GeneratorExit
        gc.collect()  # 关闭被 anyio 包装的同步生成器 → 其 finally 补账

    asyncio.run(_drive())

    # 合跑下同步生成器的关闭可能延迟一个 GC 周期：collect + 重试确保补账已落库
    import time

    rows = []
    for _ in range(6):
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT tokens_total FROM usage_logs "
                    "WHERE action_type = 'agent' AND anonymous_id = 'test-anon-disc'"
                )
            ).fetchall()
        if rows:
            break
        gc.collect()
        time.sleep(0.5)
    assert rows == [(0,)]  # 断开路径补的 0-token 账（正常完成会是真实 tokens）
