#!/usr/bin/env bash

# ------------------------------------------------------------------------------
# VideoCaptioner transcribe runner (screen single-window mode)
#
# 作用
#   - 在指定项目目录内激活 venv，并执行：
#       python -m app.cli transcribe --config <config> --input <input>
#   - 使用 screen 管理后台任务，固定只用一个 screen session（默认名 whisper）
#     且永远只复用该 session 的 window 0（不创建新 window）。
#
# 参数
#   -c <config_toml>   必填。config_ja.toml 路径，可相对或绝对。
#   -i <input_path>    必填。输入文件或目录路径（只要存在即可）。
#   -s <session_name>  可选。screen 会话名，默认 whisper。
#   -h                显示帮助。
#
# 行为逻辑（重点）
#   1) 如果 screen 会话不存在：
#        - 创建 screen 会话（Detached），并在唯一 window 中启动任务
#        - 任务结束后保留一个 shell（方便你随时 screen -r 进去看输出/复用）
#
#   2) 如果 screen 会话已存在：
#        a) 若检测到 transcribe 任务正在执行：
#             - 不启动新任务，提示你进入查看
#        b) 若会话处于 (Attached) 状态：
#             - 直接拒绝启动（避免 screen -X stuff 注入命令打断你的交互程序）
#             - 提示你先进入并分离：Ctrl-A D
#        c) 若会话 Detached 且无任务执行：
#             - 将新命令注入到 window 0 执行（复用同一个 window）
#
# 任务检测方式
#   - 优先使用 PID 文件：$PROJECT_DIR/.screen_<session>.transcribe.pid
#     启动任务时写入 pid，结束后删除
#   - PID 文件丢失/过期时，兜底扫描进程：
#     pgrep -f "python -m app.cli transcribe" + 校验 /proc/<pid>/cwd 是否在项目目录
#
# 常用命令
#   - 查看会话：screen -ls
#   - 进入会话：screen -r whisper
#   - 分离会话：在 screen 内按 Ctrl-A 然后 D
#
# 退出码
#   - 0：成功启动任务 / 或检测到正在执行而正常退出提示
#   - 1：参数/路径/环境错误
#   - 2：会话处于 Attached 状态，拒绝启动
#
# 注意
#   - 本脚本会固定在 PROJECT_DIR 下运行并使用 ./.venv/bin/activate
#   - 若你需要并行跑多个任务，请改用“新开 window”或不同 session 名（-s）
# ------------------------------------------------------------------------------


# 效果就是：
# whisper 在跑任务 → 不启动新任务
# whisper 已 attach → 直接拒绝启动（exit code 2）
# whisper 存在但没跑任务且 detached → 复用同一个 window 0 运行新任务
# whisper 不存在 → 创建 session 并运行任务

set -euo pipefail

PROJECT_DIR="/root/autodl-tmp/VideoCaptioner"
DEFAULT_SESSION="whisper"
WINDOW_INDEX="0"

usage() {
  cat <<EOF
Usage:
  $0 -c <config_toml> -i <input_path> [-s <screen_session_name>]
EOF
}

CONFIG=""
INPUT=""
SESSION="$DEFAULT_SESSION"

while getopts ":c:i:s:h" opt; do
  case "$opt" in
    c) CONFIG="$OPTARG" ;;
    i) INPUT="$OPTARG" ;;
    s) SESSION="$OPTARG" ;;
    h) usage; exit 0 ;;
    \?) echo "Unknown option: -$OPTARG" >&2; usage; exit 1 ;;
    :)  echo "Missing argument for -$OPTARG" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "${CONFIG}" || -z "${INPUT}" ]]; then
  echo "Error: -c and -i are required." >&2
  usage
  exit 1
fi

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "Error: PROJECT_DIR not found: $PROJECT_DIR" >&2
  exit 1
fi

cd "$PROJECT_DIR"

if [[ -f "$CONFIG" ]]; then
  CONFIG_PATH="$CONFIG"
elif [[ -f "$PROJECT_DIR/$CONFIG" ]]; then
  CONFIG_PATH="$PROJECT_DIR/$CONFIG"
else
  echo "Error: config not found: $CONFIG" >&2
  exit 1
fi

if [[ ! -e "$INPUT" ]]; then
  echo "Error: input not found: $INPUT" >&2
  exit 1
fi

if [[ ! -f "./.venv/bin/activate" ]]; then
  echo "Error: venv activate not found: $PROJECT_DIR/.venv/bin/activate" >&2
  exit 1
fi

PID_FILE="$PROJECT_DIR/.screen_${SESSION}.transcribe.pid"

session_exists() {
  screen -list | grep -q "[[:space:]]${SESSION}[[:space:]]"
}

session_attached() {
  screen -list | grep -E "[[:space:]]${SESSION}[[:space:]].*\(Attached\)" -q
}

task_running() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      return 0
    else
      rm -f "$PID_FILE" || true
    fi
  fi

  local pids
  pids="$(pgrep -f "python -m app\.cli transcribe" 2>/dev/null || true)"
  [[ -z "${pids:-}" ]] && return 1

  local p
  for p in $pids; do
    local cwd
    cwd="$(readlink -f "/proc/$p/cwd" 2>/dev/null || true)"
    if [[ "$cwd" == "$(readlink -f "$PROJECT_DIR")" ]]; then
      return 0
    fi
  done
  return 1
}

INNER_CMD=$(
  cat <<EOF
cd "$PROJECT_DIR" && source "./.venv/bin/activate" && \
( python -m app.cli transcribe --config "$CONFIG_PATH" --input "$INPUT" & \
  pid=\$!; echo \$pid > "$PID_FILE"; wait \$pid; rm -f "$PID_FILE" ) ; \
echo "[DONE] transcribe finished: \$(date)"
EOF
)

start_new_session_and_run() {
  screen -dmS "$SESSION" bash -lc "$INNER_CMD; exec bash -l"
  echo "✅ 已创建 screen 会话 '${SESSION}' 并在唯一 window 中启动任务。"
  echo "进入：screen -r ${SESSION}"
  echo "离开但不中断任务：在 screen 内 Ctrl-A 然后 D"
}

run_in_existing_single_window() {
  # Attached 时直接拒绝，避免 stuff 注入打断你的交互程序
  if session_attached; then
    echo "❌ screen 会话 '${SESSION}' 当前是 Attached 状态，已拒绝启动新任务。"
    echo "请先进入并分离：screen -r ${SESSION}  然后按 Ctrl-A D"
    echo "分离后再运行本脚本。"
    exit 2
  fi

  local inner_escaped line
  printf -v inner_escaped "%q" "$INNER_CMD"
  line="bash -lc $inner_escaped"

  screen -S "$SESSION" -p "$WINDOW_INDEX" -X stuff "$line"$'\n'
  echo "✅ 已在现有 screen 会话 '${SESSION}' 的唯一 window (0) 中启动新任务。"
  echo "进入查看：screen -r ${SESSION}"
}

# ---------- 主流程 ----------
if session_exists; then
  if task_running; then
    echo "⚠️  检测到 '${SESSION}' 正在执行 transcribe 任务，未启动新任务。"
    echo "进入查看：screen -r ${SESSION}"
    exit 0
  fi
  run_in_existing_single_window
else
  start_new_session_and_run
fi

echo "会话列表：screen -ls"
