"""v3.5 Agent 个人数据工具测试：resume_lookup / interview_history / usage_stats。

只查"当前归属者"自己的数据是这三个工具的核心安全属性，因此重点覆盖：
- 无身份时明确拒绝（而不是返回空结果让模型脑补）
- 归属隔离（查不到别人的简历/面试/用量）
- 正常路径的输出内容与聚合口径

数据库隔离策略：所有测试数据用专属 anonymous_id 标记，只清自己造的数据，
不动开发库里的其他记录（与 test_rag 清表策略不同，避免误删本地数据）。
"""

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal, engine
from app.models.interview import InterviewSession
from app.models.resume import Resume
from app.models.usage_log import UsageLog
from app.services.agent import ToolContext, make_tools

_OWNER = "agent_tool_test_owner"
_OTHER = "agent_tool_test_other"

_TABLES = ("resumes", "interview_sessions", "usage_logs")


def _purge() -> None:
    with engine.begin() as conn:
        for table in _TABLES:
            conn.execute(
                text(f"DELETE FROM {table} WHERE anonymous_id IN (:a, :b)"),
                {"a": _OWNER, "b": _OTHER},
            )


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


def test_make_tools_exposes_four_tools(db_session) -> None:
    tools = make_tools(db_session, None, _OWNER, ToolContext())
    assert [t.name for t in tools] == [
        "kb_search",
        "resume_lookup",
        "interview_history",
        "usage_stats",
    ]


def test_personal_tools_reject_unknown_identity(db_session) -> None:
    """无法识别身份（无 user_id 也无 anonymous_id）时三个个人工具都要明确拒绝。"""
    tools = make_tools(db_session, None, None, ToolContext())

    def call(name: str, payload: dict) -> str:
        return next(t for t in tools if t.name == name).invoke(payload)

    assert "无法识别用户身份" in call("resume_lookup", {"query": ""})
    assert "无法识别用户身份" in call("interview_history", {"limit": 3})
    assert "无法识别用户身份" in call("usage_stats", {"days": 7})


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
