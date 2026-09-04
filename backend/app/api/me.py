"""个人中心（v3.4）：当前登录用户"本人"的数据概览。挂 /api/me 前缀，全部强制登录。

与管理端 /api/admin/* 的区别：admin 看全站、需 admin 角色；这里每一项都按 user.id 过滤，
只返回本人数据，普通登录用户即可访问。匿名阶段产生的数据无 user_id，不计入个人中心。
- /stats：五张卡（我的简历/分析/面试/今日 Token/累计 Token）
- /usage：本人近 7 日每日调用次数与 token（供个人中心柱状图）
使用日志明细分页直接复用 /api/usage/logs（本就按当前用户过滤），不重复实现。
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.db.session import get_db
from app.models.analysis import Analysis
from app.models.interview import InterviewSession
from app.models.resume import Resume
from app.models.usage_log import UsageLog
from app.models.user import User

router = APIRouter(prefix="/api/me", tags=["me"])


def _today_start() -> datetime:
    return (
        datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    )


@router.get("/stats")
def my_stats(
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """本人五项计数；口径与 admin/stats 对齐，仅多一层 user_id 过滤。"""
    today_start = _today_start()

    def _count(model) -> int:
        return int(
            db.scalar(
                select(func.count()).select_from(model).where(model.user_id == user.id)
            )
            or 0
        )

    tokens_today = (
        db.scalar(
            select(func.coalesce(func.sum(UsageLog.tokens_total), 0)).where(
                UsageLog.user_id == user.id, UsageLog.created_at >= today_start
            )
        )
        or 0
    )
    tokens_total = (
        db.scalar(
            select(func.coalesce(func.sum(UsageLog.tokens_total), 0)).where(
                UsageLog.user_id == user.id
            )
        )
        or 0
    )

    return {
        "resumes": _count(Resume),
        "analyses": _count(Analysis),
        "interviews": _count(InterviewSession),
        "tokens_today": int(tokens_today),
        "tokens_total": int(tokens_total),
    }


@router.get("/usage")
def my_usage(
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> list[dict]:
    """本人近 7 日每日 token 消耗与调用次数（日期升序，供柱状图）。"""
    today_start = _today_start()
    rows = []
    for i in range(6, -1, -1):
        day = today_start - timedelta(days=i)
        next_day = day + timedelta(days=1)
        base = [
            UsageLog.user_id == user.id,
            UsageLog.created_at >= day,
            UsageLog.created_at < next_day,
        ]
        tokens = (
            db.scalar(
                select(func.coalesce(func.sum(UsageLog.tokens_total), 0)).where(*base)
            )
            or 0
        )
        count = db.scalar(select(func.count()).select_from(UsageLog).where(*base)) or 0
        rows.append(
            {
                "date": day.strftime("%Y-%m-%d"),
                "calls": int(count),
                "tokens": int(tokens),
            }
        )
    return rows
