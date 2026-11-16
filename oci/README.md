# oci/ —— Oracle Cloud 账户与资源治理

用自己的 OCI tenancy 做日常管理：实例（free-arm / orc-1 及其他 VPS）、VCN 与安全列表、
块存储与备份、Object Storage 桶、limits 与费用。**账户现状的单一事实源不在本仓库**，在
`../../tech_note_ai/30-Knowledge/Infra/oci-free-vcn-实例与SSH访问.md`；这里只放操作脚本与凭据约定。

Last Validated: 2026-11-15（session 授权跑通：`oci iam region list`、`oci os ns get`、实例与桶清单）

## 凭据：浏览器 session token（2026-11-15 定）

不留长期 API key。凭据是浏览器登录换来的 **session token**，默认 60 分钟有效（上限也是 60，
`--session-expiration-in-minutes` 范围 5–60，写在 `oci_cli/cli_session.py` 里）。

| 路径 | 说明 |
| :--- | :--- |
| `~/.oci/config` | `600`；profile `DEFAULT`，含 `security_token_file` 与 `key_file` |
| `~/.oci/sessions/DEFAULT/` | `700`；`token`、`oci_api_key.pem`、`oci_api_key_public.pem` |

两者都由 `oci session authenticate` 生成，**每次重新授权都会覆盖**，不要手工往 config 里加字段。
私钥与 token 不入 Git（`oci/.gitignore` 忽略 `*.pem`）。tenancy / compartment OCID、region、
fingerprint、Object Storage namespace **不是机密**，可以写进文档。

### 首次授权 / 续期（用 `login.sh`）

CLI 拿 token 的方式是：浏览器登录后重定向到**跑 CLI 那台机器**的 `http://localhost:8181`。
所以要么先开隧道，要么把回调地址粘回来 —— `login.sh` 两种都支持，端口改不了（CLI 里写死的）。

```sh
oci/login.sh                     # 会话有效就打印到期时间；否则起授权并打印登录链接
oci/login.sh 'http://localhost:8181/#security_token=...'   # 没隧道时：把地址栏那条整条粘回来
```

没隧道时浏览器会停在 “This site can't be reached”，这不影响结果：地址栏里就带着
`#security_token=...`，复制整条交给第二个用法，回调就被送进本机等待中的授权进程。
有隧道（Mac 上 `ssh -L 8181:127.0.0.1:8181 free-arm`）则自动完成，重跑 `oci/login.sh` 看到期时间。

profile 名固定为 `session`：`~/.bashrc` 里 `OCI_CLI_AUTH=security_token` 与 `OCI_CLI_PROFILE=session`
都已导出，日常直接 `oci …`。别用 `oci session authenticate` 裸跑 —— `~/.oci/config` 已存在时它会追问
profile 名，输 `DEFAULT` 会因与旧段冲突而无限重问。

token 默认 60 分钟有效（上限也是 60，`--session-expiration-in-minutes` 范围 5–60）；过期前
`oci session refresh` 可续（**未验证**能续几次），过期只能重跑 `login.sh`。

### 调用时必须带 auth 类型

config 里没有 `user` 字段，不带 auth 类型会报 `The config file at ~/.oci/config is invalid: user missing`。
`~/.bashrc` 已经导出了 `OCI_CLI_AUTH=security_token` 与 `OCI_CLI_PROFILE=session`，所以登录 shell 里
`oci …` 直接可用；在脚本或非登录 shell 里要自己带上两者。

### 已核对的只读事实（2026-11-15）

| 项 | 值 |
| :--- | :--- |
| region | `eu-amsterdam-1` |
| tenancy / 根 compartment | `ocid1.tenancy.oc1..aaaaaaaahrfdu7vevsnsmfj4d6r7cup532kasynxjhbwug5edqajzvmcnqta` |
| Object Storage namespace | `axh5gtcz4wbl` |
| 实例 | `free-arm`（A1.Flex, RUNNING）、`orc-1`（E5.Flex, RUNNING） |
| 根 compartment 的桶 | `yorches-spec006-t7`（2026-11-11 创建） |

```sh
export OCI_CLI_AUTH=security_token
oci iam region list
oci os ns get
oci compute instance list -c <tenancy-ocid> --all --output table \
  --query "data[].{name:\"display-name\",state:\"lifecycle-state\",shape:shape}"
oci os bucket list -ns axh5gtcz4wbl -c <tenancy-ocid> --output table
```

## 被推翻的方案（2026-11-15）

- **A. 复制 `box` 上那把 key**：`box` 是 grok bot 的机器、已停用，`ssh box` 在 free-arm 上直接解析失败。
- **B. 新建专用 IAM 用户 + 长期 key**：可单独吊销、能收窄 policy，但用户选了不留长期密钥。
- **C. 只要只读 profile**：够盘点不够治理，之后还得再走一遍加写权。
- 代价留在这里：session token 60 分钟上限，**过期后必须有人重新登录**，Agent 不能无人值守跑 OCI 命令。

## 目录

| 路径 | 作用 |
| :--- | :--- |
| `README.md` | 本节 + 凭据约定 |
| `.gitignore` | 只对本目录忽略 `*.pem` |
| `login.sh` | 会话登录：起授权打印链接 / 粘回调地址 / 打印到期时间 |

脚本按需新增（如清单、桶巡检），单文件用法写脚本头部注释，不预先建空目录。
