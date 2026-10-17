"""重排序服务测试（v4.6）：全 mock，不加载真模型、不联网。

覆盖四类路径：
1. 不满足启用条件时**直接返回 None**（开关关 / 候选不足 / 空 query）；
2. 模型不可用时的**降级**（目录缺失、加载失败只试一次、超时、API 未配置、API 报错）；
3. 正常打分后的**排序**（降序、top_n 截断）；
4. **调用方无感知**：降级绝不抛异常。

真模型的端到端效果由 `scripts/eval_rag.py` 的 hybrid_rerank 模式度量（离线、可复跑）。
"""

import time

import pytest

from app.core.config import settings
from app.services import rerank_service
from app.services.rerank_service import _OnnxReranker, rerank, reset_for_tests


@pytest.fixture(autouse=True)
def _isolate_rerank():
    """用例前后重置单例，避免模型加载状态在用例间串味。"""
    reset_for_tests()
    yield
    reset_for_tests()


def _enable(monkeypatch, **overrides):
    """打开 rerank 开关并按需覆盖参数（monkeypatch 会在用例结束自动还原）。"""
    monkeypatch.setattr(settings, "kb_rerank_enabled", True)
    for key, value in overrides.items():
        monkeypatch.setattr(settings, key, value)


# —— 1. 启用条件不满足 ——


def test_disabled_returns_none(monkeypatch) -> None:
    """开关关闭：不加载模型、不调用 provider，直接返回 None。"""
    monkeypatch.setattr(settings, "kb_rerank_enabled", False)
    assert rerank("问题", ["文档一", "文档二"]) is None


def test_too_few_documents_returns_none(monkeypatch) -> None:
    """候选少于 2 条时没有重排意义（省一次模型调用）。"""
    _enable(monkeypatch)
    assert rerank("问题", []) is None
    assert rerank("问题", ["只有一条"]) is None


def test_blank_query_returns_none(monkeypatch) -> None:
    """空白 query 不触发模型调用。"""
    _enable(monkeypatch)
    assert rerank("   ", ["文档一", "文档二"]) is None


# —— 2. 降级路径（都不允许抛异常） ——


def test_missing_model_dir_degrades(monkeypatch) -> None:
    """模型目录不存在：返回 None（调用方回退 RRF），且不抛异常。"""
    _enable(monkeypatch)
    monkeypatch.setattr(settings, "kb_rerank_model_dir", "models/__not_exists__")
    assert rerank("问题", ["文档一", "文档二"]) is None


def test_load_failure_attempted_once(monkeypatch) -> None:
    """加载失败后不再重试（避免每次请求都撞一遍文件 IO）。"""
    _enable(monkeypatch)
    monkeypatch.setattr(settings, "kb_rerank_model_dir", "models/__not_exists__")

    reranker = _OnnxReranker()
    calls = {"n": 0}
    original = reranker._load

    def counting_load() -> None:
        calls["n"] += 1
        original()

    monkeypatch.setattr(reranker, "_load", counting_load)
    assert reranker._ensure_loaded() is False
    assert reranker._ensure_loaded() is False
    assert calls["n"] == 1


def test_timeout_degrades(monkeypatch) -> None:
    """deadline 已过：立刻返回 None，不进入推理。"""
    reranker = _OnnxReranker()
    monkeypatch.setattr(reranker, "_ensure_loaded", lambda: True)
    monkeypatch.setattr(reranker, "_tokenizer", object(), raising=False)
    monkeypatch.setattr(reranker, "_session", object(), raising=False)

    scores = reranker.score("问题", ["文档一", "文档二"], deadline=time.monotonic() - 1)
    assert scores is None


def test_inference_error_degrades(monkeypatch) -> None:
    """推理抛异常：吞掉并返回 None，异常不得冒泡到检索链路。"""
    reranker = _OnnxReranker()
    monkeypatch.setattr(reranker, "_ensure_loaded", lambda: True)
    monkeypatch.setattr(reranker, "_tokenizer", object(), raising=False)
    monkeypatch.setattr(reranker, "_session", object(), raising=False)

    def boom(_query: str, _batch: list[str]) -> list[float]:
        raise RuntimeError("onnx 推理炸了")

    monkeypatch.setattr(reranker, "_score_batch", boom)
    monkeypatch.setattr(rerank_service, "_get_reranker", lambda: reranker)
    _enable(monkeypatch)

    assert rerank("问题", ["文档一", "文档二"]) is None


def test_api_provider_without_base_degrades(monkeypatch) -> None:
    """provider=api 但没配地址：返回 None，不发请求。"""
    _enable(monkeypatch, kb_rerank_provider="api", kb_rerank_api_base="")
    assert rerank("问题", ["文档一", "文档二"]) is None


def test_api_provider_error_degrades(monkeypatch) -> None:
    """API 报错：返回 None，不抛异常。"""
    _enable(monkeypatch, kb_rerank_provider="api", kb_rerank_api_base="http://x")

    def boom(*_args, **_kwargs):
        raise RuntimeError("连接被拒")

    monkeypatch.setattr(rerank_service.httpx, "post", boom)
    assert rerank("问题", ["文档一", "文档二"]) is None


# —— 3. 正常路径 ——


def test_scores_sorted_desc_with_top_n(monkeypatch) -> None:
    """按分数降序返回（原下标, 分数），并支持 top_n 截断。"""
    reranker = _OnnxReranker()
    monkeypatch.setattr(reranker, "_ensure_loaded", lambda: True)
    monkeypatch.setattr(reranker, "_tokenizer", object(), raising=False)
    monkeypatch.setattr(reranker, "_session", object(), raising=False)
    monkeypatch.setattr(
        reranker,
        "_score_batch",
        lambda _q, batch: [float(i + 1) for i in range(len(batch))],
    )
    monkeypatch.setattr(rerank_service, "_get_reranker", lambda: reranker)
    _enable(monkeypatch, kb_rerank_batch_size=10)  # 单批：分数 1.0 / 2.0 / 3.0

    ranked = rerank("问题", ["文一", "文二", "文三"])
    assert ranked == [(2, 3.0), (1, 2.0), (0, 1.0)]
    assert rerank("问题", ["文一", "文二", "文三"], top_n=2) == [(2, 3.0), (1, 2.0)]


def test_api_provider_parses_results(monkeypatch) -> None:
    """API 返回 results[] 时按 index 还原成与输入等长的分数数组。"""
    _enable(monkeypatch, kb_rerank_provider="api", kb_rerank_api_base="http://x")

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.2},
                ]
            }

    monkeypatch.setattr(rerank_service.httpx, "post", lambda *a, **k: _Resp())
    assert rerank("问题", ["文一", "文二"]) == [(1, 0.9), (0, 0.2)]


def test_score_mismatch_degrades(monkeypatch) -> None:
    """provider 返回条数与候选数不符：视为异常，回退 None。"""
    reranker = _OnnxReranker()
    monkeypatch.setattr(reranker, "_ensure_loaded", lambda: True)
    monkeypatch.setattr(reranker, "_tokenizer", object(), raising=False)
    monkeypatch.setattr(reranker, "_session", object(), raising=False)
    monkeypatch.setattr(reranker, "_score_batch", lambda _q, _b: [0.5])  # 少一条
    monkeypatch.setattr(rerank_service, "_get_reranker", lambda: reranker)
    _enable(monkeypatch)

    assert rerank("问题", ["文一", "文二"]) is None
