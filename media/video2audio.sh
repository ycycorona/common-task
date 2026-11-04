#!/usr/bin/env bash
set -euo pipefail

# 一份实现，三个薄壳共用：video2aac.sh / video2flac.sh / video2opus.sh
# （薄壳只传 -f <profile>，老用法 ./video2aac.sh <文件|目录> 保持不变）
#
# 传入一个参数：可以是单个文件，也可以是一个目录
# - 如果是文件：只转这一个文件
# - 如果是目录：递归查找常见视频后缀并批量转换
#
# 用法: video2audio.sh -f aac|flac|opus <视频文件路径 或 目录路径>
#
# 各 profile 的差异只在 profile_set()：输出后缀、ffmpeg 参数、是否跳过隐藏文件。
#   aac  : -map 0:a:0 -c:a aac -b:a 256k                 跳过隐藏文件
#   flac : -vn -ac 1 -ar 16000 -c:a flac                 不跳过隐藏文件
#   opus : -map 0:a:0 -ac 1 -c:a libopus -b:a 32k -application voip   跳过隐藏文件

PROG="${VIDEO2AUDIO_NAME:-$(basename "$0")}"

# 要识别为“视频”的文件后缀（可按需自己加）
VIDEO_EXTENSIONS=("mp4" "mkv" "mov" "avi" "flv" "webm" "m4v" "wmv")

PROFILE=""
SKIP_HIDDEN=1
OUT_EXT=""
CODEC_ARGS=()

profile_set() {
  case "$1" in
    aac)
      OUT_EXT="aac"
      SKIP_HIDDEN=1
      CODEC_ARGS=(-map 0:a:0 -c:a aac -b:a 256k)
      ;;
    flac)
      OUT_EXT="flac"
      SKIP_HIDDEN=0
      CODEC_ARGS=(-vn -ac 1 -ar 16000 -c:a flac)
      ;;
    opus)
      OUT_EXT="opus"
      SKIP_HIDDEN=1
      CODEC_ARGS=(-map 0:a:0 -ac 1 -c:a libopus -b:a 32k -application voip)
      ;;
    *)
      echo "错误: 未知 profile: $1（可选 aac|flac|opus）" >&2
      exit 2
      ;;
  esac
}

while getopts ":f:" opt; do
  case "$opt" in
    f) PROFILE="$OPTARG" ;;
    *) echo "用法: $PROG -f aac|flac|opus <视频文件路径 或 目录路径>" >&2; exit 1 ;;
  esac
done
shift $((OPTIND - 1))

[ -n "$PROFILE" ] || { echo "用法: $PROG -f aac|flac|opus <视频文件路径 或 目录路径>" >&2; exit 1; }
[ $# -eq 1 ] || { echo "用法: $PROG <视频文件路径 或 目录路径>" >&2; exit 1; }
profile_set "$PROFILE"

INPUT_PATH="$1"

process_file() {
  local in="$1"

  # 跳过不是普通文件的情况
  if [ ! -f "$in" ]; then
    return
  fi

  local filename
  filename="$(basename "$in")"
  # 忽略以 . 开头的隐藏文件（flac profile 历史上不跳过，保持原样）
  if [ "$SKIP_HIDDEN" -eq 1 ] && [ "${filename#.?}" != "$filename" ]; then
    return
  fi
  local dirname
  dirname="$(dirname "$in")"

  local name="${filename%.*}"
  local out="${dirname}/${name}.${OUT_EXT}"

  # 如果已经有对应的输出文件，就跳过
  if [ -f "$out" ]; then
    echo "已存在，跳过: $out"
    return
  fi

  echo "转换: $in  ->  $out"

  # -nostdin 避免 ffmpeg 读取 while 循环的 stdin，导致 find 输出被吞掉卡住
  ffmpeg -nostdin -y -i "$in" "${CODEC_ARGS[@]}" "$out"
}

is_video_file() {
  local file="$1"
  # 取后缀
  local ext="${file##*.}"
  # 转成小写（兼容老 bash）
  ext="$(echo "$ext" | tr 'A-Z' 'a-z')"

  for e in "${VIDEO_EXTENSIONS[@]}"; do
    if [ "$ext" = "$e" ]; then
      return 0
    fi
  done
  return 1
}

if [ -f "$INPUT_PATH" ]; then
  # 单个文件
  if is_video_file "$INPUT_PATH"; then
    process_file "$INPUT_PATH"
  else
    echo "警告: 看起来不是常见视频格式: $INPUT_PATH"
    echo "仍然尝试转码……"
    process_file "$INPUT_PATH"
  fi

elif [ -d "$INPUT_PATH" ]; then
  # 目录，递归批量处理
  echo "批量转换目录: $INPUT_PATH"
  # 先收集所有视频文件，便于统计总数（兼容 macOS 老旧 bash，无 mapfile）
  video_files=()
  if [ "$SKIP_HIDDEN" -eq 1 ]; then
    while IFS= read -r -d '' f; do
      if is_video_file "$f"; then
        video_files+=("$f")
      fi
    done < <(find "$INPUT_PATH" -path '*/.*' -prune -o -type f -print0)
  else
    while IFS= read -r -d '' f; do
      if is_video_file "$f"; then
        video_files+=("$f")
      fi
    done < <(find "$INPUT_PATH" -type f -print0)
  fi

  total=${#video_files[@]}
  if [ "$total" -eq 0 ]; then
    echo "未找到视频文件，退出。"
    exit 0
  fi

  idx=0
  for file in "${video_files[@]}"; do
    idx=$((idx + 1))
    echo "开始处理：${idx}/${total} -> $file"
    process_file "$file"
  done

else
  echo "错误: 找不到文件或目录: $INPUT_PATH"
  exit 1
fi

echo "完成 ✅"
