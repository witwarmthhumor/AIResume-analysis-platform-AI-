"""Agent 工具路由评测脚本（v3.5+ 工具扩展期）：问法 → 期望工具，量化模型"选对工具"的比例。

与 scripts/eval_rag.py 的分工：eval_rag 量**检索**质量（预期文档有没有被排上来），
本脚本量**路由**质量（模型面对用户问法时选了哪个工具）。工具越加越多（4 → 8 → 11），
路由准确率是判断"要不要拆多 Agent"的唯一硬指标。

做法：把真实工具对象的 schema（含 US-006 校准过的 docstring）bind 给 ChatOpenAI，
每题只发**一次** LLM 调用，读返回消息里的 tool_calls 取首个工具名。
工具执行体全程不被调用——既不需要数据库，也不跑检索/向量化，
所以本脚本不碰任何业务数据，只依赖一条可用的 LLM 通道。

LLM 不可用（欠费 402、网络不通、Key 没配）时脚本**明确报错并以非零码退出，不写报告**，
避免把"0 条结果"的空报告当成评测产物。

用法（backend/ 目录下）：
    ./.venv/Scripts/python.exe -m scripts.eval_agent_routing
"""

import json
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

# 评测脚本以 -m scripts.xxx 运行，需把 backend/ 加进 sys.path 才能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
)

from app.core.config import settings
from app.services.agent import (
    AGENT_SYSTEM_PROMPT,
    ToolContext,
    build_chat_llm,
    make_tools,
)

CASES_PATH = (
    Path(__file__).resolve().parents[1].parent / "data" / "agent_eval" / "routing.json"
)
REPORT_PATH = CASES_PATH.parent / "report.md"
# 模型没走工具、直接文字作答时的占位（计入未命中）
NO_TOOL = "(未调用工具)"
# 路由评测要可复现：强制温度 0，避免同一份用例集两次跑出两个准确率
EVAL_TEMPERATURE = 0
# 参照系：工具从 8 个扩到 11 个之前实测的基线（US-007，2026-09-16，24 题全中）
BASELINE = {"label": "8 工具口径基线（US-007）", "total": 24, "hit_count": 24}


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    """读取用例集：每条含 question 与 expected_tool。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        {"question": item["question"], "expected_tool": item["expected_tool"]}
        for item in raw
    ]


def pick_tool(message: BaseMessage) -> str:
    """从一次 LLM 返回里取它选中的工具名；没走工具调用返回 NO_TOOL 占位。"""
    calls = getattr(message, "tool_calls", None) or []
    return str(calls[0]["name"]) if calls else NO_TOOL


def run(cases: Sequence[dict], ask: Callable[[str], BaseMessage]) -> list[dict]:
    """逐题发起一次选择调用，产出逐题明细。

    ask 是"问一句 → 一条模型消息"的函数，脚本本体和测试各自注入实现。
    """
    details = []
    for case in cases:
        actual = pick_tool(ask(case["question"]))
        expected = case["expected_tool"]
        details.append(
            {
                "question": case["question"],
                "expected": expected,
                "actual": actual,
                "hit": actual == expected,
            }
        )
    return details


def summarize(details: Sequence[dict]) -> dict:
    """汇总：总题数、命中数与 top-1 准确率。"""
    total = len(details)
    hits = sum(1 for d in details if d["hit"])
    ratio = hits / total if total else 0.0
    return {
        "total": total,
        "hit_count": hits,
        "accuracy": ratio,
        "accuracy_text": f"{hits}/{total} ({ratio:.1%})" if total else "-",
    }


def confusion_matrix(details: Sequence[dict]) -> dict[str, dict[str, int]]:
    """混淆矩阵：{期望工具: {实际工具: 计数}}，行是期望、列是实际。"""
    matrix: dict[str, dict[str, int]] = {}
    for d in details:
        row = matrix.setdefault(d["expected"], {})
        row[d["actual"]] = row.get(d["actual"], 0) + 1
    return matrix


def _columns(matrix: dict[str, dict[str, int]]) -> list[str]:
    """矩阵列顺序：先按期望工具出现顺序，再补上只在"实际"侧出现的工具。"""
    columns = list(matrix)
    for row in matrix.values():
        for name in row:
            if name not in columns:
                columns.append(name)
    return columns


def _per_tool_stats(details: Sequence[dict]) -> list[dict]:
    """每个期望工具的用例数与命中率（按首次出现顺序）。"""
    stats: dict[str, dict] = {}
    for d in details:
        item = stats.setdefault(d["expected"], {"total": 0, "hit": 0})
        item["total"] += 1
        item["hit"] += 1 if d["hit"] else 0
    return [
        {
            "tool": tool,
            "total": item["total"],
            "hit": item["hit"],
            "rate": f"{item['hit'] / item['total']:.0%}",
        }
        for tool, item in stats.items()
    ]


def build_report(details: Sequence[dict], cases_path: Path = CASES_PATH) -> str:
    """把逐题明细渲染成 markdown 报告（汇总 + 分工具 + 混淆矩阵 + 逐题 + 误选）。"""
    stats = summarize(details)
    matrix = confusion_matrix(details)
    columns = _columns(matrix)
    per_tool = _per_tool_stats(details)
    misses = [d for d in details if not d["hit"]]

    delta = (stats["accuracy"] - BASELINE["hit_count"] / BASELINE["total"]) * 100
    lines = [
        "# Agent 工具路由评测报告",
        "",
        f"- 时间：{datetime.now(timezone.utc).astimezone():%Y-%m-%d %H:%M}",
        f"- 模型：{settings.ai_model}（temperature {EVAL_TEMPERATURE}）",
        f"- 用例集：{cases_path.name if cases_path.name else cases_path}（{stats['total']} 题 / {len(per_tool)} 个工具）",
        "- 口径：每题一次 LLM 调用做工具选择，工具执行体不运行，只统计选中的工具名",
        "",
        "## 汇总",
        "",
        f"- top-1 准确率：{stats['accuracy_text']}",
        f"- 命中 {stats['hit_count']} 题，误选 {len(misses)} 题",
        f"- 对比 {BASELINE['label']}（{BASELINE['hit_count']}/{BASELINE['total']}）：{delta:+.1f} 个百分点",
        "",
        "## 分工具命中率",
        "",
        "| 期望工具 | 用例数 | 命中 | 命中率 |",
        "| --- | --- | --- | --- |",
    ]
    for item in per_tool:
        lines.append(
            f"| {item['tool']} | {item['total']} | {item['hit']} | {item['rate']} |"
        )

    lines += [
        "",
        "## 混淆矩阵（行=期望工具，列=实际选中）",
        "",
        "| 期望 \\ 实际 | " + " | ".join(columns) + " | 合计 |",
        "| --- | " + " | ".join(["---"] * len(columns)) + " | --- |",
    ]
    for expected, row in matrix.items():
        cells = [str(row.get(name, 0)) if row.get(name) else "-" for name in columns]
        lines.append(
            f"| {expected} | " + " | ".join(cells) + f" | {sum(row.values())} |"
        )

    lines += [
        "",
        "## 逐题明细",
        "",
        "| # | 问题 | 期望 | 实际 | 命中 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for index, d in enumerate(details, start=1):
        flag = "✅" if d["hit"] else "❌"
        lines.append(
            f"| {index} | {d['question']} | {d['expected']} | {d['actual']} | {flag} |"
        )

    lines += ["", "## 误选清单", ""]
    if misses:
        for d in misses:
            lines.append(
                f"- 「{d['question']}」期望 {d['expected']}，实际 {d['actual']}"
            )
    else:
        lines.append("- 无（全部命中）")

    return "\n".join(lines)


def build_selector(tools: Sequence) -> BaseChatModel:
    """把工具 schema 绑到模型上，供路由选择调用（温度固定 0）。"""
    return build_chat_llm().bind(temperature=EVAL_TEMPERATURE).bind_tools(list(tools))


def _make_ask(selector: BaseChatModel) -> Callable[[str], BaseMessage]:
    """一问一答的调用体：与线上 Agent 同系统提示词，不带历史（路由只看当前问法）。"""

    def ask(question: str) -> BaseMessage:
        return selector.invoke(
            [SystemMessage(content=AGENT_SYSTEM_PROMPT), HumanMessage(content=question)]
        )

    return ask


def main() -> None:
    cases = load_cases()
    # 只借工具的 schema（含描述）做选择，db 传 None 也不会被执行
    tools = make_tools(None, None, None, ToolContext())
    try:
        selector = build_selector(tools)
    except ValueError as exc:
        print(f"[中止] {exc}")
        sys.exit(2)

    try:
        details = run(cases, _make_ask(selector))
    except Exception as exc:  # noqa: BLE001  LLM 侧异常种类随服务商变化，统一中止退出
        # LLM 不可用（欠费/超时/鉴权）时绝不产出空报告，避免被当成评测结果
        print(f"[中止] LLM 调用失败（{type(exc).__name__}）：{exc}")
        print("未写入报告，请确认 backend/.env 的 AI 配置与账户余额后重跑。")
        sys.exit(2)

    report = build_report(details)
    print(report)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\n[已写入] {REPORT_PATH}")

    stats = summarize(details)
    print(f"\n[top-1] {stats['accuracy_text']}")


if __name__ == "__main__":
    main()
