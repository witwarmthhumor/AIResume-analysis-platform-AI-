"""知识库域工具：kb_search（RAG 检索主链路）/ kb_list（文档清单）。"""

from langchain_core.tools import BaseTool, tool
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.kb import KBDocument
from app.services.agent.tools.common import (
    _KB_SCOPE_LABELS,
    _KB_STATUS_LABELS,
    _TOOL_KB_LIMIT,
    ToolContext,
)
from app.services.agent_capabilities import (
    CHUNK_CHARS as _TOOL_CHUNK_CHARS,
)
from app.services.agent_capabilities import (
    kb_retrieve as _kb_retrieve,
)
from app.services.kb_service import (
    count_chunks_by_document,
    list_documents,
)

logger = get_logger(__name__)


def _kb_title_candidates(
    db: Session, user_id: int | None, anonymous_id: str | None, document: str
) -> tuple[list[KBDocument], list[KBDocument]]:
    """按标题模糊匹配可见文档，返回 (命中文档, 全部可见文档)。

    标题匹配用 Python 侧的包含判断（与 kb_list 的过滤口径一致），调用方负责 try/except。
    """
    docs = list_documents(db, user_id, anonymous_id)
    needle = document.lower()
    matched = [d for d in docs if needle in (d.title or "").lower()]
    return matched, docs


def build_kb_search(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    ctx: ToolContext,
) -> BaseTool:
    """构造 kb_search 工具（闭包绑定本次请求的会话与归属者，防串数据）。"""

    @tool
    def kb_search(query: str, document: str = "") -> str:
        """检索平台技术知识库：按语义 + 关键词混合检索资料切块，把命中内容回灌给模型，
        并把来源（文档标题与块序号）记为引用展示给用户。

        什么时候用：用户问**计算机技术知识点**——编程语言、Java/JVM/并发、MySQL/Redis、
        计算机网络、操作系统、RAG/AI 应用开发等的原理、用法、对比、排错；必须先检索再作答。
        用户指明"只在某篇文档/某份资料里找"时，把文档名传给 document 限定检索范围。
        什么时候不用：问"本平台怎么用"（怎么上传简历、怎么开始模拟面试、在哪看用量、
        平台有哪些功能）请用 platform_help；要找**用户自己过去的对话记录**
        （"之前问过你什么""上次聊到哪了"）请用 conversation_search；
        只想看知识库有哪些**文档清单**请用 kb_list；
        要**出一组模拟面试题**请用 question_gen（本工具负责查答案与讲解，不负责出题）；
        用户贴出**自己的回答**让我点评/打分请用 answer_review——不要因为那段回答里
        出现了技术名词就用本工具去查知识点。
        入参 query 为精简后的检索关键词或问题；document 为可选文档名（标题片段），
        留空表示在全部可见语料中检索。"""
        document_kw = (document or "").strip()
        document_ids: list[int] | None = None
        if document_kw:
            try:
                matched, docs = _kb_title_candidates(
                    db, user_id, anonymous_id, document_kw
                )
            except Exception:
                logger.exception("agent kb_search 文档匹配查询失败")
                return "知识库文档查询暂时出错，请稍后再试。"
            if not matched:
                if not docs:
                    return "知识库当前还没有任何可查看的文档。可提示用户到知识库页上传资料。"
                names = "".join(f"\n- 《{d.title}》" for d in docs[:_TOOL_KB_LIMIT])
                return (
                    f"知识库里没有标题包含「{document_kw}」的文档。现有可见文档：{names}"
                    "\n请让用户确认文档名后再问一次，或去掉文档限定检索全部资料。"
                )
            if len(matched) > 1:
                # 匹配到多篇不擅自选一篇：列候选（标题 + 块数）让用户挑
                try:
                    counts = count_chunks_by_document(db, [d.id for d in matched])
                except Exception:
                    logger.exception("agent kb_search 文档块数查询失败")
                    return "知识库文档查询暂时出错，请稍后再试。"
                lines = [
                    f"标题包含「{document_kw}」的文档有多篇，请让用户指定其中一篇后再检索："
                ]
                lines.extend(
                    f"- 《{d.title}》（{counts.get(d.id, 0)} 个知识块）"
                    for d in matched[:_TOOL_KB_LIMIT]
                )
                return "\n".join(lines)
            document_ids = [matched[0].id]

        hits, error = _kb_retrieve(
            db,
            query,
            user_id,
            anonymous_id,
            settings.kb_search_top_k,
            document_ids=document_ids,
        )
        if error:
            return error

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

    return kb_search


def build_kb_list(
    db: Session,
    user_id: int | None,
    anonymous_id: str | None,
    ctx: ToolContext,
) -> BaseTool:
    """构造 kb_list 工具（闭包绑定本次请求的会话与归属者，防串数据）。"""

    @tool
    def kb_list(query: str) -> str:
        """列出平台知识库里可见的**文档清单**：标题、切块数、入库状态、来源
        （平台预置 / 本人上传），不含任何正文内容。

        什么时候用：用户问"知识库里有哪些资料""平台收录了哪些文档""我能问哪些方向/主题"
        ——问的是**知识库的收录范围**，就用本工具（即使句子里出现"这个平台"字样）。
        什么时候不用：要查某主题下的**具体知识点内容**（原理、用法、对比）请用 kb_search，
        本工具只给目录不回答知识问题；问"某个功能怎么操作、入口在哪、平台有哪些功能菜单"
        请用 platform_help。
        入参 query 为按标题过滤的关键词，传空字符串表示列出全部可见文档。"""
        try:
            docs = list_documents(db, user_id, anonymous_id)
            counts = count_chunks_by_document(db, [d.id for d in docs])
        except Exception:
            logger.exception("agent kb_list 查询失败")
            return "知识库文档清单查询暂时出错，请稍后再试。"

        if not docs:
            return "知识库当前还没有任何可查看的文档。可提示用户到知识库页上传资料。"

        keyword = (query or "").strip()
        if keyword:
            docs = [d for d in docs if keyword.lower() in (d.title or "").lower()]
            if not docs:
                return f"未找到标题包含「{keyword}」的文档。可提示用户换个关键词再问。"

        shown = docs[:_TOOL_KB_LIMIT]
        head = (
            f"知识库共有 {len(docs)} 篇可见文档"
            "（预置语料全站可见，用户上传的文档仅本人可见）："
        )
        lines = [head]
        for doc in shown:
            status = _KB_STATUS_LABELS.get(doc.status, doc.status)
            source = _KB_SCOPE_LABELS.get(doc.scope, doc.scope)
            lines.append(
                f"- 《{doc.title}》｜{source}｜{status}｜{counts.get(doc.id, 0)} 个知识块"
            )
        if len(docs) > _TOOL_KB_LIMIT:
            lines.append(f"（共 {len(docs)} 篇，以上仅显示前 {_TOOL_KB_LIMIT} 篇）")
        return "\n".join(lines)

    return kb_list
