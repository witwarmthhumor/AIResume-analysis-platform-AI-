"""v3.0 知识库服务测试：create/document/list/search。mock embedding，不调真实 Ollama。"""

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal, engine
from app.models.kb import KBChunk
from app.services.kb_service import (
    create_document,
    get_owned_document,
    ingest_kb_document,
    list_documents,
    search_chunks,
    soft_delete_document,
)

# 768 维全 0.5 向量（归一化后 ≈ 0.5 方向，用于测试相似度排序）
_FAKE_VEC = [0.5] * 768
_QUERY_VEC = [0.51] * 768  # 与 _FAKE_VEC 相似度 ≈ 0.999


@pytest.fixture
def db_session():
    """每个用例独立的数据库会话（用完即关）。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clean_kb_tables(monkeypatch):
    """清理 kb 表 + mock embedding（避免调真实 Ollama）。"""
    monkeypatch.setattr(
        "app.services.kb_service.embed_texts",
        lambda texts: [_FAKE_VEC] * len(texts),
    )
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM kb_chunks"))
        conn.execute(text("DELETE FROM kb_documents"))


def _make_doc(db, title, source_type="preset", scope="public", user_id=None, anonymous_id=None):
    return create_document(
        db, title=title, doc_type="text", raw_text="测试内容。" * 30,
        user_id=user_id, anonymous_id=anonymous_id, source_type=source_type,
    )


def test_create_and_list_public_doc(db_session) -> None:
    db = db_session
    doc = _make_doc(db, "public_test")
    assert doc.status == "pending"
    assert doc.source_type == "preset"
    assert doc.scope == "public"

    # 预置语料所有人可见
    listed = list_documents(db, user_id=None, anonymous_id="anon")
    assert any(d.id == doc.id for d in listed)


def test_private_doc_owner_isolation(db_session) -> None:
    db = db_session
    doc_a = _make_doc(db, "user_a_doc", source_type="uploaded", scope="private", user_id=1)
    doc_b = _make_doc(db, "anon_b_doc", source_type="uploaded", scope="private", anonymous_id="anon_b")
    doc_c = _make_doc(db, "public_doc", scope="public")

    # 用户 1 只能看到自己的 + 公共的
    listed_user1 = list_documents(db, user_id=1, anonymous_id=None)
    ids = {d.id for d in listed_user1}
    assert doc_a.id in ids
    assert doc_c.id in ids
    assert doc_b.id not in ids  # 别人的私有文档

    # 匿名 b 只能看到自己的 + 公共的
    listed_anon_b = list_documents(db, user_id=None, anonymous_id="anon_b")
    ids_b = {d.id for d in listed_anon_b}
    assert doc_b.id in ids_b
    assert doc_c.id in ids_b
    assert doc_a.id not in ids_b


def test_ingest_creates_chunks_and_sets_ready(db_session) -> None:
    db = db_session
    doc = _make_doc(db, "ingest_test")
    ok, _ = ingest_kb_document(db, doc)
    assert ok
    assert doc.status == "ready"
    assert doc.embedding_model is not None
    assert doc.embedding_dim == 768
    chunks = db.query(KBChunk).filter(KBChunk.document_id == doc.id).all()
    assert len(chunks) > 0


def test_search_chunks_returns_above_threshold(db_session) -> None:
    db = db_session
    doc = _make_doc(db, "search_test")
    ingest_kb_document(db, doc)

    # 用与 _FAKE_VEC 高度相似的向量查询 → 应命中
    results = search_chunks(db, _QUERY_VEC, user_id=None, anonymous_id="anon", top_k=5)
    assert len(results) > 0
    assert results[0]["similarity"] > 0.9


def test_search_chunks_empty_when_threshold_miss(db_session) -> None:
    db = db_session
    doc = _make_doc(db, "far_test")
    ingest_kb_document(db, doc)

    # 用正交向量查询 → 相似度 < 0.65 → 返回空
    far_vec = [0.0] * 768
    far_vec[0] = 1.0
    results = search_chunks(db, far_vec, user_id=None, anonymous_id="anon", top_k=5)
    assert len(results) == 0


def test_soft_delete_excludes_document(db_session) -> None:
    db = db_session
    doc = _make_doc(db, "del_test")
    soft_delete_document(db, doc)
    assert doc.deleted_at is not None
    listed = list_documents(db, user_id=None, anonymous_id="anon")
    assert doc.id not in {d.id for d in listed}


def test_get_owned_document_returns_none_if_not_visible(db_session) -> None:
    db = db_session
    doc = _make_doc(db, "owned_test", source_type="uploaded", scope="private", user_id=1)
    assert get_owned_document(db, doc.id, user_id=1, anonymous_id=None) is not None
    assert get_owned_document(db, doc.id, user_id=2, anonymous_id=None) is None

def test_ingest_dim_mismatch_marks_failed(db_session, monkeypatch) -> None:
    """embedding 维度与表不符 → 置 failed 而非卡在 processing（P1 修复回归）。"""
    monkeypatch.setattr(
        "app.services.kb_service.embed_texts",
        lambda texts: [[0.5] * 1024] * len(texts),  # 模拟误配 1024 维模型（表为 768 维）
    )
    db = db_session
    doc = _make_doc(db, "dim_mismatch_test")
    ok, message = ingest_kb_document(db, doc)
    assert ok is False
    assert doc.status == "failed"
    assert "维度" in message
    assert message == doc.parse_error
