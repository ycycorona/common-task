#!/usr/bin/env bash
set -euo pipefail

# 薄壳：实现在 video2audio.sh 的 aac profile。老用法 ./video2aac.sh <视频文件路径 或 目录路径> 不变。
# 转码参数：-map 0:a:0 -c:a aac -b:a 256k，并跳过隐藏文件。

here=$(cd "$(dirname "$0")" && pwd)
export VIDEO2AUDIO_NAME="$0"
exec "$here/video2audio.sh" -f aac "$@"
