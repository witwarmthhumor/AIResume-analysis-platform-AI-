"""Celery 任务入口：阶段4先提供可验证的健康任务。"""

from app.worker.celery_app import celery_app


@celery_app.task(name="app.worker.tasks.health_check")
def health_check() -> dict[str, str]:
    """用于联调 broker/result backend 的最小任务。"""
    return {"status": "ok"}
