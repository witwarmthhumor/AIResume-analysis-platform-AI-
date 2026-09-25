"""Agent v2 端到端黄金任务评测（PRD FR-12）：图结构/节点逻辑改动后的回归门禁。

与另外两份评测的分工：
- eval_rag 量**检索**质量；eval_agent_routing 量**工具路由**质量；
  本脚本量**图编排**质量（条件路由/重试回环/HITL 门禁/降级/限额/越权是否按预期流转）。

执行方式：10 个任务全部**离线确定性**跑——能力层（run_job_match / run_question_generation /
analyze_resume）打桩，LLM 不参与任务执行，因此结果可复现、不烧额度、可进 CI；
「检索不可用降级」一题例外地走真实 run_question_generation（只桩它内部的 kb_retrieve
与 run_tool_llm），保证降级逻辑本身是被测对象而不是被桩掉。
测试数据一律 e2e- 前缀（用户邮箱 / 简历文件名），finally 全量清理，不污染预置语料。

AI 通道探针沿用评测脚本约定（同 eval_agent_routing）：通道不可用时**退码 2 且不写报告**，
避免把"0 条结果"的空报告当成评测产物；--skip-ai 可跳过探针（离线环境用）。

用法（backend/ 目录下）：
    ./.venv/Scripts/python.exe -m scripts.eval_agent_e2e            # 全量 10 任务
    ./.venv/Scripts/python.exe -m scripts.eval_agent_e2e --skip-ai  # 跳过 AI 预检
退出码：0 全过 / 1 有任务失败 / 2 预检失败。
"""

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

# 评测脚本以 -m scripts.xxx 运行，需把 backend/ 加进 sys.path 才能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy import text

import app.services.agent_v2.graph as graph_module
from app.core.config import settings
from app.db.session import engine
from app.main import app
from app.services import agent_capabilities
from app.services.agent_capabilities import run_question_generation

CASES_PATH = (
    Path(__file__).resolve().parents[1].parent / "data" / "agent_eval" / "e2e.json"
)
REPORT_PATH = CASES_PATH.parent / "e2e_report.md"
# 评测门槛（PRD M4）：≥90%，10 任务即至少 9 过
PASS_RATE_THRESHOLD = 0.9
WAIT_TIMEOUT_S = 15.0

# ---------- 能力层桩（与 tests/test_agent_v2.py 同一套 canned 产物） ----------

FAKE_MATCH = {
    "match_score": 80,
    "matched_keywords": ["Python"],
    "missing_keywords": ["K8s"],
    "suggestions": ["补容器经验"],
}
FAKE_QUESTIONS = {
    "questions": ["讲讲你的项目", "Python GIL 是什么", "如何设计一个缓存"],
    "level": "通用",
    "kb_backed": False,
    "sources": [],
}


class _FakeAnalysis:
    """analyze_resume 返回形状的最小替身（图只消费这些属性）。"""

    report: ClassVar[dict] = {"target_position": "后端"}
    valid = True
    model_name = "e2e-stub"
    tokens_prompt = 10
    tokens_completion = 20
    duration_ms = 1


def _default_stubs() -> dict:
    """图节点引用的三个能力 → 固定产物（每次返回新 dict，防用例间串改）。"""
    return {
        "run_job_match": lambda *a, **k: ("匹配文本", dict(FAKE_MATCH)),
        "run_question_generation": lambda *a, **k: (
            "出题文本",
            dict(FAKE_QUESTIONS),
        ),
        "analyze_resume": lambda *a, **k: _FakeAnalysis(),
    }


def _install_stubs(overrides: dict | None = None) -> list[tuple[str, object]]:
    """把桩打到 graph 模块命名空间，返回 (名字, 原对象) 便于 finally 恢复。"""
    stubs = _default_stubs()
    stubs.update(overrides or {})
    saved = []
    for name, fn in stubs.items():
        saved.append((name, getattr(graph_module, name)))
        setattr(graph_module, name, fn)
    return saved


def _restore_stubs(saved: list[tuple[str, object]]) -> None:
    for name, original in saved:
        setattr(graph_module, name, original)


# ---------- 造数 / 轮询 / SSE 解析（与 tests/test_agent_v2.py 同套路） ----------


def _register() -> tuple[TestClient, int]:
    """注册 e2e- 前缀登录用户，返回 (带 cookie 的客户端, user_id)。"""
    c = TestClient(app)
    email = f"e2e-{uuid.uuid4().hex[:10]}@example.com"
    resp = c.post(
        "/api/auth/register", json={"email": email, "password": "correct-horse-123"}
    )
    assert resp.status_code == 201, resp.text
    with engine.begin() as conn:
        uid = conn.execute(
            text("SELECT id FROM users WHERE email = :e"), {"e": email}
        ).scalar()
    return c, uid


def _create_resume(uid: int, *, with_analysis: bool = False) -> int:
    """插一份 e2e- 简历；with_analysis 控制是否预置有效分析报告。"""
    marker = f"e2e-{uuid.uuid4().hex[:10]}"
    with engine.begin() as conn:
        resume_id = conn.execute(
            text(
                "INSERT INTO resumes (user_id, filename, file_hash, storage_path, "
                "raw_text, page_count, file_size, parse_status) "
                "VALUES (:u, :fn, :h, :p, :rt, 1, 100, 'success') RETURNING id"
            ),
            {
                "u": uid,
                "fn": f"{marker}.pdf",
                "h": uuid.uuid4().hex,
                "p": "uploads/e2e.pdf",
                "rt": "E2E 评测简历正文。",
            },
        ).scalar()
        if with_analysis:
            conn.execute(
                text(
                    "INSERT INTO analyses (resume_id, model_name, prompt_version, "
                    "result_json, valid_json, tokens_prompt, tokens_completion, "
                    "duration_ms) VALUES (:r, 'e2e-stub', 'v1', :j, true, 1, 1, 1)"
                ),
                {
                    "r": resume_id,
                    "j": json.dumps({"target_position": "预置"}, ensure_ascii=False),
                },
            )
    return resume_id


def _run_id_of(uid: int) -> int:
    """该用户最新一条 run（评测进程独占本人数据，无需更细作用域）。"""
    with engine.begin() as conn:
        return conn.execute(
            text(
                "SELECT id FROM agent_runs WHERE user_id = :u ORDER BY id DESC LIMIT 1"
            ),
            {"u": uid},
        ).scalar()


def _wait_status(run_id: int, target: str) -> str:
    status = None
    for _ in range(int(WAIT_TIMEOUT_S / 0.2)):
        with engine.begin() as conn:
            status = conn.execute(
                text("SELECT status FROM agent_runs WHERE id = :i"), {"i": run_id}
            ).scalar()
        if status == target:
            return status
        time.sleep(0.2)
    return status or "(timeout)"


def _drain_events(resp, until: tuple[str, ...] = ()) -> list[tuple[str, dict]]:
    """POST /runs 的 SSE 响应体按 event/data 解析；命中 until 即停。"""
    events: list[tuple[str, dict]] = []
    last = None
    for line in resp.text.split("\n"):
        if line.startswith("event: "):
            last = line[len("event: ") :]
        elif line.startswith("data: ") and last:
            events.append((last, json.loads(line[len("data: ") :])))
    return events


def _event_types(events) -> list[str]:
    return [etype for etype, _ in events]


# ---------- 10 个黄金任务（每个返回 (是否通过, 说明)） ----------


def task_normal_full_flow() -> tuple[bool, str]:
    """正常链路：全事件流走到批准，输出三件套齐全。"""
    c, uid = _register()
    _create_resume(uid)
    events = _drain_events(c.post("/api/agent-v2/runs", json={"jd_text": "招后端"}))
    types = _event_types(events)
    if "plan" not in types or "approval_required" not in types:
        return False, f"事件流不完整：{types}"
    run_id = _run_id_of(uid)
    c.post(f"/api/agent-v2/runs/{run_id}/approve", json={"decision": "approved"})
    if _wait_status(run_id, "completed") != "completed":
        return False, "批准后未 completed"
    output = c.get(f"/api/agent-v2/runs/{run_id}").json()["output"]
    if output["match"]["match_score"] != 80 or len(output["questions"]) != 3:
        return False, f"输出产物不符：{output['match']}, {output['questions']}"
    if not output.get("session_id"):
        return False, "批准后未创建面试场次"
    return True, "plan→…→approval_required→completed，产物齐全"


def task_approve_side_effect() -> tuple[bool, str]:
    """审批通过副作用：场次 +1 且 audit 落 approval_approved。"""
    c, uid = _register()
    _create_resume(uid)
    _drain_events(c.post("/api/agent-v2/runs", json={"jd_text": "招后端"}))
    run_id = _run_id_of(uid)
    with engine.begin() as conn:
        before = conn.execute(text("SELECT count(*) FROM interview_sessions")).scalar()
    c.post(f"/api/agent-v2/runs/{run_id}/approve", json={"decision": "approved"})
    if _wait_status(run_id, "completed") != "completed":
        return False, "批准后未 completed"
    with engine.begin() as conn:
        after = conn.execute(text("SELECT count(*) FROM interview_sessions")).scalar()
        action = conn.execute(
            text(
                "SELECT action FROM audit_logs WHERE run_id = :i AND action LIKE 'approval%'"
            ),
            {"i": run_id},
        ).scalar()
    if after != before + 1:
        return False, f"场次应 +1，实际 {before}→{after}"
    if action != "approval_approved":
        return False, f"audit 动作为 {action!r}"
    return True, "批准前 0 副作用，批准后场次 +1 + audit 留痕"


def task_reject_no_session() -> tuple[bool, str]:
    """审批拒绝：照常交付但零副作用。"""
    c, uid = _register()
    _create_resume(uid)
    _drain_events(c.post("/api/agent-v2/runs", json={"jd_text": "招后端"}))
    run_id = _run_id_of(uid)
    with engine.begin() as conn:
        before = conn.execute(text("SELECT count(*) FROM interview_sessions")).scalar()
    c.post(f"/api/agent-v2/runs/{run_id}/approve", json={"decision": "rejected"})
    if _wait_status(run_id, "completed") != "completed":
        return False, "拒绝后未 completed（拒绝不应中断交付）"
    output = c.get(f"/api/agent-v2/runs/{run_id}").json()["output"]
    with engine.begin() as conn:
        after = conn.execute(text("SELECT count(*) FROM interview_sessions")).scalar()
        approval = conn.execute(
            text(
                "SELECT status FROM agent_approvals WHERE run_id = :i "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"i": run_id},
        ).scalar()
    if output.get("session_id") is not None or after != before:
        return (
            False,
            f"拒绝产生了副作用：session={output.get('session_id')}，场次 {before}→{after}",
        )
    if approval != "rejected":
        return False, f"审批单状态 {approval!r}"
    return True, "拒绝后照常交付，场次 +0，审批单 rejected"


def task_approval_expired() -> tuple[bool, str]:
    """审批超时：TTL=0 审批单立即过期，approve 返 410，abort 收尾。"""
    c, uid = _register()
    _create_resume(uid)
    original_ttl = settings.agent_approval_ttl_minutes
    try:
        settings.agent_approval_ttl_minutes = 0
        _drain_events(c.post("/api/agent-v2/runs", json={"jd_text": "招后端"}))
    finally:
        settings.agent_approval_ttl_minutes = original_ttl
    run_id = _run_id_of(uid)
    approve_resp = c.post(
        f"/api/agent-v2/runs/{run_id}/approve", json={"decision": "approved"}
    )
    if approve_resp.status_code != 410:
        return False, f"过期审批应 410，实际 {approve_resp.status_code}"
    c.post(f"/api/agent-v2/runs/{run_id}/abort")
    if _wait_status(run_id, "aborted") != "aborted":
        return False, "abort 后未 aborted"
    with engine.begin() as conn:
        approval = conn.execute(
            text(
                "SELECT status FROM agent_approvals WHERE run_id = :i "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"i": run_id},
        ).scalar()
    if approval != "expired":
        return False, f"审批单状态 {approval!r}（应 expired）"
    return True, "TTL 到点 approve 410，abort 后 run=aborted、审批单=expired"


def task_no_resume_guidance() -> tuple[bool, str]:
    """无简历：load_resume 直接 fail，给上传引导。"""
    c, uid = _register()
    events = _drain_events(c.post("/api/agent-v2/runs", json={"jd_text": "招后端"}))
    fatal = dict(events).get("fatal")
    if not fatal or "上传" not in fatal.get("content", ""):
        return False, f"fatal 话术缺上传引导：{fatal}"
    if _wait_status(_run_id_of(uid), "failed") != "failed":
        return False, "run 未落 failed"
    return True, "无简历 fail 且话术引导上传"


def task_bad_output_retry() -> tuple[bool, str]:
    """坏输出回环：matcher 两次空产物 → 2 次 retry → 恢复到审批。"""
    c, uid = _register()
    _create_resume(uid)
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] <= 2:
            return ("匹配失败文本", None)
        return ("匹配文本", dict(FAKE_MATCH))

    saved = _install_stubs({"run_job_match": flaky})
    try:
        events = _drain_events(c.post("/api/agent-v2/runs", json={"jd_text": "招后端"}))
    finally:
        _restore_stubs(saved)
    types = _event_types(events)
    if types.count("retry") != 2 or "approval_required" not in types:
        return False, f"retry 次数 {types.count('retry')}（应 2）或未恢复：{types}"
    with engine.begin() as conn:
        retried = conn.execute(
            text(
                "SELECT count(*) FROM agent_spans WHERE run_id = :i AND status = 'retried'"
            ),
            {"i": _run_id_of(uid)},
        ).scalar()
    if retried != 2:
        return False, f"retried span 应 2，实际 {retried}"
    return True, "两次空产物触发回环，第三次恢复进审批"


def task_no_report_reanalyze() -> tuple[bool, str]:
    """无报告补分析：已解析无分析的简历触发 analyzer 补跑。"""
    c, uid = _register()
    _create_resume(uid, with_analysis=False)
    events = _drain_events(c.post("/api/agent-v2/runs", json={"jd_text": "招后端"}))
    types = _event_types(events)
    analyzer_starts = [
        payload["node"]
        for etype, payload in events
        if etype == "node_start" and payload["node"] == "analyzer"
    ]
    if not analyzer_starts:
        return False, f"未路由到 analyzer：{types}"
    if "approval_required" not in types:
        return False, f"补分析后未走到审批：{types}"
    return True, "need_analysis 命中补跑 analyzer，链路继续"


def task_kb_degraded() -> tuple[bool, str]:
    """检索不可用降级：走真实 run_question_generation，只桩其内部依赖。"""
    c, uid = _register()
    _create_resume(uid)

    def kb_down(*a, **k):
        return [], "检索服务不可用"

    def fake_tool_llm(db, user_id, anonymous_id, call):
        return SimpleNamespace(
            report={"questions": list(FAKE_QUESTIONS["questions"])}
        ), None

    saved_graph = _install_stubs(
        {"run_question_generation": run_question_generation}  # 换回真实实现
    )
    saved_caps = [
        ("kb_retrieve", agent_capabilities.kb_retrieve),
        ("run_tool_llm", agent_capabilities.run_tool_llm),
    ]
    agent_capabilities.kb_retrieve = kb_down
    agent_capabilities.run_tool_llm = fake_tool_llm
    try:
        events = _drain_events(c.post("/api/agent-v2/runs", json={"jd_text": "招后端"}))
    finally:
        _restore_stubs(saved_graph)
        for name, original in saved_caps:
            setattr(agent_capabilities, name, original)
    if "approval_required" not in _event_types(events):
        return False, f"降级后未走到审批：{_event_types(events)}"
    # 从 questioner 的 node_end 事件预览里取话术，验证降级口径
    texts = [
        payload.get("preview") or ""
        for etype, payload in events
        if etype == "node_end" and payload.get("node") == "questioner"
    ]
    if not texts:
        return False, "未收到 questioner 的 node_end 事件"
    if "平台知识库检索暂时不可用" not in texts[0]:
        return False, f"话术未声明降级：{texts[0][:120]}"
    return True, "kb 挂掉后照常出题且话术声明不来自知识库，sources 为空"


def task_daily_limit_429() -> tuple[bool, str]:
    """限额命中：agent_run_v2 记账达上限后 429，且不建 run。"""
    c, uid = _register()
    _create_resume(uid)
    with engine.begin() as conn:  # 预置一条当日记账 → 已用满
        conn.execute(
            text(
                "INSERT INTO usage_logs (user_id, action_type, tokens_total) "
                "VALUES (:u, 'agent_run_v2', 0)"
            ),
            {"u": uid},
        )
    original_limit = settings.daily_agent_v2_run_limit
    try:
        settings.daily_agent_v2_run_limit = 1
        resp = c.post("/api/agent-v2/runs", json={"jd_text": "招后端"})
    finally:
        settings.daily_agent_v2_run_limit = original_limit
    if resp.status_code != 429:
        return False, f"应 429，实际 {resp.status_code}"
    if "每日上限" not in resp.json().get("message", ""):
        return False, f"话术不符：{resp.json()}"
    with engine.begin() as conn:
        runs = conn.execute(
            text("SELECT count(*) FROM agent_runs WHERE user_id = :u"), {"u": uid}
        ).scalar()
    if runs != 0:
        return False, f"429 后不应建 run，实际 {runs} 条"
    return True, "限额命中 429 + 话术明确 + 无 run 落库"


def task_owner_isolation() -> tuple[bool, str]:
    """越权访问：B 读/审批 A 的 run 均 404。"""
    a, uid_a = _register()
    _create_resume(uid_a)
    _drain_events(a.post("/api/agent-v2/runs", json={"jd_text": "招后端"}))
    run_id = _run_id_of(uid_a)
    b, _ = _register()
    read = b.get(f"/api/agent-v2/runs/{run_id}")
    approve = b.post(
        f"/api/agent-v2/runs/{run_id}/approve", json={"decision": "approved"}
    )
    if read.status_code != 404 or approve.status_code != 404:
        return (
            False,
            f"越权读 {read.status_code} / 越权审批 {approve.status_code}（应 404/404）",
        )
    a.post(f"/api/agent-v2/runs/{run_id}/abort")  # 收尾，避免残留 waiting run
    return True, "他人 run 读与审批均 404"


# ---------- 清理 / 预检 / 主流程 ----------


def _cleanup() -> None:
    """删本脚本造成的 e2e- 数据（顺序：记账/审计 → 审批/span/run → 场次/分析/简历 → 用户）。"""
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM usage_logs WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'e2e-%')"
            )
        )
        conn.execute(
            text(
                "DELETE FROM audit_logs WHERE run_id IN (SELECT id FROM agent_runs "
                "WHERE user_id IN (SELECT id FROM users WHERE email LIKE 'e2e-%'))"
            )
        )
        conn.execute(
            text(
                "DELETE FROM agent_approvals WHERE run_id IN (SELECT id FROM agent_runs "
                "WHERE user_id IN (SELECT id FROM users WHERE email LIKE 'e2e-%'))"
            )
        )
        conn.execute(
            text(
                "DELETE FROM agent_spans WHERE run_id IN (SELECT id FROM agent_runs "
                "WHERE user_id IN (SELECT id FROM users WHERE email LIKE 'e2e-%'))"
            )
        )
        conn.execute(
            text(
                "DELETE FROM agent_runs WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'e2e-%')"
            )
        )
        conn.execute(
            text(
                "DELETE FROM interview_sessions WHERE resume_id IN "
                "(SELECT id FROM resumes WHERE filename LIKE 'e2e-%')"
            )
        )
        conn.execute(
            text(
                "DELETE FROM analyses WHERE resume_id IN "
                "(SELECT id FROM resumes WHERE filename LIKE 'e2e-%')"
            )
        )
        conn.execute(text("DELETE FROM resumes WHERE filename LIKE 'e2e-%'"))
        conn.execute(text("DELETE FROM users WHERE email LIKE 'e2e-%'"))


def _preflight(skip_ai: bool) -> tuple[bool, list[str]]:
    """预检：数据库可达 +（可选）AI 通道探测。失败信息逐条返回。"""
    problems: list[str] = []
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001  预检必须把一切异常转成失败项
        problems.append(f"数据库不可达：{type(exc).__name__}: {exc}")
    if skip_ai:
        print("[预检] AI 通道：跳过（--skip-ai）")
        return not problems, problems
    from app.services.agent.llm_factory import build_chat_llm

    try:
        probe = settings.model_copy(
            update={"ai_max_tokens": 1, "ai_timeout_seconds": 15, "temperature": 0}
        )
        build_chat_llm(probe).invoke("回复：ok")
        print("[预检] AI 通道：可用")
    except Exception as exc:  # noqa: BLE001
        problems.append(
            f"AI 通道不可用：{type(exc).__name__}（评测约定退码 2；离线环境可加 --skip-ai）"
        )
    return not problems, problems


def _write_report(results: list[tuple[str, str, bool, str]], passed: int) -> None:
    """报告：逐任务结论表 + 汇总 + 是否达门槛。"""
    total = len(results)
    rate = passed / total if total else 0.0
    lines = [
        "# Agent v2 端到端黄金任务评测报告",
        "",
        f"- 时间：{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}",
        f"- 结果：**{passed}/{total} 通过（{rate:.0%}）**，门槛 ≥{PASS_RATE_THRESHOLD:.0%}",
        f"- 结论：{'✅ 达标' if rate >= PASS_RATE_THRESHOLD else '❌ 未达门槛，禁止发布'}",
        "- 执行方式：离线确定性（能力层打桩，不烧 LLM 额度）",
        "",
        "| 任务 | 场景 | 结果 | 说明 |",
        "|---|---|---|---|",
    ]
    for task_id, name, ok, detail in results:
        lines.append(f"| {name} | `{task_id}` | {'✅' if ok else '❌'} | {detail} |")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n报告已写入 {REPORT_PATH}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent v2 端到端黄金任务评测")
    parser.add_argument("--skip-ai", action="store_true", help="跳过 AI 通道预检")
    args = parser.parse_args()

    ok, problems = _preflight(args.skip_ai)
    if not ok:
        for p in problems:
            print(f"[FAIL] 预检未过：{p}")
        print("按评测约定不写报告，退出码 2。")
        return 2

    catalog = json.loads(CASES_PATH.read_text(encoding="utf-8"))["tasks"]
    runners = {
        "normal_full_flow": task_normal_full_flow,
        "approve_side_effect": task_approve_side_effect,
        "reject_no_session": task_reject_no_session,
        "approval_expired": task_approval_expired,
        "no_resume_guidance": task_no_resume_guidance,
        "bad_output_retry": task_bad_output_retry,
        "no_report_reanalyze": task_no_report_reanalyze,
        "kb_degraded": task_kb_degraded,
        "daily_limit_429": task_daily_limit_429,
        "owner_isolation": task_owner_isolation,
    }
    missing = [t["id"] for t in catalog if t["id"] not in runners]
    if missing:
        print(f"[FAIL] 用例目录声明了没有执行器的任务：{missing}")
        return 2

    results: list[tuple[str, str, bool, str]] = []
    # 默认桩整轮生效（所有任务确定性执行）；个别任务再临时叠加 override
    saved = _install_stubs()
    try:
        for task in catalog:
            task_id = task["id"]
            print(f"[跑] {task['name']}（{task_id}）...", end="", flush=True)
            try:
                passed, detail = runners[task_id]()
            except Exception as exc:  # noqa: BLE001  单任务异常算失败，不拖垮整轮
                passed, detail = False, f"执行异常：{type(exc).__name__}: {exc}"
            print(" 通过" if passed else f" 失败（{detail}）")
            results.append((task_id, task["name"], passed, detail))
    finally:
        _restore_stubs(saved)
        _cleanup()

    passed_count = sum(1 for r in results if r[2])
    _write_report(results, passed_count)
    return 0 if passed_count / len(results) >= PASS_RATE_THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())
