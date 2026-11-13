#!/usr/bin/env python3
"""Self-check tests for collect_host_ports.py."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile

from collect_host_ports import (
    aggregate_endpoints,
    classify_scope,
    parse_ss_output,
    write_json_atomic,
)


SAMPLE_SS = """\
LISTEN 0 4096 100.126.255.75:8080 0.0.0.0:* users:(("agentsview",pid=2800088,fd=16))
LISTEN 0 4096 127.0.0.1:46095 0.0.0.0:* users:(("agy",pid=3045635,fd=11))
LISTEN 0 4096 127.0.0.1:8788 0.0.0.0:* users:(("python",pid=2800085,fd=3))
LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*
LISTEN 0 4096 [::]:22 [::]:*
LISTEN 0 4096 *:8787 *:*
LISTEN 0 4096 [fd7a:115c:a1e0::fd30:ff4c]%tailscale0:52299 [::]:*
"""


def test_classify_scope() -> None:
    assert classify_scope("127.0.0.1") == "loopback"
    assert classify_scope("127.0.0.53%lo") == "loopback"
    assert classify_scope("[::1]") == "loopback"
    assert classify_scope("100.126.255.75") == "tailnet"
    assert classify_scope("[fd7a:115c:a1e0::fd30:ff4c]%tailscale0") == "tailnet"
    assert classify_scope("0.0.0.0") == "all"
    assert classify_scope("[::]") == "all"
    assert classify_scope("*") == "all"
    assert classify_scope("192.168.1.10") == "other"


def test_parse_and_aggregate() -> None:
    endpoints = parse_ss_output(SAMPLE_SS)
    assert len(endpoints) == 7

    rows = aggregate_endpoints(endpoints, tailscale_ip="100.126.255.75")
    # Port 22 IPv4 + IPv6 should merge into one row under scope 'all'.
    ssh_rows = [r for r in rows if r["port"] == 22]
    assert len(ssh_rows) == 1
    assert ssh_rows[0]["ip_families"] == ["IPv4", "IPv6"]
    assert ssh_rows[0]["href"] == "ssh://100.126.255.75:22"

    # Tailnet agentsview
    ag_rows = [r for r in rows if r["port"] == 8080]
    assert len(ag_rows) == 1
    assert ag_rows[0]["scope"] == "tailnet"
    assert ag_rows[0]["process"] == "agentsview"
    assert ag_rows[0]["href"] == "http://100.126.255.75:8080/"

    # Wildcard 8787
    caddy_rows = [r for r in rows if r["port"] == 8787]
    assert len(caddy_rows) == 1
    assert caddy_rows[0]["href"] == "http://100.126.255.75:8787/"

    # Loopback agy should be marked ephemeral, python 8788 should not.
    agy_rows = [r for r in rows if r["port"] == 46095]
    assert len(agy_rows) == 1
    assert agy_rows[0]["ephemeral"] is True
    assert agy_rows[0]["href"] is None

    py_rows = [r for r in rows if r["port"] == 8788]
    assert len(py_rows) == 1
    assert py_rows[0]["ephemeral"] is False
    assert py_rows[0]["href"] is None


def test_write_json_atomic_permissions() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "nested" / "data.json"
        write_json_atomic(out, {"ok": True, "count": 1})
        assert out.exists()
        mode = stat.S_IMODE(os.stat(out).st_mode)
        assert mode == 0o644, f"expected 0o644, got {oct(mode)}"
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert loaded == {"ok": True, "count": 1}


def main() -> int:
    test_classify_scope()
    test_parse_and_aggregate()
    test_write_json_atomic_permissions()
    print("test_collect_host_ports: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
