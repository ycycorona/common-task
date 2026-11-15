# oci/ —— Oracle Cloud 账户与资源治理

用自己的 OCI tenancy 做日常管理：实例（free-arm / orc-1 及其他 VPS）、VCN 与安全列表、
块存储与备份、Object Storage 桶、limits 与费用。**账户现状的单一事实源不在本仓库**，在
`../../tech_note_ai/30-Knowledge/Infra/oci-free-vcn-实例与SSH访问.md`；这里只放操作脚本与本机配置。

Last Validated: 2026-11-14（目录建立，未跑过任何 OCI 调用）

## 凭据在哪（不进仓库）

OCI 的权限粒度是 IAM **用户**，不是「账户 API key」。标准做法是 user principal 的 API 密钥对，
`oci` CLI 与各语言 SDK 都读同一个位置：

| 路径 | 权限 |
| :--- | :--- |
| `~/.oci/config` | `600`；profile 段（`DEFAULT` / 只读 profile 等） |
| `~/.oci/keys/<key>.pem` | `600`，config 里的 `key_file` 指向它 |

`oci/config.example` 是模板，只含字段名与占位符；真实 config、私钥、以及任何 `*.pem` 都不入 Git
（见根 `.gitignore`）。tenancy / compartment OCID、region、fingerprint **不是机密**，可以写进
`oci/` 里的文档，但不要顺手贴私钥或 customer secret key。

现有一把可用的 user 主密钥在 `box:~/oci/config`（2026-10-29 记录，配 Python SDK venv），
free-arm 上目前**没有** `oci` CLI，也没有 `~/.oci`。

## 凭据方案（待定，选定后改写本节）

| 方案 | 能做什么 | 代价 |
| :--- | :--- | :--- |
| A. 把 `box` 现有 key 复制到 free-arm `~/.oci` | 立刻可用，保留 IAM 身份不变 | 又一台机器持有 tenancy 级密钥；无法单独吊销 free-arm |
| B. 新建专用 IAM 用户（如 `agent-ops`）+ 自己的 API key，只部署在 free-arm | 出问题吊销这一把，不动你主用户；策略可收窄到实例/网络/桶 | 要在控制台建用户、写 policy（一次） |
| C. `oci session authenticate`（浏览器 token） | 不留长期密钥 | 1 小时过期，Agent 无法自己续 |

## 目录

| 路径 | 作用 |
| :--- | :--- |
| `README.md` | 本节 + 凭据约定 |
| `config.example` | `~/.oci/config` 模板，不含真实值 |

脚本按需新增（如清单、桶巡检），单文件用法写脚本头部注释，不预先建空目录。
