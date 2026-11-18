#!/usr/bin/env python3
"""List the agent sessions of one path, newest first, with their context usage.

Answers "这个目录下有哪些 Agent 会话、各自吃了多少上下文": reads the agents'
own session files, so the numbers are the latest turn (no index lag — the
`agentsview` archive runs 1-2 minutes behind and only keeps the peak).

Session stores (verified on this Mac, 2026-11-17):

  claude  ~/.claude/projects/<cwd, every non-alnum -> ->/<session>.jsonl
  pi      ~/.pi/agent/sessions/<cwd wrapped in dashes>/<ts>_<session>.jsonl
  codex   ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl        (cwd in line 1)

Directory names encode the cwd lossily (a dash may have been a slash), so the
requested path is encoded the same way instead of decoding them; codex has no
cwd directory, so its first line is parsed.

Context used = input (cache included) + output of the last non-empty turn —
zero-usage turns from interrupted requests are skipped. The window comes from
codex's own `model_context_window`, pi's models.json, or claude's cached model
catalog; an unknown model prints `?` instead of a percentage.

The 活 column means "an agent process runs in this directory (macOS ps + lsof)
and this session was written in the last 5 minutes". A fresh file alone is not
enough, and a process cannot be pinned to one session when several share the
directory — T3-driven pi even spawns a new process per turn.

Usage:
  python3 agent-sessions/agent_sessions.py
  python3 agent-sessions/agent_sessions.py ~/Code/sub-pipe
  python3 agent-sessions/agent_sessions.py -n 20
  python3 agent-sessions/agent_sessions.py --json ~/Code/sub-pipe

Exit codes: 0 listed, 1 no session for that path, 2 not a directory.
Agents and scripts read the output with `--json` (one object on stdout).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import TypedDict

HOME = Path.home()
# The first user turn sits up to ~113KB into a pi session and further into a
# codex one (a 23KB session_meta comes first); only listed rows pay this cost.
HEAD_BYTES = {"claude": 64 * 1024, "pi": 256 * 1024, "codex": 1024 * 1024}
TAIL_BYTES = 400 * 1024
TAIL_LINES = 400
DEFAULT_LIMIT = 10
LIVE_WINDOW = 300  # seconds since the last write, for "still running"
AGENT_BINARIES = {"pi", "claude", "codex"}
TITLE_WIDTH = 40
# Injected context is not a session title; skip it and take the next message.
TITLE_SKIP_PREFIX = ("#", "<", "[", "{")


def cwd_dir(agent, cwd):
    """The session directory an agent uses for `cwd` (codex has none)."""
    stripped = cwd.rstrip("/") or "/"
    if agent == "claude":
        return HOME / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", stripped)
    if agent == "pi":
        encoded = (stripped + "/").replace("/", "-")
        return HOME / ".pi" / "agent" / "sessions" / f"-{encoded}-"
    return None


def json_lines(lines):
    """Parsed records of a JSONL slice; a truncated first/last line is dropped."""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def head_lines(path, agent):
    with open(path, "rb") as fh:
        raw = fh.read(HEAD_BYTES[agent])
    return raw.decode("utf-8", "replace").splitlines()


def tail_lines(path):
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        fh.seek(max(0, fh.tell() - TAIL_BYTES))
        raw = fh.read()
    return raw.decode("utf-8", "replace").splitlines()[-TAIL_LINES:]


def agent_pids(ps_output):
    """PIDs of running agent processes, from `ps -axo pid=,command=` output.

    Matches the first word's basename, so a pi whose kernel name is `node`
    still counts, while the Claude desktop app (`.../MacOS/Claude`) does not.
    """
    pids = []
    for line in ps_output.splitlines():
        pid, _, command = line.strip().partition(" ")
        if command and os.path.basename(command.split()[0]) in AGENT_BINARIES:
            pids.append(pid)
    return pids


def cwds_from_lsof(lsof_output):
    """The `n` rows of `lsof ... -d cwd -Fn` output."""
    return {line[1:] for line in lsof_output.splitlines() if line.startswith("n")}


def live_cwds():
    """cwd of every running claude / pi / codex process (macOS: ps + lsof)."""
    try:
        listing = subprocess.run(["ps", "-axo", "pid=,command="],
                                 capture_output=True, text=True, check=False)
        pids = agent_pids(listing.stdout)
        if not pids:
            return set()
        query = ["lsof", "-a"]
        for pid in pids:
            query += ["-p", pid]
        found = subprocess.run(query + ["-d", "cwd", "-Fn"],
                               capture_output=True, text=True, check=False)
    except OSError:  # ps / lsof missing: report nothing instead of failing
        return set()
    return cwds_from_lsof(found.stdout)


def text_of(content):
    """Plain text of a message content field (a string or a list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                part = block.get("text") or block.get("thinking")
                if isinstance(part, str):
                    parts.append(part)
        return "".join(parts)
    return ""


def usable_title(text):
    """A one-line title from a message, or "" for injected context."""
    flat = " ".join(text.split())
    return "" if flat[:1] in TITLE_SKIP_PREFIX else flat


def user_text(agent, record):
    """The user message text in a record, or "" when it is not one."""
    if agent == "codex":
        payload = record.get("payload") or {}
        if record.get("type") == "response_item" and payload.get("role") == "user":
            return text_of(payload.get("content"))
        return ""
    message = record.get("message")
    if isinstance(message, dict) and message.get("role") == "user":
        text = text_of(message.get("content"))
        # claude marks injected context with isMeta instead of a prefix.
        return "" if record.get("isMeta") else text
    return ""


def title_of(agent, path):
    """First real user message of a session, as a one-line title."""
    for record in json_lines(head_lines(path, agent)):
        title = usable_title(user_text(agent, record))
        if title:
            return title
    return ""


def codex_meta(path):
    """(cwd, session id) from the first line of a codex rollout."""
    with open(path, "rb") as fh:
        raw = fh.readline()
    try:
        record = json.loads(raw)
    except json.JSONDecodeError:
        return None, None
    payload = record.get("payload") or {}
    return payload.get("cwd"), payload.get("id") or payload.get("session_id")


def probe_usage(agent, path):
    """(context tokens, model, window) of the last non-empty turn in a session."""
    found = model = None
    for record in reversed(list(json_lines(tail_lines(path)))):
        if agent == "codex":
            payload = record.get("payload") or {}
            if record.get("type") == "turn_context" and isinstance(payload.get("model"), str):
                model = model or payload["model"]
            if payload.get("type") != "token_count":
                continue
            info = payload.get("info") or {}
            last = info.get("last_token_usage") or {}
            used = (last.get("input_tokens") or 0) + (last.get("output_tokens") or 0)
            # The model name sits in an older turn_context, so keep scanning.
            if used and found is None:
                found = (used, info.get("model_context_window"))
                if model:
                    break
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        if agent == "claude":
            keys = ("input_tokens", "cache_read_input_tokens",
                    "cache_creation_input_tokens", "output_tokens")
        else:  # pi
            keys = ("input", "cacheRead", "cacheWrite", "output")
        used = sum(usage.get(key) or 0 for key in keys)
        if used:
            return used, message.get("model"), None
    if found:
        return found[0], model, found[1]
    return None, model, None


def _collect_windows(node, out):
    if isinstance(node, dict):
        model, limit = node.get("id"), node.get("hard_limit")
        if isinstance(model, str) and isinstance(limit, int) and limit > 0:
            out.setdefault(model, limit)
        for value in node.values():
            _collect_windows(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_windows(value, out)


def claude_windows():
    """model id -> context window, from claude's cached model catalog."""
    out: dict[str, int] = {}
    for path in glob.glob(str(HOME / ".claude" / "cache" / "model-catalog" / "*.json")):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        _collect_windows(data, out)
    return out


def pi_windows():
    """model id -> context window, from pi's models.json."""
    out: dict[str, int] = {}
    try:
        data = json.loads((HOME / ".pi" / "agent" / "models.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    for provider in (data.get("providers") or {}).values():
        if not isinstance(provider, dict):
            continue
        for model in provider.get("models") or []:
            if not isinstance(model, dict):
                continue
            model_id, window = model.get("id"), model.get("contextWindow")
            if isinstance(model_id, str) and isinstance(window, int) and window > 0:
                out.setdefault(model_id, window)
    return out


def discover(cwd):
    """Every claude / pi / codex session whose own cwd is exactly `cwd`."""
    found = []
    for agent in ("claude", "pi"):
        directory = cwd_dir(agent, cwd)
        if directory is None:  # both agents have one; codex is handled below
            continue
        for path in glob.glob(str(directory / "*.jsonl")):
            stem = Path(path).stem
            session_id = stem.rsplit("_", 1)[-1] if agent == "pi" else stem
            found.append({"agent": agent, "path": path, "id": session_id,
                          "mtime": os.path.getmtime(path)})
    for path in glob.glob(str(HOME / ".codex" / "sessions" / "*" / "*" / "*" / "*.jsonl")):
        session_cwd, session_id = codex_meta(path)
        if session_cwd != cwd:
            continue
        found.append({"agent": "codex", "path": path,
                      "id": session_id or Path(path).stem,
                      "mtime": os.path.getmtime(path)})
    found.sort(key=lambda session: session["mtime"], reverse=True)
    return found


class Entry(TypedDict):
    """One rendered row: a session's last activity and context usage."""

    time: str
    live: bool
    agent: str
    used: int | None
    window: int | None
    model: str
    id: str
    file: str
    title: str


def rows(cwd, limit) -> tuple[int, list[Entry]]:
    """(sessions found, the latest `limit` of them with their context usage)."""
    found = discover(cwd)
    claude_window = claude_windows()
    pi_window = pi_windows()
    running = live_cwds()
    out: list[Entry] = []
    for session in found[:limit]:
        used, model, window = probe_usage(session["agent"], session["path"])
        if session["agent"] == "claude":
            window = window or claude_window.get(model or "")
        elif session["agent"] == "pi":
            window = window or pi_window.get(model or "")
        out.append({
            "time": time.strftime("%m-%d %H:%M", time.localtime(session["mtime"])),
            "live": cwd in running and time.time() - session["mtime"] < LIVE_WINDOW,
            "agent": session["agent"],
            "used": used,
            "window": window,
            "model": (model or "").split("/")[-1],
            "id": session["id"],
            "file": session["path"],
            "title": title_of(session["agent"], session["path"]),
        })
    return len(found), out


def tokens(count):
    return str(count) if count < 10_000 else f"{count / 1000:.0f}k"


def width(text):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def fit(text, columns):
    """Truncate `text` to `columns` display columns."""
    kept, used = "", 0
    for ch in text:
        step = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if used + step > columns:
            return kept + "…"
        kept += ch
        used += step
    return kept


def pad(text, columns, align="left"):
    space = " " * max(0, columns - width(text))
    return space + text if align == "right" else text + space


def render(target: str, entries: list[Entry], total: int) -> str:
    table = [["最后活跃", "活", "Agent", "上下文", "占比", "模型", "会话"]]
    for entry in entries:
        if entry["used"] is None:
            usage, share = "?", "-"
        else:
            window = entry["window"]
            usage = tokens(entry["used"]) if not window else f"{tokens(entry['used'])}/{tokens(window)}"
            share = f"{round(entry['used'] / window * 100)}%" if window else "-"
        table.append([entry["time"], "✓" if entry["live"] else "·", entry["agent"],
                      usage, share, entry["model"] or "?", f"{entry['id'][:8]} {entry['title']}"])
    columns = [max(width(row[i]) for row in table) for i in range(6)] + [TITLE_WIDTH]
    lines = [f"{target}  ({total} 个会话，列出最近 {len(entries)} 个)", ""]
    for index, row in enumerate(table):
        cells = [pad(fit(cell, columns[i]), columns[i]) for i, cell in enumerate(row[:-1])]
        cells.append(fit(row[-1], columns[-1]))
        lines.append("  ".join(cells).rstrip())
        if index == 0:
            lines.append("  ".join("-" * column for column in columns))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="按路径列出 Agent 会话与上下文用量")
    parser.add_argument("path", nargs="?", default=".", help="目录，默认当前目录")
    parser.add_argument("-n", "--limit", type=int, default=DEFAULT_LIMIT, help="列出几条，默认 10")
    parser.add_argument("--json", action="store_true",
                        help="输出 JSON 而不是表格；Agent 或脚本读取结果时加这个参数")
    args = parser.parse_args(argv)

    target = Path(args.path).expanduser().resolve()
    if not target.is_dir():
        print(f"不是目录: {target}", file=sys.stderr)
        return 2
    cwd = str(target)
    total, entries = rows(cwd, max(1, args.limit))
    if args.json:
        print(json.dumps({"path": cwd, "total": total, "sessions": entries}, ensure_ascii=False))
    elif entries:
        print(render(cwd, entries, total))
    if not entries:
        print(f"{cwd} 下没有找到 claude / pi / codex 的会话", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
