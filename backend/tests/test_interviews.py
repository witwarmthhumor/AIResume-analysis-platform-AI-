"""阶段3 接口测试：面试会话（全部 mock AI，不调真实大模型）。

覆盖：建会话（开场白落库）、SSE 流式回答（meta/delta/done 事件与落库）、
刷新恢复（GET 会话）、轮次上限、结束评价报告、usage 记账与限流。
前置：db 容器运行中；用后按标记清理本文件造的数据和 uploads/（v3.7 测试隔离改造）。
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
    """按标记清理本文件造的数据（简历文件名前缀 rt-），真实数据不受影响。"""
    _uploads_before = {f.name for f in UPLOAD_DIR.iterdir() if f.is_file()}
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM interview_messages WHERE session_id IN "
                "(SELECT id FROM interview_sessions WHERE resume_id IN "
                "(SELECT id FROM resumes WHERE filename LIKE 'rt-%'))"
            )
        )
        conn.execute(
            text(
                "DELETE FROM interview_sessions WHERE resume_id IN "
                "(SELECT id FROM resumes WHERE filename LIKE 'rt-%')"
            )
        )
        conn.execute(
            text(
                "DELETE FROM analyses WHERE resume_id IN "
                "(SELECT id FROM resumes WHERE filename LIKE 'rt-%')"
            )
        )
        conn.execute(
            text(
                "DELETE FROM usage_logs WHERE anonymous_id = ANY(:a) "
                "OR user_id IN (SELECT id FROM users WHERE email LIKE 'test-interview-%')"
            ),
            {"a": _anon_aids()},
        )
        conn.execute(text("DELETE FROM resumes WHERE filename LIKE 'rt-%'"))
    for f in UPLOAD_DIR.iterdir():
        if f.is_file() and f.name not in _uploads_before:
            f.unlink()


def _anon_aids() -> list[str]:
    """收集本文件共享 client 的匿名身份，用于清理 usage_logs。"""
    return [c.value for c in client.cookies.jar if c.name == "anonymous_id"]


def _upload_ok() -> int:
    resp = client.post(
        "/api/resumes",
        files={"file": ("rt-interview.pdf", make_text_pdf(), "application/pdf")},
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


def test_reenter_reuses_in_progress_session() -> None:
    resume_id = _upload_ok()
    first = client.post(f"/api/resumes/{resume_id}/interviews")
    second = client.post(f"/api/resumes/{resume_id}/interviews")
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["session"]["id"] == second.json()["session"]["id"]
    assert len(second.json()["session"]["messages"]) == 1


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

    with (
        engine.begin() as conn
    ):  # 每条 AI 回复记账（按本归属者过滤，语料/他人数据不干扰）
        aids = [c.value for c in client.cookies.jar if c.name == "anonymous_id"]
        row = conn.execute(
            text(
                "SELECT action_type, tokens_total FROM usage_logs "
                "WHERE (anonymous_id = ANY(:a) "
                "OR user_id IN (SELECT id FROM users WHERE email LIKE 'test-interview-%')) "
                "AND action_type = 'interview_message' ORDER BY id DESC"
            ),
            {"a": aids},
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
    assert "最大轮次" in resp2.json()["message"]


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
        aids = [c.value for c in client.cookies.jar if c.name == "anonymous_id"]
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM usage_logs WHERE (anonymous_id = ANY(:a) "
                    "OR user_id IN (SELECT id FROM users WHERE email LIKE 'test-interview-%')) "
                    "AND action_type = 'interview_message'"
                ),
                {"a": aids},
            ).scalar()
            == 2
        )  # 1 条消息 + 1 次报告（上传的 parse 记账不算在内）


def test_finish_requires_at_least_one_answer() -> None:
    session = _start_session()
    resp = client.post(f"/api/interviews/{session['id']}/finish")
    assert resp.status_code == 400


def test_daily_message_limit(monkeypatch) -> None:
    # 先固定身份再建会话：会话归属 = 该匿名身份，P0 修复后归属不一致会被 404 拦下
    anon = "test-anon-msg"
    client.cookies.set("anonymous_id", anon)
    session = _start_session()
    monkeypatch.setattr(settings, "daily_interview_message_limit", 0)
    resp = client.post(
        f"/api/interviews/{session['id']}/messages", json={"content": "回答"}
    )
    assert resp.status_code == 429


def test_start_interview_with_position_type() -> None:
    resume_id = _upload_ok()
    resp = client.post(
        f"/api/resumes/{resume_id}/interviews", json={"position_type": "intern"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["session"]["position_type"] == "intern"
    # 老会话复用不带 position_type 也应正常返回原值
    resp2 = client.post(f"/api/resumes/{resume_id}/interviews", json={})
    assert resp2.status_code == 201
    assert resp2.json()["session"]["id"] == body["session"]["id"]
    assert resp2.json()["session"]["position_type"] == "intern"


def test_interview_prompt_contains_position_type(monkeypatch) -> None:
    from app.services.interview_prompts import build_interviewer_system_prompt

    for pt, keyword in [
        ("intern", "实习生"),
        ("fresh", "应届"),
        ("senior", "高级"),
        (None, "通用"),
    ]:
        text = build_interviewer_system_prompt("简历", "technical", 2, 10, pt)
        assert keyword in text


def test_logged_in_message_writes_user_id_usage(monkeypatch) -> None:
    """P1 回归：登录用户的面试消息/结束评价记账必须带 user_id（使用日志按它过滤）。"""
    from fastapi.testclient import TestClient as TC

    user_client = TC(app)
    import uuid as _uuid

    addr = f"test-interview-{_uuid.uuid4().hex[:10]}@example.com"
    user_client.post(
        "/api/auth/register", json={"email": addr, "password": "correct-horse-123"}
    )
    try:
        _mock_stream(monkeypatch)

        # 结束评价的 LLM 调用也要替换（CI 无 .env，真 chat_json 会因未配置 502）
        class _FakeReportResult:
            report = fake_report()
            model_name = "fake-model"
            tokens_prompt = 60
            tokens_completion = 28

        monkeypatch.setattr(
            "app.api.interviews.chat_json", lambda *a, **k: _FakeReportResult()
        )
        # 上传与建会话走登录态
        upload = user_client.post(
            "/api/resumes",
            files={"file": ("rt-interview.pdf", make_text_pdf(), "application/pdf")},
        )
        assert upload.status_code == 201
        resume_id = upload.json()["resume"]["id"]
        session = user_client.post(f"/api/resumes/{resume_id}/interviews").json()[
            "session"
        ]

        msg = user_client.post(
            f"/api/interviews/{session['id']}/messages", json={"content": "我的回答"}
        )
        assert msg.status_code == 200

        finish = user_client.post(f"/api/interviews/{session['id']}/finish")
        assert finish.status_code == 200

        # usage_logs 里该用户的两条 interview_message 都带 user_id
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT action_type, user_id FROM usage_logs "
                    "WHERE action_type = 'interview_message' "
                    "AND user_id = (SELECT id FROM users WHERE email = :e)"
                ),
                {"e": addr},
            ).fetchall()
        assert len(rows) == 2  # 一条消息 + 一条结束评价
        assert all(r[1] is not None for r in rows)
    finally:
        user_client.post("/api/auth/logout")


def test_anonymous_session_isolation() -> None:
    """P0 越权回归：匿名用户 A 的面试会话，匿名用户 B 不可读、不可发消息、不可结束。

    历史缺陷：_get_session 只校验 user_id，漏了已写入的 anonymous_id，
    任意匿名访客可凭自增 session_id 操作他人会话（get/send/finish 全中）。
    """
    client_a = TestClient(app)
    up = client_a.post(
        "/api/resumes",
        files={"file": ("rt-interview-iso.pdf", make_text_pdf(), "application/pdf")},
    )
    assert up.status_code == 201
    sid = client_a.post(f"/api/resumes/{up.json()['resume']['id']}/interviews").json()[
        "session"
    ]["id"]

    client_b = TestClient(app)  # 全新匿名身份
    assert client_b.get(f"/api/interviews/{sid}").status_code == 404
    assert (
        client_b.post(
            f"/api/interviews/{sid}/messages", json={"content": "x"}
        ).status_code
        == 404
    )
    assert client_b.post(f"/api/interviews/{sid}/finish").status_code == 404

    # A 自己仍可正常读取（确认修复没有误伤）
    assert client_a.get(f"/api/interviews/{sid}").status_code == 200
