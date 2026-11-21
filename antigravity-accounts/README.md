# antigravity-accounts

Orca **Chat 模式** AntiGravity 账号凭据（`acp_token.json`）的保存、查看、校验与快速切换工具。零第三方依赖，仅用 Python 3.11+ 标准库。

## 为什么需要它

- Orca 原生账号库（`orca account`）只管 **CLI 表面**（`~/.gemini/antigravity-cli/antigravity-oauth-token`），且在裸金属 Linux 上切 CLI 会报错；Chat 表面只有 sign-in，没有"选择已保存账号"接口。
- Chat 凭据是单文件：`$GEMINI_HOME/antigravity-acp/acp_token.json`（Orca 默认设 `GEMINI_HOME=~/.config/orca/antigravity-chat`），非 macOS 上强制文件后端，不走 keyring，也不碰 `settings.json`。

## 凭据与账号库布局

默认账号库 `~/.config/antigravity-accounts/`（目录 `0700`、文件 `0600`，可用 `--vault` 或 `AGY_ACCOUNTS_DIR` 覆盖）：

```text
~/.config/antigravity-accounts/
  <label>/
    acp_token.json                  # 原样字节拷贝（0600）
    meta.json                       # saved_at / project_id / sha256(refresh_token) 指纹 / email / note
  _backup/
    <timestamp>-<label|unknown>/    # 每次 use 切换前备份的当前 live 文件
      acp_token.json
      meta.json
```

> [!IMPORTANT]
> `acp_token.json` 是 Google `authorized_user` 格式，**没有邮箱字段**，且不同账号可能共用同一个 `project_id`（如 `aicode-consumers`）。脚本使用 `sha256(refresh_token)` 指纹区分凭据，并通过 `verify` 查 `userinfo` 获取邮箱。

## 用法

```bash
# 直接运行（也可把 antigravity-accounts/agy-account 链到 PATH）
python3 antigravity-accounts/agy_account.py <subcommand>
```

| 子命令 | 说明 |
| --- | --- |
| `save <label> [--note TEXT] [--email EMAIL] [--force]` | 保存当前 live `acp_token.json` 到账号库；已有同名 label 或相同指纹时需 `--force` |
| `list [--json]` | 列出所有已保存账号（label、指纹前 8 位、project_id、邮箱、保存时间），标出当前账号 |
| `current [--json]` | 只读报告当前 live `acp_token.json` 的指纹、所属 label 与运行中的 Chat 会话 |
| `use <label> [--dry-run] [--json]` | 切换 live `acp_token.json`：先检测 live 会话、再备份到 `_backup/`、最后原子替换并回读校验 |
| `verify [label] [--update-email] [--json]` | 用 `refresh_token` 换 access token 并调用 Google `userinfo` 校验邮箱（**零模型额度**），不传 label 时校验当前 live 文件 |

## 切换前为什么要先关掉 Chat 会话

运行中的 `agy_acp_server.par`（一 Chat 会话一进程）在启动时把凭据缓存在内存里，access token 过期刷新后会**整文件覆盖写回** `acp_token.json`。如果不先关闭它们：

1. 已打开的会话仍会继续使用旧账号；
2. 它们刷新 token 时会把旧账号重新写回磁盘，覆盖本次切换。

因此 `use` 检测到相同 `GEMINI_HOME` 下还有 `agy_acp_server.par` 进程时**直接拒绝切换**，并列出 PID、Orca 会话 ID 与工作目录，提示用户在 Orca 界面中先关掉这些 Antigravity Chat 会话。脚本刻意**不提供自动强杀**（避免绕过 Orca 的会话生命周期记账与丢失进行中的工具调用）。

切换后无需重启 Orca，只需重新打开 Antigravity Chat 会话即可生效。

## 安全与红线

- 任何命令输出（包括报错和 `verify`）均**不打印** `refresh_token`、`client_secret`、`access_token` 明文，只展示 label、指纹前 8 位、`project_id`、邮箱、字节数与时间戳。
- 首次登录或 `verify` 报 `invalid_grant`（`[stale]`）时，仍需在 Orca Settings 里走浏览器登录，之后执行 `save <label> --force` 更新账号库。
- 同一 `GEMINI_HOME` 下的会话历史（`brain/`、`conversations/`）在切换账号后仍保留在磁盘列表中。

## 自检

```bash
python3 antigravity-accounts/test_agy_account.py
```

所有自检均在 `/tmp` 临时目录构造假 token 与假 home（注入 `GEMINI_HOME` 与 `AGY_ACCOUNTS_DIR`），并用本地回环 HTTP 服务模拟 OAuth refresh / userinfo，不会读取或修改真实 `~/.config/orca/...` 与 `~/.config/antigravity-accounts/`。
