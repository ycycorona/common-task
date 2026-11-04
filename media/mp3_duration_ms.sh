#!/usr/bin/env bash
set -euo pipefail

# 用法: ./mp3_duration_ms.sh <音频文件>
# 输出 ffprobe 读取到的时长，单位为毫秒。

if [[ $# -ne 1 || ! -f "$1" ]]; then
  echo "用法: $0 <音频文件>" >&2
  exit 2
fi

if ! command -v ffprobe >/dev/null 2>&1; then
  echo "错误: 找不到 ffprobe，请先安装 ffmpeg。" >&2
  exit 127
fi

duration="$(ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 "$1")"

if [[ ! "$duration" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "错误: 无法读取音频时长。" >&2
  exit 1
fi

awk -v seconds="$duration" 'BEGIN { printf "%.0f\n", seconds * 1000 }'
