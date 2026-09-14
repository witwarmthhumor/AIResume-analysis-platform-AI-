"""v3.5 分析报告版本列表接口测试：报告页「版本对比」的数据源。

与 `/api/resumes/{id}/analysis`（只取当前 PROMPT_VERSION 的最新一版）不同，
本接口要返回**所有历史版本**，否则改提示词后就无从对比。

数据隔离：测试数据打固定标记（resume file_hash 前缀 ver- / 邮箱前缀 ver-），只清自己造的。
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import SessionLocal, engine
from app.main import app
from app.models.analysis import Analysis
from app.models.resume import Resume

_HASH_PREFIX = "ver-"


def _register_client() -> tuple[TestClient, int]:
    client = TestClient(app)
    addr = f"ver-{uuid.uuid4().hex[:10]}@example.com"
    client.post("/api/auth/register", json={"email": addr, "password": "correct-horse-123"})
    with SessionLocal() as db:
        uid = db.scalar(text("SELECT id FROM users WHERE email = :e"), params={"e": addr})
    return client, uid


@pytest.fixture(autouse=True)
def _clean_versions():
    def _purge() -> None:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM analyses WHERE resume_id IN "
                    "(SELECT id FROM resumes WHERE file_hash LIKE :p)"
                ),
                {"p": f"{_HASH_PREFIX}%"},
            )
            conn.execute(text("DELETE FROM resumes WHERE file_hash LIKE :p"), {"p": f"{_HASH_PREFIX}%"})
            conn.execute(text("DELETE FROM users WHERE email LIKE 'ver-%'"))

    _purge()
    yield
    _purge()


def _add_resume(user_id: int | None, *, anonymous_id: str | None = None) -> int:
    with SessionLocal() as db:
        resume = Resume(
            user_id=user_id,
            anonymous_id=anonymous_id,
            filename="版本对比测试.pdf",
            file_hash=f"{_HASH_PREFIX}{uuid.uuid4().hex[:12]}",
            storage_path="uploads/ver.pdf",
            raw_text="测试简历正文",
            page_count=1,
            file_size=100,
            parse_status="success",
        )
        db.add(resume)
        db.commit()
        return resume.id


def _add_analysis(
    resume_id: int,
    *,
    user_id: int | None,
    prompt_version: str,
    valid: bool = True,
    summary: str = "岗位匹配良好。",
) -> int:
    with SessionLocal() as db:
        analysis = Analysis(
            resume_id=resume_id,
            user_id=user_id,
            model_name="fake-model",
            prompt_version=prompt_version,
            result_json={
                "target_position": "后端工程师",
                "position_match": summary,
                "strengths": ["基础扎实"],
                "weaknesses": ["缺少量化"],
                "keyword_gaps": ["Kubernetes"],
                "suggestions": ["补充数据"],
                "predicted_questions": ["讲讲你最难的 project"],
            },
            valid_json=valid,
            tokens_prompt=100,
            tokens_completion=200,
            duration_ms=1000,
        )
        db.add(analysis)
        db.commit()
        return analysis.id


def test_empty_list_when_no_analysis() -> None:
    client, uid = _register_client()
    resume_id = _add_resume(uid)

    resp = client.get(f"/api/resumes/{resume_id}/analyses")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


def test_returns_all_prompt_versions_newest_first() -> None:
    """核心：旧 prompt_version 的报告也要返回（否则无法做版本对比）。"""
    client, uid = _register_client()
    resume_id = _add_resume(uid)
    _add_analysis(resume_id, user_id=uid, prompt_version="v1", summary="旧版结论。")
    _add_analysis(resume_id, user_id=uid, prompt_version="v2", summary="新版结论。")

    items = client.get(f"/api/resumes/{resume_id}/analyses").json()["items"]

    assert [item["prompt_version"] for item in items] == ["v2", "v1"]
    assert items[0]["report"]["position_match"] == "新版结论。"
    assert items[0]["model_name"] == "fake-model"
    assert items[0]["created_at"]


def test_excludes_invalid_json_analyses() -> None:
    client, uid = _register_client()
    resume_id = _add_resume(uid)
    _add_analysis(resume_id, user_id=uid, prompt_version="v1", valid=False)

    assert client.get(f"/api/resumes/{resume_id}/analyses").json()["items"] == []


def test_missing_resume_returns_404() -> None:
    client, _uid = _register_client()
    assert client.get("/api/resumes/99999999/analyses").status_code == 404


def test_isolates_other_users_resume() -> None:
    _other_client, other_uid = _register_client()
    other_resume = _add_resume(other_uid)
    _add_analysis(other_resume, user_id=other_uid, prompt_version="v1")

    client, _uid = _register_client()

    assert client.get(f"/api/resumes/{other_resume}/analyses").status_code == 404


def test_anonymous_cannot_read_logged_in_users_resume() -> None:
    _owner_client, owner_uid = _register_client()
    resume_id = _add_resume(owner_uid)

    anon = TestClient(app)

    assert anon.get(f"/api/resumes/{resume_id}/analyses").status_code == 404
