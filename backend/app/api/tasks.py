"""异步任务接口：提交任务并查询 Celery 状态。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.db.session import get_db
from app.models.resume import Resume
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
    resume_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, str]:
    resume = db.get(Resume, resume_id)
    if resume is None or resume.user_id != user.id:
        raise HTTPException(404, "简历记录不存在或已删除")
    task = parse_resume.delay(resume_id)
    return {"task_id": task.id, "status": "pending"}


@router.post("/analyze-resume/{resume_id}")
def submit_analyze_resume(
    resume_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, str]:
    resume = db.get(Resume, resume_id)
    if resume is None or resume.user_id != user.id:
        raise HTTPException(404, "简历记录不存在或已删除")
    task = analyze_resume.delay(resume_id)
    return {"task_id": task.id, "status": "pending"}


@router.get("/{task_id}")
def get_task_status(
    task_id: str,
    user: User = Depends(get_current_user),  # noqa: B008  任务结果需登录才能查
) -> dict:
    return task_status(task_id)
