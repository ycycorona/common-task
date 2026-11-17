#!/usr/bin/env python3
"""Self-checks for agent_sessions.py. Run: python3 agent-sessions/test_agent_sessions.py"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import agent_sessions as a  # pyright: ignore[reportImplicitRelativeImport]

FAILS = []


def check(name, fn):
    try:
        fn()
        print(f"ok   {name}")
    except AssertionError as exc:
        FAILS.append(name)
        print(f"FAIL {name}: {exc}")


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def test_cwd_dir():
    cwd = "/Users/x/Code/pro_shell_for_agents"
    assert a.cwd_dir("claude", cwd) == Path.home() / ".claude/projects/-Users-x-Code-pro-shell-for-agents"
    assert a.cwd_dir("pi", cwd) == Path.home() / ".pi/agent/sessions/--Users-x-Code-pro_shell_for_agents--"
    assert a.cwd_dir("claude", "/Users/x/Code/sub-pipe") == Path.home() / ".claude/projects/-Users-x-Code-sub-pipe"
    assert a.cwd_dir("codex", cwd) is None


def test_claude_usage_skips_zero_turn():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_jsonl(Path(tmp) / "s.jsonl", [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "message": {"role": "assistant", "model": "claude-opus-5-5", "usage": {
                "input_tokens": 2, "cache_read_input_tokens": 269203,
                "cache_creation_input_tokens": 343, "output_tokens": 1372}}},
            {"type": "assistant", "message": {"role": "assistant", "usage": {
                "input_tokens": 0, "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0, "output_tokens": 0}}},
        ])
        assert a.probe_usage("claude", str(path)) == (270920, "claude-opus-5-5", None)


def test_pi_usage():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_jsonl(Path(tmp) / "s.jsonl", [
            {"type": "message", "message": {"role": "assistant", "model": "magpie/auto/x", "usage": {
                "input": 502, "output": 220, "cacheRead": 117120, "cacheWrite": 0}}},
        ])
        assert a.probe_usage("pi", str(path)) == (117842, "magpie/auto/x", None)


def test_codex_usage_and_model():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_jsonl(Path(tmp) / "r.jsonl", [
            {"type": "turn_context", "payload": {"model": "gpt-6.1-sol"}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": {"input_tokens": 98806, "output_tokens": 124},
                "model_context_window": 475000}}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": {"input_tokens": 0, "output_tokens": 0}}}},
        ])
        assert a.probe_usage("codex", str(path)) == (98930, "gpt-6.1-sol", 475000)


def test_titles_skip_injected_context():
    with tempfile.TemporaryDirectory() as tmp:
        claude = write_jsonl(Path(tmp) / "c.jsonl", [
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "meta"}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": " 真的问题 "}]}},
        ])
        pi = write_jsonl(Path(tmp) / "p.jsonl", [
            {"type": "session", "id": "abc", "cwd": "/x"},
            {"type": "message", "message": {"role": "user", "content": [
                {"type": "text", "text": "# AGENTS.md instructions"}]}},
            {"type": "message", "message": {"role": "user", "content": [
                {"type": "text", "text": "第二个问题"}]}},
        ])
        codex = write_jsonl(Path(tmp) / "r.jsonl", [
            {"type": "session_meta", "payload": {"id": "s1", "cwd": "/x"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": "[Image: a.png]"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": "codex 提问"}]}},
        ])
        assert a.title_of("claude", str(claude)) == "真的问题"
        assert a.title_of("pi", str(pi)) == "第二个问题"
        assert a.title_of("codex", str(codex)) == "codex 提问"


def test_discover_and_rows():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        cwd = str((home / "demo").resolve())   # main() canonicalises the path
        Path(cwd).mkdir()
        claude_dir = a.cwd_dir("claude", cwd)
        assert claude_dir is not None
        write_jsonl(home / claude_dir.relative_to(Path.home()) / "one.jsonl", [
            {"type": "user", "cwd": cwd, "message": {"role": "user", "content": "claude 问题"}},
            {"type": "assistant", "message": {"role": "assistant", "model": "m", "usage": {
                "input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 0}}},
        ])
        pi_dir = a.cwd_dir("pi", cwd)
        assert pi_dir is not None
        write_jsonl(home / pi_dir.relative_to(Path.home()) / "2026-01-01T00-00-00-000Z_01a1.jsonl", [
            {"type": "session", "id": "01a1", "cwd": cwd},
            {"type": "message", "message": {"role": "user", "content": "pi 问题"}},
            {"type": "message", "message": {"role": "assistant", "model": "magpie/auto/y", "usage": {
                "input": 5, "output": 5}}},
        ])
        write_jsonl(home / ".codex/sessions/2026/01/01/rollout-2026-01-01T00-00-00-01a2.jsonl", [
            {"type": "session_meta", "payload": {"id": "01a2", "cwd": cwd}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": "codex 问题"}]}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": {"input_tokens": 400, "output_tokens": 100},
                "model_context_window": 1000}}},
        ])
        # Another cwd must not leak in.
        other = str((home / "other").resolve())
        Path(other).mkdir()
        other_dir = a.cwd_dir("pi", other)
        assert other_dir is not None
        write_jsonl(home / other_dir.relative_to(Path.home()) / "t_01a3.jsonl", [
            {"type": "session", "id": "01a3", "cwd": other},
            {"type": "message", "message": {"role": "assistant", "usage": {"input": 1}}},
        ])
        original = a.HOME
        a.HOME = home
        try:
            found = a.discover(cwd)
            assert sorted(s["agent"] for s in found) == ["claude", "codex", "pi"], found
            total, entries = a.rows(cwd, 10)
            assert total == 3
            by_agent = {entry["agent"]: entry for entry in entries}
            assert by_agent["claude"]["used"] == 100, by_agent["claude"]
            assert by_agent["pi"]["used"] == 10, by_agent["pi"]
            assert by_agent["codex"]["used"] == 500 and by_agent["codex"]["window"] == 1000
            assert by_agent["codex"]["title"] == "codex 问题"
            assert a.main([cwd]) == 0
            assert a.main([other]) == 0
            assert a.main([str(home / "nope")]) == 2
            empty = home / "empty"
            assert a.main([str(empty)]) == 2
            empty.mkdir()
            assert a.main([str(home / "empty")]) == 1
        finally:
            a.HOME = original


def test_formatting():
    assert a.tokens(9999) == "9999"
    assert a.tokens(84973) == "85k"
    assert a.tokens(1048000) == "1048k"
    assert a.fit("查看一下现在的MCP服务里边的Chrome DevTools", 10) == "查看一下现…"
    assert a.width("abc") == 3 and a.width("中文") == 4
    assert a.pad("中文", 6) == "中文  "
    assert a.pad("7", 3, "right") == "  7"
    text = a.render("/x", [{"time": "01-01 00:00", "agent": "pi", "used": None, "window": None,
                            "model": "", "id": "01a1", "title": ""}], 1)
    assert "/x  (1 个会话，列出最近 1 个)" in text
    assert "01a1" in text and "-" in text


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            check(name, fn)
    print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
