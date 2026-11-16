# oci/ —— Oracle Cloud 账户与资源治理

用自己的 OCI tenancy 做日常管理：实例（free-arm / orc-1 及其他 VPS）、VCN 与安全列表、
块存储与备份、Object Storage 桶、limits 与费用。**账户现状的单一事实源不在本仓库**，在
`../../tech_note_ai/30-Knowledge/Infra/oci-free-vcn-实例与SSH访问.md`；这里只放操作脚本与凭据约定。

Last Validated: 2026-11-16（PAYG 生效、当月花费、A1 额度两处官方口径与自证用量、存储类额度与首份卷备份）

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
| 块存储（boot + block **合计**） | 200 GB | 300 GB：`free-arm` 200 + `mem-e5` 100 | 超 100 GB = €0.1275/天；删掉 `mem-e5` 后正好 200 |
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

## 监控与告警（2026-11-16）

分三层，覆盖的额度不同：

| 层 | 监控什么 | 机制 | 状态 |
| :--- | :--- | :--- | :--- |
| 容量 | 桶大小 vs 免费额度（Standard/IA/Archive 各 10 GB） | Monitoring 告警，namespace `oci_objectstorage`，指标 `StoredBytes`（bytes） | ✅ 已建 `bucket-size-8g` |
| 费用 | 任何超量的钱 | Budget `payg-monthly-150` 的 €1 / €5 / €25 三档 | ✅ 已建；`actual-spend` 是否计入 credit 覆盖的用量**未确认** |
| 请求数 | 50,000 请求/月 | `usage-api` 的 `Object Storage - Requests` SKU（单位 10K） | 只有查询，无告警；本月实测 0.0139 ≈ 139 次 |

### 已建的容量告警

| 项 | 值 |
| :--- | :--- |
| 告警 | `bucket-size-8g`，`CRITICAL`，ACTIVE |
| MQL | `StoredBytes[5m].max() > 8000000000`（阈值 8 GB，十进制；免费 Standard 10 GB） |
| 分辨率 / 等待 | `--resolution 1m`（该指标**只接受 1m**，写 1h 会被拒）、`--pending-duration PT30M`、重复通知 24h |
| 通知 | ONS 主题 `objectstorage-alerts` → 邮件 `ycy421@gmail.com` |

```sh
T=<tenancy-ocid>
oci monitoring metric-data summarize-metrics-data --compartment-id $T --namespace oci_objectstorage \
  --query-text 'StoredBytes[6h].max()'            # 看当前值；实测与 oci os object list 求和一致（14,650,054）
oci monitoring metric list --compartment-id $T --namespace oci_objectstorage --all   # 有哪些指标
oci monitoring alarm list --compartment-id $T --all                                  # 告警清单
oci ons subscription list --compartment-id $T --topic-id <topic-ocid> --all         # 订阅状态
```

已知坑：

- 指标 `StoredBytes` 的 dimension 含 `tier=NORMAL`（Standard），换 tier 要分别看。
- **桶里没有对象时该桶不发指标**（官方文档），空桶不会有告警。
- `StoredBytes` 不含未完成的分片上传（残留要 `oci multipart list` 查）。
- 邮件订阅创建后是 `PENDING`，**必须点邮件里的确认链接**，否则告警触发也不会送达。
- `StoredBytes` **大约每小时才有一个数据点**（实测 3 小时窗口只返回 1 个点），所以告警的发现与解除都以小时计，
  不要指望分钟级。

### 链路测试（2026-11-16 已做一次）

1. 订阅确认后 `lifecycle-state: ACTIVE`；
2. 把阈值临时降到 `> 1000000`（1 MB）、`pending-duration PT1M` → 约 2 分钟后
   `oci monitoring alarm-status list-alarms-status` 报 `status: FIRING`；
3. 立刻改回 `> 8000000000` 与 `PT30M`（`alarm get` 复核 query/pending/severity/enabled 均已恢复）。

结论：告警→ONS→邮件这条链路在 OCI 侧是通的（FIRING 已确认）；**邮箱端是否真收到由收件人确认**。
注意改配置后状态不会立刻从 FIRING 转 OK，要等下一个数据点。

## 备份与恢复（2026-11-16）

### 现在有什么

| 层 | 产物 | 位置 | 说明 |
| :--- | :--- | :--- | :--- |
| L0 | git 仓库 | GitHub | 代码层，唯一能自动异地的一层 |
| L1 | `free-arm-<UTC>.tar.zst.gpg` | `~/backups/`（**仅本机**） | 由 `./backup.sh` 生成；实测 252 MB / 解压 4.1 GB |
| L1' | `t7-backup.timer` 每周日 01:30 | 对象存储 `yorches-spec006-t7/daily/` | 仅一个库的 pg_dump + 校验 + 恢复演练 |
| L3 | `free-arm-baseline-20261116` | OCI 卷备份 | 200 GB FULL，占 1/5 免费名额 |

L1 内容：`orca/`（`~/.config/orca`，**排除** `agent-runtimes`、`orcad-artifacts`、`plugins-data`、`logs`）、
`.ssh`、`.oci`、`.gnupg`、`.config/{magpie,115-util,gh}`、`system/`（fstab、hosts、sshd_config、
unit 清单、timers、crontab、自定义 unit 文件）、`portal/config`、`db/*.dump`（各 postgres 容器）。

为什么排除那四类：`~/.config/orca` 实测 **5.3 GB**，其中 `agent-session-journal.db` 就 3.5 GB、
`agent-runtimes` 1.0 GB、`orcad-artifacts` 320 MB、`plugins-data` 160 MB —— 后者全可重装，
只留状态后才压到 252 MB。

口令：`~/.config/free-arm-backup/passphrase`（600，随机生成）。**必须另存一份到密码管理器** ——
机器没了就等于备份打不开。加解密：`gpg --batch --symmetric --cipher-algo AES256 --compress-algo none`。

### 文件级恢复（迁到新机或救单个文件）

```sh
# 1. 解密 + 解包（口令文件从密码管理器取回，不要放进仓库）
gpg --batch --decrypt --passphrase-file <passphrase> -o /tmp/fa.tar.zst ~/backups/free-arm-<ts>.tar.zst.gpg
mkdir -p /tmp/fa && tar -C /tmp/fa -xf /tmp/fa.tar.zst

# 2. 按目录对位还原（先备份现场的旧文件）
rsync -a /tmp/fa/free-arm-<ts>/orca/        ~/.config/orca/
rsync -a /tmp/fa/free-arm-<ts>/.ssh/        ~/.ssh/
rsync -a /tmp/fa/free-arm-<ts>/.oci/        ~/.oci/
rsync -a /tmp/fa/free-arm-<ts>/.config/     ~/.config/
cp -a  /tmp/fa/free-arm-<ts>/system/*       /etc/          # 逐个看，别整目录覆盖
cp -a  /tmp/fa/free-arm-<ts>/portal/config/ ~/portal/config/

# 3. 数据库（容器起来之后）
docker exec -i -u postgres <容器> pg_restore -U postgres -d "$POSTGRES_DB" --clean --if-exists < db/<容器>.dump
```

单个文件的场景（比如只丢了 `~/.config/orca/profiles/local-default/`）不要重启 Orca，先 `tar -x` 取那一个路径比较安全。

### 卷级恢复（整机重来）

1. `oci bv boot-volume create --source-boot-volume-backup-id <backup-ocid> --size-in-gbs 200 --availability-domain <AD>`
   —— 注意 `oci bv boot-volume list` 里备份与卷都要在 **home region**。
2. `oci compute instance launch --shape VM.Standard.A1.Flex --shape-config '{"ocpus":4,"memoryInGBs":24}'`
   挂这块新卷当引导卷，subnet 与安全列表用原来的（`Default Security List for free-vcn`）。
3. **A1 可能抢不到容量**：`out of host capacity` 时只能换 AD 重试或等；这正是需要 L4 异地副本的原因。
4. 起来之后：`tailscale up`（重新登录）、`docker compose up -d`（`~/portal`）、
   `systemctl start agentsview caddy dashboard-serve komari-agent`、`systemctl list-timers` 核对 `t7-backup`。
5. 核对：`hostname`、`df -h /`（应 193 G）、`ssh -T git@github.com`、`oci session authenticate`（重登）。

### 已知缺口

- L1 只在**本机** `~/backups/`：防误删与配置写坏，不防磁盘/实例丢失。上传需要 OCI 会话（每小时过期）——
  长期方案是 Instance Principal + 专用桶，见 `<tech_note_ai>/AI-Output/2026-11-16-OCI-free-arm-备份策略调研.md`。
- 三个 postgres 容器的监听是**非默认**的，`backup.sh` 从容器 PID1 的 cmdline 里读端口：

  | 容器 | 监听 | 备注 |
  | :--- | :--- | :--- |
  | `yorches-tests` | `127.0.0.1:55452` | trust 认证、**没有挂数据卷**（数据在容器可写层，删容器即丢），2026-11-12 起未重启 |
  | `yorches-spec006-t8` | `100.126.255.75:55442` | 监听在 Tailscale 地址上；t7 的每周备份就是它 |
  | `yorches-spec006-test` | 默认 5432 + unix socket | |

  `pg_isready` / `pg_dump` 不带端口时会连默认 5432，于是看起来"服务没在跑" —— 这是 2026-11-16 我误判过的坑。
  取端口：`docker exec <容器> sh -c "tr '\0' ' ' </proc/1/cmdline"` → 解析 `-c port=NNNN`。
- 没做过恢复演练：唯一证明是"解一个包、取一个文件、比一次 hash"。

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
| `backup.sh` | 状态备份：Orca 状态 + 密钥 + 系统配置 + 各 pg 库，zstd 打包、可选 gpg 加密 |

脚本按需新增（如清单、桶巡检），单文件用法写脚本头部注释，不预先建空目录。
