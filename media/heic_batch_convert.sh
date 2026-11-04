#!/usr/bin/env bash
set -euo pipefail

format="jpeg"
quality=80
output_dir=""

usage() {
  cat <<'EOF'
Usage:
  ./heic_batch_convert.sh [-f jpeg|png] [-q quality] [-o output_dir] <file_or_dir> [...]

Options:
  -f   Output format (jpeg or png). Default: jpeg.
  -q   JPEG quality 1-100. Ignored for png. Default: 80 (normal quality).
  -o   Output directory. Default: alongside each source file.
  -h   Show this help.

Examples:
  ./heic_batch_convert.sh Photos/*.heic
  ./heic_batch_convert.sh -f png /path/to/folder1 /path/to/image.heic
  ./heic_batch_convert.sh -o ./converted -q 75 ~/Pictures
EOF
}

while getopts "f:q:o:h" opt; do
  case "$opt" in
    f) format="$OPTARG" ;;
    q) quality="$OPTARG" ;;
    o) output_dir="${OPTARG%/}" ;;
    h)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 1
      ;;
  esac
done
shift $((OPTIND - 1))

if [[ $# -eq 0 ]]; then
  usage >&2
  exit 1
fi

format="$(printf '%s' "$format" | tr '[:upper:]' '[:lower:]')"

if [[ "$format" != "jpeg" && "$format" != "png" ]]; then
  echo "Unsupported format: $format (use jpeg or png)" >&2
  exit 1
fi

if [[ "$format" == "jpeg" ]]; then
  if ! [[ "$quality" =~ ^[0-9]+$ ]] || ((quality < 1 || quality > 100)); then
    echo "JPEG quality must be an integer between 1 and 100 (got $quality)" >&2
    exit 1
  fi
fi

if ! command -v sips >/dev/null 2>&1; then
  echo "sips not found. This script relies on the built-in macOS 'sips' tool." >&2
  exit 1
fi

if [[ -n "$output_dir" ]]; then
  mkdir -p "$output_dir"
fi

convert_file() {
  local file="$1"
  local dir dest_dir basename_out dest ext

  dir="$(dirname "$file")"
  dest_dir="${output_dir:-$dir}"
  mkdir -p "$dest_dir"

  basename_out="$(basename "${file%.*}")"
  ext=$([[ "$format" == "jpeg" ]] && echo "jpg" || echo "png")
  dest="$dest_dir/$basename_out.$ext"

  echo "Converting: $file -> $dest"

  if [[ "$format" == "jpeg" ]]; then
    sips -s format jpeg -s formatOptions "$quality" "$file" --out "$dest" >/dev/null
  else
    sips -s format png "$file" --out "$dest" >/dev/null
  fi
}

process_path() {
  local path="$1"
  local lowered

  if [[ -d "$path" ]]; then
    while IFS= read -r -d '' f; do
      convert_file "$f"
    done < <(find "$path" -type f \( -iname "*.heic" -o -iname "*.heif" \) -print0)
  elif [[ -f "$path" ]]; then
    lowered="$(printf '%s' "$path" | tr '[:upper:]' '[:lower:]')"
    case "$lowered" in
      *.heic | *.heif) convert_file "$path" ;;
      *)
        echo "Skipping non-HEIC file: $path" >&2
        ;;
    esac
  else
    echo "Skipping missing path: $path" >&2
  fi
}

for target in "$@"; do
  process_path "$target"
done
