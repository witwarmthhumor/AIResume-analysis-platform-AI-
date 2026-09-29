"""登录失败锁定（v4.1 A2）：Redis 集中计数，多 worker 部署语义正确。

fail-open 设计（评审 P2-5 裁定）：Redis 不可用时**放行登录**并记日志——
把 Redis 变成登录链路的硬依赖（fail-closed）会让缓存故障演变成全站不可登录，
两者取其轻。计数键 `login:fail:{ip}:{identifier}`，窗口由调用方传
settings.login_lockout_minutes * 60，INCR + EXPIRE 原子续窗。
"""

import logging

from redis import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "login:fail:"


def _client() -> Redis:
    """每次操作新建连接：登录是低频路径，不值得维护连接池生命周期。"""
    return Redis.from_url(
        settings.redis_url, socket_connect_timeout=1, socket_timeout=1
    )


def failure_count(key: str) -> int:
    """窗口内失败次数；Redis 异常 fail-open 返回 0（放行）。"""
    try:
        value = _client().get(f"{_KEY_PREFIX}{key}")
        return int(value or 0)
    except Exception:
        logger.warning("登录锁定计数读取失败，fail-open 放行", exc_info=True)
        return 0


def record_failure(key: str, window_seconds: int) -> None:
    """记一次失败并续窗；Redis 异常只记日志（下次成功登录前的失败仍会被正常计数）。"""
    try:
        client = _client()
        full = f"{_KEY_PREFIX}{key}"
        pipe = client.pipeline()
        pipe.incr(full)
        pipe.expire(full, window_seconds)
        pipe.execute()
    except Exception:
        logger.warning("登录锁定计数写入失败（fail-open）", exc_info=True)


def reset_failures(key: str) -> None:
    """登录成功清空该组合的失败记录；Redis 异常不影响登录结果。"""
    try:
        _client().delete(f"{_KEY_PREFIX}{key}")
    except Exception:
        logger.warning("登录锁定计数清理失败", exc_info=True)
