"""FastAPI 应用入口。

业务路由从阶段1起挂 /api 前缀（app/api）；/health 是基础设施检查，不带前缀。
"""

import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from redis import Redis
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.errors import (
    AppError,
    app_error_handler,
    http_exception_handler,
    unhandled_handler,
    validation_handler,
)
from app.core.logging import get_logger, setup_logging
from app.core.router_registry import register_all_routers
from app.db.session import ping_database

setup_logging(settings.log_level, settings.log_file or None)
logger = get_logger(__name__)

# 弱默认密钥告警（铁律：Key 只放 .env）：.env 漏配 JWT_SECRET_KEY 时 token 可被伪造
if settings.jwt_secret_key == "change-me-in-backend-env":
    logger.warning(
        "JWT_SECRET_KEY 仍是默认占位值，登录凭证可被伪造——请在 backend/.env 设置随机密钥"
    )

app = FastAPI(title="AI 简历分析与模拟面试 API", version=settings.app_version)

register_all_routers(app)  # RouterRegistry 自动注册 app/api 下全部路由

# —— 统一错误体系（P2）：所有错误输出 {"code","message","details"} ——
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_handler)
app.add_exception_handler(Exception, unhandled_handler)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """请求日志：记录方法/路径/耗时/状态码，异常时记 ERROR（不记请求体与密钥）。"""
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request failed: %s %s", request.method, request.url.path)
        raise
    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "%s %s -> %s (%dms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


@app.get("/health")
def health() -> dict[str, str]:
    """基础健康检查：永远返回 200，便于查看降级状态。"""
    db_status = ping_database()
    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "database": db_status,
        "version": settings.app_version,
    }


@app.get("/health/ready")
def readiness() -> dict[str, str]:
    """容器就绪探针：数据库和 Redis 都通才返回 200。"""
    db_status = ping_database()
    redis_status = "connected"
    try:
        Redis.from_url(settings.redis_url, socket_connect_timeout=1).ping()
    except Exception:  # noqa: BLE001  readiness 只返回状态，不把基础设施异常抛给探针
        redis_status = "disconnected"
    if db_status != "connected" or redis_status != "connected":
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail={"database": db_status, "redis": redis_status},
        )
    return {"status": "ready", "database": db_status, "redis": redis_status}
