"""analyses 表：一次 AI 简历分析 = 一行。原始返回留档，便于调试与迭代对比。"""

from sqlalchemy import BigInteger, Boolean, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Analysis(Base, TimestampMixin):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    resume_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    anonymous_id: Mapped[str | None] = mapped_column(String(64), index=True)

    model_name: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(20))  # 改提示词递增，可追溯
    result_json: Mapped[dict] = mapped_column(JSONB)  # AI 原始返回留档
    valid_json: Mapped[bool | None]  # 输出校验是否通过

    tokens_prompt: Mapped[int | None]
    tokens_completion: Mapped[int | None]
    duration_ms: Mapped[int | None]
