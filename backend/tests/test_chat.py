"""v3.1 在线对话接口测试：会话 CRUD、归属隔离、软删除、ask 持久化消息。"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import engine
from app.main import app

client = TestClient(app)

_FAKE_VEC = [0.5] * 768


def _email() -> str:
    return f"test-{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture(autouse=True)
def _clean_chat_and_usage():
    """每个用例前后清理 chat 表与 playground 用量。"""
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM chat_messages"))
        conn.execute(text("DELETE FROM chat_sessions"))
        conn.execute(text("DELETE FROM usage_logs WHERE action_type = 'playground'"))


# —— 会话 CRUD ——


def test_anonymous_create_and_list_session() -> None:
    """匿名用户：新建对话 → 列表出现 → 标题默认「新对话」。"""
    resp = client.post("/api/chat/sessions", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "新对话"
    assert "id" in data

    listing = client.get("/api/chat/sessions")
    assert listing.status_code == 200
    items = listing.json()
    assert len(items) >= 1
    assert any(s["id"] == data["id"] for s in items)


def test_create_session_with_custom_title() -> None:
    resp = client.post("/api/chat/sessions", json={"title": "我的技术问答"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "我的技术问答"


def test_create_session_trims_empty_title() -> None:
    resp = client.post("/api/chat/sessions", json={"title": "   "})
    assert resp.status_code == 200
    assert resp.json()["title"] == "新对话"


def test_list_sessions_ordered_by_updated_desc() -> None:
    """新建两个对话，列表按 updated_at 倒序（新的在前）。"""
    first = client.post("/api/chat/sessions", json={}).json()
    second = client.post("/api/chat/sessions", json={}).json()
    listing = client.get("/api/chat/sessions").json()
    ids = [s["id"] for s in listing]
    assert ids.index(second["id"]) < ids.index(first["id"])


# —— 消息 ——


def test_get_messages_empty() -> None:
    session = client.post("/api/chat/sessions", json={}).json()
    resp = client.get(f"/api/chat/sessions/{session['id']}/messages")
    assert resp.status_code == 200
    assert resp.json() == []


# —— 软删除 ——


def test_delete_session_soft() -> None:
    """删除对话 → 列表不再出现 → 直接取消息 404。"""
    session = client.post("/api/chat/sessions", json={}).json()
    sid = session["id"]

    del_resp = client.delete(f"/api/chat/sessions/{sid}")
    assert del_resp.status_code == 200
    assert del_resp.json()["ok"] is True

    listing = client.get("/api/chat/sessions").json()
    assert not any(s["id"] == sid for s in listing)

    msg_resp = client.get(f"/api/chat/sessions/{sid}/messages")
    assert msg_resp.status_code == 404


def test_delete_nonexistent_session_404() -> None:
    resp = client.delete("/api/chat/sessions/999999")
    assert resp.status_code == 404


def test_get_messages_nonexistent_session_404() -> None:
    resp = client.get("/api/chat/sessions/999999/messages")
    assert resp.status_code == 404


# —— 归属隔离 ——


def test_logged_in_user_sessions_isolated_from_anonymous() -> None:
    """登录用户的对话，匿名 client 看不到也取不了消息。"""
    # 注册登录一个用户
    user_client = TestClient(app)
    addr = _email()
    user_client.post("/api/auth/register", json={"email": addr, "password": "correct-horse-123"})
    session = user_client.post("/api/chat/sessions", json={"title": "私密对话"}).json()
    sid = session["id"]

    # 匿名 client 取消息 → 404（不泄露存在性）
    anon_resp = client.get(f"/api/chat/sessions/{sid}/messages")
    assert anon_resp.status_code == 404

    # 匿名 client 删除 → 404
    anon_del = client.delete(f"/api/chat/sessions/{sid}")
    assert anon_del.status_code == 404

    # 登录用户自己能看到
    user_listing = user_client.get("/api/chat/sessions").json()
    assert any(s["id"] == sid for s in user_listing)

    user_client.post("/api/auth/logout")


def test_two_logged_in_users_isolated() -> None:
    """用户 A 的对话，用户 B 看不到。"""
    client_a = TestClient(app)
    client_b = TestClient(app)
    addr_a = _email()
    addr_b = _email()
    client_a.post("/api/auth/register", json={"email": addr_a, "password": "correct-horse-123"})
    client_b.post("/api/auth/register", json={"email": addr_b, "password": "correct-horse-123"})

    session_a = client_a.post("/api/chat/sessions", json={"title": "A的对话"}).json()
    listing_b = client_b.get("/api/chat/sessions").json()
    assert not any(s["id"] == session_a["id"] for s in listing_b)

    client_a.post("/api/auth/logout")
    client_b.post("/api/auth/logout")


# —— playground/ask 带 session_id 持久化 ——


def test_ask_with_session_id_persists_messages(monkeypatch) -> None:
    """带 session_id 提问：用户消息和 AI 消息都落库，标题自动更新。"""
    monkeypatch.setattr(
        "app.api.playground.embed_texts",
        lambda texts: [_FAKE_VEC] * len(texts),
    )
    monkeypatch.setattr(
        "app.services.kb_service.embed_texts",
        lambda texts: [_FAKE_VEC] * len(texts),
    )

    # 预置一条公共文档
    from app.services.kb_service import create_document, ingest_kb_document
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        doc = create_document(
            db, title="测试文档", doc_type="text",
            raw_text="HashMap 是数组加链表加红黑树。" * 5,
            user_id=None, anonymous_id=None, source_type="preset",
        )
        ok, _ = ingest_kb_document(db, doc)
        assert ok

    monkeypatch.setattr(
        "app.api.playground.stream_chat",
        lambda messages, settings, usage: iter(["HashMap", "原理"]),
    )

    session = client.post("/api/chat/sessions", json={}).json()
    sid = session["id"]
    assert session["title"] == "新对话"

    resp = client.post(
        "/api/playground/ask",
        json={"content": "HashMap 原理是什么", "session_id": sid},
    )
    assert resp.status_code == 200

    # 消息落库：1 条 user + 1 条 assistant
    messages = client.get(f"/api/chat/sessions/{sid}/messages").json()
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "HashMap 原理是什么"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "HashMap原理"
    assert messages[1]["citations"] is not None
    assert len(messages[1]["citations"]) > 0

    # 标题自动更新为第一条用户消息前 20 字
    listing = client.get("/api/chat/sessions").json()
    updated = next(s for s in listing if s["id"] == sid)
    assert updated["title"] == "HashMap 原理是什么"

    # 清理预置文档
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM kb_chunks"))
        conn.execute(text("DELETE FROM kb_documents"))


def test_ask_without_session_id_does_not_persist(monkeypatch) -> None:
    """不带 session_id：保持原行为，不写 chat_messages。"""
    monkeypatch.setattr(
        "app.api.playground.embed_texts",
        lambda texts: [_FAKE_VEC] * len(texts),
    )
    monkeypatch.setattr(
        "app.services.kb_service.embed_texts",
        lambda texts: [_FAKE_VEC] * len(texts),
    )

    from app.services.kb_service import create_document, ingest_kb_document
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        doc = create_document(
            db, title="测试文档", doc_type="text",
            raw_text="HashMap 是数组加链表加红黑树。" * 5,
            user_id=None, anonymous_id=None, source_type="preset",
        )
        ingest_kb_document(db, doc)

    monkeypatch.setattr(
        "app.api.playground.stream_chat",
        lambda messages, settings, usage: iter(["回答"]),
    )

    resp = client.post("/api/playground/ask", json={"content": "HashMap"})
    assert resp.status_code == 200

    # chat_messages 表应为空（fixture 清理后无其他写入）
    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM chat_messages")).scalar()
    assert count == 0

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM kb_chunks"))
        conn.execute(text("DELETE FROM kb_documents"))


def test_ask_with_invalid_session_id_404(monkeypatch) -> None:
    """带不存在的 session_id → 404，不消耗 AI 资源。"""
    monkeypatch.setattr(
        "app.api.playground.embed_texts",
        lambda texts: [_FAKE_VEC] * len(texts),
    )
    resp = client.post(
        "/api/playground/ask",
        json={"content": "test", "session_id": 999999},
    )
    assert resp.status_code == 404
