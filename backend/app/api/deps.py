"""公共依赖：匿名身份、归属过滤统一口径与每日用量统计（PROJECT-PLAN §4 限流方案）。

首次访问下发匿名 cookie（uuid）作为用量归属；限流计数按「登录 user_id 优先，
否则匿名 cookie」聚合（enforce_daily_limit 支持传 user_id，各接口应传入以防
清 cookie 重置限额）。计数逻辑在 services/usage_service.py，记账由各接口在
成功/失败路径内联写 usage_logs（write_usage 供其复用）。
"""

import uuid
from typing import Any

from fastapi import Cookie, HTTPException, Response
from sqlalchemy.orm import Session

from app.services.usage_service import acquire_limit_lock, count_today_usage_by_owner

ANONYMOUS_COOKIE = "anonymous_id"
_COOKIE_MAX_AGE = 365 * 24 * 3600  # 一年，浏览器重装/清 cookie 后视为新用户


def get_anonymous_id(
    response: Response,
    anonymous_id: str | None = Cookie(default=None, alias=ANONYMOUS_COOKIE),
) -> str:
    """取匿名身份；没有则现发一个并写入响应 cookie（httponly，前端脚本读不到）。"""
    if anonymous_id:
        return anonymous_id
    new_id = uuid.uuid4().hex
    response.set_cookie(
        ANONYMOUS_COOKIE, new_id, max_age=_COOKIE_MAX_AGE, httponly=True, samesite="lax"
    )
    return new_id


def owner_clause(model: Any, user: Any, anonymous_id: str | None):
    """归属过滤统一口径（防匿名横向越权），所有带 user_id/anonymous_id 两列的表通用。

    登录按 user_id；匿名必须**同时**满足 user_id IS NULL 且 anonymous_id 相等——
    只按 anonymous_id 相等会把历史"双空"记录（两列均为空的遗留/异常数据）
    错配给任意匿名访客。单对象校验请配合 _matches_owner 使用。
    """
    if user is not None:
        return model.user_id == user.id
    return model.user_id.is_(None) & (model.anonymous_id == anonymous_id)


def matches_owner(
    record: Any, user: Any, anonymous_id: str | None, user_id: int | None = None
) -> bool:
    """单对象归属校验，与 owner_clause 同口径：登录看 user_id，匿名要求双条件。

    user_id 参数用于登录场景下直接传已取出的 id（record.user_id 与之比较）。
    """
    if user is not None:
        return record.user_id == user.id
    return record.user_id is None and record.anonymous_id == anonymous_id


def enforce_daily_limit(
    db: Session,
    anonymous_id: str,
    limit: int,
    action_type: str = "analysis",
    user_id: int | None = None,
) -> None:
    """超每日上限 → 429，话术含恢复时间（PROJECT-PLAN 验收要求"明确提示"）。

    传 user_id 时按登录身份计数（kb 上传等需要跨 cookie 生效的配额）；
    不传则按匿名 cookie 计数。
    """
    # 原子化：先抢事务级咨询锁，把同归属者的 count→执行→记账 串行化（防并发超限）
    acquire_limit_lock(db, action_type, user_id, anonymous_id)
    used = count_today_usage_by_owner(
        db, action_type, user_id=user_id, anonymous_id=anonymous_id
    )
    if used >= limit:
        raise HTTPException(
            429,
            f"今日次数已用完（该功能每日上限 {limit} 次），次日 0 点自动恢复",
        )
