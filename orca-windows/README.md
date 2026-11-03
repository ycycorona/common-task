# Orca Windows 桌面版更新维护工具

[`orca-server/update-orca.sh`](../orca-server/update-orca.sh) 的 Windows 对应版本。原脚本面向 **Linux ARM64 无头服务端**（AppImage 解包 + `orca serve` 重启）；Windows 上跑的是 **桌面版 Orca**（NSIS 按用户安装到 `%LOCALAPPDATA%\Programs\orca`，无需管理员权限），因此更新链路完全不同：

| 环节 | Linux 原脚本 | 本 Windows 脚本 |
| :--- | :--- | :--- |
| 产物 | `orca-linux-arm64.AppImage` | `orca-windows-setup.exe`（NSIS） |
| 校验 | 解包后检查 `orca-ide` 存在 | 对照 release 的 `latest.yml` 校验 `size` + `sha512`（base64） |
| 应用 | 蓝绿切换 `squashfs-root` 目录 | 关闭 Orca → `installer /S` 静默安装 |
| 重启对象 | `orca serve` 守护进程（`pkill` + `nohup`） | 桌面应用本身（`Orca.exe`） |
| 会话保护 | 默认不动运行中的服务 | 默认只下载+校验，**不关闭 Orca** |
| 回滚 | 保留 `squashfs-root.bak` | 装回旧 tag：`-Version <旧 tag> -Apply` |

## 为什么是 PowerShell

仓库其余脚本是 bash / Python，但这一份必须是 `.ps1`：要读写安装目录的 `package.json`、比对 `latest.yml` 的 sha512、用 `Start-Process` 拉 NSIS 静默安装并在安装前解占用安装目录。用 bash（Git Bash）做这些会引入 `taskkill` / `powershell -c` 的字符串转义，反而更脆弱。脚本按 Windows PowerShell 5.1 与 PowerShell 7 双版本兼容编写（UTF-8 BOM + CRLF）。

## 安装

把两个文件放进任意 PATH 目录（示例 `%USERPROFILE%\bin`）：

```powershell
Copy-Item orca-windows\update-orca.ps1, orca-windows\update-orca.cmd $env:USERPROFILE\bin\
```

之后在任意目录执行 `update-orca`。`update-orca.cmd` 只是入口包装（优先 `pwsh`，回退 `powershell`，并带 `-ExecutionPolicy Bypass`）。

## 使用方法

### 1. 只看计划（不下载、不安装）

```powershell
update-orca -DryRun
```

### 2. 仅下载并校验安装包（不中断当前会话）

```powershell
update-orca
```

### 3. 关闭 Orca、静默安装并重启（推荐空闲时用）

```powershell
update-orca -Apply
```

### 4. 指定版本 / 强制重装 / 回滚

```powershell
update-orca -Version v1.4.247 -Apply    # 指定 tag
update-orca -Force -Apply               # 同版本也重装
update-orca -Version v1.4.240 -Apply    # 退回旧版本
update-orca -Verify <安装包路径>         # 只校验一个已下载的安装包
update-orca -SelfTest                   # 跑脚本内的离线自检
```

### 5. 参数汇总

| 参数 | 说明 | 默认值 |
| :--- | :--- | :--- |
| `-Version <tag>` | 目标 Release Tag，如 `v1.4.247` | 最新 Release |
| `-Force` | 忽略本地版本比对，强制安装（同版本也重装） | `false` |
| `-Redownload` | 忽略已缓存安装包，重新下载 | `false` |
| `-Apply` | 关闭 Orca + 静默安装 + 重启 | `false` |
| `-NoRelaunch` | 配合 `-Apply`，安装后不启动 Orca | `false` |
| `-Yes` | 跳过 `-Apply` 的交互确认 | `false` |
| `-InstallDir <path>` | 安装目录 | `%LOCALAPPDATA%\Programs\orca` |
| `-DryRun` | 只解析并打印计划 | `false` |
| `-Verify <path>` | 校验已下载安装包的 size / sha512 | - |
| `-CleanCache` | 安装成功后清理本脚本的下载缓存 | `false` |
| `-SelfTest` | 离线断言自检（版本比较、大小格式化、sha512、清单比对） | `false` |
| `-Help` | 显示完整帮助 | `false` |

## 数据与安全

- 下载缓存：`%LOCALAPPDATA%\orca-updater\manual\<tag>\orca-windows-setup.exe`；不写 Orca 自己的 `%LOCALAPPDATA%\orca-updater\pending`。缓存复用前会重新计算 sha512。
- 校验依据是 release 资产里的 `latest.yml`；读不到清单时明确警告并跳过哈希校验，不会静默降级。
- 在 Orca 托管的终端里 `-Apply` 会被拒绝（关闭 Orca 会连带结束该终端），确需安装要加 `-Force`。
- 安装是原地替换，没有目录级回滚；需要回退时用 `-Version <旧 tag> -Apply` 重装旧版本。

## 与官方更新通道的关系

Orca 自带更新（`Help → Check for Updates...`，走 GitHub Releases 的 `stablyai/orca`）仍是首选。本脚本用于：强制重装、固定或回滚版本、只下载并校验、以及排查自动更新失败。
