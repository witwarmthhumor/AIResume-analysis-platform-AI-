"""agent v2 表：agent_runs / agent_spans / agent_approvals / audit_logs（PRD v4.0 §6）

Revision ID: b7e4a1c90d52
Revises: f3a91c2b407d
Create Date: 2026-09-23

说明：LangGraph checkpoint 三表不在此列——由 PostgresSaver.setup() 在独立
langgraph schema 内幂等创建（PRD v4.0 评审 D3 方案：checkpoint 是可再生缓存）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e4a1c90d52"
down_revision: str | None = "f3a91c2b407d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        *_ts_columns(),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("anonymous_id", sa.String(64), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=False, unique=True),
        sa.Column("thread_id", sa.String(64), nullable=False, unique=True),
        sa.Column("session_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "run_type",
            sa.String(50),
            nullable=False,
            server_default="job_prep_pipeline",
        ),
        sa.Column("input_json", sa.Text(), nullable=True),
        sa.Column("plan_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="planning"),
        sa.Column("current_node", sa.String(50), nullable=True),
        sa.Column("output_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("iterations", sa.Integer(), nullable=True),
        sa.Column("tokens_total", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("approved_by", sa.BigInteger(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"])
    op.create_index("ix_agent_runs_anonymous_id", "agent_runs", ["anonymous_id"])
    op.create_index(
        "ix_agent_runs_status_waiting",
        "agent_runs",
        ["status"],
        postgresql_where=sa.text("status = 'waiting_approval'"),
    )

    op.create_table(
        "agent_spans",
        *_ts_columns(),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_span_id", sa.BigInteger(), nullable=True),
        sa.Column("span_type", sa.String(20), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ok"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("input_preview", sa.Text(), nullable=True),
        sa.Column("output_preview", sa.Text(), nullable=True),
        sa.Column("tokens_prompt", sa.Integer(), nullable=True),
        sa.Column("tokens_completion", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(100), nullable=True),
    )
    op.create_index("ix_agent_spans_trace_id", "agent_spans", ["trace_id"])
    op.create_index("ix_agent_spans_run_id", "agent_spans", ["run_id"])

    op.create_table(
        "agent_approvals",
        *_ts_columns(),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("anonymous_id", sa.String(64), nullable=True),
        sa.Column("action_key", sa.String(50), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_approvals_run_id", "agent_approvals", ["run_id"])
    op.create_index("ix_agent_approvals_trace_id", "agent_approvals", ["trace_id"])
    op.create_index("ix_agent_approvals_user_id", "agent_approvals", ["user_id"])
    op.create_index(
        "ix_agent_approvals_anonymous_id", "agent_approvals", ["anonymous_id"]
    )
    op.create_index(
        "ix_agent_approvals_status_pending",
        "agent_approvals",
        ["status"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "audit_logs",
        *_ts_columns(),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_anonymous_id", sa.String(64), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("before_snapshot", sa.Text(), nullable=True),
        sa.Column("after_snapshot", sa.Text(), nullable=True),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_run_id", "audit_logs", ["run_id"])
    op.create_index("ix_audit_logs_trace_id", "audit_logs", ["trace_id"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_index("ix_agent_approvals_status_pending", table_name="agent_approvals")
    op.drop_table("agent_approvals")
    op.drop_index("ix_agent_spans_run_id", table_name="agent_spans")
    op.drop_index("ix_agent_spans_trace_id", table_name="agent_spans")
    op.drop_table("agent_spans")
    op.drop_index("ix_agent_runs_status_waiting", table_name="agent_runs")
    op.drop_index("ix_agent_runs_anonymous_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_user_id", table_name="agent_runs")
    op.drop_table("agent_runs")
