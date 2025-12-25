#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Hugging Face 仓库快照下载工具

这是一个基于 huggingface_hub.snapshot_download() 的命令行工具，
用于快速下载 Hugging Face 上的模型、数据集或 Space。

功能特点：
1. 支持下载模型 (model)、数据集 (dataset) 和 Space
2. 支持指定分支/标签/commit (revision)
3. 支持文件过滤 (include/exclude patterns)
4. 支持断点续传 (--resume)
5. 支持私有仓库和门控仓库 (通过 token)
6. 支持自定义缓存目录
7. 支持禁用符号链接 (推荐在 macOS/Windows 上使用 --no-symlinks)

使用示例：

1. 基本用法 - 下载完整模型仓库：
   python3 hf_snapshot_download.py kotoba-tech/kotoba-whisper-v2.0-faster

2. 推荐用法 - 不使用符号链接 + 支持断点续传：
   python3 hf_snapshot_download.py kotoba-tech/kotoba-whisper-v2.0-faster \
     -o ./kotoba-whisper-v2.0-faster --repo-type model --no-symlinks --resume

3. 只下载部分文件（例如模型权重和配置文件）：
   python3 hf_snapshot_download.py kotoba-tech/kotoba-whisper-v2.0-faster \
     -o ./kotoba --repo-type model --no-symlinks --resume \
     --include "*.bin" "*.json"

4. 下载数据集：
   python3 hf_snapshot_download.py username/dataset-name \
     --repo-type dataset -o ./my-dataset

5. 使用 token 下载私有仓库：
   python3 hf_snapshot_download.py private-org/private-model \
     --token hf_xxxxxxxxxxxx --no-symlinks

注意事项：
- 默认会使用符号链接，在 macOS/Windows 上建议使用 --no-symlinks
- 默认输出目录为当前目录下的 ./<仓库名>
- 支持从环境变量 HF_TOKEN 或 HUGGINGFACE_TOKEN 读取 token
- 网络中断时可以使用 --resume 继续下载
"""

import argparse
import os
import sys
from pathlib import Path

from huggingface_hub import snapshot_download


def build_parser() -> argparse.ArgumentParser:
    """
    构建命令行参数解析器
    
    Returns:
        配置好的 ArgumentParser 实例
    """
    p = argparse.ArgumentParser(
        description="使用 huggingface_hub.snapshot_download() 下载 Hugging Face 仓库快照"
    )
    
    # 必选参数：仓库 ID
    p.add_argument(
        "repo_id", 
        help="仓库 ID，格式：用户名/仓库名，例如 kotoba-tech/kotoba-whisper-v2.0-faster"
    )
    
    # 输出目录
    p.add_argument(
        "-o",
        "--output-dir",
        default=None,
        help="文件输出目录。默认：当前目录下的 ./<仓库名>",
    )
    
    # 仓库类型
    p.add_argument(
        "--repo-type",
        default="model",
        choices=["model", "dataset", "space"],
        help="仓库类型。默认：model",
    )
    
    # 版本/分支
    p.add_argument(
        "--revision",
        default=None,
        help="指定分支名、标签或 commit hash。默认使用仓库的默认分支",
    )
    
    # 访问令牌
    p.add_argument(
        "--token",
        default=None,
        help="Hugging Face 访问令牌，用于私有或门控仓库。未指定时会尝试使用 HF_TOKEN 环境变量或已缓存的登录信息",
    )
    
    # 缓存目录
    p.add_argument(
        "--cache-dir",
        default=None,
        help="自定义缓存目录（可选）",
    )
    
    # 断点续传
    p.add_argument(
        "--resume",
        action="store_true",
        help="启用断点续传，继续未完成的下载",
    )
    
    # 禁用符号链接
    p.add_argument(
        "--no-symlinks",
        action="store_true",
        help="不在输出目录使用符号链接（推荐在 macOS/Windows 上使用）",
    )
    
    # 文件包含模式
    p.add_argument(
        "--include",
        nargs="*",
        default=None,
        help='仅下载匹配的文件模式，例如：--include "*.json" "model.bin"',
    )
    
    # 文件排除模式
    p.add_argument(
        "--exclude",
        nargs="*",
        default=None,
        help='忽略匹配的文件模式，例如：--exclude "*.md" "README*"',
    )
    
    # 静默模式
    p.add_argument(
        "--quiet",
        action="store_true",
        help="减少输出信息",
    )
    
    return p


def main() -> int:
    """
    主函数：解析参数并执行下载
    
    Returns:
        退出代码：0 表示成功，1 表示失败
    """
    args = build_parser().parse_args()

    # 从仓库 ID 中提取仓库名（取最后一个 / 之后的部分）
    repo_name = args.repo_id.split("/")[-1]
    
    # 确定输出目录：优先使用用户指定的，否则使用默认的 ./<仓库名>
    out_dir = Path(args.output_dir or f"./{repo_name}").expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 获取 token：优先使用命令行参数，其次尝试环境变量
    token = args.token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")

    # 打印下载信息（除非启用了静默模式）
    if not args.quiet:
        print(f"仓库      : {args.repo_id} ({args.repo_type})")
        if args.revision:
            print(f"版本      : {args.revision}")
        print(f"输出目录  : {out_dir}")
        if args.include:
            print(f"包含模式  : {args.include}")
        if args.exclude:
            print(f"排除模式  : {args.exclude}")
        if args.cache_dir:
            print(f"缓存目录  : {args.cache_dir}")
        print("正在下载...")

    try:
        # 调用 huggingface_hub 的 snapshot_download 函数
        snapshot_download(
            repo_id=args.repo_id,              # 仓库 ID
            repo_type=args.repo_type,          # 仓库类型
            revision=args.revision,            # 版本/分支
            local_dir=str(out_dir),            # 本地输出目录
            local_dir_use_symlinks=(not args.no_symlinks),  # 是否使用符号链接
            cache_dir=args.cache_dir,          # 缓存目录
            token=token,                       # 访问令牌
            resume_download=args.resume,       # 是否断点续传
            allow_patterns=args.include,       # 包含模式
            ignore_patterns=args.exclude,      # 排除模式
        )
    except Exception as e:
        # 错误处理：打印错误信息和问题排查建议
        print(f"\n错误: {e}", file=sys.stderr)
        print(
            "\n问题排查:\n"
            "  - 私有/门控仓库: 使用 --token hf_xxx 或设置 HF_TOKEN 环境变量\n"
            "  - 网络问题: 使用 --resume 重新运行以继续下载\n"
            "  - 仓库类型错误: 尝试 --repo-type dataset 或 --repo-type space\n"
            "  - 仓库不存在: 检查仓库 ID 是否正确\n",
            file=sys.stderr,
        )
        return 1

    # 下载成功
    if not args.quiet:
        print("下载完成！")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
