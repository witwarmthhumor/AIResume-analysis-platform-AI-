"""阶段3 接口测试：面试会话（全部 mock AI，不调真实大模型）。

覆盖：建会话（开场白落库）、SSE 流式回答（meta/delta/done 事件与落库）、
刷新恢复（GET 会话）、轮次上限、结束评价报告、usage 记账与限流。
前置：db 容器运行中；每用例结束清相关表和 uploads/。
"""

import json

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


def make_text_pdf(content: str = "Fake resume: Li Si, Java, 5 years.") -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 6, content)
    return bytes(pdf.output())


def fake_report() -> dict:
    return {
        "technical_depth": 7,
        "communication": 8,
        "project_authenticity": 6,
        "overall": 7,
        "summary": "整体表现良好。",
        "highlights": ["项目描述清晰"],
        "improvements": ["补充量化数据"],
    }


def fake_analysis_result() -> AnalysisResult:
    return AnalysisResult(
        report=fake_report(),
        valid=True,
        model_name="fake-model",
        tokens_prompt=50,
        tokens_completion=80,
        duration_ms=321,
    )


@pytest.fixture(autouse=True)
def _clean_state():
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM interview_messages"))
        conn.execute(text("DELETE FROM interview_sessions"))
        conn.execute(text("DELETE FROM analyses"))
        conn.execute(text("DELETE FROM usage_logs"))
        conn.execute(text("DELETE FROM resumes"))
    for f in UPLOAD_DIR.iterdir():
        if f.is_file():
            f.unlink()


def _upload_ok() -> int:
    resp = client.post(
        "/api/resumes",
        files={"file": ("resume.pdf", make_text_pdf(), "application/pdf")},
    )
    assert resp.status_code == 201
    return resp.json()["resume"]["id"]


def _start_session() -> dict:
    resp = client.post(f"/api/resumes/{_upload_ok()}/interviews")
    assert resp.status_code == 201
    return resp.json()["session"]


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    """把 SSE 文本解析成 [(event, data), ...]。"""
    events = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        lines = block.splitlines()
        event = next(l for l in lines if l.startswith("event: "))[7:]
        data = json.loads(next(l for l in lines if l.startswith("data: "))[6:])
        events.append((event, data))
    return events


def _mock_stream(monkeypatch, text: str = "请介绍一下你最有挑战的项目。") -> dict:
    """把 stream_chat 换成假生成器，返回 usage 字典供断言。"""
    usage = {"tokens_prompt": 120, "tokens_completion": 45}

    def fake_stream(messages, s, usage_out):
        usage_out.update(usage)
        yield text[:3]
        yield text[3:]

    monkeypatch.setattr("app.api.interviews.stream_chat", fake_stream)
    return usage


def _mock_chat_json(monkeypatch) -> list:
    calls = []

    def fake_chat_json(system, user, s, validator):
        calls.append(1)
        return fake_analysis_result()

    monkeypatch.setattr("app.api.interviews.chat_json", fake_chat_json)
    return calls


def test_start_session_with_opening_message() -> None:
    session = _start_session()
    assert session["status"] == "in_progress"
    assert session["stage"] == "intro"
    assert session["turn_count"] == 0
    assert len(session["messages"]) == 1
    assert session["messages"][0]["role"] == "interviewer"
    assert "自我介绍" in session["messages"][0]["content"]


def test_message_flow_sse_and_persistence(monkeypatch) -> None:
    session = _start_session()
    sid = session["id"]
    _mock_stream(monkeypatch, "说说你项目里最有技术含量的部分。")

    resp = client.post(
        f"/api/interviews/{sid}/messages", json={"content": "你好，我是李四。"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    kinds = [e for e, _ in events]
    assert kinds == ["meta", "delta", "delta", "done"]  # 逐段推送
    meta, done = events[0][1], events[-1][1]
    assert meta["turn"] == 1 and meta["stage"] == "intro"
    assert done["content"] == "说说你项目里最有技术含量的部分。"
    assert done["tokens_completion"] == 45 and done["duration_ms"] is not None

    # 刷新恢复：GET 会话应包含候选人与面试官消息，轮次/阶段已推进
    recovered = client.get(f"/api/interviews/{sid}").json()
    assert recovered["turn_count"] == 1
    assert recovered["stage"] == "technical"  # 第 2 轮进入技术阶段
    roles = [m["role"] for m in recovered["messages"]]
    assert roles == ["interviewer", "candidate", "interviewer"]

    with engine.begin() as conn:  # 每条 AI 回复记账
        row = conn.execute(
            text("SELECT action_type, tokens_total FROM usage_logs")
        ).fetchone()
    assert row.action_type == "interview_message" and row.tokens_total == 165


def test_ai_error_in_stream_leaves_user_message(monkeypatch) -> None:
    session = _start_session()

    def fake_stream(messages, s, usage_out):
        raise AIError("AI 服务暂时不可用，请稍后重试")
        yield  # pragma: no cover  使其成为生成器

    monkeypatch.setattr("app.api.interviews.stream_chat", fake_stream)
    resp = client.post(
        f"/api/interviews/{session['id']}/messages", json={"content": "回答内容"}
    )
    events = _parse_sse(resp.text)
    assert events[-1][0] == "error" and "重试" in events[-1][1]["content"]

    recovered = client.get(f"/api/interviews/{session['id']}").json()
    assert recovered["turn_count"] == 0  # 轮次未推进，可重试
    roles = [m["role"] for m in recovered["messages"]]
    assert roles == ["interviewer", "candidate"]  # 用户回答不丢


def test_max_turns_blocks_new_messages(monkeypatch) -> None:
    session = _start_session()
    monkeypatch.setattr(settings, "max_interview_turns", 1)
    _mock_stream(monkeypatch)
    resp = client.post(
        f"/api/interviews/{session['id']}/messages", json={"content": "回答"}
    )
    assert resp.status_code == 200
    # 轮次已到上限（1），再来一条被拒
    resp2 = client.post(
        f"/api/interviews/{session['id']}/messages", json={"content": "再答"}
    )
    assert resp2.status_code == 400
    assert "最大轮次" in resp2.json()["detail"]


def test_finish_generates_report_and_closes(monkeypatch) -> None:
    session = _start_session()
    sid = session["id"]
    _mock_stream(monkeypatch)
    calls = _mock_chat_json(monkeypatch)

    client.post(f"/api/interviews/{sid}/messages", json={"content": "我的回答"})
    resp = client.post(f"/api/interviews/{sid}/finish")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "finished"
    assert body["final_report"] == fake_report()

    finished = client.get(f"/api/interviews/{sid}").json()  # 刷新后报告仍在
    assert finished["final_report"]["overall"] == 7
    # 已结束的会话：不能再发消息，也不能重复 finish
    assert (
        client.post(
            f"/api/interviews/{sid}/messages", json={"content": "x"}
        ).status_code
        == 400
    )
    assert client.post(f"/api/interviews/{sid}/finish").status_code == 400
    assert len(calls) == 1

    with engine.begin() as conn:  # 报告调用也记账
        assert (
            conn.execute(text("SELECT count(*) FROM usage_logs")).scalar() == 2
        )  # 1 条消息 + 1 次报告


def test_finish_requires_at_least_one_answer() -> None:
    session = _start_session()
    resp = client.post(f"/api/interviews/{session['id']}/finish")
    assert resp.status_code == 400


def test_daily_message_limit(monkeypatch) -> None:
    session = _start_session()
    monkeypatch.setattr(settings, "daily_interview_message_limit", 0)
    anon = "test-anon-msg"
    client.cookies.set("anonymous_id", anon)
    resp = client.post(
        f"/api/interviews/{session['id']}/messages", json={"content": "回答"}
    )
    assert resp.status_code == 429
