"""平台说明工具：platform_help（纯静态文案，不查库不调模型）。"""

from langchain_core.tools import BaseTool, tool
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.agent.tools.common import ToolContext

# —— 平台功能说明（platform_help 的静态文案）——
# 纯静态：不查库、不调模型，随版本迭代直接改这里。每个主题一段文案，
# 末尾附 _PLATFORM_TOPICS 做「别名 → 文案」的路由；topic 为空或全不命中时回总览。
_PLATFORM_OVERVIEW = (
    "本平台是「AI 简历分析 + AI 模拟面试」应用，主要功能：\n"
    "1. 上传简历：在「首页」上传 PDF 简历，系统自动解析正文。\n"
    "2. AI 分析：为解析成功的简历生成岗位匹配、优势、短板、关键词缺口、改进建议与预测面试题。\n"
    "3. 模拟面试：基于简历做多轮实战模拟，结束出四维评分报告。\n"
    "4. 在线对话：基于平台知识库的 RAG 问答（Playground）。\n"
    "5. AI 客服：可查本人简历/分析/面试/用量，也能检索平台知识库。\n"
    "6. 使用日志：本人简历、分析、面试汇总与用量明细（需登录）。\n"
    "7. 个人中心：个人信息、本人用量五卡、近 7 日 Token 柱图与精简日志（需登录）。\n"
    "8. 数据看板：平台用户与用量统计（管理端，需登录）。\n"
    "9. 语料库管理：上传与管理知识库文档（管理端，需登录）。\n"
    "想了解某个功能，可以把主题问得更具体一些，例如「怎么上传简历」。"
)

_PLATFORM_PROFILE = (
    "个人中心（需登录）：左侧导航「个人中心」，含四块——个人信息卡（可退出登录）、"
    "本人用量五卡（第 5 卡为累计 Token）、近 7 日 Token 用量柱图、精简版使用日志。"
)

_PLATFORM_UPLOAD = (
    "上传简历：在「首页」的简历卡上传 PDF（仅文本型 PDF，单个不超过 5MB、不超过 5 页，"
    "扫描件暂不支持）。上传后系统自动解析正文；同一文件按内容哈希去重，重复上传不会产生多条记录。"
    "解析失败会显示原因（如加密 PDF、非文本型），可重新上传。"
)

_PLATFORM_ANALYSIS = (
    "AI 分析：在「首页」选中一份解析成功的简历，点「✨ 生成 AI 分析」，通常 10~30 秒同步返回；"
    "报告包含目标岗位、岗位匹配、优势、短板、关键词缺口、改进建议与预测面试题。"
    "同一简历的历次分析会保留不同版本，可开启「版本对比」并排查看差异。"
)

_PLATFORM_INTERVIEW = (
    "模拟面试：在「首页」选中简历后点「🤖 开始模拟面试」，按开场→技术问答→深挖→收尾阶段推进，"
    f"单场上限 {settings.max_interview_turns} 轮；可选岗位类型（实习/校招/社招，留空为通用难度）。"
    "结束后生成技术深度、表达结构、项目真实性、整体表现四维评分报告。"
)

_PLATFORM_CHAT = (
    "在线对话（Playground，管理端）：左侧导航「在线对话」，基于平台知识库做 RAG 问答——"
    "先在「语料库管理」补资料，再在这里提问；回答会附引用来源（文档标题与相似度）。"
    "一个知识库问答会话可新建多轮对话，侧栏可切换与删除历史会话。"
)

_PLATFORM_AGENT = (
    "AI 客服：左侧导航「AI客服」可进入整页对话（管理端首页右下角另有悬浮入口）。"
    "它能调用平台工具查你自己的简历、AI 分析结论、模拟面试记录与分数趋势、平台用量，"
    "也能列出知识库文档、检索技术资料，以及解答本平台怎么用；"
    "检索知识库得到的回答会标注引用来源。"
)

_PLATFORM_USAGE_LOG = (
    "使用日志（需登录）：管理端左侧导航「使用日志」，汇总本人简历、AI 分析、模拟面试记录与用量明细，"
    "支持按动作类型筛选与分页，可查看每次调用消耗的 token。"
)

_PLATFORM_DASHBOARD = (
    "数据看板（管理端，需登录）：左侧导航「数据看板」，展示平台用户列表与近 7 日用量统计，"
    "用于观察整体调用量趋势，不展示单个用户的具体内容。"
)

_PLATFORM_KB_ADMIN = (
    "语料库管理（管理端，需登录）：左侧导航「语料库管理」，上传的文档是系统预置语料"
    "（全站用户可见、可被在线对话与 AI 客服检索到），也支持删除任意文档（含预置，软删除可审计）。"
    "支持 txt/md/pdf，上传后由后台异步切块并向量化入库，状态显示「可检索」即生效。"
)

# 别名 → 说明文案。**顺序即优先级**：越具体的主题排越前，避免泛词（如「客服」）
# 抢走别的主题；匹配时对 topic 与别名都做小写化再判包含。
_PLATFORM_TOPICS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("个人中心", "我的账号", "个人信息"), _PLATFORM_PROFILE),
    (("上传简历", "简历上传", "上传pdf", "上传 pdf", "传简历"), _PLATFORM_UPLOAD),
    (
        ("ai分析", "ai 分析", "简历分析", "分析报告", "生成分析", "分析结论"),
        _PLATFORM_ANALYSIS,
    ),
    (
        ("模拟面试", "模拟实战面试", "开始面试", "面试练习", "面试报告"),
        _PLATFORM_INTERVIEW,
    ),
    (("在线对话", "playground", "对话问答", "rag 问答"), _PLATFORM_CHAT),
    (("ai客服", "ai 客服", "智能客服", "客服"), _PLATFORM_AGENT),
    (
        ("使用日志", "用量明细", "用量日志", "用量统计", "查用量", "token", "消耗"),
        _PLATFORM_USAGE_LOG,
    ),
    (("数据看板", "看板", "管理后台", "后台管理"), _PLATFORM_DASHBOARD),
    (("语料库", "知识库管理", "上传文档", "上传资料"), _PLATFORM_KB_ADMIN),
)


def _platform_reply(topic: str) -> str:
    """把 LLM 现编的 topic 路由到对应功能说明，命中不到就回平台总览。"""
    text = (topic or "").strip().lower()
    if text:
        for aliases, reply in _PLATFORM_TOPICS:
            if any(alias in text for alias in aliases):
                return reply
    return _PLATFORM_OVERVIEW


def build_platform_help(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    ctx: ToolContext,
) -> BaseTool:
    """构造 platform_help 工具（闭包绑定本次请求的会话与归属者，防串数据）。"""

    @tool
    def platform_help(topic: str) -> str:
        """查询**本平台自身**的功能怎么用：上传简历、AI 分析、模拟面试、在线对话、
        AI 客服、使用日志、个人中心、数据看板、语料库管理。直接返回内置说明，
        不查数据库也不调用模型。

        什么时候用：用户问"怎么上传简历""在哪看用量""模拟面试怎么开始"
        "AI 客服能做什么""这个网站有哪些功能"等**功能怎么操作/入口在哪**的问题。
        什么时候不用：问的是计算机技术知识点（编程语言、框架原理、数据库/网络/算法等）
        请用 kb_search 检索知识库；问"知识库收录了哪些资料、我能问哪些方向"（收录范围、
        不是功能用法）请用 kb_list；要找本人**过去的对话记录**（"之前问过你什么""上次聊到哪了"）
        请用 conversation_search；要查本人**真实数据**（简历、分析、面试、用量）
        请用 resume_lookup / analysis_read / interview_history / usage_stats。
        入参 topic 为想了解的功能名或一句口语化问题；传空字符串返回平台功能总览。"""
        return _platform_reply(topic)

    return platform_help
