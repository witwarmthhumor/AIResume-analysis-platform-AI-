"""当前登录用户的使用日志明细（v3.3）：后端分页 + 多条件筛选。

只返回当前登录用户（按 user_id）的 usage_logs，匿名日志不在此展示。
原 /api/history 摘要接口保留不动。
"""

from datetime import datetime, time

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.db.session import get_db
from app.models.usage_log import UsageLog
from app.models.user import User

router = APIRouter(prefix="/api/usage", tags=["usage"])


def _parse_day(value: str, *, end_of_day: bool = False) -> datetime | None:
    """YYYY-MM-DD → 本地时区 datetime；end_of_day=True 取当天 23:59:59.999999。

    非法日期返回 None（忽略该筛选条件，不抛 422，保持筛选容错）。
    """
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    clock = time.max if end_of_day else time.min
    return datetime.combine(day, clock).astimezone()


@router.get("/logs")
def list_usage_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    action_type: str | None = Query(default=None),
    model_name: str | None = Query(default=None),
    ip_address: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    conditions = [UsageLog.user_id == user.id]

    if action_type and action_type.strip():
        conditions.append(UsageLog.action_type == action_type.strip())
    if model_name and model_name.strip():
        conditions.append(UsageLog.model_name.ilike(f"%{model_name.strip()}%"))
    if ip_address and ip_address.strip():
        conditions.append(UsageLog.ip_address == ip_address.strip())

    start_dt = _parse_day(start_date) if start_date else None
    if start_dt is not None:
        conditions.append(UsageLog.created_at >= start_dt)
    end_dt = _parse_day(end_date, end_of_day=True) if end_date else None
    if end_dt is not None:
        conditions.append(UsageLog.created_at <= end_dt)

    total = int(
        db.scalar(select(func.count()).select_from(UsageLog).where(*conditions)) or 0
    )

    rows = list(
        db.scalars(
            select(UsageLog)
            .where(*conditions)
            .order_by(UsageLog.created_at.desc(), UsageLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )

    return {
        "items": [
            {
                "id": row.id,
                "action_type": row.action_type,
                "model_name": row.model_name,
                "tokens_total": row.tokens_total or 0,
                "ip_address": row.ip_address,
                "created_at": row.created_at,
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
