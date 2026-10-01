"""工具包共享层：常量、中文标签映射、ToolContext 与归属过滤等辅助函数。

拆自原 services/agent/tools.py（v4.3 P1-6 单一数据源改造）：所有工具模块一律从
这里取常量与辅助函数，禁止各自复制口径。工具清单与中文名见 registry.py。
"""

from dataclasses import dataclass, field

from sqlalchemy import and_

from app.services.lexical_service import tokenize


@dataclass
class ToolContext:
    """一次 Agent 运行期间工具的共享状态：最近一次知识库命中（用于前端引用来源）。"""

    citations: list[dict] = field(default_factory=list)


# 单块内容回灌给 LLM 的最大字符数（5 块 × 约 300 字，控制 Agent 上下文体积）
# 列表类工具单次返回条数上限与片段宽度
_TOOL_LIST_LIMIT = 5
_TOOL_SNIPPET_WIDTH = 80
# 历史对话检索单次返回条数上限（与 _TOOL_LIST_LIMIT 同口径）
_TOOL_CONV_LIMIT = 5
# 趋势类工具最多纳入对比的场次数
_TOOL_TREND_LIMIT = 10
# 面试问答原文：单次返回的消息条数上下限与整段输出的字符上限
_TOOL_TRANSCRIPT_LIMIT = 20
_TOOL_TRANSCRIPT_CHARS = 2000
# 分析报告回灌给 LLM 的体积控制：总长上限 + 每类列表条数（预测面试题单独再收窄）
_TOOL_REPORT_CHARS = 800
_TOOL_REPORT_ITEMS = 6
_TOOL_REPORT_QUESTIONS = 5
# 知识库清单最多列出的文档数（与 kb_max_documents_per_owner 同量级）
_TOOL_KB_LIMIT = 20
# job_match/question_gen 的长度上限与限额话术已下沉 agent_capabilities.py（v4.0 M1，
# v1 工具与 v2 节点共用）；此处仅保留 answer_review 的自有上限
# answer_review：用户贴的回答与渲染出的建议条数上限（建议条数的下限由 schema 卡 min 2）
_TOOL_ANSWER_CHARS = 2000
_TOOL_ANSWER_SUGGESTIONS = 4

# 知识库文档的状态与来源中文名（与前端知识库页的徽章口径一致）
_KB_STATUS_LABELS = {
    "pending": "待入库",
    "processing": "入库中",
    "ready": "可检索",
    "failed": "入库失败",
}
_KB_SCOPE_LABELS = {"public": "平台预置", "private": "本人上传"}
# 会话来源的中文名（chat=在线对话 / agent=AI 客服，对应 chat_sessions.session_type）
_SESSION_SOURCE_LABELS = {"chat": "在线对话", "agent": "AI 客服"}
# 面试场次状态的中文名（interview_history 与 interview_transcript 共用）
_INTERVIEW_STATUS_LABELS = {
    "in_progress": "进行中",
    "finished": "已结束",
    "abandoned": "已放弃",
}
# 面试消息角色的中文名（对应 interview_messages.role）
_INTERVIEW_ROLE_LABELS = {"interviewer": "面试官", "candidate": "我", "system": "系统"}
_POSITION_DEFAULT = "通用"

# 用量动作的中文名（与前端使用日志的徽章映射保持一致）
_ACTION_LABELS = {
    "parse": "简历解析",
    "analysis": "AI 简历分析",
    "interview_message": "模拟面试对话",
    "kb_upload": "知识库上传",
    "playground": "在线对话问答",
    "chat_create": "新建对话",
    "agent": "AI 客服问答",
    "agent_create": "新建客服会话",
    "agent_tool_llm": "工具内 AI 调用",
    "question_bank": "面试题生成",  # v4.2 题库生成
    "audio_transcribe": "录音转写",  # v4.2 录音分析
    "audio_review": "录音审核",  # v4.2 录音分析
}

# 面试结束评价的分维度中文名（与 interview_prompts 的 JSON 结构一一对应）
_SCORE_LABELS = {
    "technical_depth": "技术深度",
    "communication": "表达结构",
    "project_authenticity": "项目真实性",
    "overall": "整体表现",
}


def _owner_filter(model, user_id: int | None, anonymous_id: str | None):
    """归属过滤条件：登录按 user_id，匿名按 (user_id IS NULL + anonymous_id)。

    识别不到身份时返回 None，调用方应直接告知"无法查询"而非返回全部数据。
    """
    if user_id is not None:
        return model.user_id == user_id
    if anonymous_id:
        return and_(model.user_id.is_(None), model.anonymous_id == anonymous_id)
    return None


def _snippet(text: str, keyword: str, width: int = _TOOL_SNIPPET_WIDTH) -> str | None:
    """在正文里定位关键词（先分词再逐个找）并返回上下文片段，找不到返回 None。"""
    haystack = text or ""
    if not haystack:
        return None
    terms = tokenize(keyword) or [keyword.strip()]
    for term in terms:
        if not term:
            continue
        idx = haystack.find(term)
        if idx < 0:
            continue
        start = max(0, idx - width)
        end = min(len(haystack), idx + len(term) + width)
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(haystack) else ""
        return f"{prefix}{haystack[start:end].replace(chr(10), ' ')}{suffix}"
    return None
