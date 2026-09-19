"""阶段2 接口测试：AI 分析（全部 mock，不调真实大模型）、重试、限流、去重。

分两层 mock：
- 路由层：替换 app.api.analyses.analyze_resume，测接口行为（去重/限流/落库/留痕）；
- 封装层：替换 ai_client.OpenAI 为假客户端，测 JSON 校验与重试逻辑。
前置：db 容器运行中；用后按标记清理本文件造的数据和 uploads/（v3.7 测试隔离改造）。
"""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF
from sqlalchemy import text

from app.api.resumes import UPLOAD_DIR
from app.core.config import settings
from app.db.session import engine
from app.main import app
from app.services.ai_client import AIError, AnalysisResult

client = TestClient(app)


def make_text_pdf(content: str = "Fake resume: Zhang San, Python, 3 years.") -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 6, content)
    return bytes(pdf.output())


def valid_report() -> dict:
    return {
        "target_position": "后端开发工程师",
        "position_match": "整体匹配，经验略浅。",
        "strengths": ["基础扎实"],
        "weaknesses": ["缺大型项目"],
        "keyword_gaps": ["分布式"],
        "suggestions": ["补充量化成果"],
        "predicted_questions": ["讲讲你的项目"],
    }


def fake_analysis_result() -> AnalysisResult:
    return AnalysisResult(
        report=valid_report(),
        valid=True,
        model_name="fake-model",
        tokens_prompt=100,
        tokens_completion=200,
        duration_ms=1234,
    )


@pytest.fixture(autouse=True)
def _clean_state():
    """按标记清理本文件造的数据（简历文件名前缀 rt- + 匿名 aid），真实数据不受影响。"""
    yield
    # cookie 罐里可能有多个同名 anonymous_id（用例会手工伪造），逐个收集后统一清理
    aids = [c.value for c in client.cookies.jar if c.name == "anonymous_id"]
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM analyses WHERE resume_id IN "
                "(SELECT id FROM resumes WHERE filename LIKE 'rt-%')"
            )
        )
        conn.execute(
            text("DELETE FROM usage_logs WHERE anonymous_id = ANY(:a)"), {"a": aids}
        )
        conn.execute(text("DELETE FROM resumes WHERE filename LIKE 'rt-%'"))
    for f in UPLOAD_DIR.iterdir():
        if f.is_file():
            f.unlink()


def _upload_ok() -> int:
    resp = client.post(
        "/api/resumes",
        files={"file": ("rt-analysis.pdf", make_text_pdf(), "application/pdf")},
    )
    assert resp.status_code == 201
    return resp.json()["resume"]["id"]


def _seed_usage(anonymous_id: str, n: int) -> None:
    with engine.begin() as conn:
        for _ in range(n):
            conn.execute(
                text(
                    "INSERT INTO usage_logs (anonymous_id, action_type) "
                    "VALUES (:aid, 'analysis')"
                ),
                {"aid": anonymous_id},
            )


# ---------- 路由层（mock analyze_resume） ----------


def test_analyze_success_and_usage_logged(monkeypatch) -> None:
    resume_id = _upload_ok()
    monkeypatch.setattr(
        "app.api.analyses.analyze_resume", lambda *a, **k: fake_analysis_result()
    )
    resp = client.post(f"/api/resumes/{resume_id}/analyze")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is False
    out = body["analysis"]
    assert out["report"] == valid_report()
    assert out["model_name"] == "fake-model"
    assert out["tokens_prompt"] == 100 and out["tokens_completion"] == 200

    with engine.begin() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM analyses WHERE resume_id IN "
                    "(SELECT id FROM resumes WHERE filename LIKE 'rt-%')"
                )
            ).scalar()
            == 1
        )
        row = conn.execute(
            text(
                "SELECT tokens_total, action_type FROM usage_logs "
                "WHERE action_type = 'analysis'"
            )
        ).fetchone()
    assert row.action_type == "analysis" and row.tokens_total == 300


def test_analyze_cached_second_call(monkeypatch) -> None:
    resume_id = _upload_ok()
    calls = []

    def fake(*a, **k):
        calls.append(1)
        return fake_analysis_result()

    monkeypatch.setattr("app.api.analyses.analyze_resume", fake)

    first = client.post(f"/api/resumes/{resume_id}/analyze")
    second = client.post(f"/api/resumes/{resume_id}/analyze")
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert second.json()["analysis"]["id"] == first.json()["analysis"]["id"]
    assert len(calls) == 1  # 命中去重，AI 只被调了一次
    with engine.begin() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM analyses WHERE resume_id IN "
                    "(SELECT id FROM resumes WHERE filename LIKE 'rt-%')"
                )
            ).scalar()
            == 1
        )


def test_analyze_400_when_not_parsed() -> None:
    resp = client.post("/api/resumes/999999/analyze")
    assert resp.status_code == 404


def test_analyze_502_and_tombstone_on_ai_error(monkeypatch) -> None:
    resume_id = _upload_ok()

    def fake(*a, **k):
        raise AIError(
            "AI 返回的格式不符合要求，已自动重试仍失败，请稍后重试", raw_output="oops"
        )

    monkeypatch.setattr("app.api.analyses.analyze_resume", fake)
    resp = client.post(f"/api/resumes/{resume_id}/analyze")
    assert resp.status_code == 502
    assert "重试" in resp.json()["message"]
    with engine.begin() as conn:  # 失败也留痕（valid_json=false）+ 记账
        aids = [c.value for c in client.cookies.jar if c.name == "anonymous_id"]
        assert conn.execute(text("SELECT valid_json FROM analyses")).scalar() is False
        assert (
            conn.execute(
                text("SELECT count(*) FROM usage_logs WHERE anonymous_id = ANY(:a)"),
                {"a": aids},
            ).scalar()
            == 1
        )


def test_daily_limit_429(monkeypatch) -> None:
    anon = "test-anon-429"
    # 先固定身份再上传：简历归属 = 该匿名身份，后续 analyze 才能命中限额检查
    # （P0 修复后匿名归属校验要求 resume.anonymous_id 与请求 cookie 一致）
    client.cookies.set("anonymous_id", anon)
    resume_id = _upload_ok()
    monkeypatch.setattr(settings, "daily_analysis_limit", 2)
    _seed_usage(anon, 2)
    resp = client.post(f"/api/resumes/{resume_id}/analyze")
    assert resp.status_code == 429
    assert "次日 0 点" in resp.json()["message"]


def test_get_latest_analysis(monkeypatch) -> None:
    resume_id = _upload_ok()
    assert client.get(f"/api/resumes/{resume_id}/analysis").status_code == 404

    monkeypatch.setattr(
        "app.api.analyses.analyze_resume", lambda *a, **k: fake_analysis_result()
    )
    client.post(f"/api/resumes/{resume_id}/analyze")
    resp = client.get(f"/api/resumes/{resume_id}/analysis")
    assert resp.status_code == 200
    assert resp.json()["report"] == valid_report()


# ---------- 封装层（假 OpenAI 客户端，测校验与重试） ----------


class FakeResp:
    def __init__(self, content: str) -> None:
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]
        self.usage = SimpleNamespace(prompt_tokens=10, completion_tokens=20)


def _patch_openai(monkeypatch, responses: list):
    """responses 依次返回；越界时重复最后一个。元素为 FakeResp 或异常实例。

    返回 (fake_client, state)，state["calls"] 为实际发起的调用次数。
    """
    state = {"calls": 0}

    def create(**kwargs):
        i = min(state["calls"], len(responses) - 1)
        state["calls"] += 1
        item = responses[i]
        if isinstance(item, Exception):
            raise item
        return item

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr("app.services.ai_client.OpenAI", lambda **kw: fake_client)
    return fake_client, state


def _use_fake_ai_settings(monkeypatch) -> None:
    """给 settings 注入假 AI 配置：CI 环境没有 .env，ai_client 的前置检查会直接拒绝。"""
    monkeypatch.setattr(settings, "ai_api_key", "test-key")
    monkeypatch.setattr(settings, "ai_base_url", "http://testserver")
    monkeypatch.setattr(settings, "ai_model", "fake-model")


def test_ai_client_retries_invalid_json_then_succeeds(monkeypatch) -> None:
    _use_fake_ai_settings(monkeypatch)
    from app.services.ai_client import analyze_resume as real_analyze

    bad = FakeResp("抱歉，我无法输出 JSON")
    good = FakeResp(
        "```json\n" + json.dumps(valid_report()) + "\n```"
    )  # 带围栏也要能解析
    _, state = _patch_openai(monkeypatch, [bad, good])
    result = real_analyze("简历文本", settings)
    assert result.valid is True
    assert result.report == valid_report()
    assert result.tokens_prompt == 10 and result.duration_ms >= 0
    assert state["calls"] == 2  # 首次失败 + 重试一次成功


def test_ai_client_gives_up_after_three_attempts(monkeypatch) -> None:
    _use_fake_ai_settings(monkeypatch)
    from app.services.ai_client import analyze_resume as real_analyze

    _, state = _patch_openai(monkeypatch, [FakeResp("not json at all")])
    with pytest.raises(AIError):
        real_analyze("简历文本", settings)
    assert state["calls"] == 3  # 首次 + 2 次重试，耗尽后报 AIError


def test_ai_client_network_error(monkeypatch) -> None:
    _use_fake_ai_settings(monkeypatch)
    from app.services.ai_client import analyze_resume as real_analyze

    _patch_openai(monkeypatch, [RuntimeError("connection refused")])
    with pytest.raises(AIError) as exc_info:
        real_analyze("简历文本", settings)
    assert "暂时不可用" in exc_info.value.message
