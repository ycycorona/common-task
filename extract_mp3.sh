#!/usr/bin/env bash

set -euo pipefail

show_help() {
  cat <<EOF
Usage:
  ./extract_mp3.sh [OPTIONS] INPUT_FILE [INPUT_FILE ...]

Examples:
  ./extract_mp3.sh video.mp4                  # creates video.mp3
  ./extract_mp3.sh a.mp4 b.mkv c.mov          # creates a.mp3 b.mp3 c.mp3
  ./extract_mp3.sh --bitrate 128k *.mp4       # batch process with options

Options:
  -b, --bitrate VALUE        MP3 bitrate, default: 192k
  -r, --sample-rate VALUE    MP3 sample rate, default: 48000
  -c, --channels VALUE       Audio channels, default: 1
  -a, --audio-stream VALUE   Audio stream, default: 0:a:0
      --copy-mp3             Copy existing MP3 audio without re-encoding, default
      --no-copy-mp3          Always re-encode audio
  -t, --threads VALUE        FFmpeg threads, default: 0
  -s, --start VALUE          Start timestamp, such as 00:10:00 or 600
  -e, --end VALUE            End timestamp, such as 00:20:00 or 1200
  -d, --duration VALUE       Duration, such as 00:05:00 or 300; ignored when --end is set
  -h, --help                 Show this help message

Examples:
  ./extract_mp3.sh --bitrate 128k input.mp4
  ./extract_mp3.sh --channels 2 --bitrate 256k input.mkv
  ./extract_mp3.sh --audio-stream 0:a:1 input.mkv
  ./extract_mp3.sh --no-copy-mp3 input.avi
  ./extract_mp3.sh --threads 10 --no-copy-mp3 input.mov
  ./extract_mp3.sh --start 00:10:00 --end 00:20:00 input.mp4
  ./extract_mp3.sh --start 600 --duration 300 input.mp4
EOF
}

MP3_BITRATE="192k"
MP3_SAMPLE_RATE="48000"
MP3_CHANNELS="1"
AUDIO_STREAM="0:a:0"
COPY_MP3_AUDIO="1"
FFMPEG_THREADS="0"
START_TIME=""
END_TIME=""
DURATION=""

POSITIONAL_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -b|--bitrate)
      MP3_BITRATE="${2:-}"
      shift 2
      ;;
    -r|--sample-rate)
      MP3_SAMPLE_RATE="${2:-}"
      shift 2
      ;;
    -c|--channels)
      MP3_CHANNELS="${2:-}"
      shift 2
      ;;
    -a|--audio-stream)
      AUDIO_STREAM="${2:-}"
      shift 2
      ;;
    --copy-mp3)
      COPY_MP3_AUDIO="1"
      shift
      ;;
    --no-copy-mp3)
      COPY_MP3_AUDIO="0"
      shift
      ;;
    -t|--threads)
      FFMPEG_THREADS="${2:-}"
      shift 2
      ;;
    -s|--start)
      START_TIME="${2:-}"
      shift 2
      ;;
    -e|--end)
      END_TIME="${2:-}"
      shift 2
      ;;
    -d|--duration)
      DURATION="${2:-}"
      shift 2
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        POSITIONAL_ARGS+=("$1")
        shift
      done
      ;;
    -* )
      echo "Error: unknown option: $1"
      echo
      show_help
      exit 1
      ;;
    *)
      POSITIONAL_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ ${#POSITIONAL_ARGS[@]} -lt 1 ]]; then
  echo "Error: missing input file."
  echo
  show_help
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Error: ffmpeg not found."
  echo
  echo "Install ffmpeg:"
  echo "  macOS:  brew install ffmpeg"
  echo "  Ubuntu: sudo apt update && sudo apt install -y ffmpeg"
  exit 1
fi

if ! command -v ffprobe >/dev/null 2>&1; then
  echo "Error: ffprobe not found. It usually comes with ffmpeg."
  exit 1
fi

detect_cpu_cores() {
  if command -v sysctl >/dev/null 2>&1; then
    sysctl -n hw.ncpu 2>/dev/null && return 0
  fi
  if command -v nproc >/dev/null 2>&1; then
    nproc 2>/dev/null && return 0
  fi
  if command -v getconf >/dev/null 2>&1; then
    getconf _NPROCESSORS_ONLN 2>/dev/null && return 0
  fi
  echo "unknown"
}

build_trim_args() {
  local args=()

  if [[ -n "$START_TIME" ]]; then
    args+=("-ss" "$START_TIME")
  fi

  if [[ -n "$END_TIME" ]]; then
    args+=("-to" "$END_TIME")
  elif [[ -n "$DURATION" ]]; then
    args+=("-t" "$DURATION")
  fi

  if [[ ${#args[@]} -gt 0 ]]; then
    printf '%s\0' "${args[@]}"
  fi
}

sanitize_time_for_filename() {
  echo "$1" | sed 's/[^A-Za-z0-9._-]/-/g'
}

build_default_output_name() {
  local input="$1"
  local dir
  local base
  local name
  local suffix=""

  dir="$(dirname "$input")"
  base="$(basename "$input")"
  name="${base%.*}"

  if [[ -n "$START_TIME" ]]; then
    suffix="${suffix}.start-$(sanitize_time_for_filename "$START_TIME")"
  fi

  if [[ -n "$END_TIME" ]]; then
    suffix="${suffix}.end-$(sanitize_time_for_filename "$END_TIME")"
  elif [[ -n "$DURATION" ]]; then
    suffix="${suffix}.duration-$(sanitize_time_for_filename "$DURATION")"
  fi

  echo "${dir}/${name}${suffix}.mp3"
}

CPU_CORES="$(detect_cpu_cores)"

TRIM_ARGS=()
while IFS= read -r -d '' arg; do
  TRIM_ARGS+=("$arg")
done < <(build_trim_args)

TOTAL="${#POSITIONAL_ARGS[@]}"
SUCCEEDED=0
FAILED=0

for ((i=0; i<TOTAL; i++)); do
  INPUT="${POSITIONAL_ARGS[$i]}"
  INDEX="$((i + 1))"
  PERCENT="$(( INDEX * 100 / TOTAL ))"

  echo
  echo "========================================"
  echo "[$INDEX/$TOTAL | ${PERCENT}%] Processing: $INPUT"
  echo "========================================"
  echo

  if [[ ! -f "$INPUT" ]]; then
    echo "Error: input file does not exist: $INPUT"
    FAILED="$((FAILED + 1))"
    continue
  fi

  OUTPUT="$(build_default_output_name "$INPUT")"
  TMP_OUTPUT="${OUTPUT%.mp3}.tmp.$$.$i.mp3"
  FFPROBE_AUDIO_STREAM="${AUDIO_STREAM#0:}"
  INPUT_AUDIO_CODEC="$(ffprobe -v error -select_streams "$FFPROBE_AUDIO_STREAM" -show_entries stream=codec_name -of default=noprint_wrappers=1:nokey=1 "$INPUT" | head -n 1 || true)"

  echo "Input:        $INPUT"
  echo "Output:       $OUTPUT"
  echo "Audio stream: $AUDIO_STREAM"
  echo "Bitrate:      $MP3_BITRATE"
  echo "Sample rate:  $MP3_SAMPLE_RATE"
  echo "Channels:     $MP3_CHANNELS"
  echo "Input codec:  ${INPUT_AUDIO_CODEC:-unknown}"
  echo "Copy MP3:     $COPY_MP3_AUDIO"
  echo "CPU cores:    $CPU_CORES"
  echo "FFmpeg threads: $FFMPEG_THREADS"
  echo "Start time:   ${START_TIME:-<beginning>}"
  if [[ -n "$END_TIME" ]]; then
    echo "End time:     $END_TIME"
  elif [[ -n "$DURATION" ]]; then
    echo "Duration:     $DURATION"
  else
    echo "End time:     <end>"
  fi
  echo

  echo "Checking audio streams..."
  ffprobe -v error \
    -select_streams a \
    -show_entries stream=index,codec_name,channels,sample_rate:stream_tags=language,title \
    -of default=noprint_wrappers=1 \
    "$INPUT" || true

  echo
  echo "Extracting audio to MP3..."

  set +e
  if [[ "$COPY_MP3_AUDIO" == "1" && "$INPUT_AUDIO_CODEC" == "mp3" ]]; then
    echo "Input audio is already MP3; copying audio stream without re-encoding."
    ffmpeg -hide_banner -loglevel warning -y \
      ${TRIM_ARGS[@]+"${TRIM_ARGS[@]}"} \
      -i "$INPUT" \
      -map "$AUDIO_STREAM" \
      -vn \
      -sn \
      -dn \
      -codec:a copy \
      -map_metadata -1 \
      -f mp3 \
      "$TMP_OUTPUT"
  else
    ffmpeg -hide_banner -loglevel warning -y \
      ${TRIM_ARGS[@]+"${TRIM_ARGS[@]}"} \
      -i "$INPUT" \
      -map "$AUDIO_STREAM" \
      -vn \
      -sn \
      -dn \
      -ac "$MP3_CHANNELS" \
      -ar "$MP3_SAMPLE_RATE" \
      -af "aresample=async=1:first_pts=0" \
      -codec:a libmp3lame \
      -b:a "$MP3_BITRATE" \
      -map_metadata -1 \
      -threads "$FFMPEG_THREADS" \
      -f mp3 \
      "$TMP_OUTPUT"
  fi
  FFMPEG_EXIT_CODE=$?
  set -e

  if [[ $FFMPEG_EXIT_CODE -ne 0 ]]; then
    echo "Error: ffmpeg failed for $INPUT (exit code: $FFMPEG_EXIT_CODE)"
    rm -f "$TMP_OUTPUT"
    FAILED="$((FAILED + 1))"
    continue
  fi

  mv "$TMP_OUTPUT" "$OUTPUT"
  SUCCEEDED="$((SUCCEEDED + 1))"

  echo
  echo "Done."
  echo "Created: $OUTPUT"

done

echo
echo "========================================"
echo "All tasks completed: $SUCCEEDED succeeded, $FAILED failed (total: $TOTAL)"
echo "========================================"

if [[ $FAILED -gt 0 ]]; then
  exit 1
fi
