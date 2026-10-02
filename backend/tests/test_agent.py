"""v3.4 Agent 核心服务单测：历史裁剪、kb_search 工具、流式事件队列。

不打真实大模型与 Ollama：embedding/检索全部 monkeypatch；流式用 FakeExecutor 驱动回调，
只验证我们自己写的编排逻辑（队列、步骤、token 累计、兜底）。
"""

from types import SimpleNamespace

from langchain_core.messages import AIMessage

from app.services.agent import (
    ToolContext,
    build_agent_executor,
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


class _FakeAgent:
    """模拟 1.x create_agent 图：按顺序产出 astream_events 形状的事件。"""

    async def astream_events(self, payload, version, config):
        yield {
            "event": "on_tool_start",
            "name": "kb_search",
            "data": {"input": {"query": "RAG 原理"}},
        }
        yield {
            "event": "on_tool_end",
            "data": {
                "output": SimpleNamespace(content="【来源：RAG入门】RAG 先检索再生成")
            },
        }
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content="RAG")},
        }
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content=" 是检索增强生成")},
        }
        yield {
            "event": "on_chat_model_end",
            "data": {
                "output": SimpleNamespace(
                    usage_metadata={"input_tokens": 120, "output_tokens": 30}
                )
            },
        }


def test_stream_events_sequence() -> None:
    events = list(stream_agent_events(_FakeAgent(), "什么是 RAG", []))
    types = [e["type"] for e in events]
    # on_tool_start 前置 reset（防决策轮文本混入最终回答）
    assert types == ["reset", "action", "observation", "delta", "delta", "final"]

    assert events[1]["tool"] == "kb_search"
    assert events[1]["input"] == "query=RAG 原理"  # dict 入参压成 k=v 预览
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

    class _ThinkingAgent:
        async def astream_events(self, payload, version, config):
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="让我先想想…")},
            }
            yield {
                "event": "on_tool_start",
                "name": "kb_search",
                "data": {"input": {"query": "索引失效场景"}},
            }
            yield {
                "event": "on_tool_end",
                "data": {"output": SimpleNamespace(content="【来源】最左前缀原则…")},
            }
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="索引失效的常见场景是…")},
            }

    events = list(stream_agent_events(_ThinkingAgent(), "q", []))
    types = [e["type"] for e in events]
    assert types == ["delta", "reset", "action", "observation", "delta", "final"]
    # reset 出现在决策轮文本之后、工具开始之前
    assert events[0]["content"] == "让我先想想…"
    assert types.index("delta") < types.index("reset") < types.index("action")


def test_stream_events_on_real_1x_graph() -> None:
    """真 1.x 集成：GenericFakeChatModel 驱动 create_agent 真图跑完整事件桥。

    证明 astream_events 映射对真实图成立（上面两例只证明队列编排）：
    工具真执行（astream_events 产出 on_tool_start/end）→ 逐字 delta → final.output
    等于模型最终文本。全程离线，不联网。
    """
    import json
    import re

    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
    from langchain_core.tools import tool

    @tool
    def echo_tool(query: str) -> str:
        """回显入参（测试替身）。"""
        return f" Echo:{query}"

    class _ScriptedChatModel(BaseChatModel):
        """两轮脚本的假模型：_stream 把 tool_calls 转 tool_call_chunks、content 拆词。

        GenericFakeChatModel 不流式产出 tool_calls（1.x 实测），驱动不了真图的
        ToolNode，故自写。create_agent 组图会 bind_tools，恒等返回自身。
        """

        responses: list[AIMessage]

        @property
        def _llm_type(self) -> str:
            return "scripted-fake"

        def bind_tools(self, tools, **kwargs):
            return self

        def _next(self) -> AIMessage:
            return self.responses.pop(0) if self.responses else AIMessage(content="")

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=self._next())])

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            message = self._next()
            for tcall in message.tool_calls or []:
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "name": tcall["name"],
                                "args": json.dumps(tcall["args"]),
                                "id": tcall["id"],
                                "index": 0,
                            }
                        ],
                    )
                )
            for token in re.split(r"(\s)", str(message.content)):
                if token:
                    yield ChatGenerationChunk(message=AIMessageChunk(content=token))

    model = _ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "echo_tool", "args": {"query": "RAG"}, "id": "c1"}
                ],
            ),
            AIMessage(content="RAG 是检索增强生成"),
        ]
    )
    agent = build_agent_executor(model, [echo_tool])
    events = list(stream_agent_events(agent, "什么是 RAG", []))
    types = [e["type"] for e in events]

    assert "reset" in types and "action" in types and "observation" in types
    action = next(e for e in events if e["type"] == "action")
    assert action["tool"] == "echo_tool"
    observation = next(e for e in events if e["type"] == "observation")
    assert "Echo:RAG" in observation["preview"]
    final = events[-1]
    assert final["type"] == "final"
    assert final["output"] == "RAG 是检索增强生成"
    assert final["steps"][0]["tool"] == "echo_tool"


def test_stream_events_fatal_on_exception() -> None:
    """executor 抛异常时产出 fatal 而非让生成器崩掉。"""

    class _Boom:
        async def astream_events(self, payload, version, config):
            raise RuntimeError("kaboom")
            yield  # pragma: no cover - 使其成为异步生成器

    events = list(stream_agent_events(_Boom(), "q", []))
    assert len(events) == 1
    assert events[0]["type"] == "fatal"
    assert "AI 客服运行失败" in events[0]["content"]
