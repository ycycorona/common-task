#!/usr/bin/env bash
set -euo pipefail

# 功能说明：
# 从视频文件中按指定开始/结束时间截取视频片段，并输出指定的音频格式（支持 wav 和 mp3）
# wav 格式默认为 Whisper 友好的（16kHz 单声道 PCM）
# mp3 格式默认为高质量 VBR (质量等级 2，约 190 kbps)
#
# 用法:
#   ./video_clip_to_wav.sh <视频文件> <开始时间> <结束时间> [输出目录] [音频格式: wav|mp3]
#
# 时间格式示例:
#   00:01:30
#   90
#   00:01:30.500

if [ $# -lt 3 ] || [ $# -gt 5 ]; then
  echo "用法: $0 <视频文件> <开始时间> <结束时间> [输出目录|音频格式] [音频格式: wav|mp3]"
  echo "提示: 如果省略输出目录直接写音频格式，它会自动识别。例如: $0 video.mp4 00:00 00:05 mp3"
  exit 1
fi

INPUT_FILE="$1"
START_TIME="$2"
END_TIME="$3"

if [ $# -eq 4 ]; then
  arg4=$(echo "$4" | tr '[:upper:]' '[:lower:]')
  if [[ "$arg4" == "wav" || "$arg4" == "mp3" ]]; then
    OUTPUT_DIR="$(dirname "$INPUT_FILE")"
    OUTPUT_FORMAT="$arg4"
  else
    OUTPUT_DIR="$4"
    OUTPUT_FORMAT="wav"
  fi
elif [ $# -eq 5 ]; then
  OUTPUT_DIR="$4"
  OUTPUT_FORMAT=$(echo "$5" | tr '[:upper:]' '[:lower:]')
else
  OUTPUT_DIR="$(dirname "$INPUT_FILE")"
  OUTPUT_FORMAT="wav"
fi

if [ ! -f "$INPUT_FILE" ]; then
  echo "错误: 文件不存在: $INPUT_FILE"
  exit 1
fi

# 验证音频格式
if [[ "$OUTPUT_FORMAT" != "wav" && "$OUTPUT_FORMAT" != "mp3" ]]; then
  echo "错误: 不支持的音频格式 '$OUTPUT_FORMAT'。目前仅支持 wav 和 mp3。"
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

filename="$(basename "$INPUT_FILE")"
name="${filename%.*}"
ext="${filename##*.}"

safe_start="${START_TIME//:/-}"
safe_start="${safe_start//./-}"
safe_end="${END_TIME//:/-}"
safe_end="${safe_end//./-}"

# 保持与原视频相同的后缀名，避免 ffmpeg 使用 -c copy 时遇到容器不支持该编码的问题
output_video="${OUTPUT_DIR}/${name}_clip_${safe_start}_${safe_end}.${ext}"
output_audio="${OUTPUT_DIR}/${name}_clip_${safe_start}_${safe_end}.${OUTPUT_FORMAT}"

to_seconds() {
  local t="$1"
  awk -v t="$t" 'BEGIN{
    n=split(t,a,":");
    if(n==3){h=a[1];m=a[2];s=a[3]}
    else if(n==2){h=0;m=a[1];s=a[2]}
    else {h=0;m=0;s=a[1]}
    if(h=="")h=0;if(m=="")m=0;if(s=="")s=0;
    printf("%.6f", h*3600 + m*60 + s)
  }'
}

start_sec="$(to_seconds "$START_TIME")"
end_sec="$(to_seconds "$END_TIME")"
duration="$(awk -v e="$end_sec" -v s="$start_sec" 'BEGIN{printf("%.6f", e - s)}')"

if awk -v d="$duration" 'BEGIN{exit !(d>0)}'; then :; else
  echo "错误: 结束时间必须大于开始时间"
  exit 1
fi

echo "正在截取视频: $INPUT_FILE"
echo "时间范围: $START_TIME -> $END_TIME"
echo "输出视频: $output_video"

# 截取视频片段（极速切分：快速 seek + 直接流拷贝）
ffmpeg -y -ss "$START_TIME" -i "$INPUT_FILE" \
  -t "$duration" \
  -c copy \
  "$output_video"

echo "正在生成音频 (${OUTPUT_FORMAT}): $output_audio"

if [[ "$OUTPUT_FORMAT" == "wav" ]]; then
  # 提取音频并转为 16kHz 单声道 PCM WAV（Whisper 友好）
  ffmpeg -y -ss "$START_TIME" -i "$INPUT_FILE" \
    -t "$duration" \
    -vn -ac 1 -ar 16000 -c:a pcm_s16le \
    "$output_audio"
elif [[ "$OUTPUT_FORMAT" == "mp3" ]]; then
  # 提取音频并转为 MP3（高质量 VBR -q:a 2，平均约 190 kbps）
  ffmpeg -y -ss "$START_TIME" -i "$INPUT_FILE" \
    -t "$duration" \
    -vn -c:a libmp3lame -q:a 2 \
    "$output_audio"
fi

echo "完成 ✅"