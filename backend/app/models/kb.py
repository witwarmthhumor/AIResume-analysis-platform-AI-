"""Playground 知识库表（v3.0）：文档 + 切块向量。

kb_documents 存文档元信息（含归属与状态），kb_chunks 存切块正文与其向量。
预置语料 scope=public 全站可见；用户上传 scope=private 仅本人可见（owner 隔离）。
"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class KBDocument(Base, TimestampMixin):
    __tablename__ = "kb_documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 归属：preset 语料两者皆空 + scope=public；上传文档记 owner（登录 user_id / 匿名 anonymous_id 二选一）
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    anonymous_id: Mapped[str | None] = mapped_column(String(64), index=True)

    title: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(20))  # preset / uploaded
    scope: Mapped[str] = mapped_column(String(20))  # public / private
    doc_type: Mapped[str] = mapped_column(String(20))  # text / markdown / pdf
    file_hash: Mapped[str | None] = mapped_column(
        String(64), index=True
    )  # 上传去重（同内容不重复入库）
    raw_text: Mapped[str | None] = mapped_column(Text)

    # pending(上传完待处理) / processing(切块向量化中) / ready(可检索) / failed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    parse_error: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embedding_dim: Mapped[int | None]

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )  # 软删除，与 resumes 同约定


class KBChunk(Base, TimestampMixin):
    __tablename__ = "kb_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("kb_documents.id", ondelete="CASCADE"),
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer)  # 块内序号，回答引用来源定位用
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None]  # 近似值：检索与审计参考
    embedding: Mapped[list[float]] = mapped_column(Vector(768))
