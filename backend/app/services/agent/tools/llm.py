"""工具内调 LLM 的三件套：job_match / question_gen / answer_review。

统一走 _run_tool_llm：受 daily_agent_tool_llm_limit 单独限额、记 agent_tool_llm
用量，与 Agent 主循环的 daily_agent_limit 分开（见 docs/Agent工具设计.md §六）。
"""

from langchain_core.tools import BaseTool, tool
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.agent import AnswerReviewReport
from app.services.agent.tools.common import (
    _SCORE_LABELS,
    _TOOL_ANSWER_CHARS,
    _TOOL_ANSWER_SUGGESTIONS,
    ToolContext,
)
from app.services.agent_capabilities import as_items as _as_items
from app.services.agent_capabilities import (
    run_job_match,
    run_question_generation,
)
from app.services.agent_capabilities import run_tool_llm as _run_tool_llm
from app.services.ai_client import chat_json
from app.services.prompts import (
    ANSWER_REVIEW_SYSTEM_PROMPT,
    build_answer_review_prompt,
)

logger = get_logger(__name__)


def build_job_match(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    ctx: ToolContext,
) -> BaseTool:
    """构造 job_match 工具（闭包绑定本次请求的会话与归属者，防串数据）。"""

    @tool
    def job_match(jd_text: str) -> str:
        """拿用户贴的岗位 JD（招聘要求）与其简历做匹配分析：整体匹配度评分、
        简历已命中的关键词、JD 要求但简历缺失的关键词、针对性改简历建议。
        本工具内部会调用一次 AI，受"工具内 AI 调用"的每日限额约束。

        什么时候用：用户贴了一段岗位 JD / 招聘要求，问"我匹配吗""这个岗位适合我吗"
        "我还缺什么""按这个 JD 我简历该怎么改"。用户只用口语描述了岗位要求
        （例如"这家公司要求熟悉 MySQL 和 K8s""这个岗位要会微服务和高并发"）也**算 JD**，
        把这几条要求原样作为 jd_text 传入即可，不必等他贴完整的招聘启事。
        什么时候不用：只问简历**原文**里有没有写过某技能/项目请用 resume_lookup；
        要读已有的 AI 分析结论请用 analysis_read（本工具是**现算**的 JD 匹配，不是读旧报告）；
        用户完全没提目标岗位的任何要求（纯聊岗位前景、薪资行情）时才不要调用本工具。
        入参 jd_text 为岗位描述原文或用户口述的岗位要求，超过 4000 字符会截断后分析。"""
        # v4.0 M1：核心逻辑下沉 agent_capabilities.run_job_match（v2 matcher 节点共用）
        text, _report = run_job_match(db, user_id, anonymous_id, jd_text)
        return text

    return job_match


def build_question_gen(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    ctx: ToolContext,
) -> BaseTool:
    """构造 question_gen 工具（闭包绑定本次请求的会话与归属者，防串数据）。"""

    @tool
    def question_gen(topic: str, position_type: str) -> str:
        """围绕某个技术主题**出一组模拟面试题**：先检索平台知识库，再依据检索到的语料
        出题，因此题目会贴合平台已收录的资料。本工具内部会调用一次 AI，
        受"工具内 AI 调用"的每日限额约束。

        什么时候用：用户说"给我出几道题""帮我练一下某主题""出几道面试题考考我"——
        要的是**一组新题目**，可指定实习/校招/社招的难度。
        什么时候不用：要查某知识点的**答案与讲解**（原理、用法、对比、排错）请用 kb_search，
        本工具只出题、不给答案；用户已经写好一段回答要**点评**请用 answer_review；
        要的是针对**本人简历**的预测面试题请用 analysis_read；
        问"模拟面试功能怎么开始"（入口与流程）请用 platform_help。
        入参 topic 为想练习的主题；position_type 取 intern（实习）/ fresh（校招）/
        senior（社招）/ 空字符串，其他值按通用难度处理。"""
        # v4.0 M1：核心逻辑下沉 agent_capabilities.run_question_generation（v2 questioner 节点共用）
        text, _product = run_question_generation(
            db, user_id, anonymous_id, topic, position_type
        )
        return text

    return question_gen


def build_answer_review(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    ctx: ToolContext,
) -> BaseTool:
    """构造 answer_review 工具（闭包绑定本次请求的会话与归属者，防串数据）。"""

    @tool
    def answer_review(question: str, answer: str) -> str:
        """点评用户贴的一段**面试回答**：按技术深度、表达结构、项目真实性三项 10 分制打分，
        并给出 2~4 条具体改进建议。本工具内部会调用一次 AI，受"工具内 AI 调用"的每日限额约束。

        什么时候用：用户把自己的**回答原文**贴过来（"我这么答行不行""帮我看看这段回答"
        "我这样答能得几分""刚才那道题我是这样答的，帮我点评一下"）——必须是**已有的一段回答**，
        题目与回答一起给最准；用户承接上文说"我这样答/帮我点评"而本轮看不到完整原文时，
        同样调用本工具，工具会自己提示他补全。
        什么时候不用：要**出一组成题**请用 question_gen；要查某知识点的**标准答案与讲解**
        请用 kb_search（不要因为用户贴的回答里提到某个技术名词就去检索知识库——
        意图是点评他的回答就用本工具）；要读模拟面试**已生成的结束报告**
        请用 interview_history 或 score_trend。
        不要拿本工具的评价冒充平台已生成的面试报告。
        入参 question 为对应的面试题目；answer 为用户自己的回答，超过 2000 字符会截断后点评。"""
        title = (question or "").strip()
        body = (answer or "").strip()
        if not body:
            return "请让用户把他自己的回答贴进来（题目 + 他的回答），我才能点评。"
        if not title:
            return (
                "请让用户把对应的面试题目一起发过来，我才知道该按什么标准点评这段回答。"
            )

        # 用户可能把整段自述或项目经历贴进来，超长回答会撑爆上下文并让费用翻倍
        truncated = len(body) > _TOOL_ANSWER_CHARS
        if truncated:
            body = body[:_TOOL_ANSWER_CHARS]

        def _call():
            return chat_json(
                ANSWER_REVIEW_SYSTEM_PROMPT,
                build_answer_review_prompt(title, body),
                settings,
                AnswerReviewReport.model_validate,
            )

        try:
            result, reply = _run_tool_llm(db, user_id, anonymous_id, _call)
        except Exception:
            logger.exception("agent answer_review AI 调用失败")
            return "回答点评的 AI 服务暂时不可用（可能是服务欠费或超时），请稍后再试。"
        if reply is not None:  # 达到工具内 AI 调用上限
            return reply

        report = result.report if result is not None else None
        if not isinstance(report, dict):
            return "回答点评的 AI 返回结果无法解析，请让用户稍后再试一次。"

        # 与面试结束报告共用 _SCORE_LABELS 的口径（10 分制），只取前三项不评整体
        scores = [
            f"{label} {report[key]}/10"
            for key, label in _SCORE_LABELS.items()
            if key != "overall" and isinstance(report.get(key), (int, float))
        ]
        lines = ["该段回答的点评（10 分制）："]
        if scores:
            lines.append("评分：" + " / ".join(scores))
        suggestions = _as_items(report.get("suggestions"), _TOOL_ANSWER_SUGGESTIONS)
        if suggestions:
            lines.append("改进建议：" + "；".join(suggestions))
        if truncated:
            lines.append(
                f"（回答超过 {_TOOL_ANSWER_CHARS} 字符，已按前 {_TOOL_ANSWER_CHARS} 字符点评。）"
            )
        return "\n".join(lines)

    return answer_review
