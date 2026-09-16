"""v3.5 Agent 工具测试：resume_lookup / interview_history / usage_stats / analysis_read /
kb_list / platform_help。

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
from sqlalchemy import text

from app.db.session import SessionLocal, engine
from app.models.analysis import Analysis
from app.models.interview import InterviewSession
from app.models.kb import KBChunk, KBDocument
from app.models.resume import Resume
from app.models.usage_log import UsageLog
from app.services.agent import ToolContext, make_tools
from app.services.prompts import PROMPT_VERSION

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
            "DELETE FROM kb_documents"
            " WHERE anonymous_id IN (:a, :b) OR title LIKE :p"
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


def test_make_tools_exposes_eight_tools(db_session) -> None:
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
    _add_kb_document(db, "平台预置语料.md", scope="public", chunk_texts=("块一", "块二"))
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
    _add_kb_document(db, "别人的机密资料.md", anonymous_id=_OTHER, chunk_texts=("机密",))

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


# —— 工具描述互斥性（8 个工具统一口径）——

_ALL_TOOLS = (
    "kb_search",
    "resume_lookup",
    "interview_history",
    "score_trend",
    "usage_stats",
    "analysis_read",
    "kb_list",
    "platform_help",
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
    ],
)
def test_confusable_tools_name_each_other(name: str, other: str) -> None:
    """易撞组合必须互指：只说"我干啥"不够，模型还需要知道"别用我、用谁"。"""
    assert other in _descriptions()[name]


def test_descriptions_cover_every_tool() -> None:
    """上面的参数化清单要跟 make_tools 的实际返回一致，防止加了工具忘了补描述口径。"""
    assert set(_descriptions()) == set(_ALL_TOOLS)
