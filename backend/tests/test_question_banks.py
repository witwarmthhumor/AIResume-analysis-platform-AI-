"""v4.2 B3 面试题库测试（离线 stub，不烧额度）：生成/归属隔离/列表/删除。"""

import uuid

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF
from sqlalchemy import text

from app.db.session import engine
from app.main import app
from app.services import question_bank_service
from app.services.ai_client import AnalysisResult

client = TestClient(app)

_QB_PREFIX = "qb-"


@pytest.fixture(autouse=True)
def _stub_gen_llm(monkeypatch):
    """题库生成的 LLM 替身：按 validator 分发（只挂 question_bank_service 命名空间）。"""

    def _fake_chat_json(system_prompt, user_prompt, settings, validator):
        assert system_prompt and "简历" in system_prompt
        return AnalysisResult(
            report={
                "questions": [
                    {"question": f"stub-q-{i}", "category": "项目", "difficulty": 3}
                    for i in range(1, 13)
                ]
            },
            valid=True,
            model_name="stub-model",
            tokens_prompt=200,
            tokens_completion=100,
            duration_ms=10,
        )

    monkeypatch.setattr(question_bank_service, "chat_json", _fake_chat_json)


@pytest.fixture(autouse=True)
def _clean_qb_data():
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM usage_logs WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'qb-%')"
            )
        )
        conn.execute(
            text(
                "DELETE FROM question_banks WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'qb-%')"
            )
        )
        conn.execute(text("DELETE FROM resumes WHERE filename LIKE 'qb-%'"))
        conn.execute(text("DELETE FROM users WHERE email LIKE 'qb-%'"))


def _pdf() -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 6, "Stub resume: question bank candidate.")
    return bytes(pdf.output())


def _register_and_upload() -> tuple[TestClient, int]:
    c = TestClient(app)
    email = f"{_QB_PREFIX}{uuid.uuid4().hex[:10]}@example.com"
    assert (
        c.post(
            "/api/auth/register", json={"email": email, "password": "correct-horse-123"}
        ).status_code
        == 201
    )
    up = c.post(
        "/api/resumes", files={"file": ("qb-resume.pdf", _pdf(), "application/pdf")}
    )
    assert up.status_code == 201, up.text
    return c, up.json()["resume"]["id"]


def test_generate_bank_from_own_resume() -> None:
    c, resume_id = _register_and_upload()
    resp = c.post("/api/question-banks", json={"resume_id": resume_id})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["question_count"] == 12
    assert len(body["questions"]) == 12
    assert body["title"].endswith("-面试题库")
    assert body["tokens"] == 300

    # 列表可见；usage 记账存在
    assert len(c.get("/api/question-banks").json()) == 1
    with engine.begin() as conn:
        usage = conn.execute(
            text(
                "SELECT COALESCE(SUM(tokens_total), 0) FROM usage_logs WHERE "
                "action_type = 'question_bank' AND user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'qb-%')"
            )
        ).scalar()
    assert usage == 300


def test_bank_owner_isolation() -> None:
    """归属隔离：B 看不到也删不掉 A 的题库（404 统一语义）。"""
    c_a, resume_id = _register_and_upload()
    bank_id = c_a.post("/api/question-banks", json={"resume_id": resume_id}).json()[
        "id"
    ]

    c_b = TestClient(app)
    assert (
        c_b.post(
            "/api/auth/register",
            json={
                "email": f"qb-{uuid.uuid4().hex[:10]}@example.com",
                "password": "correct-horse-123",
            },
        ).status_code
        == 201
    )
    assert c_b.get(f"/api/question-banks/{bank_id}").status_code == 404
    assert c_b.delete(f"/api/question-banks/{bank_id}").status_code == 404
    assert c_b.get("/api/question-banks").json() == []

    # 本人正常访问与删除
    assert c_a.get(f"/api/question-banks/{bank_id}").status_code == 200
    assert c_a.delete(f"/api/question-banks/{bank_id}").status_code == 204
    assert c_a.get(f"/api/question-banks/{bank_id}").status_code == 404


def test_generate_requires_parsed_own_resume() -> None:
    """他人简历 / 未解析成功的简历 → 404/400 语义（ValueError 统一转 404 防枚举）。"""
    c_a, resume_id = _register_and_upload()
    c_b = TestClient(app)
    c_b.post(
        "/api/auth/register",
        json={
            "email": f"qb-{uuid.uuid4().hex[:10]}@example.com",
            "password": "correct-horse-123",
        },
    )
    # B 拿 A 的简历生成 → 404
    assert (
        c_b.post("/api/question-banks", json={"resume_id": resume_id}).status_code
        == 404
    )
    # 不存在的简历 → 404
    assert (
        c_a.post("/api/question-banks", json={"resume_id": 999999}).status_code == 404
    )
