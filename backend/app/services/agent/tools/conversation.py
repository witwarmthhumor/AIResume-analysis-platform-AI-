"""对话与用量域工具：conversation_search（历史对话检索）/ usage_stats（用量统计）。"""

from datetime import datetime, timedelta, timezone

from langchain_core.tools import BaseTool, tool
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.chat import ChatMessage, ChatSession
from app.models.usage_log import UsageLog
from app.services.agent.tools.common import (
    _ACTION_LABELS,
    _SESSION_SOURCE_LABELS,
    _TOOL_CONV_LIMIT,
    _TOOL_SNIPPET_WIDTH,
    ToolContext,
    _owner_filter,
    _snippet,
)

logger = get_logger(__name__)


def _conv_head(session: ChatSession) -> str:
    """历史对话每行的头部：来源类型 + 会话标题 + 会话时间（不含任何正文/引用）。"""
    source = _SESSION_SOURCE_LABELS.get(session.session_type, session.session_type)
    stamp = session.updated_at or session.created_at
    when = stamp.strftime("%Y-%m-%d %H:%M") if stamp else "时间未知"
    return f"【来源：{source}】{session.title}（{when}）"


def build_conversation_search(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    ctx: ToolContext,
) -> BaseTool:
    """构造 conversation_search 工具（闭包绑定本次请求的会话与归属者，防串数据）。"""

    @tool
    def conversation_search(keyword: str, limit: int = 5) -> str:
        """在当前用户**自己过去的对话**里按关键词检索：在在线对话与 AI 客服的历史消息中
        找出命中片段，并标注每条来自哪一类会话。

        什么时候用：用户想找回"之前聊过的内容"——"我之前问过你什么""上次聊到哪了"
        "帮我在历史对话里找找关于某主题的记录""我们之前是不是聊过某个问题"。
        什么时候不用：问"本平台某个功能怎么用、入口在哪"请用 platform_help；
        要查计算机技术知识点的答案与讲解请用 kb_search（本工具只在**用户自己的历史对话**
        里找，不检索知识库、不回答技术问题，也不回灌当时引用的来源与工具过程）。
        入参 keyword 为要检索的关键词；传空字符串则列出最近几场会话的标题与时间。
        limit 为返回条数上限，默认 5，最大 5。"""
        owner = _owner_filter(ChatSession, user_id, anonymous_id)
        if owner is None:
            return "当前会话无法识别用户身份，查不到个人对话记录。请提示用户先登录后再提问。"

        try:
            count = max(1, min(int(limit or _TOOL_CONV_LIMIT), _TOOL_CONV_LIMIT))
        except (TypeError, ValueError):
            count = _TOOL_CONV_LIMIT

        keyword_kw = (keyword or "").strip()
        try:
            if keyword_kw:
                # 消息 → 会话 JOIN：既按归属者过滤，又排除软删除会话
                rows = db.execute(
                    select(ChatMessage, ChatSession)
                    .join(ChatSession, ChatMessage.session_id == ChatSession.id)
                    .where(
                        owner,
                        ChatSession.deleted_at.is_(None),
                        ChatMessage.content.ilike(f"%{keyword_kw}%"),
                    )
                    .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                    .limit(count)
                ).all()
            else:
                sessions = db.scalars(
                    select(ChatSession)
                    .where(owner, ChatSession.deleted_at.is_(None))
                    .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
                    .limit(count)
                ).all()
        except Exception:
            logger.exception("agent conversation_search 查询失败")
            return "历史对话检索暂时出错，请稍后再试。"

        if keyword_kw:
            if not rows:
                return (
                    f"没有搜到与「{keyword_kw}」相关的历史对话。"
                    "可提示用户换个关键词再试，或传空关键词列出最近的会话。"
                )
            parts = [f"在历史对话中检索到 {len(rows)} 条与「{keyword_kw}」相关的消息："]
            for message, session in rows:
                body = message.content or ""
                # 只取片段：citations / tool_steps 等过程数据一律不回灌
                snippet = _snippet(body, keyword_kw) or body[:_TOOL_SNIPPET_WIDTH]
                parts.append(f"- {_conv_head(session)}：{snippet}")
            return "\n".join(parts)

        if not sessions:
            return "该用户名下暂无历史对话记录。可提示用户到在线对话或 AI 客服聊几句后再来提问。"
        parts = [f"该用户最近 {len(sessions)} 场历史对话："]
        for session in sessions:
            parts.append(f"- {_conv_head(session)}")
        return "\n".join(parts)

    return conversation_search


def build_usage_stats(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    ctx: ToolContext,
) -> BaseTool:
    """构造 usage_stats 工具（闭包绑定本次请求的会话与归属者，防串数据）。"""

    @tool
    def usage_stats(days: int = 7, action: str = "") -> str:
        """统计当前用户自己在本平台的用量：近 N 天各动作（解析、分析、面试、问答等）
        的调用次数与 token 消耗合计。

        什么时候用：用户问"我用了多少次""消耗了多少 token""最近用得多不多"，
        或想只看**某一类动作**（如"我这周光面试花了多少 token""分析消耗了多少"）。
        什么时候不用：问的是"在哪能看到用量""使用日志页怎么筛选"（功能入口与界面说明）
        请用 platform_help；要的是简历/面试的**条数**请用 resume_lookup / interview_history。
        入参 days 为统计天数，默认 7，最大 90；action 为可选的动作类型过滤，
        留空表示统计全部动作。action 合法取值（英文枚举）：parse（简历解析）、
        analysis（AI 简历分析）、interview_message（模拟面试对话）、kb_upload（知识库上传）、
        playground（在线对话问答）、chat_create（新建对话）、agent（AI 客服问答）、
        agent_create（新建客服会话）、agent_tool_llm（工具内 AI 调用）。"""
        owner = _owner_filter(UsageLog, user_id, anonymous_id)
        if owner is None:
            return (
                "当前会话无法识别用户身份，查不到用量数据。请提示用户先登录后再提问。"
            )

        wanted = (action or "").strip()
        if wanted and wanted not in _ACTION_LABELS:
            available = "、".join(_ACTION_LABELS.values())
            return f"没有「{wanted}」这个动作类型。可用动作有：{available}。"

        try:
            span = max(1, min(int(days or 7), 90))
            since = datetime.now(timezone.utc) - timedelta(days=span)
            conditions = [owner, UsageLog.created_at >= since]
            if wanted:
                conditions.append(UsageLog.action_type == wanted)
            rows = db.execute(
                select(
                    UsageLog.action_type,
                    func.count(),
                    func.coalesce(func.sum(UsageLog.tokens_total), 0),
                )
                .where(*conditions)
                .group_by(UsageLog.action_type)
                .order_by(func.count().desc())
            ).all()
        except Exception:
            logger.exception("agent usage_stats 查询失败")
            return "用量统计查询暂时出错，请稍后再试。"

        if not rows:
            if wanted:
                return f"最近 {span} 天「{_ACTION_LABELS[wanted]}」没有记录。"
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

    return usage_stats
