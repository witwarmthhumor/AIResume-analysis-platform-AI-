"""RAG 检索评测脚本（v3.0）：黄金问答集 vs 预置语料。

度量"检索质量"而非"生成质量"——对每题，embedding 问题后在库内做余弦 top-k 检索
（不经过 kb_min_similarity 阈值，纯看排序），检查预期来源文档是否出现在 top 结果里。
改切块参数 / embedding 模型后重跑本脚本对比，即为 RAG 基线回归。

用法（backend/ 目录下）：
    ./.venv/Scripts/python.exe -m scripts.eval_rag          # 输出到 stdout
    ./.venv/Scripts/python.exe -m scripts.eval_rag --report # 追加写 data/kb_eval/report.md
"""

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.kb import KBChunk, KBDocument
from app.services.embedding_service import embed_texts

QA_PATH = Path(__file__).resolve().parents[1].parent / "data" / "kb_eval" / "qa.json"
TOP_K = 5


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def run() -> list[dict]:
    questions = json.loads(QA_PATH.read_text(encoding="utf-8"))
    with SessionLocal() as db:
        # 一次性取回全部 ready 块（语料规模小，全量内存余弦即可，无需 SQL 距离）
        rows = db.execute(
            select(KBChunk, KBDocument.title)
            .join(KBDocument, KBChunk.document_id == KBDocument.id)
            .where(
                KBDocument.deleted_at.is_(None),
                KBDocument.status == "ready",
            )
        ).all()
    bank = [(c.embedding, title) for c, title in rows]

    vectors = embed_texts([q["question"] for q in questions])  # 一次批量向量化
    results = []
    for q, vec in zip(questions, vectors):
        scored = sorted(
            ((_cosine(vec, emb), title) for emb, title in bank), reverse=True
        )[:TOP_K]
        results.append(
            {
                "question": q["question"],
                "expected": q["expected_titles"],
                "top": [{"title": t, "score": round(s, 4)} for s, t in scored],
                "hit@1": scored[0][1] in q["expected_titles"] if scored else False,
                "hit@5": any(t in q["expected_titles"] for _, t in scored),
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
        "hit@1": f"{hit1}/{n} ({hit1 / n:.1%})",
        "hit@5": f"{hit5}/{n} ({hit5 / n:.1%})",
        "avg_top1_score": f"{avg_top1:.4f}",
    }


def main() -> None:
    results = run()
    stats = summarize(results)
    lines = [
        "# RAG 检索评测报告",
        "",
        f"- 时间：{__import__('datetime').datetime.now():%Y-%m-%d %H:%M}",
        f"- embedding：{settings.embedding_model}（dim {settings.embedding_dim}）",
        f"- 切块：约 {settings.kb_chunk_size} 字/块，重叠 {settings.kb_chunk_overlap}",
        f"- 黄金问答集：{QA_PATH.name}（{stats['total']} 题）",
        "",
        "## 汇总",
        "",
        "| 指标 | 结果 |",
        "| --- | --- |",
        f"| hit@1（首块即来自预期文档） | {stats['hit@1']} |",
        f"| hit@5（前5块含预期文档） | {stats['hit@5']} |",
        f"| 平均 top1 相似度 | {stats['avg_top1_score']} |",
        "",
        "## 逐题明细",
        "",
        "| 题目 | 预期文档 | 命中 | top1 |",
        "| --- | --- | --- | --- |",
    ]
    for r in results:
        top1 = r["top"][0] if r["top"] else {}
        flag = "✅" if r["hit@5"] else "❌"
        lines.append(
            f"| {r['question']} | {','.join(r['expected'])} | {flag} | "
            f"{top1.get('title','-')} ({top1.get('score', 0):.3f}) |"
        )

    report = "\n".join(lines)
    print(report)

    if "--report" in sys.argv:
        report_path = QA_PATH.parent / "report.md"
        report_path.write_text(report, encoding="utf-8")
        print(f"\n[已写入] {report_path}")

    # 退出码：hit@1 全部命中才算 0（回归阈值），否则 1
    sys.exit(0 if all(r["hit@1"] for r in results) else 1)


if __name__ == "__main__":
    main()
