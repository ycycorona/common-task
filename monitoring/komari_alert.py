#!/usr/bin/env python3
"""Komari 离线与磁盘告警：读面板数据，只在状态发生变化时发一条通知。

Komari 1.6.0 自带的通知只有「离线 / 流量定时报告 / 到期」三类，没有磁盘阈值，
所以这里用一次轮询同时覆盖两件事，去抖状态由本脚本保存。

    python3 komari_alert.py --config /opt/komari-alert/config.json --dry-run

配置（0600，与脚本同目录，不入 Git）：

    {
      "komari_url": "http://127.0.0.1:25774",
      "credentials_file": "/opt/rigledger-monitoring/server/credentials.json",
      "state_file": "/opt/komari-alert/state.json",
      "offline_after_minutes": 5,
      "disk_warn_percent": 85,
      "disk_crit_percent": 95,
      "remind_hours": 0,
      "notify": {"kind": "smtp", "host": "", "port": 587, "starttls": true,
                 "user": "", "password": "", "from": "", "to": []}
    }

`notify.kind` 可选 `smtp`、`webhook`（POST JSON {subject, body} 到 `url`，可带 `headers`）
或 `stdout`（只打印，用于试运行）。SMTP 频道支持 `env_file`，从现成的 `.env` 读
`PUSH_SMTP_USER` / `PUSH_SMTP_PASSWORD` / `PUSH_TO`（可选 `PUSH_SMTP_HOST` / `PUSH_SMTP_PORT`），
这样不必把同一份口令再存一遍。`remind_hours` 为 0 表示同一故障不重复提醒。

退出码：0 正常（含「有告警并已发出」）；2 面板登录或取数失败；3 通知发送失败（状态不推进，下轮重试）；4 配置不完整。
"""

from __future__ import annotations

import argparse
import json
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from http.cookiejar import CookieJar
from pathlib import Path

ALERT = "alert"
OK = "ok"


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text())
    missing = [k for k in ("komari_url", "credentials_file", "state_file") if not cfg.get(k)]
    if missing:
        raise SystemExit(f"配置缺少字段: {', '.join(missing)}")
    cfg.setdefault("offline_after_minutes", 5)
    cfg.setdefault("disk_warn_percent", 85)
    cfg.setdefault("disk_crit_percent", 95)
    cfg.setdefault("remind_hours", 0)
    cfg.setdefault("notify", {"kind": "stdout"})
    return cfg


def login(base: str, credentials_file: str) -> urllib.request.OpenerDirector:
    creds = json.loads(Path(credentials_file).read_text())
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    body = json.dumps(creds).encode()
    req = urllib.request.Request(
        f"{base}/api/login", data=body, headers={"Content-Type": "application/json", "Origin": base}
    )
    with opener.open(req, timeout=20) as resp:
        if resp.status != 200:
            raise SystemExit(2)
    return opener


def fetch_nodes(base: str, opener: urllib.request.OpenerDirector) -> list[dict]:
    def get(path: str):
        req = urllib.request.Request(f"{base}{path}", headers={"Origin": base})
        with opener.open(req, timeout=20) as resp:
            return json.load(resp)

    nodes = get("/api/nodes")["data"]
    out = []
    for node in nodes:
        samples = get(f"/api/recent/{node['uuid']}")["data"]
        last = samples[-1] if samples else None
        out.append(
            {
                "uuid": node["uuid"],
                "name": node["name"],
                "sample_time": parse_time(last["updated_at"]) if last else None,
                "disk_used": (last or {}).get("disk", {}).get("used") or 0,
                "disk_total": (last or {}).get("disk", {}).get("total") or 0,
            }
        )
    return out


def parse_time(value: str) -> float:
    # 面板返回 RFC3339 纳秒，time.strptime 吃不下小数秒，这里截到秒。
    from datetime import datetime, timezone

    head = value.split(".")[0].rstrip("Z")
    return datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def evaluate(nodes: list[dict], now: float, cfg: dict) -> list[dict]:
    """把当前采样换算成每个 (节点, 指标) 的目标状态；不涉及历史。"""
    events = []
    offline_after = cfg["offline_after_minutes"] * 60
    for node in nodes:
        if node["sample_time"] is None:
            continue  # 从未上报过的节点不猜它离线
        age = now - node["sample_time"]
        offline = age > offline_after
        events.append(
            {
                "key": f"{node['uuid']}:offline",
                "name": node["name"],
                "metric": "offline",
                "state": ALERT if offline else OK,
                "detail": f"最后上报 {int(age // 60)} 分钟前（阈值 {cfg['offline_after_minutes']} 分钟）",
            }
        )
        total, used = node["disk_total"], node["disk_used"]
        if total <= 0:
            continue
        pct = used / total * 100
        level = ALERT if pct >= cfg["disk_warn_percent"] else OK
        events.append(
            {
                "key": f"{node['uuid']}:disk",
                "name": node["name"],
                "metric": "disk",
                "state": level,
                "detail": f"磁盘 {pct:.1f}%（{used} / {total} 字节，阈值 {cfg['disk_warn_percent']}%）",
                "critical": pct >= cfg["disk_crit_percent"],
            }
        )
    return events


def plan(events: list[dict], state: dict, now: float, remind_hours: float) -> tuple[list[dict], dict]:
    """对比历史状态，返回需要发送的通知与新的状态字典。"""
    sends = []
    new_state = dict(state)
    for event in events:
        prev = state.get(event["key"], {})
        if event["state"] == ALERT:
            if prev.get("state") != ALERT:
                sends.append(event)
                new_state[event["key"]] = {"state": ALERT, "since": now, "last_sent": now}
            elif remind_hours and now - prev.get("last_sent", 0) >= remind_hours * 3600:
                sends.append(event)
                new_state[event["key"]] = {**prev, "last_sent": now}
        else:
            if prev.get("state") == ALERT:
                sends.append({**event, "recovered": True})
                new_state.pop(event["key"], None)
            elif event["key"] in new_state:
                new_state.pop(event["key"])
    return sends, new_state


def smtp_settings(channel: dict) -> dict:
    """合并频道配置与可选的 env_file（复用已有投递凭据，避免重复存口令）。"""
    settings = {
        "host": channel.get("host", "smtp.gmail.com"),
        "port": int(channel.get("port", 587)),
        "starttls": channel.get("starttls", True),
        "user": channel.get("user", ""),
        "password": channel.get("password", ""),
        "to": list(channel.get("to") or []),
    }
    env_file = channel.get("env_file")
    if env_file:
        values = {}
        for line in Path(env_file).read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.lstrip().startswith("#"):
                values[key.strip()] = value.strip()
        settings["user"] = values.get("PUSH_SMTP_USER", settings["user"])
        settings["password"] = values.get("PUSH_SMTP_PASSWORD", settings["password"]).replace(" ", "")
        settings["host"] = values.get("PUSH_SMTP_HOST", settings["host"])
        settings["port"] = int(values.get("PUSH_SMTP_PORT", settings["port"]))
        recipients = values.get("PUSH_TO")
        if recipients:
            settings["to"] = [a.strip() for a in recipients.split(",") if a.strip()]
    settings["from"] = channel.get("from") or settings["user"]
    return settings


def build_message(subject: str, body: str, mail: dict) -> EmailMessage:
    """Date 与 Message-ID 必须自己加：缺了它们 Gmail 该账号发出的信更容易被判进垃圾箱。"""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail["from"]
    msg["To"] = ", ".join(mail["to"])
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=mail["from"].split("@")[-1])
    msg.set_content(body)
    return msg


def notify(cfg: dict, subject: str, body: str, dry_run: bool) -> None:
    channel = cfg["notify"]
    kind = channel.get("kind", "stdout")
    if dry_run or kind == "stdout":
        print(f"--- {subject}\n{body}")
        return
    if kind == "smtp":
        mail = smtp_settings(channel)
        if not (mail["user"] and mail["password"] and mail["to"]):
            raise SystemExit("SMTP 未配置完整（user/password/to）")
        msg = build_message(subject, body, mail)
        ctx = ssl.create_default_context()
        if mail["starttls"]:
            with smtplib.SMTP(mail["host"], mail["port"], timeout=30) as smtp:
                smtp.starttls(context=ctx)
                smtp.login(mail["user"], mail["password"])
                smtp.send_message(msg)
        else:
            with smtplib.SMTP_SSL(mail["host"], mail["port"], timeout=30, context=ctx) as smtp:
                smtp.login(mail["user"], mail["password"])
                smtp.send_message(msg)
        return
    if kind == "webhook":
        payload = json.dumps({"subject": subject, "body": body}).encode()
        headers = {"Content-Type": "application/json", **channel.get("headers", {})}
        req = urllib.request.Request(channel["url"], data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"webhook {resp.status}")
        return
    raise SystemExit(4)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Komari 离线与磁盘告警")
    parser.add_argument("--config", default="/opt/komari-alert/config.json")
    parser.add_argument("--dry-run", action="store_true", help="不发通知，只打印")
    parser.add_argument("--test-notify", action="store_true", help="发一条自检通知")
    args = parser.parse_args(argv)

    cfg = load_config(Path(args.config))
    if args.test_notify:
        notify(cfg, "[Komari] 自检通知", "如果收到这条，说明告警频道可用。", args.dry_run)
        return 0

    now = time.time()
    opener = login(cfg["komari_url"].rstrip("/"), cfg["credentials_file"])
    nodes = fetch_nodes(cfg["komari_url"].rstrip("/"), opener)
    state_path = Path(cfg["state_file"])
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    sends, new_state = plan(evaluate(nodes, now, cfg), state, now, cfg["remind_hours"])
    if not sends:
        print(f"{len(nodes)} 个节点，无状态变化")
        return 0

    lines = []
    for item in sends:
        if item.get("recovered"):
            lines.append(f"[恢复] {item['name']} · {item['metric']} · {item['detail']}")
        else:
            lines.append(f"[告警] {item['name']} · {item['metric']} · {item['detail']}")
    body = "\n".join(lines)
    subject = f"[Komari] {len(sends)} 项状态变化"
    try:
        notify(cfg, subject, body, args.dry_run)
    except Exception as error:  # 通知失败：状态不推进，下一轮重试
        print(f"通知失败: {error}", file=sys.stderr)
        return 3
    if args.dry_run:
        return 0
    state_path.write_text(json.dumps(new_state))
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
