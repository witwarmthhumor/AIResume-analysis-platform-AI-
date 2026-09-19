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
    # 一条 GROUP BY 拿全部用户的简历数，避免每个用户单独 count 的 N+1 查询
    resume_counts = dict(
        db.execute(
            select(Resume.user_id, func.count())
            .where(Resume.user_id.is_not(None))
            .group_by(Resume.user_id)
        ).all()
    )
    return [
        {
            "id": u.id,
            "email": u.email,
            "role": u.role,
            "is_active": u.is_active,
            "created_at": u.created_at,
            "resume_count": resume_counts.get(u.id, 0),
        }
        for u in users
    ]


@router.get("/usage")
def admin_usage(
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(_admin_only),  # noqa: B008
) -> list[dict]:
    """近 7 日每日 token 消耗与调用次数。"""
    # 单条 GROUP BY 聚合替代逐日 14 次查询；day_idx 以"本地今天 0 点"为基准，
    # 与旧实现完全同一套本地时区边界（created_at 为 timestamptz，epoch 为绝对秒）
    today_start = (
        datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    )
    start, end = today_start - timedelta(days=6), today_start + timedelta(days=1)
    day_idx = (
        func.floor(func.extract("epoch", UsageLog.created_at - start) / 86400.0)
    ).label("day_idx")
    grouped = {
        int(idx): (int(cnt), int(tok))
        for idx, cnt, tok in db.execute(
            select(
                day_idx,
                func.count(),
                func.coalesce(func.sum(UsageLog.tokens_total), 0),
            )
            .where(UsageLog.created_at >= start, UsageLog.created_at < end)
            .group_by(day_idx)
        ).all()
    }
    rows = []
    for i in range(6, -1, -1):
        day = today_start - timedelta(days=i)
        calls, tokens = grouped.get(6 - i, (0, 0))
        rows.append(
            {"date": day.strftime("%Y-%m-%d"), "calls": calls, "tokens": tokens}
        )
    return rows
