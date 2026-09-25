"""Agent 能力层（v4.0 M1 下沉）：v1 工具闭包与 v2 图节点共用的同一份实现。

下沉原则（docs/Agent工具设计.md §三）：接口、v1 工具、v2 节点三方调同一份 service
函数，不复制 SQL / 提示词 / 限额记账。

能力函数统一返回 ``(用户话术, 产物 dict | None)``：
- 话术：面向用户/模型的可读文本，v1 工具直接把它返回给 Agent；
- 产物：结构化结果，v2 图节点写入 State；为 None 表示本次没有可用产物
  （话术即失败原因，已足够面向用户）。

本模块内容为从 tools.py 原样迁入（行为不变的搬运）：kb_retrieve / run_tool_llm /
as_items / job_match / question_gen 的核心逻辑与相关常量。
"""

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.resume import Resume
from app.schemas.agent import JobMatchReport, QuestionGenReport
from app.services.ai_client import chat_json
from app.services.embedding_service import embed_texts
from app.services.kb_service import search_chunks
from app.services.prompts import (
    JOB_MATCH_SYSTEM_PROMPT,
    QUESTION_GEN_SYSTEM_PROMPT,
    build_job_match_prompt,
    build_question_gen_prompt,
)
from app.services.usage_service import (
    acquire_limit_lock,
    count_today_usage_by_owner,
    write_usage,
)

logger = get_logger(__name__)
_T = TypeVar("_T")

# —— 常量（原 tools.py 迁入；带 _TOOL_ 旧名的仅为保持 v1 文档口径）——
JD_MAX_CHARS = 4000  # JD 原文截断上限（超长会撑爆上下文并让费用翻倍）
MATCH_RESUME_MAX_CHARS = 6000  # 参与匹配的简历正文中段上限
MATCH_ITEMS_LIMIT = 8  # 命中/缺失/建议清单的展示上限
CHUNK_CHARS = 300  # 检索块内容截断（kb_search 与出题上下文共用同一口径）
QUESTION_CHUNKS = 3  # 出题依据取检索命中前 N 块
QUESTION_LIMIT = 5  # 出题数量上限（schema 层 3~5，这里兜底截断）

TOOL_LLM_ACTION = "agent_tool_llm"
TOOL_LLM_LIMIT_REPLY = (
    "今日工具内 AI 调用已达上限，这个功能今天暂时用不了（次日 0 点自动恢复）。"
    "不要改用自己编造的内容代替，直接说明该功能今日次数已用完即可。"
)
KB_EMBED_ERROR = "向量模型暂时不可用（本地 Ollama 未就绪？），无法检索知识库。"
KB_SEARCH_ERROR = "知识库检索暂时出错，请稍后再试。"

_POSITION_LABELS = {"intern": "实习", "fresh": "校招", "senior": "社招"}
_POSITION_DEFAULT = "通用"


def as_items(value, limit: int) -> list[str]:
    """把报告里的数组字段拍平成非空字符串列表（模型偶尔会给单个字符串）。"""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    items = [str(item).strip() for item in value]
    return [item for item in items if item][:limit]


def kb_retrieve(
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
        return [], KB_EMBED_ERROR

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
        return [], KB_SEARCH_ERROR
    return hits, ""


def _tokens_total(result) -> int | None:
    prompt = getattr(result, "tokens_prompt", None)
    completion = getattr(result, "tokens_completion", None)
    if prompt is None and completion is None:
        return None
    return (prompt or 0) + (completion or 0)


def _record_tool_llm_usage(
    db: Session, user_id: int | None, anonymous_id: str | None, tokens: int | None
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
            anonymous_id=anonymous_id,
            user_id=user_id,
            action_type=TOOL_LLM_ACTION,
            model_name=settings.ai_model,
            tokens_total=tokens,
            ip_address=None,
        )
        db.commit()
    except Exception:  # 记账失败不能拖垮工具本身
        logger.warning("agent 工具内 LLM 用量记账失败", exc_info=True)
        db.rollback()


def run_tool_llm(
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
        # 原子化：与主循环限额同样的咨询锁，防并发突破工具内每日上限
        acquire_limit_lock(db, TOOL_LLM_ACTION, user_id, anonymous_id)
        used = count_today_usage_by_owner(
            db, TOOL_LLM_ACTION, user_id=user_id, anonymous_id=anonymous_id
        )
    except Exception:  # 限额查不到时按不可用处理，不让整轮 Agent 崩
        logger.exception("agent 工具内 LLM 限额查询失败")
        return None, "工具内 AI 调用暂时不可用，请稍后再试。"

    if used >= settings.daily_agent_tool_llm_limit:
        return None, TOOL_LLM_LIMIT_REPLY

    result = call()
    _record_tool_llm_usage(db, user_id, anonymous_id, _tokens_total(result))
    return result, None


def find_latest_resume(db: Session, user_id: int | None, anonymous_id: str | None):
    """定位归属者最新一份简历（load_resume 节点与 job_match 共用）；无身份返回 None。"""
    if user_id is None and not anonymous_id:
        return None
    cond = (
        Resume.user_id == user_id
        if user_id is not None
        else and_(Resume.user_id.is_(None), Resume.anonymous_id == anonymous_id)
    )
    return db.scalars(
        select(Resume)
        .where(Resume.deleted_at.is_(None), cond)
        .order_by(Resume.created_at.desc(), Resume.id.desc())
        .limit(1)
    ).first()


def run_job_match(
    db: Session, user_id: int | None, anonymous_id: str | None, jd_text: str
) -> tuple[str, dict | None]:
    """岗位匹配能力（v1 job_match 工具与 v2 matcher 节点共用）。

    返回 (话术, 匹配报告 dict)。报告含 match_score / matched_keywords /
    missing_keywords / suggestions；失败路径报告为 None。
    """
    owner_cond = None
    if user_id is not None:
        owner_cond = Resume.user_id == user_id
    elif anonymous_id:
        owner_cond = and_(Resume.user_id.is_(None), Resume.anonymous_id == anonymous_id)
    if owner_cond is None:
        return (
            "当前会话无法识别用户身份，无法做岗位匹配。请提示用户先登录后再提问。",
            None,
        )

    jd = (jd_text or "").strip()
    if not jd:
        return (
            "请让用户把目标岗位的 JD（招聘要求/职位描述）贴进来，我才能做匹配分析。",
            None,
        )

    # 用户可能整页粘贴，超长 JD 会撑爆上下文并让费用翻倍——截断后在输出里说明
    truncated = len(jd) > JD_MAX_CHARS
    if truncated:
        jd = jd[:JD_MAX_CHARS]

    try:
        resume = db.scalars(
            select(Resume)
            .where(Resume.deleted_at.is_(None), owner_cond)
            .order_by(Resume.created_at.desc(), Resume.id.desc())
            .limit(1)
        ).first()
    except Exception:
        logger.exception("agent job_match 简历查询失败")
        return "简历查询暂时出错，请稍后再试。", None

    if resume is None:
        return (
            "需要先上传简历才能做岗位匹配。请提示用户到首页上传一份 PDF 简历后再来提问。",
            None,
        )

    body = (resume.raw_text or "")[:MATCH_RESUME_MAX_CHARS]
    if not body.strip():
        return (
            f"简历「{resume.filename}」还没有解析出正文，无法做岗位匹配，"
            "请让用户重新上传一份文本型 PDF 简历。"
        ), None

    def _call():
        return chat_json(
            JOB_MATCH_SYSTEM_PROMPT,
            build_job_match_prompt(jd, body),
            settings,
            JobMatchReport.model_validate,
        )

    try:
        result, reply = run_tool_llm(db, user_id, anonymous_id, _call)
    except Exception:
        logger.exception("agent job_match AI 调用失败")
        return (
            "岗位匹配的 AI 服务暂时不可用（可能是服务欠费或超时），请稍后再试。",
            None,
        )
    if reply is not None:  # 达到工具内 AI 调用上限
        return reply, None

    report = result.report if result is not None else None
    if not isinstance(report, dict):
        return "岗位匹配的 AI 返回结果无法解析，请让用户稍后再试一次。", None

    lines = [
        f"简历「{resume.filename}」与该岗位的匹配分析：",
        f"整体匹配度：{report['match_score']}/100",
    ]
    for key, label in (
        ("matched_keywords", "已命中关键词"),
        ("missing_keywords", "缺失关键词"),
    ):
        items = as_items(report.get(key), MATCH_ITEMS_LIMIT)
        if items:
            lines.append(f"{label}：" + "、".join(items))
    suggestions = as_items(report.get("suggestions"), MATCH_ITEMS_LIMIT)
    if suggestions:
        lines.append("针对性建议：" + "；".join(suggestions))
    if truncated:
        lines.append(
            f"（JD 超过 {JD_MAX_CHARS} 字符，已按前 {JD_MAX_CHARS} 字符分析。）"
        )
    return "\n".join(lines), report


def run_question_generation(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    topic: str,
    position_type: str,
) -> tuple[str, dict | None]:
    """按岗位出题能力（v1 question_gen 工具与 v2 questioner 节点共用）。

    返回 (话术, 产物 dict)。产物含 questions / level / kb_backed / sources；
    失败路径产物为 None。
    """
    subject = (topic or "").strip()
    if not subject:
        return (
            "请让用户说明想练习哪个主题（例如 MySQL 索引、Redis 缓存、项目深挖），我才能出题。",
            None,
        )

    level = _POSITION_LABELS.get(
        (position_type or "").strip().lower(), _POSITION_DEFAULT
    )

    hits, error = kb_retrieve(
        db, subject, user_id, anonymous_id, settings.kb_search_top_k
    )
    used = hits[:QUESTION_CHUNKS]
    context = "\n\n".join(
        f"【来源：{h['title']}（第{h['seq']}块）】\n{h['content'][:CHUNK_CHARS]}"
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
        result, reply = run_tool_llm(db, user_id, anonymous_id, _call)
    except Exception:
        logger.exception("agent question_gen AI 调用失败")
        return "出题的 AI 服务暂时不可用（可能是服务欠费或超时），请稍后再试。", None
    if reply is not None:  # 达到工具内 AI 调用上限
        return reply, None

    report = result.report if result is not None else None
    questions = (
        as_items(report.get("questions"), QUESTION_LIMIT)
        if isinstance(report, dict)
        else []
    )
    if not questions:
        return "出题的 AI 返回结果无法解析，请让用户稍后再试一次。", None

    if used:
        head = (
            f"主题「{subject}」的模拟面试题（难度定位：{level}；依据平台知识库出题）："
        )
    else:
        # 未收录与检索失败都退回模型出题，必须在输出里说清题目不来自知识库
        reason = "平台知识库检索暂时不可用" if error else "平台知识库未收录该主题"
        head = f"{reason}，以下题目不来自平台知识库（难度定位：{level}）："
    lines = [head]
    lines.extend(f"{i}. {q}" for i, q in enumerate(questions, start=1))
    sources: list[str] = []
    if used:
        titles = "、".join(dict.fromkeys(f"《{h['title']}》" for h in used))
        lines.append(
            f"（出题依据：{titles}。想看这些题怎么答，可以让我检索平台知识库。）"
        )
        sources = list(dict.fromkeys(h["title"] for h in used))
    return "\n".join(lines), {
        "questions": questions,
        "level": level,
        "kb_backed": bool(used),
        "sources": sources,
    }
