# AGENTS.md

本仓库是个人工具脚本集合（macOS 本机使用）：音视频提取、重命名、下载辅助、通知钩子等独立脚本，以及 `devps/`（孤儿开发进程检测，macOS 专用，仅标准库）。没有数据库、没有常驻服务、不管理平台状态。各脚本自带文档与测试；`devps/` 的设计文档见 [devps/docs/design-v0.2.md](devps/docs/design-v0.2.md)。

## Read first

1. 当前用户指令；确认范围、依赖与影响面。
2. 与任务相关的脚本源码、其头部注释、测试文件（`test_*.py` / `test_*.sh`）；`devps/` 任务先读 [design-v0.2.md](devps/docs/design-v0.2.md) §0。
3. 当前 Git 状态；只动与本任务相关的文件。
4. 改动前先核实事实与推断；不复制无关仓库的实现，参考时须核对接口与语义。

## Workflow and authorization

- 只实现用户明确要求的范围。验收标准、issue 指派或"就绪"状态本身不授权额外重构、删除或"顺手改进"。
- 小改动用最小文档：单文件脚本的用法写在脚本头部注释或根 README；不为流程完整性创建没有独立价值的文档。
- 跨文件或引入新依赖的改动，先在响应里说明取舍再动手。

## Development principles

- 代码、注释、标识符用英文；面向用户的输出、提交说明与文档用中文。
- 优先标准库；`devps` 明确零第三方依赖。新脚本默认 Python 3.11+（`python3`），shell 用 bash。
- 非平凡逻辑（分支、循环、解析、信号路径）必须留一个可运行检查：assert 风格的 `test_*.py` 或 `__main__` 自检，不用测试框架。
- 脚本对不存在输入、空输出、外部命令失败要有可见行为（报错退出非零），不得静默成功。

### Language Server / LSP

改源码时优先用 LSP 语义工具（定义跳转、引用查找、诊断、安全重命名），不要只用文本搜索。修改代码后、任务收尾前检查 LSP 诊断。

配置的语言服务器：
- Python: `basedpyright-langserver --stdio`

## Data, search, and changes

- 搜索用 `rg`，限定相关目录；全仓搜索排除 `.git/`、`.venv/`。
- 下载产物、媒体文件、临时输出不入 Git；确认 `.gitignore` 覆盖后再运行会产生文件的脚本。
- 不 clean、不 reset、不 stage 无关的用户改动；提交前检查完整 `git status`。

## Validation and Git

- 只跑与改动面相关的检查：`python3 <对应 test_*.py>`；`devps` 跑 `python3 devps/test_devps.py`。
- 文档改动检查链接/路径有效与 `git diff --check`。
- 已验证的改动用简洁的 Conventional Commit（`feat(xxx): …` / `fix(xxx): …`），正文列要点。未经授权不 push、不 merge、不改写历史、不动 remote。
- AI 参与的提交附加真实尾注，无法得知的值写 `unknown`，不猜：

  ```text
  AI-Model: <实际模型或 unknown>
  AI-Harness: <实际 harness 或 unknown>
  AI-Agent-Role: <当前角色或任务>
  AI-Task: <当前任务标题>
  AI-Session: <稳定会话 id 或 unknown>
  ```

## Task closeout

- 收尾时报告：改动文件、运行过的检查命令及结果、提交哈希、遗留问题、有意未提交的文件。
