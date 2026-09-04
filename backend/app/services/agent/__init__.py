"""AI 客服 Agent 服务包（v3.4，LangChain 局部引入）。

只在本包内使用 LangChain；现有简历分析/面试/Playground 的手写 RAG 链路不受影响。
- llm_factory：造 ChatOpenAI（OpenAI 兼容协议，复用 settings 的 AI 配置）
- tools：每请求构造工具集（第一版仅 kb_search，闭包隔离用户、回收引用来源）
- executor：造 AgentExecutor，并以"后台线程 + 队列 + 回调"产出全程流式事件
"""

from app.services.agent.executor import (
    AGENT_SYSTEM_PROMPT,
    build_agent_executor,
    build_history,
    stream_agent_events,
)
from app.services.agent.llm_factory import build_chat_llm
from app.services.agent.tools import ToolContext, make_tools

__all__ = [
    "AGENT_SYSTEM_PROMPT",
    "ToolContext",
    "build_agent_executor",
    "build_chat_llm",
    "build_history",
    "make_tools",
    "stream_agent_events",
]
