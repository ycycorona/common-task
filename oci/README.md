# oci/ —— Oracle Cloud 账户与资源治理

用自己的 OCI tenancy 做日常管理：实例（free-arm / orc-1 及其他 VPS）、VCN 与安全列表、
块存储与备份、Object Storage 桶、limits 与费用。**账户现状的单一事实源不在本仓库**，在
`../../tech_note_ai/30-Knowledge/Infra/oci-free-vcn-实例与SSH访问.md`；这里只放操作脚本与凭据约定。

Last Validated: 2026-11-16（PAYG 生效、当月花费与 A1 免费额度的观测结论见下）

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

## 账户状态（2026-11-16 实测）

| 项 | 值 |
| :--- | :--- |
| payment-model | `PAYG`（`FREE_TRIAL` → 升级生效于 `time-updated: 2026-11-15T22:12:59Z`） |
| 计费起点 | `end-date: 2026-11-20T23:59:59Z` —— credit 到这天为止，之后按卡扣 |
| 花费 | 10 月 €19.70 + 11 月至今 €34.30 ≈ €54（占 $300 credit 的 ~21%，其余 11-20 作废） |
| 日耗稳定值 | €2.18094 = `orc-1`（E5.Flex）€2.05344 + `mem-e5` 100 GB 引导卷 €0.1275 |
| 全免项 | `free-arm`（A1 4 OCPU/24 GB）及其 200 GB 卷、VCN、Object Storage、Telemetry |

### 存储类免费额度与现状（2026-11-16）

Oracle 官方数字（`Always Free Resources` 页，2026-11-16 抓取）：

| 资源 | Always Free 额度 | 现状 | 判定 |
| :--- | :--- | :--- | :--- |
| 块存储（boot + block **合计**） | 200 GB | 300 GB：`free-arm` 200 + `mem-e5` 100 | 超 100 GB = €0.1275/天；删掉 `mem-e5` 后正好 200 |
| 卷备份（boot + block 合计，限 home region） | **5 个** | 0 个 | 5 个空位，第 6 个会被 API 拒绝 |
| 对象存储（PAYG / 有 credit 的账户） | Standard 10 GB + Infrequent 10 GB + Archive 10 GB，50,000 请求/月 | 桶 `yorches-spec006-t7` 内 14 个对象 / **13.97 MB**（Standard） | 用了 0.14% |
| MySQL HeatWave（Always Free DB system） | 50 GB 数据 + 50 GB 备份 | 无 DBaaS 实例 | 无关 |

两点容易混淆：

- 应用自己传到桶里的数据库 dump（`yorches-spec006-t7/daily/*.dump`）**不占**「5 个卷备份」名额 ——
  那 5 个只数 `oci bv backup` / `boot-volume-backup`。应用级备份只吃对象存储的 10/10/10 GB。
- 试用期账户可以无限量存对象、只免费前 20 GB；试用结束后若对象超过 20 GB，**全部对象被删**。
  这里只有 14 MB，不构成风险。

### A1 免费额度：观测值指向 4 OCPU/24 GB

官方文档口径是「所有 tenancy 每月 1,500 OCPU-h + 9,000 GB-h，Always Free tenancy 等价 2 OCPU/12 GB」；
社区口径说 PAYG 是 3,000 OCPU-h + 18,000 GB-h（≈4/24）。用成本数据可以分辨：

- `free-arm` 4 OCPU/24 GB 自 2026-10-22 起跑，按资源查 `usage-api` 的 COST，**每天都是 0.000000**
  （同期 `orc-1` 每天 2.053440，对照组有效）。
- 若额度是 1,500 OCPU-h（4 OCPU 每天 96 OCPU-h）：按自然月算第 15.6 天、即 **11-16 前后**该开始计费；
  按滚动 30 天算更早（**11-06 前后**）。两个口径都过了，11-16 仍是 0。
- 3,000 OCPU-h 口径下 4 OCPU 跑满 30 天 = 2,920 OCPU-h、24 GB × 730 h = 17,520 GB-h，都刚好在额度内 —— 与观测一致。

结论（未定稿）：**升级 PAYG 后是 4 OCPU/24 GB 全免**。留两个观察点收口：11-17/11-18 的日成本仍为 0，
以及 11-21 起真实计费窗口里 `free-arm` 仍为 0。

### Budget 告警（2026-11-16 重建）

| 项 | 值 |
| :--- | :--- |
| budget | `payg-monthly-150`，`amount: 150`（仅作参考，阈值是绝对金额），`processing-period-type: MONTH`，`reset-period: MONTHLY`，**无到期日** |
| 告警 | `actual-1`、`actual-5`、`actual-25`，均为 `ABSOLUTE` + `ACTUAL`，收件人 `ycy421@gmail.com` |
| 语义 | 月度预算 + 绝对阈值 → **每月**累计花费跨过阈值时发**一封**，不是每笔都发；评估按天，不是实时 |

**未确认**：budgets 是否把「被 credit 覆盖的用量」算进 actual-spend。Oracle 的 Budgets 文档里 `credit` 出现 0 次；旧的 `name=1` 预算存在 25 天、同期 usage-api 记了 €54，但 `actual-spend` 一直是 `null` —— 与「按 usage 计」矛盾，也支持「只算真扣费」。
决定性测试：2026-11-17 查新预算的 `actual-spend` —— 若 ≈€34 说明计入（旧预算是自己配坏了），若仍 `null` 则说明 PAYG 下的 budget 要等 2026-11-21 之后才可能触发。在那之前别把它当兜底，改用 `usage-api` 的日成本判断「有没有产生费用」。

```sh
T=<tenancy ocid>
oci budgets budget budget list --compartment-id $T --all --output table \
  --query "data[].{name:\"display-name\",amount:amount,period:\"processing-period-type\",end:end_date}"
oci budgets budget alert-rule list --budget-id <budget-id> --output table
```

- 命令是三层嵌套：`oci budgets budget budget …` / `oci budgets budget alert-rule …`（CLI 3.96.2 如此）。
- **同一个 target compartment 只允许一个 budget**：直接再建会报
  `N budgets already exist in target compartment`。旧的那个（名 `1`、`SINGLE_USE`、2026-11-22 到期、
  阈值 €25/100/200/240 + forecast €250）已删除后才建成新的。
- `--recipients` 用裸值（`--recipients ycy421@gmail.com`）；写成 `'["…"]'` 会报 `Invalid recipients`。
- `--targets` 是 `list[str]`，写对象数组会让 CLI 报 `Unable to process JSON input`；
  正确形状：`--targetType COMPARTMENT --targets '["<tenancy-ocid>"]'`。
- 阈值是**绝对金额**（`--threshold-type ABSOLUTE`），要按百分比就换 `PERCENTAGE` + `--threshold 50`。

## 被推翻的方案（2026-11-15）## 被推翻的方案（2026-11-15）

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
