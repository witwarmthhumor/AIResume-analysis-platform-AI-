"""FastAPI 应用入口。

业务路由从阶段1起挂 /api 前缀（app/api）；/health 是基础设施检查，不带前缀。
"""

from fastapi import FastAPI
from redis import Redis

from app.api.analyses import router as analyses_router
from app.api.auth import router as auth_router
from app.api.history import router as history_router
from app.api.interviews import router as interviews_router
from app.api.resumes import router as resumes_router
from app.api.tasks import router as tasks_router
from app.core.config import settings
from app.db.session import ping_database

app = FastAPI(title="AI 简历分析与模拟面试 API", version=settings.app_version)

app.include_router(resumes_router)
app.include_router(analyses_router)
app.include_router(interviews_router)
app.include_router(auth_router)
app.include_router(history_router)
app.include_router(tasks_router)


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
