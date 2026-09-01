"""当前登录用户的历史记录摘要。"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.db.session import get_db
from app.models.analysis import Analysis
from app.models.interview import InterviewSession
from app.models.resume import Resume
from app.models.user import User

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("")
def get_history(
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    resumes = list(
        db.scalars(
            select(Resume)
            .where(Resume.user_id == user.id, Resume.deleted_at.is_(None))
            .order_by(Resume.created_at.desc(), Resume.id.desc())
        )
    )
    resume_ids = [item.id for item in resumes]
    analyses = list(
        db.scalars(
            select(Analysis)
            .where(Analysis.user_id == user.id)
            .order_by(Analysis.created_at.desc(), Analysis.id.desc())
        )
    )
    interviews = list(
        db.scalars(
            select(InterviewSession)
            .where(InterviewSession.user_id == user.id)
            .order_by(InterviewSession.created_at.desc(), InterviewSession.id.desc())
        )
    )
    return {
        "resumes": [
            {
                "id": item.id,
                "filename": item.filename,
                "parse_status": item.parse_status,
                "created_at": item.created_at,
            }
            for item in resumes
        ],
        "analyses": [
            {
                "id": item.id,
                "resume_id": item.resume_id,
                "model_name": item.model_name,
                "created_at": item.created_at,
            }
            for item in analyses
            if item.resume_id in resume_ids
        ],
        "interviews": [
            {
                "id": item.id,
                "resume_id": item.resume_id,
                "status": item.status,
                "stage": item.stage,
                "turn_count": item.turn_count,
                "created_at": item.created_at,
            }
            for item in interviews
            if item.resume_id in resume_ids
        ],
    }
