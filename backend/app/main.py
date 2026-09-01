"""FastAPI 应用入口。

业务路由从阶段1起挂 /api 前缀（app/api）；/health 是基础设施检查，不带前缀。
"""

from fastapi import FastAPI

from app.api.analyses import router as analyses_router
from app.api.auth import router as auth_router
from app.api.interviews import router as interviews_router
from app.api.resumes import router as resumes_router
from app.core.config import settings
from app.db.session import ping_database

app = FastAPI(title="AI 简历分析与模拟面试 API", version=settings.app_version)

app.include_router(resumes_router)
app.include_router(analyses_router)
app.include_router(interviews_router)
app.include_router(auth_router)


@app.get("/health")
def health() -> dict[str, str]:
    """健康检查：永远返回 200；数据库不通时 status 降级为 degraded。"""
    db_status = ping_database()
    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "database": db_status,
        "version": settings.app_version,
    }
