"""v3.5 Agent 工具测试：resume_lookup / interview_history / usage_stats / analysis_read /
kb_list / platform_help / job_match / question_gen / answer_review。

只查"当前归属者"自己的数据是个人数据工具的核心安全属性，因此重点覆盖：
- 无身份时明确拒绝（而不是返回空结果让模型脑补）
- 归属隔离（查不到别人的简历/面试/用量/分析报告/知识库上传）
- 正常路径的输出内容与聚合口径

数据库隔离策略：所有测试数据用专属 anonymous_id 标记，只清自己造的数据，
不动开发库里的其他记录（与 test_rag 清表策略不同，避免误删本地数据）。
知识库的 scope=public 测试文档两者皆空（与预置语料同形），只能按标题前缀清理。
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.models.analysis import Analysis
from app.models.interview import InterviewSession
from app.models.kb import KBChunk, KBDocument
from app.models.resume import Resume
from app.models.usage_log import UsageLog
from app.schemas.agent import AnswerReviewReport, JobMatchReport, QuestionGenReport
from app.services.agent import ToolContext, make_tools
from app.services.agent.tools import (
    _TOOL_ANSWER_CHARS,
    _TOOL_JD_CHARS,
    _TOOL_LLM_ACTION,
    _TOOL_LLM_LIMIT_REPLY,
    _run_tool_llm,
)
from app.services.ai_client import AIError, AnalysisResult
from app.services.prompts import (
    ANSWER_REVIEW_SYSTEM_PROMPT,
    JOB_MATCH_SYSTEM_PROMPT,
    PROMPT_VERSION,
    QUESTION_GEN_SYSTEM_PROMPT,
)

_OWNER = "agent_tool_test_owner"
_OTHER = "agent_tool_test_other"
# 测试知识库文档的标题前缀：public 文档归属为空，只能靠前缀把自己的数据清掉
_KB_PREFIX = "agentkb_"

_TABLES = ("analyses", "resumes", "interview_sessions", "usage_logs")


def _purge() -> None:
    with engine.begin() as conn:
        for table in _TABLES:
            conn.execute(
                text(f"DELETE FROM {table} WHERE anonymous_id IN (:a, :b)"),
                {"a": _OWNER, "b": _OTHER},
            )
        params = {"a": _OWNER, "b": _OTHER, "p": f"{_KB_PREFIX}%"}
        chunk_delete = (
            "DELETE FROM kb_chunks WHERE document_id IN"
            " (SELECT id FROM kb_documents"
            " WHERE anonymous_id IN (:a, :b) OR title LIKE :p)"
        )
        doc_delete = (
            "DELETE FROM kb_documents WHERE anonymous_id IN (:a, :b) OR title LIKE :p"
        )
        for statement in (chunk_delete, doc_delete):
            conn.execute(text(statement), params)


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clean_test_rows():
    _purge()
    yield
    _purge()


def _tool(db, name: str):
    """按名字取工具（顺序由 make_tools 决定，不依赖下标）。"""
    tools = make_tools(db, None, _OWNER, ToolContext())
    return next(t for t in tools if t.name == name)


def _add_resume(db, anonymous_id: str, filename: str, raw_text: str) -> Resume:
    resume = Resume(
        anonymous_id=anonymous_id,
        filename=filename,
        file_hash=f"hash-{anonymous_id}-{filename}",
        storage_path=f"uploads/{filename}",
        raw_text=raw_text,
        page_count=2,
        file_size=1024,
        parse_status="success",
    )
    db.add(resume)
    db.commit()
    return resume


# —— 工具集装配 ——


def test_make_tools_exposes_eleven_tools(db_session) -> None:
    tools = make_tools(db_session, None, _OWNER, ToolContext())
    assert [t.name for t in tools] == [
        "kb_search",
        "resume_lookup",
        "interview_history",
        "score_trend",
        "usage_stats",
        "analysis_read",
        "kb_list",
        "platform_help",
        "job_match",
        "question_gen",
        "answer_review",
    ]


def test_personal_tools_reject_unknown_identity(db_session) -> None:
    """无法识别身份（无 user_id 也无 anonymous_id）时个人工具都要明确拒绝。

    kb_search / platform_help 不在此列：前者只返回公共语料，后者是静态文案，
    都不泄露任何个人数据，新访客也应能用。
    """
    tools = make_tools(db_session, None, None, ToolContext())

    def call(name: str, payload: dict) -> str:
        return next(t for t in tools if t.name == name).invoke(payload)

    assert "无法识别用户身份" in call("resume_lookup", {"query": ""})
    assert "无法识别用户身份" in call("interview_history", {"limit": 3})
    assert "无法识别用户身份" in call("score_trend", {"limit": 5})
    assert "无法识别用户身份" in call("usage_stats", {"days": 7})
    assert "无法识别用户身份" in call("analysis_read", {"resume_hint": ""})
    assert "无法识别用户身份" in call("job_match", {"jd_text": "招 Java 后端"})


# —— resume_lookup ——


def test_resume_lookup_returns_own_resume_with_snippet(db_session) -> None:
    db = db_session
    _add_resume(db, _OWNER, "我的简历.pdf", "负责聚簇索引优化，查询 QPS 提升 3 倍。")

    out = _tool(db, "resume_lookup").invoke({"query": "聚簇索引"})

    assert "我的简历.pdf" in out
    assert "聚簇索引" in out  # 命中片段被摘出来
    assert "2 页" in out


def test_resume_lookup_reports_no_keyword_hit(db_session) -> None:
    db = db_session
    _add_resume(db, _OWNER, "我的简历.pdf", "负责服务端接口开发。")

    out = _tool(db, "resume_lookup").invoke({"query": "聚簇索引"})

    assert "未找到" in out


def test_resume_lookup_empty_for_new_owner(db_session) -> None:
    out = _tool(db_session, "resume_lookup").invoke({"query": ""})
    assert "没有已上传的简历" in out


def test_resume_lookup_isolates_other_owner(db_session) -> None:
    """别人的简历不可见：查不到就明确说没有，不能泄露他人数据。"""
    db = db_session
    _add_resume(db, _OTHER, "别人的简历.pdf", "聚簇索引是我做的。")

    out = _tool(db, "resume_lookup").invoke({"query": "聚簇索引"})

    assert "没有已上传的简历" in out
    assert "别人的简历.pdf" not in out


# —— interview_history ——


def _add_session(db, anonymous_id: str, **kwargs) -> InterviewSession:
    session = InterviewSession(
        resume_id=999999,  # 逻辑关联，不建外键，无需真实简历
        anonymous_id=anonymous_id,
        **kwargs,
    )
    db.add(session)
    db.commit()
    return session


def test_interview_history_renders_scores_in_chinese(db_session) -> None:
    db = db_session
    _add_session(
        db,
        _OWNER,
        status="finished",
        stage="wrapup",
        turn_count=8,
        position_type="senior",
        final_report_json={
            "technical_depth": 7,
            "communication": 6,
            "project_authenticity": 8,
            "overall": 7,
            "summary": "基础扎实，表达可再精炼。",
        },
    )

    out = _tool(db, "interview_history").invoke({"limit": 3})

    assert "社招" in out
    assert "已结束" in out
    assert "技术深度 7" in out and "项目真实性 8" in out
    assert "基础扎实" in out


def test_interview_history_handles_missing_report(db_session) -> None:
    db = db_session
    _add_session(db, _OWNER, status="in_progress", stage="technical", turn_count=3)

    out = _tool(db, "interview_history").invoke({"limit": 3})

    assert "进行中" in out
    assert "尚未生成结束评价报告" in out


def test_interview_history_empty_for_new_owner(db_session) -> None:
    out = _tool(db_session, "interview_history").invoke({"limit": 3})
    assert "暂无模拟面试记录" in out


def test_interview_history_isolates_other_owner(db_session) -> None:
    db = db_session
    _add_session(db, _OTHER, status="finished", stage="wrapup", turn_count=5)

    out = _tool(db, "interview_history").invoke({"limit": 3})

    assert "暂无模拟面试记录" in out


# —— score_trend ——


def _add_finished(
    db,
    day: int,
    anonymous_id: str = _OWNER,
    technical_depth: int = 5,
    communication: int = 5,
    project_authenticity: int = 5,
    overall: int = 5,
) -> InterviewSession:
    """造一场已结束且有评分的面试；day 决定时间先后（created_at 显式写，避免同事务同戳）。"""
    return _add_session(
        db,
        anonymous_id,
        status="finished",
        stage="wrapup",
        turn_count=8,
        created_at=datetime(2026, 1, day, tzinfo=timezone.utc),
        final_report_json={
            "technical_depth": technical_depth,
            "communication": communication,
            "project_authenticity": project_authenticity,
            "overall": overall,
        },
    )


def test_score_trend_marks_up_and_down(db_session) -> None:
    """逐场与上一场对比：首场基准场，之后给上升/下降标记，末尾给整体总结。"""
    db = db_session
    _add_finished(db, day=1, technical_depth=5, communication=5, overall=6)
    _add_finished(db, day=2, technical_depth=7, communication=6, overall=8)
    _add_finished(db, day=3, technical_depth=6, communication=6, overall=7)

    out = _tool(db, "score_trend").invoke({"limit": 5})

    assert "基准场" in out
    assert "上升" in out and "下降" in out
    assert "技术深度 5" in out and "技术深度 7↑" in out and "技术深度 6↓" in out
    assert "表达结构 6→" in out  # 与上一场持平给 →
    assert "整体表现从首场 6 到最近一场 7，上升 1 分" in out


def test_score_trend_lists_oldest_first(db_session) -> None:
    """趋势要从早到晚看：输出里低分场次必须排在前面。"""
    db = db_session
    _add_finished(db, day=1, overall=6)
    _add_finished(db, day=2, overall=8)

    out = _tool(db, "score_trend").invoke({"limit": 5})

    assert out.index("整体表现 6") < out.index("整体表现 8")


def test_score_trend_single_session_has_no_trend(db_session) -> None:
    db = db_session
    _add_finished(db, day=1, technical_depth=7, overall=7)

    out = _tool(db, "score_trend").invoke({"limit": 5})

    assert "技术深度 7" in out and "整体表现 7" in out
    assert "只有一场" in out
    assert "暂时看不出趋势" in out


def test_score_trend_empty_for_new_owner(db_session) -> None:
    out = _tool(db_session, "score_trend").invoke({"limit": 5})
    assert "暂无已完成的模拟面试" in out


def test_score_trend_ignores_unfinished_and_null_report(db_session) -> None:
    """未结束的场次、报告为 SQL NULL 的场次都不能进趋势。"""
    db = db_session
    _add_session(db, _OWNER, status="in_progress", stage="technical", turn_count=3)
    _add_session(db, _OWNER, status="finished", stage="wrapup", turn_count=8)

    out = _tool(db, "score_trend").invoke({"limit": 5})

    assert "暂无已完成的模拟面试" in out


def test_score_trend_ignores_json_null_literal(db_session) -> None:
    """JSONB 写 None 会落成 JSON null 字面量，SQL 的 is_not(NULL) 过滤不掉它。"""
    db = db_session
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO interview_sessions"
                " (resume_id, anonymous_id, status, stage, turn_count, final_report_json)"
                " VALUES (999999, :a, 'finished', 'wrapup', 8, 'null'::jsonb)"
            ),
            {"a": _OWNER},
        )
    _add_finished(db, day=1, overall=6)

    out = _tool(db, "score_trend").invoke({"limit": 5})

    assert "最近 1 场" in out
    assert "null" not in out


def test_score_trend_clamps_limit(db_session) -> None:
    """limit 钳到 1~10；非正数回落默认 5。"""
    db = db_session
    for day in range(1, 13):
        _add_finished(db, day=day, overall=day)

    assert "最近 3 场" in _tool(db, "score_trend").invoke({"limit": 3})
    assert "最近 5 场" in _tool(db, "score_trend").invoke({"limit": 0})
    assert "最近 10 场" in _tool(db, "score_trend").invoke({"limit": 999})


def test_score_trend_isolates_other_owner(db_session) -> None:
    db = db_session
    _add_finished(db, day=1, anonymous_id=_OTHER, overall=9)

    out = _tool(db, "score_trend").invoke({"limit": 5})

    assert "暂无已完成的模拟面试" in out
    assert "整体表现 9" not in out


# —— usage_stats ——


def _add_usage(db, action_type: str, tokens: int, anonymous_id: str = _OWNER) -> None:
    db.add(
        UsageLog(
            anonymous_id=anonymous_id,
            action_type=action_type,
            model_name="deepseek-chat",
            tokens_total=tokens,
            ip_address="127.0.0.1",
        )
    )
    db.commit()


def test_usage_stats_aggregates_by_action(db_session) -> None:
    db = db_session
    _add_usage(db, "analysis", 1200)
    _add_usage(db, "analysis", 800)
    _add_usage(db, "agent", 500)

    out = _tool(db, "usage_stats").invoke({"days": 7})

    assert "AI 简历分析：2 次，2000 tokens" in out
    assert "AI 客服问答：1 次，500 tokens" in out
    assert "合计消耗 2500 tokens" in out


def test_usage_stats_empty_for_new_owner(db_session) -> None:
    out = _tool(db_session, "usage_stats").invoke({"days": 7})
    assert "没有用量记录" in out


def test_usage_stats_isolates_other_owner(db_session) -> None:
    db = db_session
    _add_usage(db, "analysis", 9999, anonymous_id=_OTHER)

    out = _tool(db, "usage_stats").invoke({"days": 7})

    assert "没有用量记录" in out


# —— analysis_read ——


def _add_analysis(
    db,
    resume_id: int,
    anonymous_id: str = _OWNER,
    valid_json: bool = True,
    **report,
) -> Analysis:
    analysis = Analysis(
        resume_id=resume_id,
        anonymous_id=anonymous_id,
        model_name="deepseek-chat",
        prompt_version=PROMPT_VERSION,
        result_json=report or {"target_position": "后端工程师"},
        valid_json=valid_json,
    )
    db.add(analysis)
    db.commit()
    return analysis


def test_analysis_read_empty_for_new_owner(db_session) -> None:
    out = _tool(db_session, "analysis_read").invoke({"resume_hint": ""})
    assert "没有已上传的简历" in out


def test_analysis_read_reports_missing_report(db_session) -> None:
    db = db_session
    _add_resume(db, _OWNER, "我的简历.pdf", "负责服务端接口开发。")

    out = _tool(db, "analysis_read").invoke({"resume_hint": ""})

    assert "还没有分析报告" in out


def test_analysis_read_ignores_invalid_report(db_session) -> None:
    """没通过校验的报告（valid_json=false）不算数，不能让模型读到半成品。"""
    db = db_session
    resume = _add_resume(db, _OWNER, "我的简历.pdf", "负责服务端接口开发。")
    _add_analysis(db, resume.id, valid_json=False, target_position="不该被读到的岗位")

    out = _tool(db, "analysis_read").invoke({"resume_hint": ""})

    assert "还没有分析报告" in out
    assert "不该被读到的岗位" not in out


def test_analysis_read_renders_latest_resume_report(db_session) -> None:
    """hint 为空取最近上传的一份，输出六块内容。"""
    db = db_session
    older = _add_resume(db, _OWNER, "老简历.pdf", "一段旧经历。")
    _add_analysis(db, older.id, target_position="Java 后端")
    newer = _add_resume(db, _OWNER, "新简历.pdf", "一段新经历。")
    _add_analysis(
        db,
        newer.id,
        target_position="RAG 应用开发",
        position_match="匹配度较高。",
        strengths=["有 RAG 落地经验"],
        weaknesses=["缺少分布式经验"],
        keyword_gaps=["Kafka"],
        suggestions=["补一段消息队列实践"],
        predicted_questions=["讲讲你的检索链路"],
    )

    out = _tool(db, "analysis_read").invoke({"resume_hint": ""})

    assert "新简历.pdf" in out
    assert "老简历.pdf" not in out
    assert "RAG 应用开发" in out
    for label in (
        "目标岗位",
        "岗位匹配",
        "优势",
        "短板",
        "关键词缺口",
        "改进建议",
        "预测面试题",
    ):
        assert label in out


def test_analysis_read_matches_hint_by_filename(db_session) -> None:
    db = db_session
    frontend = _add_resume(db, _OWNER, "前端简历.pdf", "一段前端经历。")
    _add_analysis(db, frontend.id, target_position="前端工程师")
    backend = _add_resume(db, _OWNER, "后端简历.pdf", "一段后端经历。")
    _add_analysis(db, backend.id, target_position="后端工程师")

    out = _tool(db, "analysis_read").invoke({"resume_hint": "前端"})

    assert "前端简历.pdf" in out
    assert "后端简历.pdf" not in out
    assert "前端工程师" in out


def test_analysis_read_hint_not_found_lists_actual_names(db_session) -> None:
    db = db_session
    _add_resume(db, _OWNER, "我的简历.pdf", "一段经历。")

    out = _tool(db, "analysis_read").invoke({"resume_hint": "不存在的文件名"})

    assert "未找到" in out
    assert "我的简历.pdf" in out


def test_analysis_read_limits_predicted_questions_to_five(db_session) -> None:
    db = db_session
    resume = _add_resume(db, _OWNER, "我的简历.pdf", "一段经历。")
    _add_analysis(
        db, resume.id, predicted_questions=[f"预测题{i}" for i in range(1, 9)]
    )

    out = _tool(db, "analysis_read").invoke({"resume_hint": ""})

    assert "预测题5" in out
    assert "预测题6" not in out


def test_analysis_read_truncates_long_report(db_session) -> None:
    """报告再长也要压到 800 字符内，否则一次调用就挤爆上下文。"""
    db = db_session
    resume = _add_resume(db, _OWNER, "我的简历.pdf", "一段经历。")
    _add_analysis(
        db,
        resume.id,
        strengths=[f"优势{i}" * 40 for i in range(6)],
        weaknesses=[f"短板{i}" * 40 for i in range(6)],
        suggestions=[f"建议{i}" * 40 for i in range(6)],
    )

    out = _tool(db, "analysis_read").invoke({"resume_hint": ""})

    assert len(out) <= 800
    assert out.endswith("…")


def test_analysis_read_isolates_other_owner(db_session) -> None:
    db = db_session
    other = _add_resume(db, _OTHER, "别人的简历.pdf", "别人的经历。")
    _add_analysis(db, other.id, anonymous_id=_OTHER, target_position="别人的岗位")

    out = _tool(db, "analysis_read").invoke({"resume_hint": ""})

    assert "没有已上传的简历" in out
    assert "别人的岗位" not in out


def test_analysis_read_swallows_query_error(db_session, monkeypatch) -> None:
    """报告查询抛异常时返回兜底话术，不向上抛（抛了整轮 Agent 就崩）。"""
    db = db_session
    _add_resume(db, _OWNER, "我的简历.pdf", "一段经历。")

    def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.services.agent.tools.latest_valid_analysis", _boom)

    out = _tool(db, "analysis_read").invoke({"resume_hint": ""})

    assert "暂时出错" in out


# —— kb_list ——


def _add_kb_document(
    db,
    name: str,
    scope: str = "private",
    anonymous_id: str = _OWNER,
    status: str = "ready",
    raw_text: str = "文档正文",
    chunk_texts: tuple[str, ...] = (),
) -> KBDocument:
    """造一篇知识库文档（名字统一带 _KB_PREFIX，便于按前缀清理）。"""
    doc = KBDocument(
        title=f"{_KB_PREFIX}{name}",
        anonymous_id=None if scope == "public" else anonymous_id,
        source_type="preset" if scope == "public" else "uploaded",
        scope=scope,
        doc_type="text",
        raw_text=raw_text,
        status=status,
    )
    db.add(doc)
    db.commit()
    for seq, content in enumerate(chunk_texts):
        db.add(
            KBChunk(
                document_id=doc.id,
                seq=seq,
                content=content,
                embedding=[0.0] * 768,
            )
        )
    db.commit()
    return doc


def test_kb_list_shows_public_and_own_documents(db_session) -> None:
    """预置语料 + 本人上传都要列出来，标题/来源/状态/块数齐全。"""
    db = db_session
    _add_kb_document(
        db, "平台预置语料.md", scope="public", chunk_texts=("块一", "块二")
    )
    _add_kb_document(db, "我的资料.md", chunk_texts=("块甲",))

    out = _tool(db, "kb_list").invoke({"query": ""})

    assert f"{_KB_PREFIX}平台预置语料.md" in out
    assert f"{_KB_PREFIX}我的资料.md" in out
    assert "平台预置" in out and "本人上传" in out
    assert "可检索" in out
    assert "2 个知识块" in out and "1 个知识块" in out


def test_kb_list_never_leaks_document_content(db_session) -> None:
    """清单工具绝不能把 raw_text 或切块正文漏给模型——正文归 kb_search 管。"""
    db = db_session
    _add_kb_document(
        db,
        "技术笔记.md",
        raw_text="绝不外泄的原文片段",
        chunk_texts=("绝不外泄的切块正文",),
    )

    out = _tool(db, "kb_list").invoke({"query": ""})

    assert f"{_KB_PREFIX}技术笔记.md" in out
    assert "绝不外泄" not in out


def test_kb_list_filters_title_case_insensitively(db_session) -> None:
    db = db_session
    _add_kb_document(db, "RAG 面试题.md")
    _add_kb_document(db, "MySQL 索引.md")

    out = _tool(db, "kb_list").invoke({"query": "rag"})

    assert f"{_KB_PREFIX}RAG 面试题.md" in out
    assert f"{_KB_PREFIX}MySQL 索引.md" not in out

    assert "未找到" in _tool(db, "kb_list").invoke({"query": "Kafka"})


def test_kb_list_caps_at_twenty(db_session) -> None:
    """超过 20 篇只列前 20，并明确说明有截断。"""
    db = db_session
    for index in range(1, 26):
        _add_kb_document(db, f"文档{index:02d}.md")

    out = _tool(db, "kb_list").invoke({"query": ""})

    assert "仅显示前 20 篇" in out
    assert f"{_KB_PREFIX}文档25.md" in out  # 最近的在前面
    assert f"{_KB_PREFIX}文档05.md" not in out  # 第 21 篇起不出现


def test_kb_list_empty_library(db_session, monkeypatch) -> None:
    """开发库里预置语料一直存在，空库分支用 monkeypatch 隔离验证。"""
    monkeypatch.setattr("app.services.agent.tools.list_documents", lambda *a, **k: [])

    out = _tool(db_session, "kb_list").invoke({"query": ""})

    assert "还没有任何可查看的文档" in out


def test_kb_list_isolates_other_owner(db_session) -> None:
    """别人的私有文档不可见。"""
    db = db_session
    _add_kb_document(
        db, "别人的机密资料.md", anonymous_id=_OTHER, chunk_texts=("机密",)
    )

    out = _tool(db, "kb_list").invoke({"query": ""})

    assert f"{_KB_PREFIX}别人的机密资料.md" not in out
    assert "机密" not in out


def test_kb_list_swallows_query_error(db_session, monkeypatch) -> None:
    """查询抛异常时返回兜底话术，不向上抛（抛了整轮 Agent 就崩）。"""

    def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.services.agent.tools.list_documents", _boom)

    out = _tool(db_session, "kb_list").invoke({"query": ""})

    assert "暂时出错" in out


# —— platform_help ——

# 总览文案的独有标记：命中具体主题时**不该**出现，用于区分"命中"与"回落"
_OVERVIEW_MARK = "主要功能："


@pytest.mark.parametrize(
    ("topic", "marker"),
    [
        ("怎么上传简历", "上传简历"),
        ("AI 分析怎么用", "AI 分析"),
        ("模拟面试怎么开始", "模拟面试"),
        ("在线对话是干嘛的", "在线对话"),
        ("AI 客服能做什么", "AI 客服"),
        ("在哪看使用日志", "使用日志"),
        ("个人中心有什么", "个人中心"),
        ("数据看板在哪", "数据看板"),
        ("语料库管理怎么上传", "语料库管理"),
    ],
)
def test_platform_help_covers_topics(db_session, topic: str, marker: str) -> None:
    """八个必需主题 + 语料库管理都要能被口语化问法路由到专属说明。"""
    out = _tool(db_session, "platform_help").invoke({"topic": topic})

    assert marker in out
    assert _OVERVIEW_MARK not in out  # 命中了主题就不该回落总览


def test_platform_help_falls_back_to_overview(db_session) -> None:
    """topic 为空、纯空白或完全不相干时回平台功能总览，且总览覆盖全部主题。"""
    tool = _tool(db_session, "platform_help")

    for topic in ("", "   ", "今天天气怎么样"):
        out = tool.invoke({"topic": topic})
        assert _OVERVIEW_MARK in out
        for name in (
            "上传简历",
            "AI 分析",
            "模拟面试",
            "在线对话",
            "AI 客服",
            "使用日志",
            "个人中心",
            "数据看板",
            "语料库管理",
        ):
            assert name in out


def test_platform_help_is_static_text() -> None:
    """纯静态文案：不查库不调模型——db 传 None 也必须正常返回。"""
    tools = make_tools(None, None, None, ToolContext())

    out = next(t for t in tools if t.name == "platform_help").invoke({"topic": ""})

    assert _OVERVIEW_MARK in out


def test_kb_search_and_platform_help_are_mutually_exclusive(db_session) -> None:
    """两个工具的 description 必须互相点名排他，否则模型会在两者间乱选。"""
    tools = {t.name: t for t in make_tools(db_session, None, _OWNER, ToolContext())}

    assert "platform_help" in tools["kb_search"].description
    assert "kb_search" in tools["platform_help"].description


# —— 工具描述互斥性（11 个工具统一口径）——

_ALL_TOOLS = (
    "kb_search",
    "resume_lookup",
    "interview_history",
    "score_trend",
    "usage_stats",
    "analysis_read",
    "kb_list",
    "platform_help",
    "job_match",
    "question_gen",
    "answer_review",
)


def _descriptions() -> dict:
    """描述是静态的，db 传 None 即可（顺带证明取描述不需要数据库）。"""
    return {t.name: t.description for t in make_tools(None, None, None, ToolContext())}


@pytest.mark.parametrize("name", _ALL_TOOLS)
def test_tool_description_has_three_parts(name: str) -> None:
    """每个工具都要说清「做什么 / 什么时候用 / 什么时候不用」，且描述不能太短。"""
    desc = _descriptions()[name]

    assert len(desc) > 50
    assert "什么时候用" in desc
    assert "什么时候不用" in desc


@pytest.mark.parametrize(
    ("name", "other"),
    [
        ("kb_search", "platform_help"),
        ("platform_help", "kb_search"),
        ("resume_lookup", "analysis_read"),
        ("analysis_read", "resume_lookup"),
        ("kb_search", "question_gen"),
        ("question_gen", "kb_search"),
        ("question_gen", "answer_review"),
        ("answer_review", "question_gen"),
        ("kb_search", "answer_review"),
        ("answer_review", "kb_search"),
    ],
)
def test_confusable_tools_name_each_other(name: str, other: str) -> None:
    """易撞组合必须互指：只说"我干啥"不够，模型还需要知道"别用我、用谁"。"""
    assert other in _descriptions()[name]


def test_descriptions_cover_every_tool() -> None:
    """上面的参数化清单要跟 make_tools 的实际返回一致，防止加了工具忘了补描述口径。"""
    assert set(_descriptions()) == set(_ALL_TOOLS)


# —— 工具内 LLM 调用的独立限额与记账 ——
# US-009~011 的 job_match / question_gen / answer_review 都走 _run_tool_llm，
# 三个工具落地前先把它单独测透（这层只管"放不放行 + 记没记账"）。


def _tool_llm_logs(db, anonymous_id: str = _OWNER) -> list[UsageLog]:
    """取本人的 agent_tool_llm 用量行（正序）。"""
    return list(
        db.scalars(
            select(UsageLog)
            .where(
                UsageLog.anonymous_id == anonymous_id,
                UsageLog.action_type == _TOOL_LLM_ACTION,
            )
            .order_by(UsageLog.id.asc())
        )
    )


def _fake_result(prompt: int = 120, completion: int = 80) -> AnalysisResult:
    """替身：真实调用返回的就是 AnalysisResult，直接借它验 token 取数。"""
    return AnalysisResult(
        report={"ok": True},
        valid=True,
        model_name="deepseek-chat",
        tokens_prompt=prompt,
        tokens_completion=completion,
        duration_ms=5,
    )


def test_run_tool_llm_calls_and_records_usage(db_session, monkeypatch) -> None:
    """未超限：正常执行，并记一条带 token 合计的 agent_tool_llm 用量。"""
    monkeypatch.setattr(settings, "daily_agent_tool_llm_limit", 3)
    calls: list[int] = []

    def call() -> AnalysisResult:
        calls.append(1)
        return _fake_result()

    result, reply = _run_tool_llm(db_session, None, _OWNER, call)

    assert reply is None
    assert result is not None and result.report == {"ok": True}
    assert calls == [1]
    logs = _tool_llm_logs(db_session)
    assert len(logs) == 1
    assert logs[0].tokens_total == 200  # prompt + completion
    assert logs[0].model_name == settings.ai_model


def test_run_tool_llm_still_allowed_just_below_limit(db_session, monkeypatch) -> None:
    """已用 = 上限 - 1 时还能用：只有达到上限才拦。"""
    monkeypatch.setattr(settings, "daily_agent_tool_llm_limit", 3)
    for _ in range(2):
        _add_usage(db_session, _TOOL_LLM_ACTION, 100)

    result, reply = _run_tool_llm(db_session, None, _OWNER, _fake_result)

    assert reply is None and result is not None
    assert len(_tool_llm_logs(db_session)) == 3


@pytest.mark.parametrize("existing", [3, 5])
def test_run_tool_llm_blocks_at_and_over_limit(
    db_session, monkeypatch, existing: int
) -> None:
    """恰好达上限与超限都只回话术：不调模型（省钱）、不抛异常、不重复记账。"""
    monkeypatch.setattr(settings, "daily_agent_tool_llm_limit", 3)
    for _ in range(existing):
        _add_usage(db_session, _TOOL_LLM_ACTION, 100)
    calls: list[int] = []

    def call() -> str:
        calls.append(1)
        return "不该被调用"

    result, reply = _run_tool_llm(db_session, None, _OWNER, call)

    assert result is None
    assert reply is not None and "今日工具内 AI 调用已达上限" in reply
    assert calls == []  # 关闸的关键：超限时一次模型都不调
    assert len(_tool_llm_logs(db_session)) == existing


def test_run_tool_llm_disabled_by_zero_limit(db_session, monkeypatch) -> None:
    """daily_agent_tool_llm_limit=0 即关闭工具内模型调用，一次都放不出去。"""
    monkeypatch.setattr(settings, "daily_agent_tool_llm_limit", 0)

    result, reply = _run_tool_llm(db_session, None, _OWNER, _fake_result)

    assert result is None
    assert reply == _TOOL_LLM_LIMIT_REPLY
    assert _tool_llm_logs(db_session) == []


def test_run_tool_llm_swallows_limit_query_error(db_session, monkeypatch) -> None:
    """限额查询本身出错时给兜底话术，不向上抛（抛了整轮 Agent 就崩）。"""
    monkeypatch.setattr(settings, "daily_agent_tool_llm_limit", 3)

    def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.services.agent.tools.count_today_usage_by_owner", _boom)

    result, reply = _run_tool_llm(db_session, None, _OWNER, _fake_result)

    assert result is None
    assert reply is not None and "暂时不可用" in reply


def test_run_tool_llm_survives_usage_write_failure(db_session, monkeypatch) -> None:
    """记账是旁路：写不进库也要照常返回结果，不能连累功能本身。"""
    monkeypatch.setattr(settings, "daily_agent_tool_llm_limit", 3)

    def _boom(*args, **kwargs):
        raise RuntimeError("write failed")

    monkeypatch.setattr("app.services.agent.tools.write_usage", _boom)

    result, reply = _run_tool_llm(db_session, None, _OWNER, _fake_result)

    assert reply is None and result is not None
    assert _tool_llm_logs(db_session) == []


def test_run_tool_llm_works_without_identity(db_session, monkeypatch) -> None:
    """出题这类工具不查个人数据，无身份也要能用；无归属者的账写不了，直接不写。"""
    monkeypatch.setattr(settings, "daily_agent_tool_llm_limit", 3)

    result, reply = _run_tool_llm(db_session, None, None, _fake_result)

    assert reply is None and result is not None
    assert _tool_llm_logs(db_session) == []


def test_usage_stats_labels_tool_llm_action(db_session) -> None:
    """工具内 LLM 调用在用量统计里要有中文名，不能把英文 action_type 直接甩给用户。"""
    _add_usage(db_session, _TOOL_LLM_ACTION, 300)

    out = _tool(db_session, "usage_stats").invoke({"days": 7})

    assert "工具内 AI 调用：1 次，300 tokens" in out


# —— job_match（第一个工具内调 LLM 的工具）——
# 真实调用靠 monkeypatch 掉 tools.chat_json 替身：只验「送进模型的参数」与「渲染出的文本」。

_MATCH_JD = "熟悉 MySQL 索引优化，有 Redis 与 K8s 经验者优先。"


def _patch_chat_json(monkeypatch, result: AnalysisResult | None = None, error=None):
    """替身 chat_json：返回固定报告或抛错，并记录本次调用的参数。"""
    seen: list[dict] = []

    def _fake(system_prompt, user_prompt, cfg, validator):
        seen.append(
            {
                "system": system_prompt,
                "user": user_prompt,
                "validator": validator,
            }
        )
        if error is not None:
            raise error
        return result if result is not None else _fake_match_result()

    monkeypatch.setattr("app.services.agent.tools.chat_json", _fake)
    return seen


def _fake_match_result() -> AnalysisResult:
    return AnalysisResult(
        report={
            "match_score": 72,
            "matched_keywords": ["MySQL", "Redis"],
            "missing_keywords": ["K8s"],
            "suggestions": ["补一条容器化项目经历"],
        },
        valid=True,
        model_name="deepseek-chat",
        tokens_prompt=300,
        tokens_completion=120,
        duration_ms=8,
    )


def test_job_match_renders_score_and_keywords(db_session, monkeypatch) -> None:
    """成功路径：走结构化校验输出，渲染评分/命中/缺失/建议，并记一条工具内用量。"""
    _add_resume(
        db_session, _OWNER, "我的简历.pdf", "熟悉 MySQL 索引优化与 Redis 缓存。"
    )
    seen = _patch_chat_json(monkeypatch)

    out = _tool(db_session, "job_match").invoke({"jd_text": _MATCH_JD})

    assert "整体匹配度：72/100" in out
    assert "已命中关键词：MySQL、Redis" in out
    assert "缺失关键词：K8s" in out
    assert "针对性建议：补一条容器化项目经历" in out
    # 走 chat_json 的结构化校验路径（校验失败才有重试）
    assert seen[0]["system"] == JOB_MATCH_SYSTEM_PROMPT
    # 校验器是 JobMatchReport：校验不过才有 ai_client 的重试
    validated = seen[0]["validator"](
        {
            "match_score": 72,
            "matched_keywords": ["MySQL"],
            "missing_keywords": ["K8s"],
            "suggestions": ["补一条容器化项目经历"],
        }
    )
    assert isinstance(validated, JobMatchReport)
    assert "我的简历.pdf" in out  # 说明匹配的是哪份简历
    logs = _tool_llm_logs(db_session)
    assert len(logs) == 1 and logs[0].tokens_total == 420


def test_job_match_truncates_long_jd(db_session, monkeypatch) -> None:
    """整页粘贴的 JD 要截断后再送模型，并在输出里说明截断了。"""
    _add_resume(db_session, _OWNER, "我的简历.pdf", "熟悉 MySQL 索引优化。")
    seen = _patch_chat_json(monkeypatch)

    out = _tool(db_session, "job_match").invoke(
        {"jd_text": "x" * (_TOOL_JD_CHARS + 1000)}
    )

    assert f"已按前 {_TOOL_JD_CHARS} 字符分析" in out
    assert seen[0]["user"].count("x") == _TOOL_JD_CHARS  # 送入模型的只有前 4000 字


def test_job_match_requires_jd(db_session, monkeypatch) -> None:
    """没贴 JD 就提示补全，且一次模型都不调（省钱的关闸）。"""
    _add_resume(db_session, _OWNER, "我的简历.pdf", "熟悉 MySQL。")
    seen = _patch_chat_json(monkeypatch)

    out = _tool(db_session, "job_match").invoke({"jd_text": "   "})

    assert "JD" in out
    assert seen == []


def test_job_match_requires_resume(db_session, monkeypatch) -> None:
    """名下没有简历时先引导上传，不调模型。"""
    seen = _patch_chat_json(monkeypatch)

    out = _tool(db_session, "job_match").invoke({"jd_text": _MATCH_JD})

    assert "需要先上传简历" in out
    assert seen == []


def test_job_match_swallows_ai_error(db_session, monkeypatch) -> None:
    """AI 调用失败（含欠费 402）给可读话术，不向上抛，失败的调用不记账。"""
    _add_resume(db_session, _OWNER, "我的简历.pdf", "熟悉 MySQL。")
    _patch_chat_json(monkeypatch, error=AIError("AI 服务暂时不可用，请稍后重试"))

    out = _tool(db_session, "job_match").invoke({"jd_text": _MATCH_JD})

    assert "暂时不可用" in out
    assert _tool_llm_logs(db_session) == []


def test_job_match_blocked_by_tool_llm_limit(db_session, monkeypatch) -> None:
    """工具内 AI 调用被关掉（limit=0）时回统一上限话术，一次模型都不调。"""
    _add_resume(db_session, _OWNER, "我的简历.pdf", "熟悉 MySQL。")
    monkeypatch.setattr(settings, "daily_agent_tool_llm_limit", 0)
    seen = _patch_chat_json(monkeypatch)

    out = _tool(db_session, "job_match").invoke({"jd_text": _MATCH_JD})

    assert "今日工具内 AI 调用已达上限" in out
    assert seen == []


# —— question_gen（先检索平台知识库，再依据语料出题）——
# 两侧都 mock：检索侧替掉 embed_texts / search_chunks，模型侧替掉 chat_json。
# 只验「送进模型的资料对不对」与「渲染出的题目文本对不对」。

_QUESTION_TOPIC = "MySQL 索引"


def _patch_kb_hits(monkeypatch, hits=None, error: Exception | None = None) -> None:
    """替身知识库检索：命中固定块 / 空结果 / embedding 阶段直接抛错。"""

    if error is not None:

        def _boom(texts):
            raise error

        monkeypatch.setattr("app.services.agent.tools.embed_texts", _boom)
    else:
        monkeypatch.setattr(
            "app.services.agent.tools.embed_texts", lambda texts: [[0.1] * 8]
        )
    monkeypatch.setattr(
        "app.services.agent.tools.search_chunks",
        lambda *a, **k: [] if hits is None else hits,
    )


def _kb_hit() -> list[dict]:
    return [
        {
            "document_id": 1,
            "title": "MySQL 索引.md",
            "seq": 0,
            "content": "聚簇索引与二级索引的区别是叶子节点是否存整行数据。",
            "similarity": 0.83,
        }
    ]


def _fake_question_result() -> AnalysisResult:
    return AnalysisResult(
        report={
            "questions": [
                "为什么 MySQL 用 B+ 树而不是 B 树做索引？",
                "什么情况下索引会失效？",
                "如何判断一条 SQL 有没有走对索引？",
            ]
        },
        valid=True,
        model_name="deepseek-chat",
        tokens_prompt=260,
        tokens_completion=140,
        duration_ms=9,
    )


def test_question_gen_grounds_questions_in_kb_hits(db_session, monkeypatch) -> None:
    """成功路径：检索命中 → 语料送进模型 → 题目按知识库口径渲染，并记一条工具内用量。"""
    _patch_kb_hits(monkeypatch, hits=_kb_hit())
    seen = _patch_chat_json(monkeypatch, result=_fake_question_result())

    out = _tool(db_session, "question_gen").invoke(
        {"topic": _QUESTION_TOPIC, "position_type": "fresh"}
    )

    assert "依据平台知识库出题" in out
    assert "难度定位：校招" in out
    assert "1. 为什么 MySQL 用 B+ 树而不是 B 树做索引？" in out
    assert "不来自平台知识库" not in out
    # 检索到的语料确实进了提示词——本工具不是纯模型生成
    assert "聚簇索引与二级索引的区别" in seen[0]["user"]
    assert "未收录该主题" not in seen[0]["user"]
    assert seen[0]["system"] == QUESTION_GEN_SYSTEM_PROMPT
    validated = seen[0]["validator"]({"questions": ["a", "b", "c"]})
    assert isinstance(validated, QuestionGenReport)
    assert "《MySQL 索引.md》" in out  # 出题依据要标出来
    logs = _tool_llm_logs(db_session)
    assert len(logs) == 1 and logs[0].tokens_total == 400


def test_question_gen_falls_back_when_kb_misses(db_session, monkeypatch) -> None:
    """检索未命中：明确说未收录 + 标注题目不来自知识库，并让模型据此退回自身知识。"""
    _patch_kb_hits(monkeypatch, hits=[])
    seen = _patch_chat_json(monkeypatch, result=_fake_question_result())

    out = _tool(db_session, "question_gen").invoke(
        {"topic": "量子计算", "position_type": ""}
    )

    assert "平台知识库未收录该主题" in out
    assert "以下题目不来自平台知识库" in out
    assert "难度定位：通用" in out
    assert "1. 为什么 MySQL" in out  # 题目照样给出
    assert "未收录该主题" in seen[0]["user"]


def test_question_gen_falls_back_when_retrieval_fails(db_session, monkeypatch) -> None:
    """检索服务挂了（embedding 异常）不当成失败：退回模型出题并标注来源。"""
    _patch_kb_hits(monkeypatch, error=RuntimeError("ollama down"))
    seen = _patch_chat_json(monkeypatch, result=_fake_question_result())

    out = _tool(db_session, "question_gen").invoke(
        {"topic": _QUESTION_TOPIC, "position_type": "intern"}
    )

    assert "以下题目不来自平台知识库" in out
    assert "1. 为什么 MySQL" in out
    assert seen  # 检索挂了也要能出题


@pytest.mark.parametrize(
    ("position_type", "level"),
    [
        ("intern", "实习"),
        ("fresh", "校招"),
        ("senior", "社招"),
        ("", "通用"),
        ("boss", "通用"),  # 非法枚举回落通用
        ("Senior", "社招"),  # 大小写不敏感
    ],
)
def test_question_gen_maps_position_type(
    db_session, monkeypatch, position_type: str, level: str
) -> None:
    """岗位类型决定难度定位：三个合法值各自生效，空值与非法值回落通用并体现在输出里。"""
    _patch_kb_hits(monkeypatch, hits=_kb_hit())
    _patch_chat_json(monkeypatch, result=_fake_question_result())

    out = _tool(db_session, "question_gen").invoke(
        {"topic": _QUESTION_TOPIC, "position_type": position_type}
    )

    assert f"难度定位：{level}" in out


def test_question_gen_requires_topic(db_session, monkeypatch) -> None:
    """没说主题就提示补全，一次模型都不调。"""
    _patch_kb_hits(monkeypatch, hits=_kb_hit())
    seen = _patch_chat_json(monkeypatch)

    out = _tool(db_session, "question_gen").invoke(
        {"topic": "   ", "position_type": ""}
    )

    assert "想练习哪个主题" in out
    assert seen == []


def test_question_gen_swallows_ai_error(db_session, monkeypatch) -> None:
    """AI 调用失败（含欠费 402）给可读话术，不向上抛，失败的调用不记账。"""
    _patch_kb_hits(monkeypatch, hits=_kb_hit())
    _patch_chat_json(monkeypatch, error=AIError("AI 服务暂时不可用，请稍后重试"))

    out = _tool(db_session, "question_gen").invoke(
        {"topic": _QUESTION_TOPIC, "position_type": ""}
    )

    assert "暂时不可用" in out
    assert _tool_llm_logs(db_session) == []


def test_question_gen_blocked_by_tool_llm_limit(db_session, monkeypatch) -> None:
    """工具内 AI 调用被关掉（limit=0）时回统一上限话术，一次模型都不调。"""
    _patch_kb_hits(monkeypatch, hits=_kb_hit())
    monkeypatch.setattr(settings, "daily_agent_tool_llm_limit", 0)
    seen = _patch_chat_json(monkeypatch)

    out = _tool(db_session, "question_gen").invoke(
        {"topic": _QUESTION_TOPIC, "position_type": ""}
    )

    assert "今日工具内 AI 调用已达上限" in out
    assert seen == []


def test_question_gen_report_requires_three_to_five_questions() -> None:
    """题目数契约：少于 3 道或多于 5 道不通过校验（ai_client 会据此重试）。"""
    ok = QuestionGenReport.model_validate({"questions": ["a", "b", "c"]})
    assert len(ok.questions) == 3

    with pytest.raises(ValidationError):
        QuestionGenReport.model_validate({"questions": ["a", "b"]})
    with pytest.raises(ValidationError):
        QuestionGenReport.model_validate({"questions": ["a"] * 6})


# —— answer_review（点评用户贴的一段回答）——
# 与 job_match 同一套路：monkeypatch 掉 tools.chat_json，只验送进模型的参数与渲染文本。
# 本工具不查个人数据（题目与回答都是用户当场贴的），无需身份也不需要简历。

_REVIEW_QUESTION = "为什么 MySQL 索引要用 B+ 树？"
_REVIEW_ANSWER = "因为 B+ 树的叶子节点连成了链表，范围查询快，而且非叶子节点不存数据。"


def _fake_review_result() -> AnalysisResult:
    return AnalysisResult(
        report={
            "technical_depth": 7,
            "communication": 6,
            "project_authenticity": 5,
            "suggestions": [
                "补一句『非叶子节点不存数据，所以单节点能放更多 key、树更矮』",
                "先给结论再展开，最后补一句适用场景",
            ],
        },
        valid=True,
        model_name="deepseek-chat",
        tokens_prompt=280,
        tokens_completion=150,
        duration_ms=11,
    )


def test_answer_review_renders_scores_and_suggestions(db_session, monkeypatch) -> None:
    """成功路径：三项评分 + 改进建议渲染成中文，并记一条工具内用量。"""
    seen = _patch_chat_json(monkeypatch, result=_fake_review_result())

    out = _tool(db_session, "answer_review").invoke(
        {"question": _REVIEW_QUESTION, "answer": _REVIEW_ANSWER}
    )

    assert "技术深度 7/10" in out
    assert "表达结构 6/10" in out
    assert "项目真实性 5/10" in out
    assert "改进建议：" in out and "先给结论再展开" in out
    assert seen[0]["system"] == ANSWER_REVIEW_SYSTEM_PROMPT
    # 题目与回答都进了提示词
    assert _REVIEW_QUESTION in seen[0]["user"]
    assert _REVIEW_ANSWER in seen[0]["user"]
    validated = seen[0]["validator"](
        {
            "technical_depth": 7,
            "communication": 6,
            "project_authenticity": 5,
            "suggestions": ["a", "b"],
        }
    )
    assert isinstance(validated, AnswerReviewReport)
    logs = _tool_llm_logs(db_session)
    assert len(logs) == 1 and logs[0].tokens_total == 430


def test_answer_review_truncates_long_answer(db_session, monkeypatch) -> None:
    """整段自述贴进来要截断后再送模型，并在输出里说明截断了。"""
    seen = _patch_chat_json(monkeypatch, result=_fake_review_result())

    # 填充字用「好」：提示词模板里没有这个字，计数才等于送进模型的回答长度
    out = _tool(db_session, "answer_review").invoke(
        {"question": _REVIEW_QUESTION, "answer": "好" * (_TOOL_ANSWER_CHARS + 800)}
    )

    assert f"已按前 {_TOOL_ANSWER_CHARS} 字符点评" in out
    assert seen[0]["user"].count("好") == _TOOL_ANSWER_CHARS


def test_answer_review_requires_answer(db_session, monkeypatch) -> None:
    """只给题目没给回答就提示补全，一次模型都不调。"""
    seen = _patch_chat_json(monkeypatch)

    out = _tool(db_session, "answer_review").invoke(
        {"question": _REVIEW_QUESTION, "answer": "   "}
    )

    assert "回答" in out
    assert seen == []


def test_answer_review_requires_question(db_session, monkeypatch) -> None:
    """只贴回答没给题目也要补全题目（评分标准依赖题目），一次模型都不调。"""
    seen = _patch_chat_json(monkeypatch)

    out = _tool(db_session, "answer_review").invoke(
        {"question": "", "answer": _REVIEW_ANSWER}
    )

    assert "题目" in out
    assert seen == []


def test_answer_review_swallows_ai_error(db_session, monkeypatch) -> None:
    """AI 调用失败（含欠费 402）给可读话术，不向上抛，失败的调用不记账。"""
    _patch_chat_json(monkeypatch, error=AIError("AI 服务暂时不可用，请稍后重试"))

    out = _tool(db_session, "answer_review").invoke(
        {"question": _REVIEW_QUESTION, "answer": _REVIEW_ANSWER}
    )

    assert "暂时不可用" in out
    assert _tool_llm_logs(db_session) == []


def test_answer_review_blocked_by_tool_llm_limit(db_session, monkeypatch) -> None:
    """工具内 AI 调用被关掉（limit=0）时回统一上限话术，一次模型都不调。"""
    monkeypatch.setattr(settings, "daily_agent_tool_llm_limit", 0)
    seen = _patch_chat_json(monkeypatch)

    out = _tool(db_session, "answer_review").invoke(
        {"question": _REVIEW_QUESTION, "answer": _REVIEW_ANSWER}
    )

    assert "今日工具内 AI 调用已达上限" in out
    assert seen == []


def test_answer_review_report_requires_two_to_four_suggestions() -> None:
    """建议条数契约：少于 2 条或多于 4 条不通过校验（ai_client 会据此重试）。"""
    ok = AnswerReviewReport.model_validate(
        {
            "technical_depth": 7,
            "communication": 6,
            "project_authenticity": 5,
            "suggestions": ["a", "b"],
        }
    )
    assert len(ok.suggestions) == 2

    with pytest.raises(ValidationError):
        AnswerReviewReport.model_validate(
            {
                "technical_depth": 7,
                "communication": 6,
                "project_authenticity": 5,
                "suggestions": ["a"],
            }
        )
    with pytest.raises(ValidationError):
        AnswerReviewReport.model_validate(
            {
                "technical_depth": 11,  # 超出 10 分制
                "communication": 6,
                "project_authenticity": 5,
                "suggestions": ["a", "b"],
            }
        )
