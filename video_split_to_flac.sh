#!/usr/bin/env bash
set -euo pipefail

# 功能说明：
# 传入一个视频文件，按指定秒数切分（默认600秒=10分钟）
# 1. 保留切分后的视频文件：原文件名_part001.mp4, 原文件名_part002.mp4, ...
# 2. 从每段视频提取音频转为单声道16kHz的 .flac：原文件名_part001.flac, 原文件名_part002.flac, ...
# 3. 输出统一放在与原文件同名的目录下

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
  echo "用法: $0 <视频文件路径> [切分时长秒数，默认600]"
  exit 1
fi

INPUT_FILE="$1"

if [ ! -f "$INPUT_FILE" ]; then
  echo "错误: 文件不存在: $INPUT_FILE"
  exit 1
fi

# 切分时长（秒），默认600秒
DEFAULT_SEGMENT_DURATION=600
SEGMENT_DURATION="${2:-$DEFAULT_SEGMENT_DURATION}"
if ! [[ "$SEGMENT_DURATION" =~ ^[0-9]+$ ]] || [ "$SEGMENT_DURATION" -le 0 ]; then
  echo "错误: 切分时长必须是大于0的整数秒"
  exit 1
fi

# 获取文件信息
filename="$(basename "$INPUT_FILE")"
dirname="$(dirname "$INPUT_FILE")"
name="${filename%.*}"

# 获取原视频的扩展名
ext="${filename##*.}"
output_dir="${dirname}/${name}"

# 输出目录：与原文件同名的文件夹
mkdir -p "$output_dir"

echo "正在将视频切分，每段 ${SEGMENT_DURATION} 秒..."

# 使用 ffmpeg 切分视频（保留视频和音频流）
# -f segment: 使用segment muxer进行切分
# -segment_time: 每段时长
# -c copy: 直接复制流，不重新编码（更快）
# -reset_timestamps 1: 重置每段的时间戳
ffmpeg -y -i "$INPUT_FILE" \
  -f segment \
  -segment_time "$SEGMENT_DURATION" \
  -segment_start_number 1 \
  -c copy \
  -reset_timestamps 1 \
  "${output_dir}/${name}_part%03d.${ext}"

echo "正在从每段视频提取音频并转换为FLAC..."

# 遍历所有切分的视频片段，提取音频转为flac
for video_part in "${output_dir}/${name}_part"*.${ext}; do
  if [ -f "$video_part" ]; then
    # 获取片段文件名（不含扩展名）
    part_basename="$(basename "$video_part")"
    part_name="${part_basename%.*}"
    out_flac="${output_dir}/${part_name}.flac"
    
    echo "提取音频: $video_part -> $out_flac"
    
    # 转换为 16kHz 单声道 FLAC
    ffmpeg -y -i "$video_part" -vn -ac 1 -ar 16000 -c:a flac "$out_flac"
  fi
done

echo "完成 ✅"
echo "输出文件位于: $output_dir"
