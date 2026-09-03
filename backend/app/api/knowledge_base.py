"""知识库文档接口（v3.0）：上传 / 列表 / 删除。挂 /api 前缀。

上传支持 .txt/.md（直接读文本）与 .pdf（复用 pdf_parser）；内容存 kb_documents.raw_text，
不落磁盘文件。入库走 Celery 异步入库（切块+向量化），同内容按 file_hash 去重。
"""

import hashlib

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth_deps import get_optional_current_user
from app.api.deps import enforce_daily_limit, get_anonymous_id
from app.core.config import settings
from app.db.session import get_db
from app.models.kb import KBChunk, KBDocument
from app.models.user import User
from app.services.kb_service import (
    create_document,
    get_owned_document,
    list_documents,
    soft_delete_document,
)
from app.services.pdf_parser import PDF_MAGIC, ParseError, parse_pdf
from app.services.usage_service import write_usage
from app.worker.tasks import ingest_kb

router = APIRouter(prefix="/api/kb", tags=["knowledge_base"])

_TEXT_EXTENSIONS = {"txt", "md", "markdown"}
_MAX_SIZE = 5 * 1024 * 1024  # 与简历上传一致，5MB


def _owner_clause(user: User | None, anonymous_id: str | None):
    """归属条件：登录按 user_id，匿名按 anonymous_id（匿名间互不可见）。"""
    if user is not None:
        return KBDocument.user_id == user.id
    return KBDocument.anonymous_id == anonymous_id


def _doc_out(doc: KBDocument, chunk_count: int | None = None) -> dict:
    return {
        "id": doc.id,
        "title": doc.title,
        "source_type": doc.source_type,
        "scope": doc.scope,
        "doc_type": doc.doc_type,
        "status": doc.status,
        "parse_error": doc.parse_error,
        "embedding_model": doc.embedding_model,
        "chunk_count": chunk_count,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


@router.get("/documents", response_model=list[dict])
def list_kb_documents(
    db: Session = Depends(get_db),  # noqa: B008
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
) -> list[dict]:
    """可见文档列表（预置语料 + 本人上传），含切块数。"""
    docs = list_documents(db, user.id if user else None, anonymous_id)
    counts = dict(
        db.execute(
            select(KBChunk.document_id, func.count())
            .where(KBChunk.document_id.in_([d.id for d in docs]))
            .group_by(KBChunk.document_id)
        ).all()
    ) if docs else {}
    return [_doc_out(d, counts.get(d.id)) for d in docs]


@router.post("/documents", response_model=dict, status_code=201)
def upload_kb_document(
    file: UploadFile,
    db: Session = Depends(get_db),  # noqa: B008
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
) -> dict:
    """上传知识库文档（txt/md/pdf），异步入库后可用于 Playground 问答。

    上传会触发切块+批量向量化（消耗 Ollama 资源），与提问同等级别限额：
    每日次数上限 + 名下文档数上限。
    """
    user_id = user.id if user else None
    enforce_daily_limit(
        db,
        anonymous_id,
        settings.daily_kb_upload_limit,
        "kb_upload",
        user_id=user_id,
    )

    filename = file.filename or "document.txt"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if file.size is not None and file.size > _MAX_SIZE:
        raise HTTPException(413, "文件超过 5MB 限制，请压缩后重新上传")
    data = file.file.read()
    if len(data) > _MAX_SIZE:
        raise HTTPException(413, "文件超过 5MB 限制，请压缩后重新上传")

    # 文档类型与正文抽取
    if ext in _TEXT_EXTENSIONS:
        doc_type = "markdown" if ext in ("md", "markdown") else "text"
        try:
            raw_text = data.decode("utf-8")
        except UnicodeDecodeError:
            raw_text = data.decode("gbk", errors="replace")
    elif ext == "pdf":
        if not data.startswith(PDF_MAGIC):
            raise HTTPException(415, "文件内容不是 PDF，请上传 .txt / .md / .pdf")
        doc_type = "pdf"
        try:
            raw_text = parse_pdf(data, settings.upload_max_pages).text
        except ParseError as exc:
            raise HTTPException(400, exc.message) from exc
    else:
        raise HTTPException(415, "仅支持 .txt / .md / .pdf 格式的文档")

    if not raw_text.strip():
        raise HTTPException(400, "文档内容为空，无法入库")

    file_hash = hashlib.sha256(data).hexdigest()

    # 名下文档数上限（未删除的上传文档），防刷存储与向量化资源
    owned_count = (
        db.scalar(
            select(func.count())
            .select_from(KBDocument)
            .where(
                KBDocument.deleted_at.is_(None),
                KBDocument.source_type == "uploaded",
                _owner_clause(user, anonymous_id),
            )
        )
        or 0
    )
    if owned_count >= settings.kb_max_documents_per_owner:
        raise HTTPException(
            429,
            f"知识库文档数已达上限（{settings.kb_max_documents_per_owner} 篇），请先删除不需要的文档",
        )

    # 去重：同一归属下同内容文档已存在（且未删除）则复用。
    # 匿名用户必须带 anonymous_id 条件——只按 user_id IS NULL 匹配会把
    # 其他匿名用户的同内容文档误判为自己的（表现为"上传成功但消失了"）
    existing = db.scalar(
        select(KBDocument).where(
            KBDocument.file_hash == file_hash,
            KBDocument.deleted_at.is_(None),
            _owner_clause(user, anonymous_id),
        )
    )
    if existing is not None:
        return _doc_out(existing)

    doc = create_document(
        db,
        title=filename,
        doc_type=doc_type,
        raw_text=raw_text,
        user_id=user_id,
        anonymous_id=anonymous_id if user is None else None,
        file_hash=file_hash,
        source_type="uploaded",
    )
    # 记账：kb_upload 用量（限流依据），与文档创建同一事务
    write_usage(
        db,
        anonymous_id if user is None else None,
        user_id,
        "kb_upload",
        None,
        None,
        None,
    )
    db.commit()
    task = ingest_kb.delay(doc.id)  # 异步入库：切块 + 向量化
    return {**_doc_out(doc), "task_id": task.id}


@router.delete("/documents/{document_id}", status_code=204)
def delete_kb_document(
    document_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
) -> None:
    """删除文档（软删除）。预置语料不可删；仅本人上传可删。"""
    doc = get_owned_document(db, document_id, user.id if user else None, anonymous_id)
    if doc is None:
        raise HTTPException(404, "知识库文档不存在或已删除")
    if doc.source_type == "preset":
        raise HTTPException(400, "预置语料不可删除")
    soft_delete_document(db, doc)
