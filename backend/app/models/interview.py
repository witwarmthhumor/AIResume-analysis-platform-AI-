"""interview_sessions + interview_messages：一场面试 = 一行场次 + 多行消息。

消息逐条落库 → 页面刷新可恢复；数据结构是"消息列表"，天然兼容远期语音。
"""

from sqlalchemy import BigInteger, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class InterviewSession(Base, TimestampMixin):
    __tablename__ = "interview_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    resume_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    anonymous_id: Mapped[str | None] = mapped_column(String(64), index=True)

    # in_progress / finished / abandoned
    status: Mapped[str] = mapped_column(String(20), default="in_progress")
    # 面试状态机：intro → technical → deep_dive → wrapup
    stage: Mapped[str] = mapped_column(String(20), default="intro")
    turn_count: Mapped[int] = mapped_column(Integer, default=0)  # 设上限防无限聊
    final_report_json: Mapped[dict | None] = mapped_column(
        JSONB
    )  # 结束评价报告（分维度评分）


class InterviewMessage(Base, TimestampMixin):
    __tablename__ = "interview_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[int] = mapped_column(BigInteger, index=True)

    # interviewer / candidate / system
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int | None]
