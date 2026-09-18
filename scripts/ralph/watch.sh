#!/bin/bash
# Ralph 进度观察器 —— 只读，不碰仓库，可随时 Ctrl+C 退出（不影响正在跑的 Ralph）
# 用法：bash scripts/ralph/watch.sh [刷新秒数，默认 10]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
INTERVAL="${1:-10}"

cd "$PROJECT_DIR"

# ⚠️ 不要把 MSYS 风格绝对路径（/e/xxx）传给 Windows 原生的 jq.exe —— 它认不出，会报
# "Could not open file ...: No such file or directory"。统一改用相对于项目根的路径。
REL="${SCRIPT_DIR#"$PROJECT_DIR"/}"
case "$REL" in
  /*|"$SCRIPT_DIR") REL="scripts/ralph" ;;   # 前缀匹配失败时的兜底
esac

while true; do
  # 不用 clear 命令：部分 Git Bash 缺 terminfo 会报 "terminals database is inaccessible"
  printf '\033[2J\033[H'
  TOTAL=$(jq '.userStories|length' "$REL/prd.json" 2>/dev/null || echo "?")
  DONE=$(jq '[.userStories[]|select(.passes==true)]|length' "$REL/prd.json" 2>/dev/null || echo "?")

  echo "== Ralph 观察窗 ================================"
  echo "时间 $(date +%H:%M:%S)   刷新 ${INTERVAL}s   (Ctrl+C 退出，不影响 Ralph)"
  echo "分支 $(git branch --show-current)"
  echo "进度 ${DONE}/${TOTAL}"
  echo
  echo "-- 已完成 --------------------------------------"
  jq -r '.userStories[]|select(.passes==true)|"  [x] \(.id)  \(.title)"' "$REL/prd.json" 2>/dev/null
  echo "-- 下一条待办 ----------------------------------"
  jq -r '[.userStories[]|select(.passes!=true)][:3][]|"  [ ] \(.id)  \(.title)"' "$REL/prd.json" 2>/dev/null
  echo
  echo "-- 最近 3 次提交 -------------------------------"
  git log --oneline -3
  echo
  echo "-- 当前工作区改动（Ralph 正在改的文件会出现在这里）--"
  git status --short | head -15
  echo
  echo "-- progress.txt 尾部 ---------------------------"
  tail -5 "$REL/progress.txt" 2>/dev/null
  echo

  sleep "$INTERVAL"
done
