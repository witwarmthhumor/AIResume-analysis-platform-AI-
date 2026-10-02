"""三个外部边界小模块的直测（v4.3 P1-7 覆盖率短板补齐）。

- task_service.task_status：Celery AsyncResult 的状态映射（薄封装也要测薄口径）；
- agent.llm_factory.build_chat_llm：未配置 Key 的 503 前置校验 + ChatOpenAI 参数装配；
- embedding_service.embed_texts：批量入参透传 / 空列表短路 / 异常统一转 EmbeddingError。

三个模块都是外部 IO 边界（Celery/OpenAI SDK），全部 stub，不连真服务、不烧额度。
"""

from types import SimpleNamespace

import pytest

from app.core.config import settings


# —— task_service.task_status ——


class _FakeAsyncResult:
    """按 (status, successful, failed, result) 预设行为的 AsyncResult 替身。"""

    _preset: dict = {}

    def __init__(self, task_id: str, app=None) -> None:
        self.task_id = task_id
        preset = self._preset[task_id]
        self._status = preset["status"]
        self._successful = preset["successful"]
        self._failed = preset["failed"]
        self._result = preset.get("result")

    @property
    def status(self) -> str:
        return self._status

    def successful(self) -> bool:
        return self._successful

    def failed(self) -> bool:
        return self._failed

    @property
    def result(self):
        return self._result


@pytest.fixture
def fake_async_result(monkeypatch):
    def _install(presets: dict):
        _FakeAsyncResult._preset = presets
        monkeypatch.setattr(
            "app.services.task_service.AsyncResult", _FakeAsyncResult
        )

    return _install


def test_task_status_success_carries_result(fake_async_result, monkeypatch):
    fake_async_result(
        {"t1": {"status": "SUCCESS", "successful": True, "failed": False, "result": {"n": 1}}}
    )
    from app.services import task_service

    payload = task_service.task_status("t1")
    assert payload == {"task_id": "t1", "status": "success", "result": {"n": 1}}


def test_task_status_failure_maps_to_user_facing_error(fake_async_result):
    fake_async_result(
        {"t2": {"status": "FAILURE", "successful": False, "failed": True}}
    )
    from app.services import task_service

    payload = task_service.task_status("t2")
    assert payload == {"task_id": "t2", "status": "failure", "error": "异步任务执行失败"}


@pytest.mark.parametrize("raw_status", ["PENDING", "STARTED", "RETRY"])
def test_task_status_in_flight_has_no_result_nor_error(fake_async_result, raw_status):
    """进行中的任务不携带 result/error 两个键（路由层按缺失判断未完成）。"""
    fake_async_result(
        {f"t-{raw_status}": {"status": raw_status, "successful": False, "failed": False}}
    )
    from app.services import task_service

    payload = task_service.task_status(f"t-{raw_status}")
    assert payload == {"task_id": f"t-{raw_status}", "status": raw_status.lower()}
    assert "result" not in payload and "error" not in payload


# —— agent.llm_factory.build_chat_llm ——


def test_build_chat_llm_raises_without_key(monkeypatch):
    monkeypatch.setattr(settings, "ai_api_key", "")
    from app.services.agent.llm_factory import build_chat_llm

    with pytest.raises(ValueError, match="AI 服务未配置"):
        build_chat_llm()


def test_build_chat_llm_raises_without_base_url(monkeypatch):
    monkeypatch.setattr(settings, "ai_api_key", "sk-test")
    monkeypatch.setattr(settings, "ai_base_url", "")
    from app.services.agent.llm_factory import build_chat_llm

    with pytest.raises(ValueError, match="AI_BASE_URL"):
        build_chat_llm()


def test_build_chat_llm_assembles_streaming_client(monkeypatch):
    """参数装配：streaming/temperature 固定口径，其余四项全部来自 settings。"""
    monkeypatch.setattr(settings, "ai_api_key", "sk-test")
    monkeypatch.setattr(settings, "ai_base_url", "http://fake/v1")
    monkeypatch.setattr(settings, "ai_model", "fake-model")
    monkeypatch.setattr(settings, "ai_timeout_seconds", 66)
    monkeypatch.setattr(settings, "ai_max_tokens", 2048)
    from app.services.agent.llm_factory import build_chat_llm

    llm = build_chat_llm()
    assert llm.model_name == "fake-model"
    assert llm.openai_api_base == "http://fake/v1"
    assert llm.openai_api_key.get_secret_value() == "sk-test"
    assert llm.request_timeout == 66
    assert llm.max_tokens == 2048
    assert llm.streaming is True
    assert llm.temperature == 0.4


def test_build_chat_llm_prefers_runtime_settings():
    """传入显式 runtime_settings 时优先于全局 settings（eval 脚本注入假配置的通路）。"""
    from app.core.config import Settings
    from app.services.agent.llm_factory import build_chat_llm

    runtime = Settings(
        ai_api_key="sk-runtime",
        ai_base_url="http://runtime/v1",
        ai_model="runtime-model",
    )
    llm = build_chat_llm(runtime)
    assert llm.model_name == "runtime-model"
    assert llm.openai_api_key.get_secret_value() == "sk-runtime"


# —— embedding_service.embed_texts ——


@pytest.fixture
def embedding_module(monkeypatch):
    """返回可注入假客户端的 embedding 模块（每次用例隔离单例状态）。"""
    import app.services.embedding_service as emb

    monkeypatch.setattr(emb, "_client", None)
    return emb


def test_embed_texts_empty_list_short_circuits(embedding_module, monkeypatch):
    """空入参不建客户端、不发请求（短路返回空列表）。"""

    def _boom():
        raise AssertionError("空入参不应创建 OpenAI 客户端")

    monkeypatch.setattr(embedding_module, "_get_client", _boom)
    assert embedding_module.embed_texts([]) == []


def test_embed_texts_passes_model_and_input(embedding_module, monkeypatch):
    captured = {}

    class _FakeEmbeddings:
        def create(self, model, input):
            captured["model"] = model
            captured["input"] = input
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.1, 0.2]), SimpleNamespace(embedding=[0.3, 0.4])]
            )

    monkeypatch.setattr(
        embedding_module,
        "_get_client",
        lambda: SimpleNamespace(embeddings=_FakeEmbeddings()),
    )
    out = embedding_module.embed_texts(["文本一", "文本二"])
    assert out == [[0.1, 0.2], [0.3, 0.4]]  # 等长且按顺序
    assert captured["model"] == settings.embedding_model
    assert captured["input"] == ["文本一", "文本二"]


def test_embed_texts_wraps_any_sdk_error(embedding_module, monkeypatch):
    """SDK 异常种类随服务商变化：统一转 EmbeddingError 并给用户话术，不裸抛。

    注意：_get_client 在 try 之外（构造失败属配置错误，裸抛合理），
    包装的是 embeddings.create 这一 SDK 调用段的异常。
    """

    class _BrokenEmbeddings:
        def create(self, model, input):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(
        embedding_module,
        "_get_client",
        lambda: SimpleNamespace(embeddings=_BrokenEmbeddings()),
    )
    from app.services.embedding_service import EmbeddingError

    with pytest.raises(EmbeddingError) as exc_info:
        embedding_module.embed_texts(["任意"])
    assert "向量模型暂不可用" in exc_info.value.message


def test_get_client_is_singleton_with_settings(embedding_module, monkeypatch):
    """客户端模块级复用：同 base_url/api_key 只建一次（60s 超时口径）。"""
    created = []

    class _FakeOpenAI:
        def __init__(self, base_url, api_key, timeout):
            created.append({"base_url": base_url, "api_key": api_key, "timeout": timeout})

    monkeypatch.setattr(embedding_module, "OpenAI", _FakeOpenAI)
    c1 = embedding_module._get_client()
    c2 = embedding_module._get_client()
    assert c1 is c2
    assert len(created) == 1
    assert created[0]["base_url"] == settings.embedding_base_url
    assert created[0]["api_key"] == settings.embedding_api_key
    assert created[0]["timeout"] == 60.0
