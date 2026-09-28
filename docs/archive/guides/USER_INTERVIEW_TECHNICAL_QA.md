# MASA 面试技术细节：20 问与可落地设计

更新：2026-09-28。用途：简历技术准备、架构答辩与后续实现参考。

本文按“一周内可落地的增量”回答附件问题。**当前实现**来自源码；**建议方案**用于补全近期设计，不表示已经实现。昂贵能力只说明取舍，不展开。示例 ID 和阈值不是运行数据，没有编造性能收益。

阅读依据：[当前状态](../../STATUS_PROJECT.md)、[接续记录](../../handoff/CURRENT.md)、[当前路线](../../PLAN_PRIORITY_ROADMAP.md)、[代码与上下文设计](../design/DESIGN_INTELLIGENCE_CONTEXT.md)、[自适应优化设计](../design/DESIGN_ADAPTIVE_OPTIMIZATION.md)。部分状态页前文尚未同步，需结合页末更新与实际代码：目前已存在真实 Planner/Tester 规划和 Developer 多文件提案链路，但尚未组成同一张自主修复角色图。

## 一周范围与验收

以现有多文件生成、人审、快照和 Go 检查为基础，按一个开发者约五个工作日估算，包含必要测试；不是交付保证。若接入成本超出预计，先缩减补证功能，保证失败反馈链路可演示。

| 时间 | 增量 | 验收 |
|---|---|---|
| 第一天 | 提取失败日志、保留全文引用、证据去重 | 大日志不撑爆输入，错误位置与退出码保留 |
| 第二天 | Run 内文件级原文有效性检查与简单依赖传播 | 改 A 后相关材料失效；B 原文可重新核验 |
| 第三天 | read_symbol/read_failure 两种有界补证 | 最多两轮，重复停止，实际生成调用经过 ContextBuilder |
| 第四天 | 失败反馈生成关联草稿，复用人审与新 Run 验证 | 历史失败保留，新代码重新检查 |
| 第五天 | 小任务集与中断场景验收 | 记录真实通过数、Token、耗时和限制 |

主线：**失败结果 → 关键日志 → 当前实现 → 新草稿 → 人审 → 新 Run 验证**。

不把单项可能一周完成的功能全部堆入这一周。并发增量索引、运行中 GraphPatch、完整语义 Reviewer、跨平台执行、自优化和模型路由均后置。本文只形成面试材料，不改变项目实际开发计划或开始编码。

## 1. DAG、状态机、Scheduler 如何分工？

**实现状态：基础已实现。**

**代码位置：** [domain.py](../../../src/masa/domain/models.py)、[workflow.py](../../../src/masa/runtime/graph.py)、[runtime.py](../../../src/masa/runtime/engine.py)、[sqlite.py](../../../src/masa/infrastructure/store.py)。

### 当前实现

`Node` 字段为 `id/type/dependencies/trigger/operation/role`；`Graph` 包含 `nodes/version/policy_version`。边通过节点的 dependencies 表达，不另外存 Edge 类。三种节点共用数据类，在 Runtime 中按 type 分支执行：agent 调 AgentLoop，tool 调执行器，gate 调确定性验收函数。

`ready_nodes(graph, states)` 只选择 pending 且依赖满足的节点。普通节点需要 all_succeeded；Gate 使用 all_terminal 收集失败结果。Ready 目前不是数据库状态，返回列表中的第一个节点被启动，因此图中并列节点也按顺序执行。

Run 保存任务状态，Step 保存逻辑节点状态，Attempt 保存一次实际尝试。开始执行先落盘 Attempt；完成后提交产物与状态。图结构和状态通过 `(run_id, node_id)` 对应。当前图版本保存在运行数据中，不代表已有多版本改图事务。

依赖满足只是可调度条件；模型/工具预算分别在调用记账入口检查，deadline 在运行循环和工具入口检查。预算不足时终止为失败，不会把节点卡在 Ready。当前不是完整资源调度器，也没有“测试失败自动重试直到成功”的策略。

### 建议方案与例子

调度器在启动 Attempt 前做预算预检查，实际扣费入口仍原子检查。RetryPolicy 决定可否重试，状态机校验转换，Scheduler 安排新 Attempt；不要让状态枚举承担策略判断。

网络超时可以按配置有限重试；测试断言失败应生成修改方案，不能重复运行同一版本期待变绿。

**面试回答：** DAG 管依赖，状态机记录生命周期，Scheduler 结合依赖、状态和执行约束选择下一步；当前采用单 owner 串行执行，优先保证证据与恢复一致性。

## 2. 失败后如何调整流程？

本周不实现 GraphPatch，使用关联新 Run：Run A 的 S1 检查失败 → 引用失败结果生成新草稿 → 人审 → Run B 的 S2 重新验证。父 Run 保持终态和历史，不能把旧失败改成成功。

反馈请求设置唯一 ID 防止重复点击，提案绑定需求和基线；最多提供一轮明确触发的修复。新 Run 独立管理预算，页面汇总父子 Run 实际用量。简历写“失败反馈驱动的关联任务修复”，不写“运行中动态重规划”。

这避免同时解决 Running 节点改输入、并发 patch 冲突和 Gate 历史覆盖等问题。当前图仍使用固定模板，DAG 的价值体现在依赖与执行状态管理。

## 3. Evidence Gate 怎样判断任务成功？

**实现状态：工具级 Gate 已实现，完整语义审查门禁待实现。**

**代码位置：** `Runtime._gate()`、`Tools.existing()`、`Runtime.compile_project_checks()`。

Gate 输入是当前图、Step 状态/产物、当前快照及真实工具账本，不接收一句“模型认为通过”作为验收依据。它是 Python 规则函数，不调用 LLM。

当前生成项目使用 test/vet/format 三个 tool 节点，全部必需。旧单检查模式只证明所选择的检查通过。Gate 重读工具账本，校验节点产物与记录一致、snapshot/request identity 正确、执行 completed 且 exit_code 为 0；只读角色图还核对角色结果和收件记录，但这不是语义审查。

| 情况 | 当前或目标处理 |
|---|---|
| Tester 自报通过，真实测试失败 | 工具结果优先，验收失败 |
| Reviewer approve，测试失败 | 必需测试失败，仍不通过 |
| 测试通过，Reviewer 提交阻断性 API 错误 | 目标方案：阻断验收，保留 finding 与证据；当前尚无完整语义 finding 门禁 |
| 只有可选性能建议未满足 | 目标方案：记录 warning，不覆盖必需规则 |
| 旧快照测试通过，新快照尚未测试 | 不可复用为当前通过证据 |

Runtime 执行路径通过 Store 提交执行 Run 的成功终态；角色不能直接写成功。规划/草稿业务状态不等于生成代码验收成功，不能把所有业务记录的状态都说成由 Gate 决定。

**例子：** S1 的 Clamp 测试通过，人工修改后成为 S2；即使函数签名没变，也必须重新验收 S2。测试通过只覆盖实际断言；没有相关测试时，Gate 不证明所有自然语言要求。

## 4. Evidence 究竟是什么数据结构？

**实现状态：已有具体证据产物，尚未统一成覆盖所有类型的 EvidenceRef 类。**

**代码位置：** [intelligence.py](../../../src/masa/intelligence/index.py)、[tools.py](../../../src/masa/runtime/tools.py)、[project_generation.py](../../../src/masa/application/generation.py)。

当前源码 evidence 包含 kind=source_range、snapshot_id、profile_id、path、content_hash、范围和文本；工具结果包含 request_id、snapshot_id、status、exit_code、输出和耗时；审批通过 approval_ref 绑定批准文件集合。它们保存在 artifact 中，并通过引用关联业务数据。

### 建议统一为带类型的引用

```json
{
  "id": "ev-auth-1",
  "kind": "source_range",
  "snapshot_id": "S1",
  "profile_id": "P1",
  "artifact_ref": "sha256:...",
  "locator": {
    "path": "auth.go", "start_line": 12, "end_line": 30,
    "file_hash": "sha256:..."
  },
  "producer": "go_ast",
  "limitations": ["syntax-only"]
}
```

tool_result 类型使用 tool_call_id、operation、result_ref；human_approval 使用 proposal_digest、approved_at 和授权范围。不要强迫测试日志具有源码行号，也不要把人工批准描述为测试正确性的证明。

Artifact 是不可变内容及其存储身份；Evidence 是“这份内容证明什么、对应哪个版本、由谁产生”的元数据。引用保存 artifact ID，必要时实体化内容。没有校准依据时不设置虚假的 0.99 confidence，使用 observed/measured/inferred 与 limitations。

代码改变后，原证据作为历史仍然真实，但不能直接作为新版本事实；由 Memory/Context 的有效性检查决定当前是否可用。

## 5. 代码快照是什么粒度？

**实现状态：已实现仓库视图清单与完整工作副本。**

**代码位置：** [workspace.py](../../../src/masa/infrastructure/workspaces.py)、`domain.canonical/digest`。

manifest 是纳入范围的 `相对路径 → SHA256(文件字节)` 映射。snapshot_id 是规范 JSON 清单的 SHA256，代表整个受控工作区视图；文件有各自哈希，但不独立代表完整构建版本。

当前复制完整文件到运行目录，不是 Git commit，也没有文件级 CAS 存储或硬链接复用。构建配置单独以 profile 绑定证据。排除 .git/.masa/.venv 和环境文件等内容，拒绝链接与 junction；当前快照有 32 MiB/4000 文件限制。

```text
S12：A.go=a1，B.go=b1
S13：A.go=a2，B.go=b1
```

当前记忆策略会保守淘汰 S12 材料；文件级增强后，B 的原始源码经哈希校验可重新绑定 S13，依赖 A 的总结不能复用。即使 B 自身没变，“B 的调用关系”也可能因为其他文件变化而需要更新。

rename 按删除旧路径、增加新路径处理；删除通过前后清单差集识别，增量索引需删除旧声明。原内容仍在历史产物中。开始/结束工具和恢复时重新计算清单，外部修改导致 snapshot_mismatch；这是边界检测，不承诺阻止所有外部进程同时改文件。

## 6. 记忆失效传播如何实现？

**实现状态：Run 内记录、整快照/profile 失效和依赖传播已实现；文件级有效性索引是建议增强。**

**代码位置：** [memory.py](../../../src/masa/intelligence/memory.py)、[context.py](../../../src/masa/intelligence/context.py)。

当前记录有 statement、epistemic_status、evidence_ref、dependencies、snapshot_id、profile_id、required、conflict_group。提供源码证据时只允许与实际片段相等的 observation；模型解释保留 candidate。当前五层记忆是设计分类，不能说已经有五种完整独立子系统。

`Memory.current()` 每次扫描本 Run 记录：先以快照/profile 不符得到 stale 集合，再循环加入依赖 stale 或缺失父记录的项，直到不再扩大。数据库 status 更新为 stale，artifact 不删除；读取时重新检查，失效会写事件。目前不是反向索引。

### 数据结构取舍

本周先在现有 artifact 中补 file_dependencies，扫描本 Run 记录并用队列传播；不新增通用依赖数据库。以下表结构仅在规模确实需要时考虑，不计入本周交付。

```text
memory_dependencies(memory_id, dependency_kind, dependency_key, expected_version)
INDEX(dependency_kind, dependency_key)
memory_validity(memory_id, snapshot_id, profile_id, status, reason)
```

dependency_kind 可取 file、memory、index_scope、requirement。跨文件 summary 必须记录所有来源；依赖不明确的模型总结绑定整个 snapshot。内容与有效性分开，避免为了复用改写历史出处。

**auth.go 示例：** 源码记录 M1 依赖 auth.go；总结 M2 依赖 M1 和 handler.go。扫描文件依赖找到 M1，再传播至 M2；ContextBuilder 排除它们并重建当前材料。历史交接和已执行 Attempt 保留，不重新投递过期内容。本周直接重建上下文，不引入完整的交接/缓存依赖图。

新增 admin.go 可能新增调用方，因此“全部调用方集合”依赖查询范围/index generation，而不只是已有命中文件。初版直接让这类结论随索引版本失效，比构造精确语义依赖更适合个人项目。

**验收建议：** 改 A 不应复用 A 总结；B 原文可重绑；新增调用文件要使集合结论失效；失效原因必须可追溯；恢复后再次读取仍不能用旧材料。

## 7. Go AST 代码关系准确到什么程度？

**实现状态：语法级 L1，未做 go/types 类型绑定。**

**代码位置：** [index.go](../../../runner/internal/indexer/index.go)、`intelligence.py`。

确定事实包括 package 声明、import 路径/别名、函数/方法声明、接收者语法、签名文本、文件和声明范围。调用表达式只能说明代码写了什么，不证明运行时目标。

例如 `x.Validate()`：AST 看见 selector 与调用，不能单凭名称确定 x 的类型、接口动态实现或是否为函数值字段。普通 `Validate()` 也可能受局部变量遮蔽影响。候选检索根据名称、同包和 import 等线索给出待核对位置，不将名称相同当作精确调用边。

建议补证返回 `resolution=candidate`、源范围与 unknowns；Developer 可直接阅读实现和相关测试。L2 才考虑 go/packages + go/types，在指定构建配置下解析对象绑定；接口分派和反射仍可能不完整。

**限制/愿景：** 完整调用图、SSA 和跨语言数据流不是本个人项目当前承诺。简历适合写“AST 符号索引与调用方候选检索”。

## 8. 索引如何保证一致？

当前使用整快照 generation 缓存，代码变化后重建完整索引，再发布校验后的 generation。本周继续使用这个正确性基线，测试新增、删除、重命名后查询不返回旧源码。

文件级原文记忆复用不要求索引同时增量化。并发解析、文件级缓存和增量输出等于全量输出的验收作为后续独立增量，本周不写入完成态简历。

## 9. ContextBuilder 如何挑代码？

**实现状态：角色化选材、字节预算、去重和 manifest 已有；完整三档材料、Token 计数、范围合并和长日志提取待增强。**

**代码位置：** `ContextBuilder.build()`。

当前输入是 run、index、role、query、tool_results、operation、budget_bytes、handoffs。默认 query 从任务目标取得。先搜索并读取有效记忆，再加入任务、权限、交接和工具结果；Planner 使用签名，其他角色使用源码，Tester 优先测试候选。候选带确定性 score/reasons，当前不依赖 LLM 为每段打分。

当前默认 32000 字节预算、1024 字节余量；字段里的 token 估计是保守字节估计，不是精确 tokenizer。按完全相同源码范围和部分证据引用去重，放不下的材料被跳过并记录原因。required 超限直接报错。

### 建议选择算法

先做权限/版本过滤，再进行跨来源去重及范围合并，然后按 role 排序和预算装配：

- mandatory：需求、权限、当前阻断问题、必要输入输出契约。
- working：修改目标、相关契约、当前失败片段。
- optional：邻域源码、历史摘要、其他候选。

评分可用固定小整数：精确符号 +5、直接失败位置 +5、当前 diff +4、角色相关材料 +3、仅名称相似 +1；这些是起始规则，不是已评估最优权重。Developer 偏实现和契约，Tester 偏验收与测试，Reviewer 偏 diff 和独立影响线索。

超限先移除重复/无关日志，再将非目标整文件降为函数体或签名，最后减少 optional。不能把正在修改的函数仅剩签名；mandatory 放不下则拆分步骤或 context_insufficient。

当前 manifest 形状的示意（ID 与数值非真实记录）：

```json
{
  "context_ref": "artifact-context-1",
  "snapshot_id": "S2", "profile_id": "P1",
  "role": "developer", "query": "ValidateToken empty input",
  "generation": "index-S2",
  "included": ["source-auth", "source-handler"],
  "omitted": [{"id": "old-summary", "reason": "stale"}],
  "input_bytes": 18000, "budget_bytes": 32000,
  "reserved_bytes": 1024,
  "token_estimate_upper_bound": 18000,
  "token_estimator": "utf8-bytes conservative estimate; not provider usage"
}
```

目标增强再记录 granularity、selection_reason、实际 provider usage。manifest 用于解释输入，不保证模型回答可确定性重放。

## 10. 定向补证何时触发？

**实现状态：检索函数已有，通用 EvidenceRequest 循环待实现。**

本周只接 read_symbol 和 read_failure。Agent 主动提出结构化请求，Runtime 绑定当前版本与权限；不根据模型口头“低置信度”无限搜索。后面协议示例改用本周实现范围。

```json
{
  "type": "evidence_request",
  "intent": "read_symbol",
  "arguments": {"symbol_key": "auth::ValidateToken", "max_items": 4},
  "reason": "修改空 token 行为前核对调用方错误处理"
}
```

| intent | 必需参数 | 返回 |
|---|---|---|
| read_symbol | symbol_key | 实现、签名、范围 |
| read_failure | tool_call_id，可选 test_name | 对应日志片段 |

外层允许 intent/reason/arguments，参数按 intent 使用严格 schema，禁止额外字段和任意路径；snapshot、run 和权限由 Runtime 绑定，版本差异查询也必须在授权范围内。

建议每 Attempt 最多 2 轮、每轮 4 片段/8 KB，均为可调初始参数。用 `(内容哈希, 范围, 证据类型)` 判断新增证据，同时判断是否解决新的 unresolved 项；重复查询缓存 key 包含 snapshot/profile/query/intent/options/index version。缓存命中不免除循环次数限制。

例：失败摘要指出空 token 的断言失败，Developer 用 read_failure 读取对应日志，再用 read_symbol 获取 ValidateToken 当前实现。ContextBuilder 重新构建输入，移出无关地图。两轮后仍缺关键材料则人工处理。inspect_callers/find_tests/inspect_diff 的通用请求接口后置，避免本周同时完成五类工具。

## 11. Multi-Agent 是否只是四个 Prompt？

**实现状态：有代码级契约与权限差异，但两条角色路径尚未完全统一。**

**代码位置：** [collaboration.py](../../../src/masa/agents/handoffs.py)、[agent.py](../../../src/masa/agents/execution.py)、[project_plan.py](../../../src/masa/application/planning.py)、[project_generation.py](../../../src/masa/application/generation.py)。

当前只读协议图中 Planner/Developer/Reviewer 都无工具权限，只能提交精确 role_result schema；Tester 执行选定 Go 操作。Reviewer 不接收作者 summary。真实 LLM 被该图显式拒绝，不会静默替换成脚本模型。

另一条真实项目服务已有 Planner 项目规格、Tester 检查方案、Developer 多文件提案，并接人工批准和真实 harness。不能把两条路径合并描述为“真实四角色自主修复已完成”。

### 目标角色配置

| 维度 | Planner | Developer | Tester | Reviewer |
|---|---|---|---|---|
| 输入 | 需求/地图 | 批准规格/实现/契约 | 验收/接口/变更 | diff/约束/证据 |
| 输出 | ProjectSpec/GraphProposal | 文件提案 | CheckPlan/测试提案 | findings/verdict |
| 写权限 | 无 | 提案，不直接写盘 | 测试提案，不改独立验收 | 无 |
| 工具 | 受限检索 | 检索/补证 | 检索/授权验证 | 只读检索 |
| 终止 | 有效规格或 blocked | 有效提案或 blocked | 有效计划或 blocked | 明确 verdict 或 blocked |

每个角色配置局部上限，但所有调用累计到 Run 总预算。allowed message types 在 schema 中限定。文件真正落盘由授权后的发布服务执行，不能靠 prompt 说“禁止写”来代替权限检查。

**面试回答：** 多角色的差异是输入、输出、权限和验证标准，而不只是角色名称；复用一个循环可以减少工程重复。

## 12. 消息如何避免消费旧材料？

**实现状态：直接依赖交接与 receipt 已实现，通用消息系统为增强方案。**

当前 payload 含 sender、receiver、graph_version、snapshot_id、result_ref、evidence_refs、trust；run_id 在 receipt 键中。没有完整 requirement_version/causal_parent 消息字段，不能声称已经存在。

交接只沿当前图的直接依赖投递，来源必须成功且 ready；重新读取角色结果检查 run/snapshot/manifest。receipt 唯一身份为 `(run_id,sender,receiver,graph_version,snapshot_id)`，相同键对应不同 artifact 则冲突；收件记录和事件在同一事务中提交。

建议扩展 message_id、requirement_version、causation_id、type；旧版本消息保留但拒绝当作当前交接。未变源码可重新校验产生新消息，并记录 supersedes；需求变更时不能只验证文件哈希，必须重新检查结论对目标是否适用。重新补证是显式工作流动作，不让消息系统自动无限触发。

例：S1 的“接口保持不变”交接到 S2 时，如果签名已改，校验拒绝；接收角色获得缺失材料状态，重新读取 S2。receipt 解决重复消费，不保证模型调用 exactly-once。

## 13. 恢复如何避免重复执行工具？

**实现状态：保守工具恢复和前后像文件恢复已实现。**

**代码位置：** `Tools.execute/existing`、`Store.recover`、[patching.py](../../../src/masa/runtime/patches.py)。

```text
t0：提交 tool intent、request_ref、预算扣费
t1：外部工具开始
t2：工具产生结果或副作用
t3：保存 result artifact
t4：事务提交结果引用和 tool_finished
t5：提交 Step 完成状态
```

t2 到 t4 之间崩溃，数据库仍只有 intent。当前 Store.recover 将 Run 标记 needs_attention，禁止自动重放。即使叫 go_test，测试代码也可能产生外部副作用，不能简单把它视作纯读操作。

t4 后、t5 前崩溃，已记录工具结果可通过账本复用，节点恢复为待执行并开启新 Attempt；无需重新运行已经确认的工具。旧 Attempt 标 interrupted。

文件补丁另有明确的 before/after 清单：

| 实际内容 | 恢复行为 |
|---|---|
| 等于 before | 执行尚未完成的已批准替换 |
| 等于 after | 文件已经写完，不再重复替换 |
| 两者都不是 | 冲突，停止并要求人工处理 |

多文件恢复先检查整个副本，确认每个文件都属于前像或后像，再完成缺失写入；最后校验完整 after 清单，事务提交快照与账本。它是已有文件受控补丁恢复，不代表任意多文件增删都有通用事务。

建议为明确无副作用的索引/检索允许独立重试；模型请求结果未知时也不要宣称没有发生付费调用。系统保证可核对恢复，不承诺 exactly-once。

## 14. Go 进程如何处理超时、取消和残留？

**实现状态：Windows 路径已有；Linux/macOS 当前明确拒绝。**

**代码位置：** [main.go](../../../runner/cmd/masa-runner/main.go)、[execute.go](../../../runner/internal/runner/execute.go)、[contain_windows.go](../../../runner/internal/runner/contain_windows.go)、[contain_other.go](../../../runner/internal/runner/contain_other.go)、[protocol.go](../../../runner/internal/runner/protocol.go)。

Go 使用 context.WithTimeout 和 exec.CommandContext 执行固定命令；stdout/stderr 接入各自有界 Writer，os/exec 处理管道读取，cmd.Run 等待退出。设置 WaitDelay 限制后代持有管道带来的等待。

Windows 协调器与 worker 在运行仓库代码前加入 kill-on-close Job Object，进程退出关闭句柄时清理其后代。CommandContext 处理直接命令取消，Job Object 负责进程树生命周期。不能把这种本地进程约束叫作针对恶意代码的完整安全沙箱。

capBuffer 超限后不再保存更多字节，但继续接受写入并返回完整写入长度，标 truncated；否则管道写满会使子进程阻塞，父进程又在等它退出。若缓冲实现可能被多 goroutine 共用，必须有同步或明确单写者。

结构化结果区分 completed、timeout、cancelled、rejected、internal_error，附 request_id/snapshot_id、输出和耗时。只有实际完成时使用有效退出码，Python 再按运行状态解释。

**Linux：** 当前非 Windows 分支直接报不支持；本周不做跨平台进程约束，简历明确 Windows 本地执行。

“僵尸进程”特指 Unix 已退出但未被 wait 回收的进程；Windows 更准确说子进程残留。面试中分别解释两者。

## 15. 为什么 Python + Go？有没有性能证据？

**实现状态：混合架构已存在，尚无同语义 Python/Go 性能对照结果。**

Python 适合快速迭代模型接口、契约校验、上下文策略、SQLite 和 Web 服务；Go 能直接使用官方语法解析库，适合批量分析和进程生命周期处理，并以独立二进制提供清晰工具边界。

系统理论上可用 Python 重写执行器，并通过其他解析器获取源码结构；语言选择不是多 Agent 的必要条件。目前应称“Go 分析与执行层”，而不是宣称已证明“Go 性能层提速数倍”。

建议测三组：同一 Go 实现串行全量/受限并发全量/文件增量，记录仓库规模、CPU、缓存冷热、耗时分位数、峰值内存与输出一致性。若要证明语言收益，还要构建解析语义等价的 Python 基线。LLM 网络等待通常与本地索引耗时不同量级，索引快两倍不代表整个任务快两倍。

**例子：** 1000 文件只改 10 文件，增量可以避免 990 次 parse，但仍有扫描、哈希和关系装配成本，不能直接推导“提速 100 倍”。批量调用与缓存收益要分别归因。

## 16. ExperienceRecord：本周只做运行摘要

复用 Run、event 和 artifact，汇总 run_id、parent_run_id、需求、版本、结果、失败类别、实际 Token、耗时与产物引用即可。未知费用填 unknown，失败也保存。它叫运行归档，不声称跨任务经验学习；不另建经验检索和策略库。

## 17. Candidate Policy：暂不实现

由开发者根据失败修改有版本的上下文规则，再运行同一组回归测试。自动候选生成不纳入简历，不增加专门的优化 Agent。

## 18. 评估：小任务集即可

准备约 6～10 个小任务，覆盖正常生成、测试失败、源码变更、长日志、重复请求和中断恢复。边界测试用固定模型响应，选择少量案例做真实模型验收，分别标记结果。

比较改进前后时保持需求、初始代码、模型配置和工具配置一致，记录真实输入 Token、通过数和耗时；没有 tokenizer 时不拿字节数冒充实际 Token。小样本只报告本任务集观察，不声称统计显著。评估脚本与结果表足够，不搭策略实验平台。

## 19. Policy Promotion：使用人工回归和 Git 回退

本周没有自动晋升。上下文规则随代码版本管理，回归失败则回退对应提交；运行保留所用配置身份。不增加在线自动更新或统计晋升门槛。

## 20. Self-Optimizing：留作愿景

项目标题使用“证据驱动的软件工程 Agent Runtime”。反馈修复与可恢复执行属于实际工程能力；自动生成策略、评估、晋升及回退不在一周内承诺，不再展开实现细节。

## 一段可以落地的面试串讲

生成的 Go CLI 经人工审核进入 Run A。测试失败后，日志全文保存为 artifact，上下文仅包含失败测试、位置、退出码和关键片段。用户触发一次修复，Developer 获取原需求和失败证据，最多两轮读取相关实现或失败日志，生成关联草稿。人审后创建 Run B 和新快照，再次检查。

跨 Run 只显式携带需求和历史结果引用；当前源码重新读取，不自动迁移父 Run 的全部记忆。旧失败保留但不作为新版本验收结果。中断时复用已有副作用核对逻辑；未知工具结果不自动重放，付费生成请求也不盲重试。

本周先按步骤读取材料、提取日志和硬预算控制，不做多轮模型摘要。暂停仍计入现有 deadline，因此不能承诺无限时间恢复。

## 验收完成后的简历表述

> 面向 Go 项目生成与修改，构建 Python 编排、Go 分析执行、React 人工审核的软件工程 Agent 平台。以 DAG 和持久化状态管理工具执行，利用快照、工具账本与独立 Gate 验收结果；通过文件哈希有效性检查、依赖失效传播、日志提取和有界补证控制上下文，支持失败反馈生成关联草稿并重新审核验证。

其中新增能力应在完成验收后使用完成式表述。本周不写动态重规划、并发增量索引、自优化或未经测试的性能倍数。

