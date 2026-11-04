#!/usr/bin/env bash
set -euo pipefail

# 薄壳：实现在 video2audio.sh 的 flac profile。老用法 ./video2flac.sh <视频文件路径 或 目录路径> 不变。
# 转码参数：-vn -ac 1 -ar 16000 -c:a flac；历史上不跳过隐藏文件，保持一致。

here=$(cd "$(dirname "$0")" && pwd)
export VIDEO2AUDIO_NAME="$0"
exec "$here/video2audio.sh" -f flac "$@"
