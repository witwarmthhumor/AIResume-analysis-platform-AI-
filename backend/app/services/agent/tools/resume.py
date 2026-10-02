"""简历域工具：resume_lookup（简历原文定位）/ analysis_read（分析结论）。"""

from langchain_core.tools import BaseTool, tool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.resume import Resume
from app.services.agent.tools.common import (
    _TOOL_LIST_LIMIT,
    _TOOL_REPORT_CHARS,
    _TOOL_REPORT_ITEMS,
    _TOOL_REPORT_QUESTIONS,
    ToolContext,
    _owner_filter,
    _snippet,
)
from app.services.agent_capabilities import as_items as _as_items
from app.services.analysis_service import latest_valid_analysis

logger = get_logger(__name__)


def build_resume_lookup(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    ctx: ToolContext,
) -> BaseTool:
    """构造 resume_lookup 工具（闭包绑定本次请求的会话与归属者，防串数据）。"""

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

        query_kw = (query or "").strip()
        try:
            base = (
                select(Resume)
                .where(Resume.deleted_at.is_(None), owner)
                .order_by(Resume.created_at.desc(), Resume.id.desc())
            )
            rows = db.scalars(base.limit(_TOOL_LIST_LIMIT)).all()
        except Exception:
            logger.exception("agent resume_lookup 查询失败")
            return "简历查询暂时出错，请稍后再试。"

        if not rows:
            return "该用户名下没有已上传的简历。可提示用户到首页上传一份 PDF 简历后再来提问。"

        if query_kw:
            # 关键词在 SQL 端过滤，且不限 _TOOL_LIST_LIMIT：原先"先取 5 份再在正文里
            # 匹配"会漏掉第 5 份之后的简历命中，误导模型答复"正文中未找到"
            try:
                rows = db.scalars(
                    base.where(Resume.raw_text.ilike(f"%{query_kw}%"))
                ).all()
            except Exception:
                logger.exception("agent resume_lookup 关键词查询失败")
                return "简历查询暂时出错，请稍后再试。"
            if not rows:
                return (
                    f"未找到与「{query_kw}」相关的内容：名下简历正文中没有匹配项。"
                    "可提示用户确认关键词，或用空 query 先列出简历清单。"
                )

        keyword = (query or "").strip()
        parts = []
        for resume in rows:
            uploaded = (
                resume.created_at.strftime("%Y-%m-%d") if resume.created_at else "未知"
            )
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

    return resume_lookup


def build_analysis_read(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    ctx: ToolContext,
) -> BaseTool:
    """构造 analysis_read 工具（闭包绑定本次请求的会话与归属者，防串数据）。"""

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
            # 只取 id+filename：无 limit 全量拉含 raw_text 大字段的实体只为文件名匹配，太重
            rows = db.execute(
                select(Resume.id, Resume.filename)
                .where(Resume.deleted_at.is_(None), owner)
                .order_by(Resume.created_at.desc(), Resume.id.desc())
            ).all()
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

    return analysis_read
