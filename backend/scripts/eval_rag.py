"""RAG 检索评测脚本（v3.0 起，v3.5 加混合检索对比）：黄金问答集 vs 预置语料。

度量"检索质量"而非"生成质量"——对每题，embedding 问题后在库内做 top-k 检索
（不经过 kb_min_similarity 阈值，纯看排序），检查预期来源文档是否出现在 top 结果里。

v3.5 起同一份问答集跑两种模式并排对比，v4.6 起增加第三路：
  - vector：纯向量（nomic-embed-text 余弦），即 v3.0 基线
  - hybrid：向量 + BM25 词法，RRF 融合（生产实际走的路径）
  - hybrid_rerank：hybrid 之上叠加 cross-encoder 重排序。**rerank 段直接调用生产实现
    `app.services.rerank_service.rerank`，不另写一份**——评测与生产共用同一段逻辑，
    避免"影子实现漂移"（`_hybrid_order` 是历史遗留的影子复刻，仅 RRF 部分）。
改切块参数 / embedding 模型 / 检索逻辑后重跑本脚本，即为 RAG 基线回归。

指标：hit@1 / hit@5 / **MRR@5** / **nDCG@5**。后两者为 v4.6 新增——hit@k 是集合型
指标，衡量不了"把正确块从第 3 名提到第 1 名"，会系统性低估 rerank 的收益。

rerank 不可用时（模型未下载 / 依赖缺失 / 超时）hybrid_rerank 的结果与 hybrid 完全相同，
报告会显示"无翻转"，可据此判断模型是否真的生效。

用法（backend/ 目录下）：
    ./.venv/Scripts/python.exe -m scripts.eval_rag            # 三路对比，输出到 stdout
    ./.venv/Scripts/python.exe -m scripts.eval_rag --report    # 覆盖写 data/kb_eval/report.md
"""

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.kb import KBChunk, KBDocument
from app.services.embedding_service import embed_texts
from app.services.lexical_service import Bm25Index
from app.services.rerank_service import rerank

QA_PATH = Path(__file__).resolve().parents[1].parent / "data" / "kb_eval" / "qa.json"
TOP_K = 5
MODES = ("vector", "hybrid", "hybrid_rerank")


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
        {
            "id": c.id,
            "content": c.content or "",
            "embedding": c.embedding,
            "title": title,
        }
        for c, title in rows
    ]


def _vector_order(query_vec: list[float], bank: list[dict]) -> list[int]:
    """纯向量排序，返回 bank 下标序列（降序）。"""
    scored = [(_cosine(query_vec, item["embedding"]), i) for i, item in enumerate(bank)]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [i for _score, i in scored]


def _hybrid_order(
    query: str, query_vec: list[float], bank: list[dict], bm25: Bm25Index
) -> list[int]:
    """向量 + BM25 两路各自排名 → RRF 融合，返回 bank 下标序列（降序）。"""
    vector_rank = {
        idx: rank for rank, idx in enumerate(_vector_order(query_vec, bank), start=1)
    }
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


def _hybrid_rerank_order(
    query: str,
    query_vec: list[float],
    bank: list[dict],
    bm25: Bm25Index,
    timing: dict[str, float],
) -> list[int]:
    """hybrid 顺序 → cross-encoder 精排（rerank 段直接调生产实现，不重写）。

    口径与生产 `kb_service._apply_rerank` 完全对齐：只对前 `kb_rerank_candidates`
    条打分；不可用时原样返回 hybrid 顺序；尾部未参与打分的候选保持原相对顺序。
    `timing` 累计 rerank 调用次数与耗时（CPU 上的真实延迟是本方案的核心关注点）。
    """
    base_order = _hybrid_order(query, query_vec, bank, bm25)
    candidate_n = max(1, settings.kb_rerank_candidates)
    candidates = base_order[:candidate_n]

    started = time.monotonic()
    ranked = rerank(query, [bank[i]["content"] for i in candidates])
    timing["rerank_calls"] = timing.get("rerank_calls", 0) + 1
    timing["rerank_seconds"] = timing.get("rerank_seconds", 0.0) + (
        time.monotonic() - started
    )
    if ranked is None:
        timing["rerank_degraded"] = timing.get("rerank_degraded", 0) + 1
        return base_order

    ordered = [candidates[idx] for idx, _score in ranked]
    ordered.extend(base_order[candidate_n:])
    return ordered


def _dcg_at_k(titles: list[str], expected: list[str]) -> float:
    """二值相关性下的 DCG@k：标题命中预期文档即视为相关。"""
    return sum(
        1.0 / math.log2(pos + 1)
        for pos, title in enumerate(titles, start=1)
        if title in expected
    )


def _ideal_dcg(bank: list[dict], expected: list[str]) -> float:
    """理想 DCG@5：库内属于预期文档的块（上限 TOP_K）按最优位次排列。"""
    hits = min(sum(1 for item in bank if item["title"] in expected), TOP_K)
    return sum(1.0 / math.log2(pos + 1) for pos in range(1, hits + 1))


def run(bank: list[dict]) -> tuple[dict[str, list[dict]], dict[str, float]]:
    questions = json.loads(QA_PATH.read_text(encoding="utf-8"))
    vectors = embed_texts([q["question"] for q in questions])  # 一次批量向量化
    bm25 = Bm25Index([(i, item["content"]) for i, item in enumerate(bank)])
    timing: dict[str, float] = {}

    results: dict[str, list[dict]] = {mode: [] for mode in MODES}
    for q, vec in zip(questions, vectors):
        expected = q["expected_titles"]
        orders = {
            "vector": _vector_order(vec, bank),
            "hybrid": _hybrid_order(q["question"], vec, bank, bm25),
        }
        orders["hybrid_rerank"] = _hybrid_rerank_order(
            q["question"], vec, bank, bm25, timing
        )

        for mode, order in orders.items():
            top_idx = order[:TOP_K]
            top = [
                {
                    "title": bank[i]["title"],
                    "score": round(_cosine(vec, bank[i]["embedding"]), 4),
                }
                for i in top_idx
            ]
            titles = [t["title"] for t in top]
            first_rank = next(
                (pos for pos, title in enumerate(titles, start=1) if title in expected),
                None,
            )
            ideal = _ideal_dcg(bank, expected)
            dcg = _dcg_at_k(titles, expected)
            results[mode].append(
                {
                    "question": q["question"],
                    "expected": expected,
                    "top": top,
                    "hit@1": bool(titles) and titles[0] in expected,
                    "hit@5": first_rank is not None,
                    "first_rank": first_rank,
                    "reciprocal_rank": (1.0 / first_rank) if first_rank else 0.0,
                    "ndcg@5": (dcg / ideal) if ideal else 0.0,
                }
            )
    return results, timing


def summarize(results: list[dict]) -> dict:
    n = len(results)
    hit1 = sum(1 for r in results if r["hit@1"])
    hit5 = sum(1 for r in results if r["hit@5"])
    avg_top1 = sum(r["top"][0]["score"] for r in results if r["top"]) / n
    mrr = sum(r["reciprocal_rank"] for r in results) / n
    ndcg = sum(r["ndcg@5"] for r in results) / n
    return {
        "total": n,
        "hit1_count": hit1,
        "hit5_count": hit5,
        "hit@1": f"{hit1}/{n} ({hit1 / n:.1%})",
        "hit@5": f"{hit5}/{n} ({hit5 / n:.1%})",
        "mrr": mrr,
        "ndcg": ndcg,
        "mrr@5": f"{mrr:.4f}",
        "ndcg@5": f"{ndcg:.4f}",
        "avg_top1_score": f"{avg_top1:.4f}",
    }


def _pct(count: int, total: int) -> str:
    return f"{count / total:.1%}" if total else "-"


def build_report(results: dict[str, list[dict]], timing: dict[str, float]) -> str:
    stats = {mode: summarize(results[mode]) for mode in MODES}
    total = stats["vector"]["total"]
    base, hyb, rrk = stats["vector"], stats["hybrid"], stats["hybrid_rerank"]
    r_calls = int(timing.get("rerank_calls", 0))
    r_degraded = int(timing.get("rerank_degraded", 0))
    r_avg_ms = (timing.get("rerank_seconds", 0.0) / r_calls * 1000) if r_calls else 0.0

    lines = [
        "# RAG 检索评测报告",
        "",
        f"- 时间：{datetime.now(timezone.utc).astimezone():%Y-%m-%d %H:%M}",
        f"- embedding：{settings.embedding_model}（dim {settings.embedding_dim}）",
        f"- 切块：约 {settings.kb_chunk_size} 字/块，重叠 {settings.kb_chunk_overlap}",
        f"- 黄金问答集：{QA_PATH.name}（{total} 题）",
        (
            "- 检索模式：vector（纯向量，v3.0 基线）/ hybrid（向量 + BM25，RRF 融合，"
            "生产路径）/ hybrid_rerank（hybrid 之上叠加 cross-encoder 重排序）"
        ),
        (
            f"- rerank：provider={settings.kb_rerank_provider} · "
            f"model={settings.kb_rerank_model} · 候选 {settings.kb_rerank_candidates} 条 · "
            f"超时 {settings.kb_rerank_timeout_ms}ms · 实际调用 {r_calls} 次 / "
            f"降级 {r_degraded} 次 / 单次均耗时 {r_avg_ms:.0f}ms"
        ),
        "",
        "## 汇总对比",
        "",
        "| 指标 | vector（基线） | hybrid（当前） | hybrid_rerank | Δ(hybrid→rerank) |",
        "| --- | --- | --- | --- | --- |",
        (
            f"| hit@1（首块即来自预期文档） | {base['hit@1']} | {hyb['hit@1']} | "
            f"{rrk['hit@1']} | {rrk['hit1_count'] - hyb['hit1_count']:+d} |"
        ),
        (
            f"| hit@5（前5块含预期文档） | {base['hit@5']} | {hyb['hit@5']} | "
            f"{rrk['hit@5']} | {rrk['hit5_count'] - hyb['hit5_count']:+d} |"
        ),
        (
            f"| MRR@5（首个正确块位次均值） | {base['mrr@5']} | {hyb['mrr@5']} | "
            f"{rrk['mrr@5']} | {rrk['mrr'] - hyb['mrr']:+.4f} |"
        ),
        (
            f"| nDCG@5（排序质量） | {base['ndcg@5']} | {hyb['ndcg@5']} | "
            f"{rrk['ndcg@5']} | {rrk['ndcg'] - hyb['ndcg']:+.4f} |"
        ),
        (
            f"| 平均 top1 相似度 | {base['avg_top1_score']} | {hyb['avg_top1_score']} | "
            f"{rrk['avg_top1_score']} | — |"
        ),
        "",
        (
            f"> hit@1 {_pct(base['hit1_count'], total)} → {_pct(hyb['hit1_count'], total)}"
            f" → {_pct(rrk['hit1_count'], total)}；"
            f"hit@5 {_pct(base['hit5_count'], total)} → {_pct(hyb['hit5_count'], total)}"
            f" → {_pct(rrk['hit5_count'], total)}。"
        ),
    ]
    if r_degraded:
        lines.append(
            f"> ⚠️ rerank 降级 {r_degraded}/{r_calls} 次（模型未就绪 / 超时 / 异常）——"
            "降级时 hybrid_rerank 与 hybrid 结果完全相同，请先确认模型可用再解读增益。"
        )

    lines += [
        "",
        "## 翻转明细",
        "",
        "### hybrid 相对 vector（v3.5 混合检索的收益）",
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
        "### hybrid_rerank 相对 hybrid（v4.6 重排序的收益）",
        "",
        "| 题目 | 变化 | 首个正确块位次 | hybrid top1 | rerank top1 |",
        "| --- | --- | --- | --- | --- |",
    ]
    r_flipped = 0
    for hyb_r, rrk_r in zip(results["hybrid"], results["hybrid_rerank"]):
        if hyb_r["first_rank"] == rrk_r["first_rank"]:
            continue
        r_flipped += 1
        if rrk_r["hit@1"] and not hyb_r["hit@1"]:
            flag = "✅ 提到首位"
        elif hyb_r["hit@1"] and not rrk_r["hit@1"]:
            flag = "❌ 挤出首位"
        else:
            flag = "↕ 位次变化"
        hyb_top1 = hyb_r["top"][0]["title"] if hyb_r["top"] else "-"
        rrk_top1 = rrk_r["top"][0]["title"] if rrk_r["top"] else "-"
        rank_txt = f"{hyb_r['first_rank'] or '-'} → {rrk_r['first_rank'] or '-'}"
        lines.append(
            f"| {rrk_r['question']} | {flag} | {rank_txt} | {hyb_top1} | {rrk_top1} |"
        )
    if r_flipped == 0:
        lines.append(
            "| （无位次变化——rerank 未生效，或对全部题目都不改变顺序） | — | — | — | — |"
        )

    lines += [
        "",
        "## 逐题明细",
        "",
        "| 题目 | 预期文档 | vector top1 | hybrid top1 | rerank top1 | rerank hit@5 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for vec_r, hyb_r, rrk_r in zip(
        results["vector"], results["hybrid"], results["hybrid_rerank"]
    ):
        lines.append(
            f"| {rrk_r['question']} | {','.join(rrk_r['expected'])} | "
            f"{vec_r['top'][0]['title']} ({vec_r['top'][0]['score']:.3f}) | "
            f"{hyb_r['top'][0]['title']} ({hyb_r['top'][0]['score']:.3f}) | "
            f"{rrk_r['top'][0]['title']} ({rrk_r['top'][0]['score']:.3f}) | "
            f"{'✅' if rrk_r['hit@5'] else '❌'} |"
        )
    return "\n".join(lines)


def main() -> None:
    bank = _load_bank()
    if not bank:
        print("[中止] 知识库为空，请先跑 scripts.seed_kb_preset 入库预置语料")
        sys.exit(2)

    # 评测本身就是要度量 rerank，故这里显式打开开关（生产默认关，见 settings 注释）。
    # 模型未下载 / 依赖缺失时 rerank 会降级 —— 报告里 hybrid_rerank 与 hybrid 完全一致
    # 即说明它没有生效，据此判断是"无收益"还是"没跑起来"。
    if not settings.kb_rerank_enabled:
        settings.kb_rerank_enabled = True
        print(
            f"[提示] 已临时打开 kb_rerank_enabled 用于评测"
            f"（model={settings.kb_rerank_model}）"
        )

    results, timing = run(bank)
    report = build_report(results, timing)
    print(report)

    if "--report" in sys.argv:
        report_path = QA_PATH.parent / "report.md"
        report_path.write_text(report, encoding="utf-8")
        print(f"\n[已写入] {report_path}")

    base = summarize(results["vector"])
    stats = summarize(results["hybrid"])
    rrk = summarize(results["hybrid_rerank"])
    print(f"\n[vector]        hit@1 {base['hit@1']} · hit@5 {base['hit@5']}")
    print(
        f"[hybrid]        hit@1 {stats['hit@1']} · hit@5 {stats['hit@5']}"
        f" · MRR {stats['mrr@5']} · nDCG {stats['ndcg@5']}"
    )
    print(
        f"[hybrid_rerank] hit@1 {rrk['hit@1']} · hit@5 {rrk['hit@5']}"
        f" · MRR {rrk['mrr@5']} · nDCG {rrk['ndcg@5']}"
    )
    calls = int(timing.get("rerank_calls", 0))
    if calls:
        print(
            f"[rerank] 调用 {calls} 次 · 降级 {int(timing.get('rerank_degraded', 0))} 次 · "
            f"单次均耗时 {timing.get('rerank_seconds', 0.0) / calls * 1000:.0f}ms"
        )

    # 退出码与基线对齐（data/kb_eval/report.md：43/49 = 87.8%）：不低于基线即 0，
    # 低于基线说明检索质量回退，CI/外层脚本据此失败；原"全命中才 0"的口径
    # 让退出码恒为 1，自动化集成把成功的回归判定为失败（P1 修复）
    sys.exit(0 if stats["hit1_count"] >= round(49 * 0.878) else 1)


if __name__ == "__main__":
    main()
