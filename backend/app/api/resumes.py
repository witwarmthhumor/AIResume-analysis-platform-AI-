"""简历路由：上传 / 历史列表 / 详情。业务路由统一挂 /api 前缀（AGENTS.md 约定）。

错误分两类（阶段1 设计定稿）：
- 上传拦截（非 PDF / 超 5MB / 超 5 页）→ 4xx，不落库不留文件；
- 解析问题（扫描件 / 损坏）→ 201 落库留痕，parse_status 给前端友好提示。
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth_deps import get_optional_current_user
from app.api.deps import enforce_daily_limit, get_anonymous_id, owner_clause
from app.core.config import settings
from app.db.session import get_db
from app.models.resume import Resume
from app.models.user import User
from app.schemas.resume import ResumeDetail, ResumeOut, UploadResult
from app.services.pdf_parser import PDF_MAGIC, ParseError, parse_pdf
from app.services.usage_service import write_usage

router = APIRouter(prefix="/api", tags=["resumes"])

# 存储目录相对启动目录（与 .env 同一约定：统一从 backend/ 启动）。
# 文件放在 web 根目录之外、不挂静态路由，外界无法按 URL 直接访问（PROJECT-PLAN §5）。
UPLOAD_DIR = Path(settings.upload_dir)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/resumes", response_model=UploadResult, status_code=201)
async def upload_resume(
    file: UploadFile,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008  FastAPI 依赖注入官方惯用法
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
) -> UploadResult:
    """上传简历：校验 → hash 去重 → 解析落库。重复文件返回 duplicate=true。

    匿名上传必须写入 anonymous_id：详情/删除的归属判断依赖它区分
    「谁的匿名简历」，漏写会造成匿名用户横向越权（可读删他人记录）。
    """
    # 大小预检（读文件之前）：Starlette 会把整个请求体收进临时文件，
    # 不预检的话超大文件仍会被完整接收一遍才被 413 拒掉
    max_mb = settings.upload_max_size // (1024 * 1024)
    if file.size is not None and file.size > settings.upload_max_size:
        raise HTTPException(413, f"文件超过 {max_mb}MB 限制，请压缩后重新上传")

    data = await file.read()
    filename = Path(
        file.filename or "resume.pdf"
    ).name  # 消毒：只留文件名本身，剥掉路径部分

    # 上传拦截：类型（扩展名 + 文件头双校验）与页数在此校验，大小已在读前预检/读后兜底——都不落库
    if not filename.lower().endswith(".pdf") or not data.startswith(PDF_MAGIC):
        raise HTTPException(415, "只支持 PDF 文件，请上传 PDF 格式的简历")
    if (
        len(data) > settings.upload_max_size
    ):  # 兜底：无 Content-Length 时 size 可能为 None
        raise HTTPException(413, f"文件超过 {max_mb}MB 限制，请压缩后重新上传")

    file_hash = hashlib.sha256(data).hexdigest()

    # 每日上传限额（含重复上传也计数：请求本身占了带宽与校验成本）
    enforce_daily_limit(
        db,
        anonymous_id,
        settings.daily_upload_limit,
        "parse",
        user_id=user.id if user else None,
    )

    # 重复上传：hash 命中未删除的历史记录 → 直接复用，不重复解析（PROJECT-PLAN §3）。
    # 去重按归属者隔离（登录按 user_id、匿名按 anonymous_id）——匿名之间互不吞单
    existing = db.scalar(
        select(Resume).where(
            Resume.file_hash == file_hash,
            Resume.deleted_at.is_(None),
            owner_clause(Resume, user, anonymous_id),
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
        anonymous_id=None if user is not None else anonymous_id,
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
    write_usage(
        db,
        anonymous_id=None if user is not None else anonymous_id,
        user_id=user.id if user else None,
        action_type="parse",
        model_name=None,
        tokens_total=None,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return UploadResult(duplicate=False, resume=ResumeDetail.model_validate(resume))


@router.get("/resumes", response_model=list[ResumeOut])
def list_resumes(
    db: Session = Depends(get_db),  # noqa: B008
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
) -> list[Resume]:
    """历史列表：登录用户只能看到自己的简历，匿名用户只能看到自己的匿名简历。"""
    query = select(Resume).where(
        Resume.deleted_at.is_(None),
        owner_clause(Resume, user, anonymous_id),
    )
    return list(db.scalars(query.order_by(Resume.created_at.desc(), Resume.id.desc())))


@router.get("/resumes/{resume_id}", response_model=ResumeDetail)
def get_resume(
    resume_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
) -> Resume:
    """详情：仅归属者可见（登录按 user_id、匿名按 anonymous_id），跨用户统一 404。"""
    resume = db.get(Resume, resume_id)
    if resume is None or resume.deleted_at is not None:
        raise HTTPException(404, "简历记录不存在或已删除")
    if user is not None and resume.user_id != user.id:
        raise HTTPException(404, "简历记录不存在或已删除")
    if user is None and (
        resume.user_id is not None or resume.anonymous_id != anonymous_id
    ):
        raise HTTPException(404, "简历记录不存在或已删除")
    return resume


@router.delete("/resumes/{resume_id}", status_code=204)
def delete_resume(
    resume_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
) -> None:
    """软删除（P7 隐私入口）：置 deleted_at，数据保留可审计。仅归属者可删。"""
    resume = db.get(Resume, resume_id)
    if resume is None or resume.deleted_at is not None:
        raise HTTPException(404, "简历记录不存在或已删除")
    if user is not None and resume.user_id != user.id:
        raise HTTPException(404, "简历记录不存在或已删除")
    if user is None and (
        resume.user_id is not None or resume.anonymous_id != anonymous_id
    ):
        raise HTTPException(404, "简历记录不存在或已删除")
    resume.deleted_at = datetime.now(timezone.utc)
    db.commit()
