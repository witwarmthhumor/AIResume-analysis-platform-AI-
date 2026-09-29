"""录音分析接口（v4.2 B5）：上传转写 / 历史 / 编辑文本 / 角色审核 / 面试审核。

挂 /api/audio 前缀；强制登录闸门覆盖。转写走 Celery 异步（CPU 分钟级），
前端轮询 GET /{id} 看 status；两段审核为同步单次 LLM 调用。
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import enforce_daily_limit, get_anonymous_id
from app.core.config import settings
from app.db.session import get_db
from app.models.audio_analysis import AudioAnalysis
from app.models.usage_log import UsageLog
from app.models.user import User
from app.services import audio_review_service
from app.worker.tasks import transcribe_audio

router = APIRouter(prefix="/api/audio", tags=["audio"])

# 音频扩展名白名单（faster-whisper 经 PyAV 解码，容器格式不限于此六种但收紧面）
_AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a", ".webm", ".flac", ".ogg")
AUDIO_UPLOAD_DIR = Path(settings.upload_dir) / "audio"
AUDIO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _owned_audio(db: Session, user: User, audio_id: int) -> AudioAnalysis:
    """归属校验：不存在/不属于本人统一 404（防存在性泄露）。"""
    audio = db.get(AudioAnalysis, audio_id)
    if audio is None or audio.user_id != user.id:
        raise HTTPException(404, "录音记录不存在或已删除")
    return audio


@router.post("/analyses", status_code=201)
async def upload_audio(
    file: UploadFile,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """上传录音 → 建 transcribing 记录 → 触发 Celery 转写任务。"""
    filename = Path(file.filename or "audio.wav").name
    if not filename.lower().endswith(_AUDIO_EXTENSIONS):
        raise HTTPException(415, "仅支持 wav / mp3 / m4a / webm / flac / ogg 音频")
    if file.size is not None and file.size > settings.audio_upload_max_size:
        raise HTTPException(
            413, f"音频超过 {settings.audio_upload_max_size // (1024 * 1024)}MB 限制"
        )

    enforce_daily_limit(
        db,
        anonymous_id,
        settings.daily_audio_transcribe_limit,
        "audio_transcribe",
        user_id=user.id,
    )

    data = await file.read()
    import hashlib

    file_hash = hashlib.sha256(data).hexdigest()
    storage_path = AUDIO_UPLOAD_DIR / f"{file_hash}{Path(filename).suffix.lower()}"
    storage_path.write_bytes(data)

    audio = AudioAnalysis(
        user_id=user.id,
        filename=filename,
        storage_path=str(storage_path),
        file_size=len(data),
        status="transcribing",
    )
    db.add(audio)
    db.commit()
    db.refresh(audio)
    db.add(
        UsageLog(
            user_id=user.id,
            anonymous_id=None,
            action_type="audio_transcribe",
            model_name=f"whisper:{settings.asr_whisper_model}",
            tokens_total=None,
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    task = transcribe_audio.delay(audio.id)  # Celery 异步：CPU 转写分钟级，不挂请求
    return {
        "id": audio.id,
        "filename": audio.filename,
        "status": audio.status,
        "task_id": task.id,
    }


@router.get("/analyses")
def list_audios(
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> list[dict]:
    audios = db.scalars(
        select(AudioAnalysis)
        .where(AudioAnalysis.user_id == user.id)
        .order_by(AudioAnalysis.created_at.desc(), AudioAnalysis.id.desc())
    ).all()
    return [
        {
            "id": a.id,
            "filename": a.filename,
            "status": a.status,
            "duration_seconds": a.duration_seconds,
            "has_role_review": a.role_review_json is not None,
            "has_interview_review": a.interview_review_json is not None,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in audios
    ]


@router.get("/analyses/{audio_id}")
def get_audio(
    audio_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    audio = _owned_audio(db, user, audio_id)
    return {
        "id": audio.id,
        "filename": audio.filename,
        "status": audio.status,
        "duration_seconds": audio.duration_seconds,
        "transcript": audio.transcript,
        "role_review": audio.role_review_json,
        "interview_review": audio.interview_review_json,
        "error": audio.error,
        "created_at": audio.created_at.isoformat() if audio.created_at else None,
    }


class TranscriptUpdate(BaseModel):
    transcript: str = Field(min_length=1, max_length=50000)


@router.put("/analyses/{audio_id}/transcript")
def update_transcript(
    audio_id: int,
    body: TranscriptUpdate,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """编辑转写文本（参考页面"可编辑"）：改文本即作废既有审核结果。"""
    audio = _owned_audio(db, user, audio_id)
    if audio.status not in ("transcribed", "reviewed"):
        raise HTTPException(400, "转写尚未完成（或失败），无可编辑文本")
    audio.transcript = body.transcript.strip()
    audio.role_review_json = None  # 文本变了，旧审核不再可信
    audio.interview_review_json = None
    audio.status = "transcribed"
    db.commit()
    return {"id": audio.id, "status": audio.status, "transcript": audio.transcript}


@router.post("/analyses/{audio_id}/role-review")
def run_role_review(
    audio_id: int,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """角色审核：转写文本 → 按面试官/候选人分段标注（同步单次 LLM 调用）。"""
    audio = _owned_audio(db, user, audio_id)
    if not (audio.transcript or "").strip():
        raise HTTPException(400, "还没有可审核的转写文本")
    enforce_daily_limit(
        db,
        anonymous_id,
        settings.daily_audio_review_limit,
        "audio_review",
        user_id=user.id,
    )
    try:
        report, result = audio_review_service.role_review(audio.transcript)
    except Exception as exc:
        raise HTTPException(502, "审核失败，AI 服务暂时不可用，请稍后重试") from exc
    audio.role_review_json = report
    audio.model_name = result.model_name
    db.add(
        UsageLog(
            user_id=user.id,
            anonymous_id=None,
            action_type="audio_review",
            model_name=result.model_name,
            tokens_total=(result.tokens_prompt or 0) + (result.tokens_completion or 0),
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    return {"id": audio.id, "role_review": report}


@router.post("/analyses/{audio_id}/interview-review")
def run_interview_review(
    audio_id: int,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """面试审核：优先用角色标注结果渲染对话体，无标注则用原文；四维评分报告。"""
    audio = _owned_audio(db, user, audio_id)
    if not (audio.transcript or "").strip():
        raise HTTPException(400, "还没有可审核的转写文本")
    enforce_daily_limit(
        db,
        anonymous_id,
        settings.daily_audio_review_limit,
        "audio_review",
        user_id=user.id,
    )
    role_marked = (
        audio_review_service.render_role_marked(
            audio.role_review_json.get("segments", [])
        )
        if isinstance(audio.role_review_json, dict)
        else audio.transcript
    )
    try:
        report, result = audio_review_service.interview_review(role_marked)
    except Exception as exc:
        raise HTTPException(502, "审核失败，AI 服务暂时不可用，请稍后重试") from exc
    audio.interview_review_json = report
    audio.model_name = result.model_name
    audio.status = "reviewed"
    db.add(
        UsageLog(
            user_id=user.id,
            anonymous_id=None,
            action_type="audio_review",
            model_name=result.model_name,
            tokens_total=(result.tokens_prompt or 0) + (result.tokens_completion or 0),
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    return {"id": audio.id, "interview_review": report}


@router.delete("/analyses/{audio_id}", status_code=204)
def delete_audio(
    audio_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> None:
    audio = _owned_audio(db, user, audio_id)
    try:
        Path(audio.storage_path).unlink(missing_ok=True)
    except OSError:  # 文件删除失败不阻塞记录删除
        pass
    db.delete(audio)
    db.commit()
