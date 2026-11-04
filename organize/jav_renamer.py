#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
JAV 视频文件重命名工具

该工具用于自动识别和重命名 JAV 视频文件，使其包含番号、标题和演员信息。

功能特点：
1. 自动从文件名中提取 JAV 番号
2. 使用 jvav 库获取影片详细信息（标题、演员等）
3. 生成规范化的文件名格式：番号 标题 [演员].扩展名
4. 支持处理分段视频文件（如 A/B/C 或 1/2/3）
5. 支持预览模式和执行模式
6. 自动处理文件名中的非法字符
7. 避免重复重命名已存在的文件
8. 任务完成时发送桌面通知
9. 自动跳过名为 "no_need" 的文件夹

使用方法：
1. 预览模式（默认）：
   python jav_renamer.py /path/to/videos

2. 执行模式：
   python jav_renamer.py /path/to/videos --execute

注意事项：
- 默认会等待 5 秒钟以避免 IP 被封
- 不会覆盖已存在的文件
- 会跳过隐藏文件和非视频文件
- 需要安装 jvav 库依赖

支持的文件格式：
- 视频：.mp4, .mkv, .avi, .wmv, .mov, .flv, .rmvb, .m4v
- 音频：.mp3, .wav, .flac, .opus, .m4a
- 字幕：.srt, .ass, .vtt

文件名示例：
原始文件：hhd800.com@ZRK-002.mp4
重命名后：ZRK-002 好きだと叫んじゃったから。 [吉高宁々].mp4
"""

import os
import re
import sys
import time
import argparse
import subprocess
import json
from jvav import JavDbUtil

# --- 配置 ---
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.wmv', '.mov', '.flv', '.rmvb', '.m4v'}
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.opus', '.m4a'}
SUBTITLE_EXTENSIONS = {'.srt', '.ass', '.vtt'}
SUPPORTED_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | SUBTITLE_EXTENSIONS

# 自动从获取的标题中移除的关键词列表
TITLE_EXCLUDE_KEYWORDS = [
    '【FANZA限定】',
]

# 自动从原始文件名中移除的正则表达式列表（仅在远程搜索失败时用于本地优化）
FILENAME_CLEAN_PATTERNS = [
    r'hhd800\.com@',
]

# --- 脚本核心 ---

def sanitize_filename(text):
    """移除文件名中的非法字符"""
    return re.sub(r'[\/:*?"<>|]', '-', text)

def extract_id_from_filename(filename):
    """从文件名中提取 JAV 番号"""
    # 匹配大多数标准和非标准番号 (例如: ABC-123, ab-123, abc123)
    # 支持下划线、空格、连字符等分隔符，字母1-6位，数字2-6位
    # 查找所有可能的番号，优先使用最后一个（如 hhd800.com@ZRK-002.mp4）
    matches = re.findall(r'([A-Za-z]{1,6})[-_ ]?(\d{2,6})', filename, re.IGNORECASE)
    if matches:
        last = matches[-1]
        raw = last[1]
        # 数字规范化规则：
        # - 原始数字长度 >= 3: 清除所有前导0（不再补零）
        # - 原始数字长度 < 3: 去前导0后左侧补零至3位
        stripped = raw.lstrip('0')
        # 修正规则：原始位数 >= 4 则去前导0；原始位数 <= 3 则左侧补足为3位
        if len(raw) >= 4:
            num = stripped or '0'
        else:
            num = (stripped or '0').zfill(3)
        return f"{last[0].upper()}-{num}"
    return None

def send_completion_notification(success=True, message="JAV重命名任务已完成"):
    """发送任务完成通知"""
    try:
        # 构造通知数据
        notification_data = {
            "type": "agent-turn-complete",
            "last-assistant-message": message,
            "input_messages": []
        }
        
        # 调用codex_notify.py发送通知
        script_path = os.path.join(os.path.dirname(__file__), "codex_notify.py")
        if os.path.exists(script_path):
            print(f"正在发送桌面通知: {message}")
            result = subprocess.run([
                sys.executable, 
                script_path, 
                json.dumps(notification_data)
            ], capture_output=True, text=True)
            if result.returncode != 0:
                print(f"通知发送失败 (exit code {result.returncode}): {result.stderr}")
        else:
            print("警告: 未找到 codex_notify.py，无法发送通知。")
    except Exception as e:
        print(f"发送通知时发生异常: {e}")
        pass

def main(dry_run, target_directory):
    """脚本主函数"""
    print("--- JAV 文件重命名工具 ---")
    if dry_run:
        print("**模式: 预览模式 (Dry Run)。将只显示计划的更改，不执行任何操作。**")
    else:
        print("**模式: 执行模式 (Execute)。将实际重命名文件。**")
        time.sleep(3) # 在执行前给用户一个取消的机会

    if not os.path.isdir(target_directory):
        print(f"错误: 目录 '{target_directory}' 不存在或不是一个有效的目录。")
        send_completion_notification(False, f"JAV重命名任务失败：目录 '{target_directory}' 不存在")
        return

    print(f"扫描目录: {target_directory}\n")

    jav_db = JavDbUtil()
    processed_count = 0
    error_count = 0
    
    # 递归扫描所有子文件夹
    for root, dirs, files in os.walk(target_directory):
        # 排除名为 no_need 的文件夹
        if 'no_need' in dirs:
            dirs.remove('no_need')
            
        files_in_directory = sorted(files)
        for filename in files_in_directory:
            original_path = os.path.join(root, filename)
            # 跳过隐藏/系统文件（如 .DS_Store、AppleDouble 文件 ._ 开头等）
            if filename.startswith('.'):
                continue
            # 跳过目录和非文件项
            if not os.path.isfile(original_path):
                continue
            # 检查是否为支持的文件（视频、音频、字幕）
            _, ext = os.path.splitext(filename)
            if not ext or ext.lower() not in SUPPORTED_EXTENSIONS:
                continue
            jav_id = extract_id_from_filename(filename)
            if not jav_id:
                continue

            # 检测分段标识（如 A/B/C/D 或 1/2/3/4），位置独立于番号出现形式
            part_suffix = ''
            base_no_ext, _ext_dummy = os.path.splitext(filename)
            tokens = re.findall(r'([A-Za-z0-9]+)', base_no_ext)
            if tokens:
                last_token = tokens[-1]
                if re.fullmatch(r'(?i)[A-D]|[1-4]', last_token):
                    part_suffix = f' {last_token.upper()}'

            print(f"[处理] {filename}")
            print(f" ├─ 提取番号: {jav_id}")

            try:
                # 使用 jvav 库获取信息
                code, info = jav_db.get_av_by_id(jav_id, is_nice=False, is_uncensored=False)
                
                if code != 200 or not info or not info.get('title'):
                    print(" └─ 结果: 未找到远程信息，尝试本地优化...")
                    # 进行本地文件名优化
                    optimized_base = base_no_ext
                    for pattern in FILENAME_CLEAN_PATTERNS:
                        optimized_base = re.sub(pattern, '', optimized_base)
                    
                    if optimized_base == base_no_ext:
                        print(" └─ 结果: 本地无优化建议，跳过.\n")
                        error_count += 1
                        continue
                    
                    new_filename = f"{optimized_base}{ext}"
                else:
                    # 分支：成功获取远程信息，构建标准格式
                    # 清理标题
                    raw_title = info.get('title', '')
                    # 优化：移除指定的关键词
                    for keyword in TITLE_EXCLUDE_KEYWORDS:
                        raw_title = raw_title.replace(keyword, '')
                    
                    clean_title = sanitize_filename(raw_title.strip())
                    # 优化：如果标题过长，进行截断
                    if len(clean_title) > 50:
                        clean_title = clean_title[:50] + "..."

                    # 构建新文件名，追加分段标识
                    stars = info.get('stars') or []
                    if stars:
                        # 只取第一个演员并做非法字符清理
                        first_actor = stars[0]
                        name = first_actor.get('name') if isinstance(first_actor, dict) else str(first_actor)
                        actor_name = sanitize_filename(name)
                        # 若标题已以" 空格+演员"结尾，则不再添加方括号演员
                        if clean_title.endswith(f' {actor_name}'):
                            new_filename = f"{jav_id} {clean_title}{part_suffix}{ext}"
                        else:
                            # 分段标识加在片名后、演员名前
                            new_filename = f"{jav_id} {clean_title}{part_suffix} [{actor_name}]{ext}"
                    else:
                        new_filename = f"{jav_id} {clean_title}{part_suffix}{ext}"
                
                new_path = os.path.join(root, new_filename)

                print(f" └─ 计划重命名为: {new_filename}")

                if not dry_run:
                    if os.path.exists(new_path):
                        print(" └─ 操作: 失败 - 目标文件名已存在，为避免覆盖，已跳过.\n")
                        error_count += 1
                        continue
                    os.rename(original_path, new_path)
                    print(" └─ 操作: 重命名成功！\n")
                    processed_count += 1
                else:
                    print("") # 在预览模式下打印一个换行
                    processed_count += 1

            except Exception as e:
                print(f" └─ 错误: 查询或处理 {jav_id} 时发生错误: {e}\n")
                error_count += 1
            
            # 礼貌地等待，避免IP被封
            time.sleep(3)

    print("--- 所有操作完成 ---")
    # 发送完成通知
    if dry_run:
        send_completion_notification(True, f"JAV预览完成：处理{processed_count}个文件，{error_count}个错误")
    else:
        send_completion_notification(True, f"JAV重命名完成：成功处理{processed_count}个文件，{error_count}个错误")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JAV 文件重命名工具")
    parser.add_argument("directory", default=".", nargs="?", help="要扫描的目录路径 (默认为当前目录)")
    parser.add_argument("--execute", action="store_true", help="执行实际的文件重命名操作，否则只进行预览。" )
    args = parser.parse_args()

    main(dry_run=not args.execute, target_directory=args.directory)