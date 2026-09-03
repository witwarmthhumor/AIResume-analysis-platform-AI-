"""大模型封装层：全项目唯一调 AI 的地方（PROJECT-PLAN §3）。

职责：发请求、JSON 输出校验 + 失败自动重试（最多 2 次）、SSE 流式输出、
超时与异常处理、token/耗时统计。换模型 = 改 .env 三行；换重试/校验策略 = 只改这个文件。
"""

import json
import time
from collections.abc import Generator
from dataclasses import dataclass

from openai import OpenAI

from app.core.config import Settings
from app.core.logging import get_logger
from app.schemas.analysis import AIReport
from app.services.prompts import SYSTEM_PROMPT, build_user_prompt

# 最多尝试 3 次 = 首次 + 2 次重试（PROJECT-PLAN 定的"失败自动重试最多 2 次"）
_MAX_ATTEMPTS = 3

logger = get_logger(__name__)


class AIError(Exception):
    """AI 调用最终失败（重试耗尽或网络异常）。message 为面向用户的话术。"""

    def __init__(self, message: str, raw_output: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.raw_output = raw_output  # 有响应但格式不合法时留档用


def _call_error_message(exc: Exception) -> str:
    """把 SDK 异常翻译成用户话术：4xx 是配置/账户问题（重试没用），其余按暂时不可用。"""
    status = getattr(exc, "status_code", None)
    if status in (400, 401, 403, 404):
        return (
            "AI 服务拒绝了请求（配置或账户问题，如 Key 无效、欠费、模型名错误），"
            "请联系站点管理员检查 AI 配置"
        )
    return "AI 服务暂时不可用，请稍后重试"


def _build_client(settings: Settings) -> OpenAI:
    """统一出口：未配置直接报错，配置齐了才造客户端。"""
    if not settings.ai_api_key or not settings.ai_base_url:
        raise AIError("AI 服务未配置，请在 backend/.env 填写 AI_BASE_URL 与 AI_API_KEY")
    return OpenAI(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        timeout=settings.ai_timeout_seconds,
    )


@dataclass
class AnalysisResult:
    report: dict  # 通过 AIReport 校验的结构化报告
    valid: bool  # 封装层返回即 True；False 只出现在异常携带的留档场景
    model_name: str
    tokens_prompt: int | None
    tokens_completion: int | None
    duration_ms: int


def _extract_json(content: str) -> dict:
    """从模型输出里抠出 JSON：容忍 ```json 围栏和前后杂文。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")  # 去围栏
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("输出中没有 JSON 对象")
    return json.loads(text[start : end + 1])


def chat_json(
    system_prompt: str, user_prompt: str, settings: Settings, validator
) -> AnalysisResult:
    """通用"结构化 JSON 调用"：发请求 → 校验 → 失败重试（最多 2 次）。

    validator 接收解析出的 dict，返回 pydantic 模型（校验失败抛异常即触发重试）。
    简历分析（AIReport）与面试结束评价（InterviewReport）共用这条路径。
    """
    client = _build_client(settings)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    started = time.monotonic()
    last_raw: str | None = None
    last_tokens: tuple[int | None, int | None] = (None, None)

    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = client.chat.completions.create(
                model=settings.ai_model,
                messages=messages,
                max_tokens=settings.ai_max_tokens,
                temperature=0.3,  # 低随机性：结构化输出要稳定
            )
        except Exception as exc:  # noqa: BLE001  SDK 异常种类随服务商变化，按类型给话术
            logger.error(
                "AI 调用失败 model=%s attempt=%d/%d exc=%s: %s",
                settings.ai_model,
                attempt + 1,
                _MAX_ATTEMPTS,
                type(exc).__name__,
                exc,
            )
            raise AIError(_call_error_message(exc), raw_output=last_raw) from None

        content = resp.choices[0].message.content or ""
        last_raw = content
        if resp.usage:
            last_tokens = (resp.usage.prompt_tokens, resp.usage.completion_tokens)

        try:
            validated = validator(_extract_json(content))
        except Exception as exc:  # noqa: BLE001  解析/校验失败 → 重试，耗尽后统一报错
            logger.warning(
                "AI 输出未通过 JSON 校验 model=%s attempt=%d/%d exc=%s",
                settings.ai_model,
                attempt + 1,
                _MAX_ATTEMPTS,
                type(exc).__name__,
            )
            continue

        return AnalysisResult(
            report=validated.model_dump(),
            valid=True,
            model_name=settings.ai_model,
            tokens_prompt=last_tokens[0],
            tokens_completion=last_tokens[1],
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    logger.error("AI 重试耗尽 model=%s attempts=%d", settings.ai_model, _MAX_ATTEMPTS)
    raise AIError(
        "AI 返回的格式不符合要求，已自动重试仍失败，请稍后重试", raw_output=last_raw
    )


def analyze_resume(resume_text: str, settings: Settings) -> AnalysisResult:
    """同步调用大模型分析简历，输出校验过的六块报告。"""
    return chat_json(
        SYSTEM_PROMPT, build_user_prompt(resume_text), settings, AIReport.model_validate
    )


def stream_chat(
    messages: list[dict], settings: Settings, usage_out: dict
) -> Generator[str, None, None]:
    """流式对话：逐段产出 AI 文字。usage_out 会就地填入 token 统计（流式响应 usage 在最后）。

    messages 为完整对话（含 system）；连接/流中断时抛 AIError，已产出的文字仍有效。
    """
    client = _build_client(settings)
    try:
        stream = client.chat.completions.create(
            model=settings.ai_model,
            messages=messages,
            max_tokens=settings.ai_max_tokens,
            temperature=0.6,  # 对话场景允许一点随机性
            stream=True,
            stream_options={"include_usage": True},  # DeepSeek/DashScope 兼容
        )
        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage_out["tokens_prompt"] = chunk.usage.prompt_tokens
                usage_out["tokens_completion"] = chunk.usage.completion_tokens
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content
    except Exception as exc:  # noqa: BLE001  流式链路异常统一给话术
        logger.error(
            "AI 流式调用中断 model=%s exc=%s: %s",
            settings.ai_model,
            type(exc).__name__,
            exc,
        )
        raise AIError(_call_error_message(exc)) from None
