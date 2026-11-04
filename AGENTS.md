# AGENTS.md

本仓库是个人工具脚本集合（macOS 本机使用）：音视频提取、重命名、下载辅助、通知钩子等独立脚本，以及 `devps/`（孤儿开发进程检测，macOS 专用，仅标准库）。没有数据库、没有常驻服务、不管理平台状态。各脚本自带文档与测试；`devps/` 的设计文档见 [devps/docs/design-v0.2.md](devps/docs/design-v0.2.md)。

## Read first

1. 当前用户指令；确认范围、依赖与影响面。
2. 与任务相关的脚本源码、其头部注释、测试文件（`test_*.py` / `test_*.sh`）；`devps/` 任务先读 [design-v0.2.md](devps/docs/design-v0.2.md) §0。
3. 当前 Git 状态；只动与本任务相关的文件。
4. 改动前先核实事实与推断；不复制无关仓库的实现，参考时须核对接口与语义。

开发者上下文：任务需要了解 owner、机器或跨仓库 Agent 约定时，读 `../tech_note_ai/05-Agent-Context/README.md`（各机器上都是本仓库的同级目录，先 `git pull --ff-only`），只按任务打开它指向的笔记；对该仓库只读。

## Workflow and authorization

- 只实现用户明确要求的范围。验收标准、issue 指派或"就绪"状态本身不授权额外重构、删除或"顺手改进"。
- 小改动用最小文档：单文件脚本的用法写在脚本头部注释或根 README；不为流程完整性创建没有独立价值的文档。新建或改动 `docs/`、`archives/` 里的文档时按 [docs/agents/documentation-conventions.md](docs/agents/documentation-conventions.md)（角色、最小元数据、`Last Validated`）。
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
- 文档改动检查链接/路径有效与 `git diff --check`；`docs/`、`archives/` 的链接与 `Last Validated` 由 `bash check_docs.sh` 机器校验（自检：`bash test_check_docs.sh`）。
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

- 每个改过文件、跑过命令或结束任务的回合，都用下面几行收尾。写成普通 Markdown、顶格：**加粗标签** + 一级列表（`-` 项；下一步用数字列表），回复里不要套代码块、不要缩进。

  - **状态**：做完了什么，≤2 行
  - **证据**：`<命令>` → 观察到的结果；未验证：没检查到的东西，或"无"
  - **提交**：每个 Git 动作一行，`<commit 哈希或分支> · <动作> · 已提交 | 待授权 | 仅建议`；没有 Git 动作时整行省略
  - **下一步**：每个选项一行普通数字列表，`N. [<类型>] <选项> — <理由>`，推荐项前缀 `★ 推荐`；不要写成 `- N.`（会被终端渲染成 a/b/c）

  - **证据**只写命令和观察到的结果，不写退出码了事。
  - **提交**只列 Git 相关动作（提交、push、merge、tag）。本仓库没有 issue tracker，工单信息并入此项。`已提交` 指已落库并用 `git log` / `git status` 复核过；未经授权的动作算 `待授权`；只提议、不自己执行的算 `仅建议`。
  - **下一步**给 2–3 个选项，每项按动作打标签：`[实施]` 会改东西、`[验证]` 检查已完成的工作、`[调查]` 只查不改、`[决策]` 需要用户拍板、`[收尾]` 收手或搁置；必须有且只有一个 `★ 推荐`。需要授权的选项（push、merge、删除、付费调用）结尾加（需授权），并且只能作为选项出现。没有待办时写 `下一步：无需后续`。
  - 收尾必须覆盖：改动文件、运行过的检查命令及结果、提交哈希、遗留问题、有意未提交的文件。

## Playbook sync

- `docs/agents/` 按 `y-agent-toolkit/agent-playbook` 适配而来：意图与不变式来自 playbook，路径、命令与例外是本仓库的真实内容。
- 适配了什么、丢掉了什么、有哪些例外：见 [docs/agents/SYNC.md](docs/agents/SYNC.md)，其中记录 synced revision。
- 规则问题或再次同步走 playbook 的 `USAGE.md`；先比对 synced revision 与 playbook `VERSION`，副本过期时先报告，不直接照 playbook 覆盖本仓库规则。
