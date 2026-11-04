# ai-compact

本目录先按 [../AGENTS.md](../AGENTS.md) 走：Read first、Workflow and authorization、Development
principles、Validation and Git、Task closeout 全部适用。下面只写这个模块额外需要知道的。

## 模块事实

- 零第三方依赖，只用标准库（`tomllib` 读 codex 的 TOML），`python3` 3.11+。
- 入口 `ai_compact.py`（`scan` / `set` / `verify` 三个子命令），自检 `test_ai_compact.py`
  （assert 风格，不用框架）。改完跑：`python3 ai-compact/test_ai_compact.py`。

## 改这个脚本前必须知道的

- 它读写的是**用户真实的 Agent 配置**：`~/.claude/settings.json`、`~/.codex/config.toml`、
  `~/.pi/agent/settings.json`、`~/.config/opencode/opencode.jsonc`。动 `apply()` 前先想清楚
  影响面，跑 `set` 前先备份，`--unset` 之类破坏性操作要能说清恢复路径。
- 阈值语义固定为「上下文达到多少 tokens 触发自动压缩」。reserve 型（pi 用 `reserveTokens`、
  opencode 用 `buffer`）必须由 `context - threshold` 换算；拿不到模型上下文时要求 `-c`
  显式给值，不许猜、不许静默失败。
- 各 Agent 的原生键、开关、是否支持模型级，结论都在仓库根 README 第 8 节。改适配器前用真实
  配置文件 + Agent 自带解析器核实（`codex doctor`、`opencode debug config`），不要照抄别处
  实现或记忆里的字段名（例如 codex 的 `[profiles.*]` 里放 `model_auto_compact_token_limit`
  会被忽略）。
- 新增适配器：继承 `Adapter`，实现 `installed()` / `settings()` / `apply()`；Agent 有原生配置
  校验命令就接到 `native_check()` 里，写入后必须回读比对，不许跳过验证。
- 测试用临时目录当 `root` 构造适配器，**不要**在测试里读写 `~` 下的真实配置。
