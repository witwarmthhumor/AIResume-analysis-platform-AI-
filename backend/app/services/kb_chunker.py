"""切块服务（v3.0 定稿参数）：段落合并，目标 ~600 字/块，相邻块重叠 ~60 字。

纯函数、无 IO，方便单测。token_count 是近似估算（中文 1 字≈1 token，英文 4 字符≈1 token），
只用于检索与审计参考，不精确计费。
"""

import re

_CHUNK_SIZE = 600
_CHUNK_OVERLAP = 60


def approximate_tokens(text: str) -> int:
    """粗估 token 数：CJK 按 1 字 1 token，其余按 4 字符 1 token。"""
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return cjk + other // 4 + 1


def _split_long(text: str, chunk_size: int, overlap: int) -> list[str]:
    """单段超长（如整段粘贴的代码/长文）按固定窗口硬切，窗口间重叠 overlap。"""
    pieces: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        pieces.append(text[start:end])
        if end == n:
            break
        start = max(end - overlap, start + 1)  # 防 overlap>=chunk_size 时死循环
    return pieces


def chunk_text(
    text: str, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP
) -> list[dict]:
    """按段落合并成约 chunk_size 字的块，返回 [{"text", "token_count"}]。

    规则：
    1. 按空行拆段，段依次并入当前块，超长则关当前块起新块；
    2. 新块以「上一块末尾 overlap 字」开头，保住切块边界上下文；
    3. 单段超 chunk_size 时独立硬切（不进当前块，避免污染相邻语义）。
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        if len(para) > chunk_size:
            if current.strip():
                chunks.append(current)
                current = ""
            chunks.extend(_split_long(para, chunk_size, overlap))
            continue
        if current and len(current) + 1 + len(para) > chunk_size:
            chunks.append(current)  # 先归档当前块
            current = current[-overlap:] if overlap else ""  # 新块以重叠尾开头
        current = (current + "\n" + para) if current else para
    if current.strip():
        chunks.append(current)

    return [
        {"text": c, "token_count": approximate_tokens(c)} for c in chunks
    ]
