#!/usr/bin/env python3
"""Antigravity (agy) account refresh and process manager.

Inspects running `agy` processes (locally and over SSH on remote hosts), matches
them with log files under `~/.gemini/antigravity-cli/log/`, and terminates outdated
`agy acp` processes that still hold old account credentials in memory after switching
accounts.

Why this is needed:
  `agy acp` reads credentials once at startup and caches them in memory.
  When an account is switched in Orca / Antigravity, running `agy acp` processes
  continue using the old account tokens until killed. Once terminated with SIGTERM,
  Orca automatically restarts a fresh `agy acp` process on the next interaction,
  which loads the new account credentials and resumes the conversation seamlessly.

Safety guardrails:
  - Default is DRY-RUN mode (read-only inspection, zero processes killed).
  - Explicit `--apply` flag is required to terminate processes.
  - Automatically checks local PPID ancestor chain to avoid terminating the
    caller's own parent `agy acp` process.
  - Double-checks `ps -p <pid> -o pid=,command=` immediately prior to `SIGTERM`
    to ensure the PID has not been recycled by the OS.
  - Sends SIGTERM, waits 1-2 seconds, and verifies exit status.
  - Skips `agy remote-control serve` by default (unless `--include-remote-control`).
  - Never accesses credentials, tokens, or keychain data; only reads non-sensitive
    log lines and `ps` process lists.

Usage:
  python3 agy-refresh/agy_refresh.py                     # Local dry-run preview
  python3 agy-refresh/agy_refresh.py --apply             # Local terminate old accounts
  python3 agy-refresh/agy_refresh.py --host free-arm     # Remote host dry-run
  python3 agy-refresh/agy_refresh.py --host free-arm --apply
  python3 agy-refresh/agy_refresh.py --local --host free-arm  # Local + remote
  python3 agy-refresh/agy_refresh.py --all-acp           # Preview killing all ACPs
  python3 agy-refresh/agy_refresh.py --current-email foo@example.com  # Override email
  python3 agy-refresh/agy_refresh.py --json              # Output as JSON
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

DEFAULT_LOG_DIR = "~/.gemini/antigravity-cli/log"

# Regexes for parsing logs
RE_AUTH_EMAIL = re.compile(r"applyAuthResult:\s*email=([^,\s]+)")
RE_SESSION_RESUME = re.compile(
    r"Loading/Resuming session:\s*([0-9a-fA-F-]+)(?:,\s*cwd:\s*(\S+))?"
)
RE_CREATED_CONV = re.compile(r"Created conversation\s+([0-9a-fA-F-]+)")
RE_SESSION_UPDATE = re.compile(
    r"(?:Sending\s+)?session_info_update for session\s+([0-9a-fA-F-]+)"
)
RE_SESSION_SWITCH = re.compile(r"Switching model in session\s+([0-9a-fA-F-]+)")
RE_429_QUOTA = re.compile(
    r"RESOURCE_EXHAUSTED|\(code 429\)|Individual quota reached", re.I
)
RE_RESETS_IN = re.compile(r"Resets in\s+([0-9a-zA-Z]+)", re.I)


@dataclass
class ProcessInfo:
    pid: int
    ppid: int
    etime: str
    command: str
    category: str  # "acp", "remote-control", "other_agy"
    email: str | None = None
    sessions: list[str] | None = None
    cwd: str | None = None
    has_429: bool = False
    quota_message: str | None = None
    log_file: str | None = None
    is_ancestor: bool = False
    action: str = "KEEP"  # "KILL", "KEEP", "SKIP"
    status: str = ""
    result: dict[str, Any] | None = None


@dataclass
class HostReport:
    host: str
    latest_email: str | None = None
    latest_email_source: str | None = None
    processes: list[ProcessInfo] = field(default_factory=list)
    error: str | None = None


def parse_ps_line(line: str) -> dict[str, Any] | None:
    """Parse one line from `ps -A -o pid=,ppid=,etime=,command=`.

    Compatible with both BSD ps (macOS) and GNU ps (Linux).
    """
    parts = line.strip().split(None, 3)
    if len(parts) < 4:
        return None
    if not parts[0].isdigit() or not parts[1].isdigit():
        return None
    return {
        "pid": int(parts[0]),
        "ppid": int(parts[1]),
        "etime": parts[2],
        "command": parts[3],
    }


def classify_command(cmd: str) -> tuple[str, str]:
    """Classify process command line into agy category.

    Returns:
      (category, executable)
      category is one of: "acp", "remote-control", "other_agy", "not_agy"
    """
    parts = cmd.split()
    if not parts:
        return ("not_agy", "")

    # Handle wrapper prefixes like /usr/bin/env agy
    base = os.path.basename(parts[0])
    if base in ("env",) and len(parts) > 1:
        parts = parts[1:]
        base = os.path.basename(parts[0])

    # Check if this executable is agy
    if base != "agy" and not base.startswith("agy."):
        return ("not_agy", "")

    args = parts[1:]
    if any(a == "acp" for a in args):
        return ("acp", parts[0])
    if any(a == "remote-control" for a in args):
        return ("remote-control", parts[0])
    return ("other_agy", parts[0])


def get_ancestor_pids(
    start_pid: int,
    ps_runner: Callable[[list[str]], str] | None = None,
) -> list[int]:
    """Climb PPID chain from start_pid up to 1 to find all ancestors."""
    ancestors: list[int] = []
    curr = start_pid
    visited = {curr}

    while curr > 1:
        try:
            if ps_runner:
                out = ps_runner(["ps", "-p", str(curr), "-o", "ppid="])
            else:
                res = subprocess.run(
                    ["ps", "-p", str(curr), "-o", "ppid="],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                out = res.stdout
            out = out.strip()
            if not out or not out.isdigit():
                break
            ppid = int(out)
            if ppid in visited or ppid < 1:
                break
            ancestors.append(ppid)
            visited.add(ppid)
            curr = ppid
        except Exception:
            break

    return ancestors


def find_latest_account_from_logs(log_dir: str) -> tuple[str | None, str | None]:
    """Scan newest logs in log_dir for the most recent applyAuthResult email."""
    expanded = os.path.expanduser(log_dir)
    if not os.path.isdir(expanded):
        return (None, None)

    try:
        entries = [
            os.path.join(expanded, f)
            for f in os.listdir(expanded)
            if (f.startswith("cli-") or f.startswith("acp-server-"))
            and f.endswith(".log")
        ]
        # Sort by mtime descending (newest first)
        entries.sort(
            key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
            reverse=True,
        )
    except Exception:
        return (None, None)

    # Check top 50 newest log files
    for path in entries[:50]:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fp:
                # Read lines to find the last applyAuthResult in this file
                last_email = None
                for line in fp:
                    m = RE_AUTH_EMAIL.search(line)
                    if m:
                        last_email = m.group(1).strip()
                if last_email:
                    return (last_email, os.path.basename(path))
        except Exception:
            continue

    return (None, None)


def find_acp_log_for_pid(log_dir: str, pid: int) -> str | None:
    """Find matching acp-server-*-<PID>.log in log_dir (newest if multiple)."""
    expanded = os.path.expanduser(log_dir)
    if not os.path.isdir(expanded):
        return None

    prefix = "acp-server-"
    suffix = f"-{pid}.log"
    matched: list[str] = []
    try:
        for f in os.listdir(expanded):
            if f.startswith(prefix) and f.endswith(suffix):
                matched.append(os.path.join(expanded, f))
    except Exception:
        return None

    if not matched:
        return None

    matched.sort(
        key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
        reverse=True,
    )
    return matched[0]


def parse_acp_log(log_path: str) -> dict[str, Any]:
    """Parse PID log file to extract email, sessions, cwd, and 429 status."""
    email: str | None = None
    sessions: list[str] = []
    seen_sessions: set[str] = set()
    cwd: str | None = None
    has_429: bool = False
    quota_msg: str | None = None

    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as fp:
            for line in fp:
                # 1. Email
                m_email = RE_AUTH_EMAIL.search(line)
                if m_email:
                    email = m_email.group(1).strip()

                # 2. Session / cwd
                m_res = RE_SESSION_RESUME.search(line)
                if m_res:
                    s_id = m_res.group(1)
                    if s_id not in seen_sessions:
                        seen_sessions.add(s_id)
                        sessions.append(s_id)
                    if m_res.group(2):
                        cwd = m_res.group(2)

                m_conv = RE_CREATED_CONV.search(line)
                if m_conv:
                    s_id = m_conv.group(1)
                    if s_id not in seen_sessions:
                        seen_sessions.add(s_id)
                        sessions.append(s_id)

                m_upd = RE_SESSION_UPDATE.search(line)
                if m_upd:
                    s_id = m_upd.group(1)
                    if s_id not in seen_sessions:
                        seen_sessions.add(s_id)
                        sessions.append(s_id)

                m_sw = RE_SESSION_SWITCH.search(line)
                if m_sw:
                    s_id = m_sw.group(1)
                    if s_id not in seen_sessions:
                        seen_sessions.add(s_id)
                        sessions.append(s_id)

                # 3. 429 / Quota
                if RE_429_QUOTA.search(line):
                    has_429 = True
                    m_rst = RE_RESETS_IN.search(line)
                    if m_rst:
                        quota_msg = f"Resets in {m_rst.group(1)}"
                    elif not quota_msg:
                        quota_msg = "RESOURCE_EXHAUSTED (429)"

    except Exception:
        pass

    return {
        "email": email,
        "sessions": sessions,
        "cwd": cwd,
        "has_429": has_429,
        "quota_message": quota_msg,
    }


def terminate_process(
    pid: int,
    ps_runner: Callable[[list[str]], str] | None = None,
    kill_fn: Callable[[int, int], None] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Safely terminate a process with double-check ps and SIGTERM verification."""
    _kill = kill_fn or os.kill
    _sleep = sleep_fn or time.sleep

    # Step 1: Double-check command with ps -p <pid> -o pid=,command=
    try:
        if ps_runner:
            out = ps_runner(["ps", "-p", str(pid), "-o", "pid=,command="])
        else:
            res = subprocess.run(
                ["ps", "-p", str(pid), "-o", "pid=,command="],
                capture_output=True,
                text=True,
                timeout=3,
            )
            out = res.stdout
    except Exception as exc:
        return {
            "verified": False,
            "signal_sent": False,
            "exited": False,
            "message": f"PID {pid} ps 二次检查失败: {exc}",
        }

    lines = [line.strip() for line in out.splitlines() if line.strip()]
    if not lines:
        return {
            "verified": False,
            "signal_sent": False,
            "exited": True,
            "message": f"PID {pid} 已不在运行 (进程已退出)",
        }

    parts = lines[0].split(None, 1)
    if len(parts) < 2 or not parts[0].isdigit():
        return {
            "verified": False,
            "signal_sent": False,
            "exited": False,
            "message": f"PID {pid} 无法解析 ps 输出: {lines[0]}",
        }

    cmd = parts[1]
    cat, _ = classify_command(cmd)
    if cat not in ("acp", "remote-control", "other_agy"):
        return {
            "verified": False,
            "signal_sent": False,
            "exited": False,
            "message": f"PID {pid} 命令行已变更 ({cmd[:40]}...)，安全中止终止",
        }

    # Step 2: Send SIGTERM
    try:
        _kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {
            "verified": True,
            "signal_sent": True,
            "exited": True,
            "message": f"PID {pid} 在发送信号时已退出",
        }
    except PermissionError as exc:
        return {
            "verified": True,
            "signal_sent": False,
            "exited": False,
            "message": f"PID {pid} 权限不足: {exc}",
        }
    except Exception as exc:
        return {
            "verified": True,
            "signal_sent": False,
            "exited": False,
            "message": f"PID {pid} 发送 SIGTERM 异常: {exc}",
        }

    # Step 3: Wait 1-2 seconds and verify exit status
    deadline = time.time() + 2.0
    exited = False
    while time.time() < deadline:
        _sleep(0.3)
        try:
            _kill(pid, 0)
        except ProcessLookupError:
            exited = True
            break
        except PermissionError:
            # Still alive, permission denied to signal 0
            pass
        except Exception:
            break

    if exited:
        return {
            "verified": True,
            "signal_sent": True,
            "exited": True,
            "message": f"PID {pid} 已收到 SIGTERM 并确认退出",
        }
    else:
        return {
            "verified": True,
            "signal_sent": True,
            "exited": False,
            "message": f"PID {pid} 已发送 SIGTERM，但在 2s 内未完全退出",
        }


def inspect_local_host(
    current_email_override: str | None = None,
    log_dir: str = DEFAULT_LOG_DIR,
    apply: bool = False,
    all_acp: bool = False,
    include_remote_control: bool = False,
    ps_output: str | None = None,
    ps_runner: Callable[[list[str]], str] | None = None,
    kill_fn: Callable[[int, int], None] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    self_pid: int | None = None,
) -> HostReport:
    """Inspect local machine's agy processes and logs."""
    report = HostReport(host="local", processes=[])

    # 1. Determine latest email
    if current_email_override:
        latest_email = current_email_override
        source = "cli override"
    else:
        latest_email, source = find_latest_account_from_logs(log_dir)

    report.latest_email = latest_email
    report.latest_email_source = source

    # 2. Get ancestor PIDs
    pid_to_trace = self_pid if self_pid is not None else os.getpid()
    ancestor_pids = set(get_ancestor_pids(pid_to_trace, ps_runner=ps_runner))

    # 3. Get process list from ps
    if ps_output is None:
        try:
            if ps_runner:
                raw_ps = ps_runner(["ps", "-A", "-o", "pid=,ppid=,etime=,command="])
            else:
                res = subprocess.run(
                    ["ps", "-A", "-o", "pid=,ppid=,etime=,command="],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                raw_ps = res.stdout
        except Exception as exc:
            report.error = f"ps 执行失败: {exc}"
            return report
    else:
        raw_ps = ps_output

    agy_procs: list[ProcessInfo] = []
    for line in raw_ps.splitlines():
        parsed = parse_ps_line(line)
        if not parsed:
            continue
        cat, _ = classify_command(parsed["command"])
        if cat in ("acp", "remote-control", "other_agy"):
            proc = ProcessInfo(
                pid=parsed["pid"],
                ppid=parsed["ppid"],
                etime=parsed["etime"],
                command=parsed["command"],
                category=cat,
            )
            agy_procs.append(proc)

    # 4. Process matching and classification
    for proc in agy_procs:
        proc.is_ancestor = proc.pid in ancestor_pids

        # Match log file
        log_file = find_acp_log_for_pid(log_dir, proc.pid)
        if log_file:
            proc.log_file = os.path.basename(log_file)
            details = parse_acp_log(log_file)
            proc.email = details["email"]
            proc.sessions = details["sessions"]
            proc.cwd = details["cwd"]
            proc.has_429 = details["has_429"]
            proc.quota_message = details["quota_message"]

        # Classification
        if proc.is_ancestor:
            proc.action = "SKIP"
            proc.status = "本机自身祖先进程跳过"
        elif proc.category == "remote-control":
            if not include_remote_control:
                proc.action = "SKIP"
                proc.status = "remote-control 默认跳过"
            else:
                if all_acp:
                    proc.action = "KILL"
                    proc.status = "强制清理 remote-control"
                elif proc.email and latest_email and proc.email != latest_email:
                    proc.action = "KILL"
                    proc.status = "旧账号待清理"
                elif proc.email and latest_email and proc.email == latest_email:
                    proc.action = "KEEP"
                    proc.status = "已是最新账号"
                else:
                    proc.action = "SKIP"
                    proc.status = "无法识别日志"
        else:  # acp or other_agy
            if all_acp:
                proc.action = "KILL"
                proc.status = "强制清理 (--all-acp)"
            elif not proc.email:
                proc.action = "SKIP"
                proc.status = "无法识别日志"
            elif latest_email and proc.email != latest_email:
                proc.action = "KILL"
                proc.status = "旧账号待清理"
            elif latest_email and proc.email == latest_email:
                proc.action = "KEEP"
                proc.status = "已是最新账号"
            else:
                proc.action = "SKIP"
                proc.status = "无法识别最新账号"

        # Apply termination if requested and marked KILL
        if apply and proc.action == "KILL":
            proc.result = terminate_process(
                proc.pid,
                ps_runner=ps_runner,
                kill_fn=kill_fn,
                sleep_fn=sleep_fn,
            )

    report.processes = agy_procs
    return report


def inspect_remote_host(
    host: str,
    script_content: str,
    current_email_override: str | None = None,
    log_dir: str = DEFAULT_LOG_DIR,
    apply: bool = False,
    all_acp: bool = False,
    include_remote_control: bool = False,
    timeout: int = 15,
) -> HostReport:
    """Inspect remote host via SSH piping this script to remote python3."""
    report = HostReport(host=host, processes=[])

    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={timeout}",
        host,
        "python3",
        "-",
        "--internal-worker",
        "--log-dir",
        log_dir,
    ]
    if current_email_override:
        cmd.extend(["--current-email", current_email_override])
    if apply:
        cmd.append("--apply")
    if all_acp:
        cmd.append("--all-acp")
    if include_remote_control:
        cmd.append("--include-remote-control")

    try:
        proc = subprocess.run(
            cmd,
            input=script_content,
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
    except subprocess.TimeoutExpired:
        report.error = f"SSH 连接超时 ({timeout}s)"
        return report
    except Exception as exc:
        report.error = f"SSH 执行失败: {exc}"
        return report

    if proc.returncode != 0:
        err_msg = proc.stderr.strip() or f"退出码 {proc.returncode}"
        report.error = f"远端执行错误: {err_msg[:160]}"
        return report

    try:
        data = json.loads(proc.stdout)
        report.latest_email = data.get("latest_email")
        report.latest_email_source = data.get("latest_email_source")
        raw_procs = data.get("processes", [])
        procs: list[ProcessInfo] = []
        for p in raw_procs:
            procs.append(
                ProcessInfo(
                    pid=p["pid"],
                    ppid=p["ppid"],
                    etime=p["etime"],
                    command=p["command"],
                    category=p["category"],
                    email=p.get("email"),
                    sessions=p.get("sessions"),
                    cwd=p.get("cwd"),
                    has_429=p.get("has_429", False),
                    quota_message=p.get("quota_message"),
                    log_file=p.get("log_file"),
                    is_ancestor=p.get("is_ancestor", False),
                    action=p.get("action", "KEEP"),
                    status=p.get("status", ""),
                    result=p.get("result"),
                )
            )
        report.processes = procs
    except Exception as exc:
        report.error = f"远端 JSON 解析失败: {exc} (stdout: {proc.stdout[:100]})"

    return report


def format_report_text(reports: list[HostReport], dry_run: bool) -> str:
    """Format reports into clean terminal text output."""
    lines: list[str] = []
    width = 96
    mode_str = "[DRY-RUN 预览模式]" if dry_run else "[APPLY 实际执行模式]"
    lines.append("=" * width)
    lines.append(f"Antigravity (agy) 账号与进程状态检查 {mode_str}")
    lines.append("=" * width)

    total_procs = 0
    to_kill_count = 0
    killed_count = 0

    for rep in reports:
        lines.append(f"\n[目标主机: {rep.host}]")
        if rep.error:
            lines.append(f"  错误: {rep.error}")
            continue

        email_str = rep.latest_email or "未识别"
        src_str = f" (来源: {rep.latest_email_source})" if rep.latest_email_source else ""
        lines.append(f"最新账号: {email_str}{src_str}")

        procs = rep.processes or []
        if not procs:
            lines.append("  未发现运行中的 agy 进程。")
            continue

        lines.append("")
        header = f"{'PID':<9} {'类型':<6} {'运行时长':<12} {'进程内账号':<26} {'429':<5} {'会话/CWD':<22} {'状态'}"
        lines.append(header)
        lines.append("-" * width)

        for p in procs:
            total_procs += 1
            if p.action == "KILL":
                to_kill_count += 1
                if p.result and p.result.get("exited"):
                    killed_count += 1

            cat_str = "acp" if p.category == "acp" else ("rc" if p.category == "remote-control" else "other")
            email_val = p.email or "-"
            if len(email_val) > 24:
                email_val = email_val[:21] + "..."

            q_str = "是" if p.has_429 else "否"

            # session / cwd representation
            sess_str = "-"
            if p.sessions:
                sess_str = p.sessions[0][:8]
            if p.cwd:
                cwd_base = os.path.basename(p.cwd)
                sess_str = f"{sess_str} ({cwd_base[:10]})"
            elif p.sessions and len(p.sessions) > 1:
                sess_str = f"{sess_str} (+{len(p.sessions)-1})"

            status_str = p.status
            if p.has_429 and "429" not in status_str:
                status_str += " [429]"
            if p.action == "KILL" and dry_run:
                status_str += " [需清理]"

            row = f"{p.pid:<9} {cat_str:<6} {p.etime:<12} {email_val:<26} {q_str:<5} {sess_str:<22} {status_str}"
            lines.append(row)

            # If apply mode and result available, print detail line
            if p.result:
                msg = p.result.get("message", "")
                lines.append(f"    └─ 执行结果: {msg}")

    lines.append("\n" + "=" * width)
    lines.append("统计汇总:")
    lines.append(f"  - 检查主机数: {len(reports)}")
    lines.append(f"  - 发现 agy 进程: {total_procs}")
    if dry_run:
        lines.append(f"  - 待清理旧账号进程: {to_kill_count}")
        lines.append("  - [DRY-RUN] 当前为只读预览模式，未做任何更改。")
        if to_kill_count > 0:
            lines.append("  - 提示: 加上 --apply 参数可直接终止上述待清理进程。")
    else:
        lines.append(f"  - 目标清理进程: {to_kill_count}")
        lines.append(f"  - 成功终止并退出: {killed_count}")
    lines.append("=" * width)

    return "\n".join(lines)


def run_internal_worker(args: argparse.Namespace) -> int:
    """Internal worker invoked on target host over SSH, outputs JSON to stdout."""
    rep = inspect_local_host(
        current_email_override=args.current_email,
        log_dir=args.log_dir,
        apply=args.apply,
        all_acp=args.all_acp,
        include_remote_control=args.include_remote_control,
    )
    data = asdict(rep)
    print(json.dumps(data))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Antigravity (agy) 账号与进程状态检查及切号刷新运维工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--host",
        action="append",
        default=[],
        help="SSH 远端主机 (如 free-arm)，可重复传入多个",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="显式包含本机 (未指定 --host 时默认包含本机)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际执行进程终止 (默认仅只读 dry-run 预览)",
    )
    parser.add_argument(
        "--all-acp",
        action="store_true",
        help="强制清理所有非自身祖先的 agy acp 进程 (即使账号匹配)",
    )
    parser.add_argument(
        "--include-remote-control",
        action="store_true",
        help="包含 agy remote-control serve 进程 (默认跳过)",
    )
    parser.add_argument(
        "--current-email",
        type=str,
        default=None,
        help="手动指定当前最新账号邮箱 (覆盖从日志自动检测的结果)",
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default=DEFAULT_LOG_DIR,
        help=f"Antigravity 日志目录 (默认: {DEFAULT_LOG_DIR})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="SSH 连接超时秒数 (默认: 15s)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式供机器/Agent 解析",
    )
    parser.add_argument(
        "--internal-worker",
        action="store_true",
        help=argparse.SUPPRESS,  # Internal flag for SSH piped workers
    )

    args = parser.parse_args(argv)

    if args.internal_worker:
        return run_internal_worker(args)

    # Determine targets
    targets: list[str] = []
    if args.local or not args.host:
        targets.append("local")
    for h in args.host:
        if h not in targets:
            targets.append(h)

    # Read current script text for SSH piping
    script_path = Path(__file__).resolve()
    try:
        script_content = script_path.read_text(encoding="utf-8")
    except Exception as exc:
        sys.stderr.write(f"错误: 无法读取脚本自身文件 ({script_path}): {exc}\n")
        return 1

    reports: list[HostReport] = []
    for tgt in targets:
        if tgt == "local":
            rep = inspect_local_host(
                current_email_override=args.current_email,
                log_dir=args.log_dir,
                apply=args.apply,
                all_acp=args.all_acp,
                include_remote_control=args.include_remote_control,
            )
        else:
            rep = inspect_remote_host(
                host=tgt,
                script_content=script_content,
                current_email_override=args.current_email,
                log_dir=args.log_dir,
                apply=args.apply,
                all_acp=args.all_acp,
                include_remote_control=args.include_remote_control,
                timeout=args.timeout,
            )
        reports.append(rep)

    if args.json:
        out = {
            "dry_run": not args.apply,
            "reports": [asdict(r) for r in reports],
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        text = format_report_text(reports, dry_run=not args.apply)
        print(text)

    # Exit code: 0 if no unhandled errors
    has_error = any(r.error is not None for r in reports)
    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())
