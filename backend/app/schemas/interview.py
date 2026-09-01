"""模拟面试的数据合同：出参模型 + 结束评价的 AI 输出校验模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class InterviewReport(BaseModel):
    """AI 结束评价必须输出的 JSON 结构（分维度评分，PROJECT-PLAN §2）。"""

    technical_depth: int = Field(ge=1, le=10, description="技术深度")
    communication: int = Field(ge=1, le=10, description="表达结构")
    project_authenticity: int = Field(ge=1, le=10, description="项目真实性")
    overall: int = Field(ge=1, le=10, description="整体表现")
    summary: str = Field(description="整体评价")
    highlights: list[str] = Field(description="表现亮点")
    improvements: list[str] = Field(description="改进建议")


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str  # interviewer / candidate
    content: str
    created_at: datetime


class SessionOut(BaseModel):
    """会话出参：状态 + 全部消息（刷新恢复靠它）+ 结束报告（若有）。"""

    id: int
    resume_id: int
    status: str  # in_progress / finished
    stage: str  # intro / technical / deep_dive / wrapup
    turn_count: int
    max_turns: int
    messages: list[MessageOut]
    final_report: dict | None  # finished 时为 InterviewReport 通过校验后的 dict


class StartSessionOut(BaseModel):
    """建会话响应：会话 + 提示词版本（留档追溯）。"""

    interview_prompt_version: str
    session: SessionOut


class SendMessageOut(BaseModel):
    """发消息的 SSE meta/done 事件负载（也用于错误事件的 data）。"""

    turn: int
    stage: str
    content: str = ""  # done/error 事件里携带完整回复或错误话术
    tokens_prompt: int | None = None
    tokens_completion: int | None = None
    duration_ms: int | None = None
