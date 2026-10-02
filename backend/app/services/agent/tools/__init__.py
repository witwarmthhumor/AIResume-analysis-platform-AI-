"""Agent 工具包（v4.3 拆分自单文件 tools.py，行为零变化）。

- registry.TOOLS_META：工具清单与中文名的**唯一数据源**（五处口径同步的收口）；
- 各域模块：kb / resume / interview / conversation / platform / llm；
- factory.make_tools：按 registry 顺序组装，每请求闭包隔离用户。

设计要点（沿用拆分前口径）：
- make_tools 是"每请求工厂"：闭包绑定本次请求的 db 会话与归属者（user_id/anonymous_id），
  杜绝多请求共享工具导致的用户串数据。
- 所有工具都只查"当前归属者"自己的数据，天然带用户隔离；未识别到身份时明确拒绝，
  而不是返回空结果让模型自行脑补。
- 工具内部吞掉检索类异常并返回自然语言说明，让 Agent 能换通用知识兜底，而不是整轮崩掉。
- 工具内部自己还要调一次 LLM 的工具（job_match / question_gen / answer_review）统一走
  _run_tool_llm（见 llm.py），与主循环限额分开。
- 工具的 docstring 就是给模型看的"路由说明"，统一按「做什么 → 什么时候用 → 什么时候不用」
  三段写；易撞的工具要在"什么时候不用"里**互相点名**（见 docs/Agent工具设计.md）。
"""

from app.services.agent.tools.common import (
    _TOOL_ANSWER_CHARS,
    _TOOL_CONV_LIMIT,
    _TOOL_TRANSCRIPT_CHARS,
    _TOOL_TRANSCRIPT_LIMIT,
    ToolContext,
)
from app.services.agent.tools.factory import make_tools
from app.services.agent.tools.registry import TOOL_NAMES, TOOLS_META

__all__ = [
    "TOOLS_META",
    "TOOL_NAMES",
    "_TOOL_ANSWER_CHARS",
    "_TOOL_CONV_LIMIT",
    "_TOOL_TRANSCRIPT_CHARS",
    "_TOOL_TRANSCRIPT_LIMIT",
    "ToolContext",
    "make_tools",
]
