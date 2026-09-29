#!/usr/bin/env python3
"""devps 极简版 — 找出"启动者已退出"（孤儿）的开发进程；按需停止。

macOS 专用，Python 3.11+，仅标准库。设计见 docs/design-v0.2.md §0。

用法:
  devps [--all] [--port N] [--json]
  devps stop PID@STARTED [PID@STARTED ...] [--force] [--yes] [--timeout S] [--json]
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import tomllib
from datetime import datetime

CONFIG = os.path.expanduser("~/.config/devps/config.toml")
DEFAULT_DIRS = ["~/Code"]
# ponytail: 内置 ignore 只留本机实测过的这一例（cursor-agent worker），随用随加；用户规则写 config.toml
DEFAULT_IGNORE = [r"anysphere\.cursor-agent-worker/.*worker start"]
LIMIT = 20

SENS_OPT = re.compile(r"^-{1,2}[\w-]*(key|token|secret|pass(word|wd)?|pwd|auth|credential)[\w-]*$", re.I)
SECRET_PREFIX = re.compile(r"^(sk-|ghp_|github_pat_|xox[bp]-|AKIA|eyJ[\w-]*\.)")
LONG_TOKEN = re.compile(r"^[A-Za-z0-9_+=.-]{32,}$")
URL_AUTH = re.compile(r"(\w+://[^/@\s]+:)[^@\s]+(@)")
MONTH = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
INTERP = {"python", "python3", "node", "deno", "bun", "ruby", "java", "perl", "uv", "sh", "bash", "zsh"}


def run(cmd, timeout=5, ok=(0,)):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode not in ok:
        raise RuntimeError(f"{cmd[0]} exit {p.returncode}: {p.stderr.strip()[:120]}")
    return p.stdout


def lstart_epoch(s):
    """'Thu Sep 17 14:53:50 2026' -> 本地 epoch 秒。月份手工解析，不受 locale 影响。"""
    mon, rest = s.split(" ", 2)[1], s.split(" ", 2)[2]
    d, t, y = rest.split()
    h, m, sec = map(int, t.split(":"))
    return int(time.mktime((int(y), MONTH[mon], int(d), h, m, sec, 0, 0, -1)))


def mask(cmd):
    """按 token 遮蔽敏感值。ponytail: ps 给的是拼接串而非真 argv，按空白切分，空格内嵌的值属 best-effort。"""
    out, hold = [], False
    for tok in cmd.split():
        low = tok.lower()
        if hold:
            out.append("****")
            hold = False
            continue
        if low in ("bearer", "basic"):
            out.append("****")
            hold = True  # 后随的凭据本体一并遮蔽
            continue
        if low.startswith("authorization:"):
            out.append("authorization: ****")
            continue
        left, eq, right = tok.partition("=")
        if eq and SENS_OPT.match(left):  # --opt=值 / NAME=值，只遮值
            out.append(left + "=****")
            continue
        if SENS_OPT.match(tok):
            out.append(tok)
            hold = True  # 下一个 token 是值
            continue
        tok = URL_AUTH.sub(r"\1****\2", tok)
        if SECRET_PREFIX.match(tok) or (
            LONG_TOKEN.match(tok) and any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok)
        ):
            out.append("****")
            continue
        out.append(tok)
    return " ".join(out)


def service(exe, cmd):
    """服务名: 可执行文件 basename；解释器则取 -m X / 首个非参数 token（ponytail: 拼接串启发，遇错名再改）。"""
    base = os.path.basename(exe or "")
    if base.split(".")[0] in INTERP:
        toks = cmd.split()
        for i, t in enumerate(toks):
            if t.startswith("-"):
                if t == "-m" and i + 1 < len(toks):
                    t = toks[i + 1]
                else:
                    continue
            if os.path.basename(t).split(".")[0] in INTERP:
                continue
            return re.sub(r"\.(py|js|mjs|cjs|ts|rb|sh)$", "", os.path.basename(t))
        return base or "?"
    return re.sub(r"\.(py|js|mjs|cjs|ts|rb|sh)$", "", base) if base else "?"


def parse_lsof(out, want_port=False):
    """lsof -F 输出: p<pid> ... n<路径>。want_port 时 n 取冒号后的端口号。"""
    result, cur = {}, None
    for line in out.splitlines():
        tag, val = line[:1], line[1:]
        if tag == "p":
            cur = int(val) if val.isdigit() else None
        elif tag == "n" and cur is not None:
            if want_port:
                try:
                    result.setdefault(cur, []).append(int(val.rsplit(":", 1)[1]))
                except (ValueError, IndexError):
                    pass
            else:
                result[cur] = val
    return result


def collect():
    """一次快照（两次 ps + 两次 lsof + launchctl）。ps 失败整体致命；lsof/launchctl 失败降级为告警。"""
    procs = {}
    for line in run(["ps", "-axww", "-o", "pid=,ppid=,lstart=,stat=,command="]).splitlines():
        f = line.split(None, 8)
        if len(f) < 9:
            continue
        try:
            procs[int(f[0])] = {
                "ppid": int(f[1]),
                "started": lstart_epoch(" ".join(f[2:7])),
                "zombie": f[7].startswith("Z"),
                "cmd": f[8],
            }
        except (KeyError, ValueError):
            continue
    # 第二次 ps 拿可执行路径（行尾整段，含空格不切坏）；service 名以它为准
    exes = {}
    for line in run(["ps", "-axww", "-o", "pid=,comm="]).splitlines():
        f = line.split(None, 1)
        if len(f) == 2 and f[0].isdigit():
            exes[int(f[0])] = f[1]
    uid = str(os.getuid())
    warns = []
    try:
        cwds = parse_lsof(run(["lsof", "-a", "-d", "cwd", "-u", uid, "-Fpn"], ok=(0, 1)))
    except Exception as e:
        cwds, _ = {}, warns.append(f"lsof 读 cwd 失败，项目归属可能漏报: {e}")
    try:
        ports = parse_lsof(run(["lsof", "-nP", "-a", "-iTCP", "-sTCP:LISTEN", "-u", uid, "-Fpn"], 3, ok=(0, 1)), True)
    except Exception:
        ports, _ = {}, warns.append("lsof 端口查询失败，PORT 列不可用")
    try:
        launchd = {int(f[0]) for line in run(["launchctl", "list"]).splitlines() if (f := line.split()) and f[0].isdigit()}
    except Exception:
        launchd, _ = set(), warns.append("launchctl 失败，托管状态未知，结果可能含 launchd 托管服务")
    return procs, exes, cwds, ports, launchd, warns


def child_map(procs):
    kids = {}
    for pid, p in procs.items():
        kids.setdefault(p["ppid"], []).append(pid)
    return kids


def project_of(pid, procs, cwds, dev_dirs):
    """依次: cwd 在 dev_dir 下 → argv 含 dev_dir 路径。返回 (项目名, 证据) 或 (None, None)。"""
    cwd = cwds.get(pid) or ""
    for d in dev_dirs:
        if cwd.startswith(d + "/"):
            seg = cwd[len(d) + 1:].split("/")[0]
            if seg:
                return seg, "cwd"
    cmd = procs[pid]["cmd"]
    for d in dev_dirs:
        i = cmd.find(d + "/")
        if i >= 0:  # ponytail: argv 归属按串查找，路径含空格会切错段；cwd 是主证据
            seg = cmd[i + len(d) + 1:].split("/")[0].split()[0]
            if seg:
                return seg, "cmdline"
    return None, None


def build_row(pid, procs, exes, cwds, ports, dev_dirs, ignores, kids):
    def n_desc(p):
        return sum(1 + n_desc(k) for k in kids.get(p, []))

    p = procs[pid]
    name, ev = project_of(pid, procs, cwds, dev_dirs)
    rule = next((pat for pat in ignores if re.search(pat, p["cmd"])), None)
    return {
        "pid": pid,
        "started": p["started"],
        "age_seconds": max(0, int(time.time()) - p["started"]),
        "state": "EXPECTED" if rule else "CHECK",
        "rule": rule,
        "service": service(exes.get(pid), p["cmd"]),
        "project": {"name": name, "evidence": ev},
        "ports": sorted(set(ports.get(pid, []))),
        "children": n_desc(pid),
        "cmd": mask(p["cmd"]),
    }


def classify(procs, exes, cwds, ports, launchd, dev_dirs, ignores):
    """孤儿(PPID=1) ∧ 不在 launchctl ∧ 非 zombie ∧ 归属项目。"""
    kids = child_map(procs)
    rows = []
    for pid, p in procs.items():
        if p["ppid"] != 1 or pid in launchd or p["zombie"]:
            continue
        if not project_of(pid, procs, cwds, dev_dirs)[0]:
            continue
        rows.append(build_row(pid, procs, exes, cwds, ports, dev_dirs, ignores, kids))
    rows.sort(key=lambda r: (r["state"] != "CHECK", -r["age_seconds"]))
    return rows


def age(sec):
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    if sec < 86400:
        return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"
    return f"{sec // 86400}d{(sec % 86400) // 3600:02d}h"


def reason(r):
    if r["state"] == "EXPECTED":
        return f"命中 ignore 规则: {r['rule']}"
    if not r["project"]["name"]:
        return "无项目归属（仅供参考）"
    return "父进程为 1，无 launchd 记录"


def render(rows):
    if not rows:
        return "未发现需要检查的开发进程"
    lines = [f"{'STATE':<8} {'PID':>6}  {'AGE':>7}  {'PROJECT':<16} {'SERVICE':<24} {'PORT':<12} REASON"]
    for r in rows:
        svc = r["service"] + (f" +{r['children']}" if r["children"] else "")
        port = ",".join(map(str, r["ports"])) or "-"
        lines.append(
            f"{r['state']:<8} {r['pid']:>6}  {age(r['age_seconds']):>7}  "
            f"{(r['project']['name'] or '-')[:16]:<16} {svc[:24]:<24} {port:<12} {reason(r)}"
        )
    if len(rows) > LIMIT:
        lines.append(f"… 还有 {len(rows) - LIMIT} 条未显示（--json 可看全部）")
    return "\n".join(lines)


def load_cfg():
    dev_dirs, ignores = [os.path.expanduser(d) for d in DEFAULT_DIRS], list(DEFAULT_IGNORE)
    try:
        with open(CONFIG, "rb") as f:
            cfg = tomllib.load(f)
        dev_dirs += [os.path.expanduser(d) for d in cfg.get("dev_dirs", [])]
        ignores += cfg.get("ignore", [])
    except FileNotFoundError:
        pass
    except (tomllib.TOMLDecodeError, OSError) as e:
        sys.exit(f"devps: 配置解析失败 {CONFIG}: {e}（ignore 失效不能静默）")
    return dev_dirs, ignores


def show_port(n, procs, exes, cwds, ports, dev_dirs, ignores, as_json, warns):
    kids = child_map(procs)
    rows = [build_row(pid, procs, exes, cwds, ports, dev_dirs, ignores, kids)
            for pid, pl in ports.items() if n in pl]
    rows.sort(key=lambda r: -r["age_seconds"])
    if as_json:
        print(json.dumps({"schema_version": 1, "port": n, "owners": rows, "warnings": warns},
                         ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print(f"本用户没有进程监听端口 {n}（其他用户/系统进程需 sudo lsof -iTCP:{n} 确认）")
    else:
        print(render(rows))
    for w in warns:
        print(f"警告: {w}", file=sys.stderr)
    return 0


def cmd_list(args):
    dev_dirs, ignores = load_cfg()
    procs, exes, cwds, ports, launchd, warns = collect()
    if args.port is not None:
        return show_port(args.port, procs, exes, cwds, ports, dev_dirs, ignores, args.json, warns)
    rows = classify(procs, exes, cwds, ports, launchd, dev_dirs, ignores)
    shown = rows if args.all else [r for r in rows if r["state"] == "CHECK"]
    if args.json:
        print(json.dumps({
            "schema_version": 1,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "summary": {"scanned": len(procs), "shown": min(len(shown), LIMIT), "more": max(0, len(shown) - LIMIT)},
            "rows": shown[:LIMIT],
            "warnings": warns,
        }, ensure_ascii=False, indent=2))
    else:
        print(render(shown))
        for w in warns:
            print(f"警告: {w}", file=sys.stderr)
    return 0


def ancestor_chain():
    """devps 自身到 PID 1 的祖先链（不含 1）。候选虽必为孤儿，但 stop 接受任意 PID，须防误杀调用链。"""
    chain, cur = set(), os.getpid()
    for _ in range(50):
        out = subprocess.run(["ps", "-o", "ppid=", "-p", str(cur)], capture_output=True, text=True).stdout.strip()
        if not out.isdigit() or int(out) <= 1:
            break
        cur = int(out)
        chain.add(cur)
    return chain


def stop_one(pid, started, force, timeout):
    """身份复核（同 uid + lstart 相符）后发信号并等待。返回 {pid, result, reason}。"""
    if pid == 1 or pid == os.getpid():
        return {"pid": pid, "result": "refused", "reason": "PROTECTED"}
    out = subprocess.run(["ps", "-p", str(pid), "-o", "uid=,lstart=,stat="], capture_output=True, text=True).stdout
    f = out.split(None, 6)
    if len(f) < 7:
        return {"pid": pid, "result": "exited", "reason": "NOT_FOUND"}
    if f[0] != str(os.getuid()):
        return {"pid": pid, "result": "refused", "reason": "NOT_FOUND"}  # 非 same-uid 视同不可见
    if f[6].startswith("Z"):
        return {"pid": pid, "result": "exited", "reason": "OK"}
    try:
        if lstart_epoch(" ".join(f[1:6])) != started:
            return {"pid": pid, "result": "refused", "reason": "PID_REUSED"}
    except (KeyError, ValueError):
        return {"pid": pid, "result": "refused", "reason": "PID_REUSED"}
    # ponytail: 复核与发信号间存在毫秒级 PID 复用窗口（macOS 无 pidfd），与 psutil 同款模式，接受
    os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return {"pid": pid, "result": "terminated", "reason": "OK"}
        except PermissionError:
            return {"pid": pid, "result": "refused", "reason": "EPERM"}
        time.sleep(0.2)
    stat = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
    return {"pid": pid, "result": "exited" if stat.startswith("Z") else "still_running",
            "reason": "OK" if stat.startswith("Z") else "TIMEOUT"}


def cmd_stop(args):
    targets = []
    for spec in args.targets:
        pid, _, started = spec.partition("@")
        if not (pid.isdigit() and started.isdigit()):
            sys.exit(f"devps: stop 必须 PID@STARTED（STARTED 取 list --json 的 started 字段）: {spec}")
        targets.append((int(pid), int(started)))
    if not args.yes and not sys.stdin.isatty():
        results = [{"pid": p, "result": "refused", "reason": "NEEDS_CONFIRM"} for p, _ in targets]
    else:
        if not args.yes and sys.stdin.isatty():
            if input(f"停止 {len(targets)} 个进程（--force={'是' if args.force else '否'}）? [y/N] ").strip().lower() not in ("y", "yes"):
                print("已取消，未做任何事")
                return 1
        chain = ancestor_chain()
        results = []
        for pid, started in targets:
            if pid in chain:
                results.append({"pid": pid, "result": "refused", "reason": "PROTECTED"})
            else:
                results.append(stop_one(pid, started, args.force, args.timeout))
    if args.json:
        print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(f"PID {r['pid']}: {r['result']}" + (f"（{r['reason']}）" if r["reason"] != "OK" else ""))
    return 0 if all(r["result"] in ("terminated", "exited") for r in results) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="devps", description="列出'启动者已退出'的开发进程（孤儿）")
    ap.add_argument("--all", action="store_true", help="同时显示被 ignore 规则命中的进程（EXPECTED）")
    ap.add_argument("--port", type=int, metavar="N", help="查端口 N 的监听者（不做归属/状态过滤）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    sub = ap.add_subparsers(dest="cmd")
    sp = sub.add_parser("stop", help="停止进程，必须 PID@STARTED 防止 PID 复用误杀")
    sp.add_argument("targets", nargs="+", metavar="PID@STARTED")
    sp.add_argument("--force", action="store_true", help="SIGKILL 代替 SIGTERM")
    sp.add_argument("--yes", action="store_true", help="跳过确认（非交互必带）")
    sp.add_argument("--timeout", type=int, default=5, help="等待退出的秒数")
    sp.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    return cmd_stop(args) if args.cmd == "stop" else cmd_list(args)


if __name__ == "__main__":
    sys.exit(main())
