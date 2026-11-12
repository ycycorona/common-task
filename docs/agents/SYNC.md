# Playbook sync record

Playbook revision: 2026.11.06-1
Playbook commit: 2f095bb8aa947ebdeb67741776cbfe03970f6dca (`ycycorona/y-agent-toolkit` master)
Scoped sync: 2026.11.12-6 (INV-15 only)
Scoped playbook commit: 23bc54c80f50e4be668d635c585f5f20bf59679f
Mode: adapted；Run Tracker slot 按 pointer 记录
Synced: 2026-11-12
By: Codex · scoped INV-15 sync · unknown
Last Validated: 2026-11-12 (INV-15 only)

## What was read

- 用户要求「并逐仓适配同步」；本轮只适配决策请求规则 INV-15。
- 完整基线仍为 `2026.11.06-1`，原 Playbook commit 字段对应该基线；本轮来源为上列 scoped commit，该固定提交已合入源仓库本地 master（未推送）。
- 已读源 USAGE、同步要求、CHANGELOG 与决策规则；已读本仓库 Agent 入口、现行同步记录及相关工作流和文档约定。
- 本次为已对照固定源版本核对的范围同步，用户已授权本地合并；其余规则、既有例外和未决项保留，未进行全版本同步或产品验收。

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
| Active run tracker | `none`；[通用模型](https://github.com/ycycorona/y-agent-toolkit/blob/master/agent-playbook/tracker/run-tracker.md) 按指针登记，不复制字段、入口用法或事件格式 | 无编排规则与 Run；首次引入派发前重新同步并按实例契约注册 |

### Dropped（不适用）

- SPEC 目录、SPEC 编号与索引、views / supplements / 双向同步 —— 没有多轮需求契约；
  设计用 `devps/docs/design-v0.2.md` 式的单篇设计文档。
- Tracker、Epic / Milestone、triage / 优先级 / 归属 —— 本仓库没有 issue tracker。
- Orchestration（orchestration.md、recovery.md、平台映射）—— 聊天里直接派活，
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

- INV-15 is verified on this work branch against the locally merged source; local landing is authorized; remote publication remains pending authorization.
  Older baseline gaps are retained; the full Playbook revision is intentionally unchanged.
- Run Tracker 同步无未决项；未重写历史或引入项目注册。

## Self-check

- [x] INV-15: `AGENTS.md`; options, own recommendation, reason/trade-off, mid-task and closeout, missing information and one viable option checked.
- [x] INV-1 … INV-14 逐条核对（适用项落在 AGENTS.md / 文档规范，不适用项在 Dropped 说明原因）
- [x] 无残留的适配占位符或 TODO
- [x] adapter slot 填的是真实路径与命令
- [x] 例外记录在 SYNC.md 与 `AGENTS.md`，未混进适配副本正文
- [x] 本仓库无 SPEC 索引，此项不适用
