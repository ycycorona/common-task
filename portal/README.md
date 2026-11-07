# 开发 Portal（Homepage）

个人开发门户页：一个网页，聚合常用机器的 `ssh://` 入口、本机与远端服务（望楼、Orca、sub-pipe 案例台、magpie Web 管理、Komari 监控、rigledger 台账）以及它们的状态灯。上游是 [gethomepage/homepage](https://github.com/gethomepage/homepage)（GPL-3.0），我们只提供配置。

## 这份配置在哪跑

| 项 | 值 |
| :--- | :--- |
| 运行机器 | free-arm（OCI A1.Flex，Ubuntu 24.04，Tailscale `100.126.255.75`） |
| 运行目录 | `~/portal`（本目录的一份拷贝，不是软链接） |
| 访问地址 | <http://100.126.255.75:8790/> |
| 暴露面 | 仅 Tailscale：本机 iptables 默认 REJECT，公网只放 22 / 28010 |

**本目录是配置的唯一来源**，`~/portal` 只是运行副本。改完在这里提交，再同步过去：

```sh
rsync -a --delete ~/orca_pro/common-task/portal/ ~/portal/
cd ~/portal && docker compose up -d          # 首次；之后 yaml 会自动热重载
```

`--delete` 会删掉 `~/portal` 里容器自动生成的东西（`config/logs/`、空的 `custom.css`、`docker.yaml` 等），容器下次启动会重新生成，因此同步后跟一次 `up -d` 最省事。

运行目录另有一份**本机操作贴士** `~/portal/README.md`（常用命令、目录分工、哪些文件是自动生成的空壳），读者是「此刻在这台机器上操作的人」，不随本文件同步。本文件是事实源与换机手册。

## 首次部署（换机器时）

```sh
sudo apt-get install -y docker.io docker-compose-v2    # 或发行版对应包
cd ~/portal && docker compose up -d
docker compose ps                                      # 期望 homepage: Up (healthy)
curl -sI http://127.0.0.1:8790/ | head -1               # 期望 200
```

开机自恢复靠 `docker.service` enabled + compose 的 `restart: unless-stopped`，**没有** systemd 单元。

## 文件分工

| 路径 | 作用 |
| :--- | :--- |
| `docker-compose.yml` | 容器定义：host 网络、`pid: host`、`PORT=8790`、`HOMEPAGE_ALLOWED_HOSTS` |
| `config/services.yaml` | 入口分组：望楼与编排 / 开发服务 / Farm PC 算力 / 监控与台账 / 远程机器 |
| `config/settings.yaml` | 主题、状态点样式、分组布局（`layout` 的键必须与 `services.yaml` 的分组名一致）、`target: _blank` 让所有入口都开新标签、Portal 页签留原地 |
| `config/widgets.yaml` | 左上角本机 CPU / 内存 / 磁盘 / uptime + 时钟 |
| `config/bookmarks.yaml` | 底部常用链接 |
| `config/custom.js` | 按 UA 改写 Orca 卡片的跳转（见下） |
| `public/farm-compute.html` | Farm PC 算力服务汇总：在线三服务、orc-1 接入、启动限制及未启动安装；只读挂载到 Homepage public |

容器首次启动会在 `config/` 里生成 `custom.css`、`custom.js`、`docker.yaml`、`kubernetes.yaml`、`proxmox.yaml`、`logs/`；除 `custom.js` 外都没用，不入 Git。

## 加一个入口

```yaml
    - 显示名:
        icon: mdi-xxx              # https://pictogrammers.com/library/mdi/
        href: http://<host>:<port>/
        description: 一句话
        ping: http://<host>:<port>/   # 可选：有这一行才出绿/红状态点
```

新分组要同时在 `config/settings.yaml` 的 `layout` 里加同名键，否则不显示。

## Farm PC 算力服务

Portal 的「Farm PC 算力」分组链接到 `/farm-compute.html`，页面是 2026-11-07 实机核验快照，不是实时监控。在线服务为 CUDA UVR 人声分离（8703）、CUDA Qwen3-ASR-1.7B（8701）、CPU Qwen3-ForcedAligner-0.6B（8700）；orc-1 的 sub-pipe2（8875）已登记三项并启用 `--provider`，人声分离仍由每次运行的 `--separate-vocals` / `params.separate_vocals=true` 选择。

同时记录未启动的 CosyVoice3（启动脚本预设8005）安装（原 Bonsai 27B 安装已清理）。没有为盘点启动它们，也不把安装目录当推理成功。UVR 无开机任务；ASR / 对齐有开机延迟2分钟任务，本轮未重启机器验证。共享 GPU 锁、idle25秒、地址、目录、认证与恢复边界见汇总页。密钥不进页面或配置，三个认证服务不设匿名 HTTP 状态灯，以免把401画成故障。

本次变更的运行同步只复制涉及文件，保留运行目录专用 README 和容器生成文件；新增 public 挂载后在 `~/portal` 执行 `docker compose up -d --no-deps --pull never homepage`，无需更新镜像。

## Orca 入口的平台分派

`config/custom.js` 在浏览器端改写那张卡片的 `href`：

| UA | 跳到 |
| :--- | :--- |
| iOS / iPadOS / Android | `orca://` —— Orca 手机 App（已由 owner 实测可拉起） |
| macOS / Windows | `orca://` —— Orca 桌面 App |
| Linux 浏览器（如 free-arm 本身） | `http://100.126.255.75:6768/` web 界面 |

依据 `stablyai/orca` 仓库：`src/main/startup/orca-protocol-registration.ts`（桌面端注册 `orca://`）、`src/shared/remote-runtime/pairing/pairing.ts`（手机深链 `orca://pair?code=…`，说明移动 App 同用 `orca` scheme）。

限制：只能进 App 首页，进不了具体 workspace / 会话——那需要桌面 UI 现生成的一次性配对码，不能静态化。手机未装 App 时点卡片无反应（tooltip 已注明）。

## 两个必须保留的设置（踩过的坑）

1. **`network_mode: host`**。bridge 网络下容器访问不到本机服务：`docker0 → 宿主端口` 的流量进 INPUT 链，被本机「只放行 22 / 28010」的 iptables 规则 REJECT，望楼与 Orca 的 `ping` 全部失败。
2. **`pid: host`**。不加时资源组件的 CPU 显示 `-`：它读主机进程表算负载，容器自己的 PID namespace 里拿不到。

## 暴露面与密钥

- **没有登录**。仅靠 Tailscale 加密 + 本机 iptables 兜底。要认证就在前面套一层 Caddy `basic_auth`。
- 需要密钥的 widget 走 `HOMEPAGE_VAR_*` 环境变量，不写进 `services.yaml`；当前没有任何带密钥的 widget。
- magpie Web 管理（free-arm `:3430`）的 web key 在 free-arm 的 `~/.config/magpie/web.env`，不入 Git；浏览器首访带一次 `?k=<key>`，之后靠 400 天的 cookie 直接开根路径。

## 未接入 / 待办

- **澄流（115-util）**无常驻界面：AriaNg 只经 `bin/115web` 的临时 ssh 隧道 + 本机 8080 静态服务打开，不列入。
- 可选：加 `iframe` widget 嵌 Komari 面板（`http://100.75.252.49:25774/`），替代「每台机器装 glances」。
