"""FastAPI 应用入口。

业务路由从阶段1起挂 /api 前缀（app/api）；/health 是基础设施检查，不带前缀。
"""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from redis import Redis
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.gate import auth_gate_middleware
from app.core.config import Settings, settings
from app.core.errors import (
    AppError,
    app_error_handler,
    database_error_handler,
    http_exception_handler,
    unhandled_handler,
    validation_handler,
)
from app.core.logging import get_logger, setup_logging
from app.core.router_registry import register_all_routers
from app.db.session import ping_database

setup_logging(settings.log_level, settings.log_file or None)
logger = get_logger(__name__)

# 弱默认密钥（铁律：Key 只放 .env）：.env 漏配 JWT_SECRET_KEY 时 token 可被伪造。
# dev 只告警（方便本地起服务）；prod 直接拒绝启动——宁可起不来也不能带弱密钥上线
if settings.jwt_secret_key == "change-me-in-backend-env":
    if settings.app_env == "prod":
        raise RuntimeError(
            "APP_ENV=prod 但 JWT_SECRET_KEY 仍是默认占位值——"
            "请在 backend/.env 设置随机密钥后再启动"
        )
    logger.warning(
        "JWT_SECRET_KEY 仍是默认占位值，登录凭证可被伪造——请在 backend/.env 设置随机密钥"
    )


def validate_prod_settings(s: Settings) -> None:
    """prod 启动断言（v4.4.1 安全件）：安全关键配置自相矛盾时拒绝启动。

    宁可服务起不来，也不能带着"HTTPS 下不设 Secure Cookie / 关登录闸门 /
    首用户自动提权"这类裸奔配置上线。仅 app_env=prod 生效（dev 本地 HTTP 是正常态）。
    """
    if s.app_env != "prod":
        return
    problems: list[str] = []
    if not s.jwt_secure_cookie:
        problems.append("jwt_secure_cookie 必须为 True（生产 HTTPS）")
    if not s.auth_gate_enabled:
        problems.append("auth_gate_enabled 必须为 True（/api/** 默认拒绝）")
    if s.auto_promote_first_user:
        problems.append(
            "auto_promote_first_user 必须关闭（管理员唯一来源 scripts/seed_admin.py）"
        )
    if problems:
        raise RuntimeError("prod 配置校验失败：" + "；".join(problems))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动钩子：prod 配置断言 + 回收上个进程遗留的孤儿 agent run。"""
    validate_prod_settings(settings)
    from app.services.agent_v2.graph import reap_orphan_runs

    try:
        reap_orphan_runs()
    except Exception:  # 回收失败不能拖垮服务启动（如启动时 DB 尚未就绪）
        logger.warning("启动回收孤儿 agent run 失败", exc_info=True)
    yield


app = FastAPI(
    title="AI 简历分析与模拟面试 API", version=settings.app_version, lifespan=lifespan
)

register_all_routers(app)  # RouterRegistry 自动注册 app/api 下全部路由

# —— v4.1 A3 强制登录闸门（/api/** 默认拒绝，白名单见 app/api/gate.py）——
# 先于 log_requests 注册：日志中间件在最外层，401 拒绝也会留下访问日志（可审计）
app.middleware("http")(auth_gate_middleware)

# —— 统一错误体系（P2）：所有错误输出 {"code","message","details"} ——
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_handler)
# v3.6：数据库连接类异常（建连失败/连接断开）统一 503，不让请求挂起或吐 500 堆栈
app.add_exception_handler(OperationalError, database_error_handler)
app.add_exception_handler(InterfaceError, database_error_handler)
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
