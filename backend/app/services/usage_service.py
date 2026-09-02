"""用量统计服务（P4 服务层下沉）。封装限流与记账逻辑，供 api 层调用。"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.usage_log import UsageLog


def count_today_usage(db: Session, anonymous_id: str, action_type: str) -> int:
    """该匿名身份当日某类动作的次数。"""
    today_start = (
        datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    )
    return int(
        db.scalar(
            select(func.count())
            .select_from(UsageLog)
            .where(
                UsageLog.anonymous_id == anonymous_id,
                UsageLog.action_type == action_type,
                UsageLog.created_at >= today_start,
            )
        )
        or 0
    )


def write_usage(
    db: Session,
    anonymous_id: str | None,
    user_id: int | None,
    action_type: str,
    model_name: str | None,
    tokens_total: int | None,
    ip_address: str | None,
) -> None:
    """记一条用量日志，用于限流聚合与 token 统计。"""
    db.add(
        UsageLog(
            user_id=user_id,
            anonymous_id=anonymous_id,
            action_type=action_type,
            model_name=model_name,
            tokens_total=tokens_total,
            ip_address=ip_address,
        )
    )
