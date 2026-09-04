"""Agent 用的聊天模型工厂（v3.4）。

复用项目统一的 OpenAI 兼容配置（base_url/api_key/model/timeout 都来自 settings，
即 .env 的同一套通义/DeepSeek 配置），只额外打开 streaming 以支撑逐字流式输出。
换模型仍然只改 backend/.env，不改这里。
"""

from langchain_openai import ChatOpenAI

from app.core.config import Settings, settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def build_chat_llm(runtime_settings: Settings | None = None) -> ChatOpenAI:
    """构造 Agent 用 ChatOpenAI。未配置 Key/base_url 时抛 ValueError（由 API 层转 503）。"""
    s = runtime_settings or settings
    if not s.ai_api_key or not s.ai_base_url:
        raise ValueError(
            "AI 服务未配置，请在 backend/.env 填写 AI_BASE_URL 与 AI_API_KEY"
        )
    return ChatOpenAI(
        model=s.ai_model,
        base_url=s.ai_base_url,
        api_key=s.ai_api_key,
        timeout=s.ai_timeout_seconds,
        temperature=0.4,  # 客服问答：比结构化分析稍灵活，比纯闲聊稳定
        streaming=True,  # 打开 token 级流式，配合 BaseCallbackHandler.on_llm_new_token
        max_tokens=s.ai_max_tokens,
    )
