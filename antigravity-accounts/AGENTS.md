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
  - `chat` 检查目标 `GEMINI_HOME` 下的 live `agy_acp_server.par` 进程。
  - `cli` 检查活跃 OS keyring（macOS Keychain / Linux `org.freedesktop.secrets`）以及目标 `HOME` 下的 live `agy` 进程（仅排除 `agy_acp_server*` 与 `agy remote-control`；无法确定时宁可拒绝）。
- 账号库按表面分目录 `<vault>/<surface>/<label>/`（并保持对旧版 Chat 扁平目录 `<vault>/<label>/acp_token.json` 的只读识别与 `save --force` 迁移）。替换前必须把现有 live 文件备份到 `<vault>/<surface>/_backup/<timestamp>-<label|unknown>/`，再用同目录临时文件 + `os.replace` 原子替换并保持 `0600`/`0700` 权限，随后回读校验指纹。
- 自检必须全部在 `/tmp` 沙箱里跑（注入 `HOME`、`GEMINI_HOME`、`AGY_ACCOUNTS_DIR`、`AGY_TOKEN_URI`、`AGY_USERINFO_URL`），**绝不在测试里读写真实 `~/.gemini/...`、`~/.config/antigravity/...`、`~/.config/orca/...` 或真实账号库**。
