"""重排序服务（v4.6）：对 RRF 融合后的候选做 cross-encoder 精排。

设计要点（详见 docs/检索重排序-rerank-方案.md）：

- **接入点只有一处**：`kb_service.search_chunks_hybrid()`。调用方（RAG 问答 / Agent 的
  kb_search 工具）不感知 rerank，也不允许各自实现一份。
- **懒加载单例**：首次调用才加载模型；加载失败只记日志并永久降级，不影响服务启动。
- **超时熔断 + 异常兜底**：任何异常/超时都返回 `None`，由调用方回退到 RRF 结果 ——
  检索链路不允许因 rerank 失败而报错，这是硬约束。
- **零额外重依赖**：走 ONNX Runtime（`onnxruntime` 已是现有依赖，faster-whisper 引入）
  + `tokenizers`，**不引入 torch**。
- **本服务只接收文本、不碰数据库**：归属隔离由调用方在候选集上保证（rerank 只能
  在已过滤的候选内重排，不得引入任何跨用户文档）。

provider（`settings.kb_rerank_provider`）：

- `onnx`：本地 cross-encoder，模型目录见 `settings.kb_rerank_model_dir`
  （用 `python -m scripts.fetch_rerank_model` 下载）。
- `api` ：云端 rerank 服务（Jina / Cohere 兼容的 `POST {base}/v1/rerank`）。

用法::

    ranked = rerank(query, [d1, d2, ...], top_n=5)
    if ranked is None:      # 降级：保持 RRF 原顺序
        ...
"""

import time
from pathlib import Path
from threading import Lock

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# ONNX 导出惯例：HF 仓库把 ONNX 放在 onnx/ 子目录（如 Xenova/bge-reranker-base），
# 优先 int8 量化版（CPU 上快得多），再退到 fp32；同时兼容文件平铺在根目录的布局。
_ONNX_FILENAMES = (
    "onnx/model_quantized.onnx",
    "onnx/model.onnx",
    "model_quantized.onnx",
    "model.onnx",
)
_TOKENIZER_FILENAME = "tokenizer.json"
# bge-reranker 系列的输入拼接约定：query 与 doc 之间用两个 </s> 分隔
_QUERY_DOC_SEP = "</s></s>"


class _OnnxReranker:
    """本地 ONNX cross-encoder（懒加载单例，线程安全）。"""

    def __init__(self) -> None:
        self._session = None
        self._tokenizer = None
        self._input_names: set[str] = set()
        self._lock = Lock()
        self._failed = False  # 加载失败后不再重试，避免每次请求都撞一遍 IO

    @property
    def model_dir(self) -> Path:
        return Path(settings.kb_rerank_model_dir)

    def _ensure_loaded(self) -> bool:
        """首次调用时加载；失败只记日志（不抛），后续请求直接降级。"""
        if self._session is not None:
            return True
        if self._failed:
            return False
        with self._lock:
            if self._session is not None:
                return True
            if self._failed:
                return False
            started = time.monotonic()
            try:
                self._load()
            except Exception as exc:  # noqa: BLE001  模型缺失/依赖未装/文件损坏都降级，不阻断启动
                self._failed = True
                logger.warning(
                    "rerank 模型加载失败，将回退 RRF 结果：%s（目录 %s）",
                    exc,
                    self.model_dir,
                )
                return False
            logger.info(
                "rerank 模型已加载（dir=%s，耗时 %.2fs）",
                self.model_dir,
                time.monotonic() - started,
            )
            return True

    def _load(self) -> None:
        import onnxruntime
        from tokenizers import Tokenizer

        model_dir = self.model_dir
        model_path: Path | None = None
        for name in _ONNX_FILENAMES:
            candidate = model_dir / name
            if candidate.is_file():
                model_path = candidate
                break
        if model_path is None:
            raise FileNotFoundError(
                f"未找到 ONNX 模型（{' / '.join(_ONNX_FILENAMES)}）；"
                f"请先执行 python -m scripts.fetch_rerank_model（目录：{model_dir}）"
            )
        tokenizer_path = model_dir / _TOKENIZER_FILENAME
        if not tokenizer_path.is_file():
            raise FileNotFoundError(
                f"未找到 {_TOKENIZER_FILENAME}（目录：{model_dir}）"
            )

        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        tokenizer.enable_truncation(max_length=settings.kb_rerank_max_length)
        tokenizer.enable_padding()

        options = onnxruntime.SessionOptions()
        # 单机小模型：限制线程数，避免与 FastAPI / Celery 抢 CPU（延迟反升）
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        session = onnxruntime.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
            sess_options=options,
        )

        self._tokenizer = tokenizer
        self._session = session
        self._input_names = {item.name for item in session.get_inputs()}

    def score(
        self, query: str, documents: list[str], deadline: float
    ) -> list[float] | None:
        """逐批打分；超时或异常返回 None（调用方据此回退）。"""
        if not documents or not self._ensure_loaded():
            return None
        if self._tokenizer is None or self._session is None:
            return None

        scores: list[float] = []
        batch_size = max(1, settings.kb_rerank_batch_size)
        for start in range(0, len(documents), batch_size):
            if time.monotonic() > deadline:
                logger.warning(
                    "rerank 超时（已打分 %d/%d 条），回退 RRF 结果",
                    start,
                    len(documents),
                )
                return None
            batch = documents[start : start + batch_size]
            try:
                scores.extend(self._score_batch(query, batch))
            except Exception as exc:  # noqa: BLE001  推理异常必须降级而非冒泡（检索链路硬约束）
                logger.warning("rerank 推理失败，回退 RRF 结果：%s", exc)
                return None
        return scores

    def _score_batch(self, query: str, batch: list[str]) -> list[float]:
        import numpy as np

        if self._tokenizer is None or self._session is None:
            raise RuntimeError("rerank 模型未加载")

        encodings = self._tokenizer.encode_batch(
            [f"{query}{_QUERY_DOC_SEP}{doc}" for doc in batch]
        )
        input_ids = np.array([enc.ids for enc in encodings], dtype=np.int64)
        attention_mask = np.array(
            [enc.attention_mask for enc in encodings], dtype=np.int64
        )

        feed: dict[str, object] = {}
        if "input_ids" in self._input_names:
            feed["input_ids"] = input_ids
        if "attention_mask" in self._input_names:
            feed["attention_mask"] = attention_mask
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = np.zeros_like(input_ids)

        outputs = self._session.run(None, feed)
        logits = np.asarray(outputs[0]).reshape(len(batch), -1)[:, 0]
        return [float(1.0 / (1.0 + np.exp(-value))) for value in logits]


def _score_api(query: str, documents: list[str], deadline: float) -> list[float] | None:
    """云端 rerank（Jina / Cohere 兼容的 /v1/rerank）。失败返回 None。"""
    base = (settings.kb_rerank_api_base or "").rstrip("/")
    if not base:
        logger.warning("rerank provider=api 但未配置 kb_rerank_api_base，回退 RRF 结果")
        return None

    headers = {"Content-Type": "application/json"}
    if settings.kb_rerank_api_key:
        headers["Authorization"] = f"Bearer {settings.kb_rerank_api_key}"
    timeout = max(0.1, deadline - time.monotonic())
    try:
        response = httpx.post(
            f"{base}/v1/rerank",
            headers=headers,
            json={
                "model": settings.kb_rerank_model,
                "query": query,
                "documents": documents,
                "top_n": len(documents),
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001  网络/鉴权/格式异常一律降级为 RRF 顺序
        logger.warning("rerank API 调用失败，回退 RRF 结果：%s", exc)
        return None

    scores = [0.0] * len(documents)
    for item in payload.get("results", []):
        index = int(item.get("index", -1))
        if 0 <= index < len(documents):
            scores[index] = float(item.get("relevance_score", 0.0))
    return scores


_reranker: _OnnxReranker | None = None
_reranker_lock = Lock()


def _get_reranker() -> _OnnxReranker:
    global _reranker
    if _reranker is None:
        with _reranker_lock:
            if _reranker is None:
                _reranker = _OnnxReranker()
    return _reranker


def rerank(
    query: str,
    documents: list[str],
    top_n: int | None = None,
) -> list[tuple[int, float]] | None:
    """对候选文档做 cross-encoder 重排序。

    :return: `[(原下标, 分数)]` 按分数降序；**不可用时返回 None**（调用方保持原顺序）。
             候选少于 2 条、开关关闭、query 为空时同样返回 None（没有重排意义）。
    """
    if not settings.kb_rerank_enabled:
        return None
    if not query.strip() or len(documents) < 2:
        return None

    deadline = time.monotonic() + max(0.05, settings.kb_rerank_timeout_ms / 1000)
    if settings.kb_rerank_provider == "api":
        scores = _score_api(query, documents, deadline)
    else:
        scores = _get_reranker().score(query, documents, deadline)

    if scores is None or len(scores) != len(documents):
        return None

    ranked = sorted(enumerate(scores), key=lambda pair: pair[1], reverse=True)
    return ranked[:top_n] if top_n else ranked


def reset_for_tests() -> None:
    """仅供测试：清掉单例，使下次调用重新走加载逻辑。"""
    global _reranker
    with _reranker_lock:
        _reranker = None
