"""统一错误体系：自定义异常类 + 全局处理器（P2 清单项）。

所有接口错误输出统一格式 {"code": "xxx", "message": "xxx", "details": null}。
成功响应结构不动。
"""

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request


class AppError(Exception):
    """应用异常基类。子类设置 code/status/message，全局处理器统一输出。"""

    code: str = "internal_error"
    status: int = 500
    message: str = "服务器内部错误"

    def __init__(self, message: str | None = None, details: object = None) -> None:
        if message:
            self.message = message
        self.details = details


class ValidationError(AppError):
    code = "validation_error"
    status = 400
    message = "请求参数校验失败"


class AuthenticationError(AppError):
    code = "authentication_error"
    status = 401
    message = "未登录或登录已失效"


class NotFoundError(AppError):
    code = "not_found"
    status = 404
    message = "资源不存在"


class RateLimitError(AppError):
    code = "rate_limit"
    status = 429
    message = "请求次数超限"


class AIRequestError(AppError):
    code = "ai_request_error"
    status = 502
    message = "AI 服务暂时不可用"


def _error_body(code: str, message: str, details: object = None) -> dict:
    return {"code": code, "message": message, "details": details}


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    from app.core.logging import get_logger  # 延迟导入避免循环依赖

    get_logger(__name__).warning("AppError %s %s %s", exc.status, exc.code, exc.message)
    return JSONResponse(
        status_code=exc.status,
        content=_error_body(exc.code, exc.message, getattr(exc, "details", None)),
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code = {
        400: "bad_request",
        401: "unauthenticated",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "payload_too_large",
        415: "unsupported_media_type",
        422: "validation_error",
        429: "rate_limit",
        502: "ai_error",
        503: "service_unavailable",
    }.get(exc.status_code, "http_error")
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(code, detail),
    )


async def validation_handler(request: Request, exc: Exception) -> JSONResponse:
    """RequestValidationError：把字段错误列表压缩成一行友好话术。"""
    from fastapi.exceptions import RequestValidationError

    if isinstance(exc, RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
        msg = first.get("msg", "参数校验失败")
        text = f"{loc}: {msg}" if loc else msg
        return JSONResponse(
            status_code=422,
            content=_error_body("validation_error", text, details=exc.errors()),
        )
    return await unhandled_handler(request, exc)


async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    from app.core.logging import get_logger  # 延迟导入避免循环依赖

    get_logger(__name__).exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content=_error_body("internal_error", "服务器内部错误"),
    )


async def database_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """数据库连接类异常（sqlalchemy OperationalError / InterfaceError）统一转 503。

    只输出固定中文话术：不泄露连接串、用户名、密码或 Python 堆栈；
    详细原因进服务端日志，由 unhandled 之外的这里单独记录。
    """
    from app.core.logging import get_logger  # 延迟导入避免循环依赖

    get_logger(__name__).error(
        "Database unavailable: %s %s -> %s",
        request.method,
        request.url.path,
        exc.__class__.__name__,
    )
    return JSONResponse(
        status_code=503,
        content=_error_body(
            "database_unavailable",
            "数据库暂时不可用，请稍后重试；若长时间无响应请联系管理员",
        ),
    )
