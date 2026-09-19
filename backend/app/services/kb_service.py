"""知识库服务（v3.0）：切块入库、向量检索、文档 CRUD（owner 隔离）。

ingest：切块 → 批量向量化（Ollama）→ 写 kb_chunks → 文档置 ready。
search：v3.5 起为混合检索——向量路（HNSW 余弦，走数据库索引）+ 词法路（BM25），
        RRF 融合排序；不传 query_text 时退回纯向量检索（兼容旧调用方）。
owner 隔离：scope=public（预置语料）全站可见；scope=private（用户上传）仅本人可见。
"""

import math
from datetime import datetime, timezone

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.kb import KBChunk, KBDocument
from app.services.embedding_service import EmbeddingError, embed_texts
from app.services.kb_chunker import chunk_text
from app.services.lexical_service import Bm25Index

logger = get_logger(__name__)


def _visible_clause(user_id: int | None, anonymous_id: str | None):
    """可见性条件：预置语料(public) 所有人可见；上传文档仅归属者可见。"""
    return or_(
        KBDocument.scope == "public",
        and_(user_id is not None, KBDocument.user_id == user_id),
        and_(
            anonymous_id is not None,
            KBDocument.scope == "private",
            KBDocument.anonymous_id == anonymous_id,
        ),
    )


def ingest_kb_document(
    db: Session,
    document: KBDocument,
    chunks: list[dict] | None = None,
) -> tuple[bool, str | None]:
    """切块向量化入库并置 ready。chunks 可注入（测试 mock embedding 用）。返回 (成功?, 话术)。"""
    if chunks is None:
        chunks = chunk_text(
            document.raw_text or "",
            settings.kb_chunk_size,
            settings.kb_chunk_overlap,
        )
    if not chunks:
        document.status = "failed"
        document.parse_error = "文档内容为空，无法入库"
        db.commit()
        return False, document.parse_error

    try:
        embeddings = embed_texts([c["text"] for c in chunks])
    except EmbeddingError as exc:
        document.status = "failed"
        document.parse_error = exc.message
        db.commit()
        return False, exc.message

    # 维度护栏：kb_chunks.embedding 列固定 settings.embedding_dim 维（当前 768），
    # 换 embedding 模型（如 bge-m3 1024 维）而未迁移表结构时，commit 会抛数据库异常。
    # 在这里提前拦截，把文档置 failed 并给出可操作的提示，而不是让 worker 任务崩掉。
    dim = settings.embedding_dim
    mismatch = next((len(vec) for vec in embeddings if len(vec) != dim), None)
    if mismatch is not None:
        message = (
            f"向量维度不符：模型返回 {mismatch} 维，数据表为 {dim} 维。"
            "更换 embedding 模型需先做表结构迁移并对已有语料重新入库"
        )
        logger.error(
            "ingest 维度不符 document_id=%s got=%s want=%s", document.id, mismatch, dim
        )
        document.status = "failed"
        document.parse_error = message
        db.commit()
        return False, message

    # 幂等：该文档已有旧块先清（任务重跑不产生重复向量）
    try:
        db.execute(delete(KBChunk).where(KBChunk.document_id == document.id))
        for i, (chunk, vec) in enumerate(zip(chunks, embeddings)):
            db.add(
                KBChunk(
                    document_id=document.id,
                    seq=i,
                    content=chunk["text"],
                    token_count=chunk["token_count"],
                    embedding=vec,
                )
            )
        document.status = "ready"
        document.embedding_model = settings.embedding_model
        document.embedding_dim = settings.embedding_dim
        document.parse_error = None
        db.commit()
    except Exception:
        # 写库阶段的意外异常（典型：维度不符触发数据库层校验）不能让文档停在
        # processing——用户会永远看到"入库中"。统一置 failed，可对同一文档重试。
        db.rollback()
        logger.exception("ingest 写库失败 document_id=%s", document.id)
        document.status = "failed"
        document.parse_error = "入库写入失败，请检查向量模型配置后重试"
        db.commit()
        return False, document.parse_error
    return True, None


def _cosine(a: list[float], b: list[float]) -> float:
    """手算余弦相似度。数据库只负责排序（走 HNSW 索引），阈值判定在 Python 侧做。"""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def _visible_ready_query(user_id: int | None, anonymous_id: str | None):
    """可见 + 就绪的切块查询骨架：向量路与词法路共用，避免过滤条件漂移。"""
    return (
        select(KBChunk, KBDocument.title)
        .join(KBDocument, KBChunk.document_id == KBDocument.id)
        .where(
            KBDocument.deleted_at.is_(None),
            KBDocument.status == "ready",
            _visible_clause(user_id, anonymous_id),
        )
    )


def search_chunks(
    db: Session,
    query_embedding: list[float],
    user_id: int | None,
    anonymous_id: str | None,
    top_k: int | None = None,
    query_text: str | None = None,
) -> list[dict]:
    """检索可见语料，返回命中块（含来源标题）。

    传了 query_text 且 kb_hybrid_enabled=True → 混合检索（向量 + BM25，RRF 融合）；
    否则只走向量检索（低于 kb_min_similarity 的按无关丢弃）。
    保持默认参数不传时行为与 v3.0 完全一致，老调用方与测试无需改动。
    """
    if query_text and settings.kb_hybrid_enabled:
        return search_chunks_hybrid(
            db, query_text, query_embedding, user_id, anonymous_id, top_k
        )

    rows = db.execute(
        _visible_ready_query(user_id, anonymous_id)
        .order_by(KBChunk.embedding.cosine_distance(query_embedding))
        .limit(top_k or settings.kb_search_top_k)
    ).all()

    results = []
    for chunk, title in rows:
        similarity = _cosine(chunk.embedding or [], query_embedding)
        if similarity < settings.kb_min_similarity:
            continue  # 明显无关的召回（如冷启动噪声）不当作引用来源
        results.append(
            {
                "document_id": chunk.document_id,
                "title": title,
                "seq": chunk.seq,
                "content": chunk.content,
                "similarity": round(similarity, 4),
            }
        )
    return results


def search_chunks_hybrid(
    db: Session,
    query_text: str,
    query_embedding: list[float],
    user_id: int | None,
    anonymous_id: str | None,
    top_k: int | None = None,
) -> list[dict]:
    """混合检索：向量路（语义）+ BM25 词法路，RRF 融合后取 top_k。

    RRF（Reciprocal Rank Fusion）只看两路各自的名次而非原始分数，因此不必把
    余弦相似度与 BM25 分数归一化到同一量纲：
        score(d) = Σ 1 / (k + rank_i(d))
    仅出现于一路的候选也保留（缺的那路不贡献分数），正好能救回
    "向量排 30 名、词法排第 1 名" 的术语类查询。

    过滤规则：混合路径不再套用 kb_min_similarity——那条阈值是给纯向量检索滤噪声用的，
    而"仅词法命中、向量分低"恰恰是混合检索要救回的术语类查询（如「聚簇索引」）。
    BM25 自带 IDF（无区分度的常用词分本身就低），噪声由词法侧自行抑制。

    :return: 命中块列表（含 similarity / rrf_score / lexical_score，后者仅供调试归因）
    """
    limit = top_k or settings.kb_search_top_k
    candidate_n = max(settings.kb_hybrid_candidates, limit)
    base_query = _visible_ready_query(user_id, anonymous_id)

    # —— 向量路：交给数据库按余弦距离排序（命中 HNSW 索引），只取候选 ——
    vector_rows = db.execute(
        base_query.order_by(KBChunk.embedding.cosine_distance(query_embedding)).limit(
            candidate_n
        )
    ).all()

    vector_rank: dict[int, int] = {}
    chunk_map: dict[int, tuple] = {}
    similarity_map: dict[int, float] = {}
    for rank, (chunk, title) in enumerate(vector_rows, start=1):
        vector_rank[chunk.id] = rank
        chunk_map[chunk.id] = (chunk, title)
        similarity_map[chunk.id] = _cosine(chunk.embedding or [], query_embedding)

    # —— 词法路：只取轻量列建内存 BM25 索引 ——
    # 不把 embedding 大列（768 浮点/块）拉进内存：它会让词法路的 IO/内存随语料量
    # 线性膨胀，而 BM25 排序只需要 id+content；最终融合入选的词法块再按需补实体
    lex_rows = db.execute(
        select(
            KBChunk.id,
            KBChunk.content,
            KBChunk.document_id,
            KBChunk.seq,
            KBDocument.title,
        )
        .join(KBDocument, KBChunk.document_id == KBDocument.id)
        .where(
            KBDocument.deleted_at.is_(None),
            KBDocument.status == "ready",
            _visible_clause(user_id, anonymous_id),
        )
    ).all()
    bm25 = Bm25Index([(r.id, r.content) for r in lex_rows])
    lexical_hits = bm25.search(query_text, candidate_n)
    lexical_rank = {
        cid: rank for rank, (cid, _score) in enumerate(lexical_hits, start=1)
    }
    lexical_score = {cid: score for cid, score in lexical_hits}

    # 词法入选但不在向量候选里的块：补取完整实体（含 embedding），保证融合结果的
    # 相似度口径与渲染字段同纯向量路径完全一致（补取数量 ≤ candidate_n，量小）
    need_full = set(lexical_rank) - set(vector_rank)
    if need_full:
        for chunk, title in db.execute(
            _visible_ready_query(user_id, anonymous_id).where(KBChunk.id.in_(need_full))
        ).all():
            chunk_map[chunk.id] = (chunk, title)

    # —— RRF 融合 ——
    rrf_k = settings.kb_rrf_k
    fused: list[tuple[float, float, float, int, str, object]] = []
    for chunk_id in set(vector_rank) | set(lexical_rank):
        score = 0.0
        if chunk_id in vector_rank:
            score += 1.0 / (rrf_k + vector_rank[chunk_id])
        if chunk_id in lexical_rank:
            score += 1.0 / (rrf_k + lexical_rank[chunk_id])

        similarity = similarity_map.get(chunk_id)
        if similarity is None:
            chunk, _title = chunk_map[chunk_id]
            similarity = _cosine(chunk.embedding or [], query_embedding)
            similarity_map[chunk_id] = similarity

        fused.append(
            (
                score,
                similarity,
                lexical_score.get(chunk_id, 0.0),
                chunk_id,
                *chunk_map[chunk_id],
            )
        )

    fused.sort(key=lambda item: item[0], reverse=True)

    results = []
    for score, similarity, lex_score, chunk_id, chunk, title in fused[:limit]:
        results.append(
            {
                "document_id": chunk.document_id,
                "title": title,
                "seq": chunk.seq,
                "content": chunk.content,
                "similarity": round(similarity, 4),
                # 调试/评测可见：融合分与词法分（前端不展示，仅用于归因）
                "rrf_score": round(score, 6),
                "lexical_score": round(lex_score, 4),
            }
        )
    return results


# —— 文档 CRUD（上传/列表/删除，owner 隔离）——


def create_document(
    db: Session,
    title: str,
    doc_type: str,
    raw_text: str,
    user_id: int | None,
    anonymous_id: str | None,
    file_hash: str | None = None,
    source_type: str = "uploaded",
) -> KBDocument:
    """新建文档记录（status=pending，入库由 worker 任务完成）。"""
    doc = KBDocument(
        title=title,
        source_type=source_type,
        scope="public" if source_type == "preset" else "private",
        doc_type=doc_type,
        file_hash=file_hash,
        raw_text=raw_text,
        user_id=None if source_type == "preset" else user_id,
        anonymous_id=None if source_type == "preset" else anonymous_id,
        status="pending",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def list_documents(
    db: Session, user_id: int | None, anonymous_id: str | None
) -> list[KBDocument]:
    """可见文档：预置语料 + 本人上传（不含软删除）。"""
    return list(
        db.scalars(
            select(KBDocument)
            .where(
                KBDocument.deleted_at.is_(None),
                _visible_clause(user_id, anonymous_id),
            )
            .order_by(KBDocument.created_at.desc(), KBDocument.id.desc())
        )
    )


def count_chunks_by_document(db: Session, document_ids: list[int]) -> dict[int, int]:
    """批量统计各文档的切块数（一次 group by 查完，避免逐条查的 N+1）。

    文档列表页与 Agent 的 kb_list 工具共用——只数条数，**不取任何切块正文**。
    """
    if not document_ids:
        return {}
    return dict(
        db.execute(
            select(KBChunk.document_id, func.count())
            .where(KBChunk.document_id.in_(document_ids))
            .group_by(KBChunk.document_id)
        ).all()
    )


def get_owned_document(
    db: Session,
    document_id: int,
    user_id: int | None,
    anonymous_id: str | None,
) -> KBDocument | None:
    """取可见文档；不存在或不可见返回 None（调用方统一 404，不泄露存在性）。"""
    return db.scalar(
        select(KBDocument).where(
            KBDocument.id == document_id,
            KBDocument.deleted_at.is_(None),
            _visible_clause(user_id, anonymous_id),
        )
    )


def soft_delete_document(db: Session, document: KBDocument) -> None:
    """软删除：置 deleted_at，chunks 仍在库（审计可查），检索自动排除。"""
    document.deleted_at = datetime.now(timezone.utc)
    db.commit()
