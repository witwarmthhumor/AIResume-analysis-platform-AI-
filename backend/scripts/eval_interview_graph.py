"""面试图编排黄金场景评测（S1，方案 §11.2：先建评测集再改图）。

离线确定性：图内 LLM（出题/评分/报告）全部换确定性替身，不烧额度；
跑真实 DB + 真实 PostgresSaver（checkpoint 语义是真实验证对象，不 stub）。

用法（backend/ 目录下，需 db 容器在跑）：
    ./.venv/Scripts/python -m scripts.eval_interview_graph            # 跑评测写报告
    ./.venv/Scripts/python -m scripts.eval_interview_graph --skip-ai  # 同左（本评测本就离线，兼容写法）
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

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

CASES_PATH = Path("../data/interview_eval/graph.json")
REPORT_PATH = Path("../data/interview_eval/report.md")
PASS_RATE_THRESHOLD = 1.0  # 确定性场景必须全过
_EMAIL_PREFIX = "eval-ig-"


class _StubLLM:
    """确定性替身：按 validator 分发，问题带序号便于断言续跑顺序。"""

    def __init__(self):
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
            return self._result({"question": f"stub-question-{self.n}"})
        if owner is interview_graph.graph.AnswerScore:
            return self._result({"score": 7, "depth_signal": "medium", "comment": "ok"})
        if owner is InterviewReport:
            return self._result(
                {
                    "technical_depth": 7,
                    "communication": 8,
                    "project_authenticity": 6,
                    "overall": 7,
                    "summary": "stub 报告",
                    "highlights": ["h"],
                    "improvements": ["i"],
                }
            )
        raise AssertionError(f"未预期的 validator: {validator}")


def _register_and_upload() -> tuple[TestClient, int]:
    c = TestClient(app)
    email = f"{_EMAIL_PREFIX}{uuid.uuid4().hex[:10]}@example.com"
    assert (
        c.post(
            "/api/auth/register", json={"email": email, "password": "correct-horse-123"}
        ).status_code
        == 201
    )
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 6, "Stub resume for graph eval.")
    up = c.post(
        "/api/resumes",
        files={"file": ("ig-eval.pdf", bytes(pdf.output()), "application/pdf")},
    )
    assert up.status_code == 201, up.text
    return c, up.json()["resume"]["id"]


def _run_scenario(case: dict, stub: _StubLLM) -> tuple[bool, list[str]]:
    """按 actions 驱动场景，返回 (是否通过, 失败明细)。任何异常都折算成失败明细，
    不让单个场景炸掉整个评测。"""
    failures: list[str] = []
    old_max = settings.max_interview_turns
    if case.get("max_turns"):
        settings.max_interview_turns = case["max_turns"]
    try:
        return _drive(case, failures)
    except AssertionError as exc:
        failures.append(f"驱动断言失败：{exc}")
        return False, failures
    finally:
        settings.max_interview_turns = old_max
        _cleanup()


def _drive(case: dict, failures: list[str]) -> tuple[bool, list[str]]:
    c, resume_id = _register_and_upload()
    session = None
    for action in case["actions"]:
        if action == "start":
            resp = c.post(f"/api/resumes/{resume_id}/interviews", json={})
            assert resp.status_code == 201, resp.text
            session = resp.json()["session"]
        elif action == "answer":
            resp = c.post(
                f"/api/interviews/{session['id']}/messages",
                json={"content": "stub answer"},
            )
            assert resp.status_code == 200, resp.text
        elif action == "finish":
            resp = c.post(f"/api/interviews/{session['id']}/finish")
            assert resp.status_code == 200, resp.text
            session = resp.json()
    detail = c.get(f"/api/interviews/{session['id']}").json()

    expect = case["expect"]
    if "final_status" in expect and detail["status"] != expect["final_status"]:
        failures.append(f"status={detail['status']} 期望 {expect['final_status']}")
    if "turn_count" in expect and detail["turn_count"] != expect["turn_count"]:
        failures.append(
            f"turn_count={detail['turn_count']} 期望 {expect['turn_count']}"
        )
    # trace / resume_count 不在 SessionOut 投影里，直接读库
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT resume_count, COALESCE(trace_json, '{\"nodes\": []}'::jsonb) "
                "FROM interview_sessions WHERE id = :i"
            ),
            {"i": session["id"]},
        ).fetchone()
    db_resume_count, trace = row[0], row[1]
    if "report_present" in expect:
        has = detail.get("final_report") is not None
        if has != expect["report_present"]:
            failures.append(f"报告存在性={has} 期望 {expect['report_present']}")
    if "opening_count" in expect:
        n = sum(1 for m in detail["messages"] if m["content"] == OPENING_MESSAGE)
        if n != expect["opening_count"]:
            failures.append(f"开场白条数={n} 期望 {expect['opening_count']}")
    if "trace_contains" in expect:
        nodes = [n["node"] for n in trace.get("nodes", [])]
        for node in expect["trace_contains"]:
            if node not in nodes:
                failures.append(f"trace 缺少节点 {node}（实际 {nodes}）")
    if "usage_tokens_min" in expect:
        with engine.begin() as conn:
            total = conn.execute(
                text(
                    "SELECT COALESCE(SUM(tokens_total), 0) FROM usage_logs WHERE user_id = "
                    "(SELECT id FROM users WHERE email LIKE :p)"
                ),
                {"p": _EMAIL_PREFIX + "%"},
            ).scalar()
        if total < expect["usage_tokens_min"]:
            failures.append(f"usage tokens={total} 期望 ≥{expect['usage_tokens_min']}")
    if "checkpoint_present" in expect:
        has = interview_graph.has_checkpoint(session["id"])
        if has != expect["checkpoint_present"]:
            failures.append(f"checkpoint 存在性={has}")
    if "resume_count" in expect and db_resume_count != expect["resume_count"]:
        failures.append(f"resume_count={db_resume_count} 期望 {expect['resume_count']}")
    return not failures, failures


def _cleanup() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM usage_logs WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE :p)"
            ),
            {"p": _EMAIL_PREFIX + "%"},
        )
        conn.execute(
            text(
                "DELETE FROM interview_messages WHERE session_id IN "
                "(SELECT id FROM interview_sessions WHERE resume_id IN "
                "(SELECT id FROM resumes WHERE filename LIKE 'ig-eval%'))"
            )
        )
        conn.execute(
            text(
                "DELETE FROM interview_sessions WHERE resume_id IN "
                "(SELECT id FROM resumes WHERE filename LIKE 'ig-eval%')"
            )
        )
        conn.execute(text("DELETE FROM resumes WHERE filename LIKE 'ig-eval%'"))
        conn.execute(
            text("DELETE FROM users WHERE email LIKE :p"), {"p": _EMAIL_PREFIX + "%"}
        )


def run(
    cases: list[dict], stub: _StubLLM
) -> tuple[int, list[tuple[dict, bool, list[str]]]]:
    results = []
    passed = 0
    for case in cases:
        ok, failures = _run_scenario(case, stub)
        passed += 1 if ok else 0
        results.append((case, ok, failures))
    return passed, results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-ai", action="store_true", help="兼容写法：本评测本就离线"
    )
    parser.parse_args()

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    stub = _StubLLM()
    real_chat_json = interview_graph.graph.chat_json
    interview_graph.graph.chat_json = stub
    try:
        passed, results = run(cases, stub)
    finally:
        interview_graph.graph.chat_json = real_chat_json

    rate = passed / len(cases) if cases else 0.0
    lines = [
        "# 面试图编排黄金场景评测报告",
        "",
        f"- 时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- 结果：**{passed}/{len(cases)} 通过（{rate * 100:.1f}%）**，门槛 ≥{PASS_RATE_THRESHOLD * 100:.0f}%",
        "- 说明：LLM 全部替身化（离线确定性）；DB 与 PostgresSaver 为真实组件",
        "",
        "| 场景 | 结果 | 失败明细 |",
        "|---|---|---|",
    ]
    for case, ok, failures in results:
        lines.append(
            f"| {case['id']}（{case['title']}） | {'✅' if ok else '❌'} | {'; '.join(failures) or '-'} |"
        )
    lines.append("")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n通过 {passed}/{len(cases)}；报告已写入 {REPORT_PATH}")
    return 0 if rate >= PASS_RATE_THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())
