"""入口强制登录闸门（v4.1 企业级改造·A3，方案 §3.4）。

实现为 **ASGI 中间件**（评审后的最终裁定）：
- 中间件先于路由执行，连「不存在的 /api 路径」也返回 401 而非 404——不向
  未登录探测者泄露路径存在性（方案 A3 验收原文要求）；
- 可测化开关：读 `settings.auth_gate_enabled`（请求时读取）——生产恒开；
  测试由 conftest 默认关闭以保留匿名业务流用例（owner_clause 语义照常覆盖），
  闸门行为在 tests/test_auth_gate.py 里显式打开后真身验证。

白名单精确匹配：仅注册与登录两个匿名入口；/health 在 /api 前缀之外天然放行；
OPTIONS 预检不携带 cookie，不能拦。
"""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from app.api.auth_deps import ACCESS_COOKIE
from app.core.config import settings

logger = logging.getLogger(__name__)

_WHITELIST_EXACT = {"/api/auth/login", "/api/auth/register"}


async def auth_gate_middleware(request: Request, call_next):
    """默认拒绝：无 access_token cookie 的 /api/** 请求一律 401。"""
    path = request.url.path
    if (
        settings.auth_gate_enabled
        and path.startswith("/api/")
        and request.method != "OPTIONS"
        and path not in _WHITELIST_EXACT
        and not request.cookies.get(ACCESS_COOKIE)
    ):
        # 错误体与全局错误体系同形（{"code","message"}），前端按同一套逻辑提示
        return JSONResponse(
            status_code=401,
            content={"code": "unauthorized", "message": "请先登录", "details": None},
        )
    return await call_next(request)
