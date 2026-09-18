#!/bin/bash
# Ralph Wiggum - Long-running AI agent loop
# Usage: ./ralph.sh [--tool codebuddy|claude|amp] [--model <model-id>] [max_iterations]

set -e

# Parse arguments
# 本机默认引擎 = codebuddy（WorkBuddy 自带 CLI）
#   原因：amp 未安装；claude CLI 通道被 api.anthropic.com 返回 403 Request not allowed
# Available: codebuddy | claude | amp
# 模型：不传 --model 时用 codebuddy 默认档（实测 hy4-preview）；这里显式指定 deepseek-v4-flash
#   ⚠️ WorkBuddy 界面选择器显示的「Deepseek-V4.1-Flash」**不是 CLI 的模型 ID**，CLI 里没有 4.1 形态；
#      传错 ID 会被拒绝或静默回退默认档（等于没指定）。真实 ID 从打包清单查：
#      grep -aoE "deepseek-v4[a-z0-9.-]*" "<WorkBuddy>/resources/app.asar" | sort -u
#      → deepseek-v4-flash / -202605 / -free / deepseek-v4-pro / -202606
#       设 RALPH_MODEL="" 可退回默认档
TOOL="codebuddy"
RALPH_MODEL="${RALPH_MODEL-deepseek-v4-flash}"
MAX_ITERATIONS=10

while [[ $# -gt 0 ]]; do
  case $1 in
    --tool)
      TOOL="$2"
      shift 2
      ;;
    --tool=*)
      TOOL="${1#*=}"
      shift
      ;;
    --model)
      RALPH_MODEL="$2"
      shift 2
      ;;
    --model=*)
      RALPH_MODEL="${1#*=}"
      shift
      ;;
    *)
      # Assume it's max_iterations if it's a number
      if [[ "$1" =~ ^[0-9]+$ ]]; then
        MAX_ITERATIONS="$1"
      fi
      shift
      ;;
  esac
done

# Validate tool choice
if [[ "$TOOL" != "amp" && "$TOOL" != "claude" && "$TOOL" != "codebuddy" ]]; then
  echo "Error: Invalid tool '$TOOL'. Must be 'amp', 'claude' or 'codebuddy'."
  exit 1
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- codebuddy CLI 定位（WorkBuddy 自带）----
# 优先级：环境变量 CODEBUDDY_BIN > PATH 中的 codebuddy > WorkBuddy 安装目录
if [ -z "$CODEBUDDY_BIN" ] || [ ! -e "$CODEBUDDY_BIN" ]; then
  if command -v codebuddy >/dev/null 2>&1; then
    CODEBUDDY_BIN="$(command -v codebuddy)"
  elif [ -f "E:/AIDevelop/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy" ]; then
    CODEBUDDY_BIN="E:/AIDevelop/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy"
  fi
fi

# bin/codebuddy 是带 node shebang 的脚本，在 Git Bash 下直接执行会被 MSYS 路径转换搞坏
# （报 Cannot find module 'c:\e\...'），所以显式用 node 跑
CODEBUDDY_EXEC=()
if [ -n "$CODEBUDDY_BIN" ]; then
  if head -c 2 "$CODEBUDDY_BIN" 2>/dev/null | grep -q '#!'; then
    CODEBUDDY_EXEC=(node "$CODEBUDDY_BIN")
  else
    CODEBUDDY_EXEC=("$CODEBUDDY_BIN")
  fi
fi

PRD_FILE="$SCRIPT_DIR/prd.json"
PROGRESS_FILE="$SCRIPT_DIR/progress.txt"
ARCHIVE_DIR="$SCRIPT_DIR/archive"
LAST_BRANCH_FILE="$SCRIPT_DIR/.last-branch"

# Archive previous run if branch changed
if [ -f "$PRD_FILE" ] && [ -f "$LAST_BRANCH_FILE" ]; then
  CURRENT_BRANCH=$(jq -r '.branchName // empty' "$PRD_FILE" 2>/dev/null || echo "")
  LAST_BRANCH=$(cat "$LAST_BRANCH_FILE" 2>/dev/null || echo "")
  
  if [ -n "$CURRENT_BRANCH" ] && [ -n "$LAST_BRANCH" ] && [ "$CURRENT_BRANCH" != "$LAST_BRANCH" ]; then
    # Archive the previous run
    DATE=$(date +%Y-%m-%d)
    # Strip "ralph/" prefix from branch name for folder
    FOLDER_NAME=$(echo "$LAST_BRANCH" | sed 's|^ralph/||')
    ARCHIVE_FOLDER="$ARCHIVE_DIR/$DATE-$FOLDER_NAME"
    
    echo "Archiving previous run: $LAST_BRANCH"
    mkdir -p "$ARCHIVE_FOLDER"
    [ -f "$PRD_FILE" ] && cp "$PRD_FILE" "$ARCHIVE_FOLDER/"
    [ -f "$PROGRESS_FILE" ] && cp "$PROGRESS_FILE" "$ARCHIVE_FOLDER/"
    echo "   Archived to: $ARCHIVE_FOLDER"
    
    # Reset progress file for new run
    echo "# Ralph Progress Log" > "$PROGRESS_FILE"
    echo "Started: $(date)" >> "$PROGRESS_FILE"
    echo "---" >> "$PROGRESS_FILE"
  fi
fi

# Track current branch
if [ -f "$PRD_FILE" ]; then
  CURRENT_BRANCH=$(jq -r '.branchName // empty' "$PRD_FILE" 2>/dev/null || echo "")
  if [ -n "$CURRENT_BRANCH" ]; then
    echo "$CURRENT_BRANCH" > "$LAST_BRANCH_FILE"
  fi
fi

# Initialize progress file if it doesn't exist
if [ ! -f "$PROGRESS_FILE" ]; then
  echo "# Ralph Progress Log" > "$PROGRESS_FILE"
  echo "Started: $(date)" >> "$PROGRESS_FILE"
  echo "---" >> "$PROGRESS_FILE"
fi

# ---- 引擎预检：没装就直接退出，避免空烧 N 轮 ----
if [[ "$TOOL" == "codebuddy" ]]; then
  if [ ${#CODEBUDDY_EXEC[@]} -eq 0 ]; then
    echo "Error: codebuddy CLI not found. Set CODEBUDDY_BIN=/path/to/codebuddy and retry."
    exit 1
  fi
  echo "Using codebuddy: $CODEBUDDY_BIN"
elif [[ "$TOOL" == "claude" ]]; then
  command -v claude >/dev/null 2>&1 || { echo "Error: claude CLI not found in PATH."; exit 1; }
elif [[ "$TOOL" == "amp" ]]; then
  command -v amp >/dev/null 2>&1 || { echo "Error: amp CLI not found in PATH (本机未安装 amp)."; exit 1; }
fi

echo "Starting Ralph - Tool: $TOOL - Model: ${RALPH_MODEL:-（默认档）} - Max iterations: $MAX_ITERATIONS"

for i in $(seq 1 $MAX_ITERATIONS); do
  echo ""
  echo "==============================================================="
  echo "  Ralph Iteration $i of $MAX_ITERATIONS ($TOOL)"
  echo "==============================================================="

  # Run the selected tool with the ralph prompt
  if [[ "$TOOL" == "amp" ]]; then
    OUTPUT=$(cat "$SCRIPT_DIR/prompt.md" | amp --dangerously-allow-all 2>&1 | tee /dev/stderr) || true
  elif [[ "$TOOL" == "codebuddy" ]]; then
    # WorkBuddy 自带 CLI：-y 跳过权限校验，-p 一次性输出后退出
    # 消耗 WorkBuddy 额度，不经过 api.anthropic.com
    # 数组展开写，避免 RALPH_MODEL 为空时产生空参数
    MODEL_ARGS=()
    [ -n "$RALPH_MODEL" ] && MODEL_ARGS=(--model "$RALPH_MODEL")
    OUTPUT=$(CODEBUDDY_SKIP_GIT_BASH_CHECK=1 "${CODEBUDDY_EXEC[@]}" "${MODEL_ARGS[@]}" -y -p < "$SCRIPT_DIR/CLAUDE.md" 2>&1 | tee /dev/stderr) || true
  else
    # Claude Code: use --dangerously-skip-permissions for autonomous operation, --print for output
    OUTPUT=$(claude --dangerously-skip-permissions --print < "$SCRIPT_DIR/CLAUDE.md" 2>&1 | tee /dev/stderr) || true
  fi
  
  # Check for completion signal
  if echo "$OUTPUT" | grep -q "<promise>COMPLETE</promise>"; then
    echo ""
    echo "Ralph completed all tasks!"
    echo "Completed at iteration $i of $MAX_ITERATIONS"
    exit 0
  fi
  
  echo "Iteration $i complete. Continuing..."
  sleep 2
done

echo ""
echo "Ralph reached max iterations ($MAX_ITERATIONS) without completing all tasks."
echo "Check $PROGRESS_FILE for status."
exit 1
