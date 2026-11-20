# oci/ —— Oracle Cloud 账户与资源治理

用自己的 OCI tenancy 做日常管理：实例（free-arm / orc-1 及其他 VPS）、VCN 与安全列表、
块存储与备份、Object Storage 桶、limits 与费用。**账户现状的单一事实源不在本仓库**，在
`../../tech_note_ai/30-Knowledge/Infra/oci-free-vcn-实例与SSH访问.md`；这里只放操作脚本与凭据约定。

Last Validated: 2026-11-16（PAYG 生效、当月花费、A1 额度两处官方口径与自证用量、存储类额度与首份卷备份）

## 凭据：浏览器 session token（2026-11-15 定）

不留长期 API key。凭据是浏览器登录换来的 **session token**，默认 60 分钟有效（上限也是 60，
`--session-expiration-in-minutes` 范围 5–60，写在 `oci_cli/cli_session.py` 里）。

| 路径 | 权限 | 说明 |
| :--- | :--- | :--- |
| `~/.oci/` | `700` | 上级目录也要收：`oci session authenticate` 生成时是 `775`，其他人能列目录名（`config`/`token` 本身 600 不泄露内容，但目录不该开） |
| `~/.oci/sessions/` | `700` | 同上，同样是 `775` 出生 |
| `~/.oci/config` | `600` | profile **`session`**，含 `security_token_file` 与 `key_file` |
| `~/.oci/sessions/session/` | `700` | `token`、`oci_api_key.pem`、`oci_api_key_public.pem` |

重新授权后如果目录又变回 `775`，收一次即可（不影响 CLI）：

```sh
chmod 700 ~/.oci ~/.oci/sessions && stat -c '%A %n' ~/.oci ~/.oci/sessions
oci iam region list >/dev/null && echo api-ok      # 确认收权限后 token 仍可读
```

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
| 实例 | `free-arm`（A1.Flex, RUNNING）；`orc-1`（E5.Flex）已于 **2026-11-18 10:1x UTC 终止**，引导卷 `mem-e5` 一并删除（`--preserve-boot-volume false`） |
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
| 日耗稳定值 | **关停前**：€2.18094 = `orc-1`（E5.Flex）€2.05344 + `mem-e5` 100 GB 卷 €0.1275；**2026-11-18（删机当天）实测 €0.93645** = E5 €0.57497 + Memory €0.30665 + Block €0.03290 + Perf €0.02193，即整日 €2.18094 的 43%（= 10h18m / 24h，与 10:16–10:19 终止时刻一致）；**2026-11-19 整天实测 €0.00000**（桶 `2026-11-20`：`Standard - E5`/`E5 - Memory`/`Block Volume - Storage`/`Performance Units` 全部缺席，合计 €0；同日 `A1 = 96 OCPU-h`、`Block Volume - Free = 6.4516` 证明已满 24 h）|
| 全免项 | `free-arm`（A1 4 OCPU/24 GB）及其 200 GB 卷、VCN、Object Storage、Telemetry |

### 存储类免费额度与现状（2026-11-16）

Oracle 官方数字（`Always Free Resources` 页，2026-11-16 抓取）：

| 资源 | Always Free 额度 | 现状 | 判定 |
| :--- | :--- | :--- | :--- |
| 块存储（boot + block **合计**） | 200 GB | **200 GB**：`free-arm` 200（`mem-e5` 已于 2026-11-18 随实例删除，此前是 300） | 现正好满额、无超额计费 |
| 卷备份（boot + block 合计，限 home region） | **5 个**（`oci limits value list --service-name block-storage` → `free-backup-count = 5`，与 `total-free-storage-gb = 200` 是**两个独立字段**） | 1 个：`free-arm-baseline-20261116`（200 GB，FULL，AVAILABLE） | 用 1/5。备份按**个数**计，不按 GB；200 GB 全量备份不占那 200 GB 卷额度 |
| 对象存储（PAYG / 有 credit 的账户） | Standard 10 GB + Infrequent 10 GB + Archive 10 GB，50,000 请求/月 | 桶 `yorches-spec006-t7` 内 14 个对象 / **13.97 MB**（Standard） | 用了 0.14% |
| MySQL HeatWave（Always Free DB system） | 50 GB 数据 + 50 GB 备份 | 无 DBaaS 实例 | 无关 |

两点容易混淆：

- 应用自己传到桶里的数据库 dump（`yorches-spec006-t7/daily/*.dump`）**不占**「5 个卷备份」名额 ——
  那 5 个只数 `oci bv backup` / `boot-volume-backup`。应用级备份只吃对象存储的 10/10/10 GB。
- 试用期账户可以无限量存对象、只免费前 20 GB；试用结束后若对象超过 20 GB，**全部对象被删**。
  这里只有 14 MB，不构成风险。

### A1 免费额度：PAYG 是 3,000 OCPU-h / 18,000 GB-h（=4 OCPU/24 GB）

**两处官方口径，按账户类型分工，不矛盾**（2026-11-16 抓取）：

| 来源 | 原文 | 适用 |
| :--- | :--- | :--- |
| `docs.oracle.com/…/Always_Free_Resources.htm` | `All tenancies get the first 1,500 OCPU hours and 9,000 GB hours per month` + `For Always Free tenancies, this is equivalent to 2 OCPUs and 12 GB of memory.` | Always Free-only tenancy（未升级） |
| `oracle.com/cloud/price-list/` | `Each paid tenancy gets the first 3,000 OCPU hours and 18,000 GB hours per month for free to create Ampere A1 Compute instances.` | 已升级的付费 tenancy（PAYG） |

**自证用量**（`usage-api --query-type USAGE --group-by '["skuName"]'`，2026-11-01 → 11-16）：

| SKU | 用量 | 对照 |
| :--- | :--- | :--- |
| `Standard - A1` | **1,510 OCPU Per Hour** | 4 OCPU × 15.7 天 = 1,509，吻合 |
| `Standard - A1 - Memory` | **9,060 Gigabyte Per Hour** | 24 GB × 15.7 天 = 9,063，吻合 |

1,510 / 3,000 ≈ 50.3%、9,060 / 18,000 ≈ 50.3% —— 半个月刚好用掉付费额度的一半，且按资源查 COST 每天仍是 `0.000000`。
若额度是 1,500/9,000，此刻已越线（10 OCPU-h、60 GB-h），成本数据滞后的话最近一两天才会反映出来。

**因此**：4 OCPU/24 GB 在一个自然月内（2,920 OCPU-h / 17,520 GB-h）仍在付费 tenancy 的免费额度内；
但**跑满整月只剩 80 OCPU-h / 480 GB-h 余量**，不能再加 A1 实例，也不要在月内停机重开折腾。
定性收口靠 2026-11-18 之后 `free-arm` 的日成本是否出现非 0：出现即说明按 1,500 口径算，需要降到 2 OCPU/12 GB。

### 存储类免费额度与现状（2026-11-16）

Oracle 官方数字（`Always Free Resources` 页，2026-11-16 抓取）：

| 资源 | Always Free 额度 | 现状 | 判定 |
| :--- | :--- | :--- | :--- |
| 块存储（boot + block **合计**） | 200 GB | **200 GB**：`free-arm` 200（`mem-e5` 已于 2026-11-18 随实例删除，此前是 300） | 现正好满额、无超额计费 |
| 卷备份（boot + block 合计，限 home region） | **5 个**（`oci limits value list --service-name block-storage` → `free-backup-count = 5`，与 `total-free-storage-gb = 200` 是**两个独立字段**） | 1 个：`free-arm-baseline-20261116`（200 GB，FULL，AVAILABLE） | 用 1/5。备份按**个数**计，不按 GB；200 GB 全量备份不占那 200 GB 卷额度 |
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
| 多档 | **每档独立发信**：2026-11-17 那一轮评估里 €1 / €5 / €25 三档各来一封（实测 3 封） |

**实测结论（2026-11-17 07:06 收到告警邮件）**：budgets **把 credit 覆盖的用量也算进 actual-spend**。

邮件原文关键字段：`Alert Type: Actual` / `Threshold: €25.00` / `Spend in cycle: €35.17` /
`Forecast: €62.10` / `Time in cycle: 17 / 30 days`。而同期 `usage-api` 的 11 月 COST 是 €34.30（差一天的量），
两者一致 —— 也就是说 €35.17 里包含仍由 free trial credit 支付的部分（credit 到 2026-11-20 23:59Z 才用完）。

两个直接后果：

1. **这三档告警是"用量闹钟"，不是"扣钱闹钟"**：试用期内一样会响。想判断"是否真从卡上扣钱"，只能看
   `end-date`（2026-11-20T23:59:59Z）之后的数据，或控制台的 credit 余额。
2. 记录里那次 `Forecast: €62.10` 与实测日均 €2.18 × 30 ≈ €65 基本吻合，可作为量级校验。

（此前以为 `actual-spend` 恒为 `null` 说明"不计入 credit"，那是旧预算自己没跑起来 —— 旧预算 `amount: 1.0` +
绝对阈值 €25…€250 + `SINGLE_USE`，从来没评估过。）

### 用 usage-api 核对"某天到底烧了多少钱"（2026-11-18 踩过的坑）

两个粒度会打架，且日行**不保证已物化完**：

- 11-18 当天读日行：`Standard - E5 = 16 OCPU-h`；同时读小时行：`00:00…09:00` 共 **20 OCPU-h**。日行滞后。
- 判断"日行是否只是没长齐"，要拿**全天恒定的对照行**，别用随事件变化的那几行做证据。对照行是
  `Block Volume - Free`（前 200 GB 免费部分）：free-arm 的 200 GB 卷全天都在，整日应为
  `6.451612903`（=200/31）。
- **当天的行会一直变长，只有次日复核才是定稿。** 同一行 11-18 首次读到 `2.150537634`（恰 1/3）、
  08:12 UTC 再读已长到 `6.451612903`（= 199.9996 GB，满值）。所以那个 1/3 是**物化滞后**，
  不是"日表只覆盖 8 小时"——早读时连对照行本身都还没长齐，不能用它反推覆盖时长。

判"某一小时有没有计费"看小时桶的 `time-usage-ended`（桶 `10:00` 覆盖 09:00–10:00）：

| 现象 | 结论 |
| :--- | :--- |
| 桶存在且 `qty < 2.0` | 小时级、按实际秒数分账 |
| 桶不存在 | 末小时截断不计费 |
| 桶存在且 `qty = 2.0` | 按整小时计，末小时多算 |

**实测结论（2026-11-18 13:44 UTC 复核，数据已物化完整）**：按**秒**分账。

- 日行 11-18（桶标签是 end time，`2026-11-19`）：`Standard - E5 = 20.608333 OCPU-h` ÷ 2 OCPU =
  **10.3042 h = 10h18m**，与 10:16–10:19 的终止时刻吻合；`Block Volume - Storage = 1.387124` GB-month
  ÷ 3.2258（整日）= 0.43 → 同样 10.3 h。
- 小时行：`10:00` 桶（覆盖 09:00–10:00）= `2.0`（整小时在跑）；`11:00` 桶（覆盖 10:00–11:00）=
  **`0.608333` OCPU-h = 18.25 分钟** —— 落在桶内的部分按比例入账，不是整小时取整、也不是整小时不计。
- 复核对照：先前 11-18 日行只有 1/3 是因为未物化完；复核时 `Block Volume - Free` 已从 `2.1505` 长到
  `6.4516`（整日满值），证明"用不变量做对照行"这个判法有效。
- **日行会继续长，一天内可读多次**：2026-11-19 08:12 UTC 再读时，11-18 那桶的 `Block Volume - Free` 已从
  `3.2258` 长到 **`6.4516`**，所以"11-18 只有 100 GB 等价"的疑点不成立 —— 那同样是未物化完，不是口径不同。
  结论：任何"当天读到的小数"都要按"可能还会长"处理，只在次日复核后才当定稿。
- **月基准是 744 h（31 天）**：`Block Volume - Free` 的整日值 `6.4516 GB-month` × 744 ÷ 24 = **199.9996 GB**，
  正好 200 GB；若按 730 h 算只有 196.2 GB，对不上。所以"整日值 = GB ÷ 31"（`200/31 = 6.4516`、`100/31 = 3.2258`）。
- **日桶稳定落后约 2 h**：11-19 08:12 UTC 时，距终止已 22 h，而桶里 `A1` 的 25 OCPU-h ÷ 4 = 6.25 h、
  `A1 - Memory` 150 ÷ 24 = 6.25 h，`8.27 − 6.25 ≈ 2.0 h`。写结论时用"桶落后约 2 h"而不是"当前覆盖 6.25 h"，
  否则容易被读成"数据只有 6 小时"。
- **11-19 整天定稿：€0.00000**（2026-11-20 08:16 UTC 复核，桶 `2026-11-20`）：
  `Standard - E5`、`E5 - Memory`、`Block Volume - Storage`、`Block Volume - Performance Units` **全部缺席**；
  同日 `Standard - A1 = 96.0 OCPU-h`（= 4 OCPU × 24 h）、`A1 - Memory = 576 GB-h`（= 24 GB × 24 h）、
  `Block Volume - Free = 6.451612903224`（= 200 GB 整日）—— 三项都指向"该桶已覆盖满 24 小时"，
  所以"归零"不是因为没采到数据，而是真的没有计费项。对照关停前 €2.18094/天，机器成本已完全停止。

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
| `backup.sh` | 状态备份：Orca 状态 + 密钥 + 系统配置 + 各 pg 库，zstd 打包、可选 gpg 加密 |

脚本按需新增（如清单、桶巡检），单文件用法写脚本头部注释，不预先建空目录。
