"""embedding 封装层：本地 Ollama 起，留云端切换点（v3.0）。

OpenAI 兼容协议：本地 Ollama 用 base_url=http://localhost:11434/v1 + 占位 key；
切云端（如阿里云 text-embedding-v3 / 智谱）只需改 settings 三行（base_url/api_key/model）。
向量化在 Ollama 容器完成，Python 侧无本地模型依赖（requirements 只加 pgvector）。
"""

from openai import OpenAI

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """模块级复用 OpenAI 客户端：每次调用新建连接池开销大（settings 运行期不变）。"""
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            timeout=60.0,
        )
    return _client


from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class EmbeddingError(Exception):
    """embedding 调用失败（Ollama 未起 / 模型未拉 / 云端 key 无效）。message 为用户话术。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化一组文本，返回等长 list（每个元素是该文本的向量）。

    Ollama 的 /v1/embeddings 支持数组输入，一次调用批量返回；维度由 settings.embedding_dim 约定。
    """
    if not texts:
        return []
    client = _get_client()
    try:
        resp = client.embeddings.create(
            model=settings.embedding_model, input=list(texts)
        )
    except Exception as exc:  # noqa: BLE001  SDK 异常种类随服务商变化，统一给话术
        logger.error(
            "embedding 调用失败 model=%s exc=%s: %s",
            settings.embedding_model,
            type(exc).__name__,
            exc,
        )
        raise EmbeddingError("向量模型暂不可用，请检查 Ollama 服务或模型配置") from None
    return [item.embedding for item in resp.data]
