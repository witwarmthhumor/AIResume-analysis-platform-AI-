"""收口闸门（v4.3，P0-3b）：打 tag 前核对「文档口径 vs 代码实况」。

背景：文档口径失真已三次复发（后续开发规划.md #23/#26/#27——pytest 数、工具数、
迁移数、版本号在 README / AGENTS / PROGRESS / 规划文档里停在旧值），v4.2.1 还漏打了
tag。本脚本把「收口前 grep 一遍关键数字」变成机器闸门，跑法：

    cd backend && .venv/Scripts/python -m scripts.release_check

核对四类数字（全部以代码实况为准）：
1. pytest 用例数 —— pytest --collect-only 实测，四份文档必须出现该数字
2. 工具数       —— registry.TOOL_NAMES 实测，文档必须出现「N 个工具」类表述
3. 迁移数       —— alembic/versions/*.py 文件数实测
4. 版本号       —— 取 AGENTS.md「当前版本：**vX.Y.Z**」，README/PROGRESS 必须同步，
                   且 git tag v{版本} 必须存在

任一项不过退出码 1，输出到具体文档与修复提示；全过退出码 0。
"""

import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

# 收口必须同步的四份口径文档（历史版本记录段出现旧数字不算失真，只查新数字在不在）
DOCS = [
    "README.md",
    "AGENTS.md",
    "PROGRESS.md",
    "docs/后续开发规划.md",
]

results: list[tuple[bool, str]] = []


def report(ok: bool, msg: str) -> None:
    results.append((ok, msg))
    print(("[OK]   " if ok else "[FAIL] ") + msg)


def doc_has(path: Path, patterns: list[str]) -> bool:
    """任一模式命中即算同步（文档表述有「N 个工具」「N 个用例」等多种写法）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return any(p in text for p in patterns)


def main() -> int:
    # —— 1. pytest 用例数（实测 collect-only）——
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,  # collect 失败时由下方解析兜底报错
    )
    m = re.search(r"(\d+) tests? collected", proc.stdout)
    if m is None:
        report(
            False, "pytest 用例数：无法从 --collect-only 输出解析（先确认 pytest 可跑）"
        )
    else:
        n = m.group(1)
        for rel in DOCS:
            doc = ROOT / rel
            ok = (
                bool(re.search(rf"(?<!\d){n}(?!\d)", doc.read_text(encoding="utf-8")))
                if doc.exists()
                else False
            )
            report(
                ok,
                f"pytest 用例数 {n}：{rel}{'已同步' if ok else '未出现该数字，请更新口径'}",
            )

    # —— 2. 工具数（registry 单一数据源）——
    from app.services.agent.tools import TOOL_NAMES  # 依赖 sys.path 注入的 backend 目录

    n_tools = len(TOOL_NAMES)
    tool_patterns = [
        f"{n_tools} 个工具",
        f"{n_tools}个工具",
        f"{n_tools} 工具",
        f"{n_tools}工具",
    ]
    for rel in DOCS:
        ok = doc_has(ROOT / rel, tool_patterns)
        report(
            ok,
            f"工具数 {n_tools}：{rel}{'已同步' if ok else '未出现「N 个工具」表述，请更新口径'}",
        )

    # —— 3. 迁移数（versions 目录文件数）——
    n_mig = len(
        [
            f
            for f in (BACKEND / "alembic" / "versions").glob("*.py")
            if f.name != "__init__.py"
        ]
    )
    mig_patterns = [
        f"{n_mig} 次迁移",
        f"{n_mig}次迁移",
        f"{n_mig} 个迁移",
        f"{n_mig}个迁移",
    ]
    for rel in DOCS:
        ok = doc_has(ROOT / rel, mig_patterns)
        report(
            ok,
            f"迁移数 {n_mig}：{rel}{'已同步' if ok else '未出现「N 次迁移」表述，请更新口径'}",
        )

    # —— 4. 版本号（AGENTS.md 为权威来源）+ git tag 在位 ——
    agents_path = ROOT / "AGENTS.md"
    # AGENTS.md 是本地文件（.gitignore 有意忽略），不存在时版本号检查会明确报 FAIL
    agents_text = agents_path.read_text(encoding="utf-8") if agents_path.exists() else ""
    vm = re.search(r"当前版本：\*\*v(\d+\.\d+\.\d+)\*\*", agents_text)
    if vm is None:
        report(False, "版本号：AGENTS.md 未找到「当前版本：**vX.Y.Z**」标记")
    else:
        ver = vm.group(1)
        for rel in ("README.md", "PROGRESS.md"):
            ok = f"v{ver}" in (ROOT / rel).read_text(encoding="utf-8")
            report(
                ok,
                f"版本号 v{ver}：{rel}{'已同步' if ok else '未出现该版本号，请更新口径'}",
            )
        tag = subprocess.run(
            ["git", "tag", "--list", f"v{ver}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        report(
            bool(tag),
            f"git tag v{ver}：{'在位' if tag else '缺失——打 tag：git tag -a v' + ver + ' -m "..." && git push origin v' + ver}",
        )

    failed = [msg for ok, msg in results if not ok]
    print(
        f"\n== release_check 汇总：通过 {len(results) - len(failed)} / {len(results)} =="
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
