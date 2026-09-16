"""US-007 路由评测脚本的单测：全程 mock LLM，只验证统计口径与报告结构，不真调模型。

分三层：
1. 用例集本身的完整性（工具名真实存在、每个工具都有足够问法）——防止写了题目却对不上工具；
2. 纯函数（pick_tool / run / summarize / confusion_matrix / build_report）——决定报告对不对；
3. main 的两种情况（跑通写报告 / LLM 不可用非零退出且不写报告）——决定脚本在 CI 里的行为。
"""

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from app.services.agent import ToolContext, make_tools
from scripts import eval_agent_routing as ev

_CASES_PATH = (
    Path(__file__).resolve().parents[1].parent / "data" / "agent_eval" / "routing.json"
)
_MIN_CASES_PER_TOOL = 3


def _tool_names() -> set[str]:
    return {t.name for t in make_tools(None, None, None, ToolContext())}


def _message(tool_name: str | None, content: str = "") -> AIMessage:
    """构造"模型选了某个工具"的消息；tool_name 为空表示模型直接文字作答。"""
    if tool_name is None:
        return AIMessage(content=content or "直接回答")
    return AIMessage(
        content=content,
        tool_calls=[{"name": tool_name, "args": {}, "id": "call_1"}],
    )


class _FakeSelector:
    """替身模型：按 picks 顺序返回工具选择；picks 用尽后直接文字作答。"""

    def __init__(self, picks: list[str | None], error: Exception | None = None):
        self._picks = list(picks)
        self._error = error

    def bind(self, **kwargs):
        return self

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if self._error is not None:
            raise self._error
        pick = self._picks.pop(0) if self._picks else None
        return _message(pick)


# —— 用例集完整性 ——


def test_cases_cover_every_tool():
    cases = ev.load_cases(_CASES_PATH)
    tools = _tool_names()
    assert len(cases) >= len(tools) * _MIN_CASES_PER_TOOL
    assert {c["expected_tool"] for c in cases} == tools
    for tool in tools:
        owned = [c for c in cases if c["expected_tool"] == tool]
        assert len(owned) >= _MIN_CASES_PER_TOOL, f"{tool} 的问法不足 3 条"
    # 问题不能重复，否则混淆矩阵里同题会重复计数
    questions = [c["question"] for c in cases]
    assert len(questions) == len(set(questions))


# —— 纯函数 ——


def test_pick_tool_reads_first_call():
    assert ev.pick_tool(_message("kb_search")) == "kb_search"
    assert ev.pick_tool(_message(None)) == ev.NO_TOOL
    assert ev.pick_tool(AIMessage(content="")) == ev.NO_TOOL


def test_run_marks_hit_and_miss():
    cases = [
        {"question": "q1", "expected_tool": "kb_search"},
        {"question": "q2", "expected_tool": "platform_help"},
    ]
    details = ev.run(cases, lambda _q: _message("kb_search"))
    assert [d["hit"] for d in details] == [True, False]
    assert details[0]["actual"] == "kb_search"
    assert details[1]["expected"] == "platform_help"
    assert all(set(d) == {"question", "expected", "actual", "hit"} for d in details)


def test_summarize_accuracy():
    details = [
        {"question": "q1", "expected": "a", "actual": "a", "hit": True},
        {"question": "q2", "expected": "b", "actual": "a", "hit": False},
        {"question": "q3", "expected": "c", "actual": "c", "hit": True},
    ]
    stats = ev.summarize(details)
    assert stats["total"] == 3
    assert stats["hit_count"] == 2
    assert stats["accuracy"] == pytest.approx(2 / 3)
    assert stats["accuracy_text"] == "2/3 (66.7%)"


def test_confusion_matrix_counts():
    details = [
        {"question": "q1", "expected": "a", "actual": "a", "hit": True},
        {"question": "q2", "expected": "a", "actual": "b", "hit": False},
        {"question": "q3", "expected": "b", "actual": "b", "hit": True},
    ]
    matrix = ev.confusion_matrix(details)
    assert matrix == {"a": {"a": 1, "b": 1}, "b": {"b": 1}}
    # 列要含只在"实际"侧出现的工具，否则矩阵会漏列
    assert ev._columns(matrix) == ["a", "b"]


def test_confusion_matrix_column_includes_unexpected_tool():
    details = [{"question": "q", "expected": "a", "actual": ev.NO_TOOL, "hit": False}]
    assert ev._columns(ev.confusion_matrix(details)) == ["a", ev.NO_TOOL]


def test_build_report_has_all_sections():
    details = [
        {"question": "怎么上传简历", "expected": "platform_help", "actual": "platform_help", "hit": True},
        {"question": "TCP 握手", "expected": "kb_search", "actual": "platform_help", "hit": False},
    ]
    report = ev.build_report(details)
    for section in ("## 汇总", "## 分工具命中率", "## 混淆矩阵", "## 逐题明细", "## 误选清单"):
        assert section in report
    # 汇总与逐题明细都要落到具体题面与工具名上
    assert "1/2 (50.0%)" in report
    assert "platform_help" in report and "kb_search" in report
    assert "| 1 | 怎么上传简历 | platform_help | platform_help | ✅ |" in report
    assert "「TCP 握手」期望 kb_search，实际 platform_help" in report


# —— main ——


@pytest.fixture
def _three_cases(monkeypatch):
    cases = [
        {"question": "q1", "expected_tool": "kb_search"},
        {"question": "q2", "expected_tool": "platform_help"},
        {"question": "q3", "expected_tool": "resume_lookup"},
    ]
    monkeypatch.setattr(ev, "load_cases", lambda *_a, **_k: cases)
    return cases


def test_main_writes_report(monkeypatch, tmp_path, _three_cases):
    report_path = tmp_path / "report.md"
    monkeypatch.setattr(ev, "REPORT_PATH", report_path)
    monkeypatch.setattr(
        ev,
        "build_chat_llm",
        lambda *_a, **_k: _FakeSelector(["kb_search", "platform_help", "kb_search"]),
    )
    ev.main()
    assert report_path.exists()
    text = report_path.read_text(encoding="utf-8")
    assert "2/3 (66.7%)" in text
    assert "## 混淆矩阵" in text


def test_main_aborts_without_report_when_llm_unavailable(monkeypatch, tmp_path, _three_cases):
    report_path = tmp_path / "report.md"
    monkeypatch.setattr(ev, "REPORT_PATH", report_path)
    monkeypatch.setattr(
        ev,
        "build_chat_llm",
        lambda *_a, **_k: _FakeSelector([], error=RuntimeError("402 insufficient balance")),
    )
    with pytest.raises(SystemExit) as excinfo:
        ev.main()
    assert excinfo.value.code == 2
    assert not report_path.exists()


def test_main_aborts_when_ai_not_configured(monkeypatch, tmp_path, _three_cases):
    report_path = tmp_path / "report.md"
    monkeypatch.setattr(ev, "REPORT_PATH", report_path)

    def _not_configured(*_a, **_k):
        raise ValueError("AI 服务未配置，请在 backend/.env 填写 AI_BASE_URL 与 AI_API_KEY")

    monkeypatch.setattr(ev, "build_chat_llm", _not_configured)
    with pytest.raises(SystemExit) as excinfo:
        ev.main()
    assert excinfo.value.code == 2
    assert not report_path.exists()
