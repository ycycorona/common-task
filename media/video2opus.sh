#!/usr/bin/env bash
set -euo pipefail

# 薄壳：实现在 video2audio.sh 的 opus profile。老用法 ./video2opus.sh <视频文件路径 或 目录路径> 不变。
# 转码参数：-map 0:a:0 -ac 1 -c:a libopus -b:a 32k -application voip，并跳过隐藏文件。

here=$(cd "$(dirname "$0")" && pwd)
export VIDEO2AUDIO_NAME="$0"
exec "$here/video2audio.sh" -f opus "$@"
