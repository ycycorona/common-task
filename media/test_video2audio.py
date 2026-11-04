#!/usr/bin/env python3
"""Self-checks for media/video2audio.sh and its three wrappers.

Guards the differences that used to live in three near-identical scripts:
ffmpeg arguments per profile, output extension, and hidden-file handling
(aac/opus skip dotfiles, flac does not).

Run: python3 media/test_video2audio.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

MEDIA = Path(__file__).resolve().parent
WRAPPERS = {"aac": "video2aac.sh", "flac": "video2flac.sh", "opus": "video2opus.sh"}
EXPECTED_ARGS = {
    "aac": ["-map", "0:a:0", "-c:a", "aac", "-b:a", "256k"],
    "flac": ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac"],
    "opus": ["-map", "0:a:0", "-ac", "1", "-c:a", "libopus", "-b:a", "32k",
             "-application", "voip"],
}
FFMPEG_SHIM = """#!/usr/bin/env bash
printf '%s\\n' "$@" >> "$FFMPEG_LOG"
in=""; prev=""
for a in "$@"; do [ "$prev" = "-i" ] && in="$a"; prev="$a"; done
out="${!#}"
[ -f "$in" ] && cp "$in" "$out"
exit 0
"""

failures: list[str] = []


def check(name, fn):
    try:
        fn()
        print(f"ok   {name}")
    except AssertionError as exc:
        failures.append(name)
        print(f"FAIL {name}: {exc}")


class Sandbox:
    """Temp workdir + a fake ffmpeg that records its argv."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="video2audio-"))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        shim = self.bin / "ffmpeg"
        shim.write_text(FFMPEG_SHIM)
        shim.chmod(0o755)
        self.log = self.tmp / "ffmpeg.log"

    def video(self, name: str, content: str = "fake") -> Path:
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def run(self, wrapper: str, *args: str, cwd: Path | None = None):
        env = os.environ.copy()
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        env["FFMPEG_LOG"] = str(self.log)
        return subprocess.run(["bash", str(MEDIA / wrapper), *args],
                              capture_output=True, text=True,
                              cwd=str(cwd or self.tmp), env=env)

    def args_seen(self) -> list[str]:
        if not self.log.exists():
            return []
        return self.log.read_text().splitlines()


def test_profile_args_and_extension():
    for profile, wrapper in WRAPPERS.items():
        box = Sandbox()
        src = box.video("clip.mp4")
        result = box.run(wrapper, str(src))
        assert result.returncode == 0, (profile, result.stdout, result.stderr)
        out = box.tmp / f"clip.{profile}"
        assert out.exists(), f"{profile}: 缺输出 {out}"
        seen = box.args_seen()
        expected = EXPECTED_ARGS[profile]
        missing = [a for a in expected if a not in seen]
        assert not missing, f"{profile}: ffmpeg 参数缺 {missing}，实际 {seen}"
        assert seen[-1].endswith(f"clip.{profile}"), f"{profile}: 输出不是最后一个参数: {seen}"
        assert "-nostdin" in seen, f"{profile}: 缺 -nostdin"


def test_hidden_files():
    for profile, wrapper in WRAPPERS.items():
        box = Sandbox()
        box.video(".hidden.mp4")
        result = box.run(wrapper, str(box.tmp))
        assert result.returncode == 0, (profile, result.stderr)
        hidden_out = box.tmp / f".hidden.{profile}"
        if profile == "flac":
            assert hidden_out.exists(), "flac 历史上不跳过隐藏文件，这里被跳过了"
        else:
            assert not hidden_out.exists(), f"{profile} 应该跳过隐藏文件"


def test_directory_batch():
    for profile, wrapper in WRAPPERS.items():
        box = Sandbox()
        box.video("a.mp4")
        box.video("b.mkv")
        box.video(".skip.mp4")
        result = box.run(wrapper, str(box.tmp))
        assert result.returncode == 0, (profile, result.stderr)
        # flac 历史上不过滤隐藏文件，所以 .skip 也会被转换
        want = ["a." + profile, "b." + profile]
        if profile == "flac":
            want = sorted([".skip." + profile] + want)
        produced = sorted(p.name for p in box.tmp.glob(f"*.{profile}"))
        assert produced == want, f"{profile}: 产出 {produced}，期望 {want}"
        expect_ratio = f"开始处理：{len(want)}/{len(want)}"
        assert expect_ratio in result.stdout, result.stdout


def test_existing_output_is_skipped():
    box = Sandbox()
    src = box.video("dup.mp4")
    (box.tmp / "dup.aac").write_text("existing")
    result = box.run("video2aac.sh", str(src))
    assert result.returncode == 0, result.stderr
    assert "已存在，跳过" in result.stdout, result.stdout
    assert box.args_seen() == [], "已存在时不应该再调 ffmpeg"


def test_unusual_suffix_still_converts():
    box = Sandbox()
    src = box.video("weird.xyz")
    result = box.run("video2aac.sh", str(src))
    assert result.returncode == 0, result.stderr
    assert "看起来不是常见视频格式" in result.stdout, result.stdout
    assert (box.tmp / "weird.aac").exists()


def test_usage_and_missing_path():
    for profile, wrapper in WRAPPERS.items():
        box = Sandbox()
        result = box.run(wrapper)
        assert result.returncode == 1, (profile, result.returncode)
        assert wrapper in (result.stdout + result.stderr), \
            f"{profile}: 用法提示应显示 {wrapper}，实际 {result.stdout!r}"
        result = box.run(wrapper, str(box.tmp / "nope.mp4"))
        assert result.returncode == 1, (profile, result.returncode)
        assert "找不到文件或目录" in result.stdout, result.stdout


def test_unknown_profile_rejected():
    box = Sandbox()
    result = box.run("video2audio.sh", "-f", "mp3", str(box.video("x.mp4")))
    assert result.returncode == 2, result.returncode
    assert "未知 profile" in (result.stdout + result.stderr), result.stdout


def test_wrappers_delegate():
    for profile, wrapper in WRAPPERS.items():
        text = (MEDIA / wrapper).read_text()
        assert f"-f {profile}" in text, f"{wrapper} 没传 -f {profile}"
        assert "video2audio.sh" in text, f"{wrapper} 没委托给 video2audio.sh"
        assert os.access(MEDIA / wrapper, os.X_OK), f"{wrapper} 没有可执行位"


for name, fn in list(globals().items()):
    if name.startswith("test_"):
        check(name, fn)

if failures:
    print(f"\n{len(failures)} 项失败: {', '.join(failures)}")
    sys.exit(1)
print("\n全部检查通过")
