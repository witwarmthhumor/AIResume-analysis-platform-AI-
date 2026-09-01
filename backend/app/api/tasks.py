"""异步任务接口：提交任务并查询 Celery 状态。"""

from celery.result import AsyncResult
from fastapi import APIRouter

from app.worker.celery_app import celery_app
from app.worker.tasks import health_check

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("/health-check")
def submit_health_check() -> dict[str, str]:
    task = health_check.delay()
    return {"task_id": task.id, "status": "pending"}


@router.get("/{task_id}")
def get_task_status(task_id: str) -> dict:
    result = AsyncResult(task_id, app=celery_app)
    payload: dict = {"task_id": task_id, "status": result.status.lower()}
    if result.successful():
        payload["result"] = result.result
    elif result.failed():
        payload["error"] = "异步任务执行失败"
    return payload
