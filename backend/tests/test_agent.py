"""v3.4 Agent 核心服务单测：历史裁剪、kb_search 工具、流式事件队列。

不打真实大模型与 Ollama：embedding/检索全部 monkeypatch；流式用 FakeExecutor 驱动回调，
只验证我们自己写的编排逻辑（队列、步骤、token 累计、兜底）。
"""

from types import SimpleNamespace

from app.services.agent import (
    ToolContext,
    build_history,
    make_tools,
    stream_agent_events,
)

_FAKE_VEC = [0.1] * 768


# —— build_history ——


def test_build_history_maps_roles_and_truncates() -> None:
    """最近 N 轮：3 轮共 6 条、turns=2 时只保留最后 4 条，类型映射正确。"""
    rows = [
        ("user", "u1"),
        ("assistant", "a1"),
        ("user", "u2"),
        ("assistant", "a2"),
        ("user", "u3"),
        ("assistant", "a3"),
    ]
    history = build_history(rows, turns=2)
    assert len(history) == 4
    assert [m.content for m in history] == ["u2", "a2", "u3", "a3"]
    assert history[0].type == "human"
    assert history[1].type == "ai"


def test_build_history_skips_unknown_role() -> None:
    rows = [("user", "u"), ("system", "忽略"), ("assistant", "a")]
    history = build_history(rows, turns=5)
    assert [m.content for m in history] == ["u", "a"]


# —— make_tools / kb_search ——


def test_kb_search_hit_populates_citations(monkeypatch) -> None:
    """命中：ctx.citations 回填来源，返回给 LLM 的文本含资料片段。"""
    monkeypatch.setattr(
        "app.services.agent_capabilities.embed_texts", lambda texts: [_FAKE_VEC]
    )
    hits = [
        {
            "document_id": 1,
            "title": "RAG 入门",
            "seq": 0,
            "content": "RAG 先检索再生成。",
            "similarity": 0.81,
        }
    ]
    monkeypatch.setattr(
        "app.services.agent_capabilities.search_chunks", lambda *a, **k: hits
    )

    ctx = ToolContext()
    tools = make_tools(db=None, user_id=None, anonymous_id="anon", ctx=ctx)
    result = tools[0].invoke({"query": "什么是 RAG"})

    assert "RAG 入门" in result
    assert ctx.citations == [
        {"document_id": 1, "title": "RAG 入门", "seq": 0, "similarity": 0.81}
    ]


def test_kb_search_empty_clears_citations(monkeypatch) -> None:
    """未命中：清空上一轮残留引用，并提示 Agent 走通用知识。"""
    monkeypatch.setattr(
        "app.services.agent_capabilities.embed_texts", lambda texts: [_FAKE_VEC]
    )
    monkeypatch.setattr(
        "app.services.agent_capabilities.search_chunks", lambda *a, **k: []
    )

    ctx = ToolContext(citations=[{"document_id": 9, "title": "旧", "seq": 0}])
    tools = make_tools(db=None, user_id=None, anonymous_id="anon", ctx=ctx)
    result = tools[0].invoke({"query": "无关问题 xyz"})

    assert "没有检索到" in result
    assert ctx.citations == []


def test_kb_search_embedding_failure_fallback(monkeypatch) -> None:
    """embedding 服务异常时工具不抛，返回兜底提示让 Agent 继续。"""

    def _boom(texts):
        raise RuntimeError("ollama down")

    monkeypatch.setattr("app.services.agent_capabilities.embed_texts", _boom)
    ctx = ToolContext()
    tools = make_tools(db=None, user_id=None, anonymous_id="anon", ctx=ctx)
    result = tools[0].invoke({"query": "q"})
    assert "暂时不可用" in result


# —— stream_agent_events（FakeExecutor 驱动回调，验证队列编排）——


class _FakeExecutor:
    """模拟 AgentExecutor.invoke：按顺序触发回调后返回 output。"""

    def invoke(self, payload: dict, config: dict) -> dict:
        cb = config["callbacks"][0]
        cb.on_tool_start({"name": "kb_search"}, "RAG 原理")
        cb.on_tool_end("【来源：RAG入门】RAG 先检索再生成")
        cb.on_llm_new_token("RAG")
        cb.on_llm_new_token(" 是检索增强生成")
        # 用 SimpleNamespace 模拟 LLMResult 的 usage_metadata 累计
        msg = SimpleNamespace(usage_metadata={"input_tokens": 120, "output_tokens": 30})
        gen = SimpleNamespace(message=msg)
        resp = SimpleNamespace(generations=[[gen]], llm_output=None)
        cb.on_llm_end(resp)
        return {"output": "RAG 是检索增强生成"}


def test_stream_events_sequence() -> None:
    events = list(stream_agent_events(_FakeExecutor(), "什么是 RAG", []))
    types = [e["type"] for e in events]
    # on_tool_start 前置 reset（防决策轮文本混入最终回答）
    assert types == ["reset", "action", "observation", "delta", "delta", "final"]

    assert events[1]["tool"] == "kb_search"
    assert events[1]["input"] == "RAG 原理"
    assert "RAG入门" in events[2]["preview"]
    assert events[3]["content"] == "RAG"

    final = events[-1]
    assert final["output"] == "RAG 是检索增强生成"
    assert final["steps"][0]["tool"] == "kb_search"
    assert final["tokens_total"] == 150
    assert final["tokens_prompt"] == 120 and final["tokens_completion"] == 30


def test_intermediate_turn_text_is_reset_before_tool() -> None:
    """P2 回归：模型在工具决策轮吐出的文本 token 会被随后的 reset 事件打断。

    事件序列中的 delta（决策轮文本）→ reset → action 表明 API 层与前端
    可据此丢弃中间文字，最终回答只保留工具之后那轮的内容。
    """

    class _ThinkingExecutor:
        def invoke(self, payload: dict, config: dict) -> dict:
            cb = config["callbacks"][0]
            cb.on_llm_new_token("让我先想想…")  # 决策轮文本（不应留在最终回答里）
            cb.on_tool_start({"name": "kb_search"}, "索引失效场景")
            cb.on_tool_end("【来源】最左前缀原则…")
            cb.on_llm_new_token("索引失效的常见场景是…")
            return {"output": "索引失效的常见场景是…"}

    events = list(stream_agent_events(_ThinkingExecutor(), "q", []))
    types = [e["type"] for e in events]
    assert types == ["delta", "reset", "action", "observation", "delta", "final"]
    # reset 出现在决策轮文本之后、工具开始之前
    assert events[0]["content"] == "让我先想想…"
    assert types.index("delta") < types.index("reset") < types.index("action")


def test_stream_events_fatal_on_exception() -> None:
    """executor 抛异常时产出 fatal 而非让生成器崩掉。"""

    class _Boom:
        def invoke(self, payload, config):
            raise RuntimeError("kaboom")

    events = list(stream_agent_events(_Boom(), "q", []))
    assert len(events) == 1
    assert events[0]["type"] == "fatal"
    assert "AI 客服运行失败" in events[0]["content"]
