#!/usr/bin/env python3
"""Self-checks for agy_refresh.py. Run: python3 agy-refresh/test_agy_refresh.py"""

import os
import signal
import sys
import tempfile
import time
from pathlib import Path

# Add script dir to sys.path
sys.path.insert(0, str(Path(__file__).parent))

import agy_refresh as r  # pyright: ignore[reportImplicitRelativeImport]

FAILS = []


def check(name, fn):
    try:
        fn()
        print(f"ok   {name}")
    except Exception as exc:
        FAILS.append(name)
        print(f"FAIL {name}: {exc}")


def test_parse_ps_line_bsd_and_gnu():
    # macOS BSD format
    bsd_line = " 12884 12883       03:00 /Users/ycyber/.local/bin/agy acp --dangerously-skip-permissions"
    p1 = r.parse_ps_line(bsd_line)
    assert p1 is not None
    assert p1["pid"] == 12884
    assert p1["ppid"] == 12883
    assert p1["etime"] == "03:00"
    assert p1["command"] == "/Users/ycyber/.local/bin/agy acp --dangerously-skip-permissions"

    # Linux GNU format
    gnu_line = " 161806  161805       05:08 /home/ubuntu/.local/bin/agy acp --dangerously-skip-permissions"
    p2 = r.parse_ps_line(gnu_line)
    assert p2 is not None
    assert p2["pid"] == 161806
    assert p2["ppid"] == 161805
    assert p2["etime"] == "05:08"

    # Linux GNU format with days
    gnu_days = "2226662    1261  4-06:40:44 /home/ubuntu/.local/bin/agy remote-control serve"
    p3 = r.parse_ps_line(gnu_days)
    assert p3 is not None
    assert p3["pid"] == 2226662
    assert p3["ppid"] == 1261
    assert p3["etime"] == "4-06:40:44"
    assert p3["command"] == "/home/ubuntu/.local/bin/agy remote-control serve"

    # Header / invalid lines
    assert r.parse_ps_line("PID PPID ETIME COMMAND") is None
    assert r.parse_ps_line("") is None
    assert r.parse_ps_line("abc def ghi jkl") is None


def test_classify_command():
    # agy acp
    c1, exe1 = r.classify_command("/Users/ycyber/.local/bin/agy acp --dangerously-skip-permissions")
    assert c1 == "acp"
    assert exe1 == "/Users/ycyber/.local/bin/agy"

    # agy remote-control
    c2, exe2 = r.classify_command("/home/ubuntu/.local/bin/agy remote-control serve")
    assert c2 == "remote-control"
    assert exe2 == "/home/ubuntu/.local/bin/agy"

    # env agy acp
    c3, exe3 = r.classify_command("/usr/bin/env agy acp")
    assert c3 == "acp"
    assert exe3 == "agy"

    # other agy command
    c4, exe4 = r.classify_command("agy version")
    assert c4 == "other_agy"
    assert exe4 == "agy"

    # wrapper or unrelated processes
    c5, _ = r.classify_command("/Applications/Orca.app/Contents/MacOS/orca-provider-exec -- agy acp")
    assert c5 == "not_agy"

    c6, _ = r.classify_command("grep -i agp")
    assert c6 == "not_agy"

    c7, _ = r.classify_command("")
    assert c7 == "not_agy"


def test_find_latest_account_from_logs():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create older log
        f1 = os.path.join(tmpdir, "cli-20261120_100000.log")
        with open(f1, "w") as fp:
            fp.write("I1120 server_oauth.go:221] applyAuthResult: email=old@example.com, authMethod=consumer\n")

        # Create middle log
        f2 = os.path.join(tmpdir, "acp-server-20261125_120000-1111.log")
        with open(f2, "w") as fp:
            fp.write("I1125 server_oauth.go:221] applyAuthResult: email=middle@example.com, authMethod=consumer\n")

        # Create newest log
        f3 = os.path.join(tmpdir, "cli-20261126_210000.log")
        with open(f3, "w") as fp:
            fp.write("I1126 server_oauth.go:221] applyAuthResult: email=newest@example.com, authMethod=consumer\n")

        # Explicitly set mtimes: f1 < f2 < f3
        now = time.time()
        os.utime(f1, (now - 200, now - 200))
        os.utime(f2, (now - 100, now - 100))
        os.utime(f3, (now, now))

        email, src = r.find_latest_account_from_logs(tmpdir)
        assert email == "newest@example.com"
        assert src == "cli-20261126_210000.log"

        # If f2 is touched to be newest
        os.utime(f2, (now + 50, now + 50))
        email2, src2 = r.find_latest_account_from_logs(tmpdir)
        assert email2 == "middle@example.com"
        assert src2 == "acp-server-20261125_120000-1111.log"


def test_find_acp_log_for_pid():
    with tempfile.TemporaryDirectory() as tmpdir:
        f_old = os.path.join(tmpdir, "acp-server-20261120_100000-1234.log")
        f_new = os.path.join(tmpdir, "acp-server-20261126_120000-1234.log")
        f_other = os.path.join(tmpdir, "acp-server-20261126_120000-5678.log")

        with open(f_old, "w") as fp:
            fp.write("old")
        with open(f_new, "w") as fp:
            fp.write("new")
        with open(f_other, "w") as fp:
            fp.write("other")

        now = time.time()
        os.utime(f_old, (now - 100, now - 100))
        os.utime(f_new, (now, now))

        # Matches PID 1234 and picks the newest
        matched = r.find_acp_log_for_pid(tmpdir, 1234)
        assert matched is not None
        assert os.path.basename(matched) == "acp-server-20261126_120000-1234.log"

        # Non-existent PID returns None
        assert r.find_acp_log_for_pid(tmpdir, 9999) is None


def test_parse_acp_log():
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, "acp-server-20261126_120000-1234.log")
        with open(log_file, "w") as fp:
            fp.write(
                "I1126 server_oauth.go:221] applyAuthResult: email=alice@example.com, authMethod=consumer\n"
                "I1126 session_handlers.go:485] Loading/Resuming session: 4445c0e1-0df1-4e17-99d8-2c3de9431d6b, cwd: /home/ubuntu/orca/workspaces/sub-pipe\n"
                "I1126 updates.go:481] Sending session_info_update for session 4445c0e1-0df1-4e17-99d8-2c3de9431d6b: title=\"Test\"\n"
                "E1126 errorreport.go:282] transport error (ResourceExhausted, HTTP 429): Individual quota reached. Resets in 3h25m40s.: RESOURCE_EXHAUSTED (code 429)\n"
            )

        info = r.parse_acp_log(log_file)
        assert info["email"] == "alice@example.com"
        assert info["cwd"] == "/home/ubuntu/orca/workspaces/sub-pipe"
        assert info["sessions"] == ["4445c0e1-0df1-4e17-99d8-2c3de9431d6b"]
        assert info["has_429"] is True
        assert info["quota_message"] == "Resets in 3h25m40s"


def test_get_ancestor_pids():
    # Mock ps tree:
    # 100 -> 50 -> 10 -> 1
    tree = {
        100: "50",
        50: "10",
        10: "1",
        1: "0",
    }

    def mock_ps(args):
        # args: ['ps', '-p', str(curr), '-o', 'ppid=']
        pid = int(args[2])
        return tree.get(pid, "")

    ancestors = r.get_ancestor_pids(100, ps_runner=mock_ps)
    assert ancestors == [50, 10, 1]

    # Test cycle handling: 200 -> 200
    cycle_tree = {200: "200"}
    ancestors_cycle = r.get_ancestor_pids(200, ps_runner=lambda args: cycle_tree.get(int(args[2]), ""))
    assert ancestors_cycle == []


def test_inspect_local_host_status_classification():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Latest account is newest@example.com
        cli_log = os.path.join(tmpdir, "cli-20261126_210000.log")
        with open(cli_log, "w") as fp:
            fp.write("I1126 applyAuthResult: email=newest@example.com, authMethod=consumer\n")

        # PID 100: Ancestor PID (has newest account)
        log100 = os.path.join(tmpdir, "acp-server-20261126_100000-100.log")
        with open(log100, "w") as fp:
            fp.write("I1126 applyAuthResult: email=newest@example.com\n")

        # PID 200: ACP with old account
        log200 = os.path.join(tmpdir, "acp-server-20261126_100000-200.log")
        with open(log200, "w") as fp:
            fp.write(
                "I1126 applyAuthResult: email=old@example.com\n"
                "E1126 RESOURCE_EXHAUSTED (code 429): Individual quota reached\n"
            )

        # PID 300: ACP with current account
        log300 = os.path.join(tmpdir, "acp-server-20261126_100000-300.log")
        with open(log300, "w") as fp:
            fp.write("I1126 applyAuthResult: email=newest@example.com\n")

        # PID 400: remote-control serve
        # PID 500: ACP with unreadable / missing log

        ps_mock = "\n".join([
            "  100    50       01:00 /usr/local/bin/agy acp",
            "  200    50       10:00 /usr/local/bin/agy acp",
            "  300    50       05:00 /usr/local/bin/agy acp",
            "  400    50       20:00 /usr/local/bin/agy remote-control serve",
            "  500    50       02:00 /usr/local/bin/agy acp",
            "  600    50       00:10 /usr/bin/python3 script.py",
        ])

        # Tree where self_pid=5000 and ancestor chain is [100, 50, 1]
        def mock_ps(args):
            p = int(args[2])
            if p == 5000:
                return "100"
            if p == 100:
                return "50"
            if p == 50:
                return "1"
            return ""

        # Default run (dry-run, no --all-acp, no --include-remote-control)
        rep = r.inspect_local_host(
            log_dir=tmpdir,
            apply=False,
            all_acp=False,
            include_remote_control=False,
            ps_output=ps_mock,
            ps_runner=mock_ps,
            self_pid=5000,
        )

        assert rep.latest_email == "newest@example.com"
        assert len(rep.processes) == 5  # 100, 200, 300, 400, 500 (600 is not agy)

        p_by_pid = {p.pid: p for p in rep.processes}

        # PID 100 is ancestor -> SKIP
        assert p_by_pid[100].is_ancestor is True
        assert p_by_pid[100].action == "SKIP"
        assert "祖先进程跳过" in p_by_pid[100].status

        # PID 200 has old email -> KILL
        assert p_by_pid[200].action == "KILL"
        assert p_by_pid[200].has_429 is True
        assert "旧账号待清理" in p_by_pid[200].status

        # PID 300 has latest email -> KEEP
        assert p_by_pid[300].action == "KEEP"
        assert "已是最新账号" in p_by_pid[300].status

        # PID 400 is remote-control -> SKIP
        assert p_by_pid[400].action == "SKIP"
        assert "remote-control 默认跳过" in p_by_pid[400].status

        # PID 500 has no log -> SKIP
        assert p_by_pid[500].action == "SKIP"
        assert "无法识别日志" in p_by_pid[500].status

        # Test with --all-acp
        rep_all = r.inspect_local_host(
            log_dir=tmpdir,
            apply=False,
            all_acp=True,
            include_remote_control=False,
            ps_output=ps_mock,
            ps_runner=mock_ps,
            self_pid=5000,
        )
        p_all = {p.pid: p for p in rep_all.processes}
        # PID 100 is STILL protected!
        assert p_all[100].action == "SKIP"
        # PID 200, 300, 500 all become KILL
        assert p_all[200].action == "KILL"
        assert p_all[300].action == "KILL"
        assert p_all[500].action == "KILL"
        # PID 400 remote-control is still SKIP (since include_remote_control=False)
        assert p_all[400].action == "SKIP"

        # Test with --include-remote-control
        rep_rc = r.inspect_local_host(
            log_dir=tmpdir,
            apply=False,
            all_acp=False,
            include_remote_control=True,
            ps_output=ps_mock,
            ps_runner=mock_ps,
            self_pid=5000,
        )
        p_rc = {p.pid: p for p in rep_rc.processes}
        assert p_rc[400].category == "remote-control"


def test_terminate_process_safety_guardrails():
    # 1. Double check ps: command changed (PID recycled to unrelated command)
    def ps_changed(args):
        return " 1234 /usr/bin/nginx -g daemon off;\n"

    res_changed = r.terminate_process(1234, ps_runner=ps_changed)
    assert res_changed["verified"] is False
    assert res_changed["signal_sent"] is False
    assert "命令行已变更" in res_changed["message"]

    # 2. Process already exited before kill
    def ps_empty(args):
        return ""

    res_empty = r.terminate_process(1234, ps_runner=ps_empty)
    assert res_empty["verified"] is False
    assert res_empty["exited"] is True

    # 3. Successful kill verification flow
    killed_signals = []
    alive_checks = []

    def ps_valid(args):
        return " 1234 /usr/local/bin/agy acp --dangerously-skip-permissions\n"

    def mock_kill(pid, sig):
        if sig == signal.SIGTERM:
            killed_signals.append((pid, sig))
        elif sig == 0:
            alive_checks.append((pid, sig))
            if len(alive_checks) >= 2:
                # Process exits on second check
                raise ProcessLookupError()

    res_success = r.terminate_process(
        1234,
        ps_runner=ps_valid,
        kill_fn=mock_kill,
        sleep_fn=lambda _: None,
    )
    assert res_success["verified"] is True
    assert res_success["signal_sent"] is True
    assert res_success["exited"] is True
    assert (1234, signal.SIGTERM) in killed_signals


def test_dry_run_vs_apply_flow():
    with tempfile.TemporaryDirectory() as tmpdir:
        cli_log = os.path.join(tmpdir, "cli-20261126_210000.log")
        with open(cli_log, "w") as fp:
            fp.write("I1126 applyAuthResult: email=new@example.com\n")

        log_old = os.path.join(tmpdir, "acp-server-20261126_100000-200.log")
        with open(log_old, "w") as fp:
            fp.write("I1126 applyAuthResult: email=old@example.com\n")

        now = time.time()
        os.utime(log_old, (now - 100, now - 100))
        os.utime(cli_log, (now, now))

        ps_mock = "  200    50       10:00 /usr/local/bin/agy acp\n"

        killed_pids = []

        def mock_kill(pid, sig):
            killed_pids.append(pid)
            if sig == 0:
                raise ProcessLookupError()

        def mock_ps(args):
            return "  200 /usr/local/bin/agy acp\n"

        # Dry run: should NOT call kill
        rep_dry = r.inspect_local_host(
            log_dir=tmpdir,
            apply=False,
            ps_output=ps_mock,
            ps_runner=mock_ps,
            kill_fn=mock_kill,
            self_pid=9999,
        )
        assert len(killed_pids) == 0
        assert rep_dry.processes[0].action == "KILL"
        assert rep_dry.processes[0].result is None

        # Apply mode: SHOULD call kill
        rep_apply = r.inspect_local_host(
            log_dir=tmpdir,
            apply=True,
            ps_output=ps_mock,
            ps_runner=mock_ps,
            kill_fn=mock_kill,
            sleep_fn=lambda _: None,
            self_pid=9999,
        )
        assert 200 in killed_pids
        assert rep_apply.processes[0].result is not None
        assert rep_apply.processes[0].result["exited"] is True


def test_format_report_text():
    proc = r.ProcessInfo(
        pid=1234,
        ppid=567,
        etime="05:12",
        command="/path/to/agy acp",
        category="acp",
        email="old@example.com",
        sessions=["abcdef12-3456-7890-abcd-ef1234567890"],
        cwd="/Users/x/project",
        has_429=True,
        quota_message="Resets in 3h",
        action="KILL",
        status="旧账号待清理",
    )
    rep = r.HostReport(
        host="local",
        latest_email="new@example.com",
        latest_email_source="cli-log",
        processes=[proc],
    )

    text_dry = r.format_report_text([rep], dry_run=True)
    assert "[DRY-RUN 预览模式]" in text_dry
    assert "old@example.com" in text_dry
    assert "旧账号待清理" in text_dry
    assert "待清理旧账号进程: 1" in text_dry

    text_apply = r.format_report_text([rep], dry_run=False)
    assert "[APPLY 实际执行模式]" in text_apply
    assert "目标清理进程: 1" in text_apply


if __name__ == "__main__":
    check("parse_ps_line_bsd_and_gnu", test_parse_ps_line_bsd_and_gnu)
    check("classify_command", test_classify_command)
    check("find_latest_account_from_logs", test_find_latest_account_from_logs)
    check("find_acp_log_for_pid", test_find_acp_log_for_pid)
    check("parse_acp_log", test_parse_acp_log)
    check("get_ancestor_pids", test_get_ancestor_pids)
    check("inspect_local_host_status_classification", test_inspect_local_host_status_classification)
    check("terminate_process_safety_guardrails", test_terminate_process_safety_guardrails)
    check("dry_run_vs_apply_flow", test_dry_run_vs_apply_flow)
    check("format_report_text", test_format_report_text)

    print()
    if FAILS:
        print(f"FAILED ({len(FAILS)}): {', '.join(FAILS)}")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED (10/10)")
