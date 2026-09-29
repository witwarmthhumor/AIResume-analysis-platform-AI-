"""面试题库（v4.2 B3）：基于简历批量生成的定制化面试题集合。"""

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class QuestionBank(Base, TimestampMixin):
    __tablename__ = "question_banks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    resume_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String(200))  # 默认"{简历文件名}-面试题库"
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    # {"questions": [{"question", "category", "difficulty"}]}
    questions_json: Mapped[dict] = mapped_column(JSONB)
    model_name: Mapped[str | None] = mapped_column(String(100))
