#!/usr/bin/env bash
# 一键演示环境准备（v3.8·8.2）
#
# 干什么：把「演示能跑起来」需要的步骤按顺序做完 —— 起容器 → 等健康 → 迁库 → 自检 → 造演示数据。
# 不动什么：不删你的数据。seed_demo 只在演示账号名下造数（--reset 才会先清演示账号的数据）。
# 风险：低。唯一会写盘的是 seed_demo（新增演示账号与一份演示 PDF），可反复执行。
#
# 用法（在项目根目录）：
#   bash scripts/demo-up.sh              # 准备环境（含演示数据）
#   bash scripts/demo-up.sh --no-seed    # 只准备环境，不造演示数据
#
# 跑完后脚本会打印启动前后端的命令 —— 前后端是长期进程，故意不在这里后台拉起，
# 免得脚本退出后进程被回收、你还要再排查一次「为什么服务没起来」。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CONTAINERS=(ai-interview-db ai-interview-redis ai-interview-ollama)
PY="backend/.venv/Scripts/python"
SKIP_SEED=0
[[ "${1:-}" == "--no-seed" ]] && SKIP_SEED=1

step() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$1"; exit 1; }

step "1/5 检查 Docker"
if ! docker info >/dev/null 2>&1; then
  warn "Docker daemon 未运行，尝试启动 Docker Desktop…"
  # 本机实测：cmd start 不生效，必须直接执行 exe
  DOCKER_EXE="/c/Users/ZhuanZ/AppData/Local/Programs/DockerDesktop/Docker Desktop.exe"
  [[ -f "$DOCKER_EXE" ]] || die "找不到 Docker Desktop：$DOCKER_EXE（请手动启动后重跑）"
  MSYS_NO_PATHCONV=1 "$DOCKER_EXE" >/dev/null 2>&1 &
  for i in $(seq 1 30); do
    sleep 5
    docker info >/dev/null 2>&1 && { ok "Docker 就绪（第 $((i * 5)) 秒）"; break; }
    [[ $i -eq 30 ]] && die "等待 150 秒仍未就绪，请手动启动 Docker Desktop 后重跑"
  done
else
  ok "Docker daemon 已在运行"
fi

step "2/5 启动三容器并等待健康"
# 注意：不用 `docker compose up -d` —— 容器已存在时会撞名失败；start 对已运行的容器是空操作
docker start "${CONTAINERS[@]}" >/dev/null 2>&1 || true
healthy=0
for i in $(seq 1 30); do
  sleep 4
  healthy=$(docker ps --filter "name=ai-interview-" --format '{{.Status}}' | grep -c healthy || true)
  [[ "$healthy" -ge 3 ]] && { ok "db / redis / ollama 均 healthy（第 $((i * 4)) 秒）"; break; }
done
[[ "$healthy" -ge 3 ]] || die "容器未全部健康，先跑 docker ps -a --filter name=ai-interview- 看状态"

step "3/5 数据库迁移"
[[ -x "$PY" ]] || die "找不到虚拟环境：$PY（先按 README 建好 backend/.venv）"
(cd backend && ./.venv/Scripts/python -m alembic upgrade head) || die "迁移失败"
ok "已迁移到 head"

step "4/5 环境自检"
(cd backend && ./.venv/Scripts/python -m scripts.check_env --skip-ai) || warn "自检有失败项（见上方明细，多数是前后端还没起）"

step "5/5 演示数据"
if [[ "$SKIP_SEED" -eq 1 ]]; then
  warn "已按 --no-seed 跳过"
else
  (cd backend && ./.venv/Scripts/python -m scripts.seed_demo) || die "造演示数据失败"
fi

cat <<'EOF'

准备完成。现在开两个终端分别启动：

  后端：cd backend && .venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
  前端：cd frontend && npm run dev

然后浏览器打开 http://localhost:5173 ，用演示账号登录（口令见上方输出）。
EOF
