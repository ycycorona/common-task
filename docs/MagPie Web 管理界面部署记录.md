# MagPie Web 管理界面部署记录

2026-10-31 · macOS 本机（ycybers-mac）

MagPie 是 macOS 桌面 App（`/Applications/magpie.app`），它没有默认常开的 Web 管理页；`magpie web` 子命令才是「把 App 窗口搬进浏览器」的那条路。本文记录在本机把它跑起来、并只从 Tailscale 内网用 IP 直连访问的现状与做法。

## 现状

| 项 | 值 |
| --- | --- |
| 进程 | `/Applications/magpie.app/Contents/MacOS/magpie web --no-open --addr 100.100.102.49:3430` |
| 承载方式 | tmux 会话 `magpie-web`（`tmux attach -t magpie-web` 可看到实时日志） |
| 监听 | `100.100.102.49:3430`（只绑 Tailscale IP，不绑 loopback、不绑局域网） |
| 访问链接 | `http://100.100.102.49:3430/?k=<见 ~/.config/magpie/web-key>` |
| Web key | `~/.config/magpie/web-key`（权限 600，**不入 Git**） |
| 网关 | `http://127.0.0.1:3425`，仍由桌面 App（PID 1258）提供；App 退出后由本进程接管 |

## 启动

```bash
tmux new-session -d -s magpie-web -c /tmp \
  "MAGPIE_WEB_KEY=$(cat ~/.config/magpie/web-key) exec \
   /Applications/magpie.app/Contents/MacOS/magpie web --no-open --addr 100.100.102.49:3430"
tmux capture-pane -pt magpie-web | grep 'magpie web on'
```

`MAGPIE_WEB_KEY` 让 key 跨重启固定（不设则每次启动随机生成）。值只存在本机文件里，文档和仓库都不写明文。

## 访问

浏览器打开 `http://100.100.102.49:3430/?k=<web-key>`。首访返回 `303` 并下发 cookie `magpie_web_3430`（`Max-Age=34560000`，400 天，`HttpOnly`），之后同一浏览器直接开 `http://100.100.102.49:3430/` 即可，不用再带 key。

## 运维

```bash
tmux attach -t magpie-web                        # 看实时日志，Ctrl-b d 退出（不杀进程）
tmux capture-pane -pt magpie-web                 # 不回终端直接抓最后几屏
tmux kill-session -t magpie-web                  # 停止
lsof -nP -iTCP:3430 -sTCP:LISTEN                 # 确认监听地址与 PID
```

## 取舍记录

- **不用 `tailscale serve` 的 https 域名**：用户明确要直接用 Tailscale IP 访问。此前挂过 `--https=8443` 反代，已用 `tailscale serve --https=8443 off` 撤掉；`443 → 127.0.0.1:3080` 的既有反代未动。
- **不用 `--lan`**：`--lan` 绑 `0.0.0.0`，会把能改 MagPie 全部供应商配置和 key 的管理界面暴露到局域网；改成只绑 tailnet IP，暴露面最小。
- **不用 `magpie daemon install --web`**：那是 launchd 常驻，本次只需临时可用，故用 tmux 承载，会话和日志可追溯。

## 已知限制

- 重启电脑或注销后不会自动起来，需重新执行「启动」里的命令。
- `--addr` 写死当前 Tailscale IP `100.100.102.49`；该 IP 变化（重新登录 tailnet、换机器名）后要改地址重启。
- 明文 http：Tailscale 链路本身是 WireGuard 加密，但拿到链接的人都能改 MagPie 设置、看到各家 key；cookie 也没有 `Secure` 标志。
- 页面只服务 `?k=`/cookie 通过后的请求，静态资源同样需要鉴权，直接 curl 资源路径会得到 `401`。

## 验证（2026-10-31）

- `curl -L http://100.100.102.49:3430/?k=<key>` → `200 text/html`，44499 B，`<title>magpie</title>`。
- 无 key 访问 `http://100.100.102.49:3430/` → `401`。
- 带 cookie 取 `/icons/favicon.svg` → `200 image/svg+xml`。
- `http://127.0.0.1:3430/` 与 `http://192.168.18.180:3430/` 均连接被拒（确认只绑 tailnet IP）。
- `tailscale serve status` → 仅剩 `443 → 127.0.0.1:3080`，8443 已撤。
