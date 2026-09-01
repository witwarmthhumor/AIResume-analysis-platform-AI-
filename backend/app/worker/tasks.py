"""Celery 任务入口：阶段4提供基础任务与业务任务适配点。"""

from app.worker.celery_app import celery_app


@celery_app.task(name="app.worker.tasks.health_check")
def health_check() -> dict[str, str]:
    """用于联调 broker/result backend 的最小任务。"""
    return {"status": "ok"}


@celery_app.task(name="app.worker.tasks.parse_resume")
def parse_resume(resume_id: int) -> dict[str, int | str]:
    """解析任务入口；实际持久化由后续任务编排接入，先返回可查询的任务结果。"""
    return {"resume_id": resume_id, "status": "parsed"}


@celery_app.task(name="app.worker.tasks.analyze_resume")
def analyze_resume(resume_id: int) -> dict[str, int | str]:
    """分析任务入口；保留原同步分析接口，便于渐进迁移到 worker。"""
    return {"resume_id": resume_id, "status": "analyzed"}
