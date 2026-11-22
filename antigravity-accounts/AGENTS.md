# antigravity-accounts

本目录先按 [../AGENTS.md](../AGENTS.md) 走：Read first、Workflow and authorization、Development
principles、Validation and Git、Task closeout 全部适用。下面只写这个模块额外需要知道的。

## 模块事实

- 零第三方依赖，仅用标准库（`urllib` 调 Google OAuth token/userinfo），`python3` 3.11+。
- 入口 [`agy_account.py`](agy_account.py)（符号链接 `agy-account`），支持 `--surface chat|cli`（默认 `chat`），子命令 `save` / `list` / `current` / `use` / `verify`。
- 自检 [`test_agy_account.py`](test_agy_account.py)（assert 风格，不用框架），改完跑：`python3 antigravity-accounts/test_agy_account.py`。

## 改这个脚本前必须知道的

- 本模块按 `--surface` 严格隔离两套凭据：
  - `chat`（默认）：`$GEMINI_HOME/antigravity-acp/acp_token.json`（默认 `~/.config/orca/antigravity-chat`）；不要碰 `settings.json`。
  - `cli`：`~/.gemini/antigravity-cli/antigravity-oauth-token`（可由 `--home` / `AGY_CLI_DIR` 覆盖）；**绝不要在 `cli` 路径读取 `GEMINI_HOME`**（Orca Chat 会话会注入 `GEMINI_HOME`），也不要修改 `~/.config/antigravity/credential-user-scope`（属于 `authstore` 插件/MCP）或 `antigravity_state.pbtxt` / `cache/`。
- **严禁输出任何 token 明文**（`refresh_token`、`client_secret`、`access_token`、`id_token`）；日志与报错只允许出现 label、指纹前 8 位、`project_id`、`auth_method`、邮箱、字节数、时间戳。
- `use` 的安全闸不得绕过或加入静默强杀逻辑：
  - `chat` 检查目标 `GEMINI_HOME` 下的 live `agy_acp_server.par` 进程。（闸门只为防止活进程在内存里缓存旧凭据、刷新时整文件回写覆盖切换结果，与“能否继续原会话”无关：切完直接在原会话继续即可，Orca 会新拉进程读新凭据。）
  - `cli` 检查**真实探测**的 Secret Service 结论（`gdbus call … NameHasOwner org.freedesktop.secrets`，回退 `busctl --user --no-pager list`，每条 `timeout 5s`）以及目标 `HOME` 下的 live `agy` 进程（仅排除 `agy_acp_server*` 与 `agy remote-control`）。
  - Secret Service 门禁默认 fail-closed：只有 `absent` 放行；`present`（密钥库可能接管凭据）与 `unknown`（探测失败）都拒绝，退出码 `2`；唯一显式放行开关是 `--assume-file-store`（必须打印风险警告）。探测逻辑保持 `probe_secret_service(runner=…)` 可注入，方便自检模拟三种结论。
  - `cache/antigravity-keyring-unavailable` 是 **Orca daemon 的标记**，不是 `agy` 写的，禁止再把它当门禁（历史死条件已删除；真机 `agy` 二进制里也没有这个字符串）。
- **禁止把 `use --surface cli` 改回默认放行**：主人 2026-11-22 决定 CLI 账号切换在 Orca UI 里做，脚本侧默认拒绝（退出码 `2`，与活进程阻塞同口径），只有 `--allow-cli-use` 显式放行，且放行前必须先停 `antigravity-cli-daemon.service`（否则 `agy remote-control serve` 会把旧凭据写回）。新需求默认走 Orca UI。
- 账号库按表面分目录 `<vault>/<surface>/<label>/`（并保持对旧版 Chat 扁平目录 `<vault>/<label>/acp_token.json` 的只读识别与 `save --force` 迁移）。替换前必须把现有 live 文件备份到 `<vault>/<surface>/_backup/<timestamp>-<label|unknown>/`，再用同目录临时文件 + `os.replace` 原子替换并保持 `0600`/`0700` 权限，随后回读校验指纹。
- 自检必须全部在 `/tmp` 沙箱里跑（注入 `HOME`、`GEMINI_HOME`、`AGY_ACCOUNTS_DIR`、`AGY_TOKEN_URI`、`AGY_USERINFO_URL`），**绝不在测试里读写真实 `~/.gemini/...`、`~/.config/antigravity/...`、`~/.config/orca/...` 或真实账号库**。
