# Orca Headless Server 更新维护工具

用于在 Linux ARM64（如 Free-Arm 服务器）上平滑升级与维护 [stablyai/orca](https://github.com/stablyai/orca) 无头服务端（Headless Serve）。

## 背景与问题

- **为什么网页端显示“手动更新”无法点击**：Linux 无头环境（`orca serve` / `orcad`）托管了后台 PTY 会话与多智能体任务，官方为避免误中断，将远程自动热重载限制为 `unsupported-headless-serve`。
- **客户端版本一致性**：iOS 移动端连接远程主机时，会动态加载该主机所托管的 `mobile-web` bundle。当服务器版本过旧时，移动端新特性（例如浅色模式支持，首发于 `v1.4.243`）将无法生效。

## 脚本特点

1. **版本自动比对**：调用 GitHub API 获取官方最新 Release，已是最新则自动跳过。
2. **蓝绿隔离解包**：下载 AppImage 后先解压至临时目录验证二进制完整性，绝不直接覆盖正在运行的目录。
3. **安全原子切换**：自动备份旧版本至 `squashfs-root.bak`，再进行秒级目录切换。
4. **会话保护机制**：默认只更新程序文件，不强行重启后台服务；仅在显式传入 `--restart` 时才平滑重载守护进程并进行 200 OK 健康检查（本地已是最新版本时也会照此重启）。
5. **沙箱权限修复**：AppImage 以普通用户解包会丢掉 `chrome-sandbox` 的 setuid，Electron 启动即 FATAL。切换目录后脚本会用 `sudo chown root:root` + `chmod 4755` 重设，缺少免密 sudo 会直接报错退出。

## 使用方法

### 1. 仅下载更新核心文件（不中断当前会话）
```bash
./update-orca.sh
```

### 2. 更新并自动重启服务（推荐空闲时使用）
```bash
./update-orca.sh --restart
```

> 请从 SSH 会话执行 `--restart`，不要在 Orca 内置终端里跑：重启会杀掉 Orca runtime，连同承载脚本的终端一起中断，启动新进程的命令根本执行不到。脚本会通过 `ORCA_PANE_KEY` 与祖先进程检测识别这种情况，直接报错退出（非零）而不是半途自杀。

### 3. 强制重装当前版本 / 指定版本更新
```bash
# 强制重新下载并解包当前最新版
./update-orca.sh --force --restart

# 更新到指定 tag
./update-orca.sh -v v1.4.247 --restart
```

### 4. 命令行参数汇总

| 参数 | 说明 | 默认值 |
| :--- | :--- | :--- |
| `-r, --restart` | 更新文件后自动平滑重启 orcad 服务 | `false` |
| `-f, --force` | 忽略本地版本比对，强制重新下载解包 | `false` |
| `-v, --version <tag>` | 指定目标 Release Tag（如 `v1.4.247`） | 最新 Release |
| `-d, --dir <path>` | 指定 Orca 安装目录 | `/home/ubuntu/apps/orca` |
| `--ip <ip>` | 指定客户端 pairing 广告 IP | `100.126.255.75` |
| `-p, --port <port>` | 指定服务监听端口 | `6768` |
| `-h, --help` | 查看帮助说明 | - |
