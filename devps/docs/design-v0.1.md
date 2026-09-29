# devps 设计文档 v0.1（评审稿）

> 状态：草案，待评审。本文档对应《开发需求整理》首版（MVP）范围，暂不进入代码实现。
> 平台：macOS（13+）；运行时：Python 3.11+ + psutil。
> 演进起点：现有原型 `dev-process-audit.sh`（PPID=1 + CWD 过滤），MVP 将其泛化并补齐安全判断。

---

## 1. 目标与非目标

**目标**：发现当前用户名下"值得处理的开发进程"，给出归属、角色、状态与可读依据；在用户明确选择后安全地停止。

**非目标（MVP 明确不做）**：

- 不自动清理任何进程；无 daemon/常驻组件，纯按需执行的一次性命令。
- 不扫描其他用户 / root 进程（需要 sudo 的能力后置到第三阶段，缩小权限与泄密面）。
- 不读取其他进程的环境变量（macOS 上本就不可读；凭据风险集中在 argv，见 §7.3）。
- 不做 TUI / MCP / Linux / Windows（第三阶段）。
- 不依赖 Process Compose 或其他 supervisor；只审计，后接管理。

---

## 2. 核心概念与状态模型

### 2.1 角色分类（role）

| 角色 | 含义 | 示例 | 默认可见性 |
|---|---|---|---|
| `dev_server` | 开发服务 | vite / uvicorn / next dev / rails server | 显示（通常 CHECK） |
| `task_watcher` | watcher / 任务进程 | tsc --watch / nodemon / vitest watch / celery worker | 显示 |
| `agent_runtime` | Agent CLI / runtime | claude / codex / aider / ollama serve | 显示 |
| `agent_worker` | Agent 常驻 worker | Cursor `worker start`、后台 indexing worker | `--all` 显示（EXPECTED） |
| `ide_helper` | IDE helper / LSP | Code Helper (Renderer/Plugin)、Pylance、tsserver | 默认隐藏 |
| `app_service` | 应用基础服务 | postgres / redis / docker / nginx | 显示（若归属项目） |
| `unknown` | 无法归类 | — | 仅项目归属成立时显示为 CHECK |

角色由"内置规则库 + 用户规则"共同决定（§5），规则可覆盖内置结论。

### 2.2 生命周期状态（state）与判定顺序

按顺序匹配，命中即止；证据始终**累加**（reason 列出全部发现，state 取第一条命中）：

| 顺序 | 条件 | 状态 |
|---|---|---|
| 1 | 存在可识别 supervisor：launchd 注册项 / tmux 会话 / 已知 supervisor 父进程 / devps 注册记录 | `MANAGED` |
| 2 | 注册记录（lease/task）明确表明任务或租约已结束，进程仍存活 | `STALE` |
| 3 | 命中 expected 规则或内置长期驻留角色库（且规则锚定可信，见 §5.3） | `EXPECTED` |
| 4 | 项目归属成立（CWD / cmdline 路径 / 祖先进程），但用途或生命周期无法确认 | `CHECK` |
| 5 | 项目归属不成立 | 不在候选之列（`--all` 中以 `unknown` 角色呈现，不计入默认 20 条） |

硬性语义（来自需求，写进验收）：

- `PPID=1`、运行久、CPU 低、无监听端口，**任何单项都不足以升格或降格状态**，只能作为 evidence 出现。
- `EXPECTED` ≠ 已验证健康，只表示"匹配明确的长期驻留配置或已知角色"。
- 无任务结束证据时一律 `CHECK`，**永不输出"安全可杀"类表述**。

### 2.3 MVP 中 STALE 的诚实说明

STALE 依赖"任务/租约已结束"的证据。MVP 没有注册表（§10），因此 **MVP 阶段实际几乎不产生 STALE**，重心是把 CHECK / MANAGED / EXPECTED 判准；STALE 判定随第二阶段注册表落地。设计上状态机与 reason_code 现在就预留 STALE 路径，避免二次返工。

---

## 3. 架构与模块

```
┌─────────────────────────── devps CLI ───────────────────────────────┐
│  cli.py (argparse) ── scan / detail / stop / ignore / doctor        │
└──────┬──────────────┬───────────────┬──────────────┬────────────────┘
       │              │               │              │
   collect         classify        report         stopper
   ├─ process.py   ├─ roles.py     ├─ table.py    ├─ guard.py     身份守卫
   ├─ ports.py     ├─ states.py    ├─ json_out.py ├─ signals.py   优雅/强制
   ├─ ancestors.py ├─ reasons.py   └─ mask.py     └─ audit.py     审计日志
   ├─ launchd.py   └─ aggregate.py
   └─ tmux.py
   config.py（TOML + ignore/expected 规则）    registry.py（二阶段占位）
```

- **ProcessProvider 抽象层**：所有进程数据采集（枚举、属性、端口、祖先）走统一接口。真实实现基于 psutil/lsof；测试用 fixture 回放或合成进程注入。这是"停止不误杀"可被单元测试覆盖的前提。
- 一次 `devps` 调用只做**一次快照**：进程列表、端口映射、launchctl/tmux 查询各执行一次并在内存共享，避免 O(N) 次子进程调用。

目录（建议独立仓库）：

```
devps/
  pyproject.toml
  src/devps/
    cli.py  config.py  registry.py
    collect/  classify/  report/  stopper/
  rules/builtin_roles.toml        # 内置角色规则库（数据驱动，可独立更新）
  tests/  (fixtures/, integration/)
```

依赖：`psutil`（必需）；`argparse` + `tomllib`（标准库）。不引入 rich/click；表格自绘（含 East Asian Width 对齐）。若评审偏好 click 可替换，无架构影响。

---

## 4. 数据采集（macOS 细节）

### 4.1 进程枚举与快照字段

- 范围：`psutil.process_iter()`，按 `uid == os.getuid()` 过滤；`NoSuchProcess`/`ZombieProcess` 逐个吞掉并计数。
- zombie（status `Z`）单独计数排除：它们已死、无法被"停止"，显示反而误导；`--all` 汇总行提示 `zombie ×N`。
- 每进程采集：pid、ppid、pgid、name、exe、cmdline、create_time、status、cpu（可选采样）、memory(rss)、cwd、terminal?。
- 采集失败（AccessDenied 等）**逐字段降级**为 None，并把 `ACCESS_DENIED:<field>` 计入 warnings，绝不静默当"无异常"。

### 4.2 CWD

- psutil `Process.cwd()`（macOS 走 `proc_pidinfo(PROC_PIDVNODEPATHINFO)`），对同用户进程一般可用。
- 沙盒应用 / 部分助手进程会拒绝读取 → 记 `cwd unknown`，改用 cmdline 路径与祖先进程归属，不打断扫描。

### 4.3 端口映射

- 每次扫描执行一次：`lsof -nP -a -iTCP -sTCP:LISTEN -u <uid> +c0`，解析为 `{pid: [port,...]}`（NAME 列 `*:8765` / `127.0.0.1:8765` / `[::]:8765`）。
- lsof 失败/超时：降级为"端口列 = ?" + warning `LSOF_FAILED`；进程发现与归属判断**不依赖**端口。
- MVP 仅 TCP LISTEN；UDP / ESTABLISHED 后置。`--port` 模式复用同一映射。

### 4.4 祖先链与 supervisor 识别

- 祖先链：psutil 逐级上溯至 launchd，深度上限 16，缺失/拒绝逐级降级。
- launchd：`launchctl list`（快、可解析：`PID\tStatus\tLabel`）建立 `{pid: label}`；命中 → evidence `MANAGED_LAUNCHD(label)`。注意 `PID=-` 的条目是未运行任务，忽略。
- tmux：`tmux list-panes -a -F '#{pane_pid}\t#{session_name}\t#{window_name}'` 建 `{pane_pid: session}`，进程的祖先命中 pane_pid → evidence `MANAGED_TMUX(session:window)`；tmux server 未运行视为"无此来源"，不算 warning。
- 其他 supervisor：祖先链中命中已知 supervisor 进程名（supervisord、pm2、forever、docker-proxy 等）→ `MANAGED_SUPERVISOR(name)`。
- PPID=1 语义：在 macOS 上 launchd 代管孤儿，仅作为 `ORPHAN_PPID1` 证据，不参与状态判定（§2.2）。

### 4.5 CPU 采样

只对"最终可见候选"做一次 `cpu_percent(interval=0.1)`，作为低活动证据 `LOW_CPU`（仅描述性，永不单独作为结论）。

---

## 5. 识别规则（内置角色库）

### 5.1 规则形态（数据驱动，`rules/builtin_roles.toml`）

```toml
[[role]]                                # 内置规则示例
role = "ide_helper"
hidden = true
any_of = [
  { cmd_regex = "/Code Helper( \\(|$)" },     # VS Code Electron helpers
  { cmd_regex = "/Cursor Helper" },
  { cmd_regex = "tsserver\\.js" },
  { cmd_regex = "pylance" },
]

[[role]]
role = "agent_worker"
expected = true                          # 默认不报警，--all 显示为 EXPECTED
all_of = [
  { cmd_regex = "/Cursor\\.app/" },      # 必须锚定宿主应用路径，防误伤
  { cmd_regex = "worker start" },
]

[[role]]
role = "dev_server"
any_of = [
  { cmd_regex = "(^|/)vite( |$)" },
  { cmd_regex = "uvicorn" },
  { cmd_regex = "next( |-)dev" },
  { cmd_regex = "-m http\\.server" },
]
```

- 匹配优先级：用户 expected/ignore 规则 > 用户角色规则 > 内置规则（同段内按文件顺序，先命中先得）。
- service 名提取：优先命令行中的模块/脚本名（如 `subtitle-pipeline serve` → `subtitle-pipeline`），其次 exe basename；供聚合与展示。

### 5.2 项目归属（三条独立证据，取最具体者）

1. `PROJECT_BY_CWD`：cwd 位于 dev_dirs 下 → 项目名 = dev_dir 下第一级目录；若该目录是 git 仓库根则用仓库名。
2. `PROJECT_BY_CMDLINE`：argv 中出现 dev_dirs 下的路径。
3. `PROJECT_BY_ANCESTOR`：祖先链中某进程 CWD 命中（覆盖由终端在项目目录启动的 worker）。

dev_dirs 默认 `["~/Code", "~/dev"]`（存在才生效），配置可增删。归属全部失败 → 不产生候选（见 §2.2 顺序 5）。

### 5.3 expected 规则的安全约束（防"一刀切静默"）

- `cmd_regex` 必须同时锚定**宿主路径**（如 `/Cursor.app/`、`/Visual Studio Code.app/`）或完整可执行路径，不允许裸关键词（裸 `worker` 会误伤业务脚本）。
- 所有 expected/ignore 命中都必须带 `note`（人类可读原因），在 `--all` 与 `devps ignore list` 中展示，保证"静默也有依据"。

### 5.4 聚合

- 聚合键：`(project_name, service_name)`。
- 组长选择：监听端口的进程 > 最老进程；行内 SERVICE 列标 `×N`，PORT 显示并集。
- 详情里给出组成员清单；MVP 不提供"一键停组"，组操作走 `devps stop --group <PGID>`（§8.4）。

---

## 6. 判定流水线与 reason 体系

### 6.1 reason_code 清单（初版）

| 类别 | code | 说明 |
|---|---|---|
| 管理 | `MANAGED_LAUNCHD` / `MANAGED_TMUX` / `MANAGED_SUPERVISOR` / `MANAGED_REGISTRY` | 各 supervisor 命中 |
| 结束 | `STALE_LEASE_EXPIRED` / `STALE_TASK_ENDED` | 仅注册表（二阶段） |
| 生命周期 | `ORPHAN_PPID1` / `NO_MANAGER_RECORD` | 描述性证据 |
| 强度 | `AGE_LONG`（≥24h，文案注明时长） / `LOW_CPU` / `NO_LISTEN_PORTS` / `PORT_LISTENING(n)` | 描述性证据 |
| 归属 | `PROJECT_BY_CWD` / `PROJECT_BY_CMDLINE` / `PROJECT_BY_ANCESTOR` / `UNKNOWN_PROJECT` | 带 path/detail |
| 角色 | `ROLE_MATCH(role, rule)` / `IGNORED_BY_RULE(note)` / `EXPECTED_BY_RULE(note)` | 带规则名与 note |
| 异常 | `ACCESS_DENIED(field)` / `ZOMBIE` | 触发 warning 流 |

- 每个候选 state_reasons ≥ 1；CHECK 必须能回答"为什么值得看"（默认至少含 `NO_MANAGER_RECORD`，如另有 `ORPHAN_PPID1` / `AGE_LONG` 一并列出）。
- 文案集中在 i18n 表（默认中文，`--lang en` 预留），REASON 列取 2–3 条最重证据的短文案拼接。

### 6.2 排序与默认显示

- 默认仅显示 `CHECK`、`STALE`；上限 20 条，超出显示 `… 还有 N 条未显示（--limit N / --all）`。
- 排序：STALE 优先，其次 CHECK 按年龄降序（越老越靠前）。
- 无候选：仅输出一行"未发现需要检查的开发进程"（`--json` 时 `candidates: []` 且附 summary）。
- 扫描异常（如 lsof 失败、大量 AccessDenied）：stderr 打印 warning 摘要；`--json` 写入 `warnings[]`。**任何采集失败都不允许表现为"没有异常"。**

### 6.3 默认视图示例

```
STATE   PID     AGE  PROJECT        SERVICE              PORT   REASON
CHECK   62894   7d   transcription  subtitle-pipeline    8765   无管理记录；PPID=1；低CPU
CHECK   70233   2d   infra-scripts  vitest               -      无管理记录；无端口；低CPU
… 还有 3 条未显示（--limit 50 / --all）
已按规则忽略 6 个进程，zombie 2 个（--all 查看）
```

---

## 7. 输出

### 7.1 表格约束

- 列宽：STATE 8 / PID 7 / AGE 5 / PROJECT 16 / SERVICE 20 / PORT 6 / REASON 末列（≤60 字符，超长省略；完整版在 detail）。
- 对齐处理 East Asian Width（PROJECT/SERVICE/REASON 可能含中文）。
- 非 tty（管道）输出：关闭颜色，宽度默认 120。
- `--limit N` 覆盖默认 20；`--all` 显示全部角色（含 MANAGED / EXPECTED / ide_helper / unknown），ignored 仍只计数量不展示（防刷屏），可在 `devps ignore list` 审计。

### 7.2 detail 视图（`devps detail PID`）

```
PID 62894  subtitle-pipeline serve          STATE: CHECK   ROLE: dev_server
project: transcription (/Users/…/sub-pipe)   evidence: PROJECT_BY_CWD
age: 7d2h (since 2025-11-20 14:03)           cpu 0.1%  rss 182MB
ports: 8765 (TCP LISTEN)                     pgid: 62894 (单进程)
cmd:  /usr/bin/python3 -m subtitle_pipeline serve --token sk-ab**** …(已遮蔽)
cwd:  /Users/…/sub-pipe
tree: launchd(1) ── 62894（父进程为 1，无 supervisor 记录）
evidence:
  - NO_MANAGER_RECORD   未在 launchctl / tmux / 已知 supervisor 中找到
  - ORPHAN_PPID1        父进程为 1
  - AGE_LONG            已运行 7 天
  - LOW_CPU             采样 CPU 0.1%（仅描述，不构成结论）
建议: 用途不明确时先确认归属；停止需你明确执行: devps stop 62894
```

### 7.3 凭据遮蔽（表格 / detail / --json 三处一致）

- 模式：`(?i)(api[_-]?key|token|secret|password|passwd|pwd|authorization|bearer)\s*[=:]\s*(\S+)`；已知前缀 `sk-`、`ghp_`、`github_pat_`、`xox[bp]-`、`AKIA[0-9A-Z]{16}`；JWT（`eyJ…` 三段式）。
- 遮蔽形如 `sk-ab****`（保留前 4 字符）；JSON 中 cmdline 同样遮蔽并标 `cmdline_redacted: true`，**任何输出通道都不存在原始 argv 备份**。
- macOS 上 psutil 拿不到其他进程 environ，泄露面收敛在 argv——遮蔽因此覆盖 argv 全量（含 detail 的完整命令）。
- 测试为属性测试：给定一组注入 secret，断言所有渲染输出中不含 secret 任意子串。

### 7.4 JSON 输出（`--json`，schema v1）

```json
{
  "schema_version": 1,
  "tool": "devps",
  "generated_at": "2025-11-27T10:00:00Z",
  "scope": { "uid": 501, "dev_dirs": ["/Users/me/Code"] },
  "summary": { "visible": 5, "scanned": 341, "hidden_by_rule": 6, "zombie": 2, "warnings": 1 },
  "candidates": [{
    "pid": 62894, "ppid": 1, "pgid": 62894,
    "state": "CHECK", "role": "dev_server", "service": "subtitle-pipeline",
    "project": { "name": "transcription", "path": "/Users/me/Code/transcription",
                 "evidence": "PROJECT_BY_CWD" },
    "age_seconds": 612300, "started_at": "2025-11-20T14:03:00+08:00",
    "ports": [8765], "cpu_percent": 0.1, "rss_mb": 182.4,
    "cmdline": ["/usr/bin/python3", "-m", "subtitle_pipeline", "serve", "--token", "sk-ab****"],
    "cmdline_redacted": true, "cwd": "/Users/me/Code/transcription",
    "ancestors": [{ "pid": 1, "name": "launchd" }],
    "children_pids": [], "group_size": 1,
    "state_reasons": ["NO_MANAGER_RECORD", "ORPHAN_PPID1", "AGE_LONG"],
    "evidence": [{ "code": "NO_MANAGER_RECORD", "source": "launchctl+tmux+ps",
                   "detail": "未找到管理记录" }]
  }],
  "warnings": [{ "code": "ACCESS_DENIED", "detail": "cwd: 12 个进程不可读" }]
}
```

`devps detail PID --json` 输出单候选 + 完整 evidence；`devps stop --json` 输出每目标执行结果。schema_version 从 1 起，Agent 接入按 version 判断兼容性。

### 7.5 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功（列表模式：无候选） |
| 1 | 列表模式发现候选（仅当指定 `--exit-code`，方便 CI/Agent 判断；默认列表恒 0） |
| 2 | 参数/用法错误 |
| 3 | 扫描/系统错误（psutil 不可用、lsof 与枚举双失败等） |
| 4 | detail：PID 不存在 |
| 5 | stop：安全拒绝（身份核验失败 / 保护名单 / 非交互缺 `--yes`） |
| 6 | stop：已执行但部分或全部失败 |

---

## 8. 停止流程（安全设计）

### 8.1 身份守卫（防 PID 复用）

身份 token 四元组：`{pid, create_time, pgid, argv 前两段}`。

1. 扫描/计划阶段生成 token；
2. 发信号前**重新读取**目标，逐项比对（create_time 精确相等；argv 前两段逐段相等）；
3. 任一不符 → 该目标中止，报 `PID_REUSE_SUSPECTED`（exit 5），绝不"猜着杀"；
4. 目标已成 zombie → 视为已退出，记成功并注明。

### 8.2 执行步骤

```
解析目标 → 生成停止计划(pid/身份/状态/信号) → [交互:列出计划并确认 | 非交互必须 --yes]
 → 身份守卫复核 → SIGTERM（--force 则 SIGKILL）→ 等待退出(轮询存在性, 上限 --timeout 默认 5s)
 → 逐目标报告结果 → 追加审计日志
```

- 默认只发 SIGTERM；超时未退出 → 明确提示"仍在运行，如需强杀：devps stop PID --force"，**不自动升级为 KILL**。`--force` = 跳过交互确认 + 直接 SIGKILL（守卫复核仍执行）。
- 非交互（无 tty）且未 `--yes` → 拒绝执行（exit 5）。Agent/JSON 场景必须显式 `--yes`，对应"用户明确选择后执行"。

### 8.3 保护名单

默认拒绝停止：devps 自身及其祖先、PID 1（launchd）、loginwindow、WindowServer、Terminal/iTerm2、sshd、sudo；以及用户配置 `protected_names`。命中 → exit 5 + `PROTECTED_PROCESS`。MVP 不提供覆盖开关（宁可少杀）。

### 8.4 进程组

- `devps stop PID` 默认只停该 PID；若该 PID 是组长且存在同组存活成员，计划中列出成员清单并提示"如需整组：devps stop PID --group"。
- `--group`：展开 PGID 全部成员（逐个枚举，**不使用 killpg**），打印完整清单后要求确认（非交互需 `--yes` 且输出成员数）；先子后父、间隔 200ms 逐个发信号，每个成员独立过守卫。

### 8.5 审计

`~/.local/state/devps/audit.jsonl`：时间、uid、目标 token、信号、结果、调用方 tty/ppid。供回溯"谁在什么时候停了什么"，也是第二阶段"按项目停止"的信任基础。

---

## 9. 配置与忽略规则

路径：config `~/.config/devps/config.toml`；state `~/.local/state/devps/`（第三阶段用 platformdirs 适配 Linux/Windows）。

```toml
[scan]
dev_dirs = ["~/Code", "~/dev"]
default_limit = 20
term_grace_seconds = 5

[stop]
protected_names = ["ollama serve"]     # 追加到内置保护名单

[[rules]]                               # ignore = 完全排除（计数提示）
action = "ignore"
kind  = "cmd_regex"                     # name_glob | cmd_regex | path_prefix | port | project
value = ".*redis-server.*6379.*"
note  = "本机常驻开发 redis"

[[rules]]                               # expected = 降为 EXPECTED（--all 可见）
action  = "expected"
kind    = "path_prefix"
value   = "~/Code/infra/scheduler-daemon"
note    = "cron 替代品，长期驻留属预期"
```

- `devps ignore list / add / remove` 管理规则；`devps ignore add <PID>` 从现有进程预填（path_prefix + name_glob，附 note 提示）。
- 规则变化即时生效，无缓存。

---

## 10. 注册表（第二阶段预览，MVP 预留 schema）

- MVP 不写注册文件，但状态机、reason_code、JSON schema 均预留 STALE / MANAGED_REGISTRY 路径。
- 第二阶段锚点是 **`devps run` 包装器**（无需先接 supervisor）：

  `devps run --project sub-pipe --task build --lease 30m -- npm run build`

  注册 `{pid, create_time, project, task, agent, session_id, started_at, lease_until}` 至 `~/.local/state/devps/registry.jsonl`，随后 exec 目标命令。扫描时按 pid+create_time 匹配：命中 → `MANAGED_REGISTRY`；`lease_until` 已过仍存活 → `STALE_LEASE_EXPIRED`（这才是 STALE 的主要证据来源）。
- "按项目停止"（`devps stop --project X`）同样先出成员计划、逐个守卫，不做盲杀。

---

## 11. 性能预算

| 项 | 预算 |
|---|---|
| psutil 全量枚举（~500 进程） | < 0.5s |
| lsof 端口映射（单次调用） | < 1s |
| launchctl list / tmux list-panes | < 0.3s |
| 端到端默认视图 | < 2s（冷启动） |
| `--port N` 模式 | 仅端口映射 + 单进程读取，< 0.5s |

超预算的处理是"提示"而非静默：单次调用 lsof 超时（默认 3s）→ 降级 + warning。

---

## 12. 测试与验收映射

### 12.1 测试策略

- **fixture 回放**：ProcessProvider 接口 + 真实环境快照（含 IDE/Agent 混杂场景）作为回放输入，判定逻辑纯函数化，覆盖状态机与聚合。
- **合成进程集成测试**：测试中 spawn 真实子进程树（`python -m http.server`、`sleep`），验证归属、聚合、停止全链路（含守卫）。
- **遮蔽属性测试**：注入 secret 集合，断言表格/JSON/detail 三通道零泄露。
- **守卫单测**：mock provider 模拟 PID 复用（同 pid、新 create_time）→ 必须中止；zombie → 记成功。

### 12.2 验收标准 → 设计措施

| 验收标准 | 措施 |
|---|---|
| 大量 IDE/Agent 进程时默认输出仍简短 | 角色默认可见性（§2.1）+ 20 条上限 + 聚合（§5.4）+ ignore 计数 |
| 能发现无端口 worker | 归属三通道不依赖端口（§5.2），task_watcher 无端口照样成候选 |
| 每条可疑结果有可读依据 | state_reasons 强制 ≥1，CHECK 必含 NO_MANAGER_RECORD（§6.1） |
| 无法证明任务结束用 CHECK | 状态机无"安全可杀"路径；STALE 仅注册表证据（§2.2/§2.3） |
| 不泄露命令行凭据 | argv 三通道遮蔽 + 属性测试（§7.3） |
| 停止不因 PID 复用/进程组误杀 | 四元组守卫 + 禁 killpg + 保护名单 + 审计（§8） |

---

## 13. 风险

| 风险 | 应对 |
|---|---|
| 部分进程 cwd/exe 读取被拒（沙盒应用） | 字段级降级 + AccessDenied 计数进 warnings，不中断扫描 |
| lsof 冷启动慢 / 失败 | 单次调用 + 3s 超时降级；端口缺失不阻断发现与归属 |
| expected 规则过宽导致静默漏报 | 规则必须锚定宿主路径（§5.3），ignore/expected 全部带 note 并可审计 |
| macOS 版本差异（proc_pidinfo 行为变化） | ProcessProvider 隔离 + doctor 自检（`devps doctor` 检查 lsof/launchctl/psutil/配置可用性） |
| 终端中文对齐错位 | East Asian Width 感知填充；REASON 置末列 |
| 名称 `devps` 与 PyPI/Homebrew 撞名 | 发布前检查占用（发布任务，非 MVP 阻塞项） |

---

## 14. 已按建议确定的默认项（可推翻）

| 决策点 | 默认 | 备选 |
|---|---|---|
| 输出语言 | 中文（跟随需求示例），文案集中 i18n 表，预留 `--lang en` | 英文优先 |
| CLI 框架 | argparse（零依赖，子命令规模小） | click/typer |
| Python 版本 | ≥ 3.11（用标准库 tomllib） | 3.10 + tomli |
| 表格渲染 | 自绘（含 CJK 宽度），不引入 rich | rich（依赖变重） |
| `devps stop` 默认 grace | SIGTERM 后等 5s，不自动升级 KILL | 其他时长经 `--timeout` |
| `--port` 范围 | 仅 TCP LISTEN | UDP/ESTABLISHED 后置 |
| MVP 的 STALE | 状态机预留、实际几乎不产生（无注册表） | 提前做最小注册表 |

## 15. MVP 范围清单（对应需求 §6）

扫描、项目归属、角色分类、状态判定、精简输出/聚合/上限、detail、遮蔽、ignore/expected 规则与 `devps ignore`、安全停止（守卫/优雅/保护名单/组确认/审计）、`--json`、`devps doctor`（自检，可选但推荐）。
第二阶段：`devps run` 注册、lease/TTL、按项目停止、supervisor 接入。第三阶段：异常提醒、Linux/Windows、TUI/MCP。
