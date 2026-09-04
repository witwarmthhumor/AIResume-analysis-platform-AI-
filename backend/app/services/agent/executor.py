"""Agent 执行器（v3.4）：组装 AgentExecutor，并把"工具过程 + 最终回答"全程流式产出。

为什么用后台线程 + queue.Queue：项目是同步栈（SQLAlchemy 同步 Session、FastAPI 同步路由），
而 LangChain AgentExecutor.invoke 是阻塞调用。把 invoke 放进 daemon 线程，
通过 BaseCallbackHandler 把"工具开始/工具结果/逐字 token"推进线程安全队列，
主生成器从队列取事件并以 SSE 下发——既拿到真流式，又不引入异步 DB 会话。

事件类型（dict 的 type 字段）：
- action：开始调工具 {tool,input}
- reset：工具决策轮开始前的提示 {无字段}——模型可能在决策轮同时吐出文本 token（已被
  当作 delta 下发），这些文字属于中间过程而非最终回答，前端应清空累计的回答内容
- observation：工具返回 {tool,preview}
- delta：最终回答的逐字片段 {content}
- error：可恢复错误（单次工具/LLM 失败）{content}
- final：正常结束 {output,steps,tokens_total}
- fatal：执行器整体异常 {content}
"""

import queue
import threading
from collections.abc import Generator, Sequence

from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import BaseTool

from app.core.config import Settings, settings
from app.core.logging import get_logger

logger = get_logger(__name__)

AGENT_SYSTEM_PROMPT = (
    "你是「AI 简历分析与模拟面试」平台的 AI 客服，负责解答计算机技术学习与技术面试问题"
    "（涵盖 Java/JVM/并发编程、MySQL、Redis、计算机网络、操作系统、RAG 与 AI 应用开发等），"
    "也解答平台功能使用问题。\n"
    "工作规则：\n"
    "1. 遇到具体技术知识点，必须先调用 kb_search 工具检索平台知识库，并优先依据检索结果作答，不要编造；\n"
    "2. 若知识库未命中，可用你掌握的通用编程知识简要回答，并说明这部分不来自平台知识库；\n"
    "3. 平台使用类问题（如何上传简历、如何开始模拟面试、某功能在哪）直接清晰回答；\n"
    "4. 用中文、分点适度、简洁作答，不要输出 markdown 代码块以外的多余符号。"
)

# 工具入参/结果在前端与 steps 里的预览长度
_PREVIEW_LIMIT = 200


def build_agent_executor(
    llm: BaseChatModel,
    tools: Sequence[BaseTool],
    runtime_settings: Settings | None = None,
) -> AgentExecutor:
    """组装 openai-tools Agent + Executor。prompt 预留历史与 scratchpad 占位。"""
    s = runtime_settings or settings
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", AGENT_SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )
    agent_runnable = create_openai_tools_agent(llm, list(tools), prompt)
    return AgentExecutor(
        agent=agent_runnable,
        tools=list(tools),
        max_iterations=s.agent_max_iterations,
        handle_parsing_errors=True,  # 模型输出解析异常时回传纠错而非直接 500
        verbose=False,
        return_intermediate_steps=False,
    )


def build_history(rows: Sequence[tuple[str, str]], turns: int) -> list[BaseMessage]:
    """把库内最近 N 轮 (role, content) 转成 LangChain 消息（工具步骤不进上下文）。"""
    history: list[BaseMessage] = []
    for role, content in rows[-turns * 2 :]:
        if role == "user":
            history.append(HumanMessage(content=content))
        elif role == "assistant":
            history.append(AIMessage(content=content))
    return history


class _QueueCallback(BaseCallbackHandler):
    """把 LangChain 执行回调转成队列事件，同时累计工具步骤与 token 数。"""

    def __init__(
        self, q: "queue.Queue[dict | None]", steps: list[dict], tokens: dict
    ) -> None:
        self._q = q
        self._steps = steps
        self._tokens = tokens

    # —— 工具过程 ——
    def on_tool_start(self, serialized: dict, input_str: str, **kwargs) -> None:
        tool_name = (serialized or {}).get("name", "kb_search")
        preview_input = str(input_str)[:_PREVIEW_LIMIT]
        # 部分模型（如 DeepSeek 部分版本）在工具决策轮会同时吐出文本 token，
        # 已被 on_llm_new_token 当作 delta 下发；这些是中间过程不是最终回答，
        # 先发 reset 让前端清空累计的回答内容（done 事件本身也会整体覆盖）
        self._q.put({"type": "reset"})
        self._steps.append({"tool": tool_name, "input": preview_input, "preview": ""})
        self._q.put({"type": "action", "tool": tool_name, "input": preview_input})

    def on_tool_end(self, output, *, run_id=None, **kwargs) -> None:
        preview = str(output)[:_PREVIEW_LIMIT]
        if self._steps:
            self._steps[-1]["preview"] = preview
        self._q.put({"type": "observation", "preview": preview})

    def on_tool_error(self, error, *, run_id=None, **kwargs) -> None:
        self._q.put({"type": "error", "content": f"工具调用出错：{error}"})

    # —— 逐字 token（仅最终回答轮有可见文本，工具决策轮自然无 token）——
    def on_llm_new_token(self, token: str, **kwargs) -> None:
        if token and token.strip():
            self._q.put({"type": "delta", "content": token})

    def on_llm_end(self, response, **kwargs) -> None:
        # Agent 可能多轮调 LLM，累计各轮 token；langchain-openai 把用量放在 usage_metadata
        try:
            for gen_list in response.generations:
                for gen in gen_list:
                    um = getattr(gen.message, "usage_metadata", None)
                    if um:
                        self._tokens["prompt"] += um.get("input_tokens", 0) or 0
                        self._tokens["completion"] += um.get("output_tokens", 0) or 0
                    elif response.llm_output and "token_usage" in response.llm_output:
                        tu = response.llm_output["token_usage"]
                        self._tokens["prompt"] += getattr(tu, "prompt_tokens", 0) or 0
                        self._tokens["completion"] += (
                            getattr(tu, "completion_tokens", 0) or 0
                        )
        except Exception:
            logger.debug("agent token 累计失败", exc_info=True)

    def on_llm_error(self, error, **kwargs) -> None:
        self._q.put({"type": "error", "content": f"AI 模型调用出错：{error}"})


def stream_agent_events(
    executor: AgentExecutor,
    user_input: str,
    chat_history: Sequence[BaseMessage],
) -> Generator[dict, None, None]:
    """后台线程跑 executor，主生成器按到达顺序 yield 事件，直到 final/fatal 与哨兵。"""
    q: queue.Queue[dict | None] = queue.Queue()
    steps: list[dict] = []
    tokens = {"prompt": 0, "completion": 0}
    callback = _QueueCallback(q, steps, tokens)

    def _run() -> None:
        try:
            result = executor.invoke(
                {"input": user_input, "chat_history": list(chat_history)},
                config={"callbacks": [callback]},
            )
            output = str(result.get("output", "")).strip()
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
        except Exception as exc:
            logger.exception("agent executor 运行失败")
            q.put(
                {"type": "fatal", "content": f"AI 客服运行失败：{type(exc).__name__}"}
            )
        finally:
            q.put(None)  # 结束哨兵

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()

    while True:
        event = q.get()
        if event is None:
            break
        yield event
