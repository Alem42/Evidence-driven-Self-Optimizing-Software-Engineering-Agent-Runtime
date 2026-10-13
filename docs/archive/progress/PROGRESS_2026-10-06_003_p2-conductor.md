# P2 指挥者（混合式 orchestrator–workers）/ Conductor

分支 `feature/dynamic-roles`。**默认关闭**（策略 `conductor: false`）；关闭时行为与改动前一致（有测试证明）。**没有做过真实模型运行**，全部验证来自脚本化假模型的测试。

## 做了什么
- **`llm_choice` 节点**（`flow.py`）：节点声明候选列表，`validate()` 要求候选存在、每个候选都有 `chosen` 边，并且有 `always` 兜底边。新增 guard：`conductor_due`、`chosen`、`skeptic_says_wrong`、`only_assertions`、`repaired_before_pass`。
- **`fix-v1` 接入**：`classify → conduct` 是 classify 的**第一条**边（条件 `conductor_due`）；`conduct` 的兜底边回到 `classify`，此时 `conducted` 已置位，所以由确定性规则接手。指挥者开关关闭时这条边永不触发。
- **`application/conductor.py`（纯函数）**：候选集合（`candidate_nodes`）、是否有歧义（`is_ambiguous`：归属不明 / 补丁停滞 / 同一失败签名重复≥2）、是否该问（`due`）、简报（`briefing`，由账本事实截取，不含源码与日志）、提议校验（`check_choice`：`next` 必须是候选，字段必须恰好 `next/reason/brief`，长度上限）。
- **三个新角色，全是数据**（`roles/specs/*.json + .prompt.md`，校验器用通用的 `json_schema`，没有写 Python 校验器）：`project_conductor`、`test_skeptic`（只剩断言失败时逐条核对期望，结论由代码按逐条结果重新推导）、`code_reviewer`（通过 Gate 且经过修复后的只读风险提示，只写 `code_review` 事件）。三个都是只读（`writes: none`）。
- **协调器**（`coordinator.py`）：`_conductor_facts`、`_conduct`、`_skeptic`、`_maybe_review`；`_failure_context` 从 `_diagnose` 里抽出，供 Diagnoser/怀疑者/指挥者共用（Diagnoser 的输入逐字不变，旧测试全绿）。
- **策略**：`conductor`（默认 false）、`conductor_max_calls`（默认 6，范围 1–12）、`conductor_cascade`（默认 false：先用低等级，提议不合法再升级到最高等级）。设置页“路由”里有开关。
- **事件**：`conductor_decided`、`conductor_rejected`、`skeptic_verdict`、`skeptic_failed`、`code_review`、`code_review_skipped`；报告“修复过程”和活动日志里可见。

## 回退与安全（测试覆盖）
越界 id、当前不是候选（如 `rewrite`、`halt`）、多余字段、非对象、调用崩溃/超时、路由停止（预算/等级）、次数上限——全部拒绝并写 `conductor_rejected`，再回到确定性规则；任务仍然通过。指挥者不读源码；不执行任何模型产出的东西；`halt` 只在“同一失败重复且最强模型已试过”时才是候选。

## 测试
后端 448 通过（新增 `tests/test_conductor.py` 16 项），前端 33 通过，`tsc` 无错误。改了一条旧断言：`test_roles_registry` 里“路由角色集合”——P2 在末尾追加了三个角色，旧 6 个角色的集合与顺序仍逐项断言。变异验证：把 `conductor_due` 改成恒假，6 项测试失败。

## 没验证的（更新：已做真实对照评测，见进展005——未见收益，且评测后指挥者的触发与候选有改动：仅停滞时触发、halt 不再是候选）
- 没有用真实模型跑过指挥者，**不知道**它是否提升通过率或省 token；按设计原则 4，没有评测证据前保持关闭。预计每次决策输入约 1.5–3k token，上限 6 次/任务（估算，未实测）。
- `test_skeptic` 与 Diagnoser 的重叠度、`code_reviewer` 的建议质量都未评估。
- 评测运行器还没有统计指挥者调用次数（P3 调优器需要它，计划在 P3 接入）。
