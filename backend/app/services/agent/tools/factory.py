"""make_tools 组装层：按 registry.TOOL_NAMES 顺序把各域工具拼成请求专属列表。

每请求新建工具实例（闭包绑定本次 db 会话与归属者），绝不跨请求共享。
"""

from langchain_core.tools import BaseTool
from sqlalchemy.orm import Session

from app.services.agent.tools.common import ToolContext
from app.services.agent.tools.conversation import (
    build_conversation_search,
    build_usage_stats,
)
from app.services.agent.tools.interview import (
    build_interview_history,
    build_interview_transcript,
    build_score_trend,
)
from app.services.agent.tools.kb import build_kb_list, build_kb_search
from app.services.agent.tools.llm import (
    build_answer_review,
    build_job_match,
    build_question_gen,
)
from app.services.agent.tools.platform import build_platform_help
from app.services.agent.tools.registry import TOOL_NAMES
from app.services.agent.tools.resume import build_analysis_read, build_resume_lookup

_BUILDERS = {
    "kb_search": build_kb_search,
    "resume_lookup": build_resume_lookup,
    "interview_history": build_interview_history,
    "interview_transcript": build_interview_transcript,
    "score_trend": build_score_trend,
    "conversation_search": build_conversation_search,
    "usage_stats": build_usage_stats,
    "analysis_read": build_analysis_read,
    "kb_list": build_kb_list,
    "platform_help": build_platform_help,
    "job_match": build_job_match,
    "question_gen": build_question_gen,
    "answer_review": build_answer_review,
}


def make_tools(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    ctx: ToolContext,
) -> list[BaseTool]:
    """构造本次请求专属的工具列表（顺序与 registry.TOOL_NAMES 严格一致）。"""
    assert set(_BUILDERS) == set(TOOL_NAMES), "registry 与工具实现不同步"
    return [_BUILDERS[name](db, user_id, anonymous_id, ctx) for name in TOOL_NAMES]
