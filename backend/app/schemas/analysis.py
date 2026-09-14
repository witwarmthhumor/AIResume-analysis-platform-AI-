"""AI 分析报告的数据合同：出参模型 + AI 输出校验模型。

AIReport 是"AI 返回的 JSON 必须长什么样"的唯一权威定义；
解析或类型不过 → 视为无效输出，由封装层触发重试。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AIReport(BaseModel):
    """AI 必须输出的固定 JSON 结构（PROJECT-PLAN 阶段2 验收的六块内容）。"""

    target_position: str = Field(description="从简历推断的目标岗位")
    position_match: str = Field(description="岗位匹配度评估")
    strengths: list[str] = Field(description="优势")
    weaknesses: list[str] = Field(description="短板")
    keyword_gaps: list[str] = Field(description="关键词缺口")
    suggestions: list[str] = Field(description="改进建议")
    predicted_questions: list[str] = Field(description="预测面试题")


class AnalysisOut(BaseModel):
    """分析接口的出参（报告 = 通过校验的 result_json）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    resume_id: int
    model_name: str
    prompt_version: str
    report: dict  # 取自 result_json（入库前已通过 AIReport 校验）
    tokens_prompt: int | None
    tokens_completion: int | None
    duration_ms: int | None
    created_at: datetime


class AnalysisResultOut(BaseModel):
    """analyze 接口响应。cached=true 表示命中去重，直接返回已有报告，未重新调用 AI。"""

    cached: bool
    analysis: AnalysisOut


class AnalysisVersionsOut(BaseModel):
    """该简历的历次分析报告列表（v3.5 报告页「版本对比」数据源）。"""

    items: list[AnalysisOut]
