"""管理员只读面板（P6 清单项）：统计、用户列表、用量聚合。"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.db.session import get_db
from app.models.analysis import Analysis
from app.models.interview import InterviewSession
from app.models.resume import Resume
from app.models.usage_log import UsageLog
from app.models.user import User

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _admin_only(user: User = Depends(get_current_user)) -> User:  # noqa: B008
    if user.role != "admin":
        raise HTTPException(403, "仅管理员可访问")
    return user


@router.get("/stats")
def admin_stats(
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(_admin_only),  # noqa: B008
) -> dict:
    return {
        "users": db.scalar(select(func.count()).select_from(User)) or 0,
        "resumes": db.scalar(select(func.count()).select_from(Resume)) or 0,
        "analyses": db.scalar(select(func.count()).select_from(Analysis)) or 0,
        "interviews": db.scalar(select(func.count()).select_from(InterviewSession))
        or 0,
        "tokens_today": db.scalar(
            select(func.coalesce(func.sum(UsageLog.tokens_total), 0)).where(
                UsageLog.created_at
                >= datetime.now()
                .astimezone()
                .replace(hour=0, minute=0, second=0, microsecond=0)
            )
        )
        or 0,
    }


@router.get("/users")
def admin_users(
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(_admin_only),  # noqa: B008
) -> list[dict]:
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    result = []
    for u in users:
        resume_count = (
            db.scalar(
                select(func.count()).select_from(Resume).where(Resume.user_id == u.id)
            )
            or 0
        )
        result.append(
            {
                "id": u.id,
                "email": u.email,
                "role": u.role,
                "is_active": u.is_active,
                "created_at": u.created_at,
                "resume_count": resume_count,
            }
        )
    return result


@router.get("/usage")
def admin_usage(
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(_admin_only),  # noqa: B008
) -> list[dict]:
    """近 7 日每日 token 消耗与调用次数。"""
    rows = []
    for i in range(6, -1, -1):
        day = datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=i)
        next_day = day + timedelta(days=1)
        tokens = (
            db.scalar(
                select(func.coalesce(func.sum(UsageLog.tokens_total), 0)).where(
                    UsageLog.created_at >= day, UsageLog.created_at < next_day
                )
            )
            or 0
        )
        count = (
            db.scalar(
                select(func.count())
                .select_from(UsageLog)
                .where(UsageLog.created_at >= day, UsageLog.created_at < next_day)
            )
            or 0
        )
        rows.append(
            {"date": day.strftime("%Y-%m-%d"), "calls": count, "tokens": tokens}
        )
    return rows
