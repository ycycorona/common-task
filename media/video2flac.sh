#!/usr/bin/env bash
set -euo pipefail

# 简单说明：
# 传入一个参数：可以是单个文件，也可以是一个目录
# - 如果是文件：只转这一个文件
# - 如果是目录：递归查找常见视频后缀并批量转换为 .flac

if [ $# -ne 1 ]; then
  echo "用法: $0 <视频文件路径 或 目录路径>"
  exit 1
fi

INPUT_PATH="$1"

# 要识别为“视频”的文件后缀（可按需自己加）
VIDEO_EXTENSIONS=("mp4" "mkv" "mov" "avi" "flv" "webm" "m4v" "wmv")

process_file() {
  local in="$1"

  # 跳过不是普通文件的情况
  if [ ! -f "$in" ]; then
    return
  fi

  local filename
  filename="$(basename "$in")"
  local dirname
  dirname="$(dirname "$in")"

  local name="${filename%.*}"
  local out="${dirname}/${name}.flac"

  # 如果已经有对应的 .flac，就跳过
  if [ -f "$out" ]; then
    echo "已存在，跳过: $out"
    return
  fi

  echo "转换: $in  ->  $out"

  # 核心 ffmpeg 命令：16kHz 单声道 FLAC
  # -nostdin 避免 ffmpeg 读取 while 循环的 stdin，导致 find 输出被吞掉卡住
  ffmpeg -nostdin -y -i "$in" -vn -ac 1 -ar 16000 -c:a flac "$out"
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
  while IFS= read -r -d '' f; do
    if is_video_file "$f"; then
      video_files+=("$f")
    fi
  done < <(find "$INPUT_PATH" -type f -print0)

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
