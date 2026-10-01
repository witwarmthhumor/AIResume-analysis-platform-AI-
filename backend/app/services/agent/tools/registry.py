"""工具元数据注册表（v4.3 单一数据源）。

工具清单一律以这里的 TOOLS_META 为准：
- 后端 GET /api/agent/tools 直接返回本表（前端工具中文名不再手写映射）；
- 测试断言从本表派生（test_agent_tools 的 _ALL_TOOLS / 数量断言）；
- 新增/删除工具只改本表 + 对应工具模块，name 必须与工具函数名严格一致。
顺序即 make_tools 的返回顺序（kb_search 必须始终第一）。
"""

TOOLS_META: list[dict] = [
    {
        "name": "kb_search",
        "label": "检索知识库",
        "llm_backed": False,
        "summary": "检索平台技术知识库（RAG 主链路，唯一会回填 citations 的工具）",
    },
    {
        "name": "resume_lookup",
        "label": "查看简历原文",
        "llm_backed": False,
        "summary": "查当前用户自己的简历（有哪些、正文里有没有提到某关键词）",
    },
    {
        "name": "interview_history",
        "label": "查看面试历史",
        "llm_backed": False,
        "summary": "查当前用户自己的模拟面试单场记录与四维评分评语",
    },
    {
        "name": "interview_transcript",
        "label": "查看面试问答原文",
        "llm_backed": False,
        "summary": "复述当前用户某场模拟面试的问答原文（问了什么/当时怎么答的）",
    },
    {
        "name": "score_trend",
        "label": "查看分数趋势",
        "llm_backed": False,
        "summary": "查当前用户自己历次模拟面试的分数趋势（逐场对比升降）",
    },
    {
        "name": "conversation_search",
        "label": "检索历史对话",
        "llm_backed": False,
        "summary": "在当前用户自己的历史对话（在线对话 + AI 客服）里按关键词检索",
    },
    {
        "name": "usage_stats",
        "label": "查看用量统计",
        "llm_backed": False,
        "summary": "查当前用户自己的平台用量（近 N 天各动作次数与 token，可按动作过滤）",
    },
    {
        "name": "analysis_read",
        "label": "读取分析报告",
        "llm_backed": False,
        "summary": "读当前用户某份简历的 AI 分析结论（目标岗位/优劣势/建议/预测题）",
    },
    {
        "name": "kb_list",
        "label": "列出知识库文档",
        "llm_backed": False,
        "summary": "列知识库文档清单（标题/块数/状态/来源，不含任何正文）",
    },
    {
        "name": "platform_help",
        "label": "平台功能说明",
        "llm_backed": False,
        "summary": "答“本平台自身功能怎么用”（纯静态文案，不查库不调模型）",
    },
    {
        "name": "job_match",
        "label": "岗位匹配分析",
        "llm_backed": True,
        "summary": "拿岗位 JD 与本人简历做匹配分析（工具内调一次 LLM）",
    },
    {
        "name": "question_gen",
        "label": "生成面试题",
        "llm_backed": True,
        "summary": "围绕某主题出一组模拟面试题（先检索语料再出题，工具内调一次 LLM）",
    },
    {
        "name": "answer_review",
        "label": "点评我的回答",
        "llm_backed": True,
        "summary": "点评用户贴的一段面试回答（三项打分 + 改进建议，工具内调一次 LLM）",
    },
]

TOOL_NAMES: tuple[str, ...] = tuple(m["name"] for m in TOOLS_META)
