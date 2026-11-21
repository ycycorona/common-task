# antigravity-accounts

本目录先按 [../AGENTS.md](../AGENTS.md) 走：Read first、Workflow and authorization、Development
principles、Validation and Git、Task closeout 全部适用。下面只写这个模块额外需要知道的。

## 模块事实

- 零第三方依赖，仅用标准库（`urllib` 调 Google OAuth token/userinfo），`python3` 3.11+。
- 入口 [`agy_account.py`](agy_account.py)（符号链接 `agy-account`），子命令 `save` / `list` / `current` / `use` / `verify`。
- 自检 [`test_agy_account.py`](test_agy_account.py)（assert 风格，不用框架），改完跑：`python3 antigravity-accounts/test_agy_account.py`。

## 改这个脚本前必须知道的

- 本模块只管 Orca **Chat 表面**的 `$GEMINI_HOME/antigravity-acp/acp_token.json`（默认 `~/.config/orca/antigravity-chat`）。不要去碰 `settings.json`、`~/.gemini/antigravity-cli/antigravity-oauth-token`（CLI 表面）或 `~/.config/antigravity/credential-user-scope`（keyring scope）。
- **严禁输出任何 token 明文**（`refresh_token`、`client_secret`、`access_token`）；日志与报错只允许出现 label、指纹前 8 位、`project_id`、邮箱、字节数、时间戳。
- `use` 必须先检查目标 `GEMINI_HOME` 下是否存在 live `agy_acp_server.par` 进程，有则拒绝切换；不要加入静默强杀逻辑。
- 替换前必须把现有 live 文件备份到 `<vault>/_backup/<timestamp>-<label|unknown>/`（不可覆盖已有备份或用户保存的账号），再用同目录临时文件 + `os.replace` 原子替换并保持 `0600`/`0700` 权限，随后回读校验指纹。
- 自检必须全部在 `/tmp` 沙箱里跑（注入 `GEMINI_HOME`、`AGY_ACCOUNTS_DIR`、`HOME`），**绝不在测试里读写真实 `~/.config/orca/...` 或真实账号库**。
