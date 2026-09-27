# 本轮进展：现实 CSV 场景、测试契约与 Agent 检查图

2026-09-28。用户要求测试更合理、使用现实问题，并向 Agent 决定图节点推进。

## 实现

Tester 现在可以提交具体案例：名称、输入、独立预期、unit/integration/cli 层级，前端可展开查看。Developer 提示要求实现这些案例、保留失败语义、不跳过测试，CLI 优先使用 run(args, stdout, stderr) int，禁止测试直接递归启动 os.Args[0]。

检查计划从固定 3 节点改为 Agent 选择 1～3 个允许操作，go_test 必需，vet/format 推荐。Runtime 按已批准计划生成 tool 节点并添加覆盖所有节点的独立 Gate；重跑保留这个图。不是任意动态图：操作集合仍受限，依赖和 Gate 由 Runtime 控制，运行中重规划尚未实现。

验收条目上限从 12 调至 24，字节预算保持限制，以容纳实际项目。

## 真实模型验收

场景：[CSV 费用汇总 CLI](../scenarios/SCENARIO_CSV_EXPENSE.md)，包含整数金额、退款、分类聚合/排序、引用逗号、行上限、错误时不输出部分结果。

过程如实保留：
1. 第一次 Planner 超出旧 12 条验收上限，失败记录 ccf17bd3f53d4b7bbb7b3f9559db0b15。
2. 成功方案 6048d520f8134a62b67ca0540427d4f6；首份代码 5b164fc8e9054c37b37437916b854a36 的 CLI 测试递归启动测试二进制，审查发现后取消，未执行。
3. 改进提示后重新生成 4e73993c6005486e98f72cab58c55315；仍缺 encoding/csv 导入，人审仅补齐导入，未更改测试预期。
4. 执行 1c29203773ce47eb9a104166673a3d54：12 个生成测试、vet、格式检查全部通过。

独立编写的 16 个黑盒 CLI 场景全部通过，包括上/下金额边界、1000/1001 条记录、错误参数、空文件、迟到的非法行且 stdout 必须为空。另在两个临时副本故意破坏聚合与放宽金额限制，两处均被生成测试检出，原项目未改动。证据保存为 external_acceptance_completed 和 mutation_acceptance_completed 事件引用。

共 5 次真实调用（含失败及重生成），17,260 tokens；未查询实际账单。不是“一次无人工干预全部成功”，也不能据此保证所有生成项目都会通过。

## 验证与接续

95 项 Python 全套通过；之后改动重跑保留图逻辑，Web 15 项再次通过；前端 4 项与构建通过。未进行浏览器视觉点击验收。

代码提交 494083f、95a2679。可复验：
- scripts/smoke_project_live.py --goal-file docs/scenarios/SCENARIO_CSV_EXPENSE.md（付费，重新生成）
- scripts/accept_expense_project.py 1c29203773ce47eb9a104166673a3d54（离线独立验证）

下一步：真实失败证据驱动的受控修复，尤其缺导入/测试架构错误；固定入口前端程序运行。动态图继续分阶段推进，不让 Agent 绕过 Gate。
