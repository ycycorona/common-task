#!/usr/bin/env bash
set -euo pipefail

# 从远程主机覆盖拉取一个目录，本地做干净覆盖（旧文件全部清掉）。
# 用法: ./remote-dir-sync.sh [host] [remote_path] [local_dir]
# 默认: ./remote-dir-sync.sh
#       → free-arm:/home/ubuntu/wmc-dev/dist/unpacked → ~/Downloads/wmc-dev/unpacked
# 拉取失败时本地目录保持原样不动。

HOST=${1:-free-arm}
REMOTE=${2:-/home/ubuntu/wmc-dev/dist/unpacked}
DEST=${3:-$HOME/Downloads/wmc-dev/unpacked}
DEST=${DEST%/}

for tool in ssh tar; do
  command -v "$tool" >/dev/null 2>&1 || { echo "错误: 找不到 $tool。" >&2; exit 127; }
done

mkdir -p "$(dirname "$DEST")"

# 先解到同级临时目录，成功后整体替换，避免失败时留下半个目录
tmp=$(mktemp -d "$(dirname "$DEST")/.sync.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

if ! ssh "$HOST" "tar czf - -C $(printf %q "$REMOTE") ." | tar xzf - -C "$tmp"; then
  echo "错误: 从 $HOST:$REMOTE 拉取失败，本地目录未改动。" >&2
  exit 1
fi

if [[ -z "$(ls -A "$tmp")" ]]; then
  echo "错误: $HOST:$REMOTE 为空，本地目录未改动。" >&2
  exit 1
fi

chmod 755 "$tmp"
rm -rf "$DEST"
mv "$tmp" "$DEST"
trap - EXIT

echo "已覆盖 $DEST ← $HOST:$REMOTE ($(find "$DEST" -type f | wc -l | tr -d ' ') 个文件, $(du -sh "$DEST" | cut -f1))"
