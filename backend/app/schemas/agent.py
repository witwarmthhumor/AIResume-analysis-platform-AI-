"""AI 客服工具内 LLM 输出的数据合同：AI 返回的 JSON 必须长什么样。

与 analysis.py / interview.py 同层同级——那两个是接口级报告的合同，这里是工具级
（job_match / question_gen / answer_review 等工具内部调模型）的合同，校验不过 → 由
ai_client 触发重试。
"""

from pydantic import BaseModel, Field


class JobMatchReport(BaseModel):
    """job_match 工具必须输出的固定 JSON 结构（JD 与简历的匹配分析）。"""

    match_score: int = Field(ge=0, le=100, description="0~100 的整体匹配度")
    matched_keywords: list[str] = Field(description="简历已覆盖的 JD 关键词")
    missing_keywords: list[str] = Field(description="JD 要求但简历缺失的关键词")
    suggestions: list[str] = Field(description="针对该岗位的改进建议")


class QuestionGenReport(BaseModel):
    """question_gen 工具必须输出的固定 JSON 结构（围绕主题的一组模拟面试题）。"""

    questions: list[str] = Field(
        min_length=3,
        max_length=5,
        description="3~5 道面试题，只给题目不给答案",
    )


class AnswerReviewReport(BaseModel):
    """answer_review 工具必须输出的固定 JSON 结构（对一段回答的点评）。

    三项评分与 interview_prompts 的四维口径同名同量程（10 分制），这里刻意**不评
    整体表现**——单次回答只作三项点评，整体小结交给模拟面试的结束报告。
    """

    technical_depth: int = Field(ge=1, le=10, description="技术深度 1~10")
    communication: int = Field(ge=1, le=10, description="表达结构 1~10")
    project_authenticity: int = Field(ge=1, le=10, description="项目真实性 1~10")
    suggestions: list[str] = Field(
        min_length=2,
        max_length=4,
        description="2~4 条具体可操作的改进建议",
    )
