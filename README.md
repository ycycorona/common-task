# Common Task Scripts

这是一个包含常用 Python 脚本的项目，主要用于文件处理、通知和媒体转换。

## 系统要求 (System Requirements)

本项目脚本主要在 macOS 环境下开发，部分功能依赖以下系统级命令行工具：

1.  **FFmpeg**: 用于所有音视频处理（音频提取、静音替换、格式转换）。
    ```bash
    brew install ffmpeg
    ```
2.  **terminal-notifier**: 用于在脚本任务完成后发送 macOS 桌面通知。
    ```bash
    brew install terminal-notifier
    ```
3.  **sips**: macOS 系统的脚本化图像处理系统，用于 HEIC 到 JPEG 的批量转换（macOS 自带）。
4.  **SetFile**: macOS 命令行工具，用于修改和保留文件的创建日期（通常随 Xcode Command Line Tools 安装）。

## 安装方式

```bash
git clone <repository-url>
cd common-task
pip install -r requirements.txt
```

## 脚本列表

### 1. JAV 整理工具 (`jav_renamer.py`)

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
python jav_renamer.py /path/to/videos

# 执行模式 - 实际重命名文件
python jav_renamer.py /path/to/videos --execute
```


---

### 2. Hugging Face 仓库下载工具 (`hf_snapshot_download.py`)

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
python3 hf_snapshot_download.py kotoba-tech/kotoba-whisper-v2.0-faster

# 推荐用法 - 不使用符号链接 + 断点续传
python3 hf_snapshot_download.py kotoba-tech/kotoba-whisper-v2.0-faster \
  -o ./kotoba-whisper --repo-type model --no-symlinks --resume

# 只下载部分文件（如模型权重和配置）
python3 hf_snapshot_download.py kotoba-tech/kotoba-whisper-v2.0-faster \
  -o ./kotoba --no-symlinks --resume \
  --include "*.bin" "*.json"

# 下载数据集
python3 hf_snapshot_download.py username/dataset-name \
  --repo-type dataset -o ./my-dataset

# 使用 token 下载私有仓库
python3 hf_snapshot_download.py private-org/private-model \
  --token hf_xxxxxxxxxxxx --no-symlinks
```

---

### 3. 音频静音替换工具 (`replace_audio_silence.py`)


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
python replace_audio_silence.py /input/dir /output/dir

# 递归处理所有子文件夹
python replace_audio_silence.py /input/dir /output/dir --recursive

# 预览模式
python replace_audio_silence.py /input/dir /output/dir --dry-run
```

---

### 4. Codex 通知工具 (`codex_notify.py`)

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
python codex_notify.py '{"type": "agent-turn-complete", "last-assistant-message": "任务已完成"}'
```

---

### 5. DMM 预览视频下载工具 (`download_dmm_preview.py`)

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
all_proxy=socks5h://127.0.0.1:1080 python download_dmm_preview.py SSIS-001
```

---

### 6. 辅助 Shell 脚本

项目中还包含以下用于快速处理媒体文件的 Shell 脚本：

-   **`heic_batch_convert.sh`**: 批量将 HEIC 图片转换为 JPEG 或 PNG 格式。
-   **`video2aac.sh`**: 批量从视频中提取音频并转换为 AAC 格式（256k 码率）。支持单个文件或递归处理整个目录，自动识别常见视频格式（mp4/mkv/mov/avi/flv/webm/m4v/wmv），跳过已存在的 AAC 文件避免重复转换。
-   **`video2flac.sh` / `video2opus.sh`**: 快速从视频中提取音频并转换为高压缩率的 FLAC 或 Opus 格式（适配 OpenAI Whisper 或其他 AI 音频转录工具）。`video2opus.sh` 会跳过隐藏文件/目录。
-   **`video_clip_to_wav.sh`**: 从视频中按指定的起止时间极速截取片段，并提取其音频。音频输出支持格式为 Whisper 友好的 WAV (16kHz 单声道 PCM) 或高质量 VBR 的 MP3。支持自动识别省略参数以输出至原目录。
-   **`video_split_to_flac.sh`**: 将长视频或音频文件按指定时长切分（默认 10 分钟），并自动将切分后的分段音频转换为 FLAC（默认）或 MP3 格式。
-   **`extract_mp3.sh`**: 从视频文件中提取音频并转换为 MP3 格式。支持传入多个文件顺序处理，自动根据输入文件名生成 `.mp3` 输出。支持丰富的参数自定义（码率、采样率、声道、音频流选择、起止时间截取等）。自动检测输入音频是否为 MP3，若是则直接复制音轨避免重复编码。运行时显示当前处理进度（`[N/TOTAL | PERCENT%]`），并自动过滤 ffmpeg 正常日志仅保留警告和报错，最后输出处理统计（成功/失败/总数）。单个文件失败不会中断后续任务。
-   **`run_whisper_screen.sh`**: VideoCaptioner 转录任务管理器。使用 screen 在后台运行转录任务，固定复用单一 session 和 window，支持任务检测、避免重复启动和 Attached 状态保护。

---

## 文档

- `docs/macOS SSH Key 全流程.md`: macOS 上生成/管理 SSH Key、配置 Keychain、安装公钥到服务器的完整流程。
