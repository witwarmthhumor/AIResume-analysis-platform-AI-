"""KB 上传接口测试：匿名去重隔离与配额（v3.0 安全补强的回归测试）。

异步入库任务 mock 成替身，不触发真实 Celery/Ollama；测试结束清 kb 表与 kb_upload 用量。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import settings
from app.db.session import engine
from app.main import app


class _FakeTask:
    """替身 Celery 任务：只记录被投递的 document_id，不触发真实入库。"""

    def __init__(self):
        self.delayed = []

    def delay(self, document_id):
        self.delayed.append(document_id)

        class _Result:
            id = "fake-task-id"

        return _Result()


@pytest.fixture(autouse=True)
def _fake_ingest(monkeypatch):
    monkeypatch.setattr("app.api.knowledge_base.ingest_kb", _FakeTask())


@pytest.fixture(autouse=True)
def _clean_tables():
    """按标记清理本文件造的 kb 数据（文件名前缀 kbup-），预置语料不受影响。"""
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM kb_chunks WHERE document_id IN "
                "(SELECT id FROM kb_documents WHERE title LIKE 'kbup-%')"
            )
        )
        conn.execute(text("DELETE FROM kb_documents WHERE title LIKE 'kbup-%'"))
        conn.execute(text("DELETE FROM usage_logs WHERE action_type = 'kb_upload'"))


def _upload(client: TestClient, content: str, filename: str = "kbup-note.txt"):
    return client.post(
        "/api/kb/documents",
        files={"file": (filename, content.encode("utf-8"), "text/plain")},
    )


def test_anonymous_dedup_isolated_between_users():
    """两个匿名用户上传同内容文件：各自 201、互不吞单。

    回归点：旧去重条件只按 user_id IS NULL 匹配，B 会上传失败般"消失"在 A 的文档上。
    """
    alice = TestClient(app)
    bob = TestClient(app)
    content = "HashMap 底层是数组加链表加红黑树。" * 10

    resp_a = _upload(alice, content)
    assert resp_a.status_code == 201
    resp_b = _upload(bob, content)
    assert resp_b.status_code == 201
    assert resp_b.json()["id"] != resp_a.json()["id"]

    # 各自列表里恰好看到自己那一份（按测试文件名过滤，忽略预置语料）
    mine_a = [
        d
        for d in alice.get("/api/kb/documents").json()
        if d["title"] == "kbup-note.txt"
    ]
    mine_b = [
        d for d in bob.get("/api/kb/documents").json() if d["title"] == "kbup-note.txt"
    ]
    assert len(mine_a) == 1
    assert len(mine_b) == 1
    assert mine_b[0]["id"] != mine_a[0]["id"]


def test_same_anonymous_user_dedup_reuses_document():
    """同一匿名用户重复上传同内容：复用已存在文档，不新建。"""
    user = TestClient(app)
    content = "TCP 三次握手建立连接。" * 10
    first = _upload(user, content)
    second = _upload(user, content)
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]


def test_upload_daily_limit():
    """上传达到每日上限后 429，不再放行。"""
    user = TestClient(app)
    limit = settings.daily_kb_upload_limit
    for i in range(limit):
        resp = _upload(user, f"第 {i} 篇文档的内容。" * 5, f"kbup-doc-{i}.txt")
        assert resp.status_code == 201, f"第 {i + 1} 次上传不应被拦"
    assert _upload(user, "超限的文档内容。" * 5, "kbup-over.txt").status_code == 429
