"""AI 分析路由：发起分析 / 查看最新报告。挂 /api 前缀。

流程（POST analyze）：校验简历可分析 → 命中有效报告直接返回（不重复计费）→
每日限流 → 调封装层（内部已含 JSON 校验重试）→ analyses 落库（失败也留痕）→
usage_logs 记账（限流依据）。AI 调用是同步的，前端需等待 10~30 秒。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth_deps import get_optional_current_user
from app.api.deps import enforce_daily_limit, get_anonymous_id
from app.core.config import settings
from app.db.session import get_db
from app.models.analysis import Analysis
from app.models.resume import Resume
from app.models.usage_log import UsageLog
from app.models.user import User
from app.schemas.analysis import AnalysisOut, AnalysisResultOut
from app.services.ai_client import AIError, AnalysisResult, analyze_resume
from app.services.prompts import PROMPT_VERSION

router = APIRouter(prefix="/api", tags=["analyses"])


def _to_out(a: Analysis) -> AnalysisOut:
    """ORM → 出参。report 字段对应库里的 result_json，显式构造，避免别名戏法。"""
    return AnalysisOut(
        id=a.id,
        resume_id=a.resume_id,
        model_name=a.model_name,
        prompt_version=a.prompt_version,
        report=a.result_json,
        tokens_prompt=a.tokens_prompt,
        tokens_completion=a.tokens_completion,
        duration_ms=a.duration_ms,
        created_at=a.created_at,
    )


def _latest_valid_analysis(db: Session, resume_id: int) -> Analysis | None:
    return db.scalar(
        select(Analysis)
        .where(
            Analysis.resume_id == resume_id,
            Analysis.valid_json.is_(True),  # 无效输出不留着复用，重试才有机会修好
            Analysis.prompt_version == PROMPT_VERSION,  # 提示词改版后旧报告不再复用
        )
        .order_by(Analysis.created_at.desc(), Analysis.id.desc())
    )


@router.post("/resumes/{resume_id}/analyze", response_model=AnalysisResultOut)
def analyze_resume_endpoint(
    resume_id: int,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008  FastAPI 依赖注入官方惯用法
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> AnalysisResultOut:
    """对已成功解析的简历发起 AI 分析。同一简历同版本提示词只算一次。"""
    resume = db.get(Resume, resume_id)
    if resume is None or resume.deleted_at is not None:
        raise HTTPException(404, "简历记录不存在或已删除")
    if user is not None and resume.user_id != user.id:
        raise HTTPException(404, "简历记录不存在或已删除")
    if user is None and resume.user_id is not None:
        raise HTTPException(404, "简历记录不存在或已删除")
    if resume.parse_status != "success" or not resume.raw_text:
        raise HTTPException(400, "该简历未成功解析出文本，无法发起 AI 分析")

    existing = _latest_valid_analysis(db, resume_id)
    if existing is not None:
        return AnalysisResultOut(cached=True, analysis=_to_out(existing))

    enforce_daily_limit(db, anonymous_id, settings.daily_analysis_limit)

    try:
        result = analyze_resume(resume.raw_text, settings)
    except AIError as exc:
        _record(db, resume_id, anonymous_id, user, request, exc, None)
        raise HTTPException(502, exc.message) from exc

    analysis = Analysis(
        resume_id=resume_id,
        user_id=user.id if user is not None else None,
        anonymous_id=anonymous_id if user is None else None,
        model_name=result.model_name,
        prompt_version=PROMPT_VERSION,
        result_json=result.report,
        valid_json=result.valid,
        tokens_prompt=result.tokens_prompt,
        tokens_completion=result.tokens_completion,
        duration_ms=result.duration_ms,
    )
    _record(db, resume_id, anonymous_id, user, request, result, analysis)
    return AnalysisResultOut(cached=False, analysis=_to_out(analysis))


@router.get("/resumes/{resume_id}/analysis", response_model=AnalysisOut)
def get_analysis(
    resume_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> AnalysisOut:
    """该简历最新的有效分析报告；没有则 404。"""
    resume = db.get(Resume, resume_id)
    if resume is None or resume.deleted_at is not None:
        raise HTTPException(404, "简历记录不存在或已删除")
    if user is not None and resume.user_id != user.id:
        raise HTTPException(404, "简历记录不存在或已删除")
    if user is None and resume.user_id is not None:
        raise HTTPException(404, "简历记录不存在或已删除")
    analysis = _latest_valid_analysis(db, resume_id)
    if analysis is None:
        raise HTTPException(404, "该简历还没有分析报告")
    return _to_out(analysis)


def _record(
    db: Session,
    resume_id: int,
    anonymous_id: str,
    user: User | None,
    request: Request,
    result: AnalysisResult | AIError,
    analysis: Analysis | None,
) -> None:
    """analyses 落库（失败留痕）+ usage_logs 记账（限流与 token 可见）。"""
    if isinstance(result, AIError):
        result_json = (
            {"raw_output": result.raw_output}
            if result.raw_output
            else {"error": result.message}
        )
        valid, model, tokens = False, settings.ai_model, None
    else:
        result_json, valid, model = result.report, result.valid, result.model_name
        tokens = (result.tokens_prompt or 0) + (result.tokens_completion or 0)

    if analysis is None:  # 失败留痕：valid_json=false，调试与迭代对比用
        analysis = Analysis(
            resume_id=resume_id,
            user_id=user.id if user is not None else None,
            anonymous_id=anonymous_id if user is None else None,
            model_name=model,
            prompt_version=PROMPT_VERSION,
            result_json=result_json,
            valid_json=valid,
        )

    db.add(analysis)  # 成功路径的对象也在这里统一入会话
    db.add(
        UsageLog(
            user_id=user.id if user is not None else None,
            anonymous_id=anonymous_id if user is None else None,
            action_type="analysis",
            model_name=model,
            tokens_total=tokens,
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(analysis)
