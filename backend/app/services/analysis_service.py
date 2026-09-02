"""分析结果落库服务（P4 服务层下沉）。负责 analyses 落库 + usage_logs 记账。"""

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.analysis import Analysis
from app.models.usage_log import UsageLog
from app.services.ai_client import AIError, AnalysisResult
from app.services.prompts import PROMPT_VERSION


def record_analysis(
    db: Session,
    resume_id: int,
    anonymous_id: str,
    user_id: int | None,
    request: Request,
    result: AnalysisResult | AIError,
    analysis: Analysis | None = None,
) -> Analysis:
    """analyses 落库（失败也留痕）+ usage_logs 记账（限流与 token 可见）。返回落库对象。"""
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
            user_id=user_id,
            anonymous_id=anonymous_id,
            model_name=model,
            prompt_version=PROMPT_VERSION,
            result_json=result_json,
            valid_json=valid,
        )

    db.add(analysis)  # 成功路径的对象也在这里统一入会话
    db.add(
        UsageLog(
            user_id=user_id,
            anonymous_id=anonymous_id,
            action_type="analysis",
            model_name=model,
            tokens_total=tokens,
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(analysis)
    return analysis
