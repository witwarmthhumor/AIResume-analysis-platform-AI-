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


# —— v3.5 混合检索（向量 + BM25，RRF 融合）——

_ORTHOGONAL_VEC = [0.0] * 768  # 与 _QUERY_VEC 余弦相似度 ≈ 0.036，远低于 0.65 阈值
_ORTHOGONAL_VEC[0] = 1.0


def _ready_doc(db, title: str):
    """造一个 ready 状态的文档（search 只认 ready）。"""
    doc = _make_doc(db, title)
    doc.status = "ready"
    db.commit()
    return doc


def _insert_chunk(db, doc, content: str, embedding: list[float]):
    """直接插切块：绕开 ingest，便于精确指定向量与正文。"""
    chunk = KBChunk(
        document_id=doc.id,
        seq=0,
        content=content,
        token_count=len(content),
        embedding=embedding,
    )
    db.add(chunk)
    db.commit()
    return chunk


def test_hybrid_rescues_term_hit_filtered_by_vector_threshold(db_session) -> None:
    """术语命中但向量不相似：纯向量被阈值过滤，混合检索应把该块救回并排第一。"""
    db = db_session
    lex_doc = _ready_doc(db, "lex_doc")
    vec_doc = _ready_doc(db, "vec_doc")
    _insert_chunk(db, lex_doc, "聚簇索引是把数据行按主键顺序物理存储的结构。", _ORTHOGONAL_VEC)
    _insert_chunk(db, vec_doc, "Redis 的持久化方式有 RDB 和 AOF 两种。", _FAKE_VEC)

    # 纯向量：lex_doc 相似度 0.036 < 0.65 → 被过滤，只剩 vec_doc
    vector_only = search_chunks(db, _QUERY_VEC, None, "anon", top_k=5)
    assert {r["title"] for r in vector_only} == {"vec_doc"}

    # 混合：lex_doc 占词法第 1 名 + 向量第 2 名，RRF 分高于只有向量第 1 名的 vec_doc
    hybrid = search_chunks(
        db, _QUERY_VEC, None, "anon", top_k=5, query_text="聚簇索引是什么"
    )
    assert hybrid[0]["title"] == "lex_doc"
    assert {"lex_doc", "vec_doc"} == {r["title"] for r in hybrid}
    # 混合结果带归因字段（调试用）
    assert "rrf_score" in hybrid[0] and "lexical_score" in hybrid[0]


def test_hybrid_disabled_falls_back_to_vector(db_session, monkeypatch) -> None:
    """关掉开关即退回纯向量路径（便于 A/B 对比与回滚）。"""
    monkeypatch.setattr("app.services.kb_service.settings.kb_hybrid_enabled", False)
    db = db_session
    lex_doc = _ready_doc(db, "lex_doc")
    _insert_chunk(db, lex_doc, "聚簇索引是把数据行按主键顺序物理存储的结构。", _ORTHOGONAL_VEC)

    results = search_chunks(
        db, _QUERY_VEC, None, "anon", top_k=5, query_text="聚簇索引是什么"
    )
    assert results == []


def test_hybrid_keeps_owner_isolation(db_session) -> None:
    """混合检索同样受可见性约束：别人的私有文档不可见。"""
    db = db_session
    other_doc = create_document(
        db,
        title="other_private",
        doc_type="text",
        raw_text="聚簇索引相关私有资料。" * 20,
        user_id=2,
        anonymous_id=None,
        source_type="uploaded",
    )
    other_doc.status = "ready"
    db.commit()
    _insert_chunk(db, other_doc, "聚簇索引是把数据行按主键顺序物理存储的结构。", _ORTHOGONAL_VEC)

    hybrid = search_chunks(
        db, _QUERY_VEC, 1, None, top_k=5, query_text="聚簇索引是什么"
    )
    assert hybrid == []
