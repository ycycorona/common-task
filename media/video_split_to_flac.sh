#!/usr/bin/env bash
set -euo pipefail

# 功能说明：
# 传入一个媒体文件（视频或音频），按指定秒数切分（默认600秒=10分钟）
# 1. 保留切分后的片段文件：原文件名_part001.ext, 原文件名_part002.ext, ...
# 2. 将每段片段转换为指定的音频格式（flac 或 mp3）
# 3. 输出统一放在与原文件同名的目录下

if [ $# -lt 1 ] || [ $# -gt 3 ]; then
  echo "用法: $0 <媒体文件路径> [切分时长秒数|输出格式] [输出格式: flac|mp3]"
  echo "示例:"
  echo "  $0 video.mp4                 # 默认 600秒，输出 flac"
  echo "  $0 video.mp4 300             # 300秒，输出 flac"
  echo "  $0 video.mp4 mp3             # 默认 600秒，输出 mp3"
  echo "  $0 video.mp4 300 mp3         # 300秒，输出 mp3"
  exit 1
fi

INPUT_FILE="$1"

if [ ! -f "$INPUT_FILE" ]; then
  echo "错误: 文件不存在: $INPUT_FILE"
  exit 1
fi

DEFAULT_SEGMENT_DURATION=600
SEGMENT_DURATION=$DEFAULT_SEGMENT_DURATION
OUTPUT_FORMAT="flac"

if [ $# -eq 2 ]; then
  arg2=$(echo "$2" | tr '[:upper:]' '[:lower:]')
  if [[ "$arg2" == "flac" || "$arg2" == "mp3" ]]; then
    OUTPUT_FORMAT="$arg2"
  else
    SEGMENT_DURATION="$2"
  fi
elif [ $# -eq 3 ]; then
  SEGMENT_DURATION="$2"
  OUTPUT_FORMAT=$(echo "$3" | tr '[:upper:]' '[:lower:]')
fi

if ! [[ "$SEGMENT_DURATION" =~ ^[0-9]+$ ]] || [ "$SEGMENT_DURATION" -le 0 ]; then
  echo "错误: 切分时长必须是大于0的整数秒"
  exit 1
fi

if [[ "$OUTPUT_FORMAT" != "flac" && "$OUTPUT_FORMAT" != "mp3" ]]; then
  echo "错误: 不支持的输出格式 '$OUTPUT_FORMAT'。目前仅支持 flac 和 mp3。"
  exit 1
fi

# 获取文件信息
filename="$(basename "$INPUT_FILE")"
dirname="$(dirname "$INPUT_FILE")"
name="${filename%.*}"
ext="${filename##*.}"
output_dir="${dirname}/${name}"

mkdir -p "$output_dir"

echo "正在将媒体文件切分，每段 ${SEGMENT_DURATION} 秒..."

# 使用 ffmpeg 切分文件（保留流）
ffmpeg -y -i "$INPUT_FILE" \
  -f segment \
  -segment_time "$SEGMENT_DURATION" \
  -segment_start_number 1 \
  -c copy \
  -reset_timestamps 1 \
  "${output_dir}/${name}_part%03d.${ext}"

echo "正在将片段转换为 ${OUTPUT_FORMAT} 格式..."

for media_part in "${output_dir}/${name}_part"*.${ext}; do
  if [ -f "$media_part" ]; then
    part_basename="$(basename "$media_part")"
    part_name="${part_basename%.*}"
    out_audio="${output_dir}/${part_name}.${OUTPUT_FORMAT}"
    
    echo "转换: $media_part -> $out_audio"
    
    if [[ "$OUTPUT_FORMAT" == "flac" ]]; then
      # 转换为 16kHz 单声道 FLAC
      ffmpeg -y -i "$media_part" -vn -ac 1 -ar 16000 -c:a flac "$out_audio"
    elif [[ "$OUTPUT_FORMAT" == "mp3" ]]; then
      # 转换为高质量 VBR MP3 (-q:a 2)
      ffmpeg -y -i "$media_part" -vn -c:a libmp3lame -q:a 2 "$out_audio"
    fi
  fi
done

echo "完成 ✅"
echo "输出文件位于: $output_dir"