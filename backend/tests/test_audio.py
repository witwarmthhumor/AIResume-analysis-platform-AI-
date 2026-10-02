"""v4.2 B5 录音分析测试（离线 stub）：上传转写/编辑/角色审核/面试审核/归属隔离。

whisper 转写与审核 LLM 全部替身；Celery delay 换成同步执行（测试进程内跑完任务）。
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import engine
from app.main import app

client = TestClient(app)

_AUD_PREFIX = "aud-"
_DELAY_FAST: dict = {
    "fast": None
}  # 记录最近一次 delay 收到的 fast 实参（快速档透传断言用）

_FAKE_TRANSCRIBE = {
    "text": "面试官：请自我介绍。候选人：我有三年 Django 经验。",
    "segments": [
        {"start": 0.0, "end": 3.0, "text": "面试官：请自我介绍。"},
        {"start": 3.5, "end": 9.0, "text": "候选人：我有三年 Django 经验。"},
    ],
    "duration_seconds": 9,
}

_FAKE_ROLE_REVIEW = {
    "segments": [
        {"speaker": "面试官", "text": "请自我介绍。", "reason": "引导发言"},
        {"speaker": "候选人", "text": "我有三年 Django 经验。", "reason": "自我陈述"},
    ],
    "summary": "一问一答结构清晰。",
}

_FAKE_INTERVIEW_REVIEW = {
    "technical_depth": 7,
    "communication": 8,
    "project_authenticity": 6,
    "overall": 7,
    "summary": "回答结构清晰，量化不足。",
    "highlights": ["表达流畅"],
    "improvements": ["补充量化结果"],
}


@pytest.fixture(autouse=True)
def _stub_audio_llm(monkeypatch):
    """whisper 转写替身 + 审核 LLM 替身 + Celery delay 同步化。"""
    from app.api import audio as audio_api
    from app.worker import tasks as worker_tasks

    _DELAY_FAST["fast"] = None
    monkeypatch.setattr(
        worker_tasks,
        "transcribe",
        lambda data, filename, model_name=None: dict(_FAKE_TRANSCRIBE),
    )

    # delay → 同步执行真任务（测试进程内完成转写，不依赖 worker/Redis 消费）
    def _sync_delay(audio_id: int, fast: bool = False):
        _DELAY_FAST["fast"] = fast
        return (
            type("R", (), {"id": f"sync-{audio_id}"})()
            if (worker_tasks.transcribe_audio(audio_id, fast) or True)
            else None
        )

    monkeypatch.setattr(
        audio_api,
        "transcribe_audio",
        type("T", (), {"delay": staticmethod(_sync_delay)}),
    )

    def _fake_role(transcript):
        assert transcript.strip()  # 文本可编辑：只断言非空，不断言具体内容
        return dict(_FAKE_ROLE_REVIEW), _result()

    def _fake_review(role_marked):
        assert "【面试官】" in role_marked  # 面试审核吃到的是角色标注后的对话体
        return dict(_FAKE_INTERVIEW_REVIEW), _result()

    monkeypatch.setattr(audio_review_service_mod(), "role_review", _fake_role)
    monkeypatch.setattr(audio_review_service_mod(), "interview_review", _fake_review)


def _result():
    from app.services.ai_client import AnalysisResult

    return AnalysisResult(
        report={},
        valid=True,
        model_name="stub",
        tokens_prompt=80,
        tokens_completion=40,
        duration_ms=5,
    )


def audio_review_service_mod():
    from app.services import audio_review_service

    return audio_review_service


@pytest.fixture(autouse=True)
def _clean_aud_data():
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM usage_logs WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'aud-%')"
            )
        )
        conn.execute(
            text(
                "DELETE FROM audio_analyses WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'aud-%')"
            )
        )
        conn.execute(text("DELETE FROM users WHERE email LIKE 'aud-%'"))


def _register() -> TestClient:
    c = TestClient(app)
    assert (
        c.post(
            "/api/auth/register",
            json={
                "email": f"{_AUD_PREFIX}{uuid.uuid4().hex[:10]}@example.com",
                "password": "correct-horse-123",
            },
        ).status_code
        == 201
    )
    return c


def _upload_wav(c: TestClient) -> dict:
    resp = c.post(
        "/api/audio/analyses",
        files={"file": ("aud-test.wav", b"RIFF fake wav bytes", "audio/wav")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_full_audio_flow() -> None:
    """上传 → 同步转写完成（transcribed）→ 编辑文本作废审核 → 角色审核 → 面试审核。"""
    c = _register()
    body = _upload_wav(c)
    audio_id = body["id"]
    # delay 同步化：上传返回时转写已完成
    detail = c.get(f"/api/audio/analyses/{audio_id}").json()
    assert detail["status"] == "transcribed"
    assert "Django" in detail["transcript"]
    assert detail["duration_seconds"] == 9

    # 编辑文本 → 审核结果作废（此时尚无审核，仅验证状态回转）
    edited = c.put(
        f"/api/audio/analyses/{audio_id}/transcript",
        json={"transcript": "面试官：请介绍项目。候选人：我做过订单系统。"},
    )
    assert edited.status_code == 200

    # 角色审核 → 面试审核
    role = c.post(f"/api/audio/analyses/{audio_id}/role-review")
    assert role.status_code == 200, role.text
    assert role.json()["role_review"]["segments"][0]["speaker"] == "面试官"

    review = c.post(f"/api/audio/analyses/{audio_id}/interview-review")
    assert review.status_code == 200, review.text
    assert review.json()["interview_review"]["overall"] == 7

    final = c.get(f"/api/audio/analyses/{audio_id}").json()
    assert final["status"] == "reviewed"

    # 历史列表 + 记账
    assert len(c.get("/api/audio/analyses").json()) == 1
    with engine.begin() as conn:
        usage = conn.execute(
            text(
                "SELECT action_type, COUNT(*) FROM usage_logs WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'aud-%') GROUP BY action_type"
            )
        ).all()
    types = {t: n for t, n in usage}
    assert types.get("audio_transcribe") == 1
    assert types.get("audio_review") == 2

    # 删除 → 404
    assert c.delete(f"/api/audio/analyses/{audio_id}").status_code == 204
    assert c.get(f"/api/audio/analyses/{audio_id}").status_code == 404


def test_audio_owner_isolation_and_validation() -> None:
    """归属隔离 404；非法扩展名 415；无文本审核 400。"""
    c_a = _register()
    body = _upload_wav(c_a)
    audio_id = body["id"]

    c_b = _register()
    assert c_b.get(f"/api/audio/analyses/{audio_id}").status_code == 404
    assert c_b.delete(f"/api/audio/analyses/{audio_id}").status_code == 404

    bad = c_a.post(
        "/api/audio/analyses",
        files={"file": ("song.txt", b"not audio", "text/plain")},
    )
    assert bad.status_code == 415

    # 上传成功但把转写文本清空后审核 → 400（stub 不会走到，这里验证守卫）
    empty = c_a.post(
        "/api/audio/analyses",
        files={"file": ("aud-empty.wav", b"RIFF x", "audio/wav")},
    )
    assert empty.status_code == 201
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE audio_analyses SET transcript = NULL WHERE id = :i"),
            {"i": empty.json()["id"]},
        )
    assert (
        c_a.post(f"/api/audio/analyses/{empty.json()['id']}/role-review").status_code
        == 400
    )


def test_interview_review_uses_edited_text() -> None:
    """G-1 回归：面试审核请求体带 text 时，送审的是用户编辑后的文本（非角色标注）。"""
    c = _register()
    body = _upload_wav(c)
    audio_id = body["id"]
    # 做一次角色审核（stub），再带编辑文本请求面试审核
    c.post(f"/api/audio/analyses/{audio_id}/role-review")
    captured = {}

    def _fake_review(role_marked):
        captured["text"] = role_marked
        return dict(_FAKE_INTERVIEW_REVIEW), _result()

    from app.services import audio_review_service

    import_tests_orig = audio_review_service.interview_review
    audio_review_service.interview_review = _fake_review
    try:
        resp = c.post(
            f"/api/audio/analyses/{audio_id}/interview-review",
            json={"text": "【面试官】 edited 问。"},
        )
    finally:
        audio_review_service.interview_review = import_tests_orig
    assert resp.status_code == 200, resp.text
    assert captured["text"] == "【面试官】 edited 问。"


# —— v4.4.1 安全件：音频文件头（magic bytes）校验 ——


def test_upload_rejects_fake_wav_content() -> None:
    """.wav 扩展名但内容不是 RIFF：415 拦下，不落记录、不进转写队列。"""
    c = _register()
    resp = c.post(
        "/api/audio/analyses",
        files={"file": ("aud-fake.wav", b"this is not a real wav", "audio/wav")},
    )
    assert resp.status_code == 415
    assert "不符" in resp.json()["message"]


def test_upload_fast_mode_reaches_task() -> None:
    """fast=true 透传到 Celery 任务（快速档用 base 模型）。"""
    c = _register()
    resp = c.post(
        "/api/audio/analyses",
        files={"file": ("aud-fast.wav", b"RIFF fake wav bytes", "audio/wav")},
        data={"fast": "true"},
    )
    assert resp.status_code == 201, resp.text
    assert _DELAY_FAST["fast"] is True


def test_upload_default_is_slow_tier() -> None:
    """不传 fast：默认走标准档（False），行为与 v4.2 完全一致。"""
    c = _register()
    _upload_wav(c)
    assert _DELAY_FAST["fast"] is False
