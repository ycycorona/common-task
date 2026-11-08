# Common Task Scripts

这是一个包含常用 Python 脚本的项目，主要用于文件处理、通知和媒体转换。

## 系统要求 (System Requirements)

本项目脚本主要在 macOS 环境下开发。系统级命令按用途分布如下，缺哪个只影响对应目录的脚本：

| 命令 | 用于 | 安装 |
| --- | --- | --- |
| `ffmpeg` | `media/` 全部音视频脚本（含 `ffprobe`，`mp3_duration_ms.sh` 直接调它） | `brew install ffmpeg` |
| `terminal-notifier` | `tools/codex_notify.py`、`organize/jav_renamer.py` 的任务完成通知 | `brew install terminal-notifier` |
| `sips` | `media/heic_batch_convert.sh` 的 HEIC 转换 | macOS 自带 |
| `SetFile` | `media/replace_audio_silence.py` 保留创建时间 | Xcode Command Line Tools |
| `ssh` | `download/download_dmm_preview.py --via`、`tools/remote-dir-sync.sh` | macOS 自带 |
| `screen` | `media/run_whisper_screen.sh`（远端转录任务） | macOS 自带 |
| `ps`、`lsof` | `devps/devps.py` 的进程与 cwd 探测 | macOS 自带 |
| `swiftc` | `zed-defaults/` 编译 | Xcode Command Line Tools |

Python 依赖见 [`requirements.txt`](requirements.txt)：`jvav`、`huggingface_hub`、`yt-dlp` 是运行依赖，
`pytest` 只有跑 `media/test_extract_mp3.py` 时才需要。

## 安装方式

```bash
git clone <repository-url>
cd common-task
pip install -r requirements.txt
```

## 目录结构

| 目录 | 内容 |
| --- | --- |
| `media/` | 音视频与图片处理（ffmpeg / sips），含 `media/tests/mocks` 假二进制 |
| `download/` | 下载类：DMM 预览、Hugging Face 快照 |
| `organize/` | 文件名整理与文本统计（JAV 重命名、SRT 字数） |
| `tools/` | 小工具：Codex 通知钩子、远程目录同步 |
| `devtools/` | 仓库自身的文档校验（`check_docs.sh` + 自检） |
| `devps/` | 孤儿开发进程检测/停止（macOS，零依赖） |
| `monitoring/` | Komari 离线/磁盘告警（零依赖，跑在监控宿主上） |
| `portal/` | 个人开发门户站（free-arm 上的 Homepage 配置：机器与服务入口、状态灯、平台深链） |
| `ai-compact/` | AI Agent 自动压缩阈值的探测与设置 |
| `archives/` | 一次性/归档任务（荷兰超市宣传册等） |
| `orca-server/`、`orca-windows/` | Orca 无头服务端的更新维护（Linux / Windows） |
| `zed-defaults/` | macOS 默认程序切到 Zed |

根目录只留 `README.md`、`AGENTS.md`、`requirements.txt` 与各子目录。

## 脚本列表

### 1. JAV 整理工具 (`organize/jav_renamer.py`)

一个自动化的 JAV 文件整理工具，能够识别视频、音频及字幕文件中的番号，自动获取元数据并重命名。

**功能特点:**
- **自动识别**: 从文件名中智能提取 JAV 番号（如 ABC-123）。
- **元数据获取**: 使用 `jvav` 库抓取影片标题和演员信息。
- **标准化命名**: 将文件重命名为 `番号 标题 [演员].扩展名` 的格式。
- **多格式支持**: 支持视频 (`.mp4`, `.mkv` 等)、音频 (`.mp3`, `.flac` 等) 以及字幕 (`.srt`, `.ass` 等)。
- **分段支持**: 自动处理和保留分段标记（如 A/B/C 或 1/2/3）。
- **智能清理**: 自动移除标题中的指定关键词，并对过长标题进行截断（默认 50 字符）。
- **本地兜底**: 远程未匹配时，会尝试基于本地文件名做清理后重命名。
- **目录忽略**: 自动跳过名为 `no_need` 的文件夹。
- **安全执行**: 
  - 默认开启预览模式 (Dry Run)，只显示计划的变更。
  - 遇到已存在的目标文件名会自动跳过。
  - 任务完成后发送桌面通知。

**依赖:**
- `jvav` 库
- 系统需安装 `terminal-notifier` (用于通知功能)

**使用方法:**
```bash
# 预览模式 (默认) - 仅显示会做什么，不实际修改
python organize/jav_renamer.py /path/to/videos

# 执行模式 - 实际重命名文件
python organize/jav_renamer.py /path/to/videos --execute
```


---

### 2. Hugging Face 仓库下载工具 (`download/hf_snapshot_download.py`)

基于 `huggingface_hub.snapshot_download()` 的命令行工具，用于快速下载 Hugging Face 上的模型、数据集或 Space。

**功能特点:**
- **多类型支持**: 支持下载模型 (model)、数据集 (dataset) 和 Space。
- **版本控制**: 支持指定分支、标签或 commit hash。
- **文件过滤**: 支持通过 include/exclude 模式选择性下载文件。
- **断点续传**: 网络中断后可继续下载（`--resume`）。
- **私有仓库**: 支持使用 token 访问私有和门控仓库。
- **跨平台优化**: 支持禁用符号链接（推荐在 macOS/Windows 上使用 `--no-symlinks`）。
- **自定义缓存**: 支持指定自定义缓存目录。

**依赖:**
- `huggingface_hub` 库

**使用方法:**
```bash
# 基本用法 - 下载完整模型
python3 download/hf_snapshot_download.py kotoba-tech/kotoba-whisper-v2.0-faster

# 推荐用法 - 不使用符号链接 + 断点续传
python3 download/hf_snapshot_download.py kotoba-tech/kotoba-whisper-v2.0-faster \
  -o ./kotoba-whisper --repo-type model --no-symlinks --resume

# 只下载部分文件（如模型权重和配置）
python3 download/hf_snapshot_download.py kotoba-tech/kotoba-whisper-v2.0-faster \
  -o ./kotoba --no-symlinks --resume \
  --include "*.bin" "*.json"

# 下载数据集
python3 download/hf_snapshot_download.py username/dataset-name \
  --repo-type dataset -o ./my-dataset

# 使用 token 下载私有仓库
python3 download/hf_snapshot_download.py private-org/private-model \
  --token hf_xxxxxxxxxxxx --no-symlinks
```

---

### 3. 音频静音替换工具 (`media/replace_audio_silence.py`)


用于批量替换 MP4 视频文件中的音频轨道为静音轨道的工具。这在需要去除原始音频但保持视频结构或为了特定播放设备兼容性时非常有用。

**功能特点:**
- **音频替换**: 使用 `ffmpeg` 将音频轨道替换为符合特定参数的静音 ADPCM IMA WAV 轨道 (16kHz, Stereo)。
- **时间戳保留**: 尝试保留文件的创建时间 (Creation Time) 和修改时间 (Modify Time)。需要 `SetFile` 命令支持（通常在 macOS 上可用）。
- **批量处理**: 支持递归处理子目录。
- **非破坏性**: 将处理后的文件输出到指定的输出目录，不直接覆盖源文件（除非输入输出目录相同，但脚本会阻止这种情况）。

**依赖:**
- `ffmpeg`
- `SetFile` (可选，用于保留创建时间)

**使用方法:**
```bash
# 基本用法
python media/replace_audio_silence.py /input/dir /output/dir

# 递归处理所有子文件夹
python media/replace_audio_silence.py /input/dir /output/dir --recursive

# 预览模式
python media/replace_audio_silence.py /input/dir /output/dir --dry-run
```

---

### 4. Codex 通知工具 (`tools/codex_notify.py`)

一个简单的脚本，用于发送桌面通知。主要被其他脚本（如 `jav_renamer.py`）调用，用于在长时间运行的任务结束时提醒用户。

**功能特点:**
- **JSON 输入**: 接收 JSON 格式的参数来定制通知内容。
- **Codex 集成**: 专门处理 `agent-turn-complete` 类型的通知，显示助手的最后一条消息或默认消息。
- **系统通知**: 使用 `terminal-notifier` 发送 macOS 原生通知。

**依赖:**
- `terminal-notifier` (macOS 命令行工具)

**使用方法:**
通常不单独使用，而是由其他脚本调用。如果需要手动测试：
```bash
python tools/codex_notify.py '{"type": "agent-turn-complete", "last-assistant-message": "任务已完成"}'
```

---

### 5. DMM 预览视频下载工具 (`download/download_dmm_preview.py`)

一个用于获取并下载 DMM (FANZA) 高清预览视频流媒体（m3u8 解析合并）的 Python 脚本。

**功能特点:**
- **突破地域限制**: 原生支持通过环境变量自动使用 HTTP 或 SOCKS5 代理。
- **自动获取高清原画**: 智能识别并从基础的影片小样视频重定向到高清版视频分发链接。
- **流媒体切片合并**: 内部集成 `yt-dlp` 处理最新 DMM 弃用 MP4 改用的 m3u8 分片，并无损合并为本地的 mp4。
- **防鬼影/死链保护**: 能自动探测由于代理失明或只下载到零碎切片残余出的垃圾空壳文件（低于 10kb），并在运行前主动清理，确保下载内容完整有效。

**依赖:**
- `jvav` 库
- `yt-dlp`

**使用方法:**
鉴于 DMM 严格屏蔽非日本国内 IP，强烈建议用携带纯净日本节点的 SOCKS5 代理或者 HTTP 代理声明并运行：

```bash
# 假设您的本地代理端绑定在 127.0.0.1:1080 端口，且已切换成日本节点：
all_proxy=socks5h://127.0.0.1:1080 python download/download_dmm_preview.py SSIS-001

# SSH 动态代理，把本地 1080 端口转成代理端口。
# 命令：
ssh -D 1080 -N -C 用户名@你的VPSIP
```

---

### 6. 荷兰超市宣传册下载 (`archives/fetch_supermarket_folders.py`)

下载 AH、Jumbo、Vomar、LIDL、ALDI 五家荷兰超市当期的优惠宣传册（reclamefolder）为 PDF。
纯标准库 + `curl`，无需登录、无需浏览器。AH 官方不提供 PDF（仅分页图片），脚本用
ImageMagick 合成；ALDI 与 LIDL 直接取官方 PDF，Jumbo 与 Vomar 取 Publitas 出版物的附件 PDF。

```bash
# 下载全部五家到 folders/<今天日期>/
python3 archives/fetch_supermarket_folders.py

# 只下其中几家
python3 archives/fetch_supermarket_folders.py --only AH LIDL

# 指定输出目录
python3 archives/fetch_supermarket_folders.py --outdir /tmp/folders

# 离线解析自检（不联网）
python3 archives/fetch_supermarket_folders.py --selfcheck
```

选期依据各家的**生效日期**而非发布日期：AH 按宣传册上印的起始周选取（避免误取同日发布的
促销插页），LIDL 用 offer 日期（其下周宣传册会提前几天上线）。输出目录 `folders/` 已加入
`.gitignore`。

### 7. 音视频 Shell 脚本（`media/`）

项目中还包含以下用于快速处理媒体文件的 Shell 脚本：

-   **`media/heic_batch_convert.sh`**: 批量将 HEIC 图片转换为 JPEG 或 PNG 格式。
-   **`media/video2aac.sh` / `media/video2flac.sh` / `media/video2opus.sh`**: 从视频提取音频为 AAC / FLAC / Opus（`video2opus.sh` 适配 Whisper 类转录）。三者是薄壳，共享实现在 `media/video2audio.sh`：转码参数、输出后缀与"是否跳过隐藏文件"集中在 `profile_set()`（aac 256k 跳过隐藏文件；flac 16k 单声道、历史上不跳过；opus 单声道 32k VOIP）。自检：`python3 media/test_video2audio.py`。
-   **`media/video_clip_to_wav.sh`**: 从视频中按指定的起止时间极速截取片段，并提取其音频。音频输出支持格式为 Whisper 友好的 WAV (16kHz 单声道 PCM) 或高质量 VBR 的 MP3。支持自动识别省略参数以输出至原目录。
-   **`media/video_split_to_flac.sh`**: 将长视频或音频文件按指定时长切分（默认 10 分钟），并自动将切分后的分段音频转换为 FLAC（默认）或 MP3 格式。
-   **`media/extract_mp3.sh`**: 从视频文件中提取音频并转换为 MP3 格式。支持传入多个文件顺序处理，自动根据输入文件名生成 `.mp3` 输出。支持丰富的参数自定义（码率、采样率、声道、音频流选择、起止时间截取等）。自动检测输入音频是否为 MP3，若是则直接复制音轨避免重复编码。运行时显示当前处理进度（`[N/TOTAL | PERCENT%]`），并自动过滤 ffmpeg 正常日志仅保留警告和报错，最后输出处理统计（成功/失败/总数）。单个文件失败不会中断后续任务。
-   **`media/run_whisper_screen.sh`**: VideoCaptioner 转录任务管理器。使用 screen 在后台运行转录任务，固定复用单一 session 和 window，支持任务检测、避免重复启动和 Attached 状态保护。

补充：`media/mp3_duration_ms.sh` 用 ffprobe 输出毫秒时长（给其它脚本调用）；
`tools/remote-dir-sync.sh` 从远程主机干净覆盖拉取一个目录（自检 `tools/test_remote_dir_sync.sh`）；
`organize/count_chars.py` 统计 SRT/文本字数（跳过时间轴行）。

### 8. AI Agent 自动压缩阈值 (`ai-compact/ai_compact.py`)

探测本机已安装的 AI Agent（claude / codex / pi / opencode / cursor-agent），读它们自己的配置
和文档，报出自动压缩的开关状态、触发阈值和模型级覆盖，然后按统一阈值写入并回读验证。

阈值统一表示"上下文达到多少 tokens 时触发自动压缩"；各家原生项不同，写入时换算：

| Agent | 开关 | 阈值项 | 模型级 |
| --- | --- | --- | --- |
| claude | `autoCompactEnabled` | `autoCompactWindow`（100000–1000000 或 `"auto"`） | `modelSettings.<model>.autoCompactWindow` |
| codex | 无 | `model_auto_compact_token_limit` | 不支持（`[profiles.*]` 内同名键会被忽略） |
| pi | `compaction.enabled` | `compaction.reserveTokens` = 上下文 - 阈值 | `compaction.modelOverrides.<provider/model>.reserveTokens` |
| opencode | `compaction.auto` | `compaction.buffer` = 上下文 - 阈值 | 不支持 |
| cursor-agent | 无（未暴露） | 无 | 无 |

```bash
python3 ai-compact/ai_compact.py scan                       # 探测全部已安装 Agent
python3 ai-compact/ai_compact.py scan --agent pi --json     # 单看一家 / 机器可读
python3 ai-compact/ai_compact.py set 200000                 # 统一阈值，写完自动验证
python3 ai-compact/ai_compact.py set 300000 -a claude -m claude-opus-5-5   # 模型级
python3 ai-compact/ai_compact.py set 200000 -a pi -c 1048000               # 换算 reserve 需上下文
python3 ai-compact/ai_compact.py set --unset -a pi -m 'coding-plan/*'      # 删掉匹配的模型级条目
python3 ai-compact/ai_compact.py set --disable -a opencode  # 关掉自动压缩
python3 ai-compact/ai_compact.py verify -a codex --expect 300000
```

`set` 会先落盘再进验证：回读配置比对阈值，并调用 Agent 自带的解析器
（codex 用 `codex doctor`，opencode 用 `opencode debug config`）确认配置真被认；不一致时退出码 1。


### 9. 运维与子系统

-   **`devps/devps.py`**：找出"启动者已退出"（孤儿）的开发进程并按需停止。macOS 专用、仅标准库；
    `devps [--all] [--port N] [--json]` 列进程，`devps stop PID@STARTED [--force --yes]` 停进程
    （必须带启动时间，防 PID 复用误杀）。设计见 `devps/docs/design-v0.2.md`，自检 `python3 devps/test_devps.py`。
-   **`devtools/check_docs.sh`**：校验 `docs/`、`archives/` 的相对链接可达与 `Last Validated` 唯一性；
    自检 `bash devtools/test_check_docs.sh`。
-   **`orca-server/update-orca.sh`**、**`orca-windows/update-orca.cmd`**：Orca 无头服务端的平滑更新
    （Linux ARM64 / Windows），说明见各自目录的 README。
-   **`monitoring/komari_alert.py`**：轮询 Komari 面板，节点掉线或磁盘超阈值时发一条通知，只在状态
    变化时发（去抖靠本地状态文件）。部署在监控宿主 `/opt/komari-alert/`，配 `komari-alert.timer` 每 5 分钟跑一次；
    邮件频道复用现成的 `/opt/mdp/.env.push`（`PUSH_SMTP_*` / `PUSH_TO`），不另存口令。
    `--dry-run` 只打印不发，自检 `python3 monitoring/test_komari_alert.py`。
-   **`zed-defaults/zeddefaults.swift`**：把 macOS 默认程序从 VS Code 换成 Zed，
    `zeddefaults selftest|snapshot|apply|revert|list`，说明见 `zed-defaults/README.md`。


---

## 文档

- `archives/荷兰超市宣传册下载.md`: 五家荷兰超市宣传册的抓取链路、选期陷阱与产物特征说明（配套 `archives/fetch_supermarket_folders.py`）。
- `docs/macOS SSH Key 全流程.md`: macOS 上生成/管理 SSH Key、配置 Keychain、安装公钥到服务器的完整流程。
- `docs/MagPie Web 管理界面部署记录.md`: 用 `magpie web` 把 MagPie 桌面 App 搬进浏览器、只从 Tailscale IP 访问的部署现状与运维命令。
