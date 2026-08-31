"""resumes 表：一次上传 = 一行。字段与 PROJECT-PLAN.md §2 一一对应。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Resume(Base, TimestampMixin):
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 归属：V1 无登录用 anonymous_id，阶段4 接 user_id（先可空）
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    anonymous_id: Mapped[str | None] = mapped_column(String(64), index=True)

    filename: Mapped[str] = mapped_column(String(255))  # 原始文件名（已消毒）
    file_hash: Mapped[str] = mapped_column(
        String(64), index=True
    )  # SHA-256，重复上传去重
    storage_path: Mapped[str] = mapped_column(String(500))  # 存 web 根目录之外

    raw_text: Mapped[str | None]  # 解析出的纯文本
    page_count: Mapped[int | None]
    file_size: Mapped[int | None]

    # pending(上传完成待解析) / success / failed / unsupported(扫描件等)
    parse_status: Mapped[str] = mapped_column(String(20), default="pending")
    parse_error: Mapped[str | None] = mapped_column(Text)  # 面向用户的失败话术

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )  # 软删除
