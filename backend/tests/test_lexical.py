"""v3.5 词法检索（BM25）测试：分词过滤、打分排序、边界情况。纯内存，不依赖数据库。"""

from app.services.lexical_service import Bm25Index, tokenize


def test_tokenize_filters_stopwords_and_punctuation() -> None:
    tokens = tokenize("请问，HashMap 的底层实现原理是什么？")
    assert "hashmap" in tokens  # 英文统一小写
    assert "底层" in tokens or "实现" in tokens
    # 停用词与标点不进入 token 流
    assert "请问" not in tokens
    assert "什么" not in tokens
    assert "的" not in tokens
    assert "," not in tokens


def test_tokenize_empty_input() -> None:
    assert tokenize("") == []
    assert tokenize("   ") == []


def test_bm25_ranks_matching_document_first() -> None:
    docs = [
        (1, "聚簇索引是把数据行按主键顺序物理存储，叶子节点即数据页。"),
        (2, "Redis 的持久化方式有 RDB 和 AOF 两种。"),
        (3, "HTTP 三次握手的过程是 SYN、SYN-ACK、ACK。"),
    ]
    index = Bm25Index(docs)
    hits = index.search("聚簇索引是什么", top_n=3)

    assert hits, "命中文档不应为空"
    assert hits[0][0] == 1  # 唯一包含该术语的文档排第一
    assert hits[0][1] > 0


def test_bm25_returns_empty_when_no_term_overlap() -> None:
    index = Bm25Index([(1, "聚簇索引是把数据行按主键顺序物理存储。")])
    assert index.search("今天北京天气怎么样", top_n=3) == []


def test_bm25_empty_corpus_and_empty_query() -> None:
    assert len(Bm25Index([])) == 0
    assert Bm25Index([]).search("任意查询", top_n=5) == []

    index = Bm25Index([(1, "聚簇索引是把数据行按主键顺序物理存储。")])
    assert index.search("", top_n=5) == []


def test_bm25_ignores_documents_without_usable_tokens() -> None:
    """纯标点/停用词文档不进索引，避免污染平均文档长度。"""
    index = Bm25Index([(1, "的 了 是 ，。？"), (2, "聚簇索引是物理存储结构")])
    assert len(index) == 1
    assert index.search("聚簇索引", top_n=5)[0][0] == 2
