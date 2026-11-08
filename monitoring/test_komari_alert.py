#!/usr/bin/env python3
"""komari_alert.py 的自检：状态机、阈值与去抖，不联网。"""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("komari_alert", Path(__file__).with_name("komari_alert.py"))
ka = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ka)

CFG = {"offline_after_minutes": 5, "disk_warn_percent": 85, "disk_crit_percent": 95}
NOW = 1_800_000_000.0
GB = 1024**3


def node(uuid="u1", name="n1", age_seconds=0.0, used=10 * GB, total=100 * GB):
    return {
        "uuid": uuid,
        "name": name,
        "sample_time": None if age_seconds is None else NOW - age_seconds,
        "disk_used": used,
        "disk_total": total,
    }


def states(events):
    return {e["key"]: e["state"] for e in events}


# 1. 采样新鲜 → 在线；磁盘 10% → 正常
events = ka.evaluate([node(age_seconds=5, used=10 * GB)], NOW, CFG)
assert states(events) == {"u1:offline": ka.OK, "u1:disk": ka.OK}, events

# 2. 超过 5 分钟没有样本 → 离线告警；磁盘 90% → 告警且未到 95 严重线
events = ka.evaluate([node(age_seconds=6 * 60, used=90 * GB)], NOW, CFG)
assert states(events) == {"u1:offline": ka.ALERT, "u1:disk": ka.ALERT}, events
assert [e for e in events if e["metric"] == "disk"][0]["critical"] is False
assert "6 分钟前" in [e for e in events if e["metric"] == "offline"][0]["detail"]

# 3. 磁盘 96% → 严重标记
disk = [e for e in ka.evaluate([node(used=96 * GB)], NOW, CFG) if e["metric"] == "disk"][0]
assert disk["critical"] is True

# 4. 从未上报的节点不判定离线，也不判定磁盘（total=0）
assert ka.evaluate([node(age_seconds=None, used=0, total=0)], NOW, CFG) == []

# 5. 去抖：正常→告警发一次，同状态再算不重复，恢复发一次并清状态
state = {}
sends, state = ka.plan(ka.evaluate([node(age_seconds=600)], NOW, CFG), state, NOW, 0)
assert [s["key"] for s in sends] == ["u1:offline"], sends
assert state["u1:offline"]["state"] == ka.ALERT

sends, state = ka.plan(ka.evaluate([node(age_seconds=900)], NOW + 60, CFG), state, NOW + 60, 0)
assert sends == [], sends  # 仍在离线，不重复提醒

sends, state = ka.plan(ka.evaluate([node(age_seconds=1)], NOW + 120, CFG), state, NOW + 120, 0)
assert len(sends) == 1 and sends[0]["recovered"] is True, sends
assert "u1:offline" not in state, state

# 6. remind_hours > 0 时按间隔重复提醒
state = {"u1:offline": {"state": ka.ALERT, "since": NOW, "last_sent": NOW}}
sends, state = ka.plan(ka.evaluate([node(age_seconds=600)], NOW + 3600 * 5, CFG), state, NOW + 3600 * 5, 6)
assert sends == [], "5 小时未到 6 小时提醒间隔"
sends, state = ka.plan(ka.evaluate([node(age_seconds=600)], NOW + 3600 * 7, CFG), state, NOW + 3600 * 7, 6)
assert len(sends) == 1, sends

# 7. 时间解析吃得下面板的纳秒时间戳
assert ka.parse_time("2026-11-08T17:56:23.123456789Z") == 1794160583.0

# 8. stdout 频道在 dry-run 下不写状态（由 main 保证），notify 打印不抛异常
ka.notify({"notify": {"kind": "stdout"}}, "subject", "body", dry_run=True)

print("komari_alert self-check ok")
