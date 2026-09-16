"""Agent 工具集（v3.4 起，v3.5 扩到四件套）。

设计要点：
- make_tools 是"每请求工厂"：闭包绑定本次请求的 db 会话与归属者（user_id/anonymous_id），
  杜绝多请求共享工具导致的用户串数据。
- 所有工具都只查"当前归属者"自己的数据，天然带用户隔离；未识别到身份时明确拒绝，
  而不是返回空结果让模型自行脑补。
- 检索直接复用现有 embedding_service.embed_texts + kb_service.search_chunks，不重写 RAG。
- ToolContext 回收本次命中的引用来源（citations），供 API 层随 done 事件回传前端；
  工具返回给 LLM 的是拼好的文本片段。
- 工具内部吞掉检索类异常并返回自然语言说明，让 Agent 能换通用知识兜底，而不是整轮崩掉。
- 工具内部自己还要调一次 LLM 的工具（job_match / question_gen / answer_review）统一走
  _run_tool_llm：按 daily_agent_tool_llm_limit 单独限额、单独记 agent_tool_llm 用量，
  与 Agent 主循环的 daily_agent_limit 分开，否则实际可用次数会莫名腰斩且无法归因。
- 工具的 docstring 就是给模型看的"路由说明"，统一按「做什么 → 什么时候用 → 什么时候不用」
  三段写；易撞的工具要在"什么时候不用"里**互相点名**（见 docs/Agent工具设计.md 的易撞组合表）。

工具清单：
1. kb_search          查平台技术知识库（RAG 主链路，唯一会回填 citations 的工具）
2. resume_lookup      查当前用户自己的简历（有哪些、正文里有没有提到某关键词）
3. interview_history  查当前用户自己的模拟面试记录与分维度评分
4. score_trend        查当前用户自己历次模拟面试的分数趋势（逐场对比升降）
5. usage_stats        查当前用户自己的平台用量（近 N 天各动作次数与 token）
6. analysis_read      读当前用户某份简历的 AI 分析结论（复用 analysis_service 的查询）
7. kb_list            列知识库文档清单（标题/块数/状态/来源，不含任何正文）
8. platform_help      答"本平台自身功能怎么用"（纯静态文案，不查库不调模型）
9. job_match          拿岗位 JD 与本人简历做匹配分析（工具内调一次 LLM）
10. question_gen      围绕某主题出一组模拟面试题（先检索平台知识库，再依据语料出题）
11. answer_review     点评用户贴的一段面试回答（三项打分 + 改进建议）
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TypeVar

from langchain_core.tools import BaseTool, tool
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.interview import InterviewSession
from app.models.resume import Resume
from app.models.usage_log import UsageLog
from app.schemas.agent import AnswerReviewReport, JobMatchReport, QuestionGenReport
from app.services.ai_client import chat_json
from app.services.analysis_service import latest_valid_analysis
from app.services.embedding_service import embed_texts
from app.services.kb_service import (
    count_chunks_by_document,
    list_documents,
    search_chunks,
)
from app.services.lexical_service import tokenize
from app.services.prompts import (
    ANSWER_REVIEW_SYSTEM_PROMPT,
    JOB_MATCH_SYSTEM_PROMPT,
    QUESTION_GEN_SYSTEM_PROMPT,
    build_answer_review_prompt,
    build_job_match_prompt,
    build_question_gen_prompt,
)
from app.services.usage_service import count_today_usage_by_owner, write_usage

logger = get_logger(__name__)

_T = TypeVar("_T")

# 单块内容回灌给 LLM 的最大字符数（5 块 × 约 300 字，控制 Agent 上下文体积）
_TOOL_CHUNK_CHARS = 300
# 列表类工具单次返回条数上限与片段宽度
_TOOL_LIST_LIMIT = 5
_TOOL_SNIPPET_WIDTH = 80
# 趋势类工具最多纳入对比的场次数
_TOOL_TREND_LIMIT = 10
# 分析报告回灌给 LLM 的体积控制：总长上限 + 每类列表条数（预测面试题单独再收窄）
_TOOL_REPORT_CHARS = 800
_TOOL_REPORT_ITEMS = 6
_TOOL_REPORT_QUESTIONS = 5
# 知识库清单最多列出的文档数（与 kb_max_documents_per_owner 同量级）
_TOOL_KB_LIMIT = 20
# job_match：JD 与简历正文送入模型的长度上限（用户可能贴一整页 JD），
# 以及渲染回 LLM 时每类关键词/建议的条数上限
_TOOL_JD_CHARS = 4000
_TOOL_MATCH_RESUME_CHARS = 6000
_TOOL_MATCH_ITEMS = 8
# question_gen：送入模型的平台语料块数（与 kb_search 同样按 _TOOL_CHUNK_CHARS 截块）
# 与渲染给模型的题目条数上限
_TOOL_QUESTION_CHUNKS = 3
_TOOL_QUESTION_LIMIT = 5
# answer_review：用户贴的回答与渲染出的建议条数上限（建议条数的下限由 schema 卡 min 2）
_TOOL_ANSWER_CHARS = 2000
_TOOL_ANSWER_SUGGESTIONS = 4
# 工具内部自带 LLM 调用的记账口径：与 Agent 主循环的 agent/agent_create 分开统计，
# 上限独立走 settings.daily_agent_tool_llm_limit
_TOOL_LLM_ACTION = "agent_tool_llm"
_TOOL_LLM_LIMIT_REPLY = (
    "今日工具内 AI 调用已达上限，这个功能今天暂时用不了（次日 0 点自动恢复）。"
    "不要改用自己编造的内容代替，直接说明该功能今日次数已用完即可。"
)

# 知识库文档的状态与来源中文名（与前端知识库页的徽章口径一致）
_KB_STATUS_LABELS = {
    "pending": "待入库",
    "processing": "入库中",
    "ready": "可检索",
    "failed": "入库失败",
}
_KB_SCOPE_LABELS = {"public": "平台预置", "private": "本人上传"}
# 检索链路两种失败的话术：向量模型挂了与检索本身出错，对模型要分开说清
_KB_EMBED_ERROR = (
    "知识库检索服务（向量模型）暂时不可用，请基于你已有的通用知识谨慎回答，"
    "并说明未检索平台知识库。"
)
_KB_SEARCH_ERROR = (
    "知识库检索暂时出错，请基于你已有的通用知识谨慎回答，并说明未检索平台知识库。"
)

# 岗位类型 → 中文难度定位（与前端面试页选项、interview_prompts 的口径一致）
_POSITION_LABELS = {"intern": "实习", "fresh": "校招", "senior": "社招"}
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
}

# 面试结束评价的分维度中文名（与 interview_prompts 的 JSON 结构一一对应）
_SCORE_LABELS = {
    "technical_depth": "技术深度",
    "communication": "表达结构",
    "project_authenticity": "项目真实性",
    "overall": "整体表现",
}

# —— 平台功能说明（platform_help 的静态文案）——
# 纯静态：不查库、不调模型，随版本迭代直接改这里。每个主题一段文案，
# 末尾附 _PLATFORM_TOPICS 做「别名 → 文案」的路由；topic 为空或全不命中时回总览。
_PLATFORM_OVERVIEW = (
    "本平台是「AI 简历分析 + AI 模拟面试」应用，主要功能：\n"
    "1. 上传简历：在「首页」上传 PDF 简历，系统自动解析正文。\n"
    "2. AI 分析：为解析成功的简历生成岗位匹配、优势、短板、关键词缺口、改进建议与预测面试题。\n"
    "3. 模拟面试：基于简历做多轮实战模拟，结束出四维评分报告。\n"
    "4. 在线对话：基于平台知识库的 RAG 问答（Playground）。\n"
    "5. AI 客服：可查本人简历/分析/面试/用量，也能检索平台知识库。\n"
    "6. 使用日志：本人简历、分析、面试汇总与用量明细（需登录）。\n"
    "7. 个人中心：个人信息、本人用量五卡、近 7 日 Token 柱图与精简日志（需登录）。\n"
    "8. 数据看板：平台用户与用量统计（管理端，需登录）。\n"
    "9. 语料库管理：上传与管理知识库文档（管理端，需登录）。\n"
    "想了解某个功能，可以把主题问得更具体一些，例如「怎么上传简历」。"
)

_PLATFORM_PROFILE = (
    "个人中心（需登录）：左侧导航「个人中心」，含四块——个人信息卡（可退出登录）、"
    "本人用量五卡（第 5 卡为累计 Token）、近 7 日 Token 用量柱图、精简版使用日志。"
)

_PLATFORM_UPLOAD = (
    "上传简历：在「首页」的简历卡上传 PDF（仅文本型 PDF，单个不超过 5MB、不超过 5 页，"
    "扫描件暂不支持）。上传后系统自动解析正文；同一文件按内容哈希去重，重复上传不会产生多条记录。"
    "解析失败会显示原因（如加密 PDF、非文本型），可重新上传。"
)

_PLATFORM_ANALYSIS = (
    "AI 分析：在「首页」选中一份解析成功的简历，点「✨ 生成 AI 分析」，通常 10~30 秒同步返回；"
    "报告包含目标岗位、岗位匹配、优势、短板、关键词缺口、改进建议与预测面试题。"
    "同一简历的历次分析会保留不同版本，可开启「版本对比」并排查看差异。"
)

_PLATFORM_INTERVIEW = (
    "模拟面试：在「首页」选中简历后点「🤖 开始模拟面试」，按开场→技术问答→深挖→收尾阶段推进，"
    f"单场上限 {settings.max_interview_turns} 轮；可选岗位类型（实习/校招/社招，留空为通用难度）。"
    "结束后生成技术深度、表达结构、项目真实性、整体表现四维评分报告。"
)

_PLATFORM_CHAT = (
    "在线对话（Playground，管理端）：左侧导航「在线对话」，基于平台知识库做 RAG 问答——"
    "先在「语料库管理」补资料，再在这里提问；回答会附引用来源（文档标题与相似度）。"
    "一个知识库问答会话可新建多轮对话，侧栏可切换与删除历史会话。"
)

_PLATFORM_AGENT = (
    "AI 客服：左侧导航「AI客服」可进入整页对话（管理端首页右下角另有悬浮入口）。"
    "它能调用平台工具查你自己的简历、AI 分析结论、模拟面试记录与分数趋势、平台用量，"
    "也能列出知识库文档、检索技术资料，以及解答本平台怎么用；"
    "检索知识库得到的回答会标注引用来源。"
)

_PLATFORM_USAGE_LOG = (
    "使用日志（需登录）：管理端左侧导航「使用日志」，汇总本人简历、AI 分析、模拟面试记录与用量明细，"
    "支持按动作类型筛选与分页，可查看每次调用消耗的 token。"
)

_PLATFORM_DASHBOARD = (
    "数据看板（管理端，需登录）：左侧导航「数据看板」，展示平台用户列表与近 7 日用量统计，"
    "用于观察整体调用量趋势，不展示单个用户的具体内容。"
)

_PLATFORM_KB_ADMIN = (
    "语料库管理（管理端，需登录）：左侧导航「语料库管理」，上传的文档是系统预置语料"
    "（全站用户可见、可被在线对话与 AI 客服检索到），也支持删除任意文档（含预置，软删除可审计）。"
    "支持 txt/md/pdf，上传后由后台异步切块并向量化入库，状态显示「可检索」即生效。"
)

# 别名 → 说明文案。**顺序即优先级**：越具体的主题排越前，避免泛词（如「客服」）
# 抢走别的主题；匹配时对 topic 与别名都做小写化再判包含。
_PLATFORM_TOPICS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("个人中心", "我的账号", "个人信息"), _PLATFORM_PROFILE),
    (("上传简历", "简历上传", "上传pdf", "上传 pdf", "传简历"), _PLATFORM_UPLOAD),
    (
        ("ai分析", "ai 分析", "简历分析", "分析报告", "生成分析", "分析结论"),
        _PLATFORM_ANALYSIS,
    ),
    (
        ("模拟面试", "模拟实战面试", "开始面试", "面试练习", "面试报告"),
        _PLATFORM_INTERVIEW,
    ),
    (("在线对话", "playground", "对话问答", "rag 问答"), _PLATFORM_CHAT),
    (("ai客服", "ai 客服", "智能客服", "客服"), _PLATFORM_AGENT),
    (
        ("使用日志", "用量明细", "用量日志", "用量统计", "查用量", "token", "消耗"),
        _PLATFORM_USAGE_LOG,
    ),
    (("数据看板", "看板", "管理后台", "后台管理"), _PLATFORM_DASHBOARD),
    (("语料库", "知识库管理", "上传文档", "上传资料"), _PLATFORM_KB_ADMIN),
)


@dataclass
class ToolContext:
    """一次 Agent 运行期间工具的共享状态：最近一次知识库命中（用于前端引用来源）。"""

    citations: list[dict] = field(default_factory=list)


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


def _kb_retrieve(
    db: Session,
    query: str,
    user_id: int | None,
    anonymous_id: str | None,
    top_k: int,
) -> tuple[list[dict], str]:
    """知识库检索的公共入口（embedding + 混合检索）：kb_search 与 question_gen 共用。

    返回 (命中块, 兜底话术)：检索正常时话术为空串；任一步失败时命中为空、话术为说明文案。
    之所以返回话术而不是抛异常——两个调用方的处置不同：kb_search 要把"向量模型不可用"
    与"检索出错"分开告诉模型，question_gen 则把任何检索失败都当成"未收录"，退回模型出题。
    """
    try:
        vectors = embed_texts([query])
    except Exception:
        logger.warning("agent 知识库检索 embedding 失败，走无检索兜底", exc_info=True)
        return [], _KB_EMBED_ERROR

    try:
        hits = search_chunks(
            db,
            vectors[0],
            user_id,
            anonymous_id,
            top_k=top_k,
            # v3.5：工具入参原文一并交给检索层，走向量 + BM25 混合检索
            query_text=query,
        )
    except Exception:
        logger.exception("agent 知识库检索失败")
        return [], _KB_SEARCH_ERROR
    return hits, ""


def _as_items(value, limit: int) -> list[str]:
    """把报告里的数组字段拍平成非空字符串列表（模型偶尔会给单个字符串）。"""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    items = [str(item).strip() for item in value]
    return [item for item in items if item][:limit]


def _score_of(report: dict, key: str) -> float | None:
    """取报告里某个维度的分数，缺失或不是数字返回 None。"""
    value = report.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _trend_arrow(previous: float | None, current: float | None) -> str:
    """与上一场同维度对比的升降箭头；任一侧缺分数时不给箭头。"""
    if previous is None or current is None:
        return ""
    if current > previous:
        return "↑"
    if current < previous:
        return "↓"
    return "→"


def _trend_mark(previous: float | None, current: float | None) -> str:
    """整场相对上一场的升降标记（首场另由调用方标为基准场）。"""
    if previous is None or current is None:
        return "缺对比数据"
    if current > previous:
        return "上升"
    if current < previous:
        return "下降"
    return "持平"


def _trend_overall(report: dict) -> float | None:
    """整场代表分：优先取整体表现，缺失时用三项维度的均值兜底。"""
    overall = _score_of(report, "overall")
    return overall if overall is not None else _mean_score(report)


def _mean_score(report: dict) -> float | None:
    values = [
        score
        for key in ("technical_depth", "communication", "project_authenticity")
        if (score := _score_of(report, key)) is not None
    ]
    return round(sum(values) / len(values), 1) if values else None


def _platform_reply(topic: str) -> str:
    """把 LLM 现编的 topic 路由到对应功能说明，命中不到就回平台总览。"""
    text = (topic or "").strip().lower()
    if text:
        for aliases, reply in _PLATFORM_TOPICS:
            if any(alias in text for alias in aliases):
                return reply
    return _PLATFORM_OVERVIEW


def _tokens_total(result) -> int | None:
    """取 ai_client.AnalysisResult 之类结果里的 token 合计；取不到就记 None。"""
    prompt = getattr(result, "tokens_prompt", None)
    completion = getattr(result, "tokens_completion", None)
    if prompt is None and completion is None:
        return None
    return int(prompt or 0) + int(completion or 0)


def _record_tool_llm_usage(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    tokens_total: int | None,
) -> None:
    """记一条 agent_tool_llm 用量并提交：Agent 后续崩了，这笔消耗也不能丢。

    记账属于旁路，失败只告警（不抛、不回滚掉工具结果）；无归属者的账单没法归因，
    直接不写，免得污染全站总量统计。
    """
    if user_id is None and anonymous_id is None:
        return
    try:
        write_usage(
            db,
            anonymous_id,
            user_id,
            _TOOL_LLM_ACTION,
            settings.ai_model,
            tokens_total,
            None,
        )
        db.commit()
    except Exception:  # 记账失败不能拖垮工具本身
        logger.warning("agent 工具内 LLM 用量记账失败", exc_info=True)
        db.rollback()


def _run_tool_llm(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    call: Callable[[], _T],
) -> tuple[_T | None, str | None]:
    """工具内调 LLM 的统一入口：当日限额 → 执行 → 记一条 agent_tool_llm 用量。

    返回 (结果, 兜底话术)，两者只有一个非空：已超限时结果为 None、话术是上限提示，
    调用方直接把它返回给模型即可；执行成功时结果为 call() 的返回值、话术为 None。

    为什么要单独限额：工具内再调一次模型会让单次提问的实际消耗翻倍，跟主循环的
    daily_agent_limit 混在一起就没法归因（docs/Agent工具设计.md §六）。

    异常不在这里吞——"JD 太短""回答为空""服务欠费"要给的上下文话术各不相同，由各工具
    自己 try/except 组织。**失败的调用不记账**（重试仍受主循环 daily_agent_limit 约束）。
    """
    try:
        used = count_today_usage_by_owner(
            db, _TOOL_LLM_ACTION, user_id=user_id, anonymous_id=anonymous_id
        )
    except Exception:  # 限额查不到时按不可用处理，不让整轮 Agent 崩
        logger.exception("agent 工具内 LLM 限额查询失败")
        return None, "工具内 AI 调用暂时不可用，请稍后再试。"

    if used >= settings.daily_agent_tool_llm_limit:
        return None, _TOOL_LLM_LIMIT_REPLY

    result = call()
    _record_tool_llm_usage(db, user_id, anonymous_id, _tokens_total(result))
    return result, None


def make_tools(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    ctx: ToolContext,
) -> list[BaseTool]:
    """构造本次请求专属的工具列表。"""

    @tool
    def kb_search(query: str) -> str:
        """检索平台技术知识库：按语义 + 关键词混合检索资料切块，把命中内容回灌给模型，
        并把来源（文档标题与块序号）记为引用展示给用户。

        什么时候用：用户问**计算机技术知识点**——编程语言、Java/JVM/并发、MySQL/Redis、
        计算机网络、操作系统、RAG/AI 应用开发等的原理、用法、对比、排错；必须先检索再作答。
        什么时候不用：问"本平台怎么用"（怎么上传简历、怎么开始模拟面试、在哪看用量、
        平台有哪些功能）请用 platform_help；只想看知识库有哪些**文档清单**请用 kb_list；
        要**出一组模拟面试题**请用 question_gen（本工具负责查答案与讲解，不负责出题）。
        入参 query 为精简后的检索关键词或问题。"""
        hits, error = _kb_retrieve(
            db, query, user_id, anonymous_id, settings.kb_search_top_k
        )
        if error:
            return error

        if not hits:
            # 清空上一轮残留引用，并明确告知 Agent 没命中（避免它编造"知识库说"）
            ctx.citations = []
            return "知识库中没有检索到与该问题相关的内容。如确有把握可用通用知识简要回答，并明确说明这部分不来自平台知识库。"

        # 记录引用来源（前端展示用），并拼出回灌给 LLM 的资料文本
        ctx.citations = [
            {
                "document_id": h["document_id"],
                "title": h["title"],
                "seq": h["seq"],
                "similarity": h["similarity"],
            }
            for h in hits
        ]
        parts = []
        for h in hits:
            snippet = h["content"][:_TOOL_CHUNK_CHARS]
            parts.append(f"【来源：{h['title']}（第{h['seq']}块）】\n{snippet}")
        return "以下是知识库检索到的资料，请据此回答：\n\n" + "\n\n".join(parts)

    @tool
    def resume_lookup(query: str) -> str:
        """查询当前用户自己上传的简历：列出简历清单，或在简历正文里定位关键词所在片段。

        什么时候用：用户问"我上传了哪些简历""我的简历是什么状态"
        "我的简历里有没有提到某技能/项目/经历"。
        什么时候不用：要的是 AI 对简历的分析**结论**（目标岗位、优劣势、改进建议）
        请用 analysis_read；别人的简历一律查不到，通用知识问答请用 kb_search。
        入参 query 为要在简历正文里查的关键词；只列清单就传空字符串。"""
        owner = _owner_filter(Resume, user_id, anonymous_id)
        if owner is None:
            return "当前会话无法识别用户身份，查不到个人简历数据。请提示用户先登录后再提问。"

        try:
            rows = (
                db.scalars(
                    select(Resume)
                    .where(Resume.deleted_at.is_(None), owner)
                    .order_by(Resume.created_at.desc(), Resume.id.desc())
                    .limit(_TOOL_LIST_LIMIT)
                )
                .all()
            )
        except Exception:
            logger.exception("agent resume_lookup 查询失败")
            return "简历查询暂时出错，请稍后再试。"

        if not rows:
            return "该用户名下没有已上传的简历。可提示用户到首页上传一份 PDF 简历后再来提问。"

        keyword = (query or "").strip()
        parts = []
        for resume in rows:
            uploaded = resume.created_at.strftime("%Y-%m-%d") if resume.created_at else "未知"
            head = (
                f"【简历】{resume.filename}"
                f"（{resume.page_count or '?'} 页，解析状态 {resume.parse_status}，上传于 {uploaded}）"
            )
            if keyword:
                snippet = _snippet(resume.raw_text or "", keyword)
                head += (
                    f"\n  命中片段：{snippet}"
                    if snippet
                    else f"\n  正文中未找到与「{keyword}」相关的内容"
                )
            parts.append(head)
        return "以下是该用户自己的简历信息（仅本人可见）：\n" + "\n".join(parts)

    @tool
    def interview_history(limit: int = 3) -> str:
        """查询当前用户自己的模拟面试**单场**记录与结束评价：场次时间、岗位类型、
        进行状态、已进行轮次，以及技术深度/表达结构/项目真实性/整体表现四维评分与评语。

        什么时候用：用户问"我上次模拟面试多少分""我练了几场""某一场的评价是什么"。
        什么时候不用：要的是**跨场次**的进步/退步对比（分数趋势、上升下降）请用 score_trend；
        问"模拟面试功能怎么开始"（入口与流程）请用 platform_help。
        入参 limit 为返回的最近场次数，默认 3。"""
        owner = _owner_filter(InterviewSession, user_id, anonymous_id)
        if owner is None:
            return "当前会话无法识别用户身份，查不到个人面试记录。请提示用户先登录后再提问。"

        try:
            count = max(1, min(int(limit or 3), _TOOL_LIST_LIMIT))
            rows = (
                db.scalars(
                    select(InterviewSession)
                    .where(owner)
                    .order_by(InterviewSession.created_at.desc(), InterviewSession.id.desc())
                    .limit(count)
                )
                .all()
            )
        except Exception:
            logger.exception("agent interview_history 查询失败")
            return "面试记录查询暂时出错，请稍后再试。"

        if not rows:
            return "该用户名下暂无模拟面试记录。可提示用户到首页基于简历开始一场模拟面试。"

        status_labels = {
            "in_progress": "进行中",
            "finished": "已结束",
            "abandoned": "已放弃",
        }
        parts = [f"该用户最近 {len(rows)} 场模拟面试："]
        for index, session in enumerate(rows, start=1):
            when = session.created_at.strftime("%Y-%m-%d") if session.created_at else "未知"
            position = _POSITION_LABELS.get(
                session.position_type or "", _POSITION_DEFAULT
            )
            status = status_labels.get(session.status, session.status)
            parts.append(
                f"【第 {index} 场 · 最近在前】{when} · {position} · {status}"
                f" · 已进行 {session.turn_count or 0} 轮"
            )
            report = session.final_report_json
            if not isinstance(report, dict):
                parts.append("  尚未生成结束评价报告。")
                continue
            scores = [
                f"{label} {report[key]}"
                for key, label in _SCORE_LABELS.items()
                if isinstance(report.get(key), (int, float))
            ]
            if scores:
                parts.append("  评分（10 分制）：" + " / ".join(scores))
            summary = str(report.get("summary") or "").strip()
            if summary:
                parts.append(f"  评价：{summary[:200]}")
        return "\n".join(parts)

    @tool
    def score_trend(limit: int = 5) -> str:
        """查询当前用户自己历次模拟面试的分数趋势：按时间正序列出各场次的技术深度、
        表达结构、项目真实性、整体表现四项分数，并逐场给出相对上一场的上升/下降/持平。

        什么时候用：用户问"我进步了吗""面试分数有没有提升""最近表现变好还是变差"。
        什么时候不用：只想看**某一场**的记录与结束评价（多少分、评价说了什么）
        请用 interview_history；只完成一场时趋势无意义，不要强行解读。
        入参 limit 为纳入对比的最近已结束场次数，默认 5，最大 10。"""
        owner = _owner_filter(InterviewSession, user_id, anonymous_id)
        if owner is None:
            return "当前会话无法识别用户身份，查不到个人面试记录。请提示用户先登录后再提问。"

        try:
            want = int(limit or 0)
            want = 5 if want <= 0 else max(1, min(want, _TOOL_TREND_LIMIT))
            # 先取最近 N 场（倒序），渲染前再翻成正序——趋势要从早到晚看
            rows = list(
                db.scalars(
                    select(InterviewSession)
                    .where(owner, InterviewSession.status == "finished")
                    .order_by(
                        InterviewSession.created_at.desc(), InterviewSession.id.desc()
                    )
                    .limit(want)
                ).all()
            )
        except Exception:
            logger.exception("agent score_trend 查询失败")
            return "面试分数趋势查询暂时出错，请稍后再试。"

        # JSONB 列写入 None 会落成 JSON null 字面量，SQL 层的 is_not(None) 过滤不掉
        sessions = [s for s in rows if isinstance(s.final_report_json, dict)]
        sessions.reverse()
        if not sessions:
            return (
                "该用户名下暂无已完成的模拟面试，暂时看不出分数趋势。"
                "可提示用户到首页完成一场模拟面试后再来提问。"
            )

        head = (
            f"该用户最近 {len(sessions)} 场已完成模拟面试的分数趋势"
            "（10 分制，按时间正序）："
        )
        lines = [
            head,
            "（箭头为与上一场同维度对比：↑上升 ↓下降 →持平）",
        ]
        previous: dict | None = None
        previous_overall: float | None = None
        first_overall: float | None = None
        for index, session in enumerate(sessions, start=1):
            report = session.final_report_json or {}
            when = session.created_at.strftime("%m-%d") if session.created_at else "未知"
            if index == 1:
                mark = "基准场"
            else:
                mark = _trend_mark(previous_overall, _trend_overall(report))
            scores = []
            for key, label in _SCORE_LABELS.items():
                value = _score_of(report, key)
                if value is None:
                    continue
                before = _score_of(previous, key) if previous else None
                scores.append(f"{label} {value:g}{_trend_arrow(before, value)}")
            overall = _trend_overall(report)
            if index == 1:
                first_overall = overall
            text = " / ".join(scores) or "该场暂无评分数据"
            lines.append(f"【第 {index} 场 · {mark}】{when}：{text}")
            previous = report
            previous_overall = overall

        if len(sessions) == 1:
            lines.append("目前只有一场已完成的模拟面试，暂时看不出趋势，多练几场后再来看对比。")
        elif first_overall is not None and previous_overall is not None:
            delta = previous_overall - first_overall
            if delta > 0:
                verdict = f"上升 {delta:g} 分"
            elif delta < 0:
                verdict = f"下降 {abs(delta):g} 分"
            else:
                verdict = "基本持平"
            lines.append(
                f"总结：整体表现从首场 {first_overall:g} 到最近一场 {previous_overall:g}，{verdict}。"
            )
        return "\n".join(lines)

    @tool
    def usage_stats(days: int = 7) -> str:
        """统计当前用户自己在本平台的用量：近 N 天各动作（解析、分析、面试、问答等）
        的调用次数与 token 消耗合计。

        什么时候用：用户问"我用了多少次""消耗了多少 token""最近用得多不多"。
        什么时候不用：问的是"在哪能看到用量""使用日志页怎么筛选"（功能入口与界面说明）
        请用 platform_help；要的是简历/面试的**条数**请用 resume_lookup / interview_history。
        入参 days 为统计天数，默认 7，最大 90。"""
        owner = _owner_filter(UsageLog, user_id, anonymous_id)
        if owner is None:
            return "当前会话无法识别用户身份，查不到用量数据。请提示用户先登录后再提问。"

        try:
            span = max(1, min(int(days or 7), 90))
            since = datetime.now(timezone.utc) - timedelta(days=span)
            rows = db.execute(
                select(
                    UsageLog.action_type,
                    func.count(),
                    func.coalesce(func.sum(UsageLog.tokens_total), 0),
                )
                .where(owner, UsageLog.created_at >= since)
                .group_by(UsageLog.action_type)
                .order_by(func.count().desc())
            ).all()
        except Exception:
            logger.exception("agent usage_stats 查询失败")
            return "用量统计查询暂时出错，请稍后再试。"

        if not rows:
            return f"最近 {span} 天没有用量记录。"

        total_tokens = 0
        lines = [f"最近 {span} 天用量统计："]
        for action_type, count, tokens in rows:
            token_count = int(tokens or 0)
            total_tokens += token_count
            label = _ACTION_LABELS.get(action_type, action_type)
            lines.append(f"- {label}：{count} 次，{token_count} tokens")
        lines.append(f"合计消耗 {total_tokens} tokens。")
        return "\n".join(lines)

    @tool
    def analysis_read(resume_hint: str) -> str:
        """读取当前用户某份简历的 AI 分析**结论**：目标岗位、岗位匹配、优势、短板、
        关键词缺口、改进建议、预测面试题。

        什么时候用：用户问"我的分析报告说了什么""上次分析的结论/评价"
        "我简历的短板是什么""帮我看看该改哪些地方"。
        什么时候不用：要看简历**原文**（有没有写过某技能/项目/经历）请用 resume_lookup；
        要出新的分析或问"AI 分析怎么用"请用 platform_help。本工具只读已有结论，
        不生成新报告，也不要用自己的判断冒充报告内容。
        入参 resume_hint 是简历文件名或其片段；传空字符串表示最近上传的一份。"""
        owner = _owner_filter(Resume, user_id, anonymous_id)
        if owner is None:
            return "当前会话无法识别用户身份，查不到个人分析报告。请提示用户先登录后再提问。"

        try:
            rows = (
                db.scalars(
                    select(Resume)
                    .where(Resume.deleted_at.is_(None), owner)
                    .order_by(Resume.created_at.desc(), Resume.id.desc())
                )
                .all()
            )
        except Exception:
            logger.exception("agent analysis_read 简历查询失败")
            return "分析报告查询暂时出错，请稍后再试。"

        if not rows:
            return "该用户名下没有已上传的简历。可提示用户到首页上传一份 PDF 简历并做一次 AI 分析后再来提问。"

        # LLM 手上只有文件名（甚至只是记忆里的片段），不是主键，只能按文件名包含匹配
        hint = (resume_hint or "").strip()
        if hint:
            matched = [r for r in rows if hint.lower() in (r.filename or "").lower()]
            if not matched:
                names = "、".join(r.filename for r in rows[:_TOOL_LIST_LIMIT])
                return (
                    f"未找到文件名包含「{hint}」的简历。该用户名下实际有：{names}。"
                    "请让用户确认文件名后再问一次。"
                )
            target = matched[0]
        else:
            target = rows[0]

        try:
            analysis = latest_valid_analysis(db, target.id)
        except Exception:
            logger.exception("agent analysis_read 报告查询失败")
            return "分析报告查询暂时出错，请稍后再试。"

        report = analysis.result_json if analysis is not None else None
        if not isinstance(report, dict) or not report:
            return (
                f"简历「{target.filename}」还没有分析报告（或报告未通过校验）。"
                "可提示用户到报告页发起一次 AI 分析后再来提问。"
            )

        lines = [f"简历「{target.filename}」的 AI 分析结论："]
        position = str(report.get("target_position") or "").strip()
        if position:
            lines.append(f"目标岗位：{position}")
        match = str(report.get("position_match") or "").strip()
        if match:
            lines.append(f"岗位匹配：{match}")
        for key, label in (
            ("strengths", "优势"),
            ("weaknesses", "短板"),
            ("keyword_gaps", "关键词缺口"),
            ("suggestions", "改进建议"),
        ):
            items = _as_items(report.get(key), _TOOL_REPORT_ITEMS)
            if items:
                lines.append(f"{label}：" + "；".join(items))
        questions = _as_items(report.get("predicted_questions"), _TOOL_REPORT_QUESTIONS)
        if questions:
            lines.append("预测面试题：" + "；".join(questions))

        text = "\n".join(lines)
        if len(text) > _TOOL_REPORT_CHARS:
            return text[: _TOOL_REPORT_CHARS - 1] + "…"
        return text

    @tool
    def kb_list(query: str) -> str:
        """列出平台知识库里可见的**文档清单**：标题、切块数、入库状态、来源
        （平台预置 / 本人上传），不含任何正文内容。

        什么时候用：用户问"知识库里有哪些资料""平台收录了哪些文档""我能问哪些主题"。
        什么时候不用：要查某主题下的**具体知识点内容**（原理、用法、对比）请用 kb_search，
        本工具只给目录不回答知识问题；问"怎么上传知识库文档"（管理端操作）
        请用 platform_help。
        入参 query 为按标题过滤的关键词，传空字符串表示列出全部可见文档。"""
        try:
            docs = list_documents(db, user_id, anonymous_id)
            counts = count_chunks_by_document(db, [d.id for d in docs])
        except Exception:
            logger.exception("agent kb_list 查询失败")
            return "知识库文档清单查询暂时出错，请稍后再试。"

        if not docs:
            return "知识库当前还没有任何可查看的文档。可提示用户到知识库页上传资料。"

        keyword = (query or "").strip()
        if keyword:
            docs = [d for d in docs if keyword.lower() in (d.title or "").lower()]
            if not docs:
                return f"未找到标题包含「{keyword}」的文档。可提示用户换个关键词再问。"

        shown = docs[:_TOOL_KB_LIMIT]
        head = (
            f"知识库共有 {len(docs)} 篇可见文档"
            "（预置语料全站可见，用户上传的文档仅本人可见）："
        )
        lines = [head]
        for doc in shown:
            status = _KB_STATUS_LABELS.get(doc.status, doc.status)
            source = _KB_SCOPE_LABELS.get(doc.scope, doc.scope)
            lines.append(
                f"- 《{doc.title}》｜{source}｜{status}｜{counts.get(doc.id, 0)} 个知识块"
            )
        if len(docs) > _TOOL_KB_LIMIT:
            lines.append(f"（共 {len(docs)} 篇，以上仅显示前 {_TOOL_KB_LIMIT} 篇）")
        return "\n".join(lines)

    @tool
    def platform_help(topic: str) -> str:
        """查询**本平台自身**的功能怎么用：上传简历、AI 分析、模拟面试、在线对话、
        AI 客服、使用日志、个人中心、数据看板、语料库管理。直接返回内置说明，
        不查数据库也不调用模型。

        什么时候用：用户问"怎么上传简历""在哪看用量""模拟面试怎么开始"
        "AI 客服能做什么""这个网站有哪些功能"等**平台使用方式**问题。
        什么时候不用：问的是计算机技术知识点（编程语言、框架原理、数据库/网络/算法等）
        请用 kb_search 检索知识库；要查本人**真实数据**（简历、分析、面试、用量）
        请用 resume_lookup / analysis_read / interview_history / usage_stats。
        入参 topic 为想了解的功能名或一句口语化问题；传空字符串返回平台功能总览。"""
        return _platform_reply(topic)

    @tool
    def job_match(jd_text: str) -> str:
        """拿用户贴的岗位 JD（招聘要求）与其简历做匹配分析：整体匹配度评分、
        简历已命中的关键词、JD 要求但简历缺失的关键词、针对性改简历建议。
        本工具内部会调用一次 AI，受"工具内 AI 调用"的每日限额约束。

        什么时候用：用户贴了一段岗位 JD / 招聘要求，问"我匹配吗""这个岗位适合我吗"
        "我还缺什么""按这个 JD 我简历该怎么改"。
        什么时候不用：只问简历**原文**里有没有写过某技能/项目请用 resume_lookup；
        要读已有的 AI 分析结论请用 analysis_read（本工具是**现算**的 JD 匹配，不是读旧报告）；
        用户没贴 JD 或只是聊岗位前景时不要调用本工具。
        入参 jd_text 为岗位描述原文，超过 4000 字符会截断后分析。"""
        owner = _owner_filter(Resume, user_id, anonymous_id)
        if owner is None:
            return "当前会话无法识别用户身份，无法做岗位匹配。请提示用户先登录后再提问。"

        jd = (jd_text or "").strip()
        if not jd:
            return "请让用户把目标岗位的 JD（招聘要求/职位描述）贴进来，我才能做匹配分析。"

        # 用户可能整页粘贴，超长 JD 会撑爆上下文并让费用翻倍——截断后在输出里说明
        truncated = len(jd) > _TOOL_JD_CHARS
        if truncated:
            jd = jd[:_TOOL_JD_CHARS]

        try:
            resume = db.scalars(
                select(Resume)
                .where(Resume.deleted_at.is_(None), owner)
                .order_by(Resume.created_at.desc(), Resume.id.desc())
                .limit(1)
            ).first()
        except Exception:
            logger.exception("agent job_match 简历查询失败")
            return "简历查询暂时出错，请稍后再试。"

        if resume is None:
            return "需要先上传简历才能做岗位匹配。请提示用户到首页上传一份 PDF 简历后再来提问。"

        body = (resume.raw_text or "")[:_TOOL_MATCH_RESUME_CHARS]
        if not body.strip():
            return (
                f"简历「{resume.filename}」还没有解析出正文，无法做岗位匹配，"
                "请让用户重新上传一份文本型 PDF 简历。"
            )

        def _call():
            return chat_json(
                JOB_MATCH_SYSTEM_PROMPT,
                build_job_match_prompt(jd, body),
                settings,
                JobMatchReport.model_validate,
            )

        try:
            result, reply = _run_tool_llm(db, user_id, anonymous_id, _call)
        except Exception:
            logger.exception("agent job_match AI 调用失败")
            return "岗位匹配的 AI 服务暂时不可用（可能是服务欠费或超时），请稍后再试。"
        if reply is not None:  # 达到工具内 AI 调用上限
            return reply

        report = result.report if result is not None else None
        if not isinstance(report, dict):
            return "岗位匹配的 AI 返回结果无法解析，请让用户稍后再试一次。"

        lines = [
            f"简历「{resume.filename}」与该岗位的匹配分析：",
            f"整体匹配度：{report['match_score']}/100",
        ]
        for key, label in (
            ("matched_keywords", "已命中关键词"),
            ("missing_keywords", "缺失关键词"),
        ):
            items = _as_items(report.get(key), _TOOL_MATCH_ITEMS)
            if items:
                lines.append(f"{label}：" + "、".join(items))
        suggestions = _as_items(report.get("suggestions"), _TOOL_MATCH_ITEMS)
        if suggestions:
            lines.append("针对性建议：" + "；".join(suggestions))
        if truncated:
            lines.append(f"（JD 超过 {_TOOL_JD_CHARS} 字符，已按前 {_TOOL_JD_CHARS} 字符分析。）")
        return "\n".join(lines)

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
        subject = (topic or "").strip()
        if not subject:
            return "请让用户说明想练习哪个主题（例如 MySQL 索引、Redis 缓存、项目深挖），我才能出题。"

        level = _POSITION_LABELS.get(
            (position_type or "").strip().lower(), _POSITION_DEFAULT
        )

        hits, error = _kb_retrieve(
            db, subject, user_id, anonymous_id, settings.kb_search_top_k
        )
        used = hits[:_TOOL_QUESTION_CHUNKS]
        context = "\n\n".join(
            f"【来源：{h['title']}（第{h['seq']}块）】\n{h['content'][:_TOOL_CHUNK_CHARS]}"
            for h in used
        )

        def _call():
            return chat_json(
                QUESTION_GEN_SYSTEM_PROMPT,
                build_question_gen_prompt(subject, level, context),
                settings,
                QuestionGenReport.model_validate,
            )

        try:
            result, reply = _run_tool_llm(db, user_id, anonymous_id, _call)
        except Exception:
            logger.exception("agent question_gen AI 调用失败")
            return "出题的 AI 服务暂时不可用（可能是服务欠费或超时），请稍后再试。"
        if reply is not None:  # 达到工具内 AI 调用上限
            return reply

        report = result.report if result is not None else None
        questions = (
            _as_items(report.get("questions"), _TOOL_QUESTION_LIMIT)
            if isinstance(report, dict)
            else []
        )
        if not questions:
            return "出题的 AI 返回结果无法解析，请让用户稍后再试一次。"

        if used:
            head = f"主题「{subject}」的模拟面试题（难度定位：{level}；依据平台知识库出题）："
        else:
            # 未收录与检索失败都退回模型出题，必须在输出里说清题目不来自知识库
            reason = "平台知识库检索暂时不可用" if error else "平台知识库未收录该主题"
            head = f"{reason}，以下题目不来自平台知识库（难度定位：{level}）："
        lines = [head]
        lines.extend(f"{i}. {q}" for i, q in enumerate(questions, start=1))
        if used:
            titles = "、".join(dict.fromkeys(f"《{h['title']}》" for h in used))
            lines.append(
                f"（出题依据：{titles}。想看这些题怎么答，可以让我检索平台知识库。）"
            )
        return "\n".join(lines)

    @tool
    def answer_review(question: str, answer: str) -> str:
        """点评用户贴的一段**面试回答**：按技术深度、表达结构、项目真实性三项 10 分制打分，
        并给出 2~4 条具体改进建议。本工具内部会调用一次 AI，受"工具内 AI 调用"的每日限额约束。

        什么时候用：用户把自己的**回答原文**贴过来（"我这么答行不行""帮我看看这段回答"
        "我这样答能得几分"）——必须是**已有的一段回答**，题目与回答一起给最准。
        什么时候不用：要**出一组成题**请用 question_gen；要查某知识点的**标准答案与讲解**
        请用 kb_search；要读模拟面试**已生成的结束报告**请用 interview_history 或 score_trend。
        不要拿本工具的评价冒充平台已生成的面试报告。
        入参 question 为对应的面试题目；answer 为用户自己的回答，超过 2000 字符会截断后点评。"""
        title = (question or "").strip()
        body = (answer or "").strip()
        if not body:
            return "请让用户把他自己的回答贴进来（题目 + 他的回答），我才能点评。"
        if not title:
            return "请让用户把对应的面试题目一起发过来，我才知道该按什么标准点评这段回答。"

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

    return [
        kb_search,
        resume_lookup,
        interview_history,
        score_trend,
        usage_stats,
        analysis_read,
        kb_list,
        platform_help,
        job_match,
        question_gen,
        answer_review,
    ]
