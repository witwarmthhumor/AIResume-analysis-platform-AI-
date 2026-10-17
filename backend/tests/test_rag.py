"""v3.0 知识库服务测试：create/document/list/search。mock embedding，不调真实 Ollama。"""

import pytest
from sqlalchemy import text

from app.core.config import settings
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

# 测试文档标题统一前缀：清理时只删自己造的文档，预置语料（v3.7 起常驻库中）不受影响
_KB_PREFIX = "ragtest-"


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
    """mock embedding（避免调真实 Ollama）+ 按标记清理本文件造的 kb 数据。"""
    monkeypatch.setattr(
        "app.services.kb_service.embed_texts",
        lambda texts: [_FAKE_VEC] * len(texts),
    )
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM kb_chunks WHERE document_id IN "
                "(SELECT id FROM kb_documents WHERE title LIKE :p)"
            ),
            {"p": f"{_KB_PREFIX}%"},
        )
        conn.execute(
            text("DELETE FROM kb_documents WHERE title LIKE :p"),
            {"p": f"{_KB_PREFIX}%"},
        )


def _make_doc(
    db, title, source_type="preset", scope="public", user_id=None, anonymous_id=None
):
    return create_document(
        db,
        title=f"{_KB_PREFIX}{title}",
        doc_type="text",
        raw_text="测试内容。" * 30,
        user_id=user_id,
        anonymous_id=anonymous_id,
        source_type=source_type,
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
    doc_a = _make_doc(
        db, "user_a_doc", source_type="uploaded", scope="private", user_id=1
    )
    doc_b = _make_doc(
        db, "anon_b_doc", source_type="uploaded", scope="private", anonymous_id="anon_b"
    )
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
    doc = _make_doc(
        db, "owned_test", source_type="uploaded", scope="private", user_id=1
    )
    assert get_owned_document(db, doc.id, user_id=1, anonymous_id=None) is not None
    assert get_owned_document(db, doc.id, user_id=2, anonymous_id=None) is None


def test_ingest_dim_mismatch_marks_failed(db_session, monkeypatch) -> None:
    """embedding 维度与表不符 → 置 failed 而非卡在 processing（P1 修复回归）。"""
    monkeypatch.setattr(
        "app.services.kb_service.embed_texts",
        lambda texts: (
            [[0.5] * 1024] * len(texts)
        ),  # 模拟误配 1024 维模型（表为 768 维）
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
    _insert_chunk(
        db, lex_doc, "聚簇索引是把数据行按主键顺序物理存储的结构。", _ORTHOGONAL_VEC
    )
    _insert_chunk(db, vec_doc, "Redis 的持久化方式有 RDB 和 AOF 两种。", _FAKE_VEC)

    # 纯向量：lex_doc 相似度 0.036 < 0.65 → 被阈值过滤
    vector_only = search_chunks(db, _QUERY_VEC, None, "anon", top_k=5)
    assert lex_doc.title not in {r["title"] for r in vector_only}

    # 混合：lex_doc 占词法第 1 名，RRF 把它救回并排第一。
    # 语料在场时命中块可能更多（预置的 database_index 同样含"聚簇索引"内容），
    # 因此只断言「救回 + 排序」这两个核心属性，不做结果全集相等断言。
    hybrid = search_chunks(
        db, _QUERY_VEC, None, "anon", top_k=5, query_text="聚簇索引是什么"
    )
    assert hybrid[0]["title"] == lex_doc.title
    assert vec_doc.title in {r["title"] for r in hybrid}
    # 混合结果带归因字段（调试用）
    assert "rrf_score" in hybrid[0] and "lexical_score" in hybrid[0]


def test_hybrid_disabled_falls_back_to_vector(db_session, monkeypatch) -> None:
    """关掉开关即退回纯向量路径（便于 A/B 对比与回滚）。"""
    monkeypatch.setattr("app.services.kb_service.settings.kb_hybrid_enabled", False)
    db = db_session
    lex_doc = _ready_doc(db, "lex_doc")
    _insert_chunk(
        db, lex_doc, "聚簇索引是把数据行按主键顺序物理存储的结构。", _ORTHOGONAL_VEC
    )

    results = search_chunks(
        db, _QUERY_VEC, None, "anon", top_k=5, query_text="聚簇索引是什么"
    )
    assert results == []


def test_hybrid_keeps_owner_isolation(db_session) -> None:
    """混合检索同样受可见性约束：别人的私有文档不可见。"""
    db = db_session
    other_doc = create_document(
        db,
        title=f"{_KB_PREFIX}other_private",
        doc_type="text",
        raw_text="聚簇索引相关私有资料。" * 20,
        user_id=2,
        anonymous_id=None,
        source_type="uploaded",
    )
    other_doc.status = "ready"
    db.commit()
    _insert_chunk(
        db, other_doc, "聚簇索引是把数据行按主键顺序物理存储的结构。", _ORTHOGONAL_VEC
    )

    hybrid = search_chunks(
        db, _QUERY_VEC, 1, None, top_k=5, query_text="聚簇索引是什么"
    )
    # 语料在场时混合检索可能命中公共文档，但**别人的私有文档**绝不可见——这才是本测试保护的属性
    assert all(r["title"] != other_doc.title for r in hybrid)


# —— v4.6 重排序接入（kb_service 层三层验证：关闭 / 生效 / 降级）——


def _seed_rerank_docs(db):
    """造两篇 ready 文档各一块：一篇高相似、一篇正交（用于观察重排是否换序）。"""
    doc_a = create_document(
        db,
        title=f"{_KB_PREFIX}rrk_a",
        doc_type="text",
        raw_text="HashMap 底层实现。",
        user_id=None,
        anonymous_id=None,
        source_type="preset",
    )
    doc_b = create_document(
        db,
        title=f"{_KB_PREFIX}rrk_b",
        doc_type="text",
        raw_text="完全无关的另一段内容。",
        user_id=None,
        anonymous_id=None,
        source_type="preset",
    )
    for doc, vec in ((doc_a, _FAKE_VEC), (doc_b, _ORTHOGONAL_VEC)):
        doc.status = "ready"
        db.commit()
        _insert_chunk(db, doc, doc.raw_text, vec)
    return doc_a, doc_b


def test_rerank_disabled_keeps_baseline_shape(db_session, monkeypatch) -> None:
    """开关关闭：返回结构不含 rerank 字段（与 v3.5 基线逐字段一致）。"""
    monkeypatch.setattr(settings, "kb_rerank_enabled", False)
    db = db_session
    doc_a, doc_b = _seed_rerank_docs(db)

    hits = search_chunks(
        db,
        _QUERY_VEC,
        None,
        "anon-rrk-shape",
        top_k=2,
        query_text="HashMap 底层实现",
        document_ids=[doc_a.id, doc_b.id],
    )
    assert hits
    for hit in hits:
        assert "reranked" not in hit
        assert "rerank_score" not in hit


def test_rerank_reorders_within_candidate_pool(db_session, monkeypatch) -> None:
    """rerank 生效：在候选池内换序、字段出现，且**集合不变**（不引入新文档）。

    用 document_ids 把候选池限定为本次造的两篇 —— 常驻的预置语料同样会被检索到，
    不限定范围时"反转候选顺序"的语义不可控。
    """
    db = db_session
    doc_a, doc_b = _seed_rerank_docs(db)
    doc_ids = [doc_a.id, doc_b.id]
    baseline = search_chunks(
        db,
        _QUERY_VEC,
        None,
        "anon-rrk",
        top_k=2,
        query_text="HashMap 底层实现",
        document_ids=doc_ids,
    )
    assert len(baseline) == 2

    def fake_rerank(_query, documents, top_n=None):
        # 下标越大分越高 → 强制反转候选顺序，便于断言"确实换了序"
        ranked = [(idx, float(idx)) for idx in range(len(documents))]
        return ranked[:top_n] if top_n else ranked

    monkeypatch.setattr(settings, "kb_rerank_enabled", True)
    monkeypatch.setattr(settings, "kb_rerank_candidates", 10)
    monkeypatch.setattr("app.services.kb_service.rerank", fake_rerank)

    reranked = search_chunks(
        db,
        _QUERY_VEC,
        None,
        "anon-rrk",
        top_k=2,
        query_text="HashMap 底层实现",
        document_ids=doc_ids,
        caller="playground",  # 声明为问答页调用方（默认 scope=playground 才对它生效）
    )
    assert [h["document_id"] for h in reranked] == [
        h["document_id"] for h in reversed(baseline)
    ]
    assert all(h["reranked"] is True for h in reranked)
    assert all("rerank_score" in h for h in reranked)
    # 关键属性：只在池内换序，集合不变
    assert {h["document_id"] for h in reranked} == {h["document_id"] for h in baseline}


def test_rerank_degraded_marks_false_and_keeps_order(db_session, monkeypatch) -> None:
    """rerank 不可用：顺序保持 RRF，且 reranked=false（可观测，不静默）。"""
    db = db_session
    doc_a, doc_b = _seed_rerank_docs(db)
    doc_ids = [doc_a.id, doc_b.id]
    baseline = search_chunks(
        db,
        _QUERY_VEC,
        None,
        "anon-rrk-dg",
        top_k=2,
        query_text="HashMap 底层实现",
        document_ids=doc_ids,
    )
    assert baseline

    monkeypatch.setattr(settings, "kb_rerank_enabled", True)
    monkeypatch.setattr("app.services.kb_service.rerank", lambda *a, **k: None)

    degraded = search_chunks(
        db,
        _QUERY_VEC,
        None,
        "anon-rrk-dg",
        top_k=2,
        query_text="HashMap 底层实现",
        document_ids=doc_ids,
        caller="playground",
    )
    assert [h["document_id"] for h in degraded] == [h["document_id"] for h in baseline]
    assert all(h["reranked"] is False for h in degraded)
    assert all("rerank_score" not in h for h in degraded)


def test_rerank_scope_playground_excludes_agent(db_session, monkeypatch) -> None:
    """scope=playground（默认）时 Agent 调用方不走 rerank：顺序与字段保持基线，且不触发推理。"""
    db = db_session
    doc_a, doc_b = _seed_rerank_docs(db)
    common = {
        "top_k": 2,
        "query_text": "HashMap 底层实现",
        "document_ids": [doc_a.id, doc_b.id],
    }
    baseline = search_chunks(db, _QUERY_VEC, None, "anon-scope", **common)
    assert len(baseline) == 2

    calls = {"n": 0}

    def fake_rerank(_query, documents, top_n=None):
        calls["n"] += 1
        return [(idx, float(idx)) for idx in range(len(documents))]

    monkeypatch.setattr(settings, "kb_rerank_enabled", True)
    monkeypatch.setattr(settings, "kb_rerank_scope", "playground")
    monkeypatch.setattr("app.services.kb_service.rerank", fake_rerank)

    # Agent 侧：不启用 → 顺序不变、无字段，且 rerank() 根本没被调用（省掉 1s 延迟）
    agent_hits = search_chunks(
        db, _QUERY_VEC, None, "anon-scope", caller="agent", **common
    )
    assert [h["document_id"] for h in agent_hits] == [
        h["document_id"] for h in baseline
    ]
    assert all("reranked" not in h for h in agent_hits)
    assert calls["n"] == 0

    # 问答页：启用 → 换序 + 字段出现
    page_hits = search_chunks(
        db, _QUERY_VEC, None, "anon-scope", caller="playground", **common
    )
    assert calls["n"] == 1
    assert all(h["reranked"] is True for h in page_hits)
    assert [h["document_id"] for h in page_hits] == [
        h["document_id"] for h in reversed(baseline)
    ]


def test_rerank_scope_all_covers_every_caller(db_session, monkeypatch) -> None:
    """scope=all 时 Agent 调用方同样启用重排序。"""
    db = db_session
    doc_a, doc_b = _seed_rerank_docs(db)

    monkeypatch.setattr(settings, "kb_rerank_enabled", True)
    monkeypatch.setattr(settings, "kb_rerank_scope", "all")
    monkeypatch.setattr(
        "app.services.kb_service.rerank",
        lambda _q, docs, top_n=None: [(i, float(i)) for i in range(len(docs))],
    )

    hits = search_chunks(
        db,
        _QUERY_VEC,
        None,
        "anon-scope-all",
        top_k=2,
        query_text="HashMap 底层实现",
        document_ids=[doc_a.id, doc_b.id],
        caller="agent",
    )
    assert hits
    assert all(h["reranked"] is True for h in hits)
