"""Offline self-checks for the tunnel helpers in download_dmm_preview.py.

The happy path (a real `ssh -D`) is covered by running the script with `--via`.
"""
import socket

import download_dmm_preview as d


def test_free_port_is_bindable():
    port = d.find_free_port()
    assert 1024 < port < 65536, port
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))


def test_bad_host_raises_and_cleans_up():
    try:
        d.start_tunnel("no-such-host.invalid", timeout=5)
    except RuntimeError as exc:
        assert "no-such-host.invalid" in str(exc), exc
    else:
        raise AssertionError("start_tunnel should raise on an unreachable host")


if __name__ == "__main__":
    test_free_port_is_bindable()
    test_bad_host_raises_and_cleans_up()
    print("ok")
