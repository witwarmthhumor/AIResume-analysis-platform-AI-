"""Celery 应用：Redis 作为 broker 与结果后端。"""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "ai_interview",
    broker=settings.redis_url,
    backend=settings.celery_result_url,
    include=["app.worker.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)
