# Playbook sync record

Playbook revision: `2026.10.27-7`
Playbook commit: `y-agent-toolkit/agent-playbook` 工作副本（2026-11-02 读取，VERSION `2026.10.27-7`）
Mode: adapted
Synced: 2026-11-02
By: pi · 文档规范改造 · `auto/deepseek-v4.1-flash`
Last Validated: 2026-11-02

## What was read

- Playbook: `README.md`、`USAGE.md`、`agents/workflow.md`、`agents/domain.md`、
  `documentation/documentation-conventions.md`、`templates/AGENTS.template.md`、
  `templates/SYNC-RECORD.template.md`
- Target: 根 `AGENTS.md`、`docs/*.md`、`archives/*.md`、`devps/docs/design-v0.2.md`、
  `README.md`、`test_*.py` / `test_*.sh`

## Differences

### Kept（不变式）

| 不变式 | 本地位置 |
| --- | --- |
| INV-1 四类权威互不替代 | `AGENTS.md`（Read first / 文档规范"不把契约藏在普通文档里"） |
| INV-2 / INV-3 SPEC 冻结、agent 不拿实现改约定 | `docs/agents/documentation-conventions.md#不把契约藏在普通文档里` |
| INV-4 实现授权 | `AGENTS.md#Workflow and authorization` |
| INV-5 / INV-7 / INV-8 工单与 Epic / Milestone | 不适用，见 Dropped |
| INV-6 一次一个写者 | `AGENTS.md#Workflow and authorization`（只动本任务文件） |
| INV-9 已验证要写命令与观察结果 | `AGENTS.md#Task closeout` |
| INV-10 提交带真实归因 | `AGENTS.md#Validation and Git` |
| INV-11 关键决策落文档 | `docs/agents/documentation-conventions.md`（决策进设计文档/MagPie 式记录） |
| INV-12 / INV-13 / INV-14 | 不适用，见 Dropped |
| Last Validated、文档角色与最小元数据 | `docs/agents/documentation-conventions.md` |

### Rewritten（adapter slots）

| playbook 位置 | 实例化 | 依据 |
| --- | --- | --- |
| 验证层级 | 混合（B 自动 + A 手工）：`python3 <对应 test_*.py>`、`python3 devps/test_devps.py` | `AGENTS.md#Validation and Git` |
| 文档目录 | `docs/`（操作记录、研究）、`archives/`（一次性证据）、`devps/docs/`（设计文档） | 现有目录结构 |
| SPEC 目录、状态索引、决定日志、tracker | 无（本仓库不维护，见 Dropped） | 无数据库/无 tracker 的脚本集合 |
| Closeout 的"工单"行 | 换成"提交"行，只列 Git 动作 | 无 issue tracker |
| 工作语言 | 中文文档与提交说明，代码与标识符英文 | `AGENTS.md#Development principles` |

### Dropped（不适用）

- SPEC 目录、SPEC 编号与索引、views / supplements / 双向同步 —— 没有多轮需求契约；
  设计用 `devps/docs/design-v0.2.md` 式的单篇设计文档。
- Tracker、Epic / Milestone、triage / 优先级 / 归属 —— 本仓库没有 issue tracker。
- Orchestration（orchestration.md、recovery.md、run-repository.md、平台映射）—— 聊天里直接派活，
  没有 run 目录与多 agent 监督循环。
- Discovery record、PRD、roadmap、release / incident 文档 —— 无对应物件。

### Repository-only rules（未写进适配副本）

- `devps/` 零第三方依赖、Python 3.11+、`test_*.py` assert 风格、媒体产物不入 Git：
  `AGENTS.md#Development principles` / `#Data, search, and changes`。
- LSP 优先（`basedpyright-langserver`）：`AGENTS.md#Language Server / LSP`。

### Exceptions

| 冲突 | 决定 | 记录位置 |
| --- | --- | --- |
| playbook 要求 `docs/agents/` 放全部适配副本 | 本仓库只放文档规范一份；workflow / Git / closeout 的不变式直接写在根 `AGENTS.md` | 本文件 |
| 历史文档缺 `Last Validated` 与最小元数据 | 不批量重写，下次触碰时补 | `docs/agents/documentation-conventions.md#既有文档的过渡` |
| `archives/irm-avlabo-starter.md` 自称"唯一启动依据" | 用户决定：按归档保留，不涉设计文档；只把措辞改成一次性方案快照 | `archives/irm-avlabo-starter.md` 头部 |

## Open items

- 未推送的 3 条历史提交净效果为零（新增→移动→删除 `pi-stream-fold/`），是否整理历史需授权。

## Self-check

- [x] INV-1 … INV-14 逐条核对（适用项落在 AGENTS.md / 文档规范，不适用项在 Dropped 说明原因）
- [x] 无残留的适配占位符或 TODO
- [x] adapter slot 填的是真实路径与命令
- [x] 例外记录在 SYNC.md 与 `AGENTS.md`，未混进适配副本正文
- [x] 本仓库无 SPEC 索引，此项不适用
