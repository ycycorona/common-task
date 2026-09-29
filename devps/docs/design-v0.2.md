# devps 设计文档 v0.2（精简稿）

> 状态：评审稿，取代 [v0.1](design-v0.1.md)（保留作对照）。
> 平台：macOS；Python 3.11+；**零第三方依赖**。~~唯一依赖 psutil~~—— 2026-09-29 开源调研 + 实测推翻了这一判断：`ps -axww` 两调用（共 0.09s / 682 进程）即可拿启动时间、完整参数和含空格路径，见 §4 修正。
> 需求不变。砍掉的是实现层的过度设计；判定口径有一处实质修正（§3），依据见 §2。

## 0. 最小实现（已落地：`devps/devps.py`）

2026-09-29 调研了 GitHub / npm / crates 上 11 个同类工具（proc、reclaim、agent-janitor 等）：需求的 70–80% 已有人做，没人做的恰好是三样——**EXPECTED/ignore 语义**、**排除 launchd + 项目归属的孤儿口径**、**按需运行无常驻**。最小实现只做这三样加上安全停止的最小需要：

- 命令面：`devps [--all] [--port N] [--json]`；`devps stop PID@STARTED … [--force] [--yes] [--timeout S] [--json]`。
- 口径：孤儿（PPID=1）∧ 不在 launchctl ∧ 非 zombie ∧ 归属 dev_dir 项目 ∧ 不命中 ignore → CHECK（默认显示）；命中 ignore → EXPECTED（仅 `--all`）。不实现 §3.1 的五态级联，MANAGED 在进入候选前就被过滤。
- 采集：`ps -axww`（两次：pid/ppid/lstart/stat/command 与 pid/comm，后者取真实可执行路径做 service 名）+ `lsof`（cwd、监听端口各一次）+ `launchctl list`，各一次调用，总计 < 0.7s。
- 停止：**@STARTED 无条件必带**（比 §6 更严，tty 也必带）；发信号前重读 uid/lstart 复核；拒绝自身/祖先链/PID 1；不 killpg、不展开子树。zombie（父进程未 reap）记 `exited`。
- 相对正文的替换：psutil → ps+lsof；role 标签、`detail`、`--project`、`--limit`（固定 20 条）、config 的 ignore_projects 均不做；`ignore` 规则 = 内置默认（cursor-agent worker）+ `~/.config/devps/config.toml` 可选合并。
- 测试：`python3 devps/test_devps.py`，26 项 assert（classify / mask / lstart / service / stop 真实进程），无框架。本验收收：默认输出"未发现"，`--all` 恰一条 EXPECTED 的 cursor-agent（归属 sub-pipe），`--port 5000` 显示 ControlCenter，真实 69 字符 `--api-key` 在 JSON 中显示 `****`。

正文以下保留 v0.2 完整设计作对照；与 `devps.py` 冲突处以代码为准。

---

## 1. 目标与非目标

**目标**：找出**启动者已退出**、仍在运行的开发进程，说明项目归属与依据；用户明确选择后，有依据地停止。

**不做**：自动清理；常驻组件；其他用户 / root 进程；Linux / Windows / TUI / MCP；STALE / 注册表 / Lease（没有证据来源，见 §10）。

环境变量不读。这是设计选择，不是平台限制：macOS 上同用户进程的环境变量可读（`ps eww` 实测）。因此凭据风险只收敛在 argv（§5.5）。

## 2. 设计依据（2026-09-29 本机实测）

| 事实 | 结论 |
|---|---|
| 项目内共 49 个进程。v0.1 的"归属即 CHECK"会出 17 行（zsh×13、claude×4…），全是活着的；其中 PPID=1 的只有 1 个（`cursor-agent`） | 默认口径改为"启动者已退出"（§3） |
| 348 个 PPID=1 进程里 229 个在 `launchctl list` 中（GUI 应用、LaunchAgent）；tmux 未运行 | launchctl 是区分"孤儿"与"托管"的关键来源；tmux 不单独查询 |
| Cursor worker 的真实路径是 `~/Library/Application Support/Cursor/User/globalStorage/anysphere.cursor-agent-worker/…/cursor-agent … worker start`，不在 `Cursor.app` 内；命令行含空格分隔的 `--api-key <69 字符>` | expected 规则改锚该路径；脱敏必须按 argv 处理，v0.1 的 `key=value` 正则会原样放行 |
| 5000 / 7000 端口被 `ControlCenter`（AirPlay）占用，不属于任何项目 | `--port` 不做归属过滤 |
| Claude、VS Code、ChatGPT（Codex）、WorkBuddy 四个 GUI 应用自身 PPID=1、cwd 不在项目内，各有 4–12 个项目内后代 | 不做"后代归属"，否则它们会被误判为候选（它们都在 launchctl 里，靠 MANAGED 才被排除） |

## 3. 判定模型

### 3.1 候选与状态

只有 **PPID=1 的进程**才是候选根，其后代折叠进同一行（§3.4）。父进程还活着，说明启动者仍在，不算"遗留"。PPID=1 只用来划定"先看谁"，不用来定性：状态仍是 CHECK，不是"可停止"。

按顺序匹配，命中即止；所有证据累加进 reason：

| 顺序 | 条件 | 状态 | 默认显示 |
|---|---|---|---|
| 1 | 未归属项目（§3.2 都不成立） | 无状态（仅 `detail` / `--port` 可见） | 否 |
| 2 | PID 在 `launchctl list`（launchd 托管，含 GUI 应用） | `MANAGED`（launchd:标签） | 否 |
| 3 | PPID≠1（父进程存活） | `MANAGED`（父进程 名(pid)，有 tty 时注明） | 否 |
| 4 | 命中 expected 规则（§3.3） | `EXPECTED`（规则名） | 否 |
| 5 | 其余（PPID=1，无 launchd 记录） | `CHECK` | **是** |

- `STALE` 是保留名，v0.2 不产生。
- PPID=1、运行久、无端口，单项都不构成结论。CHECK 永不输出"可停止 / 安全可杀"类表述。
- 已知取舍：启动者仍存活但已闲置（Agent 会话没关、终端标签忘了关）不算遗留，用 `--all` 查看。

### 3.2 项目归属

依次取第一个成立的（`evidence` 写入输出）：

1. `cwd` 在某个 `dev_dir` 下 → 项目 = `dev_dir` 下第一级目录名（`cwd`）
2. argv 中出现 `dev_dir` 下的路径 → 同上（`cmdline`）
3. 父进程已归属 → 继承（`ancestor`）。最弱证据；孤儿根没有父项目，所以只影响 `--all` / `--project`

`dev_dirs` 默认 `["~/Code"]`；想要更细的项目粒度，把子目录单列进去。

### 3.3 角色与 expected 规则

一张表 `RULES = [(regex, role, expected)]`，匹配完整 argv 拼接串，先命中先得。`role` 只是 detail / JSON 里的标签（`dev_server` / `task_watcher` / `agent_runtime` / `agent_worker` / `ide_helper` / `app_service` / `unknown`）。**可见性只由 §3.1 的状态决定。**

内置 expected 只放已实测的，其余随用随加：

| role | 匹配 |
|---|---|
| `agent_worker` | `anysphere\.cursor-agent-worker/.*worker start` |
| `ide_helper` | `Code Helper\|Cursor Helper\|tsserver\.js\|pylance` |

其余角色仅打标签：vite / uvicorn / next dev → `dev_server`；nodemon / vitest / celery / `--watch` → `task_watcher`；claude / codex / aider / opencode → `agent_runtime`；postgres / redis / nginx → `app_service`。

expected 规则必须锚定宿主路径或完整可执行路径，不许裸关键词（裸 `worker` 会误伤业务脚本）。用户规则见 §7。

service 名：argv 中第一个"程序 / 模块"token。跳过解释器（python / node / ruby / java / uv…）与 `-` 开头的参数，`-m X` 取 X，取 basename 去扩展名；取自**遮蔽后**的 argv。`python -m subtitle_pipeline serve` → `subtitle_pipeline`。启发式很朴素，遇到无意义的名字再改。

### 3.4 折叠

显示集合中"父进程不在集合内"的进程各占一行；行内 `SERVICE +N` 的 N = 其后代进程总数（不论状态）。默认视图即候选根 + 后代数。同名兄弟孤儿不合并，出现刷屏再按 (project, service) 合并。

## 4. 采集

一次快照，各外部命令各调一次，之后全是内存里的纯函数：

- **进程**：两次 `ps -axww`（0.09s / 682 进程实测）：`pid=,ppid=,lstart=,stat=,command=` 拿全量；`pid=,comm=` 拿可执行路径（行尾整段，`Application Support` 等含空格路径不切坏——此前"ps 会断词、必须 psutil"的说法错误，撤回）。zombie 看 `stat` 列；same-uid 由 `-u` 保证；devps 自身父链存活、PPID≠1，天然不成为候选。
- **端口**：`lsof -nP -a -iTCP -sTCP:LISTEN -u <uid> -Fpn`（3s 超时）→ `{pid: [port]}`。
- **launchd**：`launchctl list` → `{pid: label}`（PID 列为 `-` 的忽略）。

本机各外部命令实测均 < 0.5s（ps 0.09s、lsof 端口 0.07s、launchctl 0.01s，lsof cwd 同量级）。目标：整体 < 2s（实测 0.69s）。

**完整性告警**（stderr；`--json` 写入 `warnings[]`）。任何采集失败都不得表现为"没有异常"：

| 情况 | 处理 |
|---|---|
| lsof 失败 / 超时 | PORT 列显示 `?` + 告警 |
| launchctl 失败 | 告警"托管状态未知，结果可能含 launchd 托管服务" |
| 孤儿（PPID=1 且不在 launchctl）里 cwd 与 cmdline 都读不到归属的进程有 N 个 | 告警"N 个进程无法判断归属，可能漏报"（其他进程读不到不告警，避免常驻噪音） |
| ps 枚举整体失败 | 报错，退出 1 |

有告警且无候选时，输出"未发现需要检查的开发进程（扫描不完整，见警告）"，不输出干净的"未发现"。

## 5. 命令与输出

### 5.1 命令

```
devps [--all] [--project NAME] [--limit N] [--json]   # 列表：默认只显示 CHECK
devps --port N [--json]                                # 端口属主：不做归属/状态过滤
devps detail PID [--json]
devps stop PID[@STARTED] [PID[@STARTED] ...] [--force] [--yes] [--timeout S] [--json]
```

- 默认最多 20 行；`--limit N` 覆盖，`0` 不限。超出时：`… 还有 N 条未显示（--limit 0）`。
- `--all` 显示全部已归属进程（含 MANAGED / EXPECTED）；`--project` 与 `--all` 正交，可组合。
- `--port N` 无监听者时：`本用户没有进程监听端口 N（其他用户/系统进程需 sudo lsof -iTCP:N 确认）`。
- 无候选时只输出一行：`未发现需要检查的开发进程`。
- 退出码：`0` 成功；`1` 失败 / 拒绝 / 部分失败（原因在文本或 JSON 里）；`2` 由 argparse 给。

### 5.2 表格

```
STATE  PID    AGE  PROJECT        SERVICE               PORT  REASON
CHECK  62894  7d   transcription  subtitle-pipeline +2  8765  父进程为 1，无 launchd 记录
```

各列按内容取宽（PROJECT ≤ 16、SERVICE ≤ 24）；REASON 是唯一含中文的列且在末尾，不补齐、不截断，所以不需要 CJK 宽度处理；无颜色。REASON 只放定性事实（≤ 2 句）；年龄、端口已有单独的列。排序：CHECK → EXPECTED → MANAGED，同状态按年龄降序。

### 5.3 detail

```
PID 62894  subtitle-pipeline serve            STATE: CHECK  ROLE: dev_server
项目   transcription  (cwd → /Users/me/Code/transcription)
运行   7d2h（自 2026-09-22 14:03）   端口 8765   子进程 2
命令   /usr/bin/python3 -m subtitle_pipeline serve --token ****
父链   launchd(1) ← 62894
依据   ORPHAN      父进程为 1，启动者已退出
       NO_LAUNCHD  launchctl 中没有该 PID
       PROJECT     cwd 在 ~/Code/transcription 下
提示   用途无法确认；如需停止，请显式执行: devps stop 62894@1790056980
```

detail 对任意同 uid 的 PID 可用（未归属项目时项目显示 `-`）。证据码：`ORPHAN` / `NO_LAUNCHD` / `LAUNCHD` / `PARENT_ALIVE` / `RULE` / `PROJECT` / `PORT` / `CHILDREN`。

### 5.4 JSON（`--json`，三个命令都支持）

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-29T16:05:00+08:00",
  "summary": { "scanned": 525, "shown": 1, "more": 0, "zombie": 0 },
  "candidates": [{
    "pid": 62894, "started": 1790056980, "age_seconds": 612120,
    "state": "CHECK", "role": "dev_server", "service": "subtitle-pipeline",
    "project": { "name": "transcription", "evidence": "cwd" },
    "ports": [8765], "children": 2,
    "evidence": [{ "code": "ORPHAN", "detail": "父进程为 1" },
                 { "code": "NO_LAUNCHD", "detail": "launchctl 中没有该 PID" }]
  }],
  "warnings": []
}
```

- list 不含 cmdline / cwd（需求：完整命令只在详情）。`detail --json` 增加 `ppid`、`cwd`、`cmdline`（已遮蔽的 argv 列表）、`ancestors`、`children_pids`。
- `started` 是 `ps lstart` 的整数秒（秒粒度），也是 `stop PID@STARTED` 里的 STARTED。
- `stop --json`：`{"results":[{"pid":…,"result":"terminated|exited|still_running|refused","reason":"PROTECTED|PID_REUSED|NEEDS_STARTED|NEEDS_CONFIRM|NOT_FOUND|…"}]}`。
- Agent 调用流：`devps --json` → 取 `pid` + `started` → `devps stop PID@STARTED --yes --json`。

### 5.5 凭据遮蔽

所有输出通道（detail、`--json`、stop 计划、告警、错误信息）都经同一个 `mask(argv)`；表格与 list JSON 根本不展示命令行。对 argv 列表逐元素处理：

1. 元素是敏感选项名（`^-{1,2}[\w-]*(key|token|secret|pass(word|wd)?|pwd|auth|credential)[\w-]*$`，不区分大小写）→ **下一个元素**整体遮蔽；`--opt=值`、`NAME=值` 同理，只遮蔽值。
2. `Authorization:` / `Bearer` / `Basic` 头 → 冒号后整体遮蔽。
3. URL userinfo：`scheme://user:密码@` → `user:****@`。
4. 已知前缀 `sk-`、`ghp_`、`github_pat_`、`xox[bp]-`、`AKIA…`、JWT（`eyJ….….…`）→ 整体 `****`。
5. 兜底：不含 `/` 且 ≥ 32 字符的 `[A-Za-z0-9_+=.-]` 串，同时含字母和数字 → `****`。

遮蔽为整段 `****`，不保留任何原文字符（v0.1 保留前 4 位会泄露口令前缀）。局限：短选项（`redis-cli -a x`、`mysql -px`）与无标记的位置参数只靠规则 4、5 兜底，属 best-effort，这也是表格不展示命令行的原因。

## 6. 停止

```
devps stop 62894@1790056980 [PID ...] [--force] [--yes] [--timeout 5]
```

1. 目标必须是快照中的同 uid 进程，否则拒绝（`NOT_FOUND`）。
2. **`@STARTED` 无条件必带**（最小实现比原文更严：tty 也必带），否则拒绝（`NEEDS_STARTED`）。带了就与 `lstart` 整数秒比对，不符拒绝（`PID_REUSED`）。理由：PID 复用的真实窗口是"上次列表 → 这次 stop"，STARTED 是唯一能证明"停的就是看到的那个进程"的东西。detail 与 JSON 里都有现成的值。
3. 拒绝 devps 自身、其祖先链、PID 1（`PROTECTED`）。不维护其他名单：真正会被误杀的是跑着 devps 的 Agent / IDE 链，名单永远列不全。
4. 打印计划：每个目标的 PID / 状态 / 项目 / service / AGE / 端口 / 遮蔽后的命令，以及存活的子进程（前 5 个）与提示"子进程不会被一并停止，需要请显式列出 PID"。目标未归属任何项目时醒目标注"非开发进程"；目标是 launchd 托管时追加提示"停止后可能被自动拉起，请用 launchctl / brew services 管理"。**不使用 killpg，不按进程组或子树展开。**
5. 确认：tty 且无 `--yes` → 一次 `[y/N]`；非 tty 且无 `--yes` → 拒绝（`NEEDS_CONFIRM`）。`--yes` 只跳过提示。
6. 发信号：发前重读一次 `ps -p PID -o uid=,lstart=,stat=`，确认同 uid、lstart 与 started 字段相符且非 zombie，再 `os.kill`（SIGTERM；`--force` 改 SIGKILL）。**`--force` 不影响是否确认。** 人工复核与 psutil 的 `_raise_if_pid_reused()` 同款模式，同样有毫秒级残留竞窗（macOS 无 pidfd），接受并注明。已退出或已成 zombie → 记 `exited`。
7. 等待 `--timeout`（默认 5s）。超时 → `still_running` 并提示 `devps stop PID@STARTED --force`；**不自动升级 SIGKILL**。
8. 多目标逐个独立处理；全部 `terminated` / `exited` 才退出 0。

## 7. 配置

`~/.config/devps/config.toml`，可选，缺失用默认值：

```toml
dev_dirs = ["~/Code"]
ignore = [
  "redis-server.*6379",      # 本机常驻开发 redis（原因写注释）
  "tmux: server",
]
ignore_projects = ["scratch"]
```

- `ignore`：regex，匹配完整命令行 → `EXPECTED`；`ignore_projects`：项目名精确匹配 → `EXPECTED`。命中规则显示在 `--all` 的 REASON 里。
- 没有 `ignore` 子命令，手改文件。
- TOML 解析失败或 regex 非法 → 明确报错并退出 1，不静默降级（忽略规则失效不能被当成"没有异常"）。

## 8. 实现与测试

- `devps/devps.py` 单文件（~290 行，已落地）：`collect()` 是唯一碰 ps / lsof / launchctl 的函数 → `classify()` 纯函数 → 渲染 → `main()`。**没有 Provider 抽象**：测试直接给 `classify` 传 dict。
- 零依赖、无 pyproject、不安装：`python3 devps/devps.py` 直接运行；测试 `python3 devps/test_devps.py`。
- `devps/test_devps.py`，三组 assert（沿用仓库根的 `test_*.py` 惯例）：
  1. **classify**：孤儿 + 项目内 → CHECK 且含 `ORPHAN` / `NO_LAUNCHD`；launchctl 命中 → MANAGED；PPID≠1 → MANAGED；cursor worker 真实路径形态 → EXPECTED；未归属 → 无状态；折叠 `+N`；`--port` 不受归属过滤。
  2. **mask**：§5.5 覆盖的写法（`--token` / `--password` / `--secret` 空格分隔、`--api-key=值`、`Authorization: Bearer`、DSN 口令、未知选项 + 长串兜底）+ 真实形态（空格分隔的 69 字符 `--api-key`）。断言文本与 JSON 输出都不含任一原文。已知局限（`redis-cli -a x`）不入测试，只在文档里声明。
  3. **stop**：spawn `sleep 60`。错误的 `@STARTED` → 拒绝且进程仍活；非 tty 且无 `@STARTED` → 拒绝；正确 → 终止；自身 / 父进程 / PID 1 → 拒绝。
- 验收时在本机手动跑一次：`devps` 应输出"未发现…"（`cursor-agent` 命中 expected）；`devps --all` 能看到它；`devps --port 5000` 显示 ControlCenter。

## 9. 验收对照

| 验收标准 | 措施 |
|---|---|
| 大量 IDE / Agent 进程时默认输出仍简短 | 只看 PPID=1 且不在 launchctl 的项目内进程；活父进程一律 MANAGED；20 行上限。按本机快照推演默认 0 行（v0.1 口径 17 行） |
| 能发现项目中的无端口 worker | 候选不依赖端口，归属靠 cwd / cmdline |
| 每条结果有可读依据 | reason ≥ 1（CHECK 固定含"父进程为 1，无 launchd 记录"），detail 列全部 evidence |
| 无法证明任务结束时用 CHECK | 没有 STALE 路径，没有"可停止"文案 |
| 不泄露命令行凭据 | §5.5 逐元素遮蔽 + 表格 / list JSON 不展示命令行 + 真实形态测试 |
| 停止不因 PID 复用或进程组误杀 | `@STARTED` 核验（无人确认时必带）+ psutil 发信号前自检 + 不用 killpg、不展开子树 + 拒绝自身 / 祖先 / PID 1 |

## 10. 有意不做（相对 v0.1）

| 不做 | 原因 | 何时再加 |
|---|---|---|
| STALE / registry / lease / `devps run` 包装器 | 没有证据来源；你同时在用 Claude Code / Codex / Cursor / opencode，没有一个会走包装器 | 第二阶段优先试"任务边界进程差集"（开始时 snapshot，结束或 hook 时 diff） |
| 进程组 / 子树停止（`--group`） | 计划里已列出子进程，可显式多 PID | 真需要时加 `--tree`（psutil `children(recursive=True)`，逐个过身份核验），仍不用 killpg |
| audit.jsonl | 无人值守停止尚未发生 | Agent 开始常规 `--yes` 调用时（约 5 行） |
| `doctor`、`ignore` 子命令、i18n、CJK 宽度对齐、颜色 | 无真实需求；告警已覆盖失败可见性 | — |
| tmux 查询、supervisor 名单、保护名单 | 活父进程即 MANAGED；tmux server 用 `ignore` 一行解决 | 出现误报时 |
| 逐进程 CPU 采样 | `interval=0.1` 串行阻塞（20 行 2s，`--all` 数十秒），且对判断没有信息量 | 需要时用 `cpu_times()/age` 的一生平均，不等待 |
| 后代归属 / 同名孤儿合并 | 后代归属依赖 launchctl 排除 GUI 应用，且实测无漏报 | 出现漏报再加 |
| 细分退出码、`--exit-code` | JSON 里有原因 | — |

第三阶段（Linux / Windows、TUI / MCP、异常提醒）不变，不在本稿范围。
