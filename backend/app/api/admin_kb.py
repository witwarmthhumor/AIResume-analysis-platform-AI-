"""管理员语料库管理（v3.1）：列所有文档 / 删任意文档 / 上传为预置 public。挂 /api/admin/kb 前缀。

与普通用户 /api/kb 的区别：
- 列表：所有文档（含预置、所有用户上传），带 owner 邮箱与块数
- 删除：绕过 owner 校验与 preset 不可删限制，软删除可审计
- 上传：source_type=preset, scope=public（全站可见），不限每日上传次数与名下文档数
"""

import hashlib

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.admin import _admin_only
from app.db.session import get_db
from app.models.kb import KBChunk, KBDocument
from app.models.user import User
from app.services.kb_service import create_document, soft_delete_document
from app.services.pdf_parser import PDF_MAGIC, ParseError, parse_pdf
from app.worker.tasks import ingest_kb

router = APIRouter(prefix="/api/admin/kb", tags=["admin_kb"])

_TEXT_EXTENSIONS = {"txt", "md", "markdown"}
_MAX_SIZE = 5 * 1024 * 1024  # 与普通上传一致，5MB


def _doc_out(
    doc: KBDocument, chunk_count: int | None = None, owner_email: str | None = None
) -> dict:
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
        "owner_email": owner_email,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


@router.get("/documents")
def list_all_documents(
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(_admin_only),  # noqa: B008
) -> list[dict]:
    """所有文档列表（含预置与所有用户上传，未删除），带 owner 邮箱与块数。"""
    docs = list(
        db.scalars(
            select(KBDocument)
            .where(KBDocument.deleted_at.is_(None))
            .order_by(KBDocument.created_at.desc(), KBDocument.id.desc())
        )
    )
    # 块数：一条 GROUP BY
    counts = (
        dict(
            db.execute(
                select(KBChunk.document_id, func.count())
                .where(KBChunk.document_id.in_([d.id for d in docs]))
                .group_by(KBChunk.document_id)
            ).all()
        )
        if docs
        else {}
    )
    # owner 邮箱：user_id 非空的查 users
    user_ids = [d.user_id for d in docs if d.user_id is not None]
    emails = (
        dict(db.execute(select(User.id, User.email).where(User.id.in_(user_ids))).all())
        if user_ids
        else {}
    )

    result = []
    for d in docs:
        if d.source_type == "preset":
            owner = "系统预置"
        elif d.user_id is not None:
            owner = emails.get(d.user_id)
        else:
            owner = "匿名用户"
        result.append(_doc_out(d, counts.get(d.id), owner))
    return result


@router.delete("/documents/{document_id}", status_code=204)
def delete_any_document(
    document_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(_admin_only),  # noqa: B008
) -> None:
    """删除任意文档（含预置、他人上传），软删除可审计。绕过普通接口的 preset 不可删限制。"""
    doc = db.get(KBDocument, document_id)
    if doc is None or doc.deleted_at is not None:
        raise HTTPException(404, "知识库文档不存在或已删除")
    soft_delete_document(db, doc)


@router.post("/documents", status_code=201)
def upload_preset_document(
    file: UploadFile,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(_admin_only),  # noqa: B008
) -> dict:
    """上传文档为预置 public（全站可见，不限流，不占用户名下额度）。"""
    filename = file.filename or "document.txt"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if file.size is not None and file.size > _MAX_SIZE:
        raise HTTPException(413, "文件超过 5MB 限制，请压缩后重新上传")
    data = file.file.read()
    if len(data) > _MAX_SIZE:
        raise HTTPException(413, "文件超过 5MB 限制，请压缩后重新上传")

    # 文档类型与正文抽取（与普通上传一致）
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
            raw_text = parse_pdf(data, 50).text  # 预置文档允许更多页
        except ParseError as exc:
            raise HTTPException(400, exc.message) from exc
    else:
        raise HTTPException(415, "仅支持 .txt / .md / .pdf 格式的文档")

    if not raw_text.strip():
        raise HTTPException(400, "文档内容为空，无法入库")

    file_hash = hashlib.sha256(data).hexdigest()

    # 全局去重：预置语料不按归属，同内容全站只存一份
    existing = db.scalar(
        select(KBDocument).where(
            KBDocument.file_hash == file_hash,
            KBDocument.deleted_at.is_(None),
            KBDocument.source_type == "preset",
        )
    )
    if existing is not None:
        return _doc_out(existing, owner_email="系统预置")

    doc = create_document(
        db,
        title=filename,
        doc_type=doc_type,
        raw_text=raw_text,
        user_id=None,
        anonymous_id=None,
        file_hash=file_hash,
        source_type="preset",
    )
    db.commit()
    task = ingest_kb.delay(doc.id)  # 异步入库：切块 + 向量化
    return {**_doc_out(doc, owner_email="系统预置"), "task_id": task.id}
