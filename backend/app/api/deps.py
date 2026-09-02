"""公共依赖：匿名身份与每日用量统计（PROJECT-PLAN §4 限流方案）。

首次访问下发匿名 cookie（uuid）作为用量归属；限流计数目前按该 cookie 聚合
（登录用户的 user_id 也会写入 usage_logs，但 enforce_daily_limit 不按它查，
清 cookie 即重置限额——已知取舍）。统计与记账逻辑在 services/usage_service.py（P4）。
"""

import uuid

from fastapi import Cookie, HTTPException, Response
from sqlalchemy.orm import Session

from app.services.usage_service import count_today_usage

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


def enforce_daily_limit(
    db: Session, anonymous_id: str, limit: int, action_type: str = "analysis"
) -> None:
    """超每日上限 → 429，话术含恢复时间（PROJECT-PLAN 验收要求"明确提示"）。"""
    used = count_today_usage(db, anonymous_id, action_type)
    if used >= limit:
        raise HTTPException(
            429,
            f"今日次数已用完（该功能每日上限 {limit} 次），次日 0 点自动恢复",
        )
