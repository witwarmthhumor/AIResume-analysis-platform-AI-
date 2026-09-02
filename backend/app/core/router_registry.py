"""RouterRegistry —— 动态路由注册（P4 清单项）。

自动发现 app/api 目录下所有 APIRouter 实例并注册到 FastAPI 应用，
main.py 只保留两行调用，不再手动 import/include_router。
"""

import importlib
import pkgutil
from types import ModuleType

from fastapi import APIRouter, FastAPI


def _iter_router_modules() -> list[ModuleType]:
    """遍历 app/api 包内所有模块，返回含 APIRouter 实例的模块。"""
    import app.api  # 延迟导入确保包已加载

    modules: list[ModuleType] = []
    for importer, modname, ispkg in pkgutil.iter_modules(app.api.__path__):
        if ispkg or modname.startswith("_"):
            continue
        mod = importlib.import_module(f"app.api.{modname}")
        if hasattr(mod, "router") and isinstance(mod.router, APIRouter):
            modules.append(mod)
    return modules


def register_all_routers(app: FastAPI) -> None:
    """自动发现并注册所有路由模块。"""
    for mod in _iter_router_modules():
        app.include_router(mod.router)
