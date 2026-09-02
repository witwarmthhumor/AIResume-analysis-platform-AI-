"""v3.0 切块器单元测试：纯函数，不依赖数据库。"""

from app.services.kb_chunker import approximate_tokens, chunk_text


def test_approximate_tokens_cjk() -> None:
    assert approximate_tokens("中文测试") == 5  # 4 字 + 1
    assert approximate_tokens("") == 1


def test_short_text_single_chunk() -> None:
    chunks = chunk_text("只有一段话。", chunk_size=50, overlap=0)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "只有一段话。"
    assert chunks[0]["token_count"] > 0


def test_no_content_loss_on_multiple_chunks() -> None:
    text = "A段\n\nB段\n\nC段\n\n" * 10
    chunks = chunk_text(text, chunk_size=50, overlap=10)
    # 输入 30 段，输出总字符（含重叠重复）应不少于输入
    joined = "".join(c["text"] for c in chunks)
    for para in ("A段", "B段", "C段"):
        assert joined.count(para) >= 10
    assert len(chunks) > 1


def test_overlap_between_adjacent_chunks() -> None:
    text = ("第一段内容" + "\n\n" + "第二段内容\n\n第三段内容\n\n第四段内容\n\n") * 4
    chunks = chunk_text(text, chunk_size=30, overlap=8)
    assert len(chunks) > 1
    for i in range(1, len(chunks)):
        prev_tail = chunks[i - 1]["text"][-8:]
        assert chunks[i]["text"].startswith(prev_tail)


def test_overlong_paragraph_hard_split() -> None:
    # 单段远超 chunk_size：应被硬切成多块，且内容完整
    long_para = "很长的段落内容。" * 60  # 360 字
    chunks = chunk_text(long_para, chunk_size=100, overlap=10)
    assert len(chunks) >= 3
    joined = "".join(c["text"] for c in chunks)
    assert joined.count("很长的段落内容。") >= 60


def test_empty_and_whitespace_input() -> None:
    assert chunk_text("", chunk_size=50) == []
    assert chunk_text("   \n\n  ", chunk_size=50) == []


def test_mixed_cjk_ascii_content_preserved() -> None:
    text = "HashMap 底层是数组 + 链表 + 红黑树。\n\n这是第二段。"
    chunks = chunk_text(text, chunk_size=100, overlap=0)
    assert len(chunks) == 1
    assert "HashMap" in chunks[0]["text"]
    assert "红黑树" in chunks[0]["text"]
    assert "第二段" in chunks[0]["text"]
