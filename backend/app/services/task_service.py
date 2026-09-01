"""Celery 任务薄封装：路由只负责提交/查状态，不直接依赖 Celery 细节。"""

from celery.result import AsyncResult

from app.worker.celery_app import celery_app


def task_status(task_id: str) -> dict:
    result = AsyncResult(task_id, app=celery_app)
    payload = {"task_id": task_id, "status": result.status.lower()}
    if result.successful():
        payload["result"] = result.result
    elif result.failed():
        payload["error"] = "异步任务执行失败"
    return payload
