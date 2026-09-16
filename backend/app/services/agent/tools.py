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

工具清单：
1. kb_search          查平台技术知识库（RAG 主链路，唯一会回填 citations 的工具）
2. resume_lookup      查当前用户自己的简历（有哪些、正文里有没有提到某关键词）
3. interview_history  查当前用户自己的模拟面试记录与分维度评分
4. score_trend        查当前用户自己历次模拟面试的分数趋势（逐场对比升降）
5. usage_stats        查当前用户自己的平台用量（近 N 天各动作次数与 token）
6. analysis_read      读当前用户某份简历的 AI 分析结论（复用 analysis_service 的查询）
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from langchain_core.tools import BaseTool, tool
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.interview import InterviewSession
from app.models.resume import Resume
from app.models.usage_log import UsageLog
from app.services.analysis_service import latest_valid_analysis
from app.services.embedding_service import embed_texts
from app.services.kb_service import search_chunks
from app.services.lexical_service import tokenize

logger = get_logger(__name__)

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
}

# 面试结束评价的分维度中文名（与 interview_prompts 的 JSON 结构一一对应）
_SCORE_LABELS = {
    "technical_depth": "技术深度",
    "communication": "表达结构",
    "project_authenticity": "项目真实性",
    "overall": "整体表现",
}


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


def make_tools(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    ctx: ToolContext,
) -> list[BaseTool]:
    """构造本次请求专属的工具列表。"""

    @tool
    def kb_search(query: str) -> str:
        """检索平台技术知识库。当用户询问计算机技术、编程语言、Java/JVM/并发、MySQL/Redis、
        计算机网络、操作系统、RAG/AI 应用开发等具体知识点时，必须先调用本工具获取权威资料，
        再基于检索结果回答。入参 query 为精简后的检索关键词或问题。"""
        try:
            vectors = embed_texts([query])
        except Exception:
            logger.warning(
                "agent kb_search embedding 失败，走无检索兜底", exc_info=True
            )
            return "知识库检索服务（向量模型）暂时不可用，请基于你已有的通用知识谨慎回答，并说明未检索平台知识库。"

        try:
            hits = search_chunks(
                db,
                vectors[0],
                user_id,
                anonymous_id,
                top_k=settings.kb_search_top_k,
                # v3.5：工具入参原文一并交给检索层，走向量 + BM25 混合检索
                query_text=query,
            )
        except Exception:
            logger.exception("agent kb_search 检索失败")
            return "知识库检索暂时出错，请基于你已有的通用知识谨慎回答，并说明未检索平台知识库。"

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
        """查询当前用户自己上传的简历。当用户问"我上传了哪些简历""我的简历情况"
        "我的简历里有没有提到某技能/项目/经历"时调用。
        入参 query 为要在简历正文里查的关键词；若只想列出简历清单，传空字符串即可。"""
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
        """查询当前用户自己的模拟面试记录与结束评价。当用户问"我面试表现怎么样"
        "上次模拟面试多少分""我练了几场"时调用。入参 limit 为返回的最近场次数，默认 3。"""
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
        position_labels = {"intern": "实习", "fresh": "校招", "senior": "社招"}
        parts = [f"该用户最近 {len(rows)} 场模拟面试："]
        for index, session in enumerate(rows, start=1):
            when = session.created_at.strftime("%Y-%m-%d") if session.created_at else "未知"
            position = position_labels.get(session.position_type or "", "通用")
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
        当用户问"我进步了吗""面试分数有没有提升""最近表现变好还是变差"时调用。
        入参 limit 为纳入对比的最近已结束场次数，默认 5，最大 10。
        本工具给的是**跨场次的趋势对比**；只想看某一场的记录与结束评价请用 interview_history。"""
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
        """查询当前用户自己在本平台的用量统计。当用户问"我用了多少次""消耗了多少
        token""最近用得多不多"时调用。入参 days 为统计天数，默认 7，最大 90。"""
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
        """读取当前用户某份简历的 AI 分析**结论**（目标岗位、岗位匹配、优势、短板、
        关键词缺口、改进建议、预测面试题）。当用户问"我的分析报告说了什么"
        "上次分析的结论/评价""我简历的短板是什么"时调用。
        入参 resume_hint 是简历文件名或其片段；传空字符串表示最近上传的一份。
        本工具给的是 AI 对简历的**结论**；要看简历**原文**（有没有写过某技能/项目）
        请用 resume_lookup。平台功能使用问题请用 platform_help，不要用本工具。"""
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

    return [
        kb_search,
        resume_lookup,
        interview_history,
        score_trend,
        usage_stats,
        analysis_read,
    ]
