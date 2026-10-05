# 设计与执行说明：动态角色（RoleSpec + 混合式指挥者 + 工作流调优器 + MCP）

2026-10-06。**这份文档写给负责执行的会话**：它是自包含的，按“背景 → 设计 → 分阶段任务与验收 → 约束”读即可。
决策背景与路线对比见 [ROADMAP §5](ROADMAP.md)；项目整体见 [PROJECT_OVERVIEW](PROJECT_OVERVIEW.md)。**不做**：通用化为通用 Agent 平台、评审（review）任务类型、非 Go 语言包（全部后置）。

## 0. 目标与原则

**目标**：让角色与流程“动态”——角色是数据、下一步可由指挥模型在受限范围内提议、工作流本身可被评测驱动地优化——同时**不破坏**项目的核心卖点：可验证（成功由账本推导）、可恢复（exactly-once、付费调用不重放）、成本可控（预算）。

**四条设计原则（任何实现都不得违反）**
1. **动态的是“选择”和“数据”，不是“可执行代码”**：模型只能在已注册的有限集合里选，只能提议修改**数据**（图定义、策略），绝不执行模型写的代码。
2. **每个动态决策都是账本里的一条事件**，可重放、可审计；非法提议被拒绝并留痕，回退到确定性规则。
3. **规则优先，LLM 只处理歧义或卡住**：默认走确定性快路径（不花 token），只有多个候选或卡住时才问指挥者。
4. **用评测决定是否启用**：任何动态能力默认关闭，评测证明有收益才改默认；没有收益就保持关闭并写下结论（负结果也是产出）。

## 1. 现状（改动前必须知道的事实）

- 协调器：`src/masa/application/coordinator.py`（`WorkflowCoordinator`）。修复子图：`application/flow.py`（`FlowEngine`、`guard()` 注册、`validate()`）与 `application/workflows.py`（`FIX_V1` 数据定义）。guard 目前有 `format_only`、`primary_is`、`needs_diagnosis`、`arbitrate_due`、`rewrite_due`、`diagnosis_is`、`noop*`、`flip_to_tests`、`halted`、`always`。
- **角色元数据散落在多处**（这是 RoleSpec 要收拢的）：
  - 提示词 if 链：`src/masa/agents/protocol.py`（`instruction_for`、`validate_response`）
  - 输出 schema：`src/masa/agents/schemas.py`（`response_schema`）
  - 校验器：`src/masa/domain/proposals.py`（`validate_spec/checks/repair/test_revision/diagnosis/triage/file_proposal`）
  - 路由里的角色表：`src/masa/application/routing.py`（`ROLES`、`prefer_highest_roles`、`DEFAULT_POLICY`、`validate_policy`）
  - 配置的角色白名单：`src/masa/infrastructure/llm.py::validate_config`（`allowed={...}`）
  - 前端标签：`frontend/src/entities/status.ts`（`stageLabels`）、`features/thread/Thread.tsx`（`ROLE_ORDER`）
- 现有角色（purpose 名）：`project_planner`、`project_tester`、`project_developer`、`project_repair`、`project_test_revision`、`project_diagnoser`、`project_triage`（另有历史的 `project_test_reviewer` 等）。
- 角色调用统一经 `runtime/roles.py::RoleRuntime.call(rid, provider, purpose, values, invocation_id=…, freeze_key=…)`（exactly-once、写 `model_requested/completed/failed`）。模型选择经 `application/router.py::Router.decide` → 纯函数 `routing.route()`。
- 评测：`src/masa/bench/`（`tasks.py` 23 题、`oracle.py` 独立判官、`runner.py`、`report.py`），设置页“评测”与 `scripts/bench.py`。**调优器要复用它。**
- 测试：`python -m unittest discover -s tests`（当前 404 项 + 前端 31 项，都不调用真实模型）。

## 2. 设计

### 2.1 RoleSpec（角色即数据）
一份数据描述一个角色；注册表是**唯一**的角色元数据来源。字段刻意与 Claude Code subagent（name/description/tools + 提示词正文）和 OpenAI Agents SDK（instructions/tools/handoffs/output_type）对齐，便于导入导出。

```yaml
id: test_skeptic                    # 唯一，等同现在的 purpose（现有角色保留原 purpose 名，不改账本语义）
description: 审查冻结测试里是否有算错或与需求矛盾的期望值   # 给指挥者读的一句话，≤120 字
level: top                          # 默认等级：top | low | 具体数字；路由器的 prefer_highest_roles 由此派生
permissions: {reads: [spec, tests, failure_summary], writes: none}   # none | tests | implementation
tools: [run_tests]                  # 白名单操作；后续可含 MCP 工具名
input: [goal, acceptance, test_files, failure_summary]               # 简报里取哪些键
output_schema: {…JSON schema…}      # 同时用于约束解码（Ollama format）与校验
validator: skeptic_findings         # 指向 domain/proposals 里注册的校验函数名
max_output_tokens: 1500
when: {guard: assertion_only, after_round: 1}   # 允许被选中的条件，引用 flow.py 的 guard 与参数
prompt: |                           # 系统提示词（支持占位符）；现有角色的提示词原样迁移
  ...
```
- 存放：`src/masa/roles/` 下一角色一个 `.yaml`（或 `.md`+frontmatter，二选一，建议 YAML）；`src/masa/roles/registry.py` 负责加载、校验、查询。
- 校验：加载时检查字段齐全、`validator` 已注册、`output_schema` 合法、`when.guard` 存在、id 唯一；失败即启动报错。
- **迁移原则**：现有 7 个角色的行为必须**逐字节不变**（提示词、schema、校验器不改，只是换了来源）；现有 404 项测试全绿是迁移的硬验收。

### 2.2 指挥者（Conductor）：混合式 orchestrator–workers
```
验证失败
  → 确定性规则（guard）算出“候选动作集合”   例如 {fix_implementation, revise_tests, diagnose, rewrite, test_skeptic}
  → 只有一个明显候选：直接走（快路径，零 token）
  → 多个候选，或停滞（stall≥N、noop 反复）：调用指挥者
        输入：简报（确定性代码从账本生成，约 1.5–3k token）
        输出（schema 约束）：{"next": <候选 id>|"halt", "reason": "≤200字", "brief": "给该角色的任务说明 ≤300字"}
        校验器：next 必须 ∈ 候选集合；不能越过 Gate；步数、预算、每任务指挥调用次数（默认 ≤6）都要满足
        不合法 → 回退到确定性规则，并写 conductor_rejected 事件
  → 执行被选角色（只收到 brief + 自己的 input 键），返回结果摘要（≤200 字）+ 产物引用
  → 摘要进入下一次简报；指挥者永远不读原始代码/日志
```
- **简报（briefing）由确定性代码生成，不让 LLM 总结**：目标+验收项（截断）、当前失败归属摘要（`ownership.describe`）、前几轮摘要（`attempts.summary`）、剩余预算、已注册候选角色的 `id+description`（只含满足 `when` 的）、此前的指挥决策。
- **角色之间不“协商”**：沟通只通过结构化产物与摘要，经指挥者中转；这样可恢复、可审计、省 token。
- **指挥者自己也走路由器**：role id `project_conductor`，默认 `prefer_highest_roles`；可选级联（先 flash，决定非法/低置信再 pro），由策略开关控制。
- **token 预期**：每次决策输入约 1.5–3k、输出约 150–400；每任务 ≤6 次；总计约 1–3 万 token，量级与现有 Diagnoser 相当。必须在报告里记录“指挥者调用次数与 token”。
- 实现位置：`flow.py` 新增节点类型（如 `llm_choice`）与对应校验；`workflows.py` 的 `FIX_V1` 在多候选处引用它；`coordinator.py` 增加简报生成与执行器；新增 RoleSpec `project_conductor`（read-only，写权限 none）。
- **默认关闭**：策略项 `conductor: false`（`routing.py` 的 `DEFAULT_POLICY` 与 `validate_policy` 增加该键及 `conductor_max_calls`、`conductor_cascade`）。

### 2.3 新增可选角色（让“动态”有内容）
至少两个，先用 RoleSpec 声明，再接到 guard：
- `test_skeptic`（read-only）：对只剩断言失败的情况，逐条核对测试期望是否算错（与 Diagnoser 的 `expectation_checks` 互补；可复用其 schema 的子集）。
- `code_reviewer`（read-only）：对**已通过 Gate** 但疑似风险高的实现（例如入口复杂、边界多）给出风险点；**只产出建议，不改文件，不影响 Gate 裁决**。
这两个角色只在指挥者开启时才可能被选中；关闭时行为与现在完全一致。

### 2.4 工作流调优器（AFlow-lite，“自我优化”）
**思想**（来自 AFlow / ADAS：把工作流设计当搜索问题，评测分数即奖励），但**搜索空间是我们的数据，而不是任意代码**：
- 可调对象（都是数据）：`FIX_V1` 图定义（可选节点启用与否、边顺序）、路由策略（`start_level_by_role`、`attempts_per_level`、`max_escalations`、`prefer_highest_roles`、`stuck_after`、`conductor*`）、RoleSpec 的提示片段（只允许改标注为 `tunable` 的段落）。
- **提议者**：一个 LLM（最强模型）根据“当前最优配置 + 上轮评测结果摘要（各等级通过率、假通过、云端 token、触发的机制计数）”提出**一个**修改（JSON patch，≤3 处）。
- **校验器**：patch 应用后必须通过 `flow.validate()` 与 `validate_policy()`、以及 RoleSpec 校验；非法直接丢弃，不执行任何模型生成的代码。
- **评分**：在“快速”套餐（6 题）上跑，目标函数 = 加权通过率 − λ·每次通过的云端 token − μ·假通过率（λ、μ 可配）；每个候选最多重复 N 次取均值。
- **搜索策略**：先做逐轮淘汰（successive halving）：每代 4 个候选，淘汰一半；以后可以换 MCTS 式。**每个候选的 token/时间上限沿用评测的三层上限**，并设“整个调优预算”（云端 token、墙钟、候选数）。
- 产物：`.masa/tuning/<id>/`：每代配置、得分、对比；最优配置可一键保存为“预设”（`routing.json` 与图定义的版本化副本），报告里列出“相对基线的变化”。
- 实现位置：`src/masa/tuning/`（`space.py` 描述可调空间与 patch 校验，`proposer.py`，`search.py`，`score.py`）；`bench/runner.py` 需要支持“用覆盖配置运行”（传入策略与图定义覆盖，不写入用户设置）；命令行 `scripts/tune.py`；设置页后续再加（本阶段只要 CLI + 报告文件）。

### 2.5 MCP 服务
- 目的：生态对接。把验证与评测能力暴露给任何 MCP 客户端；并让 RoleSpec 的 `tools` 可以引用 MCP 工具（**仅声明与白名单校验，本阶段不要求实际调用外部 MCP 服务器**）。
- `src/masa/interfaces/mcp_server.py`（FastMCP，stdio），工具：`verify_project(files, checks)`（在隔离环境运行并返回 Gate 裁决与证据；**复用现有 runner 与 Gate，不另写一套**）、`run_benchmark(suite, caps)`、`route_preview(role, history)`（纯函数 `route()` 的只读预览）、`get_run_report(run_id)`。
- 安全：`verify_project` 只接受白名单检查、文件总大小上限、超时；不接受命令字符串；输出截断。
- 加 `scripts/mcp_server.py` 启动入口与 README 片段；测试用进程内调用工具函数即可，不要求起真实客户端。
- 依赖：项目目前 `dependencies=[]`。MCP 作为**可选依赖**（`pyproject` 的 optional extra），缺依赖时给出清晰报错，不影响核心。

## 3. 分阶段任务与验收（顺序固定，每阶段独立提交）

| 阶段 | 内容 | 验收（硬标准） |
|---|---|---|
| **P1 RoleSpec 注册表** | 建立 `roles/` 与 `registry.py`；把 7 个现有角色迁入；`protocol.py/schemas.py/proposals.py/routing.py/llm.py` 的角色信息改为从注册表读；前端标签从 `/api/roles`（新只读端点）读 | 现有全部后端/前端测试**不改断言**即全绿；新增注册表校验测试（缺字段、未注册 validator、重复 id、未知 guard 都在加载时报错）；新增一个测试角色，**只加 yaml 不改 Python** 即可被路由器与 `llm.validate_config` 接受 |
| **P2 指挥者** | `llm_choice` 节点、简报生成、校验器、回退、`project_conductor` RoleSpec、策略开关；`test_skeptic`、`code_reviewer` 两个可选角色 | 开关关闭时与改动前**行为完全一致**（用现有 fix-flow 测试证明）；开启后：单候选走快路径且**零指挥者调用**；多候选时调用指挥者；非法提议（越界 id、超预算、越过 Gate）被拒绝、留痕并回退；指挥者调用数受上限约束；全部用脚本化假模型测试，包含“指挥者崩溃/超时 → 回退”。账本事件：`conductor_decided`、`conductor_rejected`；报告“修复过程”里能看到 |
| **P3 调优器** | 见 §2.4 | 离线测试：patch 校验（非法 patch 被拒）、评分函数、淘汰逻辑、预算耗尽即停、结果文件结构；用假的评测运行器跑通一次完整搜索。**真实运行只做一次小规模烟测**（≤2 代、候选 2、快速套餐 ×1，严格限制 token） |
| **P4 MCP 服务** | 见 §2.5 | 工具函数的单测（含超大输入、非白名单检查被拒、超时）；用 MCP 官方 SDK 的进程内客户端或直接调用做一次集成测试（若 SDK 不可用则跳过并注明）；README 片段说明如何接入 Claude Desktop/Claude Code |
| **收口** | 更新 `docs/`：PROJECT_OVERVIEW §7 状态表、ROADMAP §5.3 状态、新增 `progress/PROGRESS_…` 进展记录；`handoff/CURRENT.md`；设置页展示角色清单（只读） | 文档如实标注“已实现/默认关闭/实测收益”；**不要写没有实测的收益** |

可选的 P5（有余力再做）：评测里加“固定图 vs 指挥者开启”的对照运行与报告（W6d），用来决定是否改默认。**这一步会花云端 token，必须先征得用户同意并设上限。**

## 4. 执行约束（务必遵守）

**项目约定**
- 所有 Markdown 放 `docs/`；核心代码**中英双语注释**（沿用现有风格：中文在前、英文在后）。
- 每个已验证的增量单独提交；**提交信息不加 `Co-Authored-By`**，提交后用 `git log -1 --format=%B | grep -ci co-authored` 确认为 0；推送到 `feature/multi-model`。
- 每个缺陷修复配一个“**修复前确认会失败**”的回归测试（用 `git stash` 单独撤销源码改动验证）。
- 不提交 `.masa/`、`.tools/`、密钥。
- 报告里不得夸大：区分“真实运行验证过 / 只被测试覆盖 / 计划中”。

**资源与成本**
- 云端调用按量计费：任何会调用真实模型的脚本，**先设 token 与时间上限**；批量评测不要重跑，重跑只跑单个任务。本阶段的大部分验证应该用假模型测试完成。
- 显卡归用户：用户说停就立刻停并卸载（`keep_alive:0`，确认 `curl localhost:11434/api/ps` 为空）；没有明确许可时优先用纯云端。
- 不要动用户正在运行的进程（API/前端）；需要起服务验证时用复制的状态目录和另一端口。

**工具使用中反复踩过的坑**（在 Windows + 这套工具链下）
- 通过 `python - <<'PYEOF'` 打补丁时，`\\n`、`\\x00` 这类转义会被折叠，写入文件后变成**真实换行或 NUL**，导致 `unterminated string literal` / `null bytes`。**补丁里用 `chr(10)`/`chr(0)`，或直接用 Edit/Write 工具；打完补丁立刻 `python -c "import ast; ast.parse(open(p,encoding='utf-8').read())"`。**
- 带单引号的字符串不要写 `\'`（会被折成裸引号），改写措辞。
- 新文件用 **Write 工具**，一个文件一次调用；不要在一条 Bash 里串多个大 heredoc（整条命令会解析失败且什么都没写）。
- 用 `nohup … &` 启动的后台任务，“Background command completed (exit 0)”只是外层 shell 退出，真正结束要等日志里的结束标记。
- 文件换行可能是 CRLF，做多行字符串替换前先 `replace('\r\n','\n')` 并在写回时还原。
- 先复现再下结论：改动前用现有测试/账本证据确认问题，不要凭印象。

**回退与安全**
- 所有动态能力默认关闭；关闭时必须与改动前行为一致（有测试证明）。
- 提议者与指挥者的输出一律视为**不可信输入**，只经校验器后才影响流程；不执行任何模型生成的代码或命令。

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| RoleSpec 迁移引入细微行为变化（提示词空白、字段顺序） | 迁移前后对每个角色做“渲染结果逐字节比对”的测试；schema 的属性顺序也比对（约束解码依赖顺序） |
| 指挥者让流程变得不可预测 | 默认关闭；规则优先；候选集合与步数/预算硬上限；非法提议回退并留痕；评测对照后再决定是否默认启用 |
| 调优器过拟合快速套餐的 6 道题 | 分数用多题多次均值；最终配置再在“标准”套餐上复核一次（需用户批准 token）；记录样本量 |
| 调优器烧钱 | 候选数、代数、整体 token/时间预算都有硬上限；到顶即停并保存已有结果 |
| MCP 依赖拖累核心 | 可选依赖；缺失时只影响 MCP 入口 |
| 改动面大、回归多 | 阶段独立提交；每阶段先跑全量测试；P1 以“不改任何现有断言”为准绳 |

## 6. 完成后请汇报

1. 每个阶段的提交号与测试数（后端/前端）。
2. 默认关闭的开关清单与开启方法。
3. 真实运行验证了什么、没验证什么（诚实标注）。
4. 遇到的新坑，补充到 `docs/` 与长期记忆。
5. 用户需要决策的事项（例如是否做 P5 的对照评测、是否改默认）。
