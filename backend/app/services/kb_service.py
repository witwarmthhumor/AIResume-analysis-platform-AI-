"""知识库服务（v3.0）：切块入库、向量检索、文档 CRUD（owner 隔离）。

ingest：切块 → 批量向量化（Ollama）→ 写 kb_chunks → 文档置 ready。
search：提问向量做 HNSW 余弦近似检索，召回 top-k 块并带来源文档信息。
owner 隔离：scope=public（预置语料）全站可见；scope=private（用户上传）仅本人可见。
"""

import math
from datetime import datetime, timezone

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.kb import KBChunk, KBDocument
from app.services.embedding_service import EmbeddingError, embed_texts
from app.services.kb_chunker import chunk_text


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

    # 幂等：该文档已有旧块先清（任务重跑不产生重复向量）
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
    return True, None


def search_chunks(
    db: Session,
    query_embedding: list[float],
    user_id: int | None,
    anonymous_id: str | None,
    top_k: int | None = None,
) -> list[dict]:
    """余弦近似检索可见语料，返回命中块（含来源标题），相似度低于阈值的不算命中。"""
    rows = db.execute(
        select(KBChunk, KBDocument.title)
        .join(KBDocument, KBChunk.document_id == KBDocument.id)
        .where(
            KBDocument.deleted_at.is_(None),
            KBDocument.status == "ready",
            _visible_clause(user_id, anonymous_id),
        )
        .order_by(KBChunk.embedding.cosine_distance(query_embedding))
        .limit(top_k or settings.kb_search_top_k)
    ).all()

    results = []
    for chunk, title in rows:
        # order_by 里用的是 SQLAlchemy 表达式（数据库算距离）；取回后 chunk.embedding
        # 是 Python list，这里手动算余弦相似度做阈值过滤
        vec = chunk.embedding or []
        if not vec:
            continue
        dot = sum(a * b for a, b in zip(vec, query_embedding))
        norm_a = math.sqrt(sum(a * a for a in vec))
        norm_b = math.sqrt(sum(b * b for b in query_embedding))
        similarity = dot / (norm_a * norm_b) if norm_a and norm_b else 0.0
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
