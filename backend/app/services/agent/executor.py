"""Agent 执行器（v4.4 起，LangChain 1.x）：组装 create_agent 图，并把
"工具过程 + 最终回答"全程流式产出。

为什么用后台线程 + queue.Queue：项目是同步栈（SQLAlchemy 同步 Session、FastAPI 同步路由），
而 LangChain 1.x 的 create_agent 图原生是异步 astream_events。把事件泵放进 daemon 线程的
asyncio.run 里，将"工具开始/工具结果/逐字 token"推进线程安全队列，主生成器从队列取事件
并以 SSE 下发——既拿到真流式，又不引入异步 DB 会话。

1.x 迁移注记（docs/依赖链解冻调研报告.md）：0.3 的 AgentExecutor + BaseCallbackHandler 已移除，
本文件从"回调桥"改为"astream_events 桥"，事件映射 on_chat_model_stream→delta、
on_tool_start→reset+action、on_tool_end→observation，前端 SSE 协议零变化。

事件类型（dict 的 type 字段）：
- action：开始调工具 {tool,input}
- reset：工具决策轮开始前的提示 {无字段}——模型可能在决策轮同时吐出文本 token（已被
  当作 delta 下发），这些文字属于中间过程而非最终回答，前端应清空累计的回答内容
- observation：工具返回 {tool,preview}
- delta：最终回答的逐字片段 {content}
- error：可恢复错误（单次工具失败）{content}
- final：正常结束 {output,steps,tokens_total}
- fatal：执行器整体异常 {content}
"""

import asyncio
import queue
import threading
from collections.abc import Generator, Sequence
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import BaseTool

from app.core.config import Settings, settings
from app.core.logging import get_logger

logger = get_logger(__name__)

AGENT_SYSTEM_PROMPT = (
    "你是「AI 简历分析与模拟面试」平台的 AI 客服，负责解答计算机技术学习与技术面试问题"
    "（涵盖 Java/JVM/并发编程、MySQL、Redis、计算机网络、操作系统、RAG 与 AI 应用开发等），"
    "也解答平台功能使用问题，并能查询提问者本人的平台数据。\n"
    "工作规则：\n"
    "1. 遇到具体技术知识点，必须先调用 kb_search 工具检索平台知识库，并优先依据检索结果作答，不要编造；\n"
    "2. 若知识库未命中，可用你掌握的通用编程知识简要回答，并说明这部分不来自平台知识库；\n"
    "3. 涉及「我的简历 / 我的面试 / 我的用量」这类个人数据的问题，必须调用对应工具"
    "（resume_lookup / interview_history / usage_stats）取真实数据后再回答，"
    "不要凭猜测作答，也不要向用户索要简历内容等隐私信息；\n"
    "4. 平台使用类问题（如何上传简历、如何开始模拟面试、某功能在哪）直接清晰回答；\n"
    "5. 用中文、分点适度、简洁作答，不要输出 markdown 代码块以外的多余符号。"
)

# 工具入参/结果在前端与 steps 里的预览长度
_PREVIEW_LIMIT = 200


def _text_of(value: Any) -> str:
    """消息 content 的兜底取文本：str 原样，内容块列表抽取 text 段，其余 str()。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for block in value:
            if isinstance(block, dict):
                parts.append(str(block.get("text") or ""))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(value)


def _format_tool_input(raw: Any) -> str:
    """工具入参预览：astream_events 给的是参数 dict，压成 k=v 行比 repr(dict) 可读。"""
    if isinstance(raw, dict):
        return " ".join(f"{k}={v}" for k, v in raw.items()) or "{}"
    return str(raw)


def build_agent_executor(
    llm: BaseChatModel,
    tools: Sequence[BaseTool],
    runtime_settings: Settings | None = None,
) -> Any:
    """组装 1.x create_agent 图（内部即 langgraph，系统提示经 system_prompt 注入）。

    对话历史不再走 prompt 占位符，由 stream_agent_events 把历史消息并入输入。
    循环上限在流式阶段以 recursion_limit 落地（见 stream_agent_events）。
    """
    del runtime_settings  # 1.x 下无 Executor 参数可设，保留形参以稳定调用方签名
    return create_agent(llm, list(tools), system_prompt=AGENT_SYSTEM_PROMPT)


def build_history(rows: Sequence[tuple[str, str]], turns: int) -> list[BaseMessage]:
    """把库内最近 N 轮 (role, content) 转成 LangChain 消息（工具步骤不进上下文）。"""
    history: list[BaseMessage] = []
    for role, content in rows[-turns * 2 :]:
        if role == "user":
            history.append(HumanMessage(content=content))
        elif role == "assistant":
            history.append(AIMessage(content=content))
    return history


def stream_agent_events(
    agent: Any,
    user_input: str,
    chat_history: Sequence[BaseMessage],
) -> Generator[dict, None, None]:
    """后台线程泵 astream_events，主生成器按到达顺序 yield 事件，直到 final/fatal 与哨兵。

    agent：build_agent_executor 返回的图（任何实现 astream_events 的可调用均可，便于测试替身）。
    """
    q: queue.Queue[dict | None] = queue.Queue()
    steps: list[dict] = []
    tokens = {"prompt": 0, "completion": 0}
    messages = [*chat_history, HumanMessage(content=user_input)]

    async def _pump() -> None:
        # 1.x 的循环上限：recursion_limit 计的是图步（模型节点+工具节点各 1 步），
        # 按"最大工具轮数 ×2 + 余量"折算，语义对齐 0.3 时代的 max_iterations
        config = {"recursion_limit": max(settings.agent_max_iterations * 2 + 2, 8)}
        answer_parts: list[str] = []  # 自最近 reset 起的累计回答（与前端累计口径一致）
        last_plain_answer = ""  # 最后一条无工具调用的 AIMessage 文本（兜底）

        async for ev in agent.astream_events(
            {"messages": messages}, version="v2", config=config
        ):
            kind = ev.get("event")

            if kind == "on_chat_model_stream":
                chunk = ev["data"].get("chunk")
                text = _text_of(getattr(chunk, "content", ""))
                if text:
                    answer_parts.append(text)
                    q.put({"type": "delta", "content": text})

            elif kind == "on_tool_start":
                # 部分模型（如 DeepSeek）在工具决策轮会同时吐出文本 token，已被当作
                # delta 下发；这些是中间过程不是最终回答，先发 reset 让前端清空
                name = ev.get("name") or "unknown_tool"
                preview_input = _format_tool_input(ev["data"].get("input"))[
                    :_PREVIEW_LIMIT
                ]
                answer_parts.clear()
                steps.append({"tool": name, "input": preview_input, "preview": ""})
                q.put({"type": "reset"})
                q.put({"type": "action", "tool": name, "input": preview_input})

            elif kind == "on_tool_end":
                output = ev["data"].get("output")
                preview = _text_of(getattr(output, "content", output))[:_PREVIEW_LIMIT]
                if getattr(output, "status", None) == "error":
                    # 图内工具异常由 ToolNode 转成 status=error 的 ToolMessage；
                    # 只回中性话术：原始异常文本可能携带 SQL 片段/内部路径，经 SSE 下发有泄露风险
                    q.put({"type": "error", "content": "工具调用出错，请稍后重试"})
                    continue
                if steps:
                    steps[-1]["preview"] = preview
                q.put({"type": "observation", "preview": preview})

            elif kind == "on_chat_model_end":
                out = ev["data"].get("output")
                um = getattr(out, "usage_metadata", None)
                if um:
                    tokens["prompt"] += um.get("input_tokens", 0) or 0
                    tokens["completion"] += um.get("output_tokens", 0) or 0
                if isinstance(out, AIMessage) and not getattr(out, "tool_calls", None):
                    last_plain_answer = _text_of(out.content)

        # 正常结束：最终回答 = 累计 delta；理论为空时退最后一条纯文本 AI 消息
        output = "".join(answer_parts).strip() or last_plain_answer.strip()
        q.put(
            {
                "type": "final",
                "output": output,
                "steps": steps,
                "tokens_total": tokens["prompt"] + tokens["completion"],
                "tokens_prompt": tokens["prompt"],
                "tokens_completion": tokens["completion"],
            }
        )

    def _run() -> None:
        try:
            asyncio.run(_pump())
        except Exception as exc:
            logger.exception("agent executor 运行失败")
            q.put(
                {"type": "fatal", "content": f"AI 客服运行失败：{type(exc).__name__}"}
            )
        finally:
            q.put(None)  # 结束哨兵

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()

    sentinel_seen = False
    try:
        while True:
            event = q.get()
            if event is None:
                sentinel_seen = True
                break
            yield event
    finally:
        # 消费者中途断开（GeneratorExit）也要把队列读到哨兵：等 worker 自然结束。
        # worker 的工具闭包持有请求级 db Session（非线程安全），调用方只有在
        # 哨兵消费后才能安全地用同一 Session 补账/落库。
        # 正常路径哨兵已在本循环消费（sentinel_seen=True），绝不能再等第二次
        if not sentinel_seen:
            while q.get() is not None:
                pass
