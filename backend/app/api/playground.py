"""Playground 问答接口（v3.0）：SSE 流式回答 + 引用来源。挂 /api 前缀。

流程：问题 → embedding → 相似度检索 → 拼 RAG 提示词 → stream_chat → done 事件带引用。
"""

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth_deps import get_optional_current_user
from app.api.deps import enforce_daily_limit, get_anonymous_id
from app.core.config import settings
from app.db.session import get_db
from app.models.usage_log import UsageLog
from app.models.user import User
from app.services.ai_client import AIError, stream_chat
from app.services.embedding_service import EmbeddingError, embed_texts
from app.services.kb_service import search_chunks

router = APIRouter(prefix="/api", tags=["playground"])

_RAG_SYSTEM_PROMPT = (
    "你是一位技术面试助教，基于以下知识库内容回答用户的问题。"
    "如果知识库内容不足以回答，请明确告知“没有找到相关信息”。"
    "回答要求：1) 基于知识库内容，不要编造知识库中没有的信息；"
    "2) 知识库内容不足时明确说目前知识库中没有相关回答；"
    "3) 回答简洁，2~5 句话；4) 纯文本，不要使用 markdown 标记。"
)


class AskIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


@router.post("/playground/ask")
def ask(
    body: AskIn,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    anonymous_id: str = Depends(get_anonymous_id),
    user: User | None = Depends(get_optional_current_user),  # noqa: B008
) -> StreamingResponse:
    """用户提问 → 向量检索 → RAG 流式回答。"""
    enforce_daily_limit(
        db, anonymous_id, settings.daily_playground_limit, "playground"
    )

    # 1) 向量化问题
    try:
        query_embeddings = embed_texts([body.content])
    except EmbeddingError as exc:
        raise HTTPException(502, exc.message) from exc

    query_vec = query_embeddings[0]

    # 2) 检索知识库
    citations = search_chunks(
        db,
        query_vec,
        user.id if user else None,
        anonymous_id,
        top_k=settings.kb_search_top_k,
    )

    # 3) 拼 RAG 上下文
    context_parts = []
    for c in citations:
        context_parts.append(
            f"---来源：{c['title']}（第 {c['seq']} 块）---\n{c['content']}"
        )
    context = "\n\n".join(context_parts)

    if not context:
        # 没有命中的引用来源，直接返回未找到
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

    # 4) SSE 流式回答
    def event_stream():
        started = time.monotonic()
        yield _sse("meta", {"stage": "rag"})

        usage: dict = {}
        chunks: list[str] = []
        try:
            for delta in stream_chat(messages, settings, usage):
                chunks.append(delta)
                yield _sse("delta", {"content": delta})
        except AIError as exc:
            yield _sse("error", {"content": exc.message})
            return

        full_text = "".join(chunks).strip()
        if not full_text:
            yield _sse("error", {"content": "AI 返回了空回复，请重试"})
            return

        duration_ms = int((time.monotonic() - started) * 1000)
        # 记账：本次问答作为 playground 用量的依据（限流 + token 统计）
        db.add(
            UsageLog(
                user_id=user.id if user else None,
                anonymous_id=anonymous_id,
                action_type="playground",
                model_name=settings.ai_model,
                tokens_total=(usage.get("tokens_prompt") or 0)
                + (usage.get("tokens_completion") or 0),
                ip_address=request.client.host if request.client else None,
            )
        )
        db.commit()
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

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"