"""录音分析（v4.2 B5）：录音转写 + 角色审核 + 面试审核的持久化记录。"""

from sqlalchemy import BigInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AudioAnalysis(Base, TimestampMixin):
    __tablename__ = "audio_analyses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(500))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    duration_seconds: Mapped[int | None] = mapped_column(BigInteger)
    # transcribing（Celery 转写中）/ transcribed（可编辑、可审核）/ reviewed（已面试审核）
    status: Mapped[str] = mapped_column(String(20), default="transcribing")
    asr_model: Mapped[str | None] = mapped_column(String(50))  # whisper 模型名
    transcript: Mapped[str | None] = mapped_column(Text)
    role_review_json: Mapped[dict | None] = mapped_column(JSONB)
    interview_review_json: Mapped[dict | None] = mapped_column(JSONB)
    model_name: Mapped[str | None] = mapped_column(String(100))  # 审核 LLM
    error: Mapped[str | None] = mapped_column(Text)  # 转写失败话术
