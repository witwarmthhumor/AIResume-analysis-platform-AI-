"""异步任务接口：提交任务并查询 Celery 状态。"""

from fastapi import APIRouter, Depends

from app.api.auth_deps import get_current_user
from app.models.user import User
from app.services.task_service import task_status
from app.worker.tasks import analyze_resume, health_check, parse_resume

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("/health-check")
def submit_health_check() -> dict[str, str]:
    task = health_check.delay()
    return {"task_id": task.id, "status": "pending"}


@router.post("/parse-resume/{resume_id}")
def submit_parse_resume(
    resume_id: int, user: User = Depends(get_current_user)  # noqa: B008
) -> dict[str, str]:
    task = parse_resume.delay(resume_id)
    return {"task_id": task.id, "status": "pending"}


@router.post("/analyze-resume/{resume_id}")
def submit_analyze_resume(
    resume_id: int, user: User = Depends(get_current_user)  # noqa: B008
) -> dict[str, str]:
    task = analyze_resume.delay(resume_id)
    return {"task_id": task.id, "status": "pending"}


@router.get("/{task_id}")
def get_task_status(task_id: str) -> dict:
    return task_status(task_id)
