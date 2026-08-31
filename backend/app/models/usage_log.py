"""usage_logs 表：每一次消耗 AI/解析资源的动作记一笔。

每日限流 = 按 created_at 的日期聚合统计；所以 created_at 单独加索引。
"""

from datetime import datetime

from sqlalchemy import DateTime, BigInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UsageLog(Base, TimestampMixin):
    __tablename__ = "usage_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    anonymous_id: Mapped[str | None] = mapped_column(String(64), index=True)

    # parse / analysis / interview_message
    action_type: Mapped[str] = mapped_column(String(30))
    model_name: Mapped[str | None] = mapped_column(String(100))
    tokens_total: Mapped[int | None]
    ip_address: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
