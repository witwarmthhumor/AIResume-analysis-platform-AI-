"""RAG 检索评测脚本（v3.0 起，v3.5 加混合检索对比）：黄金问答集 vs 预置语料。

度量"检索质量"而非"生成质量"——对每题，embedding 问题后在库内做 top-k 检索
（不经过 kb_min_similarity 阈值，纯看排序），检查预期来源文档是否出现在 top 结果里。

v3.5 起同一份问答集跑两种模式并排对比：
  - vector：纯向量（nomic-embed-text 余弦），即 v3.0 基线
  - hybrid：向量 + BM25 词法，RRF 融合（生产实际走的路径）
改切块参数 / embedding 模型 / 检索逻辑后重跑本脚本，即为 RAG 基线回归。

用法（backend/ 目录下）：
    ./.venv/Scripts/python.exe -m scripts.eval_rag            # 两种模式对比，输出到 stdout
    ./.venv/Scripts/python.exe -m scripts.eval_rag --report    # 覆盖写 data/kb_eval/report.md
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.kb import KBChunk, KBDocument
from app.services.embedding_service import embed_texts
from app.services.lexical_service import Bm25Index

QA_PATH = Path(__file__).resolve().parents[1].parent / "data" / "kb_eval" / "qa.json"
TOP_K = 5
MODES = ("vector", "hybrid")


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _load_bank() -> list[dict]:
    """取全部 ready 切块：id / 内容 / 向量 / 来源标题。"""
    with SessionLocal() as db:
        rows = db.execute(
            select(KBChunk, KBDocument.title)
            .join(KBDocument, KBChunk.document_id == KBDocument.id)
            .where(
                KBDocument.deleted_at.is_(None),
                KBDocument.status == "ready",
            )
        ).all()
    return [
        {"id": c.id, "content": c.content or "", "embedding": c.embedding, "title": title}
        for c, title in rows
    ]


def _vector_order(query_vec: list[float], bank: list[dict]) -> list[int]:
    """纯向量排序，返回 bank 下标序列（降序）。"""
    scored = [(_cosine(query_vec, item["embedding"]), i) for i, item in enumerate(bank)]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [i for _score, i in scored]


def _hybrid_order(query: str, query_vec: list[float], bank: list[dict], bm25: Bm25Index) -> list[int]:
    """向量 + BM25 两路各自排名 → RRF 融合，返回 bank 下标序列（降序）。"""
    vector_rank = {idx: rank for rank, idx in enumerate(_vector_order(query_vec, bank), start=1)}
    lexical = bm25.search(query, len(bank))  # [(下标, bm25 分)]
    lexical_rank = {idx: rank for rank, (idx, _s) in enumerate(lexical, start=1)}

    rrf_k = settings.kb_rrf_k
    fused: list[tuple[float, int]] = []
    for idx in set(vector_rank) | set(lexical_rank):
        score = 0.0
        if idx in vector_rank:
            score += 1.0 / (rrf_k + vector_rank[idx])
        if idx in lexical_rank:
            score += 1.0 / (rrf_k + lexical_rank[idx])
        fused.append((score, idx))
    fused.sort(key=lambda pair: pair[0], reverse=True)
    return [idx for _score, idx in fused]


def run(bank: list[dict]) -> dict[str, list[dict]]:
    questions = json.loads(QA_PATH.read_text(encoding="utf-8"))
    vectors = embed_texts([q["question"] for q in questions])  # 一次批量向量化
    bm25 = Bm25Index([(i, item["content"]) for i, item in enumerate(bank)])

    results: dict[str, list[dict]] = {mode: [] for mode in MODES}
    for q, vec in zip(questions, vectors):
        orders = {
            "vector": _vector_order(vec, bank),
            "hybrid": _hybrid_order(q["question"], vec, bank, bm25),
        }
        for mode, order in orders.items():
            top_idx = order[:TOP_K]
            top = [
                {"title": bank[i]["title"], "score": round(_cosine(vec, bank[i]["embedding"]), 4)}
                for i in top_idx
            ]
            results[mode].append(
                {
                    "question": q["question"],
                    "expected": q["expected_titles"],
                    "top": top,
                    "hit@1": bool(top) and top[0]["title"] in q["expected_titles"],
                    "hit@5": any(t["title"] in q["expected_titles"] for t in top),
                }
            )
    return results


def summarize(results: list[dict]) -> dict:
    n = len(results)
    hit1 = sum(1 for r in results if r["hit@1"])
    hit5 = sum(1 for r in results if r["hit@5"])
    avg_top1 = sum(r["top"][0]["score"] for r in results if r["top"]) / n
    return {
        "total": n,
        "hit1_count": hit1,
        "hit5_count": hit5,
        "hit@1": f"{hit1}/{n} ({hit1 / n:.1%})",
        "hit@5": f"{hit5}/{n} ({hit5 / n:.1%})",
        "avg_top1_score": f"{avg_top1:.4f}",
    }


def _pct(count: int, total: int) -> str:
    return f"{count / total:.1%}" if total else "-"


def build_report(results: dict[str, list[dict]]) -> str:
    stats = {mode: summarize(results[mode]) for mode in MODES}
    total = stats["vector"]["total"]
    delta1 = stats["hybrid"]["hit1_count"] - stats["vector"]["hit1_count"]
    delta5 = stats["hybrid"]["hit5_count"] - stats["vector"]["hit5_count"]

    lines = [
        "# RAG 检索评测报告",
        "",
        f"- 时间：{datetime.now(timezone.utc).astimezone():%Y-%m-%d %H:%M}",
        f"- embedding：{settings.embedding_model}（dim {settings.embedding_dim}）",
        f"- 切块：约 {settings.kb_chunk_size} 字/块，重叠 {settings.kb_chunk_overlap}",
        f"- 黄金问答集：{QA_PATH.name}（{total} 题）",
        "- 检索模式：vector（纯向量，v3.0 基线）/ hybrid（向量 + BM25，RRF 融合，生产路径）",
        "",
        "## 汇总对比",
        "",
        "| 指标 | vector（基线） | hybrid（当前） | 变化 |",
        "| --- | --- | --- | --- |",
        (
            f"| hit@1（首块即来自预期文档） | {stats['vector']['hit@1']} | "
            f"{stats['hybrid']['hit@1']} | {delta1:+d} |"
        ),
        (
            f"| hit@5（前5块含预期文档） | {stats['vector']['hit@5']} | "
            f"{stats['hybrid']['hit@5']} | {delta5:+d} |"
        ),
        (
            f"| 平均 top1 相似度 | {stats['vector']['avg_top1_score']} | "
            f"{stats['hybrid']['avg_top1_score']} | — |"
        ),
        "",
        (
            f"> hit@1 命中率 {_pct(stats['vector']['hit1_count'], total)} → "
            f"{_pct(stats['hybrid']['hit1_count'], total)}；"
            f"hit@5 命中率 {_pct(stats['vector']['hit5_count'], total)} → "
            f"{_pct(stats['hybrid']['hit5_count'], total)}。"
        ),
        "",
        "## 翻转明细（hybrid 相对 vector 的变化）",
        "",
        "| 题目 | 变化 | vector top1 | hybrid top1 |",
        "| --- | --- | --- | --- |",
    ]

    flipped = 0
    for vec_r, hyb_r in zip(results["vector"], results["hybrid"]):
        if vec_r["hit@1"] == hyb_r["hit@1"]:
            continue
        flipped += 1
        flag = "✅ 救回" if hyb_r["hit@1"] else "❌ 变差"
        vec_top1 = vec_r["top"][0]["title"] if vec_r["top"] else "-"
        hyb_top1 = hyb_r["top"][0]["title"] if hyb_r["top"] else "-"
        lines.append(f"| {hyb_r['question']} | {flag} | {vec_top1} | {hyb_top1} |")
    if flipped == 0:
        lines.append("| （无翻转，两模式 hit@1 完全一致） | — | — | — |")

    lines += [
        "",
        "## 逐题明细",
        "",
        "| 题目 | 预期文档 | vector top1 | hybrid top1 | hybrid hit@5 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for vec_r, hyb_r in zip(results["vector"], results["hybrid"]):
        lines.append(
            f"| {hyb_r['question']} | {','.join(hyb_r['expected'])} | "
            f"{vec_r['top'][0]['title']} ({vec_r['top'][0]['score']:.3f}) | "
            f"{hyb_r['top'][0]['title']} ({hyb_r['top'][0]['score']:.3f}) | "
            f"{'✅' if hyb_r['hit@5'] else '❌'} |"
        )
    return "\n".join(lines)


def main() -> None:
    bank = _load_bank()
    if not bank:
        print("[中止] 知识库为空，请先跑 scripts.seed_kb_preset 入库预置语料")
        sys.exit(2)

    results = run(bank)
    report = build_report(results)
    print(report)

    if "--report" in sys.argv:
        report_path = QA_PATH.parent / "report.md"
        report_path.write_text(report, encoding="utf-8")
        print(f"\n[已写入] {report_path}")

    stats = summarize(results["hybrid"])
    print(f"\n[hybrid] hit@1 {stats['hit@1']} · hit@5 {stats['hit@5']}")
    # 退出码：hybrid 模式下 hit@1 全部命中才算 0（严格回归阈值），否则 1
    sys.exit(0 if all(r["hit@1"] for r in results["hybrid"]) else 1)


if __name__ == "__main__":
    main()
