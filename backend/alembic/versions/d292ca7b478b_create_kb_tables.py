"""create kb_documents and kb_chunks with vector

Revision ID: d292ca7b478b
Revises: ff56460ee037
Create Date: 2026-09-03 01:05:00.000000

Playground 知识库（v3.0）：建 vector 扩展、文档表、切块表与 HNSW 余弦索引。
手写而非 autogenerate：扩展与 HNSW 索引 autogenerate 不会产出。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d292ca7b478b"
down_revision: str | None = "ff56460ee037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 依赖 pgvector/pgvector 镜像；CREATE EXTENSION 需要 superuser（POSTGRES_USER 即 superuser）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "kb_documents",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("anonymous_id", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("doc_type", sa.String(length=20), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kb_documents")),
    )
    op.create_index("ix_kb_documents_user_id", "kb_documents", ["user_id"])
    op.create_index("ix_kb_documents_anonymous_id", "kb_documents", ["anonymous_id"])
    op.create_index("ix_kb_documents_file_hash", "kb_documents", ["file_hash"])

    op.create_table(
        "kb_chunks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "document_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "kb_documents.id",
                name=op.f("fk_kb_chunks_kb_documents_document_id"),
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("embedding", Vector(dim=768), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kb_chunks")),
    )
    op.create_index("ix_kb_chunks_document_id", "kb_chunks", ["document_id"])

    # HNSW 余弦近似最近邻：召回快，nomic-embed-text 768 维在万级块上足够
    op.execute(
        "CREATE INDEX ix_kb_chunks_embedding_hnsw ON kb_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_kb_chunks_embedding_hnsw", table_name="kb_chunks")
    op.drop_index("ix_kb_chunks_document_id", table_name="kb_chunks")
    op.drop_table("kb_chunks")
    op.drop_index("ix_kb_documents_file_hash", table_name="kb_documents")
    op.drop_index("ix_kb_documents_anonymous_id", table_name="kb_documents")
    op.drop_index("ix_kb_documents_user_id", table_name="kb_documents")
    op.drop_table("kb_documents")
