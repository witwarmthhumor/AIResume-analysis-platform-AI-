"""面试域工具：interview_history（单场结果）/ interview_transcript（问答原文）/
score_trend（跨场次趋势）。"""

from langchain_core.tools import BaseTool, tool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.interview import InterviewMessage, InterviewSession
from app.services.agent.tools.common import (
    _INTERVIEW_ROLE_LABELS,
    _INTERVIEW_STATUS_LABELS,
    _SCORE_LABELS,
    _TOOL_LIST_LIMIT,
    _TOOL_TRANSCRIPT_CHARS,
    _TOOL_TRANSCRIPT_LIMIT,
    _TOOL_TREND_LIMIT,
    ToolContext,
    _owner_filter,
)
from app.services.agent_capabilities import _POSITION_LABELS

logger = get_logger(__name__)

_POSITION_DEFAULT = "通用"


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


def build_interview_history(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    ctx: ToolContext,
) -> BaseTool:
    """构造 interview_history 工具（闭包绑定本次请求的会话与归属者，防串数据）。"""

    @tool
    def interview_history(limit: int = 3) -> str:
        """查询当前用户自己的模拟面试**单场**记录与结束评价：场次时间、岗位类型、
        进行状态、已进行轮次，以及技术深度/表达结构/项目真实性/整体表现四维评分与评语。

        什么时候用：用户问"我上次模拟面试多少分""我练了几场""某一场的评价是什么"。
        什么时候不用：要的是**跨场次**的进步/退步对比（分数趋势、上升下降）请用 score_trend；
        要复盘**问答过程原文**（当时问了什么、用户怎么答的）请用 interview_transcript——
        本工具只给**结果**（分数与评价），不给问答原文；
        问"模拟面试功能怎么开始"（入口与流程）请用 platform_help。
        入参 limit 为返回的最近场次数，默认 3。"""
        owner = _owner_filter(InterviewSession, user_id, anonymous_id)
        if owner is None:
            return "当前会话无法识别用户身份，查不到个人面试记录。请提示用户先登录后再提问。"

        try:
            count = max(1, min(int(limit or 3), _TOOL_LIST_LIMIT))
            rows = db.scalars(
                select(InterviewSession)
                .where(owner)
                .order_by(
                    InterviewSession.created_at.desc(), InterviewSession.id.desc()
                )
                .limit(count)
            ).all()
        except Exception:
            logger.exception("agent interview_history 查询失败")
            return "面试记录查询暂时出错，请稍后再试。"

        if not rows:
            return (
                "该用户名下暂无模拟面试记录。可提示用户到首页基于简历开始一场模拟面试。"
            )

        status_labels = _INTERVIEW_STATUS_LABELS
        parts = [f"该用户最近 {len(rows)} 场模拟面试："]
        for index, session in enumerate(rows, start=1):
            when = (
                session.created_at.strftime("%Y-%m-%d")
                if session.created_at
                else "未知"
            )
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

    return interview_history


def build_interview_transcript(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    ctx: ToolContext,
) -> BaseTool:
    """构造 interview_transcript 工具（闭包绑定本次请求的会话与归属者，防串数据）。"""

    @tool
    def interview_transcript(session_id: int | None = None, limit: int = 10) -> str:
        """复述当前用户某场模拟面试的**问答原文**：按时间正序列出面试官问了什么、
        用户当时怎么答的，用于复盘面试**过程**。

        什么时候用：用户想回看自己**当时的问答内容**——"我上次面试都问了什么"
        "我当时那道题是怎么答的""帮我把上次面试的问答原文调出来"。
        什么时候不用：要的是面试的**结果**（多少分、评价说了什么、练了几场、进步趋势）
        请用 interview_history 或 score_trend——本工具只给过程原文、不给评分与评价；
        问"模拟面试怎么开始"（入口与流程）请用 platform_help。
        入参 session_id 为面试场次 id，省略则取最近一场；limit 为返回的最近消息条数，
        默认 10，范围 2~20。"""
        owner = _owner_filter(InterviewSession, user_id, anonymous_id)
        if owner is None:
            return "当前会话无法识别用户身份，查不到个人面试记录。请提示用户先登录后再提问。"

        try:
            want = int(limit or 0)
        except (TypeError, ValueError):
            want = 0
        want = 10 if want <= 0 else max(2, min(want, _TOOL_TRANSCRIPT_LIMIT))

        try:
            if session_id is not None:
                # 指定场次必须归属校验：不属于本人或不存在，查询结果都为空
                session = db.scalars(
                    select(InterviewSession).where(
                        InterviewSession.id == int(session_id), owner
                    )
                ).first()
            else:
                session = db.scalars(
                    select(InterviewSession)
                    .where(owner)
                    .order_by(
                        InterviewSession.created_at.desc(), InterviewSession.id.desc()
                    )
                    .limit(1)
                ).first()
        except Exception:
            logger.exception("agent interview_transcript 场次查询失败")
            return "面试问答记录查询暂时出错，请稍后再试。"

        if session is None:
            if session_id is not None:
                # 不区分"不存在"与"不属于你"，避免泄露他人场次的存在性
                return (
                    "没找到这场面试，无法调出问答原文。"
                    "可提示用户确认场次，或省略 session_id 直接查看最近一场。"
                )
            return (
                "该用户名下暂无模拟面试记录。可提示用户到首页基于简历开始一场模拟面试。"
            )

        try:
            # 取最近 limit 条（倒序），渲染前再翻成正序——问答要从先到后读
            messages = list(
                db.scalars(
                    select(InterviewMessage)
                    .where(InterviewMessage.session_id == session.id)
                    .order_by(
                        InterviewMessage.created_at.desc(), InterviewMessage.id.desc()
                    )
                    .limit(want)
                ).all()
            )
        except Exception:
            logger.exception("agent interview_transcript 消息查询失败")
            return "面试问答记录查询暂时出错，请稍后再试。"

        when = (
            session.created_at.strftime("%Y-%m-%d %H:%M")
            if session.created_at
            else "时间未知"
        )
        position = _POSITION_LABELS.get(session.position_type or "", _POSITION_DEFAULT)
        status = _INTERVIEW_STATUS_LABELS.get(session.status, session.status)
        head = (
            f"该用户一场模拟面试的问答原文（{when} · {position} · {status}"
            f" · 已进行 {session.turn_count or 0} 轮，按时间正序）："
        )
        if not messages:
            return head + "\n该场面试暂无问答消息。可提示用户到模拟面试页先开始对话。"

        messages.reverse()
        lines = [head]
        for message in messages:
            role = _INTERVIEW_ROLE_LABELS.get(message.role, message.role)
            lines.append(f"【{role}】{message.content or ''}")

        text = "\n".join(lines)
        if len(text) > _TOOL_TRANSCRIPT_CHARS:
            notice = (
                f"\n（问答原文较长，已截断至 {_TOOL_TRANSCRIPT_CHARS} 字符，"
                f"仅展示该场最近 {want} 条中的前一部分。）"
            )
            text = text[:_TOOL_TRANSCRIPT_CHARS] + notice
        return text

    return interview_transcript


def build_score_trend(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    ctx: ToolContext,
) -> BaseTool:
    """构造 score_trend 工具（闭包绑定本次请求的会话与归属者，防串数据）。"""

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
            when = (
                session.created_at.strftime("%m-%d") if session.created_at else "未知"
            )
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
            lines.append(
                "目前只有一场已完成的模拟面试，暂时看不出趋势，多练几场后再来看对比。"
            )
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

    return score_trend
