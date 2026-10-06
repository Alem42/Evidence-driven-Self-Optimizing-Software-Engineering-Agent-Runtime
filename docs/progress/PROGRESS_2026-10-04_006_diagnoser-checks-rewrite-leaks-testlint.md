# 进展014：Diagnoser 逐条核对 · 整体重写 · 提示泄漏检查 · 测试静态检查

2026-10-04。分支 `feature/multi-model`。前置：[进展013](PROGRESS_2026-10-04_005_frontend-defaults-evidence-attempts-imports.md)。方案与出处：[PLAN_LOW_MODEL_QUALITY](../reference/LOW_MODEL_QUALITY.md)。

## 1. 做了什么（按你的顺序）

| 项 | 实现 | 说明 |
|---|---|---|
| **#12 盲测诊断 → 逐条核对** | `expectation_checks` + `reconcile_diagnosis` | **设计有修正**：真正“盲”做不到——失败用例的输入只存在于测试源码里。改成 chain-of-verification 式：Diagnoser 对每个失败用例**先写 `requirement_says`（只根据需求自己算出的正确输出），再写 `test_expects`，最后 `matches`**；schema 的属性顺序即生成顺序。确定性改判：核对过的用例**全部不一致 ⇒ 判测试有问题**；部分不一致且原判只是实现 ⇒ 两侧都有问题。只剩断言失败时**第一轮就诊断**（以前要 2 轮失败后）。 |
| 顺手修的 bug | Diagnoser 看不到测试源码 | 断言条目里只有文件名（`cli_test.go`），bundle 里是完整路径，`path in bundle` 永远为假——**测试源码从未送进 Diagnoser**，这正是它被测试带偏的原因之一。已按唯一后缀还原完整路径。 |
| **#14 提示泄漏检查** | `leaks.py` | 源码里出现我们的提示词字段名/固定句子（`previous_attempt_error`、`failure_evidence`、`previous_files`……）即判失败并带原因就地重写；生成、修复、测试修订三处都查。它在语法上是合法 Go（在字符串里），语法门抓不到。提示词也加了“绝不能抄进源码”。 |
| **#4 冻结前检查** | `testlint.py` | 做了能干净做的一半：测试文件必须有 Test 函数、有失败断言、不能 `t.Skip`（注释/字符串里的假断言不算），不满足就就地重写。**真正的红灯**（空实现上必须失败）需要存根或变异，留给 M3 变异门，不假装做到。 |
| **#13 整体重写** | 工作流 `fix-v1` 新增 `rewrite` 节点 | 连续 2 轮补丁没有改善（未解决条目数不降）且最强模型已试过 ⇒ 最高等级丢弃现有结构，对着冻结的测试重写一次（给全部实现文件，带前几轮摘要）；每任务最多一次；有改善的任务从不触发。 |

## 2. 真实运行里发现并修复的三个新问题（CSV 任务，4 次运行）

1. **生成阶段失败（不是验证阶段）**：① 强模型写的测试文件被 8192 token 截断（`model refused or returned incomplete output`）→ 提示词要求测试文件紧凑（≤~250 行、≤12 个用例）；② 弱模型把内部包 import 成 `github.com/example.com/expense/internal/expense`（编造的 module 前缀）→ `fix_module_imports`：只在路径恰好以某一个已知包目录结尾时，改回批准的 module；歧义不改；③ **模型梯子上限 4 太小**：每级 2 次 × 3 级 = 6，上限 4 会在最高等级出场前就停下 → 改为 6。
2. 上一轮已记录：测试修订主路径漏了 import 修复（已补）。

## 3. 真实运行结果

CSV 费用汇总：**通过**（12 次调用，云端约 3.2 万 token，129 秒）。路径：`imports_fixed`（`remove path/filepath`、`remove strconv`、`add io` …）→ 修复实现（本地 → deepseek-flash → v4-pro；`rounds_extended` 因仍在收敛多给一轮）→ 最后剩测试侧编译问题 → 修订测试（v4-pro）→ 通过。Ollama 结束后 `/api/ps` 为空。

**诚实说明**：这次通过**没有触发**盲测核对和整体重写——它们只被单元/集成测试覆盖（含一个复现 CSV 真实失败形态的集成测试：断言只剩降序被写成升序、Diagnoser 判成实现问题，但它自己的核对全部不一致，结果被改判为测试并修好）。上一轮同一任务失败正是卡在那个形态，这次模型没有再写出它，所以**还没有真实运行证明这两项在线上起作用**。下一次遇到才算验证。

## 4. 测试
Python 353 项、前端 31 项通过。新增：`test_leaks`、`test_testlint`、`test_diagnosis_reconcile`、`test_goimports`（module 前缀）、`test_fix_flow`（整体重写、核对改判、测试源码送达）、`test_local_generation`（泄漏/空转测试/语法的就地重写）。

## 5. 下一步
- 对同一批任务（文本统计、CSV、动态规划、随机整数）各跑多次，统计通过率——这就是 M2 的 W0 评测基线；单次通过不能说明稳定。
- “Tester 写完后独立重算期望值”（#12 的另一半）；#7 经验卡（只记录）。
- 模型梯子上限现在是 6，**请留意云端 token 消耗**（预算上限仍有效）。
