#!/usr/bin/env python3
"""Manage and switch AntiGravity credentials for Orca Chat and CLI (`--surface chat|cli`).

Two surfaces:
1. `chat` (default):
   Credential file: $GEMINI_HOME/antigravity-acp/acp_token.json
   (default ~/.config/orca/antigravity-chat/antigravity-acp/acp_token.json).
   Format: Google OAuth `authorized_user` JSON (`client_id`, `client_secret`, `refresh_token`,
   `token_uri`, optional `project_id`).
   Safety gate: refuses `use` while any matching `agy_acp_server.par` process is running.

2. `cli`:
   Credential file: ~/.gemini/antigravity-cli/antigravity-oauth-token
   (overridable via `--home` or `AGY_CLI_DIR`, never `GEMINI_HOME`).
   Format: `agy` StoredToken JSON (`token.refresh_token`, `token.access_token`, `auth_method`,
   optional `id_token_email`, `writer_id`, `project_id`).
   Note: `~/.config/antigravity/credential-user-scope` belongs to `authstore` (plugin/MCP OAuth
   scoping), not CLI's own login token. On headless Linux without `org.freedesktop.secrets`,
   `agy` falls back to file storage (`antigravity-oauth-token`) without keyring.
   Safety gate: refuses `use` while any matching `agy` CLI process (excluding `agy_acp_server`
   and `agy remote-control`) is running, and unless a live Secret Service is *proven absent*
   (fail-closed: `present` or `unknown` both refuse; `--assume-file-store` overrides at own risk).

Vault layout (default: ~/.config/antigravity-accounts, 0700):
  <surface>/<label>/<token_file>   0600, raw byte copy of the credential file
  <surface>/<label>/meta.json      0600, label / surface / saved_at / sha256 fp / email / note
  <surface>/_backup/<ts>-<label|unknown>/<token_file>  0600, pre-switch backup
  (Legacy Chat flat layout `<vault>/<label>/acp_token.json` is still recognized.)

Usage:
  agy-account save <label> [--surface chat|cli] [--note TEXT] [--email EMAIL] [--force]
  agy-account list [--surface chat|cli] [--json]
  agy-account current [--surface chat|cli] [--json]
  agy-account use <label> [--surface chat|cli] [--dry-run] [--json]
  agy-account verify [label] [--surface chat|cli] [--update-email] [--json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SURFACES = ("chat", "cli")
DEFAULT_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
CLI_DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
BACKUP_DIR_NAME = "_backup"
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

ACP_REL_TOKEN = Path("antigravity-acp") / "acp_token.json"
ACP_REL_SETTINGS = Path("antigravity-acp") / "settings.json"
CHAT_TOKEN_FILENAME = "acp_token.json"

CLI_REL_DIR = Path(".gemini") / "antigravity-cli"
CLI_TOKEN_FILENAME = "antigravity-oauth-token"

# `cache/antigravity-keyring-unavailable` inside the CLI dir is NOT read or written by `agy`: it is
# the Orca daemon's marker meaning "Orca cannot verify which credential store agy will use". Never
# use it as a gate here; the real signal is a Secret Service probe below.
SECRET_SERVICE_NAME = "org.freedesktop.secrets"
SECRET_SERVICE_TIMEOUT_S = 5.0

# Extracted from `~/.local/bin/agy` (`cli/backend/auth/auth.getOauthParams`) so `verify --surface cli`
# can exchange `refresh_token` -> `access_token` -> Google `userinfo` without spending model quota.
_CLI_OAUTH_CLIENTS: dict[str, dict[str, str]] = {
    "consumer": {
        "client_id": "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com",
        "client_secret": "GOCSPX-K58FWR486LdLJ1mLB8sXC4z6qDA",
    },
    "gcp": {
        "client_id": "884354919052-36trc1jjb3tguiac32ov6cod268c5blh.apps.googleusercontent.com",
        "client_secret": "GOCSPX-9YQWpF7RWDC0QTdj-YxKMwR0Zts",
    },
}


class AccountError(Exception):
    """Domain error with a sanitized, user-facing message."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class TokenInfo:
    fingerprint: str
    project_id: str
    token_uri: str
    size_bytes: int
    auth_method: str = ""
    id_token_email: str | None = None

    @property
    def short_fp(self) -> str:
        return self.fingerprint[:8]


@dataclass(frozen=True)
class AcpProcess:
    pid: int
    gemini_home: str | None
    session_id: str | None
    cwd: str | None
    argv0: str


@dataclass(frozen=True)
class CliProcess:
    pid: int
    home: str | None
    session_id: str | None
    cwd: str | None
    argv0: str
    subcommand: str | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_backup_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def validate_surface(surface: str | None) -> str:
    val = surface or "chat"
    if val not in SURFACES:
        raise AccountError(f"不支持的 surface: {val!r}（仅支持 chat 或 cli）")
    return val


def surface_token_filename(surface: str) -> str:
    return CHAT_TOKEN_FILENAME if surface == "chat" else CLI_TOKEN_FILENAME


def validate_label(label: str) -> str:
    if not label or label in {".", "..", BACKUP_DIR_NAME}:
        raise AccountError(f"非法 label: {label!r}")
    if label.startswith("_") or label.startswith("."):
        raise AccountError(f"label 不能以 '_' 或 '.' 开头: {label!r}")
    if not LABEL_RE.match(label):
        raise AccountError(
            f"非法 label {label!r}：只允许字母/数字开头，后接字母、数字、'.'、'_'、'-'（最多 64 字符）。"
        )
    return label


def fingerprint_refresh_token(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def parse_chat_token_bytes(raw: bytes, source: str) -> TokenInfo:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AccountError(f"凭据文件不是有效 JSON（{source}）：{exc.__class__.__name__}") from None
    if not isinstance(data, dict):
        raise AccountError(f"凭据文件顶层必须是 JSON 对象（{source}）")

    for req in ("client_id", "client_secret", "refresh_token", "token_uri"):
        val = data.get(req)
        if not isinstance(val, str) or not val.strip():
            raise AccountError(f"凭据文件缺少非空字段 {req!r}（{source}）")

    project_id = data.get("project_id")
    if project_id is None:
        project_id_str = ""
    elif isinstance(project_id, str):
        project_id_str = project_id
    else:
        raise AccountError(f"凭据文件字段 'project_id' 类型异常（{source}）")

    fp = fingerprint_refresh_token(data["refresh_token"])
    return TokenInfo(
        fingerprint=fp,
        project_id=project_id_str,
        token_uri=data["token_uri"],
        size_bytes=len(raw),
    )


def parse_token_bytes(raw: bytes, source: str) -> TokenInfo:
    """Backward-compatible alias for Chat `acp_token.json` parsing."""
    return parse_chat_token_bytes(raw, source)


def parse_cli_token_bytes(raw: bytes, source: str) -> TokenInfo:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AccountError(f"凭据文件不是有效 JSON（{source}）：{exc.__class__.__name__}") from None
    if not isinstance(data, dict):
        raise AccountError(f"凭据文件顶层必须是 JSON 对象（{source}）")

    token_obj = data.get("token")
    if not isinstance(token_obj, dict):
        raise AccountError(f"CLI 凭据文件缺少对象字段 'token'（{source}）")

    refresh_token = token_obj.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise AccountError(f"CLI 凭据文件缺少非空字段 'token.refresh_token'（{source}）")

    access_token = token_obj.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise AccountError(f"CLI 凭据文件缺少非空字段 'token.access_token'（{source}）")

    auth_method = data.get("auth_method")
    if not isinstance(auth_method, str) or not auth_method.strip():
        raise AccountError(f"CLI 凭据文件缺少非空字段 'auth_method'（{source}）")

    project_id = data.get("project_id")
    if project_id is None:
        project_id_str = ""
    elif isinstance(project_id, str):
        project_id_str = project_id
    else:
        raise AccountError(f"CLI 凭据文件字段 'project_id' 类型异常（{source}）")

    id_email_raw = data.get("id_token_email")
    if id_email_raw is None:
        id_token_email: str | None = None
    elif isinstance(id_email_raw, str):
        id_token_email = id_email_raw.strip() or None
    else:
        raise AccountError(f"CLI 凭据文件字段 'id_token_email' 类型异常（{source}）")

    token_uri = os.environ.get("AGY_TOKEN_URI", "").strip() or CLI_DEFAULT_TOKEN_URI
    fp = fingerprint_refresh_token(refresh_token)
    return TokenInfo(
        fingerprint=fp,
        project_id=project_id_str,
        token_uri=token_uri,
        size_bytes=len(raw),
        auth_method=auth_method.strip(),
        id_token_email=id_token_email,
    )


def parse_surface_token_bytes(surface: str, raw: bytes, source: str) -> TokenInfo:
    if surface == "cli":
        return parse_cli_token_bytes(raw, source)
    return parse_chat_token_bytes(raw, source)


def _read_secret_oauth_fields(raw: bytes, source: str, surface: str = "chat") -> dict[str, str]:
    """Read OAuth refresh credentials solely in memory for `verify`. Never log or print."""
    if surface == "cli":
        info = parse_cli_token_bytes(raw, source)
        client = _CLI_OAUTH_CLIENTS.get(info.auth_method)
        if client is None:
            raise AccountError(
                f"不支持的 CLI auth_method {info.auth_method!r}（仅支持 {', '.join(sorted(_CLI_OAUTH_CLIENTS))}），"
                "无法通过 refresh_token 校验邮箱。"
            )
        data = json.loads(raw.decode("utf-8"))
        return {
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "refresh_token": data["token"]["refresh_token"],
            "token_uri": info.token_uri,
        }

    parse_chat_token_bytes(raw, source)
    data = json.loads(raw.decode("utf-8"))
    return {
        "client_id": data["client_id"],
        "client_secret": data["client_secret"],
        "refresh_token": data["refresh_token"],
        "token_uri": data["token_uri"],
    }


def ensure_dir_secure(path: Path, mode: int = 0o700) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, mode)


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600, dir_mode: int = 0o700) -> None:
    ensure_dir_secure(path.parent, dir_mode)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
        os.chmod(path, mode)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, payload: dict[str, Any], mode: int = 0o600, dir_mode: int = 0o700) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_bytes(path, text.encode("utf-8"), mode=mode, dir_mode=dir_mode)


def resolve_vault_dir(vault_arg: str | None) -> Path:
    if vault_arg:
        return Path(vault_arg).expanduser()
    env_dir = os.environ.get("AGY_ACCOUNTS_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    return Path.home() / ".config" / "antigravity-accounts"


def resolve_surface_vault(vault: Path, surface: str) -> Path:
    return vault / surface


def _is_acp_server_args(args: list[str]) -> bool:
    if not args:
        return False
    base0 = Path(args[0]).name
    if base0 == "agy_acp_server.par":
        return True
    if len(args) >= 2 and (base0 == "python" or base0.startswith("python3")):
        if Path(args[1]).name == "agy_acp_server.par":
            return True
    return False


def _first_cli_subcommand(args: list[str]) -> str | None:
    for arg in args[1:]:
        if arg == "--":
            break
        if arg.startswith("-"):
            continue
        return arg
    return None


def _is_cli_agy_args(args: list[str]) -> bool:
    """Match live `agy` CLI processes, excluding `agy_acp_server*` and `agy remote-control`."""
    if not args:
        return False
    base0 = Path(args[0]).name
    if base0 != "agy" or "agy_acp_server" in args[0]:
        return False
    if _first_cli_subcommand(args) == "remote-control":
        return False
    return True


def _parse_proc_environ(raw: bytes) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if b"=" not in item:
            continue
        k, v = item.split(b"=", 1)
        env[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
    return env


def find_live_acp_processes(proc_root: Path = Path("/proc")) -> list[AcpProcess]:
    results: list[AcpProcess] = []
    if proc_root.is_dir():
        for entry in sorted(proc_root.iterdir(), key=lambda p: p.name):
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            try:
                cmd_raw = (entry / "cmdline").read_bytes()
            except OSError:
                continue
            args = [part.decode("utf-8", "replace") for part in cmd_raw.split(b"\0") if part]
            if not _is_acp_server_args(args):
                continue

            gemini_home: str | None = None
            session_id: str | None = None
            try:
                env = _parse_proc_environ((entry / "environ").read_bytes())
                gh = env.get("GEMINI_HOME", "").strip()
                if gh:
                    gemini_home = gh
                else:
                    home = env.get("HOME", "").strip()
                    if home:
                        gemini_home = str(Path(home) / ".gemini")
                sid = env.get("ORCA_AGENT_SESSION_ID", "").strip()
                if sid:
                    session_id = sid
            except OSError:
                gemini_home = None

            cwd: str | None = None
            try:
                cwd = os.readlink(entry / "cwd")
            except OSError:
                cwd = None

            results.append(
                AcpProcess(
                    pid=pid,
                    gemini_home=gemini_home,
                    session_id=session_id,
                    cwd=cwd,
                    argv0=args[0],
                )
            )
        return results

    # Fallback for non-/proc platforms (e.g. macOS)
    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return results
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        args = parts[1].split()
        if _is_acp_server_args(args):
            results.append(
                AcpProcess(
                    pid=pid,
                    gemini_home=None,
                    session_id=None,
                    cwd=None,
                    argv0=args[0],
                )
            )
    return results


def find_live_cli_processes(proc_root: Path = Path("/proc")) -> list[CliProcess]:
    results: list[CliProcess] = []
    if proc_root.is_dir():
        for entry in sorted(proc_root.iterdir(), key=lambda p: p.name):
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            try:
                cmd_raw = (entry / "cmdline").read_bytes()
            except OSError:
                continue
            args = [part.decode("utf-8", "replace") for part in cmd_raw.split(b"\0") if part]
            if not _is_cli_agy_args(args):
                continue

            home_val: str | None = None
            session_id: str | None = None
            try:
                env = _parse_proc_environ((entry / "environ").read_bytes())
                h = env.get("HOME", "").strip()
                if h:
                    home_val = h
                sid = env.get("ORCA_AGENT_SESSION_ID", "").strip()
                if sid:
                    session_id = sid
            except OSError:
                home_val = None

            cwd: str | None = None
            try:
                cwd = os.readlink(entry / "cwd")
            except OSError:
                cwd = None

            subcmd = _first_cli_subcommand(args)
            results.append(
                CliProcess(
                    pid=pid,
                    home=home_val,
                    session_id=session_id,
                    cwd=cwd,
                    argv0=args[0],
                    subcommand=subcmd,
                )
            )
        return results

    # Fallback for non-/proc platforms (e.g. macOS)
    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return results
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        args = parts[1].split()
        if _is_cli_agy_args(args):
            subcmd = _first_cli_subcommand(args)
            results.append(
                CliProcess(
                    pid=pid,
                    home=None,
                    session_id=None,
                    cwd=None,
                    argv0=args[0],
                    subcommand=subcmd,
                )
            )
    return results


def filter_conflicting_processes(procs: list[AcpProcess], target_home: Path) -> list[AcpProcess]:
    target_real = os.path.realpath(target_home)
    conflicts: list[AcpProcess] = []
    for proc in procs:
        if proc.gemini_home is None:
            conflicts.append(proc)
            continue
        if os.path.realpath(proc.gemini_home) == target_real:
            conflicts.append(proc)
    return conflicts


def filter_conflicting_cli_processes(
    procs: list[CliProcess], target_user_home: Path | None
) -> list[CliProcess]:
    if target_user_home is None:
        return list(procs)
    target_real = os.path.realpath(target_user_home)
    conflicts: list[CliProcess] = []
    for proc in procs:
        if proc.home is None:
            conflicts.append(proc)
            continue
        if os.path.realpath(proc.home) == target_real:
            conflicts.append(proc)
    return conflicts


@dataclass(frozen=True)
class SecretServiceProbe:
    """Result of probing the session bus for `org.freedesktop.secrets`."""

    verdict: str  # "present" | "absent" | "unknown"
    source: str  # command probed (or why nothing could be probed)
    detail: str  # short stdout/error summary, never contains credentials


# (kind, argv) probed in order; the first determinate answer wins.
_SECRET_SERVICE_PROBES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "gdbus",
        (
            "gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
            "--object-path", "/org/freedesktop/DBus",
            "--method", "org.freedesktop.DBus.NameHasOwner", SECRET_SERVICE_NAME,
        ),
    ),
    ("busctl", ("busctl", "--user", "--no-pager", "list")),
)


def probe_secret_service(
    runner: Any = None, timeout: float = SECRET_SERVICE_TIMEOUT_S
) -> SecretServiceProbe:
    """Return present/absent/unknown for a usable Secret Service; any failure is `unknown`."""
    run = runner or subprocess.run
    failures: list[str] = []
    for kind, argv in _SECRET_SERVICE_PROBES:
        try:
            res = run(list(argv), capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            failures.append(f"{kind}: {type(exc).__name__}")
            continue
        stdout = (res.stdout or "").strip()
        stderr = (res.stderr or "").strip()
        if res.returncode != 0:
            failures.append(f"{kind}: 退出码 {res.returncode} {stderr[:100]}".rstrip())
            continue
        if kind == "gdbus":
            if "true" in stdout:
                return SecretServiceProbe("present", " ".join(argv), stdout[:100])
            if "false" in stdout:
                return SecretServiceProbe("absent", " ".join(argv), stdout[:100])
            failures.append(f"gdbus: 无法解析输出 {stdout[:100]}")
            continue
        if SECRET_SERVICE_NAME in stdout:
            return SecretServiceProbe("present", " ".join(argv), f"列表含 {SECRET_SERVICE_NAME}")
        return SecretServiceProbe(
            "absent", " ".join(argv), f"会话总线列表不含 {SECRET_SERVICE_NAME}"
        )
    return SecretServiceProbe(
        "unknown",
        "探测命令全部失败" if failures else "无可用探测命令（gdbus / busctl 均不可用）",
        "；".join(failures) if failures else "未找到 gdbus 或 busctl",
    )


def keyring_gate_message(probe: SecretServiceProbe) -> str:
    """Explain why a non-`absent` probe blocks a file-replacement switch."""
    if probe.verdict == "present":
        why = f"检测到 Secret Service（{probe.source} → {probe.detail}）"
    else:
        why = f"探测失败，无法确认本机凭据存储（{probe.source} → {probe.detail}）"
    return (
        f"拒绝切换 CLI：{why}。\n"
        "此时 agy 可能把凭据搬进密钥库，仅替换 "
        f"{CLI_TOKEN_FILENAME} 不保证生效（甚至两份存储各存一半）。\n"
        "如需继续，请加 `--assume-file-store` 明确承担该风险；或改用 `agy auth login` 重新登录目标账号。"
    )


def _is_real_user_home() -> bool:
    try:
        real_home = pwd.getpwuid(os.getuid()).pw_dir
    except KeyError:
        return False
    return os.path.realpath(Path.home()) == os.path.realpath(real_home)


def resolve_gemini_home(home_arg: str | None, proc_root: Path = Path("/proc")) -> tuple[Path, str]:
    if home_arg:
        return Path(home_arg).expanduser(), "--home"
    env_home = os.environ.get("GEMINI_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser(), "GEMINI_HOME"

    default_orca = Path.home() / ".config" / "orca" / "antigravity-chat"
    if _is_real_user_home() and not os.environ.get("AGY_NO_PROC_DISCOVERY"):
        live = find_live_acp_processes(proc_root)
        homes = sorted({os.path.realpath(p.gemini_home) for p in live if p.gemini_home})
        if len(homes) == 1:
            return Path(homes[0]), "live-acp-proc"
        if len(homes) > 1:
            default_real = os.path.realpath(default_orca)
            if default_real in homes:
                return default_orca, "default-orca-chat"
            raise AccountError(
                "检测到多个不同 GEMINI_HOME 的 live agy_acp_server.par 进程，请显式传入 --home 或设置 GEMINI_HOME。"
            )
    return default_orca, "default"


def resolve_cli_dir(home_arg: str | None) -> tuple[Path, str, Path | None]:
    """Resolve CLI state dir (~/.gemini/antigravity-cli). Never reads GEMINI_HOME."""
    if home_arg:
        p = Path(home_arg).expanduser()
        if (p / "antigravity-cli").is_dir() and not (p / ".gemini").exists():
            return p / "antigravity-cli", "--home", None
        if p.name == "antigravity-cli" and ((p / CLI_TOKEN_FILENAME).exists() or (p / "settings.json").exists()):
            return p, "--home", None
        return p / CLI_REL_DIR, "--home", p

    env_cli_dir = os.environ.get("AGY_CLI_DIR", "").strip()
    if env_cli_dir:
        return Path(env_cli_dir).expanduser(), "AGY_CLI_DIR", None

    user_home = Path.home()
    return user_home / CLI_REL_DIR, "HOME", user_home


def _read_account_record(entry: Path, surface: str, legacy: bool = False) -> dict[str, Any] | None:
    token_filename = surface_token_filename(surface)
    token_path = entry / token_filename
    meta_path = entry / "meta.json"
    if not token_path.is_file():
        return None
    raw = token_path.read_bytes()
    info = parse_surface_token_bytes(surface, raw, str(token_path))
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except (OSError, json.JSONDecodeError):
            meta = {}
    return {
        "label": entry.name,
        "surface": surface,
        "legacy": legacy,
        "fingerprint": info.fingerprint,
        "fingerprint_short": info.short_fp,
        "project_id": meta.get("project_id", info.project_id),
        "auth_method": meta.get("auth_method", info.auth_method) if surface == "cli" else None,
        "saved_at": meta.get("saved_at"),
        "email": meta.get("email") or info.id_token_email,
        "email_source": meta.get("email_source") or ("id_token_email" if info.id_token_email else None),
        "verified_at": meta.get("verified_at"),
        "stale": bool(meta.get("stale", False)),
        "note": meta.get("note"),
        "size_bytes": info.size_bytes,
        "token_path": str(token_path),
        "meta_path": str(meta_path),
        "account_dir": str(entry),
    }


def load_saved_accounts(vault: Path, surface: str = "chat") -> list[dict[str, Any]]:
    surface = validate_surface(surface)
    if not vault.exists():
        return []

    records: list[dict[str, Any]] = []
    seen_labels: set[str] = set()

    surface_dir = resolve_surface_vault(vault, surface)
    if surface_dir.is_dir():
        for entry in sorted(surface_dir.iterdir(), key=lambda p: p.name):
            if not entry.is_dir():
                continue
            if entry.name == BACKUP_DIR_NAME or entry.name.startswith(".") or entry.name.startswith("_"):
                continue
            rec = _read_account_record(entry, surface, legacy=False)
            if rec is not None:
                records.append(rec)
                seen_labels.add(rec["label"])

    # Backward compatibility: recognize legacy flat Chat accounts `<vault>/<label>/acp_token.json`
    if surface == "chat":
        for entry in sorted(vault.iterdir(), key=lambda p: p.name):
            if not entry.is_dir():
                continue
            if entry.name in {BACKUP_DIR_NAME, *SURFACES} or entry.name.startswith(".") or entry.name.startswith("_"):
                continue
            if entry.name in seen_labels:
                continue
            rec = _read_account_record(entry, "chat", legacy=True)
            if rec is not None:
                records.append(rec)
                seen_labels.add(rec["label"])

    records.sort(key=lambda r: str(r["label"]))
    return records


def match_labels_by_fp(accounts: list[dict[str, Any]], fp: str) -> list[dict[str, Any]]:
    return [a for a in accounts if a["fingerprint"] == fp]



def _format_proc_line(proc: AcpProcess) -> str:
    parts = [f"PID {proc.pid}"]
    if proc.session_id:
        parts.append(f"会话 {proc.session_id}")
    if proc.cwd:
        parts.append(f"目录 {proc.cwd}")
    if proc.gemini_home:
        parts.append(f"GEMINI_HOME={proc.gemini_home}")
    return " | ".join(parts)


def _format_cli_proc_line(proc: CliProcess) -> str:
    parts = [f"PID {proc.pid}"]
    if proc.subcommand:
        parts.append(f"子命令 {proc.subcommand}")
    if proc.session_id:
        parts.append(f"会话 {proc.session_id}")
    if proc.cwd:
        parts.append(f"目录 {proc.cwd}")
    if proc.home:
        parts.append(f"HOME={proc.home}")
    return " | ".join(parts)


def _format_mtime(path: Path) -> str:
    st = path.stat()
    return datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class _SurfaceTarget:
    surface: str
    token_filename: str
    live_dir: Path
    live_token: Path
    home_source: str
    target_user_home: Path | None


def _resolve_surface_target(surface: str, home_arg: str | None) -> _SurfaceTarget:
    surface = validate_surface(surface)
    if surface == "cli":
        cli_dir, home_source, target_user_home = resolve_cli_dir(home_arg)
        return _SurfaceTarget(
            surface="cli",
            token_filename=CLI_TOKEN_FILENAME,
            live_dir=cli_dir,
            live_token=cli_dir / CLI_TOKEN_FILENAME,
            home_source=home_source,
            target_user_home=target_user_home,
        )
    gemini_home, home_source = resolve_gemini_home(home_arg)
    return _SurfaceTarget(
        surface="chat",
        token_filename=CHAT_TOKEN_FILENAME,
        live_dir=gemini_home,
        live_token=gemini_home / ACP_REL_TOKEN,
        home_source=home_source,
        target_user_home=None,
    )


def _ensure_surface_anchor(target: _SurfaceTarget, action: str) -> None:
    if target.surface == "chat":
        settings_file = target.live_dir / ACP_REL_SETTINGS
        if not settings_file.is_file():
            raise AccountError(
                f"未找到 {settings_file}（GEMINI_HOME 来源：{target.home_source}），拒绝{action}以防写入错误目录。"
            )
        return

    anchors = (
        target.live_token,
        target.live_dir / "settings.json",
        target.live_dir / "antigravity_state.pbtxt",
        target.live_dir / "installation_id",
    )
    if not target.live_dir.is_dir() or not any(p.is_file() for p in anchors):
        raise AccountError(
            f"未找到有效的 CLI 目录或锚点文件：{target.live_dir}（来源：{target.home_source}），拒绝{action}以防写入错误目录。"
        )


def cmd_save(args: argparse.Namespace) -> int:
    surface = validate_surface(getattr(args, "surface", None))
    label = validate_label(args.label)
    vault = resolve_vault_dir(args.vault)
    target = _resolve_surface_target(surface, args.home)
    _ensure_surface_anchor(target, "操作")

    if not target.live_token.is_file():
        raise AccountError(f"未找到当前 {surface} live 凭据文件：{target.live_token}")

    raw = target.live_token.read_bytes()
    info = parse_surface_token_bytes(surface, raw, str(target.live_token))

    accounts = load_saved_accounts(vault, surface)
    surface_vault = resolve_surface_vault(vault, surface)
    target_dir = surface_vault / label
    target_token = target_dir / target.token_filename
    target_meta = target_dir / "meta.json"

    legacy_dir = vault / label
    legacy_token = legacy_dir / CHAT_TOKEN_FILENAME
    legacy_meta = legacy_dir / "meta.json"
    has_legacy = surface == "chat" and legacy_token.is_file()

    if (target_token.exists() or has_legacy) and not args.force:
        raise AccountError(f"账号 {label!r} ({surface}) 已存在，若要覆盖请加 --force。")

    same_fp_others = [a["label"] for a in accounts if a["fingerprint"] == info.fingerprint and a["label"] != label]
    if same_fp_others and not args.force:
        raise AccountError(
            f"当前 {surface} 凭据指纹 ({info.short_fp}) 已保存在其他 label 下：{', '.join(same_fp_others)}；如仍要保存请加 --force。"
        )

    prev_meta: dict[str, Any] = {}
    prev_meta_path = target_meta if target_meta.is_file() else (legacy_meta if has_legacy and legacy_meta.is_file() else None)
    if prev_meta_path is not None:
        try:
            loaded = json.loads(prev_meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                prev_meta = loaded
        except (OSError, json.JSONDecodeError):
            prev_meta = {}

    same_fp_as_prev = prev_meta.get("fingerprint") == info.fingerprint
    if args.email is not None:
        email = args.email
        email_source: str | None = "manual"
    elif same_fp_as_prev and prev_meta.get("email"):
        email = prev_meta.get("email")
        email_source = prev_meta.get("email_source")
    elif surface == "cli" and info.id_token_email:
        email = info.id_token_email
        email_source = "id_token_email"
    else:
        email = None
        email_source = None

    verified_at = prev_meta.get("verified_at") if same_fp_as_prev else None
    stale = False if not same_fp_as_prev else bool(prev_meta.get("stale", False))
    note = args.note if args.note is not None else (prev_meta.get("note") if same_fp_as_prev else None)

    saved_at = utc_now_iso()
    meta: dict[str, Any] = {
        "label": label,
        "surface": surface,
        "saved_at": saved_at,
        "project_id": info.project_id,
        "fingerprint": info.fingerprint,
        "fingerprint_short": info.short_fp,
        "size_bytes": info.size_bytes,
        "note": note,
        "email": email,
        "email_source": email_source,
        "verified_at": verified_at,
        "stale": stale,
    }
    if surface == "cli":
        meta["auth_method"] = info.auth_method

    ensure_dir_secure(vault, 0o700)
    ensure_dir_secure(surface_vault, 0o700)
    ensure_dir_secure(target_dir, 0o700)
    atomic_write_bytes(target_token, raw, mode=0o600, dir_mode=0o700)
    atomic_write_json(target_meta, meta, mode=0o600, dir_mode=0o700)

    migrated_from_legacy = False
    if has_legacy:
        try:
            legacy_token.unlink(missing_ok=True)
            legacy_meta.unlink(missing_ok=True)
            legacy_dir.rmdir()
            migrated_from_legacy = True
        except OSError:
            migrated_from_legacy = True

    if args.json:
        payload: dict[str, Any] = {
            "action": "save",
            "surface": surface,
            "label": label,
            "fingerprint_short": info.short_fp,
            "project_id": info.project_id,
            "size_bytes": info.size_bytes,
            "saved_at": saved_at,
            "email": email,
            "note": note,
            "vault_dir": str(target_dir),
        }
        if surface == "cli":
            payload["auth_method"] = info.auth_method
        if migrated_from_legacy:
            payload["migrated_from_legacy"] = True
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        email_part = f" | email={email}" if email else ""
        note_part = f" | 备注={note}" if note else ""
        auth_part = f" | auth={info.auth_method}" if surface == "cli" else ""
        mig_part = "（已从旧布局迁移）" if migrated_from_legacy else ""
        print(
            f"已保存账号 [{surface}] {label}{mig_part}：fp={info.short_fp} | project_id={info.project_id or '-'}"
            f"{auth_part} | {info.size_bytes} B | {saved_at}{email_part}{note_part}"
        )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    surface = validate_surface(getattr(args, "surface", None))
    vault = resolve_vault_dir(args.vault)
    target = _resolve_surface_target(surface, args.home)

    accounts = load_saved_accounts(vault, surface)
    live_info: TokenInfo | None = None
    live_error: str | None = None
    if target.live_token.is_file():
        try:
            live_info = parse_surface_token_bytes(surface, target.live_token.read_bytes(), str(target.live_token))
        except AccountError as exc:
            live_error = str(exc)

    live_fp = live_info.fingerprint if live_info else None
    matched_labels = [a["label"] for a in accounts if live_fp and a["fingerprint"] == live_fp]

    if args.json:
        out_accounts = []
        for a in accounts:
            item: dict[str, Any] = {
                "label": a["label"],
                "surface": a["surface"],
                "current": bool(live_fp and a["fingerprint"] == live_fp),
                "fingerprint_short": a["fingerprint_short"],
                "project_id": a["project_id"],
                "email": a["email"],
                "saved_at": a["saved_at"],
                "stale": a["stale"],
                "legacy": bool(a.get("legacy", False)),
                "note": a["note"],
            }
            if surface == "cli":
                item["auth_method"] = a.get("auth_method")
            out_accounts.append(item)
        payload: dict[str, Any] = {
            "surface": surface,
            "vault": str(vault),
            "surface_vault": str(resolve_surface_vault(vault, surface)),
            "home_source": target.home_source,
            "token_path": str(target.live_token),
            "live_fingerprint_short": live_info.short_fp if live_info else None,
            "live_matched_labels": matched_labels,
            "live_error": live_error,
            "accounts": out_accounts,
        }
        if surface == "chat":
            payload["gemini_home"] = str(target.live_dir)
        else:
            payload["cli_dir"] = str(target.live_dir)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not accounts:
        print(f"账号库为空（surface={surface}，{resolve_surface_vault(vault, surface)}）。")
    else:
        print(f"Surface: {surface} | 凭据路径: {target.live_token}")
        headers = (
            ("当前", "LABEL", "指纹", "AUTH/PROJECT", "EMAIL", "保存时间", "状态/备注")
            if surface == "cli"
            else ("当前", "LABEL", "指纹", "PROJECT_ID", "EMAIL", "保存时间", "状态/备注")
        )
        rows: list[tuple[str, ...]] = []
        for a in accounts:
            cur = "*" if (live_fp and a["fingerprint"] == live_fp) else ""
            flags: list[str] = []
            if a.get("legacy"):
                flags.append("legacy")
            if a["stale"]:
                flags.append("stale")
            if a["note"]:
                flags.append(str(a["note"]))
            proj_col = (
                f"{a.get('auth_method') or '-'}/{a['project_id'] or '-'}"
                if surface == "cli"
                else (a["project_id"] or "-")
            )
            rows.append(
                (
                    cur,
                    a["label"],
                    a["fingerprint_short"],
                    proj_col,
                    a["email"] or "-",
                    a["saved_at"] or "-",
                    " | ".join(flags) if flags else "-",
                )
            )
        widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
        fmt = "  ".join(f"{{:<{w}}}" for w in widths)
        print(fmt.format(*headers))
        print(fmt.format(*("-" * w for w in widths)))
        for r in rows:
            print(fmt.format(*r))

    if live_error:
        print(f"\n警告：当前 {surface} live 凭据异常：{live_error}")
    elif live_info and not matched_labels:
        email_hint = f"，id_token_email={live_info.id_token_email}" if live_info.id_token_email else ""
        save_flag = " --surface cli" if surface == "cli" else ""
        print(
            f"\n当前 {surface} live 账号未在账号库中保存（fp={live_info.short_fp}，project_id={live_info.project_id or '-'}{email_hint}），"
            f"可运行 `agy-account save{save_flag} <label>` 保存。"
        )
    elif not target.live_token.exists():
        print(f"\n提示：当前 {surface} live 凭据文件不存在（{target.live_token}）。")
    return 0


def cmd_current(args: argparse.Namespace) -> int:
    surface = validate_surface(getattr(args, "surface", None))
    vault = resolve_vault_dir(args.vault)
    target = _resolve_surface_target(surface, args.home)

    if not target.live_token.is_file():
        raise AccountError(
            f"未找到当前 {surface} live 凭据文件：{target.live_token}（来源：{target.home_source}）"
        )

    raw = target.live_token.read_bytes()
    info = parse_surface_token_bytes(surface, raw, str(target.live_token))
    mtime = _format_mtime(target.live_token)
    accounts = load_saved_accounts(vault, surface)
    matches = match_labels_by_fp(accounts, info.fingerprint)

    if surface == "chat":
        live_acp_procs = filter_conflicting_processes(find_live_acp_processes(), target.live_dir)
        live_cli_procs: list[CliProcess] = []
        live_count = len(live_acp_procs)
    else:
        live_acp_procs = []
        live_cli_procs = filter_conflicting_cli_processes(find_live_cli_processes(), target.target_user_home)
        live_count = len(live_cli_procs)

    matched_email = next((m["email"] for m in matches if m.get("email")), None) or info.id_token_email

    if args.json:
        payload: dict[str, Any] = {
            "surface": surface,
            "home_source": target.home_source,
            "token_path": str(target.live_token),
            "fingerprint_short": info.short_fp,
            "project_id": info.project_id,
            "size_bytes": info.size_bytes,
            "mtime": mtime,
            "matched_labels": [m["label"] for m in matches],
            "email": matched_email,
            "status": "matched" if matches else "unknown",
            "live_process_count": live_count,
        }
        if surface == "chat":
            payload["gemini_home"] = str(target.live_dir)
            payload["live_acp_processes"] = [
                {"pid": p.pid, "session_id": p.session_id, "cwd": p.cwd} for p in live_acp_procs
            ]
        else:
            payload["cli_dir"] = str(target.live_dir)
            payload["auth_method"] = info.auth_method
            payload["id_token_email"] = info.id_token_email
            payload["live_cli_processes"] = [
                {
                    "pid": p.pid,
                    "subcommand": p.subcommand,
                    "session_id": p.session_id,
                    "cwd": p.cwd,
                    "home": p.home,
                }
                for p in live_cli_procs
            ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if matches:
        labels_str = ", ".join(m["label"] for m in matches)
        email_str = f" <{matched_email}>" if matched_email else ""
        stale_flag = any(m.get("stale") for m in matches)
        stale_str = " [stale]" if stale_flag else ""
        print(f"当前账号 [{surface}]：{labels_str}{email_str}{stale_str}")
    else:
        email_hint = f" <{info.id_token_email}>" if info.id_token_email else ""
        save_flag = " --surface cli" if surface == "cli" else ""
        print(f"当前账号 [{surface}]：unknown{email_hint}（未保存；可运行 `agy-account save{save_flag} <label>` 保存）")

    auth_part = f" | auth={info.auth_method}" if surface == "cli" else ""
    print(f"指纹：{info.short_fp} | project_id={info.project_id or '-'}{auth_part} | {info.size_bytes} B | mtime={mtime}")
    print(f"凭据路径：{target.live_token}（来源：{target.home_source}）")
    if surface == "chat":
        if live_acp_procs:
            print(f"运行中的 Antigravity Chat 会话：{live_count} 个")
            for p in live_acp_procs:
                print(f"  - {_format_proc_line(p)}")
        else:
            print("运行中的 Antigravity Chat 会话：0 个")
    else:
        if live_cli_procs:
            print(f"运行中的 agy CLI 会话：{live_count} 个")
            for p in live_cli_procs:
                print(f"  - {_format_cli_proc_line(p)}")
        else:
            print("运行中的 agy CLI 会话：0 个")
    return 0


def _allocate_backup_dir(vault: Path, surface: str, label_part: str) -> Path:
    backup_root = resolve_surface_vault(vault, surface) / BACKUP_DIR_NAME
    ensure_dir_secure(vault, 0o700)
    ensure_dir_secure(resolve_surface_vault(vault, surface), 0o700)
    ensure_dir_secure(backup_root, 0o700)
    stamp = utc_backup_stamp()
    base_name = f"{stamp}-{label_part}"
    candidate = backup_root / base_name
    idx = 1
    while candidate.exists():
        candidate = backup_root / f"{base_name}-{idx}"
        idx += 1
    ensure_dir_secure(candidate, 0o700)
    return candidate


def _backup_live_token(
    vault: Path,
    surface: str,
    live_token: Path,
    accounts: list[dict[str, Any]],
) -> tuple[Path, str]:
    raw = live_token.read_bytes()
    matched_label = "unknown"
    fp_short = "invalid"
    fp_full = ""
    project_id = ""
    try:
        info = parse_surface_token_bytes(surface, raw, str(live_token))
        fp_short = info.short_fp
        fp_full = info.fingerprint
        project_id = info.project_id
        matches = match_labels_by_fp(accounts, info.fingerprint)
        if matches:
            matched_label = matches[0]["label"]
    except AccountError:
        matched_label = "unknown"

    backup_dir = _allocate_backup_dir(vault, surface, matched_label)
    backup_token = backup_dir / surface_token_filename(surface)
    atomic_write_bytes(backup_token, raw, mode=0o600, dir_mode=0o700)
    atomic_write_json(
        backup_dir / "meta.json",
        {
            "surface": surface,
            "backed_up_at": utc_now_iso(),
            "source_path": str(live_token),
            "matched_label": matched_label,
            "fingerprint": fp_full or None,
            "fingerprint_short": fp_short,
            "project_id": project_id,
            "size_bytes": len(raw),
        },
        mode=0o600,
        dir_mode=0o700,
    )
    return backup_dir, matched_label


def cmd_use(args: argparse.Namespace) -> int:
    surface = validate_surface(getattr(args, "surface", None))
    label = validate_label(args.label)
    vault = resolve_vault_dir(args.vault)
    target = _resolve_surface_target(surface, args.home)

    accounts = load_saved_accounts(vault, surface)
    target_meta = next((a for a in accounts if a["label"] == label), None)
    if target_meta is None:
        expected_path = resolve_surface_vault(vault, surface) / label / target.token_filename
        raise AccountError(f"账号库 ({surface}) 中未找到 {label!r}：{expected_path}")

    target_token = Path(target_meta["token_path"])
    if not target_token.is_file():
        raise AccountError(f"账号库 ({surface}) 中未找到 {label!r}：{target_token}")

    target_raw = target_token.read_bytes()
    target_info = parse_surface_token_bytes(surface, target_raw, str(target_token))

    _ensure_surface_anchor(target, "切换")

    keyring_probe: SecretServiceProbe | None = None
    if surface == "chat":
        conflicts = filter_conflicting_processes(find_live_acp_processes(), target.live_dir)
        if conflicts:
            lines = [
                f"拒绝切换：检测到 {len(conflicts)} 个正在运行的 Antigravity Chat 会话（目标 {target.live_dir}）。",
                "请先在 Orca 里关掉这些 Antigravity Chat 会话：",
            ]
            for proc in conflicts:
                lines.append(f"  - {_format_proc_line(proc)}")
            lines.append("原因：运行中的会话在内存缓存凭据，刷新 token 时会把旧账号写回文件覆盖本次切换；本脚本不会自动杀掉会话。")
            raise AccountError("\n".join(lines), exit_code=2)
    else:
        keyring_probe = probe_secret_service()
        if keyring_probe.verdict != "absent" and not getattr(args, "assume_file_store", False):
            raise AccountError(keyring_gate_message(keyring_probe), exit_code=2)
        cli_conflicts = filter_conflicting_cli_processes(find_live_cli_processes(), target.target_user_home)
        if cli_conflicts:
            lines = [
                f"拒绝切换 CLI：检测到 {len(cli_conflicts)} 个正在运行的 agy CLI 会话（目标 {target.live_dir}）。",
                "请先退出这些 agy CLI 会话：",
            ]
            for proc in cli_conflicts:
                lines.append(f"  - {_format_cli_proc_line(proc)}")
            lines.append("原因：运行中的 agy CLI 会话在内存持有凭据，刷新 token 时可能写回旧账号覆盖本次切换；本脚本不会自动杀掉会话。")
            raise AccountError("\n".join(lines), exit_code=2)

    backup_root = resolve_surface_vault(vault, surface) / BACKUP_DIR_NAME
    if args.dry_run:
        if args.json:
            payload: dict[str, Any] = {
                "action": "use",
                "surface": surface,
                "dry_run": True,
                "target_label": label,
                "target_fingerprint_short": target_info.short_fp,
                "target_project_id": target_info.project_id,
                "home_source": target.home_source,
                "live_token": str(target.live_token),
                "live_exists": target.live_token.is_file(),
                "legacy_source": bool(target_meta.get("legacy", False)),
            }
            if surface == "chat":
                payload["gemini_home"] = str(target.live_dir)
            else:
                payload["cli_dir"] = str(target.live_dir)
                payload["auth_method"] = target_info.auth_method
                if keyring_probe is not None:
                    payload["secret_service"] = {
                        "verdict": keyring_probe.verdict,
                        "source": keyring_probe.source,
                        "detail": keyring_probe.detail,
                        "assume_file_store": bool(getattr(args, "assume_file_store", False)),
                    }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            auth_part = f", auth={target_info.auth_method}" if surface == "cli" else ""
            proc_name = (
                "live agy_acp_server.par 进程"
                if surface == "chat"
                else "live agy CLI 进程（且 Secret Service 探测为 absent）"
            )
            print(
                f"[dry-run] 目标账号 [{surface}]：{label} "
                f"(fp={target_info.short_fp}, project_id={target_info.project_id or '-'}{auth_part})"
            )
            print(f"[dry-run] 目标路径：{target.live_token}（来源：{target.home_source}）")
            print(f"[dry-run] 阻塞检查：无匹配的 {proc_name}")
            if keyring_probe is not None:
                print(
                    f"[dry-run] Secret Service 探测：{keyring_probe.verdict} "
                    f"（来源：{keyring_probe.source}；输出：{keyring_probe.detail}）"
                )
                if keyring_probe.verdict != "absent":
                    print("[dry-run] 风险：已按 --assume-file-store 放行；agy 可能仍用密钥库，本次切换不保证生效。")
            print(f"[dry-run] 若执行，将先备份现有文件到 {backup_root}/，再原子替换并回读校验。")
        return 0

    ensure_dir_secure(vault, 0o700)
    backup_dir: Path | None = None
    prev_raw: bytes | None = None
    if target.live_token.is_file():
        prev_raw = target.live_token.read_bytes()
        backup_dir, _ = _backup_live_token(vault, surface, target.live_token, accounts)

    try:
        atomic_write_bytes(target.live_token, target_raw, mode=0o600, dir_mode=0o700)
        verify_raw = target.live_token.read_bytes()
        verify_info = parse_surface_token_bytes(surface, verify_raw, str(target.live_token))
        if verify_info.fingerprint != target_info.fingerprint or verify_raw != target_raw:
            raise AccountError("写入后回读校验失败：指纹或内容不一致")
    except Exception as exc:
        if prev_raw is not None:
            try:
                atomic_write_bytes(target.live_token, prev_raw, mode=0o600, dir_mode=0o700)
            except Exception:
                pass
        backup_note = f"（已尝试回滚自备份 {backup_dir}）" if backup_dir else ""
        raise AccountError(f"切换失败：{exc}{backup_note}") from None

    email = target_meta.get("email")
    if args.json:
        payload = {
            "action": "use",
            "surface": surface,
            "label": label,
            "fingerprint_short": target_info.short_fp,
            "project_id": target_info.project_id,
            "email": email,
            "size_bytes": target_info.size_bytes,
            "live_token": str(target.live_token),
            "backup_dir": str(backup_dir) if backup_dir else None,
        }
        if surface == "cli":
            payload["auth_method"] = target_info.auth_method
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        email_part = f" <{email}>" if email else ""
        auth_part = f", auth={target_info.auth_method}" if surface == "cli" else ""
        print(
            f"已切换当前账号 [{surface}] 为：{label}{email_part} "
            f"(fp={target_info.short_fp}, project_id={target_info.project_id or '-'}{auth_part}, {target_info.size_bytes} B)"
        )
        if backup_dir:
            print(f"切换前备份：{backup_dir}")
        if surface == "chat":
            print(
                "提示：切换已完成，该表面此刻没有活进程（这是切换的前提）；"
                "直接在原会话继续即可 —— Orca 会新拉进程读新凭据，不需要重启，也不需要另开会话。"
            )
        else:
            print(
                "提示：直接在原终端重新跑 agy 即可（原会话继续亦可），不需要重启"
                "（不影响 agy remote-control serve 与 Chat 会话）。"
            )
            if keyring_probe is not None and keyring_probe.verdict != "absent":
                print(
                    f"警告：Secret Service 探测为 {keyring_probe.verdict}，已按 --assume-file-store 继续；"
                    "若 agy 实际使用密钥库，本次切换可能不生效。"
                )
    return 0


def _validate_http_url(url: str, name: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return
    raise AccountError(f"拒绝不安全的 {name} 地址（仅允许 https 或本机回环测试地址）")


def _extract_safe_oauth_error(body: bytes) -> tuple[str | None, str | None]:
    try:
        parsed = json.loads(body[:4096].decode("utf-8", "replace"))
        if isinstance(parsed, dict):
            err = parsed.get("error")
            desc = parsed.get("error_description")
            return (
                str(err) if isinstance(err, str) else None,
                str(desc) if isinstance(desc, str) else None,
            )
    except json.JSONDecodeError:
        pass
    return None, None


def _refresh_access_token(secrets: dict[str, str], timeout: float = 15.0) -> str:
    token_uri = secrets["token_uri"]
    _validate_http_url(token_uri, "token_uri")
    form = urllib.parse.urlencode(
        {
            "client_id": secrets["client_id"],
            "client_secret": secrets["client_secret"],
            "refresh_token": secrets["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        token_uri,
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_code, err_desc = _extract_safe_oauth_error(exc.read())
        if err_code == "invalid_grant":
            raise AccountError(
                f"refresh_token 已失效（HTTP {exc.code}: invalid_grant），请重新登录后重新 save --force。",
                exit_code=3,
            ) from None
        detail = f": {err_code}" if err_code else ""
        if err_desc:
            detail += f" ({err_desc})"
        raise AccountError(f"换取 access token 失败（HTTP {exc.code}{detail}）") from None
    except urllib.error.URLError as exc:
        raise AccountError(f"连接 token_uri 失败：{exc.reason}") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise AccountError("token_uri 返回了无法解析的响应") from None

    if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str) or not payload["access_token"]:
        raise AccountError("token_uri 响应中缺少有效的 access_token")
    return payload["access_token"]


def _fetch_userinfo_email(access_token: str, userinfo_url: str, timeout: float = 15.0) -> str:
    _validate_http_url(userinfo_url, "userinfo")
    req = urllib.request.Request(
        userinfo_url,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_code, _ = _extract_safe_oauth_error(exc.read())
        suffix = f" ({err_code})" if err_code else ""
        raise AccountError(f"查询 userinfo 失败（HTTP {exc.code}{suffix}）") from None
    except urllib.error.URLError as exc:
        raise AccountError(f"连接 userinfo 失败：{exc.reason}") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise AccountError("userinfo 返回了无法解析的响应") from None

    email = payload.get("email") if isinstance(payload, dict) else None
    if not isinstance(email, str) or not email.strip():
        raise AccountError("userinfo 响应中未包含 email 字段")
    return email.strip()


def _update_meta_after_verify(
    meta_path: Path,
    label: str,
    info: TokenInfo,
    email: str | None,
    stale: bool,
    update_email: bool,
    surface: str = "chat",
) -> tuple[dict[str, Any], str]:
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except (OSError, json.JSONDecodeError):
            meta = {}

    now = utc_now_iso()
    meta.setdefault("label", label)
    meta.setdefault("surface", surface)
    meta.setdefault("project_id", info.project_id)
    if surface == "cli" and info.auth_method:
        meta.setdefault("auth_method", info.auth_method)
    meta["fingerprint"] = info.fingerprint
    meta["fingerprint_short"] = info.short_fp
    meta["size_bytes"] = info.size_bytes
    meta["verified_at"] = now
    meta["stale"] = stale

    status = "stale" if stale else "verified"
    if email is not None:
        existing_email = meta.get("email")
        if not existing_email:
            meta["email"] = email
            meta["email_source"] = "userinfo"
            status = "recorded"
        elif existing_email.lower() == email.lower():
            status = "matched"
        elif update_email:
            meta["email"] = email
            meta["email_source"] = "userinfo"
            status = "updated"
        else:
            atomic_write_json(meta_path, meta, mode=0o600, dir_mode=0o700)
            raise AccountError(
                f"身份不匹配：账号 {label!r} ({surface}) 的 meta.email 为 {existing_email}，但 userinfo 返回 {email}（若确需覆盖请加 --update-email）。"
            )

    atomic_write_json(meta_path, meta, mode=0o600, dir_mode=0o700)
    return meta, status


def cmd_verify(args: argparse.Namespace) -> int:
    surface = validate_surface(getattr(args, "surface", None))
    vault = resolve_vault_dir(args.vault)
    userinfo_url = os.environ.get("AGY_USERINFO_URL", "").strip() or DEFAULT_USERINFO_URL
    accounts = load_saved_accounts(vault, surface)

    target_label: str | None = None
    if args.label is not None:
        target_label = validate_label(args.label)
        matched_records = [a for a in accounts if a["label"] == target_label]
        if not matched_records:
            expected_path = resolve_surface_vault(vault, surface) / target_label / surface_token_filename(surface)
            raise AccountError(f"账号库 ({surface}) 中未找到 {target_label!r}：{expected_path}")
        token_path = Path(matched_records[0]["token_path"])
        if not token_path.is_file():
            raise AccountError(f"账号库 ({surface}) 中未找到 {target_label!r}：{token_path}")
        raw = token_path.read_bytes()
        info = parse_surface_token_bytes(surface, raw, str(token_path))
    else:
        target = _resolve_surface_target(surface, args.home)
        token_path = target.live_token
        if not token_path.is_file():
            raise AccountError(
                f"未找到当前 {surface} live 凭据文件：{token_path}（来源：{target.home_source}）"
            )
        raw = token_path.read_bytes()
        info = parse_surface_token_bytes(surface, raw, str(token_path))
        matched_records = match_labels_by_fp(accounts, info.fingerprint)
        if len(matched_records) == 1:
            target_label = matched_records[0]["label"]

    secrets = _read_secret_oauth_fields(raw, str(token_path), surface=surface)
    try:
        access_token = _refresh_access_token(secrets)
    except AccountError as exc:
        if exc.exit_code == 3:
            for rec in matched_records:
                _update_meta_after_verify(
                    Path(rec["meta_path"]),
                    rec["label"],
                    info,
                    email=None,
                    stale=True,
                    update_email=False,
                    surface=surface,
                )
        raise

    email = _fetch_userinfo_email(access_token, userinfo_url)
    del access_token
    del secrets

    statuses: list[str] = []
    for rec in matched_records:
        _, st = _update_meta_after_verify(
            Path(rec["meta_path"]),
            rec["label"],
            info,
            email=email,
            stale=False,
            update_email=args.update_email,
            surface=surface,
        )
        statuses.append(f"{rec['label']}:{st}")

    label_display = (
        target_label
        if target_label
        else (", ".join(r["label"] for r in matched_records) if matched_records else "unknown (未保存)")
    )

    if args.json:
        payload: dict[str, Any] = {
            "action": "verify",
            "surface": surface,
            "label": label_display,
            "fingerprint_short": info.short_fp,
            "project_id": info.project_id,
            "email": email,
            "meta_status": statuses,
        }
        if surface == "cli":
            payload["auth_method"] = info.auth_method
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        meta_note = f" | meta={', '.join(statuses)}" if statuses else " | 未在账号库中保存"
        auth_part = f", auth={info.auth_method}" if surface == "cli" else ""
        print(
            f"校验通过 [{surface}]：{label_display} -> {email} "
            f"(fp={info.short_fp}, project_id={info.project_id or '-'}{auth_part}{meta_note})"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--surface",
        dest="surface",
        choices=SURFACES,
        default=None,
        help="目标表面：chat（默认，acp_token.json）或 cli（antigravity-oauth-token）",
    )
    common.add_argument("--home", help="覆盖目标根目录（chat 覆盖 GEMINI_HOME；cli 覆盖用户 HOME 或 cli 目录）")
    common.add_argument("--vault", help="覆盖账号库目录（默认 ~/.config/antigravity-accounts）")
    common.add_argument("--json", action="store_true", help="输出 JSON 格式")

    parser = argparse.ArgumentParser(
        prog="agy-account",
        description="Orca AntiGravity Chat/CLI 账号凭据保存、查看、验证与切换工具。",
    )
    parser.add_argument(
        "--surface",
        dest="surface_top",
        choices=SURFACES,
        default=None,
        help="目标表面：chat（默认，acp_token.json）或 cli（antigravity-oauth-token）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_save = sub.add_parser("save", parents=[common], help="将当前 live 凭据保存到账号库")
    p_save.add_argument("label", help="账号标签（例如 work-a）")
    p_save.add_argument("--note", help="可选备注")
    p_save.add_argument("--email", help="可选手工标注邮箱")
    p_save.add_argument("--force", action="store_true", help="允许覆盖已有 label 或重复指纹")
    p_save.set_defaults(func=cmd_save)

    p_list = sub.add_parser("list", parents=[common], help="列出账号库中已保存账号")
    p_list.set_defaults(func=cmd_list)

    p_cur = sub.add_parser("current", parents=[common], help="只读报告当前 live 凭据指纹与对应账号")
    p_cur.set_defaults(func=cmd_current)

    p_use = sub.add_parser("use", parents=[common], help="切换 live 凭据到指定已保存账号")
    p_use.add_argument("label", help="目标账号标签")
    p_use.add_argument("--dry-run", action="store_true", help="仅检查并打印计划，不写入文件")
    p_use.add_argument(
        "--assume-file-store",
        dest="assume_file_store",
        action="store_true",
        help=(
            "即使探测到 Secret Service 或无法排除（unknown）也按文件存储切换；仅在确认 agy 实际使用"
            " antigravity-oauth-token 文件时使用，风险是凭据可能其实在密钥库、本次切换不生效"
        ),
    )
    p_use.set_defaults(func=cmd_use)

    p_ver = sub.add_parser("verify", parents=[common], help="用 refresh_token 换取 userinfo 邮箱（零模型额度）")
    p_ver.add_argument("label", nargs="?", help="已保存账号标签（省略时校验当前 live 文件）")
    p_ver.add_argument("--update-email", action="store_true", help="userinfo 与 meta 不一致时更新 meta.email")
    p_ver.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        surf_top = getattr(args, "surface_top", None)
        surf_sub = getattr(args, "surface", None)
        if surf_top and surf_sub and surf_top != surf_sub:
            raise AccountError(f"前后 --surface 参数冲突：{surf_top!r} vs {surf_sub!r}")
        args.surface = validate_surface(surf_sub or surf_top or "chat")
        return args.func(args)
    except AccountError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
