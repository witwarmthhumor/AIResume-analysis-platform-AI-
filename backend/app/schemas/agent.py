"""AI 客服工具内 LLM 输出的数据合同：AI 返回的 JSON 必须长什么样。

与 analysis.py / interview.py 同层同级——那两个是接口级报告的合同，这里是工具级
（job_match / answer_review 等工具内部调模型）的合同，校验不过 → 由 ai_client 触发重试。
"""

from pydantic import BaseModel, Field


class JobMatchReport(BaseModel):
    """job_match 工具必须输出的固定 JSON 结构（JD 与简历的匹配分析）。"""

    match_score: int = Field(ge=0, le=100, description="0~100 的整体匹配度")
    matched_keywords: list[str] = Field(description="简历已覆盖的 JD 关键词")
    missing_keywords: list[str] = Field(description="JD 要求但简历缺失的关键词")
    suggestions: list[str] = Field(description="针对该岗位的改进建议")
