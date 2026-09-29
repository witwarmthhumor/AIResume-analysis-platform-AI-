"""S1 面试图编排测试（离线 stub，不烧额度）。

覆盖：开局（开场白+第一问落库、checkpoint 挂起）、作答续跑（评分节点+下一问+
resume_count）、结束出报告、usage 记账、以及"每轮重建图对象从 checkpoint 恢复"
的崩溃续跑语义。图内 LLM 全部 stub（按 validator 类型分发替身）。
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF
from sqlalchemy import text

from app.core.config import settings
from app.db.session import engine
from app.main import app
from app.schemas.interview import InterviewReport
from app.services import interview_graph
from app.services.ai_client import AnalysisResult
from app.services.interview_prompts import OPENING_MESSAGE

client = TestClient(app)

_graph_stub_calls: list = []

_IG_PREFIX = "ig-"  # 一文件一前缀：本文件造的账号/简历全部带此标记


class _StubLLM:
    """按 validator 类型分发的替身模型：NextQuestion→问题 / AnswerScore→评分 /
    InterviewReport→报告。记录全部调用供断言。"""

    def __init__(self):
        self.calls: list[str] = []
        self.n = 0

    def _result(self, report: dict) -> AnalysisResult:
        return AnalysisResult(
            report=report,
            valid=True,
            model_name="stub-model",
            tokens_prompt=100,
            tokens_completion=50,
            duration_ms=10,
        )

    def __call__(self, system_prompt, user_prompt, settings, validator):
        owner = getattr(validator, "__self__", None)
        if owner is interview_graph.graph.NextQuestion:
            self.n += 1
            self.calls.append("question")
            return self._result({"question": f"stub-question-{self.n}"})
        if owner is interview_graph.graph.AnswerScore:
            self.calls.append("score")
            return self._result(
                {"score": 7, "depth_signal": "medium", "comment": "回答尚可"}
            )
        if owner is InterviewReport:
            self.calls.append("report")
            return self._result(
                {
                    "technical_depth": 7,
                    "communication": 8,
                    "project_authenticity": 6,
                    "overall": 7,
                    "summary": "stub 报告",
                    "highlights": ["h1"],
                    "improvements": ["i1"],
                }
            )
        raise AssertionError(f"未预期的 validator: {validator}")


@pytest.fixture(autouse=True)
def _graph_mode(monkeypatch):
    """图开关恒开（本文件专门测图路径），LLM 换替身。"""
    monkeypatch.setattr(settings, "interview_graph_enabled", True)
    stub = _StubLLM()
    monkeypatch.setattr(interview_graph.graph, "chat_json", stub)
    yield stub


@pytest.fixture(autouse=True)
def _clean_ig_data():
    """前后双清 ig- 标记数据（含 usage id 快照兜底）。"""
    with engine.begin() as conn:
        snap = conn.execute(
            text("SELECT COALESCE(MAX(id), 0) FROM usage_logs")
        ).scalar()
        conn.execute(text("DELETE FROM users WHERE email LIKE 'ig-%'"))
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM usage_logs WHERE id > :s OR user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'ig-%')"
            ),
            {"s": snap},
        )
        conn.execute(
            text(
                "DELETE FROM interview_messages WHERE session_id IN "
                "(SELECT id FROM interview_sessions WHERE resume_id IN "
                "(SELECT id FROM resumes WHERE filename LIKE 'ig-%'))"
            )
        )
        conn.execute(
            text(
                "DELETE FROM interview_sessions WHERE resume_id IN "
                "(SELECT id FROM resumes WHERE filename LIKE 'ig-%')"
            )
        )
        conn.execute(text("DELETE FROM resumes WHERE filename LIKE 'ig-%'"))
        conn.execute(text("DELETE FROM users WHERE email LIKE 'ig-%'"))


def _pdf() -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 6, "Stub resume: graph interview candidate.")
    return bytes(pdf.output())


def _register_and_upload() -> tuple[TestClient, int]:
    c = TestClient(app)
    email = f"ig-{uuid.uuid4().hex[:10]}@example.com"
    assert (
        c.post(
            "/api/auth/register", json={"email": email, "password": "correct-horse-123"}
        ).status_code
        == 201
    )
    up = c.post(
        "/api/resumes",
        files={"file": ("ig-resume.pdf", _pdf(), "application/pdf")},
    )
    assert up.status_code == 201, up.text
    return c, up.json()["resume"]["id"]


def _parse_sse(raw: str) -> dict[str, dict]:
    """SSE 文本 → {event: 最后一个 data payload}（本测试只关心终态事件）。"""
    out: dict[str, dict] = {}
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        event = None
        data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if event and data:
            out[event] = json.loads(data)
    return out


def test_start_asks_first_question_and_pauses() -> None:
    """开局：开场白+第一问两条面试官消息，turn_count=1，图挂起在 wait_answer。"""
    c, resume_id = _register_and_upload()
    resp = c.post(f"/api/resumes/{resume_id}/interviews", json={})
    assert resp.status_code == 201
    session = resp.json()["session"]
    assert [m["role"] for m in session["messages"]] == ["interviewer", "interviewer"]
    assert session["messages"][0]["content"] == OPENING_MESSAGE
    assert session["messages"][1]["content"] == "stub-question-1"
    assert session["turn_count"] == 1
    assert interview_graph.has_checkpoint(session["id"])


def test_answer_resumes_from_checkpoint_and_scores() -> None:
    """作答：从 checkpoint 续跑 → 评分节点入 trace → 下一问；resume_count=1。

    每次 API 请求都重建图对象——本用例同时是"进程重启后从 checkpoint 恢复"的回归：
    续跑不依赖任何内存态，状态全部来自 PostgresSaver。
    """
    c, resume_id = _register_and_upload()
    session = c.post(f"/api/resumes/{resume_id}/interviews", json={}).json()["session"]

    resp = c.post(
        f"/api/interviews/{session['id']}/messages",
        json={"content": "我有三年 Django 经验。"},
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert events["done"]["content"] == "stub-question-2"
    assert events["done"]["turn"] == 2

    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT turn_count, stage, resume_count, trace_json FROM interview_sessions "
                "WHERE id = :i"
            ),
            {"i": session["id"]},
        ).fetchone()
    assert row[0] == 2  # 两问都已落库
    assert row[2] == 1  # 续跑一次
    nodes = [n["node"] for n in row[3]["nodes"]]
    assert nodes == ["intro", "ask_question", "score_answer", "ask_question"]
    # 记账：开局 + 本轮（评分+出题）都有 usage 行
    with engine.begin() as conn2:
        total = conn2.execute(
            text(
                "SELECT COALESCE(SUM(tokens_total), 0) FROM usage_logs WHERE user_id = "
                "(SELECT id FROM users WHERE email LIKE 'ig-%' ORDER BY id DESC LIMIT 1)"
            )
        ).scalar()
    assert total >= 450  # 开局 150 + 本轮(评分150+出题150)


def test_finish_generates_report_via_graph() -> None:
    """结束：finish 走图 → 报告节点 → status=finished，报告结构与既有口径一致。"""
    c, resume_id = _register_and_upload()
    session = c.post(f"/api/resumes/{resume_id}/interviews", json={}).json()["session"]
    c.post(
        f"/api/interviews/{session['id']}/messages", json={"content": "自我介绍内容。"}
    )

    resp = c.post(f"/api/interviews/{session['id']}/finish")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "finished"
    assert body["final_report"]["summary"] == "stub 报告"
    assert body["final_report"]["overall"] == 7


def test_bank_driven_interview_skips_question_llm() -> None:
    """v4.2 题库驱动：开局带 bank_id → 出题按题库顺序（零 LLM 出题调用）；
    题库问完后回落 AI 出题；游标随 checkpoint 前进（续跑不重复同一题）。"""
    c, resume_id = _register_and_upload()
    # 直接入库造题库（题库生成本身有专属测试文件覆盖）
    with engine.begin() as conn:
        uid = conn.execute(
            text("SELECT id FROM users WHERE email LIKE 'ig-%' ORDER BY id DESC LIMIT 1")
        ).scalar()
        bank_id = conn.execute(
            text(
                "INSERT INTO question_banks (user_id, resume_id, title, question_count, "
                "questions_json) VALUES (:uid, :rid, 'ig-题库', 2, "
                "'{\"questions\": [{\"question\": \"bank-q-1\", \"category\": \"项目\", "
                "\"difficulty\": 3}, {\"question\": \"bank-q-2\", \"category\": \"基础\", "
                "\"difficulty\": 2}]}'::jsonb) RETURNING id"
            ),
            {"uid": uid, "rid": resume_id},
        ).scalar_one()

    start = c.post(f"/api/resumes/{resume_id}/interviews", json={"bank_id": bank_id})
    assert start.status_code == 201, start.text
    session = start.json()["session"]
    questions = [m["content"] for m in session["messages"] if m["role"] == "interviewer"]
    assert questions[-1] == "bank-q-1"  # 第一题来自题库（开场白之后）

    # 作答：评分仍走 LLM（stub 记 score），第二题来自题库
    resp = c.post(
        f"/api/interviews/{session['id']}/messages", json={"content": "我的回答。"}
    )
    events = _parse_sse(resp.text)
    assert events["done"]["content"] == "bank-q-2"

    # 题库耗尽 → 回落 AI 出题（stub 记到 question 调用，序号 1）
    resp2 = c.post(
        f"/api/interviews/{session['id']}/messages", json={"content": "继续。"}
    )
    events2 = _parse_sse(resp2.text)
    assert events2["done"]["content"] == "stub-question-1"

    # 全程出题零 LLM（stub.calls 里 score 有、question 在回落前没有）
    stub_calls = list(_graph_stub_calls)
    assert "score" in stub_calls
    assert stub_calls.index("question") > stub_calls.index("score")  # 回落后的首次出题


@pytest.fixture(autouse=True)
def _capture_stub_calls(_graph_mode):
    """把 _StubLLM 的调用序列暴露给用例断言（fixture 链：先拿到 _graph_mode 的 stub）。"""
    global _graph_stub_calls
    _graph_stub_calls = _graph_mode.calls
    yield
