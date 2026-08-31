"""公共依赖：匿名身份与每日用量统计（PROJECT-PLAN §4 限流方案）。

V1 无登录：首次访问下发匿名 cookie（uuid），作为 usage_logs 的归属标识；
阶段4 接登录后同一套表按 user_id 统计，逻辑不变。
"""

import uuid
from datetime import datetime

from fastapi import Cookie, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.usage_log import UsageLog

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


def count_today_analysis(db: Session, anonymous_id: str) -> int:
    """该匿名身份今日（本地时区 0 点起）已发起的分析次数。"""
    today_start = (
        datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    )
    return int(
        db.scalar(
            select(func.count())
            .select_from(UsageLog)
            .where(
                UsageLog.anonymous_id == anonymous_id,
                UsageLog.action_type == "analysis",
                UsageLog.created_at >= today_start,
            )
        )
        or 0
    )


def enforce_daily_limit(db: Session, anonymous_id: str, limit: int) -> None:
    """超每日上限 → 429，话术含恢复时间（PROJECT-PLAN 验收要求"明确提示"）。"""
    used = count_today_analysis(db, anonymous_id)
    if used >= limit:
        raise HTTPException(
            429,
            f"今日 AI 分析次数已用完（每日上限 {limit} 次），次日 0 点自动恢复",
        )
