"""简历路由：上传 / 历史列表 / 详情。业务路由统一挂 /api 前缀（AGENTS.md 约定）。

错误分两类（阶段1 设计定稿）：
- 上传拦截（非 PDF / 超 5MB / 超 5 页）→ 4xx，不落库不留文件；
- 解析问题（扫描件 / 损坏）→ 201 落库留痕，parse_status 给前端友好提示。
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth_deps import get_optional_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.resume import Resume
from app.models.user import User
from app.schemas.resume import ResumeDetail, ResumeOut, UploadResult
from app.services.pdf_parser import PDF_MAGIC, ParseError, parse_pdf

router = APIRouter(prefix="/api", tags=["resumes"])

# 存储目录相对启动目录（与 .env 同一约定：统一从 backend/ 启动）。
# 文件放在 web 根目录之外、不挂静态路由，外界无法按 URL 直接访问（PROJECT-PLAN §5）。
UPLOAD_DIR = Path(settings.upload_dir)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/resumes", response_model=UploadResult, status_code=201)
async def upload_resume(
    file: UploadFile,
    db: Session = Depends(get_db),  # noqa: B008  FastAPI 依赖注入官方惯用法
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> UploadResult:
    """上传简历：校验 → hash 去重 → 解析落库。重复文件返回 duplicate=true。"""
    data = await file.read()
    filename = Path(
        file.filename or "resume.pdf"
    ).name  # 消毒：只留文件名本身，剥掉路径部分

    # 三道上传拦截：类型（扩展名 + 文件头双校验）、大小、页数——都不落库
    if not filename.lower().endswith(".pdf") or not data.startswith(PDF_MAGIC):
        raise HTTPException(415, "只支持 PDF 文件，请上传 PDF 格式的简历")
    if len(data) > settings.upload_max_size:
        raise HTTPException(413, "文件超过 5MB 限制，请压缩后重新上传")

    file_hash = hashlib.sha256(data).hexdigest()

    # 重复上传：hash 命中未删除的历史记录 → 直接复用，不重复解析（PROJECT-PLAN §3）
    ownership = (
        Resume.user_id == user.id if user is not None else Resume.user_id.is_(None)
    )
    existing = db.scalar(
        select(Resume).where(
            Resume.file_hash == file_hash,
            Resume.deleted_at.is_(None),
            ownership,
        )
    )
    if existing is not None:
        return UploadResult(
            duplicate=True, resume=ResumeDetail.model_validate(existing)
        )

    parse_status = "success"
    parse_error: str | None = None
    raw_text: str | None = None
    page_count: int | None = None
    try:
        result = parse_pdf(data, settings.upload_max_pages)
    except ParseError as exc:
        if exc.kind == "too_many_pages":
            raise HTTPException(400, exc.message) from exc
        parse_status = exc.kind  # unsupported（扫描件）/ failed（损坏）：落库留痕
        parse_error = exc.message
    else:
        raw_text = result.text
        page_count = result.page_count

    # 文件以内容 hash 命名：同内容只存一份，且文件名不可预测
    storage_path = UPLOAD_DIR / f"{file_hash}.pdf"
    storage_path.write_bytes(data)

    resume = Resume(
        user_id=user.id if user is not None else None,
        filename=filename,
        file_hash=file_hash,
        storage_path=str(storage_path),
        raw_text=raw_text,
        page_count=page_count,
        file_size=len(data),
        parse_status=parse_status,
        parse_error=parse_error,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return UploadResult(duplicate=False, resume=ResumeDetail.model_validate(resume))


@router.get("/resumes", response_model=list[ResumeOut])
def list_resumes(
    db: Session = Depends(get_db),  # noqa: B008
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> list[Resume]:
    """历史列表：登录用户只能看到自己的简历，匿名阶段保持原有兼容行为。"""
    query = select(Resume).where(Resume.deleted_at.is_(None))
    if user is not None:
        query = query.where(Resume.user_id == user.id)
    else:
        query = query.where(Resume.user_id.is_(None))
    return list(db.scalars(query.order_by(Resume.created_at.desc(), Resume.id.desc())))


@router.get("/resumes/{resume_id}", response_model=ResumeDetail)
def get_resume(
    resume_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> Resume:
    """详情：登录用户只能访问自己的记录，跨用户统一返回 404。"""
    resume = db.get(Resume, resume_id)
    if resume is None or resume.deleted_at is not None:
        raise HTTPException(404, "简历记录不存在或已删除")
    if user is not None and resume.user_id != user.id:
        raise HTTPException(404, "简历记录不存在或已删除")
    if user is None and resume.user_id is not None:
        raise HTTPException(404, "简历记录不存在或已删除")
    return resume


@router.delete("/resumes/{resume_id}", status_code=204)
def delete_resume(
    resume_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> None:
    """软删除（P7 隐私入口）：置 deleted_at，数据保留可审计。仅本人可删。"""
    resume = db.get(Resume, resume_id)
    if resume is None or resume.deleted_at is not None:
        raise HTTPException(404, "简历记录不存在或已删除")
    if user is not None and resume.user_id != user.id:
        raise HTTPException(404, "简历记录不存在或已删除")
    if user is None and resume.user_id is not None:
        raise HTTPException(404, "简历记录不存在或已删除")
    resume.deleted_at = datetime.now(timezone.utc)
    db.commit()
