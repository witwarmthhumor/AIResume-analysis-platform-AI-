"""v3.6 环境自检脚本测试：汇总统计、退出码、修复命令输出、单项崩溃隔离。

全部用替身结果/自定义 plan，不碰真实 Docker/数据库/AI——
真实环境的验证由人工在收口时跑一次脚本完成。
"""

import pytest

from scripts import check_env
from scripts.check_env import CheckResult, main, print_report, run_checks, summarize


def _r(status: str, fix: str | None = None) -> CheckResult:
    """造一条指定状态的替身结果，FAIL 可带修复命令。"""
    return CheckResult("测试节", f"测试项-{status}", status, "细节", fix=fix)


def test_summarize_counts_pass_fail_skip():
    """汇总三元组分别数 OK/FAIL/SKIP，互不串账。"""
    results = [_r("OK"), _r("OK"), _r("FAIL"), _r("SKIP")]
    assert summarize(results) == (2, 1, 1)
    assert summarize([]) == (0, 0, 0)


def test_main_exit_0_when_all_pass(monkeypatch):
    """全通过 → 退出码 0（可被 CI/外层脚本判定）。"""
    monkeypatch.setattr(check_env, "run_checks", lambda skip_ai=False: [_r("OK")])
    monkeypatch.setattr("sys.argv", ["check_env"])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 0


def test_main_exit_1_when_any_fail(monkeypatch):
    """存在失败 → 退出码 1。"""
    monkeypatch.setattr(
        check_env, "run_checks", lambda skip_ai=False: [_r("OK"), _r("FAIL")]
    )
    monkeypatch.setattr("sys.argv", ["check_env"])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1


def test_fail_item_prints_fix_command(capsys):
    """[FAIL] 行后必须带「修复:」+ 可复制命令，且不打印 emoji。"""
    print_report([_r("FAIL", fix="docker start ai-interview-db")])
    out = capsys.readouterr().out
    assert "[FAIL] 测试项-FAIL" in out
    assert "修复: docker start ai-interview-db" in out
    assert "✅" not in out and "❌" not in out


def test_run_checks_isolates_single_crash(monkeypatch):
    """单项检查自身崩溃 → 记 FAIL 继续跑后续，绝不向上抛异常。"""

    def boom() -> CheckResult:
        raise RuntimeError("替身爆炸")

    def fine() -> CheckResult:
        return _r("OK")

    monkeypatch.setattr(check_env, "check_boom", boom, raising=False)
    monkeypatch.setattr(check_env, "check_fine", fine, raising=False)
    results = run_checks(plan=[("T", "check_boom"), ("T", "check_fine")])
    assert [r.status for r in results] == [check_env.FAIL, check_env.OK]
    assert results[0].detail == "检查器自身异常"


def test_skip_ai_does_not_invoke_ai_check(monkeypatch):
    """--skip-ai 时 AI 项直接给 SKIP，检查函数一次都不执行（不消耗额度）。"""

    def must_not_run() -> CheckResult:
        raise AssertionError("skip_ai=True 时不应调用 check_ai_channel")

    monkeypatch.setattr(check_env, "check_ai_channel", must_not_run)
    results = run_checks(skip_ai=True, plan=[("AI 通道", "check_ai_channel")])
    assert len(results) == 1
    assert results[0].status == check_env.SKIP


def test_ai_check_unconfigured_gives_fail_with_env_fix(monkeypatch):
    """AI 未配置（build_chat_llm 抛 ValueError）→ FAIL 且修复命令指向 backend/.env。"""
    from app.services.agent import llm_factory

    def fake_build(_settings):
        raise ValueError("AI 服务未配置")

    monkeypatch.setattr(llm_factory, "build_chat_llm", fake_build)
    # check_ai_channel 内部是「延迟导入 build_chat_llm」，所以要 patch 导入源
    monkeypatch.setattr(
        "app.services.agent.llm_factory.build_chat_llm", fake_build, raising=True
    )
    result = check_env.check_ai_channel()
    assert result.status == check_env.FAIL
    assert result.fix and "backend/.env" in result.fix
