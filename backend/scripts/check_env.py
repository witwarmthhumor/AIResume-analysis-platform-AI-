"""环境自检脚本（v3.6 收口 US-007）：一条命令看清所有启动前置条件。

项目历史上多次因容器未启动而误判为代码问题（AGENTS.md「已知」清单），
本脚本把排查动作收敛为一条命令：逐项检查、失败项直接给可复制的修复命令。

检查项（对应 tasks/prd-release-consolidation.md US-007）：
  ① Docker daemon ② ai-interview-db/redis/ollama 三容器健康
  ③ 数据库可达 + Alembic 迁移为 head ④ Redis 可达
  ⑤ 后端 8000 / 前端 5173 端口占用情况 ⑥ /health 与 /health/ready 探针
  ⑦ 预置语料 kb_chunks 条数 ⑧ AI 通道（最小请求，--skip-ai 可跳过）

用法（backend/ 目录下）：
    python -m scripts.check_env             # 全量检查（AI 项消耗一次 1 token 级请求）
    python -m scripts.check_env --skip-ai   # 跳过 AI 通道，避免无意消耗额度

输出风格与 scripts/eval_rag.py 一致：分节标题 + 逐项一行结论 + 末尾汇总，
失败项用 [FAIL] 前缀（不用 emoji）。退出码：全部通过 0，存在失败 1。
"""

import argparse
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from redis import Redis
from sqlalchemy import text

from app.core.config import settings
from app.db.session import engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# 检查计划：(所属分节, 检查函数名)。用函数名而非函数对象，方便测试 monkeypatch 替换单项。
CHECK_PLAN: list[tuple[str, str]] = [
    ("Docker 与容器", "check_docker"),
    ("Docker 与容器", "check_containers"),
    ("数据库与迁移", "check_database"),
    ("Redis", "check_redis"),
    ("端口与探针", "check_port_backend"),
    ("端口与探针", "check_port_frontend"),
    ("端口与探针", "check_health_endpoints"),
    ("语料", "check_kb_corpus"),
    ("AI 通道", "check_ai_channel"),
]

CONTAINERS = ("ai-interview-db", "ai-interview-redis", "ai-interview-ollama")
START_CONTAINERS_CMD = f"docker start {' '.join(CONTAINERS)}"
START_BACKEND_CMD = (
    "cd backend && .venv\\Scripts\\python -m uvicorn app.main:app --reload --port 8000"
)
START_FRONTEND_CMD = "cd frontend && npm run dev"

OK, FAIL, SKIP = "OK", "FAIL", "SKIP"


@dataclass
class CheckResult:
    """单项检查结果：分节 + 名称 + 状态 + 一行结论 + 失败时的修复命令。"""

    section: str
    name: str
    status: str  # OK / FAIL / SKIP
    detail: str = ""
    fix: str | None = None


def _run_docker(args: list[str], timeout: int = 20) -> str:
    """跑 docker CLI 子命令并返回 stdout；非零退出/超时统一抛 RuntimeError。"""
    proc = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,  # 非零退出由下面自己判（错误信息要进 FAIL 结论，不能直接抛 CalledProcessError）
    )
    if proc.returncode != 0:
        # 只取前 200 字符：CLI 报错可能很长，结论行里放不下
        raise RuntimeError((proc.stderr or proc.stdout).strip()[:200])
    return proc.stdout


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """TCP 探测端口是否可连接（被监听即视为可连接）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_get(url: str, timeout: float = 3.0) -> tuple[int, str]:
    """GET 一个 URL，返回 (状态码, 响应体前 120 字符)；连接类错误抛 OSError。"""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read(200).decode("utf-8", errors="replace")[:120]


def check_docker() -> CheckResult:
    """① Docker daemon 是否可用。"""
    try:
        version = _run_docker(["info", "--format", "{{.ServerVersion}}"])
    except Exception:  # noqa: BLE001  检查器必须吞掉一切异常转为 FAIL 结论
        return CheckResult(
            "Docker 与容器",
            "Docker daemon",
            FAIL,
            "不可用（Docker Desktop 未启动？）",
            fix="启动 Docker Desktop 后重试",
        )
    return CheckResult(
        "Docker 与容器", "Docker daemon", OK, f"可用（Server {version.strip()}）"
    )


def check_containers() -> CheckResult:
    """② 三容器是否都在 healthy。任一不在就整体 FAIL，修复命令一次性列出三个。"""
    try:
        bad: list[str] = []
        for name in CONTAINERS:
            state = _run_docker(
                ["inspect", "--format", "{{.State.Health.Status}}", name]
            ).strip()
            if state != "healthy":
                bad.append(f"{name}（{state or '不存在'}）")
    except Exception:  # noqa: BLE001  检查器必须吞掉一切异常转为 FAIL 结论
        return CheckResult(
            "Docker 与容器",
            "三容器健康（db/redis/ollama）",
            FAIL,
            "状态未知（Docker daemon 不可用）",
            fix="启动 Docker Desktop 后重试",
        )
    if bad:
        return CheckResult(
            "Docker 与容器",
            "三容器健康（db/redis/ollama）",
            FAIL,
            f"未达 healthy：{'、'.join(bad)}",
            fix=START_CONTAINERS_CMD,
        )
    return CheckResult(
        "Docker 与容器", "三容器健康（db/redis/ollama）", OK, "db/redis/ollama 均 healthy"
    )


def check_database() -> CheckResult:
    """③ 数据库可达 + Alembic 迁移版本是否为 head。"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            current = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
    except Exception:  # noqa: BLE001  检查器必须吞掉一切异常转为 FAIL 结论
        return CheckResult(
            "数据库与迁移",
            "数据库可达且迁移为 head",
            FAIL,
            f"连接失败（{settings.database_url.split('@')[-1]}，不含凭据）",
            fix="docker start ai-interview-db",
        )
    head = ScriptDirectory.from_config(
        AlembicConfig(str(BACKEND_ROOT / "alembic.ini"))
    ).get_current_head()
    if current != head:
        return CheckResult(
            "数据库与迁移",
            "数据库可达且迁移为 head",
            FAIL,
            f"迁移落后：库 {current} ≠ head {head}",
            fix="cd backend && .venv\\Scripts\\python -m alembic upgrade head",
        )
    return CheckResult(
        "数据库与迁移", "数据库可达且迁移为 head", OK, f"已连接，迁移 {head}"
    )


def check_redis() -> CheckResult:
    """④ Redis 可达（Celery 与限流依赖）。"""
    try:
        Redis.from_url(settings.redis_url, socket_connect_timeout=2).ping()
    except Exception:  # noqa: BLE001  检查器必须吞掉一切异常转为 FAIL 结论
        return CheckResult(
            "Redis", "Redis 可达", FAIL, f"{settings.redis_url.split('@')[-1]} 连不上",
            fix="docker start ai-interview-redis",
        )
    return CheckResult("Redis", "Redis 可达", OK, "PING 通")


def check_port_backend() -> CheckResult:
    """⑤ 后端端口 8000：未监听判 FAIL（启动前自检最常见的问题）。"""
    if not _port_open("localhost", 8000):
        return CheckResult(
            "端口与探针", "后端端口 8000", FAIL, "没有进程在监听", fix=START_BACKEND_CMD
        )
    return CheckResult("端口与探针", "后端端口 8000", OK, "已监听（健康状态见探针项）")


def check_port_frontend() -> CheckResult:
    """⑥ 前端端口 5173：未监听判 FAIL。Vite 可能只绑 IPv6 ::1，用 localhost 探测。"""
    if not _port_open("localhost", 5173):
        return CheckResult(
            "端口与探针", "前端端口 5173", FAIL, "没有进程在监听", fix=START_FRONTEND_CMD
        )
    return CheckResult("端口与探针", "前端端口 5173", OK, "已监听")


def check_health_endpoints() -> CheckResult:
    """⑦ /health 与 /health/ready 探针。后端未监听时报告但不崩溃。"""
    if not _port_open("localhost", 8000):
        return CheckResult(
            "端口与探针", "/health 与 /health/ready", FAIL, "后端未监听，探针不可用",
            fix=START_BACKEND_CMD,
        )
    try:
        status, _ = _http_get("http://localhost:8000/health")
        ready, ready_body = _http_get("http://localhost:8000/health/ready")
    except (OSError, urllib.error.URLError) as exc:
        # 端口开着但 /health 不通：多半 8000 被其他程序占用（历史坑：曾让项目改去 8001）
        return CheckResult(
            "端口与探针",
            "/health 与 /health/ready",
            FAIL,
            f"8000 已监听但探针失败（{exc.__class__.__name__}）——可能被其他程序占用",
            fix="netstat -ano | findstr :8000   # 确认占用进程",
        )
    if status == 200 and ready == 200:
        return CheckResult(
            "端口与探针", "/health 与 /health/ready", OK, "两个探针均 200"
        )
    return CheckResult(
        "端口与探针",
        "/health 与 /health/ready",
        FAIL,
        f"/health={status}，/health/ready={ready}（{ready_body}）",
        fix="检查数据库/Redis 状态后重启后端",
    )


def check_kb_corpus() -> CheckResult:
    """⑧ 预置语料条数：0 块说明语料被清（老测试跑全量 pytest 的已知副作用）。"""
    try:
        with engine.connect() as conn:
            count = conn.execute(text("SELECT count(*) FROM kb_chunks")).scalar_one()
    except Exception:  # noqa: BLE001  检查器必须吞掉一切异常转为 FAIL 结论
        return CheckResult(
            "语料", "kb_chunks 条数", FAIL, "查询失败（表不存在或数据库不可达）",
            fix="cd backend && .venv\\Scripts\\python -m alembic upgrade head",
        )
    if count == 0:
        return CheckResult(
            "语料", "kb_chunks 条数", FAIL, "知识库为空（RAG/客服将无从作答）",
            fix="cd backend && .venv\\Scripts\\python -m scripts.seed_kb_preset",
        )
    return CheckResult("语料", "kb_chunks 条数", OK, f"{count} 块")


def check_ai_channel() -> CheckResult:
    """⑨ AI 通道可用性：用 max_tokens=1 的最小请求探测（消耗可忽略）。

    不直接复用 settings：拷贝一份把 max_tokens 压到 1、超时压到 15s，避免探测拖满 60s。
    """
    from app.services.agent.llm_factory import build_chat_llm

    try:
        probe_settings = settings.model_copy(
            update={"ai_max_tokens": 1, "ai_timeout_seconds": 15, "temperature": 0}
        )
        llm = build_chat_llm(probe_settings)
        llm.invoke("回复：ok")
    except ValueError:
        return CheckResult(
            "AI 通道", "AI 通道可用", FAIL, "AI_BASE_URL / AI_API_KEY 未配置",
            fix="在 backend/.env 填 AI_BASE_URL、AI_MODEL、AI_API_KEY",
        )
    except Exception as exc:  # noqa: BLE001  检查器必须吞掉一切异常转为 FAIL 结论
        return CheckResult(
            "AI 通道",
            "AI 通道可用",
            FAIL,
            f"调用失败（{exc.__class__.__name__}）",
            fix="检查 backend/.env 的 AI_* 配置、网络与额度",
        )
    return CheckResult(
        "AI 通道", "AI 通道可用", OK, f"{settings.ai_model} 探测通过（1 token 级消耗）"
    )


def run_checks(skip_ai: bool = False, plan: list[tuple[str, str]] | None = None):
    """按计划逐项执行检查。任一项崩溃只记 FAIL，不中断后续检查。"""
    results = []
    for section, fname in plan or CHECK_PLAN:
        if skip_ai and fname == "check_ai_channel":
            results.append(
                CheckResult(section, "AI 通道可用", SKIP, "已按 --skip-ai 跳过，不消耗额度")
            )
            continue
        fn = globals()[fname]
        try:
            results.append(fn())
        except Exception:  # 兜底：单项崩溃也要给出统一 FAIL，保证检查能跑完（带堆栈记日志）
            import logging

            logging.getLogger(__name__).warning(
                "check %s crashed", fname, exc_info=True
            )
            results.append(
                CheckResult(section, fname, FAIL, "检查器自身异常", fix="查看脚本报错输出")
            )
    return results


def summarize(results) -> tuple[int, int, int]:
    """统计 (通过, 失败, 跳过) 三元组，退出码只看失败数。"""
    passed = sum(1 for r in results if r.status == OK)
    failed = sum(1 for r in results if r.status == FAIL)
    skipped = sum(1 for r in results if r.status == SKIP)
    return passed, failed, skipped


def print_report(results) -> None:
    """分节打印逐项结论与末尾汇总；[FAIL] 项下一行缩进输出可复制的修复命令。"""
    print("== 环境自检（python -m scripts.check_env）==")
    last_section = None
    for r in results:
        if r.section != last_section:
            print(f"\n-- {r.section} --")
            last_section = r.section
        print(f"  [{r.status}] {r.name}：{r.detail}")
        if r.status == FAIL and r.fix:
            print(f"        → 修复: {r.fix}")
    passed, failed, skipped = summarize(results)
    tail = f" / 跳过 {skipped} 项" if skipped else ""
    print(f"\n== 汇总：通过 {passed} 项 / 失败 {failed} 项{tail} ==")


def main() -> None:
    parser = argparse.ArgumentParser(description="项目环境自检（启动前跑一次）")
    parser.add_argument(
        "--skip-ai", action="store_true", help="跳过 AI 通道检查，避免消耗额度"
    )
    args = parser.parse_args()
    results = run_checks(skip_ai=args.skip_ai)
    print_report(results)
    _, failed, _ = summarize(results)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
