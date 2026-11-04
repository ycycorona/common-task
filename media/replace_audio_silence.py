#!/usr/bin/env python3
"""Replace MP4 audio tracks with silent ADPCM IMA WAV audio matching a reference."""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNEL_LAYOUT = "stereo"
DEFAULT_AUDIO_CODEC = "adpcm_ima_wav"
DEFAULT_HANDLER_NAME = "Sound Media Handler"
DEFAULT_LANGUAGE = "eng"


def format_setfile_timestamp(ts: float) -> str:
    """Format a POSIX timestamp for SetFile (MM/DD/YYYY HH:MM:SS)."""
    return _dt.datetime.fromtimestamp(ts).strftime("%m/%d/%Y %H:%M:%S")


def preserve_file_times(source: Path, target: Path, setfile_path: str | None) -> None:
    """Copy access/modify times via utime and creation time via SetFile when available."""
    stat_info = os.stat(source)
    os.utime(target, (stat_info.st_atime, stat_info.st_mtime))

    if setfile_path is None:
        return

    creation = getattr(stat_info, "st_birthtime", None)
    if creation is None:
        return

    creation_str = format_setfile_timestamp(creation)
    modified_str = format_setfile_timestamp(stat_info.st_mtime)

    subprocess.run([setfile_path, "-d", creation_str, str(target)], check=True)
    subprocess.run([setfile_path, "-m", modified_str, str(target)], check=True)


def build_ffmpeg_command(ffmpeg_bin: str, source: Path, temp_target: Path) -> list[str]:
    """Construct ffmpeg invocation that injects a silent audio track."""
    lavfi_src = f"anullsrc=channel_layout={DEFAULT_CHANNEL_LAYOUT}:sample_rate={DEFAULT_SAMPLE_RATE}"
    return [
        ffmpeg_bin,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-f",
        "lavfi",
        "-i",
        lavfi_src,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        DEFAULT_AUDIO_CODEC,
        "-shortest",
        "-metadata:s:a:0",
        f"handler_name={DEFAULT_HANDLER_NAME}",
        "-metadata:s:a:0",
        f"language={DEFAULT_LANGUAGE}",
        "-map_metadata",
        "0",
        "-f",
        "mov",
        str(temp_target),
    ]


def process_file(
    ffmpeg_bin: str,
    source: Path,
    destination: Path,
    output_root: Path,
    setfile_path: str | None,
    dry_run: bool,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_target = destination.with_suffix(destination.suffix + ".tmp")

    if dry_run:
        print(f"[DRY-RUN] Would process: {source} -> {destination}")
        return

    if temp_target.exists():
        temp_target.unlink()

    cmd = build_ffmpeg_command(ffmpeg_bin, source, temp_target)
    subprocess.run(cmd, check=True)

    os.replace(temp_target, destination)
    preserve_file_times(source, destination, setfile_path)

    try:
        display_path = destination.relative_to(output_root)
    except ValueError:
        display_path = destination

    print(f"Processed: {source.name} -> {display_path}")


def collect_mp4_files(root: Path, recursive: bool) -> list[Path]:
    if recursive:
        return sorted(
            p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".mp4"
        )
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".mp4")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replace the audio track of every MP4 in a directory with a silent "
            "ADPCM IMA WAV track matching the reference settings."
        )
    )
    parser.add_argument("input_dir", type=Path, help="Directory containing MP4 files to process")
    parser.add_argument("output_dir", type=Path, help="Directory where processed files will be written")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="Path to the ffmpeg executable")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Process MP4 files in subdirectories as well",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the files that would be processed without invoking ffmpeg",
    )

    args = parser.parse_args()

    if not args.input_dir.exists() or not args.input_dir.is_dir():
        print(f"Input directory does not exist: {args.input_dir}", file=sys.stderr)
        return 1

    if args.output_dir.resolve() == args.input_dir.resolve():
        print("Output directory must be different from input directory", file=sys.stderr)
        return 1

    files = collect_mp4_files(args.input_dir, args.recursive)
    if not files:
        print("No MP4 files found to process.")
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)

    setfile_path = shutil.which("SetFile")
    if setfile_path is None and not args.dry_run:
        print(
            "Warning: SetFile not found. Creation timestamps will not be preserved.",
            file=sys.stderr,
        )

    for src in files:
        if args.recursive:
            relative = src.relative_to(args.input_dir)
            dest = args.output_dir / relative
        else:
            dest = args.output_dir / src.name

        process_file(args.ffmpeg, src, dest, args.output_dir, setfile_path, args.dry_run)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
