#!/usr/bin/env python3
"""Manage and switch Orca Chat AntiGravity credentials (acp_token.json).

Orca's Chat surface stores a single Google OAuth `authorized_user` credential file at:
  $GEMINI_HOME/antigravity-acp/acp_token.json
(defaulting to ~/.config/orca/antigravity-chat/antigravity-acp/acp_token.json).

Because each live `agy_acp_server.par` process caches credentials in memory and
overwrites `acp_token.json` on refresh, `use` refuses to switch while any
matching `agy_acp_server.par` session is running.

Vault layout (default: ~/.config/antigravity-accounts, 0700):
  <label>/acp_token.json     0600, raw byte copy of the credential file
  <label>/meta.json          0600, label / saved_at / project_id / sha256 fingerprint / email / note
  _backup/<ts>-<label|unknown>/acp_token.json  0600, pre-switch backup

Usage:
  agy-account save <label> [--note TEXT] [--email EMAIL] [--force]
  agy-account list [--json]
  agy-account current [--json]
  agy-account use <label> [--dry-run] [--json]
  agy-account verify [label] [--update-email] [--json]
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

DEFAULT_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
BACKUP_DIR_NAME = "_backup"
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ACP_REL_TOKEN = Path("antigravity-acp") / "acp_token.json"
ACP_REL_SETTINGS = Path("antigravity-acp") / "settings.json"


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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_backup_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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


def parse_token_bytes(raw: bytes, source: str) -> TokenInfo:
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


def _read_secret_oauth_fields(raw: bytes, source: str) -> dict[str, str]:
    """Read OAuth refresh credentials solely in memory for `verify`. Never log or print."""
    parse_token_bytes(raw, source)
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


def load_saved_accounts(vault: Path) -> list[dict[str, Any]]:
    if not vault.exists():
        return []
    records: list[dict[str, Any]] = []
    for entry in sorted(vault.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        if entry.name == BACKUP_DIR_NAME or entry.name.startswith(".") or entry.name.startswith("_"):
            continue
        token_path = entry / "acp_token.json"
        meta_path = entry / "meta.json"
        if not token_path.is_file():
            continue
        raw = token_path.read_bytes()
        info = parse_token_bytes(raw, str(token_path))
        meta: dict[str, Any] = {}
        if meta_path.is_file():
            try:
                loaded = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    meta = loaded
            except (OSError, json.JSONDecodeError):
                meta = {}
        record = {
            "label": entry.name,
            "fingerprint": info.fingerprint,
            "fingerprint_short": info.short_fp,
            "project_id": meta.get("project_id", info.project_id),
            "saved_at": meta.get("saved_at"),
            "email": meta.get("email"),
            "email_source": meta.get("email_source"),
            "verified_at": meta.get("verified_at"),
            "stale": bool(meta.get("stale", False)),
            "note": meta.get("note"),
            "size_bytes": info.size_bytes,
            "token_path": str(token_path),
            "meta_path": str(meta_path),
        }
        records.append(record)
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


def _format_mtime(path: Path) -> str:
    st = path.stat()
    return datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_save(args: argparse.Namespace) -> int:
    label = validate_label(args.label)
    vault = resolve_vault_dir(args.vault)
    gemini_home, home_source = resolve_gemini_home(args.home)
    live_token = gemini_home / ACP_REL_TOKEN
    settings_file = gemini_home / ACP_REL_SETTINGS

    if not settings_file.is_file():
        raise AccountError(
            f"未找到 {settings_file}（GEMINI_HOME 来源：{home_source}），拒绝操作以防写入错误目录。"
        )
    if not live_token.is_file():
        raise AccountError(f"未找到当前 live 凭据文件：{live_token}")

    raw = live_token.read_bytes()
    info = parse_token_bytes(raw, str(live_token))

    accounts = load_saved_accounts(vault)
    target_dir = vault / label
    target_token = target_dir / "acp_token.json"
    target_meta = target_dir / "meta.json"

    if target_token.exists() and not args.force:
        raise AccountError(f"账号 {label!r} 已存在，若要覆盖请加 --force。")

    same_fp_others = [a["label"] for a in accounts if a["fingerprint"] == info.fingerprint and a["label"] != label]
    if same_fp_others and not args.force:
        raise AccountError(
            f"当前凭据指纹 ({info.short_fp}) 已保存在其他 label 下：{', '.join(same_fp_others)}；如仍要保存请加 --force。"
        )

    prev_meta: dict[str, Any] = {}
    if target_meta.is_file():
        try:
            loaded = json.loads(target_meta.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                prev_meta = loaded
        except (OSError, json.JSONDecodeError):
            prev_meta = {}

    same_fp_as_prev = prev_meta.get("fingerprint") == info.fingerprint
    email = args.email if args.email is not None else (prev_meta.get("email") if same_fp_as_prev else None)
    email_source = (
        "manual"
        if args.email is not None
        else (prev_meta.get("email_source") if same_fp_as_prev else None)
    )
    verified_at = prev_meta.get("verified_at") if same_fp_as_prev else None
    stale = False if not same_fp_as_prev else bool(prev_meta.get("stale", False))
    note = args.note if args.note is not None else (prev_meta.get("note") if same_fp_as_prev else None)

    saved_at = utc_now_iso()
    meta: dict[str, Any] = {
        "label": label,
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

    ensure_dir_secure(vault, 0o700)
    ensure_dir_secure(target_dir, 0o700)
    atomic_write_bytes(target_token, raw, mode=0o600, dir_mode=0o700)
    atomic_write_json(target_meta, meta, mode=0o600, dir_mode=0o700)

    if args.json:
        print(
            json.dumps(
                {
                    "action": "save",
                    "label": label,
                    "fingerprint_short": info.short_fp,
                    "project_id": info.project_id,
                    "size_bytes": info.size_bytes,
                    "saved_at": saved_at,
                    "email": email,
                    "note": note,
                    "vault_dir": str(target_dir),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        email_part = f" | email={email}" if email else ""
        note_part = f" | 备注={note}" if note else ""
        print(
            f"已保存账号 {label}：fp={info.short_fp} | project_id={info.project_id or '-'} | "
            f"{info.size_bytes} B | {saved_at}{email_part}{note_part}"
        )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    vault = resolve_vault_dir(args.vault)
    gemini_home, home_source = resolve_gemini_home(args.home)
    live_token = gemini_home / ACP_REL_TOKEN

    accounts = load_saved_accounts(vault)
    live_info: TokenInfo | None = None
    live_error: str | None = None
    if live_token.is_file():
        try:
            live_info = parse_token_bytes(live_token.read_bytes(), str(live_token))
        except AccountError as exc:
            live_error = str(exc)

    live_fp = live_info.fingerprint if live_info else None
    matched_labels = [a["label"] for a in accounts if live_fp and a["fingerprint"] == live_fp]

    if args.json:
        out_accounts = []
        for a in accounts:
            out_accounts.append(
                {
                    "label": a["label"],
                    "current": bool(live_fp and a["fingerprint"] == live_fp),
                    "fingerprint_short": a["fingerprint_short"],
                    "project_id": a["project_id"],
                    "email": a["email"],
                    "saved_at": a["saved_at"],
                    "stale": a["stale"],
                    "note": a["note"],
                }
            )
        print(
            json.dumps(
                {
                    "vault": str(vault),
                    "gemini_home": str(gemini_home),
                    "home_source": home_source,
                    "live_fingerprint_short": live_info.short_fp if live_info else None,
                    "live_matched_labels": matched_labels,
                    "live_error": live_error,
                    "accounts": out_accounts,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if not accounts:
        print(f"账号库为空（{vault}）。")
    else:
        headers = ("当前", "LABEL", "指纹", "PROJECT_ID", "EMAIL", "保存时间", "状态/备注")
        rows: list[tuple[str, ...]] = []
        for a in accounts:
            cur = "*" if (live_fp and a["fingerprint"] == live_fp) else ""
            flags: list[str] = []
            if a["stale"]:
                flags.append("stale")
            if a["note"]:
                flags.append(str(a["note"]))
            rows.append(
                (
                    cur,
                    a["label"],
                    a["fingerprint_short"],
                    a["project_id"] or "-",
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
        print(f"\n警告：当前 live 凭据异常：{live_error}")
    elif live_info and not matched_labels:
        print(
            f"\n当前 live 账号未在账号库中保存（fp={live_info.short_fp}，project_id={live_info.project_id or '-'}），"
            "可运行 `agy-account save <label>` 保存。"
        )
    elif not live_token.exists():
        print(f"\n提示：当前 live 凭据文件不存在（{live_token}）。")
    return 0


def cmd_current(args: argparse.Namespace) -> int:
    vault = resolve_vault_dir(args.vault)
    gemini_home, home_source = resolve_gemini_home(args.home)
    live_token = gemini_home / ACP_REL_TOKEN

    if not live_token.is_file():
        raise AccountError(f"未找到当前 live 凭据文件：{live_token}（GEMINI_HOME 来源：{home_source}）")

    raw = live_token.read_bytes()
    info = parse_token_bytes(raw, str(live_token))
    mtime = _format_mtime(live_token)
    accounts = load_saved_accounts(vault)
    matches = match_labels_by_fp(accounts, info.fingerprint)
    live_procs = filter_conflicting_processes(find_live_acp_processes(), gemini_home)

    if args.json:
        print(
            json.dumps(
                {
                    "gemini_home": str(gemini_home),
                    "home_source": home_source,
                    "token_path": str(live_token),
                    "fingerprint_short": info.short_fp,
                    "project_id": info.project_id,
                    "size_bytes": info.size_bytes,
                    "mtime": mtime,
                    "matched_labels": [m["label"] for m in matches],
                    "email": next((m["email"] for m in matches if m.get("email")), None),
                    "status": "matched" if matches else "unknown",
                    "live_acp_processes": [
                        {
                            "pid": p.pid,
                            "session_id": p.session_id,
                            "cwd": p.cwd,
                        }
                        for p in live_procs
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if matches:
        labels_str = ", ".join(m["label"] for m in matches)
        email = next((m["email"] for m in matches if m.get("email")), None)
        email_str = f" <{email}>" if email else ""
        stale_flag = any(m.get("stale") for m in matches)
        stale_str = " [stale]" if stale_flag else ""
        print(f"当前账号：{labels_str}{email_str}{stale_str}")
    else:
        print("当前账号：unknown（未保存；可运行 `agy-account save <label>` 保存）")

    print(f"指纹：{info.short_fp} | project_id={info.project_id or '-'} | {info.size_bytes} B | mtime={mtime}")
    print(f"凭据路径：{live_token}（来源：{home_source}）")
    if live_procs:
        print(f"运行中的 Antigravity Chat 会话：{len(live_procs)} 个")
        for p in live_procs:
            print(f"  - {_format_proc_line(p)}")
    else:
        print("运行中的 Antigravity Chat 会话：0 个")
    return 0


def _allocate_backup_dir(vault: Path, label_part: str) -> Path:
    backup_root = vault / BACKUP_DIR_NAME
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
    live_token: Path,
    accounts: list[dict[str, Any]],
) -> tuple[Path, str]:
    raw = live_token.read_bytes()
    matched_label = "unknown"
    fp_short = "invalid"
    fp_full = ""
    project_id = ""
    try:
        info = parse_token_bytes(raw, str(live_token))
        fp_short = info.short_fp
        fp_full = info.fingerprint
        project_id = info.project_id
        matches = match_labels_by_fp(accounts, info.fingerprint)
        if len(matches) == 1:
            matched_label = matches[0]["label"]
        elif len(matches) > 1:
            matched_label = matches[0]["label"]
    except AccountError:
        matched_label = "unknown"

    backup_dir = _allocate_backup_dir(vault, matched_label)
    backup_token = backup_dir / "acp_token.json"
    atomic_write_bytes(backup_token, raw, mode=0o600, dir_mode=0o700)
    atomic_write_json(
        backup_dir / "meta.json",
        {
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
    label = validate_label(args.label)
    vault = resolve_vault_dir(args.vault)
    gemini_home, home_source = resolve_gemini_home(args.home)
    live_token = gemini_home / ACP_REL_TOKEN
    settings_file = gemini_home / ACP_REL_SETTINGS

    target_dir = vault / label
    target_token = target_dir / "acp_token.json"
    if not target_token.is_file():
        raise AccountError(f"账号库中未找到 {label!r}：{target_token}")

    target_raw = target_token.read_bytes()
    target_info = parse_token_bytes(target_raw, str(target_token))

    if not settings_file.is_file():
        raise AccountError(
            f"未找到 {settings_file}（GEMINI_HOME 来源：{home_source}），拒绝切换以防写入错误目录。"
        )

    conflicts = filter_conflicting_processes(find_live_acp_processes(), gemini_home)
    if conflicts:
        lines = [
            f"拒绝切换：检测到 {len(conflicts)} 个正在运行的 Antigravity Chat 会话（目标 {gemini_home}）。",
            "请先在 Orca 里关掉这些 Antigravity Chat 会话：",
        ]
        for proc in conflicts:
            lines.append(f"  - {_format_proc_line(proc)}")
        lines.append("原因：运行中的会话在内存缓存凭据，刷新 token 时会把旧账号写回文件覆盖本次切换；本脚本不会自动杀掉会话。")
        raise AccountError("\n".join(lines), exit_code=2)

    accounts = load_saved_accounts(vault)
    target_meta = next((a for a in accounts if a["label"] == label), None)

    if args.dry_run:
        if args.json:
            print(
                json.dumps(
                    {
                        "action": "use",
                        "dry_run": True,
                        "target_label": label,
                        "target_fingerprint_short": target_info.short_fp,
                        "target_project_id": target_info.project_id,
                        "gemini_home": str(gemini_home),
                        "home_source": home_source,
                        "live_token": str(live_token),
                        "live_exists": live_token.is_file(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"[dry-run] 目标账号：{label} (fp={target_info.short_fp}, project_id={target_info.project_id or '-'})")
            print(f"[dry-run] 目标路径：{live_token}（GEMINI_HOME 来源：{home_source}）")
            print(f"[dry-run] 阻塞检查：无匹配的 live agy_acp_server.par 进程")
            print(f"[dry-run] 若执行，将先备份现有文件到 {vault / BACKUP_DIR_NAME}/，再原子替换并回读校验。")
        return 0

    ensure_dir_secure(vault, 0o700)
    backup_dir: Path | None = None
    prev_raw: bytes | None = None
    if live_token.is_file():
        prev_raw = live_token.read_bytes()
        backup_dir, _ = _backup_live_token(vault, live_token, accounts)

    try:
        atomic_write_bytes(live_token, target_raw, mode=0o600, dir_mode=0o700)
        verify_raw = live_token.read_bytes()
        verify_info = parse_token_bytes(verify_raw, str(live_token))
        if verify_info.fingerprint != target_info.fingerprint or verify_raw != target_raw:
            raise AccountError("写入后回读校验失败：指纹或内容不一致")
    except Exception as exc:
        if prev_raw is not None:
            try:
                atomic_write_bytes(live_token, prev_raw, mode=0o600, dir_mode=0o700)
            except Exception:
                pass
        backup_note = f"（已尝试回滚自备份 {backup_dir}）" if backup_dir else ""
        raise AccountError(f"切换失败：{exc}{backup_note}") from None

    email = target_meta.get("email") if target_meta else None
    if args.json:
        print(
            json.dumps(
                {
                    "action": "use",
                    "label": label,
                    "fingerprint_short": target_info.short_fp,
                    "project_id": target_info.project_id,
                    "email": email,
                    "size_bytes": target_info.size_bytes,
                    "live_token": str(live_token),
                    "backup_dir": str(backup_dir) if backup_dir else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        email_part = f" <{email}>" if email else ""
        print(
            f"已切换当前账号为：{label}{email_part} "
            f"(fp={target_info.short_fp}, project_id={target_info.project_id or '-'}, {target_info.size_bytes} B)"
        )
        if backup_dir:
            print(f"切换前备份：{backup_dir}")
        print("提示：请重开 Antigravity Chat 会话后生效（不需要重启 Orca）。")
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
                f"refresh_token 已失效（HTTP {exc.code}: invalid_grant），请在 Orca 中重新登录后重新 save --force。",
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
    meta.setdefault("project_id", info.project_id)
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
                f"身份不匹配：账号 {label!r} 的 meta.email 为 {existing_email}，但 userinfo 返回 {email}（若确需覆盖请加 --update-email）。"
            )

    atomic_write_json(meta_path, meta, mode=0o600, dir_mode=0o700)
    return meta, status


def cmd_verify(args: argparse.Namespace) -> int:
    vault = resolve_vault_dir(args.vault)
    userinfo_url = os.environ.get("AGY_USERINFO_URL", "").strip() or DEFAULT_USERINFO_URL
    accounts = load_saved_accounts(vault)

    target_label: str | None = None
    if args.label is not None:
        target_label = validate_label(args.label)
        token_path = vault / target_label / "acp_token.json"
        if not token_path.is_file():
            raise AccountError(f"账号库中未找到 {target_label!r}：{token_path}")
        raw = token_path.read_bytes()
        info = parse_token_bytes(raw, str(token_path))
        matched_records = [a for a in accounts if a["label"] == target_label]
    else:
        gemini_home, home_source = resolve_gemini_home(args.home)
        token_path = gemini_home / ACP_REL_TOKEN
        if not token_path.is_file():
            raise AccountError(f"未找到当前 live 凭据文件：{token_path}（GEMINI_HOME 来源：{home_source}）")
        raw = token_path.read_bytes()
        info = parse_token_bytes(raw, str(token_path))
        matched_records = match_labels_by_fp(accounts, info.fingerprint)
        if len(matched_records) == 1:
            target_label = matched_records[0]["label"]

    secrets = _read_secret_oauth_fields(raw, str(token_path))
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
        )
        statuses.append(f"{rec['label']}:{st}")

    label_display = (
        target_label
        if target_label
        else (", ".join(r["label"] for r in matched_records) if matched_records else "unknown (未保存)")
    )

    if args.json:
        print(
            json.dumps(
                {
                    "action": "verify",
                    "label": label_display,
                    "fingerprint_short": info.short_fp,
                    "project_id": info.project_id,
                    "email": email,
                    "meta_status": statuses,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        meta_note = f" | meta={', '.join(statuses)}" if statuses else " | 未在账号库中保存"
        print(
            f"校验通过：{label_display} -> {email} "
            f"(fp={info.short_fp}, project_id={info.project_id or '-'}{meta_note})"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", help="覆盖 GEMINI_HOME 路径")
    common.add_argument("--vault", help="覆盖账号库目录（默认 ~/.config/antigravity-accounts）")
    common.add_argument("--json", action="store_true", help="输出 JSON 格式")

    parser = argparse.ArgumentParser(
        prog="agy-account",
        description="Orca AntiGravity Chat 账号凭据（acp_token.json）保存、查看、验证与切换工具。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_save = sub.add_parser("save", parents=[common], help="将当前 live acp_token.json 保存到账号库")
    p_save.add_argument("label", help="账号标签（例如 work-a）")
    p_save.add_argument("--note", help="可选备注")
    p_save.add_argument("--email", help="可选手工标注邮箱")
    p_save.add_argument("--force", action="store_true", help="允许覆盖已有 label 或重复指纹")
    p_save.set_defaults(func=cmd_save)

    p_list = sub.add_parser("list", parents=[common], help="列出账号库中已保存账号")
    p_list.set_defaults(func=cmd_list)

    p_cur = sub.add_parser("current", parents=[common], help="只读报告当前 live acp_token.json 指纹与对应账号")
    p_cur.set_defaults(func=cmd_current)

    p_use = sub.add_parser("use", parents=[common], help="切换 live acp_token.json 到指定已保存账号")
    p_use.add_argument("label", help="目标账号标签")
    p_use.add_argument("--dry-run", action="store_true", help="仅检查并打印计划，不写入文件")
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
        return args.func(args)
    except AccountError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
