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

### 2. 音频静音替换工具 (`replace_audio_silence.py`)

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

### 3. Codex 通知工具 (`codex_notify.py`)

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

### 4. 辅助 Shell 脚本

项目中还包含以下用于快速处理媒体文件的 Shell 脚本：

-   **`heic_batch_convert.sh`**: 批量将 HEIC 图片转换为 JPEG 或 PNG 格式。
-   **`video2flac.sh` / `video2opus.sh`**: 快速从视频中提取音频并转换为高压缩率的 FLAC 或 Opus 格式（适配 OpenAI Whisper 或其他 AI 音频转录工具）。
-   **`video_split_to_flac.sh`**: 将长视频按时长切分（默认 10 分钟），并自动提取切分后的音轨为 FLAC 格式。
