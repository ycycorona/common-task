# antigravity-accounts

Orca **Chat 模式**与 **`agy` CLI** 的 AntiGravity 账号凭据保存、查看、校验与安全切换工具（`--surface chat|cli`）。零第三方依赖，仅用 Python 3.11+ 标准库。

## 两套表面与凭据差异

| 维度 | `chat`（默认，`--surface chat`） | `cli`（`--surface cli`） |
| --- | --- | --- |
| Live 凭据文件 | `$GEMINI_HOME/antigravity-acp/acp_token.json`（默认 `~/.config/orca/antigravity-chat/...`） | `~/.gemini/antigravity-cli/antigravity-oauth-token`（忽略 `GEMINI_HOME`；可用 `--home` / `AGY_CLI_DIR` 覆盖） |
| JSON 格式 | Google `authorized_user`（`client_id` / `client_secret` / `refresh_token` / `token_uri` / 可选 `project_id`，无邮箱字段） | `agy` `StoredToken`（`token.refresh_token` / `token.access_token` / `auth_method` / 可选 `id_token_email`） |
| Keyring 行为 | 非 macOS 上强制文件后端，不走 keyring | `agy` 优先尝试 keyring，无 `org.freedesktop.secrets` 时回落到文件；若检测到活跃 keyring 则 `use` 拒绝切换 |
| 不需切换的文件 | `settings.json` | `~/.config/antigravity/credential-user-scope`（仅 `authstore` 插件/MCP 隔离用）、`antigravity_state.pbtxt`、`cache/`、`last_check.timestamp` |
| `use` 阻塞检查 | 目标 `GEMINI_HOME` 下正在运行的 `agy_acp_server.par` | 目标 `HOME` 下正在运行的 `agy` CLI（排除 `agy_acp_server*` 与 `agy remote-control`） |
| 生效方式 | 重开 Antigravity Chat 会话（不需重启 Orca） | 重开 `agy` CLI 会话（不影响 `agy remote-control serve` 与 Chat） |

> [!NOTE]
> 为什么需要它：Orca 原生 `orca account` 只管 CLI 表面且在裸金属 Linux（非 SSH/WSL）上会直接拒绝切换；Chat 表面只有登录流程、没有切换已保存账号的入口。`--surface cli` 实测确认在无 Secret Service 的 Linux 宿主上，替换 `antigravity-oauth-token` 单文件即可完成账号切换。

## 账号库布局与旧版兼容

默认账号库 `~/.config/antigravity-accounts/`（目录 `0700`、文件 `0600`，可用 `--vault` 或 `AGY_ACCOUNTS_DIR` 覆盖）：

```text
~/.config/antigravity-accounts/
  chat/
    <label>/
      acp_token.json                # 原样字节拷贝（0600）
      meta.json                     # surface / saved_at / project_id / sha256(refresh_token) / email / note
    _backup/
      <timestamp>-<label|unknown>/  # chat 切换前备份
  cli/
    <label>/
      antigravity-oauth-token       # 原样字节拷贝（0600）
      meta.json                     # surface / auth_method / saved_at / fingerprint / email / note
    _backup/
      <timestamp>-<label|unknown>/  # cli 切换前备份
```

**旧版 Chat 布局兼容**：早期版本保存在 `~/.config/antigravity-accounts/<label>/acp_token.json` 的账号仍可被 `list` / `current` / `use` / `verify` 直接识别（`list` 中标为 `legacy`）；对旧账号执行 `save <label> --force` 会自动将其迁移至 `chat/<label>/` 并保留原有的 `email` / `note` / `verified_at`。

> [!IMPORTANT]
> Chat 凭据无邮箱字段，CLI 的 `id_token_email` 也可能缺省或陈旧，且不同账号可能共用 `project_id`。脚本统一使用 `sha256(refresh_token)` 指纹区分凭据，并用 `verify` 查 Google `userinfo` 核对真实邮箱。

## 用法

```bash
# 直接运行（也可把 antigravity-accounts/agy-account 链到 PATH）
# --surface 可放在子命令前或后，默认 chat
python3 antigravity-accounts/agy_account.py [--surface chat|cli] <subcommand>
```

| 子命令 | 说明 |
| --- | --- |
| `save <label> [--surface chat\|cli] [--note TEXT] [--email EMAIL] [--force]` | 保存当前 live 凭据到 `<vault>/<surface>/<label>/`；已有同名 label 或相同指纹时需 `--force` |
| `list [--surface chat\|cli] [--json]` | 列出指定表面已保存账号，标出当前 live 账号与 `legacy`/`stale` 状态 |
| `current [--surface chat\|cli] [--json]` | 只读报告当前表面 live 凭据指纹、所属 label 与运行中的会话数量 |
| `use <label> [--surface chat\|cli] [--dry-run] [--json]` | 切换指定表面 live 凭据：先检查 live 会话（CLI 额外检查 keyring），再备份到 `_backup/`，最后原子替换并回读校验 |
| `verify [label] [--surface chat\|cli] [--update-email] [--json]` | 用 `refresh_token` 换 access token 并查 Google `userinfo` 校验邮箱（**零模型额度**），不传 label 时校验当前 live 文件 |

## 安全闸、回滚与红线

1. **进程阻塞检查（不自动强杀）**：
   - **Chat**：运行中的 `agy_acp_server.par` 在内存缓存凭据并在刷新时整文件写回 `acp_token.json`；检测到同 `GEMINI_HOME` 活进程即拒绝（退出码 `2`）。
   - **CLI**：`agy` 交互会话在内存持有凭据并在刷新时覆盖写回 `antigravity-oauth-token`；检测到同 `HOME` 的 `agy` 进程即拒绝（退出码 `2`），但显式排除 `agy_acp_server*` 与 `agy remote-control`。当命令行参数或 `HOME` 不可判定时宁可拒绝（防误切）。
   - **CLI Keyring 门禁**：若检测到系统存在活跃 keyring（macOS Keychain 无 SSH/unavailable 标记，或 Linux D-Bus `org.freedesktop.secrets` 有 owner），`use --surface cli` 拒绝仅替换文件（退出码 `2`），避免表里不一致。
2. **原子替换与自动/手动回滚**：
   - `use` 写入目标目录 `.tmp` 后 `fsync` + `os.replace`，并回读比较字节与指纹；若校验异常则自动用切前内容回滚。
   - 如需手动回滚，从 `<vault>/<surface>/_backup/<timestamp>-<label|unknown>/` 取回对应凭据文件覆盖 live 路径即可（或先将备份再切换回原 `<label>`）。
3. **不泄露凭据明文**：
   - 任何命令输出与报错均**不打印** `refresh_token`、`client_secret`、`access_token` 明文，只展示 label、指纹前 8 位、`project_id`/`auth_method`、邮箱、字节数与时间戳。
   - `verify` 报 `invalid_grant`（`[stale]`，退出码 `3`）时，需通过 Orca 或 `agy auth login` 重新登录后再 `save --force`。

## 自检

```bash
python3 antigravity-accounts/test_agy_account.py
```

所有自检均在 `/tmp` 临时目录构造假 token 与假 home（注入 `HOME`、`GEMINI_HOME`、`AGY_ACCOUNTS_DIR`），并用本地回环 HTTP 服务（`AGY_TOKEN_URI`、`AGY_USERINFO_URL`）与合成 `/proc` 目录验证 Chat、旧布局兼容与 CLI 全流程，不会读取或修改真实 `~/.gemini/**`、`~/.config/antigravity/**`、`~/.config/orca/**` 或真实账号库。
