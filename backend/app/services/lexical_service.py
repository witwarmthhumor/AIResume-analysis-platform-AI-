"""词法检索服务（v3.5）：jieba 分词 + BM25 打分，与向量检索做 RRF 融合。

为什么需要它：纯向量检索（nomic-embed-text）对"术语精确命中"不够敏感——
专有名词、缩写、API 名（如「乐观锁」「聚簇索引」「HNSW」）在中文短查询里
容易被语义向量模糊化而排到后面。BM25 是经典的词频-逆文档频率打分，对
"查询词是否原样出现在文档里"极其敏感，两者互补。

实现取舍：
- 手写 BM25（不引 rank_bm25），公式为标准 Okapi BM25，约 40 行，可控可读。
- 分词用 jieba.lcut_for_search：比精确模式多切出子词，召回更好。
- 不做语料缓存：当前语料规模（百级块、每块约 600 字）全量分词约 10~20ms，
  加缓存带来的失效复杂度不划算；语料涨到千级以上再考虑。

调用方：kb_service.search_chunks_hybrid()。
"""

import math
import re
from collections import Counter

import jieba

from app.core.config import settings

# 中文停用词：疑问词/虚词对 BM25 无区分度，还会让"怎么用"这类查询误命中。
# 按语义分行分组便于维护，故保留多行字符串 + split 写法（不用列表字面量）。
_STOPWORDS = frozenset(
    """
    的 了 是 在 和 与 及 等 什么 怎么 怎样 如何 为什么 哪些 哪个 请问 吗 呢 啊 吧
    我 你 他 她 它 我们 你们 他们 这 那 这个 那个 这些 那些 一个 一些 可以 能够
    有 没有 不 也 都 就 还 而 但 但是 因为 所以 如果 会 要 想 说 请 帮我 告诉
    关于 对于 以及 通过 使用 进行 实现 需要 应该 是否 能 不能 一下 介绍 解释 讲讲
    """.split()  # noqa: SIM905
)

# 纯标点/空白 token 过滤（jieba 会把标点单独切成 token）
_PUNCT_RE = re.compile(r"^[\W_]+$")


def tokenize(text: str) -> list[str]:
    """分词并过滤停用词、标点、单字符英文；返回小写 token 列表。"""
    if not text:
        return []
    tokens = []
    for raw in jieba.lcut_for_search(text):
        token = raw.strip().lower()
        if not token or token in _STOPWORDS:
            continue
        if _PUNCT_RE.match(token):
            continue
        # 单个英文字母/数字无检索价值（中文单字有价值，如「锁」「栈」，故只过滤非中文单字）
        if len(token) == 1 and not "\u4e00" <= token <= "\u9fff":
            continue
        tokens.append(token)
    return tokens


class Bm25Index:
    """内存 BM25 索引：构建一次，可多次查询。

    score(q, d) = Σ_t IDF(t) · tf·(k1+1) / (tf + k1·(1 - b + b·dl/avgdl))
    IDF(t)      = ln(1 + (N - df + 0.5) / (df + 0.5))
    """

    def __init__(self, documents: list[tuple[int, str]]) -> None:
        """documents：[(chunk_id, 文本)]；文本为空视为不参与检索。"""
        self._k1 = settings.kb_bm25_k1
        self._b = settings.kb_bm25_b
        self._ids: list[int] = []
        self._term_freqs: list[Counter] = []
        self._lengths: list[int] = []
        self._doc_freq: Counter = Counter()

        for chunk_id, text in documents:
            tokens = tokenize(text)
            if not tokens:
                continue
            tf = Counter(tokens)
            self._ids.append(chunk_id)
            self._term_freqs.append(tf)
            self._lengths.append(len(tokens))
            for term in tf:
                self._doc_freq[term] += 1

        total = len(self._ids)
        self._avg_len = sum(self._lengths) / total if total else 0.0

    def __len__(self) -> int:
        return len(self._ids)

    def _idf(self, term: str) -> float:
        n = len(self._ids)
        df = self._doc_freq.get(term, 0)
        # 词未出现在任何文档中 → IDF 为 0（该词不贡献分数）
        if df == 0:
            return 0.0
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_n: int) -> list[tuple[int, float]]:
        """返回 [(chunk_id, bm25_score)]，按分数降序，仅含分数 > 0 的结果。"""
        query_terms = tokenize(query)
        if not query_terms or not self._ids:
            return []

        scored: list[tuple[int, float]] = []
        for idx, chunk_id in enumerate(self._ids):
            tf = self._term_freqs[idx]
            length = self._lengths[idx]
            score = 0.0
            for term in query_terms:
                freq = tf.get(term, 0)
                if not freq:
                    continue
                denom = freq + self._k1 * (
                    1 - self._b + self._b * length / self._avg_len
                )
                score += self._idf(term) * freq * (self._k1 + 1) / denom
            if score > 0:
                scored.append((chunk_id, score))

        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_n]
