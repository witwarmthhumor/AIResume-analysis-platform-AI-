"""Agent v2（LangGraph）数据模型：run 实例 / 调用链 span / 审批单 / 审计日志（PRD §6）。

归属口径与全项目一致：登录记 user_id，匿名记 anonymous_id（owner_clause 双条件校验）。
span 的 input/output_preview 只存 ≤200 字预览，禁止简历正文与密钥（PRD §10.4）。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AgentRun(Base, TimestampMixin):
    """一次图运行实例（run_type=job_prep_pipeline）。"""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    anonymous_id: Mapped[str | None] = mapped_column(String(64), index=True)
    trace_id: Mapped[str] = mapped_column(String(64), unique=True)
    thread_id: Mapped[str] = mapped_column(String(64), unique=True)
    session_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    run_type: Mapped[str] = mapped_column(String(50), default="job_prep_pipeline")
    input_json: Mapped[str | None] = mapped_column(Text)  # 归一化后的用户输入
    plan_json: Mapped[str | None] = mapped_column(Text)  # Planner 计划（步骤条数据源）
    status: Mapped[str] = mapped_column(
        String(30),
        default="planning",
        index=True,
    )  # planning/running/waiting_approval/verifying/completed/failed/aborted/rejected
    current_node: Mapped[str | None] = mapped_column(String(50))
    output_json: Mapped[str | None] = mapped_column(Text)  # 最终产物（三段摘要）
    error: Mapped[str | None] = mapped_column(Text)  # 面向用户的失败话术
    iterations: Mapped[int | None]
    tokens_total: Mapped[int | None]
    duration_ms: Mapped[int | None]
    approved_by: Mapped[int | None] = mapped_column(BigInteger)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentSpan(Base, TimestampMixin):
    """调用链 span：节点 / LLM / 检索 / 工具 四个粒度，支持 trace 树回放。"""

    __tablename__ = "agent_spans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[int] = mapped_column(BigInteger, index=True)
    parent_span_id: Mapped[int | None] = mapped_column(BigInteger)
    span_type: Mapped[str] = mapped_column(String(20))  # agent_node/llm/retrieval/tool
    name: Mapped[str] = mapped_column(String(50))  # planner/matcher/kb_search 等
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok/error/retried
    attempt: Mapped[int] = mapped_column(Integer, default=1)  # 第几次尝试（回环观测）
    input_preview: Mapped[str | None] = mapped_column(Text)  # ≤200 字，禁正文/密钥
    output_preview: Mapped[str | None] = mapped_column(Text)
    tokens_prompt: Mapped[int | None]
    tokens_completion: Mapped[int | None]
    duration_ms: Mapped[int | None]
    error_type: Mapped[str | None] = mapped_column(String(100))


class AgentApproval(Base, TimestampMixin):
    """HITL 审批单：风险动作执行前的人机确认（PRD FR-7，零副作用硬门禁）。"""

    __tablename__ = "agent_approvals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    anonymous_id: Mapped[str | None] = mapped_column(String(64), index=True)
    action_key: Mapped[str] = mapped_column(String(50))  # create_interview_session 等
    payload_json: Mapped[str | None] = mapped_column(Text)  # 动作参数快照（续跑输入）
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # pending / approved / rejected / expired
    decided_by: Mapped[int | None] = mapped_column(BigInteger)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base, TimestampMixin):
    """审计日志（标准⑥）：谁在什么时候对什么做了什么（风险动作与审批全程留痕）。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    actor_anonymous_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(50), index=True)
    target_type: Mapped[str | None] = mapped_column(String(50))
    target_id: Mapped[str | None] = mapped_column(String(64))
    before_snapshot: Mapped[str | None] = mapped_column(Text)  # 变更摘要，不放正文全文
    after_snapshot: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
