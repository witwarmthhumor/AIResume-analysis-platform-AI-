"""面试题库接口（v4.2 B3）：生成 / 列表 / 详情 / 删除。挂 /api/question-banks 前缀。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import enforce_daily_limit, get_anonymous_id
from app.core.config import settings
from app.db.session import get_db
from app.models.usage_log import UsageLog
from app.models.user import User
from app.services import question_bank_service

router = APIRouter(prefix="/api/question-banks", tags=["question_banks"])


class GenerateIn(BaseModel):
    resume_id: int


@router.post("", status_code=201)
def generate_bank(
    body: GenerateIn,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """基于本人简历生成一套面试题（一次 LLM 调用，同步返回）。

    记账 action_type=question_bank（usage_stats 中文标签见 tools._ACTION_LABELS）。
    """
    enforce_daily_limit(
        db,
        anonymous_id,
        settings.daily_question_bank_limit,
        "question_bank",
        user_id=user.id,
    )
    try:
        bank, result = question_bank_service.generate_question_bank(
            db, user, body.resume_id
        )
    except question_bank_service.ResumeNotParsedError as exc:
        # 本人可纠正状态：400 + 明确指引（v6 审计 G6-4 语义分拆）
        raise HTTPException(400, exc.message) from None
    except ValueError as exc:
        # 不存在/非本人：404 统一口径，不泄露「资源存在但无权」
        raise HTTPException(404, str(exc)) from None
    # 记账：生成成功后写 usage_logs（question_bank 动作），与题库落库同一事务
    db.add(
        UsageLog(
            user_id=user.id,
            anonymous_id=None,  # 强制登录后恒有 user
            action_type="question_bank",
            model_name=result.model_name,
            tokens_total=(result.tokens_prompt or 0) + (result.tokens_completion or 0),
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    return {
        "id": bank.id,
        "title": bank.title,
        "question_count": bank.question_count,
        "questions": bank.questions_json.get("questions", []),
        "tokens": (result.tokens_prompt or 0) + (result.tokens_completion or 0),
        "duration_ms": result.duration_ms,
    }


@router.get("")
def list_banks(
    limit: int = Query(100, ge=1, le=500),  # v4.3 分页收口：默认 100 封顶防全量
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> list[dict]:
    banks = question_bank_service.list_question_banks(
        db, user, limit=limit, offset=offset
    )
    return [
        {
            "id": b.id,
            "title": b.title,
            "question_count": b.question_count,
            "resume_id": b.resume_id,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in banks
    ]


@router.get("/{bank_id}")
def get_bank(
    bank_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    bank = question_bank_service.get_owned_bank(db, user, bank_id)
    if bank is None:
        raise HTTPException(404, "题库不存在或已删除")
    return {
        "id": bank.id,
        "title": bank.title,
        "question_count": bank.question_count,
        "resume_id": bank.resume_id,
        "questions": bank.questions_json.get("questions", []),
        "created_at": bank.created_at.isoformat() if bank.created_at else None,
    }


@router.delete("/{bank_id}", status_code=204)
def delete_bank(
    bank_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> None:
    bank = question_bank_service.get_owned_bank(db, user, bank_id)
    if bank is None:
        raise HTTPException(404, "题库不存在或已删除")
    db.delete(bank)
    db.commit()
