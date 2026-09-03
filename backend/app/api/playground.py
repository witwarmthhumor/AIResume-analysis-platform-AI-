"""Playground 问答接口（v3.0 + v3.1）：SSE 流式回答 + 引用来源。挂 /api 前缀。

流程：问题 → embedding → 相似度检索 → 拼 RAG 提示词 → stream_chat → done 事件带引用。
v3.1 新增：可选 session_id，有则持久化用户/AI 消息到 chat_messages，并更新 chat_session。
"""

import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth_deps import get_optional_current_user
from app.api.deps import enforce_daily_limit, get_anonymous_id
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.chat import ChatMessage, ChatSession
from app.models.user import User
from app.services.ai_client import AIError, stream_chat
from app.services.embedding_service import EmbeddingError, embed_texts
from app.services.kb_service import search_chunks
from app.services.usage_service import write_usage

router = APIRouter(prefix="/api", tags=["playground"])

logger = get_logger(__name__)

_RAG_SYSTEM_PROMPT = (
    "你是一位技术面试助教，基于以下知识库内容回答用户的问题。"
    "如果知识库内容不足以回答，请明确告知“没有找到相关信息”。"
    "回答要求：1) 基于知识库内容，不要编造知识库中没有的信息；"
    "2) 知识库内容不足时明确说目前知识库中没有相关回答；"
    "3) 回答简洁，2~5 句话；4) 纯文本，不要使用 markdown 标记。"
)


class AskIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    session_id: int | None = Field(default=None, description="在线对话会话 ID，传入则持久化消息")


def _get_owned_session(
    db: Session, session_id: int, user: User | None, anonymous_id: str
) -> ChatSession | None:
    """取会话并校验归属；不存在/已删除/无权限 → 404。"""
    session = db.get(ChatSession, session_id)
    if session is None or session.deleted_at is not None:
        raise HTTPException(404, "对话不存在")
    if user:
        if session.user_id != user.id:
            raise HTTPException(404, "对话不存在")
    else:
        if session.anonymous_id != anonymous_id:
            raise HTTPException(404, "对话不存在")
    return session


@router.post("/playground/ask")
def ask(
    body: AskIn,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> StreamingResponse:
    """用户提问 → 向量检索 → RAG 流式回答。有 session_id 时持久化消息。"""
    enforce_daily_limit(
        db, anonymous_id, settings.daily_playground_limit, "playground"
    )

    # v3.1：校验会话归属（有 session_id 时）
    session: ChatSession | None = None
    if body.session_id is not None:
        session = _get_owned_session(db, body.session_id, user, anonymous_id)

    def _log_usage(tokens_total: int) -> None:
        """记账：本次问答作为 playground 用量的依据（限流 + token 统计）。

        失败/空回复/断开路径也计账（0 token）——否则失败重试可无限白嫖
        embedding 与 AI 资源。成功路径带真实 token 数。
        """
        write_usage(
            db,
            anonymous_id,
            user.id if user else None,
            "playground",
            settings.ai_model,
            tokens_total,
            request.client.host if request.client else None,
        )
        db.commit()

    def _save_user_message() -> None:
        """保存用户消息；若是会话首条消息，更新标题为内容前 20 字。"""
        if session is None:
            return
        try:
            msg_count = db.scalar(
                select(ChatMessage.id).where(ChatMessage.session_id == session.id)
            )
            is_first = msg_count is None
            user_msg = ChatMessage(
                session_id=session.id,
                role="user",
                content=body.content,
            )
            db.add(user_msg)
            if is_first and session.title == "新对话":
                title = body.content.strip()[:20]
                if title:
                    session.title = title
            session.updated_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            logger.warning("playground 保存用户消息失败", exc_info=True)
            db.rollback()

    def _save_assistant_message(full_text: str, citations: list, tokens_total: int) -> None:
        """流结束后保存 AI 消息（含引用来源），失败只记日志不影响已返回内容。"""
        if session is None:
            return
        try:
            ai_msg = ChatMessage(
                session_id=session.id,
                role="assistant",
                content=full_text,
                citations=citations,
                tokens=tokens_total,
            )
            db.add(ai_msg)
            session.updated_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            logger.warning("playground 保存 AI 消息失败", exc_info=True)
            db.rollback()

    # 1) 向量化问题
    try:
        query_embeddings = embed_texts([body.content])
    except EmbeddingError as exc:
        raise HTTPException(502, exc.message) from exc

    query_vec = query_embeddings[0]
    # 维度护栏：embedding 模型被切换后问题向量与库内旧向量维度不一致，
    # 数据库余弦计算会直接抛异常——提前拦下给可读话术
    if len(query_vec) != settings.embedding_dim:
        raise HTTPException(
            502,
            "向量维度与知识库不一致（embedding 模型可能已切换），请重新入库语料后再提问",
        )

    # 2) 检索知识库（检索异常兜底：不把数据库原始错误抛给前端）
    try:
        citations = search_chunks(
            db,
            query_vec,
            user.id if user else None,
            anonymous_id,
            top_k=settings.kb_search_top_k,
        )
    except Exception:
        logger.exception("kb 检索失败")
        raise HTTPException(502, "知识库检索暂不可用，请稍后再试") from None

    # 3) 拼 RAG 上下文
    context_parts = []
    for c in citations:
        context_parts.append(
            f"---来源：{c['title']}（第 {c['seq']} 块）---\n{c['content']}"
        )
    context = "\n\n".join(context_parts)

    if not context:
        # 没有命中的引用来源，直接返回未找到（embedding 已消耗，同样要计账）
        _log_usage(0)

        def empty_sse():
            yield _sse(
                "error",
                {"content": "知识库中没有相关内容，请换一个问题试试"},
            )

        return StreamingResponse(
            empty_sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    messages = [
        {"role": "system", "content": _RAG_SYSTEM_PROMPT},
        {"role": "user", "content": f"知识库内容：\n{context}\n\n用户问题：{body.content}"},
    ]

    # v3.1：流式开始前保存用户消息
    _save_user_message()

    # 4) SSE 流式回答
    def event_stream():
        started = time.monotonic()
        accounted = False  # 失败/断开路径也要计账，否则失败重试可无限白嫖资源
        yield _sse("meta", {"stage": "rag"})

        usage: dict = {}
        chunks: list[str] = []
        try:
            try:
                for delta in stream_chat(messages, settings, usage):
                    chunks.append(delta)
                    yield _sse("delta", {"content": delta})
            except AIError as exc:
                _log_usage(0)
                accounted = True
                yield _sse("error", {"content": exc.message})
                return

            full_text = "".join(chunks).strip()
            if not full_text:
                _log_usage(0)
                accounted = True
                yield _sse("error", {"content": "AI 返回了空回复，请重试"})
                return

            duration_ms = int((time.monotonic() - started) * 1000)
            tokens_total = (usage.get("tokens_prompt") or 0) + (
                usage.get("tokens_completion") or 0
            )
            _log_usage(tokens_total)
            accounted = True

            # v3.1：流结束后保存 AI 消息
            _save_assistant_message(full_text, citations, tokens_total)

            yield _sse(
                "done",
                {
                    "content": full_text,
                    "tokens_prompt": usage.get("tokens_prompt"),
                    "tokens_completion": usage.get("tokens_completion"),
                    "duration_ms": duration_ms,
                    "citations": citations,
                },
            )
        finally:
            if not accounted:
                # 客户端中途断开（GeneratorExit）等未走完的路径：补一条 0-token 账
                try:
                    _log_usage(0)
                except Exception:  # 断开清理失败不影响响应已终止的事实
                    logger.warning("playground 断开路径记账失败", exc_info=True)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
