#!/usr/bin/env bash
set -euo pipefail

# 快查孤儿开发进程：PPID 为 1 且工作目录位于 ~/Code/ 下的进程。
# 用法: ./dev-process-audit.sh
# 只读脚本，不会结束任何进程。

for tool in ps lsof; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "错误: 找不到 $tool。" >&2
    exit 127
  }
done

procs=$(ps -axo pid=,ppid=) || {
  echo "错误: ps 执行失败。" >&2
  exit 1
}

found=0
while read -r pid; do
  [[ -n "$pid" ]] || continue

  cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' || true)

  [[ "$cwd" == "$HOME/Code/"* ]] || continue

  ps -p "$pid" -o pid=,ppid=,etime=,command=
  found=1
done < <(awk '$2 == 1 {print $1}' <<<"$procs")

if [[ $found -eq 0 ]]; then
  echo "未发现孤儿开发进程（PPID=1 且工作目录在 $HOME/Code/ 下）。" >&2
fi
