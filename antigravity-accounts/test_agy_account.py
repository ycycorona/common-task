#!/usr/bin/env python3
"""Self-checks for agy_account.py. Run: python3 antigravity-accounts/test_agy_account.py"""

from __future__ import annotations

import contextlib
import hashlib
import http.server
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import agy_account as aa  # pyright: ignore[reportImplicitRelativeImport]

FAILS: list[str] = []


def check(name: str, fn) -> None:
    try:
        fn()
        print(f"ok   {name}")
    except AssertionError as exc:
        FAILS.append(name)
        print(f"FAIL {name}: {exc}")
    except Exception as exc:
        FAILS.append(name)
        print(f"FAIL {name}: unexpected {type(exc).__name__}: {exc}")


def make_fake_token(
    refresh_token: str,
    project_id: str = "aicode-consumers",
    token_uri: str = "https://oauth2.googleapis.com/token",
    client_secret: str = "CLIENT_SECRET_FAKE_DO_NOT_LEAK",
) -> bytes:
    payload = {
        "client_id": "1234567890-fake.apps.googleusercontent.com",
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "token_uri": token_uri,
        "scopes": [
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/aicode",
        ],
        "project_id": project_id,
    }
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


class Sandbox:
    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agy-account-test-"))
        self.home = self.root / "user_home"
        self.gemini_home = self.root / "gemini_home"
        self.vault = self.root / "vault"
        self.acp_dir = self.gemini_home / "antigravity-acp"
        self.acp_dir.mkdir(parents=True)
        (self.acp_dir / "settings.json").write_text('{"auth":{"type":"oauth-personal"}}\n', encoding="utf-8")
        os.chmod(self.acp_dir / "settings.json", 0o600)
        self.live_token = self.acp_dir / "acp_token.json"

    def write_live(self, raw: bytes) -> None:
        self.live_token.write_bytes(raw)
        os.chmod(self.live_token, 0o600)

    def run_cli(
        self,
        args: list[str],
        extra_env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home),
            "GEMINI_HOME": str(self.gemini_home),
            "AGY_ACCOUNTS_DIR": str(self.vault),
        }
        if extra_env:
            env.update(extra_env)
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "agy_account.py"), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def run_inproc(self, argv: list[str], extra_env: dict[str, str] | None = None) -> tuple[int, str, str]:
        old_env = os.environ.copy()
        out = io.StringIO()
        err = io.StringIO()
        try:
            os.environ["HOME"] = str(self.home)
            os.environ["GEMINI_HOME"] = str(self.gemini_home)
            os.environ["AGY_ACCOUNTS_DIR"] = str(self.vault)
            if extra_env:
                os.environ.update(extra_env)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = aa.main(argv)
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        return rc, out.getvalue(), err.getvalue()

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_save_and_permissions() -> None:
    sb = Sandbox()
    try:
        tok_a = make_fake_token("REFRESH_SECRET_ALPHA_0001")
        sb.write_live(tok_a)

        rc, out, err = sb.run_inproc(["save", "work-a", "--note", "primary", "--email", "a@example.com"])
        assert rc == 0, f"save failed: {err}"
        assert "work-a" in out
        assert "REFRESH_SECRET_ALPHA_0001" not in out + err
        assert "CLIENT_SECRET_FAKE_DO_NOT_LEAK" not in out + err

        assert _mode(sb.vault) == 0o700
        assert _mode(sb.vault / "work-a") == 0o700
        saved_tok = sb.vault / "work-a" / "acp_token.json"
        saved_meta = sb.vault / "work-a" / "meta.json"
        assert _mode(saved_tok) == 0o600
        assert _mode(saved_meta) == 0o600
        assert saved_tok.read_bytes() == tok_a

        meta = json.loads(saved_meta.read_text(encoding="utf-8"))
        expected_fp = hashlib.sha256(b"REFRESH_SECRET_ALPHA_0001").hexdigest()
        assert meta["label"] == "work-a"
        assert meta["fingerprint"] == expected_fp
        assert meta["fingerprint_short"] == expected_fp[:8]
        assert meta["project_id"] == "aicode-consumers"
        assert meta["note"] == "primary"
        assert meta["email"] == "a@example.com"
        assert meta["size_bytes"] == len(tok_a)

        # Duplicate label rejected without --force
        rc, _, err = sb.run_inproc(["save", "work-a"])
        assert rc != 0 and "已存在" in err

        # Duplicate fingerprint under new label rejected without --force
        rc, _, err = sb.run_inproc(["save", "work-a-copy"])
        assert rc != 0 and "work-a" in err

        # Allowed with --force
        rc, _, err = sb.run_inproc(["save", "work-a-copy", "--force"])
        assert rc == 0, err

        # Invalid labels rejected
        for bad in ("_backup", ".hidden", "../x", "a/b", "bad label"):
            rc, _, _ = sb.run_inproc(["save", bad])
            assert rc != 0, f"expected failure for label {bad!r}"
    finally:
        sb.cleanup()


def test_current_and_list() -> None:
    sb = Sandbox()
    try:
        tok_a = make_fake_token("REFRESH_SECRET_A_111")
        tok_b = make_fake_token("REFRESH_SECRET_B_222")
        tok_c = make_fake_token("REFRESH_SECRET_C_333")

        sb.write_live(tok_a)
        assert sb.run_inproc(["save", "acct-a"])[0] == 0
        sb.write_live(tok_b)
        assert sb.run_inproc(["save", "acct-b"])[0] == 0

        rc, out, _ = sb.run_inproc(["current", "--json"])
        assert rc == 0
        cur = json.loads(out)
        assert cur["status"] == "matched"
        assert cur["matched_labels"] == ["acct-b"]
        assert cur["fingerprint_short"] == hashlib.sha256(b"REFRESH_SECRET_B_222").hexdigest()[:8]

        rc, out, _ = sb.run_inproc(["list", "--json"])
        assert rc == 0
        lst = json.loads(out)
        flags = {a["label"]: a["current"] for a in lst["accounts"]}
        assert flags == {"acct-a": False, "acct-b": True}

        # Live drifts to unsaved token C
        sb.write_live(tok_c)
        rc, out, _ = sb.run_inproc(["current"])
        assert rc == 0
        assert "unknown" in out
        rc, out, _ = sb.run_inproc(["list"])
        assert rc == 0
        assert "未在账号库中保存" in out

        # Missing live token fails visibly
        sb.live_token.unlink()
        rc, _, err = sb.run_inproc(["current"])
        assert rc != 0 and "未找到" in err
    finally:
        sb.cleanup()


def test_use_switch_backup_and_dry_run() -> None:
    sb = Sandbox()
    try:
        tok_a = make_fake_token("REFRESH_SECRET_ALPHA")
        tok_b = make_fake_token("REFRESH_SECRET_BETA")
        tok_unsaved = make_fake_token("REFRESH_SECRET_UNSAVED")

        sb.write_live(tok_a)
        assert sb.run_inproc(["save", "acct-a"])[0] == 0
        sb.write_live(tok_b)
        assert sb.run_inproc(["save", "acct-b"])[0] == 0

        # Dry run touches nothing
        rc, out, err = sb.run_inproc(["use", "acct-a", "--dry-run"])
        assert rc == 0, err
        assert "[dry-run]" in out
        assert sb.live_token.read_bytes() == tok_b
        assert not (sb.vault / "_backup").exists()

        # Real switch in /tmp sandbox from acct-b -> acct-a
        rc, out, err = sb.run_inproc(["use", "acct-a"])
        assert rc == 0, err
        assert "acct-a" in out
        assert "重开 Antigravity Chat 会话后生效" in out
        assert "REFRESH_SECRET_ALPHA" not in out + err
        assert "REFRESH_SECRET_BETA" not in out + err
        assert sb.live_token.read_bytes() == tok_a
        assert _mode(sb.live_token) == 0o600

        backups = sorted((sb.vault / "_backup").iterdir())
        assert len(backups) == 1
        assert backups[0].name.endswith("-acct-b"), backups[0].name
        assert _mode(backups[0]) == 0o700
        assert _mode(backups[0] / "acp_token.json") == 0o600
        assert (backups[0] / "acp_token.json").read_bytes() == tok_b
        # Saved acct-b in vault remains untouched
        assert (sb.vault / "acct-b" / "acp_token.json").read_bytes() == tok_b

        # Unsaved live token backed up under -unknown
        sb.write_live(tok_unsaved)
        time.sleep(0.01)
        rc, _, err = sb.run_inproc(["use", "acct-b"])
        assert rc == 0, err
        backups = sorted((sb.vault / "_backup").iterdir())
        assert len(backups) == 2
        assert any(b.name.endswith("-unknown") for b in backups), [b.name for b in backups]
        unknown_dir = next(b for b in backups if b.name.endswith("-unknown"))
        assert (unknown_dir / "acp_token.json").read_bytes() == tok_unsaved
        assert sb.live_token.read_bytes() == tok_b

        # Missing settings.json refuses switch
        (sb.acp_dir / "settings.json").unlink()
        rc, _, err = sb.run_inproc(["use", "acct-a"])
        assert rc != 0 and "settings.json" in err
        assert sb.live_token.read_bytes() == tok_b
    finally:
        sb.cleanup()


def test_use_blocks_live_acp_process() -> None:
    sb = Sandbox()
    proc_match = None
    proc_other = None
    try:
        tok_a = make_fake_token("REFRESH_SECRET_A_PROC")
        tok_b = make_fake_token("REFRESH_SECRET_B_PROC")
        sb.write_live(tok_a)
        assert sb.run_inproc(["save", "acct-a"])[0] == 0
        sb.write_live(tok_b)
        assert sb.run_inproc(["save", "acct-b"])[0] == 0

        fake_bin = sb.root / "agy_acp_server.par"
        fake_bin.write_text("#!/usr/bin/env python3\nimport time; time.sleep(60)\n", encoding="utf-8")
        os.chmod(fake_bin, 0o755)

        # Spawn process targeting a DIFFERENT GEMINI_HOME: must NOT block our sandbox
        other_home = sb.root / "other_gemini_home"
        other_home.mkdir()
        proc_other = subprocess.Popen(
            [sys.executable, str(fake_bin), "--uid="],
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "GEMINI_HOME": str(other_home),
                "ORCA_AGENT_SESSION_ID": "other-session-999",
            },
        )
        time.sleep(0.15)
        rc, _, err = sb.run_inproc(["use", "acct-a"])
        assert rc == 0, f"unrelated GEMINI_HOME should not block: {err}"
        assert sb.live_token.read_bytes() == tok_a

        # Spawn process targeting OUR GEMINI_HOME: MUST block switching to acct-b
        proc_match = subprocess.Popen(
            [sys.executable, str(fake_bin), "--uid="],
            cwd=str(sb.root),
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "GEMINI_HOME": str(sb.gemini_home),
                "ORCA_AGENT_SESSION_ID": "live-session-xyz",
            },
        )
        time.sleep(0.15)
        rc, out, err = sb.run_inproc(["use", "acct-b"])
        assert rc == 2, f"expected exit 2 when live ACP process exists, got {rc}, out={out}, err={err}"
        assert "请先在 Orca 里关掉这些 Antigravity Chat 会话" in err
        assert str(proc_match.pid) in err
        assert "live-session-xyz" in err
        assert sb.live_token.read_bytes() == tok_a
    finally:
        for p in (proc_match, proc_other):
            if p is not None:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
        sb.cleanup()


def test_synthetic_proc_scanner() -> None:
    root = Path(tempfile.mkdtemp(prefix="agy-proc-test-"))
    try:
        # 1. Native PAR process
        p1 = root / "101"
        p1.mkdir()
        (p1 / "cmdline").write_bytes(b"/opt/orca/agy_acp_server.par\0--uid=\0")
        (p1 / "environ").write_bytes(b"GEMINI_HOME=/tmp/h1\0ORCA_AGENT_SESSION_ID=sess-1\0")
        os.symlink("/tmp", p1 / "cwd")

        # 2. Wrapper orca-provider-exec (should be skipped)
        p2 = root / "100"
        p2.mkdir()
        (p2 / "cmdline").write_bytes(
            b"/opt/orca/orca-provider-exec\0--\0/opt/orca/agy_acp_server.par\0--uid=\0"
        )
        (p2 / "environ").write_bytes(b"GEMINI_HOME=/tmp/h1\0")

        found = aa.find_live_acp_processes(proc_root=root)
        assert len(found) == 1, found
        assert found[0].pid == 101
        assert found[0].gemini_home == "/tmp/h1"
        assert found[0].session_id == "sess-1"
        assert found[0].cwd == "/tmp"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_rollback_on_verify_failure() -> None:
    sb = Sandbox()
    try:
        tok_a = make_fake_token("REFRESH_SECRET_ROLL_A")
        tok_b = make_fake_token("REFRESH_SECRET_ROLL_B")
        sb.write_live(tok_a)
        assert sb.run_inproc(["save", "acct-a"])[0] == 0
        sb.write_live(tok_b)
        assert sb.run_inproc(["save", "acct-b"])[0] == 0

        orig_atomic = aa.atomic_write_bytes
        live_calls = {"count": 0}

        def corrupting_write(path: Path, data: bytes, mode: int = 0o600, dir_mode: int = 0o700) -> None:
            if path == sb.live_token:
                live_calls["count"] += 1
                if live_calls["count"] == 1:
                    # Corrupt the written live file on the switch write, allow rollback write
                    orig_atomic(path, b'{"broken": true}', mode=mode, dir_mode=dir_mode)
                    return
            orig_atomic(path, data, mode=mode, dir_mode=dir_mode)

        aa.atomic_write_bytes = corrupting_write
        try:
            rc, _, err = sb.run_inproc(["use", "acct-a"])
        finally:
            aa.atomic_write_bytes = orig_atomic

        assert rc != 0 and "回滚" in err
        assert sb.live_token.read_bytes() == tok_b, "rollback must restore previous live token"
    finally:
        sb.cleanup()


def test_verify_http_flow_and_redaction() -> None:
    sb = Sandbox()
    state = {"mode": "ok", "email": "alice@example.com"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            if state["mode"] == "invalid_grant":
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "invalid_grant", "error_description": "Token expired"}).encode())
                return
            assert body.get("grant_type") == ["refresh_token"]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"access_token": "ACCESS_TOKEN_SECRET_XYZ", "token_type": "Bearer"}).encode()
            )

        def do_GET(self) -> None:
            auth = self.headers.get("Authorization")
            if auth != "Bearer ACCESS_TOKEN_SECRET_XYZ":
                self.send_response(401)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"email": state["email"], "email_verified": True}).encode())

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        token_url = f"http://127.0.0.1:{port}/token"
        userinfo_url = f"http://127.0.0.1:{port}/userinfo"
        tok_a = make_fake_token("REFRESH_SECRET_VERIFY_A", token_uri=token_url)
        sb.write_live(tok_a)

        # Subprocess CLI end-to-end with GEMINI_HOME injection
        rc, out, err = sb.run_cli(["save", "acct-a"])
        assert rc == 0, err

        rc, out, err = sb.run_cli(["verify", "acct-a"], extra_env={"AGY_USERINFO_URL": userinfo_url})
        assert rc == 0, err
        assert "alice@example.com" in out
        for secret in ("REFRESH_SECRET_VERIFY_A", "CLIENT_SECRET_FAKE_DO_NOT_LEAK", "ACCESS_TOKEN_SECRET_XYZ"):
            assert secret not in out + err

        meta = json.loads((sb.vault / "acct-a" / "meta.json").read_text(encoding="utf-8"))
        assert meta["email"] == "alice@example.com"
        assert meta["email_source"] == "userinfo"
        assert meta["stale"] is False

        # Second verify matches meta
        rc, out, err = sb.run_cli(["verify", "acct-a"], extra_env={"AGY_USERINFO_URL": userinfo_url})
        assert rc == 0, err
        assert "matched" in out

        # Mismatch fails without --update-email
        state["email"] = "bob@example.com"
        rc, out, err = sb.run_cli(["verify", "acct-a"], extra_env={"AGY_USERINFO_URL": userinfo_url})
        assert rc != 0 and "身份不匹配" in err
        meta = json.loads((sb.vault / "acct-a" / "meta.json").read_text(encoding="utf-8"))
        assert meta["email"] == "alice@example.com"

        # With --update-email it updates
        rc, out, err = sb.run_cli(
            ["verify", "acct-a", "--update-email"], extra_env={"AGY_USERINFO_URL": userinfo_url}
        )
        assert rc == 0, err
        meta = json.loads((sb.vault / "acct-a" / "meta.json").read_text(encoding="utf-8"))
        assert meta["email"] == "bob@example.com"

        # Verify without [label] uses live token
        rc, out, err = sb.run_cli(["verify"], extra_env={"AGY_USERINFO_URL": userinfo_url})
        assert rc == 0, err
        assert "bob@example.com" in out

        # invalid_grant marks stale=True and exits non-zero without printing secrets
        state["mode"] = "invalid_grant"
        rc, out, err = sb.run_cli(["verify", "acct-a"], extra_env={"AGY_USERINFO_URL": userinfo_url})
        assert rc == 3
        assert "invalid_grant" in err
        for secret in ("REFRESH_SECRET_VERIFY_A", "CLIENT_SECRET_FAKE_DO_NOT_LEAK", "ACCESS_TOKEN_SECRET_XYZ"):
            assert secret not in out + err
        meta = json.loads((sb.vault / "acct-a" / "meta.json").read_text(encoding="utf-8"))
        assert meta["stale"] is True
    finally:
        server.shutdown()
        server.server_close()
        sb.cleanup()


def main() -> int:
    check("save_and_permissions", test_save_and_permissions)
    check("current_and_list", test_current_and_list)
    check("use_switch_backup_and_dry_run", test_use_switch_backup_and_dry_run)
    check("use_blocks_live_acp_process", test_use_blocks_live_acp_process)
    check("synthetic_proc_scanner", test_synthetic_proc_scanner)
    check("rollback_on_verify_failure", test_rollback_on_verify_failure)
    check("verify_http_flow_and_redaction", test_verify_http_flow_and_redaction)
    if FAILS:
        print(f"\n{len(FAILS)} check(s) failed: {', '.join(FAILS)}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
