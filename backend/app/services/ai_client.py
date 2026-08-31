"""大模型封装层：全项目唯一调 AI 的地方（PROJECT-PLAN §3）。

职责：发请求、JSON 输出校验 + 失败自动重试（最多 2 次）、超时与异常处理、
token/耗时统计。换模型 = 改 .env 三行；换重试/校验策略 = 只改这个文件。
"""

import json
import time
from dataclasses import dataclass

from openai import OpenAI

from app.core.config import Settings
from app.schemas.analysis import AIReport
from app.services.prompts import SYSTEM_PROMPT, build_user_prompt

# 最多尝试 3 次 = 首次 + 2 次重试（PROJECT-PLAN 定的"失败自动重试最多 2 次"）
_MAX_ATTEMPTS = 3


class AIError(Exception):
    """AI 调用最终失败（重试耗尽或网络异常）。message 为面向用户的话术。"""

    def __init__(self, message: str, raw_output: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.raw_output = raw_output  # 有响应但格式不合法时留档用


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


def analyze_resume(resume_text: str, settings: Settings) -> AnalysisResult:
    """同步调用大模型分析简历。重试耗尽或网络异常 → AIError；成功 → 校验过的报告。"""
    if not settings.ai_api_key or not settings.ai_base_url:
        raise AIError("AI 服务未配置，请在 backend/.env 填写 AI_BASE_URL 与 AI_API_KEY")

    client = OpenAI(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        timeout=settings.ai_timeout_seconds,
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(resume_text)},
    ]

    started = time.monotonic()
    last_raw: str | None = None
    last_tokens: tuple[int | None, int | None] = (None, None)

    for _ in range(_MAX_ATTEMPTS):
        try:
            resp = client.chat.completions.create(
                model=settings.ai_model,
                messages=messages,
                max_tokens=settings.ai_max_tokens,
                temperature=0.3,  # 低随机性：结构化输出要稳定
            )
        except Exception:  # noqa: BLE001  SDK 异常种类随服务商变化，统一按"服务暂不可用"话术
            raise AIError(
                "AI 服务暂时不可用，请稍后重试", raw_output=last_raw
            ) from None

        content = resp.choices[0].message.content or ""
        last_raw = content
        if resp.usage:
            last_tokens = (resp.usage.prompt_tokens, resp.usage.completion_tokens)

        try:
            report = AIReport.model_validate(_extract_json(content))
        except Exception:  # noqa: BLE001, S112  解析/校验失败 → 重试，耗尽后统一报错
            continue

        return AnalysisResult(
            report=report.model_dump(),
            valid=True,
            model_name=settings.ai_model,
            tokens_prompt=last_tokens[0],
            tokens_completion=last_tokens[1],
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    raise AIError(
        "AI 返回的格式不符合要求，已自动重试仍失败，请稍后重试", raw_output=last_raw
    )
