"""用量统计服务（P4 服务层下沉）。封装限流与记账逻辑，供 api 层调用。"""

from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.usage_log import UsageLog


def acquire_limit_lock(
    db: Session, action_type: str, user_id: int | None, anonymous_id: str | None
) -> None:
    """当日限额检查的原子化前置：取事务级咨询锁（PG 专属，随事务结束自动释放）。

    限额是"先 count 后执行后记账"的三段流程，并发请求会在 count 与记账之间
    互相看不到对方（TOCTOU）从而突破每日上限。同归属者同动作先抢到这把锁，
    后续请求的 count 会阻塞到前者记账提交之后，串行化整个窗口。
    """
    key = f"{action_type}:{user_id if user_id is not None else anonymous_id}"
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k)::bigint)"), {"k": key})


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


def count_today_usage_by_owner(
    db: Session,
    action_type: str,
    user_id: int | None = None,
    anonymous_id: str | None = None,
) -> int:
    """按归属者统计当日次数：登录用户按 user_id（跨设备/清 cookie 也有效），
    匿名用户按 anonymous_id。两者都为 None 时计 0（不应发生，防御性返回）。"""
    if user_id is None and anonymous_id is None:
        return 0
    today_start = (
        datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    )
    owner_cond = (
        UsageLog.user_id == user_id
        if user_id is not None
        else UsageLog.anonymous_id == anonymous_id
    )
    return int(
        db.scalar(
            select(func.count())
            .select_from(UsageLog)
            .where(
                owner_cond,
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
