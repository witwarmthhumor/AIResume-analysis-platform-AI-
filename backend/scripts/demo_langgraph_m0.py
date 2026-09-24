"""M0 版本门控 demo（PRD v4.0 FR-1）：2 节点图 + 同步 PostgresSaver + interrupt/resume。

验证三件事（PRD §4.2 / 评审 D3）：
1. langgraph 0.2.76 与 langchain-core 0.3.86 同环境可用（依赖矩阵实测结论的运行时确认）；
2. 同步 PostgresSaver 在独立 langgraph schema 内建表并持久化 checkpoint（与业务表隔离，绕过
   Alembic 的三张表按 D3 方案治理）；
3. interrupt() 挂起 → Command(resume=...) 断点续跑，且中断点之前的节点不重复执行。

用法（backend/ 目录）：./.venv/Scripts/python -m scripts.demo_langgraph_m0
退出码：全部断言通过 0，任一失败 1。
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import operator
from typing import Annotated, TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg import connect

from app.core.config import settings


class DemoState(TypedDict):
    """demo 共享状态：log 用 operator.add 累加，便于统计各节点实际执行次数。"""

    log: Annotated[list[str], operator.add]


def node_a(state: DemoState) -> dict:
    """前置节点：interrupt 之前的步骤（断点续跑后不得重复执行）。"""
    return {"log": ["node_a"]}


def node_b(state: DemoState) -> dict:
    """风险动作节点：模拟 HITL——首次执行到此挂起，等人工决定。"""
    decision = interrupt({"question": "是否继续执行 node_c？", "risk": "demo"})
    return {"log": [f"node_b(decision={decision})"]}


def node_c(state: DemoState) -> dict:
    return {"log": ["node_c"]}


def build_graph(checkpointer):
    builder = StateGraph(DemoState)
    builder.add_node("node_a", node_a)
    builder.add_node("node_b", node_b)
    builder.add_node("node_c", node_c)
    builder.add_edge(START, "node_a")
    builder.add_edge("node_a", "node_b")
    builder.add_edge("node_b", "node_c")
    builder.add_edge("node_c", END)
    return builder.compile(checkpointer=checkpointer)


def main() -> None:
    # 连接串：去掉 SQLAlchemy 方言后缀（PostgresSaver 直连 psycopg3），
    # 并把 search_path 指到独立 langgraph schema（D3：checkpoint 表与业务表隔离）
    base_dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    # search_path 经 URI query 传入（conninfo 尾部拼空格参数会被解析器拒绝）
    dsn = f"{base_dsn}?options=-csearch_path%3Dlanggraph%2Cpublic"

    with connect(base_dsn) as conn:  # 建独立 schema（幂等）
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS langgraph")
    print("[1/4] langgraph schema 就绪")

    with PostgresSaver.from_conn_string(dsn) as checkpointer:
        checkpointer.setup()  # 幂等建 checkpoint 三表（在 langgraph schema 内）
        print("[2/4] checkpoint 表就绪（langgraph schema）")

        graph = build_graph(checkpointer)
        # 每次运行用唯一 thread_id：checkpoint 跨运行持久化，同 id 会把上次的状态合并进来
        thread = {"configurable": {"thread_id": f"m0-demo-{uuid.uuid4().hex[:8]}"}}

        # 第一次运行：应停在 node_b 的 interrupt 上（0.2.x 的挂起信息在 get_state，
        # values 字典不带 __interrupt__ 键）
        r1 = graph.invoke({"log": []}, thread)
        state = graph.get_state(thread)
        pending = state.next
        interrupts = state.tasks[0].interrupts if state.tasks else ()
        assert "node_b" in pending, f"挂起点不在 node_b：{pending}"
        assert interrupts and interrupts[0].resumable, f"无可恢复中断：{interrupts}"
        assert r1["log"].count("node_a") == 1
        print("[3/4] interrupt 挂起成功（停在 node_b，node_a 已执行 1 次）")

        # 人工决定后断点续跑：node_a 不重复执行，node_b 拿到 resume 值
        r2 = graph.invoke(Command(resume="approved"), thread)
        assert r2["log"].count("node_a") == 1, f"node_a 被重复执行：{r2['log']}"
        assert "node_b(decision=approved)" in r2["log"]
        assert "node_c" in r2["log"]
        assert graph.get_state(thread).next == ()
        print("[4/4] 断点续跑成功（node_a 未重复，全图完成）")

    print(
        "\nM0 demo 全部断言通过：版本兼容 / checkpoint 持久化 / interrupt-resume 均可用。"
    )
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"\nM0 demo 失败：{exc}")
        sys.exit(1)
