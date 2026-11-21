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


def make_fake_cli_token(
    refresh_token: str,
    access_token: str = "ACCESS_CLI_FAKE_000",
    auth_method: str = "consumer",
    id_token_email: str | None = "cli@example.com",
    project_id: str | None = None,
) -> bytes:
    payload: dict[str, object] = {
        "token": {
            "access_token": access_token,
            "token_type": "Bearer",
            "refresh_token": refresh_token,
            "expiry": "2026-11-21T20:08:55Z",
            "expires_in": 3599,
        },
        "auth_method": auth_method,
        "writer_id": "test-writer-1",
    }
    if id_token_email is not None:
        payload["id_token_email"] = id_token_email
    if project_id is not None:
        payload["project_id"] = project_id
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

        self.cli_dir = self.home / ".gemini" / "antigravity-cli"
        self.cli_dir.mkdir(parents=True)
        (self.cli_dir / "settings.json").write_text('{"security":{"auth":{"selectedType":"oauth-personal"}}}\n', encoding="utf-8")
        os.chmod(self.cli_dir / "settings.json", 0o600)
        (self.cli_dir / "antigravity_state.pbtxt").write_text('installation_uuid: "test-uuid"\n', encoding="utf-8")
        os.chmod(self.cli_dir / "antigravity_state.pbtxt", 0o600)
        self.cli_scope_file = self.home / ".config" / "antigravity" / "credential-user-scope"
        self.cli_scope_file.parent.mkdir(parents=True, exist_ok=True)
        self.cli_scope_file.write_text("u0123456789abcdef", encoding="utf-8")
        self.live_cli_token = self.cli_dir / "antigravity-oauth-token"

    def write_live(self, raw: bytes) -> None:
        self.live_token.write_bytes(raw)
        os.chmod(self.live_token, 0o600)

    def write_live_cli(self, raw: bytes) -> None:
        self.live_cli_token.write_bytes(raw)
        os.chmod(self.live_cli_token, 0o600)

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
        assert _mode(sb.vault / "chat") == 0o700
        assert _mode(sb.vault / "chat" / "work-a") == 0o700
        saved_tok = sb.vault / "chat" / "work-a" / "acp_token.json"
        saved_meta = sb.vault / "chat" / "work-a" / "meta.json"
        assert _mode(saved_tok) == 0o600
        assert _mode(saved_meta) == 0o600
        assert saved_tok.read_bytes() == tok_a

        meta = json.loads(saved_meta.read_text(encoding="utf-8"))
        expected_fp = hashlib.sha256(b"REFRESH_SECRET_ALPHA_0001").hexdigest()
        assert meta["label"] == "work-a"
        assert meta["surface"] == "chat"
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
        assert cur["surface"] == "chat"
        assert cur["status"] == "matched"
        assert cur["matched_labels"] == ["acct-b"]
        assert cur["live_process_count"] == 0
        assert cur["fingerprint_short"] == hashlib.sha256(b"REFRESH_SECRET_B_222").hexdigest()[:8]

        rc, out, _ = sb.run_inproc(["list", "--json"])
        assert rc == 0
        lst = json.loads(out)
        assert lst["surface"] == "chat"
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
        assert not (sb.vault / "chat" / "_backup").exists()

        # Real switch in /tmp sandbox from acct-b -> acct-a
        rc, out, err = sb.run_inproc(["use", "acct-a"])
        assert rc == 0, err
        assert "acct-a" in out
        assert "重开 Antigravity Chat 会话后生效" in out
        assert "REFRESH_SECRET_ALPHA" not in out + err
        assert "REFRESH_SECRET_BETA" not in out + err
        assert sb.live_token.read_bytes() == tok_a
        assert _mode(sb.live_token) == 0o600

        backups = sorted((sb.vault / "chat" / "_backup").iterdir())
        assert len(backups) == 1
        assert backups[0].name.endswith("-acct-b"), backups[0].name
        assert _mode(backups[0]) == 0o700
        assert _mode(backups[0] / "acp_token.json") == 0o600
        assert (backups[0] / "acp_token.json").read_bytes() == tok_b
        # Saved acct-b in vault remains untouched
        assert (sb.vault / "chat" / "acct-b" / "acp_token.json").read_bytes() == tok_b

        # Unsaved live token backed up under -unknown
        sb.write_live(tok_unsaved)
        time.sleep(0.01)
        rc, _, err = sb.run_inproc(["use", "acct-b"])
        assert rc == 0, err
        backups = sorted((sb.vault / "chat" / "_backup").iterdir())
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
        # 1. Native ACP PAR process
        p1 = root / "101"
        p1.mkdir()
        (p1 / "cmdline").write_bytes(b"/opt/orca/agy_acp_server.par\0--uid=\0")
        (p1 / "environ").write_bytes(b"GEMINI_HOME=/tmp/h1\0ORCA_AGENT_SESSION_ID=sess-1\0")
        os.symlink("/tmp", p1 / "cwd")

        # 2. Wrapper orca-provider-exec (should be skipped for both ACP and CLI)
        p2 = root / "100"
        p2.mkdir()
        (p2 / "cmdline").write_bytes(
            b"/opt/orca/orca-provider-exec\0--\0/opt/orca/agy_acp_server.par\0--uid=\0"
        )
        (p2 / "environ").write_bytes(b"GEMINI_HOME=/tmp/h1\0")

        # 3. Interactive CLI `agy` process
        p3 = root / "201"
        p3.mkdir()
        (p3 / "cmdline").write_bytes(b"/home/u/.local/bin/agy\0chat\0")
        (p3 / "environ").write_bytes(b"HOME=/tmp/cli-h1\0ORCA_AGENT_SESSION_ID=cli-sess-1\0")
        os.symlink("/tmp", p3 / "cwd")

        # 4. `agy remote-control serve` (must be excluded)
        p4 = root / "202"
        p4.mkdir()
        (p4 / "cmdline").write_bytes(b"/home/u/.local/bin/agy\0remote-control\0serve\0")
        (p4 / "environ").write_bytes(b"HOME=/tmp/cli-h1\0")

        # 5. `agy --quiet remote-control serve` (must also be excluded)
        p5 = root / "203"
        p5.mkdir()
        (p5 / "cmdline").write_bytes(b"/home/u/.local/bin/agy\0--quiet\0remote-control\0serve\0")
        (p5 / "environ").write_bytes(b"HOME=/tmp/cli-h1\0")

        found_acp = aa.find_live_acp_processes(proc_root=root)
        assert len(found_acp) == 1, found_acp
        assert found_acp[0].pid == 101
        assert found_acp[0].gemini_home == "/tmp/h1"
        assert found_acp[0].session_id == "sess-1"
        assert found_acp[0].cwd == "/tmp"

        found_cli = aa.find_live_cli_processes(proc_root=root)
        assert len(found_cli) == 1, found_cli
        assert found_cli[0].pid == 201
        assert found_cli[0].home == "/tmp/cli-h1"
        assert found_cli[0].session_id == "cli-sess-1"
        assert found_cli[0].subcommand == "chat"
        assert found_cli[0].cwd == "/tmp"
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

        meta = json.loads((sb.vault / "chat" / "acct-a" / "meta.json").read_text(encoding="utf-8"))
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
        meta = json.loads((sb.vault / "chat" / "acct-a" / "meta.json").read_text(encoding="utf-8"))
        assert meta["email"] == "alice@example.com"

        # With --update-email it updates
        rc, out, err = sb.run_cli(
            ["verify", "acct-a", "--update-email"], extra_env={"AGY_USERINFO_URL": userinfo_url}
        )
        assert rc == 0, err
        meta = json.loads((sb.vault / "chat" / "acct-a" / "meta.json").read_text(encoding="utf-8"))
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
        meta = json.loads((sb.vault / "chat" / "acct-a" / "meta.json").read_text(encoding="utf-8"))
        assert meta["stale"] is True
    finally:
        server.shutdown()
        server.server_close()
        sb.cleanup()


def test_legacy_chat_vault_compat_and_migration() -> None:
    sb = Sandbox()
    try:
        tok_legacy = make_fake_token("REFRESH_LEGACY_123")
        tok_new = make_fake_token("REFRESH_NEW_456")

        legacy_dir = sb.vault / "legacy-work"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "acp_token.json").write_bytes(tok_legacy)
        os.chmod(legacy_dir / "acp_token.json", 0o600)
        (legacy_dir / "meta.json").write_text(
            json.dumps(
                {
                    "label": "legacy-work",
                    "saved_at": "2026-11-20T00:00:00Z",
                    "project_id": "aicode-consumers",
                    "fingerprint": hashlib.sha256(b"REFRESH_LEGACY_123").hexdigest(),
                    "fingerprint_short": hashlib.sha256(b"REFRESH_LEGACY_123").hexdigest()[:8],
                    "email": "legacy@example.com",
                    "note": "from-v1",
                }
            ),
            encoding="utf-8",
        )

        # Save a new-layout Chat account too
        sb.write_live(tok_new)
        assert sb.run_inproc(["save", "modern-work"])[0] == 0

        # List recognizes both new and legacy Chat accounts
        rc, out, err = sb.run_inproc(["list", "--json"])
        assert rc == 0, err
        lst = json.loads(out)
        by_label = {a["label"]: a for a in lst["accounts"]}
        assert set(by_label) == {"legacy-work", "modern-work"}
        assert by_label["legacy-work"]["legacy"] is True
        assert by_label["modern-work"]["legacy"] is False

        # Use switches cleanly to legacy-work
        rc, out, err = sb.run_inproc(["use", "legacy-work"])
        assert rc == 0, err
        assert sb.live_token.read_bytes() == tok_legacy
        rc, out, _ = sb.run_inproc(["current", "--json"])
        assert rc == 0
        assert json.loads(out)["matched_labels"] == ["legacy-work"]

        # Saving same label without --force refuses
        rc, _, err = sb.run_inproc(["save", "legacy-work"])
        assert rc != 0 and "已存在" in err

        # Saving with --force migrates legacy-work into chat/legacy-work and preserves email/note
        rc, out, err = sb.run_inproc(["save", "legacy-work", "--force", "--json"])
        assert rc == 0, err
        res = json.loads(out)
        assert res.get("migrated_from_legacy") is True
        assert not (sb.vault / "legacy-work").exists()
        migrated_meta = json.loads((sb.vault / "chat" / "legacy-work" / "meta.json").read_text(encoding="utf-8"))
        assert migrated_meta["email"] == "legacy@example.com"
        assert migrated_meta["note"] == "from-v1"
        assert migrated_meta["surface"] == "chat"
    finally:
        sb.cleanup()


def test_cli_save_list_current_and_permissions() -> None:
    sb = Sandbox()
    try:
        cli_a = make_fake_cli_token(
            "CLI_REFRESH_ALPHA_001",
            "CLI_ACCESS_ALPHA_001",
            auth_method="consumer",
            id_token_email="alpha@example.com",
        )
        sb.write_live_cli(cli_a)
        # Ensure CLI completely ignores GEMINI_HOME even if live Chat file is missing/broken
        sb.live_token.write_bytes(b"not json at all")

        rc, out, err = sb.run_inproc(["save", "cli-a", "--surface", "cli", "--note", "personal"])
        assert rc == 0, f"cli save failed: {err}"
        assert "[cli]" in out and "cli-a" in out
        for secret in ("CLI_REFRESH_ALPHA_001", "CLI_ACCESS_ALPHA_001"):
            assert secret not in out + err

        saved_dir = sb.vault / "cli" / "cli-a"
        assert _mode(sb.vault / "cli") == 0o700
        assert _mode(saved_dir) == 0o700
        saved_tok = saved_dir / "antigravity-oauth-token"
        saved_meta = saved_dir / "meta.json"
        assert _mode(saved_tok) == 0o600
        assert _mode(saved_meta) == 0o600
        assert saved_tok.read_bytes() == cli_a

        meta = json.loads(saved_meta.read_text(encoding="utf-8"))
        assert meta["surface"] == "cli"
        assert meta["auth_method"] == "consumer"
        assert meta["email"] == "alpha@example.com"
        assert meta["email_source"] == "id_token_email"
        assert meta["note"] == "personal"

        # --surface before subcommand also works
        rc, out, err = sb.run_inproc(["--surface", "cli", "current", "--json"])
        assert rc == 0, err
        cur = json.loads(out)
        assert cur["surface"] == "cli"
        assert cur["matched_labels"] == ["cli-a"]
        assert cur["auth_method"] == "consumer"
        assert cur["live_process_count"] == 0
        assert cur["cli_dir"] == str(sb.cli_dir)

        rc, out, err = sb.run_inproc(["list", "--surface", "cli", "--json"])
        assert rc == 0, err
        lst = json.loads(out)
        assert lst["surface"] == "cli"
        assert len(lst["accounts"]) == 1 and lst["accounts"][0]["current"] is True

        # Conflicting --surface before/after subcommand is rejected
        rc, _, err = sb.run_inproc(["--surface", "cli", "current", "--surface", "chat"])
        assert rc != 0 and "冲突" in err
    finally:
        sb.cleanup()


def test_cli_use_switch_backup_and_dry_run() -> None:
    sb = Sandbox()
    try:
        cli_a = make_fake_cli_token("CLI_REFRESH_A", "CLI_ACCESS_A", id_token_email="a@example.com")
        cli_b = make_fake_cli_token("CLI_REFRESH_B", "CLI_ACCESS_B", id_token_email="b@example.com")
        scope_before = sb.cli_scope_file.read_bytes()
        state_before = (sb.cli_dir / "antigravity_state.pbtxt").read_bytes()

        sb.write_live_cli(cli_a)
        assert sb.run_inproc(["save", "cli-a", "--surface", "cli"])[0] == 0
        sb.write_live_cli(cli_b)
        assert sb.run_inproc(["save", "cli-b", "--surface", "cli"])[0] == 0

        # Dry run
        rc, out, err = sb.run_inproc(["use", "cli-a", "--surface", "cli", "--dry-run"])
        assert rc == 0, err
        assert "[dry-run]" in out and "[cli]" in out
        assert sb.live_cli_token.read_bytes() == cli_b
        assert not (sb.vault / "cli" / "_backup").exists()

        # Real switch cli-b -> cli-a
        rc, out, err = sb.run_inproc(["use", "cli-a", "--surface", "cli"])
        assert rc == 0, err
        assert "重新启动 agy CLI" in out
        for secret in ("CLI_REFRESH_A", "CLI_ACCESS_A", "CLI_REFRESH_B", "CLI_ACCESS_B"):
            assert secret not in out + err
        assert sb.live_cli_token.read_bytes() == cli_a
        assert _mode(sb.live_cli_token) == 0o600
        # Auxiliary files remain untouched
        assert sb.cli_scope_file.read_bytes() == scope_before
        assert (sb.cli_dir / "antigravity_state.pbtxt").read_bytes() == state_before

        backups = sorted((sb.vault / "cli" / "_backup").iterdir())
        assert len(backups) == 1 and backups[0].name.endswith("-cli-b")
        assert (backups[0] / "antigravity-oauth-token").read_bytes() == cli_b

        # Rollback on write verification failure
        orig_atomic = aa.atomic_write_bytes
        calls = {"count": 0}

        def corrupting_write(path: Path, data: bytes, mode: int = 0o600, dir_mode: int = 0o700) -> None:
            if path == sb.live_cli_token:
                calls["count"] += 1
                if calls["count"] == 1:
                    orig_atomic(path, b'{"token": "broken"}', mode=mode, dir_mode=dir_mode)
                    return
            orig_atomic(path, data, mode=mode, dir_mode=dir_mode)

        aa.atomic_write_bytes = corrupting_write
        try:
            rc, _, err = sb.run_inproc(["use", "cli-b", "--surface", "cli"])
        finally:
            aa.atomic_write_bytes = orig_atomic
        assert rc != 0 and "回滚" in err
        assert sb.live_cli_token.read_bytes() == cli_a
    finally:
        sb.cleanup()


def test_cli_use_blocks_live_agy_and_keyring() -> None:
    sb = Sandbox()
    proc_match = None
    proc_other = None
    proc_remote = None
    try:
        cli_a = make_fake_cli_token("CLI_REFRESH_PROC_A", "CLI_ACCESS_PROC_A")
        cli_b = make_fake_cli_token("CLI_REFRESH_PROC_B", "CLI_ACCESS_PROC_B")
        sb.write_live_cli(cli_a)
        assert sb.run_inproc(["save", "cli-a", "--surface", "cli"])[0] == 0
        sb.write_live_cli(cli_b)
        assert sb.run_inproc(["save", "cli-b", "--surface", "cli"])[0] == 0

        sleep_bin = shutil.which("sleep")
        sh_bin = shutil.which("sh")
        assert sleep_bin and sh_bin
        fake_agy = sb.root / "agy"
        os.symlink(sleep_bin, fake_agy)

        # 1. `agy` under a different HOME must NOT block
        other_home = sb.root / "other_user_home"
        other_home.mkdir()
        proc_other = subprocess.Popen(
            [str(fake_agy), "60"],
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(other_home)},
        )
        # 2. `agy remote-control serve` under OUR HOME must NOT block
        proc_remote = subprocess.Popen(
            [str(fake_agy), "-s", "remote-control", "serve"],
            executable=sh_bin,
            stdin=subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(sb.home)},
        )
        time.sleep(0.15)
        rc, _, err = sb.run_inproc(["use", "cli-a", "--surface", "cli"])
        assert rc == 0, f"remote-control / other HOME should not block: {err}"
        assert sb.live_cli_token.read_bytes() == cli_a

        # 3. Interactive `agy` under OUR HOME MUST block (exit 2)
        proc_match = subprocess.Popen(
            [str(fake_agy), "60"],
            cwd=str(sb.root),
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(sb.home),
                "ORCA_AGENT_SESSION_ID": "cli-live-sess-42",
            },
        )
        time.sleep(0.15)
        rc, out, _ = sb.run_inproc(["current", "--surface", "cli", "--json"])
        assert rc == 0
        assert json.loads(out)["live_process_count"] == 1

        rc, _, err = sb.run_inproc(["use", "cli-b", "--surface", "cli"])
        assert rc == 2, f"expected exit 2 when live agy CLI process exists, got {rc}: {err}"
        assert str(proc_match.pid) in err
        assert "cli-live-sess-42" in err
        assert sb.live_cli_token.read_bytes() == cli_a

        proc_match.terminate()
        proc_match.wait(timeout=3)
        proc_match = None

        # 4. Active Linux Secret Service keyring blocks unless keyring-unavailable marker exists
        if sys.platform.startswith("linux"):
            bin_dir = sb.root / "mock_bin"
            bin_dir.mkdir()
            dbus_mock = bin_dir / "dbus-send"
            dbus_mock.write_text("#!/bin/sh\necho '   boolean true'\n", encoding="utf-8")
            os.chmod(dbus_mock, 0o755)
            kr_env = {
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                "AGY_TEST_KEYRING_CHECK": "1",
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/fake-bus",
                "SSH_TTY": "",
                "SSH_CLIENT": "",
                "SSH_CONNECTION": "",
                "WSL_DISTRO_NAME": "",
                "WSL_INTEROP": "",
            }
            rc, _, err = sb.run_cli(["use", "cli-b", "--surface", "cli"], extra_env=kr_env)
            assert rc == 2 and "keyring" in err, f"expected keyring gate refusal, got rc={rc}: {err}"

            marker = sb.cli_dir / "cache" / "antigravity-keyring-unavailable"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("1\n", encoding="utf-8")
            rc, _, err = sb.run_cli(["use", "cli-b", "--surface", "cli"], extra_env=kr_env)
            assert rc == 0, f"keyring-unavailable marker should allow switch: {err}"
            assert sb.live_cli_token.read_bytes() == cli_b
    finally:
        for p in (proc_match, proc_other, proc_remote):
            if p is not None:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
        sb.cleanup()


def test_cli_verify_http_flow_and_redaction() -> None:
    sb = Sandbox()
    state = {"mode": "ok", "email": "cli-alice@example.com"}

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
                self.wfile.write(json.dumps({"error": "invalid_grant", "error_description": "Revoked"}).encode())
                return
            assert body.get("grant_type") == ["refresh_token"]
            assert body.get("client_id") == [aa._CLI_OAUTH_CLIENTS["consumer"]["client_id"]]
            assert body.get("client_secret") == [aa._CLI_OAUTH_CLIENTS["consumer"]["client_secret"]]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"access_token": "CLI_VERIFY_ACCESS_XYZ", "token_type": "Bearer"}).encode()
            )

        def do_GET(self) -> None:
            if self.headers.get("Authorization") != "Bearer CLI_VERIFY_ACCESS_XYZ":
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
        tok = make_fake_cli_token(
            "CLI_VERIFY_REFRESH_SECRET",
            "CLI_OLD_ACCESS_SECRET",
            auth_method="consumer",
            id_token_email="cli-alice@example.com",
        )
        sb.write_live_cli(tok)
        rc, _, err = sb.run_cli(["save", "cli-a", "--surface", "cli"])
        assert rc == 0, err

        ver_env = {"AGY_TOKEN_URI": token_url, "AGY_USERINFO_URL": userinfo_url}
        rc, out, err = sb.run_cli(["verify", "cli-a", "--surface", "cli"], extra_env=ver_env)
        assert rc == 0, err
        assert "[cli]" in out and "cli-alice@example.com" in out and "matched" in out
        for secret in (
            "CLI_VERIFY_REFRESH_SECRET",
            "CLI_OLD_ACCESS_SECRET",
            "CLI_VERIFY_ACCESS_XYZ",
            aa._CLI_OAUTH_CLIENTS["consumer"]["client_secret"],
        ):
            assert secret not in out + err

        # Mismatch against id_token_email-seeded meta requires --update-email
        state["email"] = "cli-bob@example.com"
        rc, out, err = sb.run_cli(["verify", "--surface", "cli"], extra_env=ver_env)
        assert rc != 0 and "身份不匹配" in err

        rc, out, err = sb.run_cli(["verify", "--surface", "cli", "--update-email"], extra_env=ver_env)
        assert rc == 0, err
        meta = json.loads((sb.vault / "cli" / "cli-a" / "meta.json").read_text(encoding="utf-8"))
        assert meta["email"] == "cli-bob@example.com"
        assert meta["email_source"] == "userinfo"

        # invalid_grant marks stale=True and exits 3 without leaking secrets
        state["mode"] = "invalid_grant"
        rc, out, err = sb.run_cli(["verify", "cli-a", "--surface", "cli"], extra_env=ver_env)
        assert rc == 3 and "invalid_grant" in err
        for secret in (
            "CLI_VERIFY_REFRESH_SECRET",
            "CLI_OLD_ACCESS_SECRET",
            "CLI_VERIFY_ACCESS_XYZ",
            aa._CLI_OAUTH_CLIENTS["consumer"]["client_secret"],
        ):
            assert secret not in out + err
        meta = json.loads((sb.vault / "cli" / "cli-a" / "meta.json").read_text(encoding="utf-8"))
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
    check("legacy_chat_vault_compat_and_migration", test_legacy_chat_vault_compat_and_migration)
    check("cli_save_list_current_and_permissions", test_cli_save_list_current_and_permissions)
    check("cli_use_switch_backup_and_dry_run", test_cli_use_switch_backup_and_dry_run)
    check("cli_use_blocks_live_agy_and_keyring", test_cli_use_blocks_live_agy_and_keyring)
    check("cli_verify_http_flow_and_redaction", test_cli_verify_http_flow_and_redaction)
    if FAILS:
        print(f"\n{len(FAILS)} check(s) failed: {', '.join(FAILS)}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
