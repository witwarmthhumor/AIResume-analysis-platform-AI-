"""v3.0 Playground 接口测试：SSE 问答（mock embedding 与 AI 流式回复，不烧真实调用）。"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import SessionLocal, engine
from app.main import app
from app.services.kb_service import create_document, ingest_kb_document

client = TestClient(app)

_FAKE_VEC = [0.5] * 768  # 与库里向量同方向 → 相似度 1.0，稳定命中


@pytest.fixture(autouse=True)
def _clean_and_seed(monkeypatch):
    """清理 kb 表与 playground 用量；mock embedding 为固定向量；预置一条公共文档。"""
    monkeypatch.setattr(
        "app.api.playground.embed_texts",
        lambda texts: [_FAKE_VEC] * len(texts),
    )
    monkeypatch.setattr(
        "app.services.kb_service.embed_texts",
        lambda texts: [_FAKE_VEC] * len(texts),
    )

    with SessionLocal() as db:
        doc = create_document(
            db,
            title="测试文档",
            doc_type="text",
            raw_text="HashMap 是数组加链表加红黑树。" * 5,
            user_id=None,
            anonymous_id=None,
            source_type="preset",
        )
        ok, _ = ingest_kb_document(db, doc)
        assert ok

    yield

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM kb_chunks"))
        conn.execute(text("DELETE FROM kb_documents"))
        conn.execute(text("DELETE FROM usage_logs WHERE action_type = 'playground'"))


def test_ask_streams_answer_with_citations(monkeypatch) -> None:
    """命中场景：meta → delta* → done（带引用来源）。"""
    monkeypatch.setattr(
        "app.api.playground.stream_chat",
        lambda messages, settings, usage: iter(["这是", "回答内容"]),
    )
    resp = client.post("/api/playground/ask", json={"content": "HashMap 原理"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = {}
    for block in resp.text.split("\n\n"):
        if not block.strip():
            continue
        evt = block.splitlines()[0].replace("event: ", "")
        data = json.loads(block.splitlines()[1].replace("data: ", ""))
        events.setdefault(evt, []).append(data)

    assert "meta" in events
    assert events["meta"][0]["stage"] == "rag"
    assert events["delta"][0]["content"] == "这是"
    assert "done" in events
    done = events["done"][0]
    assert done["content"] == "这是回答内容"
    assert len(done["citations"]) > 0
    assert done["citations"][0]["title"] == "测试文档"


def test_ask_returns_error_when_no_citations(monkeypatch) -> None:
    """未命中场景：检索为空 → error 事件明确提示没有相关内容。"""
    monkeypatch.setattr("app.api.playground.search_chunks", lambda *a, **k: [])
    monkeypatch.setattr(
        "app.api.playground.stream_chat",
        lambda messages, settings, usage: iter([]),
    )
    resp = client.post("/api/playground/ask", json={"content": "无关问题"})
    assert resp.status_code == 200
    assert "event: error" in resp.text
    assert "没有相关内容" in resp.text


def test_ask_validates_empty_content() -> None:
    resp = client.post("/api/playground/ask", json={"content": ""})
    assert resp.status_code == 422
