#!/usr/bin/env python3
"""devps 测试：python3 devps/test_devps.py（无框架，assert 失败即非零退出）"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import devps as D  # noqa: E402

NOW = int(time.time())
FAILS = []


def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        FAILS.append(name)


def proc(cmd, ppid=1, started=None, zombie=False):
    return {"ppid": ppid, "started": started if started is not None else NOW - 86400, "zombie": zombie, "cmd": cmd}


# ---- classify ----
PROCS = {
    100: proc("/usr/bin/python3 -m subtitle_pipeline serve --token abc123"),
    101: proc("/usr/local/bin/devserver"),            # launchd 托管 → 排除
    102: proc("/bin/sh -c tail -f x", ppid=500),      # 父进程存活 → 排除
    103: proc("bash /Users/me/Code/webapi/run.sh"),   # cwd 读不到，argv 归属
    104: proc("some-daemon --flag"),                  # 未归属 → 排除
    105: proc("/usr/bin/python3 -m x", zombie=True),  # zombie → 排除
    110: proc("worker A", ppid=100),
    111: proc("worker B", ppid=110),
    200: proc("/Users/ycyber/Library/Application Support/Cursor/User/globalStorage/"
              "anysphere.cursor-agent-worker/agent-cli/.local/bin/cursor-agent --api-key " + "k" * 69 + " worker start"),
}
CWDS = {100: "/Users/me/Code/transcription", 101: "/Users/me/Code/transcription", 200: "/Users/me/Code/sub-pipe"}
LAUNCHD = {101}
ROWS = D.classify(PROCS, {200: "/x/cursor-agent"}, CWDS, {}, LAUNCHD,
                  ["/Users/me/Code"], D.DEFAULT_IGNORE)
by_pid = {r["pid"]: r for r in ROWS}

check("孤儿+cwd 归属 → CHECK", by_pid[100]["state"] == "CHECK" and by_pid[100]["project"] == {"name": "transcription", "evidence": "cwd"})
check("argv 归属", by_pid[103]["project"] == {"name": "webapi", "evidence": "cmdline"})
check("launchctl 命中被排除", 101 not in by_pid)
check("活父进程被排除", 102 not in by_pid)
check("未归属被排除", 104 not in by_pid)
check("zombie 被排除", 105 not in by_pid)
check("后代折叠 +N", by_pid[100]["children"] == 2 and 110 not in by_pid)
check("cursor worker → EXPECTED", by_pid[200]["state"] == "EXPECTED" and by_pid[200]["service"] == "cursor-agent")
check("排序 CHECK 在前", [r["pid"] for r in ROWS] == [100, 103, 200])

# ---- mask ----
KEY69 = "A1" + "x9" * 30 + "z"  # 69 字符字母数字混合
m = D.mask(f"/usr/bin/python3 serve --api-key {KEY69} --token abc --password=secret "
           "'Authorization: Bearer eyJhbGci.abc.def' postgres://adm:s3cret@db/x")
check("遮蔽空格分隔值", "abc" not in m and "****" in m)
check("遮蔽 --opt=值", "secret" not in m and "--password=****" in m)
check("遮蔽 69 字符 api-key", KEY69 not in m)
check("遮蔽 Bearer/JWT", "eyJhbGci" not in m)
check("遮蔽 DSN 口令", "s3cret" not in m and "adm:****@db" in m)
check("非敏感参数保留", "--password=****" in m and "serve" in m)
m2 = D.mask("curl -H 'Authorization: Basic dXNlcjpwYXNzMzM=' https://x")
check("遮蔽 Basic 凭据本体", "dXNlcjpwYXNzMzM" not in m2)
check("未知选项+长串兜底", "x" * 40 not in D.mask(f"tool --weird {'a1' * 20}"))

# ---- lstart_epoch ----
check("lstart 解析", D.lstart_epoch("Mon Sep 28 18:18:57 2026") == int(time.mktime((2026, 9, 28, 18, 18, 57, 0, 0, -1))))

# ---- service ----
check("service 解释器跳过 + -m", D.service("/usr/bin/python3", "/usr/bin/python3 -m subtitle_pipeline serve") == "subtitle_pipeline")
check("service comm 路径含空格", D.service("/Users/x/Application Support/Cursor/u/agent-cli/.local/bin/cursor-agent", "...") == "cursor-agent")
check("service node 脚本", D.service("/usr/local/bin/node", "node ./server.mjs") == "server")

# ---- stop（真实进程）----
sleeper = subprocess.Popen(["/bin/sleep", "60"])
try:
    ls = subprocess.run(["ps", "-o", "lstart=", "-p", str(sleeper.pid)], capture_output=True, text=True).stdout.strip()
    started = D.lstart_epoch(ls)
    r = D.stop_one(sleeper.pid, started - 1, False, 2)
    check("错误 @STARTED 拒绝且进程仍活", r["result"] == "refused" and r["reason"] == "PID_REUSED" and sleeper.poll() is None)
    check("PID 1 拒绝", D.stop_one(1, 0, False, 1)["reason"] == "PROTECTED")
    check("自身拒绝", D.stop_one(os.getpid(), 0, False, 1)["reason"] == "PROTECTED")
    r = D.stop_one(sleeper.pid, started, False, 2)
    check("正确 @STARTED 终止（父进程未 reap 时 zombie 也算已退出）",
          r["result"] in ("terminated", "exited") and sleeper.poll() is not None)
finally:
    sleeper.kill()
check("同 uid 校验（PID 1 拒绝已覆盖 uid 分支语义）", D.stop_one(1, 0, False, 1)["result"] == "refused")

print()
if FAILS:
    sys.exit(f"{len(FAILS)} 项失败: {FAILS}")
print("全部通过")
