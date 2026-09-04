"""Agent 工具集（v3.4）：第一版只挂 kb_search 一个工具。

设计要点：
- make_tools 是"每请求工厂"：闭包绑定本次请求的 db 会话与归属者（user_id/anonymous_id），
  杜绝多请求共享工具导致的用户串数据。
- 检索直接复用现有 embedding_service.embed_texts + kb_service.search_chunks，不重写 RAG。
- ToolContext 回收本次命中的引用来源（citations），供 API 层随 done 事件回传前端；
  工具返回给 LLM 的是拼好的文本片段。
- 工具内部吞掉检索类异常并返回自然语言说明，让 Agent 能换通用知识兜底，而不是整轮崩掉。
"""

from dataclasses import dataclass, field

from langchain_core.tools import BaseTool, tool
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.services.embedding_service import embed_texts
from app.services.kb_service import search_chunks

logger = get_logger(__name__)

# 单块内容回灌给 LLM 的最大字符数（5 块 × 约 300 字，控制 Agent 上下文体积）
_TOOL_CHUNK_CHARS = 300


@dataclass
class ToolContext:
    """一次 Agent 运行期间工具的共享状态：最近一次知识库命中（用于前端引用来源）。"""

    citations: list[dict] = field(default_factory=list)


def make_tools(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    ctx: ToolContext,
) -> list[BaseTool]:
    """构造本次请求专属的工具列表。"""

    @tool
    def kb_search(query: str) -> str:
        """检索平台技术知识库。当用户询问计算机技术、编程语言、Java/JVM/并发、MySQL/Redis、
        计算机网络、操作系统、RAG/AI 应用开发等具体知识点时，必须先调用本工具获取权威资料，
        再基于检索结果回答。入参 query 为精简后的检索关键词或问题。"""
        try:
            vectors = embed_texts([query])
        except Exception:
            logger.warning(
                "agent kb_search embedding 失败，走无检索兜底", exc_info=True
            )
            return "知识库检索服务（向量模型）暂时不可用，请基于你已有的通用知识谨慎回答，并说明未检索平台知识库。"

        try:
            hits = search_chunks(
                db,
                vectors[0],
                user_id,
                anonymous_id,
                top_k=settings.kb_search_top_k,
            )
        except Exception:
            logger.exception("agent kb_search 检索失败")
            return "知识库检索暂时出错，请基于你已有的通用知识谨慎回答，并说明未检索平台知识库。"

        if not hits:
            # 清空上一轮残留引用，并明确告知 Agent 没命中（避免它编造"知识库说"）
            ctx.citations = []
            return "知识库中没有检索到与该问题相关的内容。如确有把握可用通用知识简要回答，并明确说明这部分不来自平台知识库。"

        # 记录引用来源（前端展示用），并拼出回灌给 LLM 的资料文本
        ctx.citations = [
            {
                "document_id": h["document_id"],
                "title": h["title"],
                "seq": h["seq"],
                "similarity": h["similarity"],
            }
            for h in hits
        ]
        parts = []
        for h in hits:
            snippet = h["content"][:_TOOL_CHUNK_CHARS]
            parts.append(f"【来源：{h['title']}（第{h['seq']}块）】\n{snippet}")
        return "以下是知识库检索到的资料，请据此回答：\n\n" + "\n\n".join(parts)

    return [kb_search]
