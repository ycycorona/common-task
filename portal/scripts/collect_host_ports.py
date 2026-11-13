#!/usr/bin/env python3
"""Collect listening TCP ports on the host and write a JSON snapshot for Portal."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
from typing import NotRequired, TypedDict

# Process names treated as ephemeral IDE/agent loopback listeners.
EPHEMERAL_PROCESSES = frozenset({"agy", "opencode"})
EPHEMERAL_PREFIXES = ("code-",)


class ServiceMeta(TypedDict):
    """Static metadata for a well-known listening port."""

    name: str
    description: str
    scheme: NotRequired[str]


class ServiceRow(TypedDict):
    """Aggregated listening port entry serialized into JSON."""

    port: int
    scope: str
    addresses: list[str]
    ip_families: list[str]
    process: str
    pid: int | None
    name: str
    description: str
    href: str | None
    ephemeral: bool


class SnapshotSummary(TypedDict):
    """Summary counters for the port snapshot."""

    exposed_count: int
    loopback_count: int
    ephemeral_count: int
    total_count: int


class SnapshotPayload(TypedDict):
    """Top-level JSON snapshot payload."""

    generated_at: str
    hostname: str
    tailscale_ip: str
    summary: SnapshotSummary
    services: list[ServiceRow]


# Known service metadata keyed by TCP port.
KNOWN_SERVICES: dict[int, ServiceMeta] = {
    22: {"name": "SSH", "description": "OpenSSH 远程登录", "scheme": "ssh"},
    53: {"name": "systemd-resolved", "description": "本机 DNS 解析"},
    111: {"name": "rpcbind", "description": "RPC 端口映射服务"},
    631: {"name": "CUPS", "description": "本地打印服务"},
    3425: {"name": "magpie 代理", "description": "magpie 内部/代理端口"},
    3430: {"name": "magpie Web 管理", "description": "free-arm Web 管理界面（首访需 ?k=）", "scheme": "http"},
    5900: {"name": "Orca 桌面 (x11vnc)", "description": "VNC 图形桌面环境", "scheme": "vnc"},
    6768: {"name": "Orca Web IDE", "description": "Orca 浏览器端工作台", "scheme": "http"},
    8080: {"name": "agentsview", "description": "Agent 舰队会话查看 · 需登录", "scheme": "http"},
    8766: {"name": "sub-pipe 案例台", "description": "free-arm case-playground", "scheme": "http"},
    8787: {"name": "望楼 / 提示词生成器", "description": "Caddy 反向代理（agent-runs 只读面板）", "scheme": "http"},
    8788: {"name": "望楼后端服务", "description": "agent-runs Python 本地后端（由 Caddy :8787 反代）"},
    8790: {"name": "开发 Portal (Homepage)", "description": "本门户导航页", "scheme": "http"},
    28010: {"name": "SSH (备用端口)", "description": "OpenSSH 备用端口", "scheme": "ssh"},
}

USERS_RE = re.compile(r'\("([^"]+)",pid=(\d+),fd=\d+\)')


@dataclass(frozen=True)
class ListeningEndpoint:
    """Single parsed listening socket line from ss."""

    address: str
    port: int
    ip_family: str
    scope: str
    process: str
    pid: int | None


def classify_scope(address: str) -> str:
    """Classify bind address exposure scope."""
    clean = address.split("%", 1)[0].strip("[]")
    if clean in {"0.0.0.0", "::", "*"}:
        return "all"
    if clean.startswith("127.") or clean == "::1":
        return "loopback"
    if clean.startswith("100.") or clean.startswith("fd7a:"):
        return "tailnet"
    return "other"


def split_host_port(local_addr: str) -> tuple[str, int]:
    """Split ss Local Address:Port into (host, port)."""
    host, sep, port_str = local_addr.rpartition(":")
    if not sep or not port_str.isdigit():
        raise ValueError(f"Invalid local address: {local_addr!r}")
    return host, int(port_str)


def detect_ip_family(address: str) -> str:
    """Detect IPv4 vs IPv6 vs dual-stack wildcard from ss address string."""
    if address.startswith("[") or "::" in address or address.startswith("fd7a:"):
        return "IPv6"
    if address == "*":
        return "IPv4/IPv6"
    return "IPv4"


def parse_ss_output(output: str) -> list[ListeningEndpoint]:
    """Parse output of `ss -H -tlnp` into ListeningEndpoint records."""
    endpoints: list[ListeningEndpoint] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        local_addr = parts[3]
        address, port = split_host_port(local_addr)
        process = ""
        pid: int | None = None
        if len(parts) >= 6:
            match = USERS_RE.search(" ".join(parts[5:]))
            if match:
                process = match.group(1)
                pid = int(match.group(2))
        endpoints.append(
            ListeningEndpoint(
                address=address,
                port=port,
                ip_family=detect_ip_family(address),
                scope=classify_scope(address),
                process=process,
                pid=pid,
            )
        )
    return endpoints


def is_ephemeral_process(process: str, scope: str) -> bool:
    """Return True for IDE/agent temporary loopback listeners."""
    if scope != "loopback" or not process:
        return False
    if process in EPHEMERAL_PROCESSES:
        return True
    return any(process.startswith(prefix) for prefix in EPHEMERAL_PREFIXES)


def build_href(port: int, address: str, scope: str, tailscale_ip: str) -> str | None:
    """Build clickable URL for non-loopback services with known scheme."""
    if scope == "loopback":
        return None
    meta = KNOWN_SERVICES.get(port)
    if not meta:
        return None
    scheme = meta.get("scheme")
    if not scheme:
        return None
    clean_addr = address.split("%", 1)[0].strip("[]")
    target_host = tailscale_ip if clean_addr in {"0.0.0.0", "::", "*"} else clean_addr
    if not target_host:
        return None
    if scheme == "http":
        return f"http://{target_host}:{port}/"
    if scheme == "ssh":
        return f"ssh://{target_host}:{port}"
    if scheme == "vnc":
        return f"vnc://{target_host}:{port}"
    return None


def aggregate_endpoints(
    endpoints: list[ListeningEndpoint],
    tailscale_ip: str,
) -> list[ServiceRow]:
    """Merge IPv4/IPv6 duplicates on same (port, scope) and enrich metadata."""
    merged: dict[tuple[int, str], ServiceRow] = {}
    for ep in endpoints:
        key = (ep.port, ep.scope)
        meta = KNOWN_SERVICES.get(ep.port)
        existing = merged.get(key)
        if existing is None:
            href = build_href(ep.port, ep.address, ep.scope, tailscale_ip)
            merged[key] = {
                "port": ep.port,
                "scope": ep.scope,
                "addresses": [ep.address],
                "ip_families": [ep.ip_family],
                "process": ep.process,
                "pid": ep.pid,
                "name": meta["name"] if meta else (ep.process or f"端口 {ep.port}"),
                "description": meta["description"] if meta else "",
                "href": href,
                "ephemeral": is_ephemeral_process(ep.process, ep.scope),
            }
        else:
            if ep.address not in existing["addresses"]:
                existing["addresses"].append(ep.address)
            if ep.ip_family not in existing["ip_families"]:
                existing["ip_families"].append(ep.ip_family)
            if not existing["process"] and ep.process:
                existing["process"] = ep.process
                existing["pid"] = ep.pid
                existing["ephemeral"] = is_ephemeral_process(ep.process, ep.scope)
            if not existing["href"]:
                existing["href"] = build_href(ep.port, ep.address, ep.scope, tailscale_ip)

    rows = list(merged.values())
    scope_order = {"tailnet": 0, "all": 1, "other": 2, "loopback": 3}
    rows.sort(key=lambda item: (scope_order.get(item["scope"], 9), item["port"]))
    return rows


def collect_snapshot(tailscale_ip: str) -> SnapshotPayload:
    """Run `ss -H -tlnp` and return structured host port snapshot."""
    result = subprocess.run(
        ["ss", "-H", "-tlnp"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ss -H -tlnp failed with code {result.returncode}: {result.stderr.strip()}"
        )
    endpoints = parse_ss_output(result.stdout)
    services = aggregate_endpoints(endpoints, tailscale_ip=tailscale_ip)
    exposed_count = sum(1 for item in services if item["scope"] != "loopback")
    loopback_count = sum(1 for item in services if item["scope"] == "loopback")
    ephemeral_count = sum(1 for item in services if item["ephemeral"])
    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "hostname": socket.gethostname(),
        "tailscale_ip": tailscale_ip,
        "summary": {
            "exposed_count": exposed_count,
            "loopback_count": loopback_count,
            "ephemeral_count": ephemeral_count,
            "total_count": len(services),
        },
        "services": services,
    }


def write_json_atomic(path: Path, payload: object) -> None:
    """Atomically write JSON with 0644 permissions so container UID 1000 can read it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            _ = handle.write("\n")
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Collect host listening TCP ports for Portal.")
    _ = parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "public" / "host-ports" / "data.json",
        help="Output JSON file path.",
    )
    _ = parser.add_argument(
        "--tailscale-ip",
        default="100.126.255.75",
        help="Tailscale IP used to construct clickable links for wildcard listeners.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    out_path = args.out if isinstance(args.out, Path) else Path(str(args.out))
    tailscale_ip = str(args.tailscale_ip)
    try:
        snapshot = collect_snapshot(tailscale_ip=tailscale_ip)
        write_json_atomic(out_path, snapshot)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
