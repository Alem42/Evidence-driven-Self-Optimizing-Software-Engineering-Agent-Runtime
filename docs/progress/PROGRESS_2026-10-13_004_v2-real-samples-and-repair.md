# PROGRESS 2026-10-13 · 004 — V2 在真实 10 文件样本上逐条迭代；只重开失败的包
# V2 iterated sample by sample on real 10-file tasks; reopening only the failing package

目标 / goal：补做 V2 的“验证失败后只重开出问题的包”，并用真实样本把包工人策略一条一条磨稳。预算 ¥10，**云端只用 Flash（不用 Pro）**，本地 glm-4.7-flash。
方法 / method（用户提议）：**一次只做一个样本；失败了先查账本里的真实证据，判断是流程哪一环的问题，修了再跑同一个样本，直到它稳了再换下一个**——不反复跑全量。每个修复都带一个“由真实失败倒推出来的”单元测试（零 token）。
样本 / samples：`bank`、`inventory`、`grades`（Python，约 10 个文件、5–6 个包，规格里有“跨包要一致”的规则；独立判官判定）。账本在 `.masa/ab2/`，逐步查看工具 `.masa/_steps.py`，汇总 `.masa/ab2/summary.json`。
**样本量很小（每个样本 1–7 次，不是独立重复，且每次都在修复之后）；下面的数字是“这条流程现在能不能跑通”的证据，不是通过率。**

## 1. 结果总览 / Results

| 样本 | 轮次（修复前 → 修复后） | 最终状态 |
|---|---|---|
| bank | r1 停（预算）· r2 失败 · r3 停（token 上限）· r4 失败 · r5 失败 · r6 **通过**（¥0.40，175 s）· r7（Flash 起步）一次网络抖动失败（¥0.14），修复后见 §5 | 通过过 1 次；r7 的失败是网络抖动，已修 |
| inventory | r−1 被误拦 · r−2 规划字段错 · r1 **通过**（¥0.12，121 s）· r7 **通过**（¥0.13，92 s） | 稳定通过 |
| grades | r1–r5 失败（5 次，¥0.28–0.78）· r6 **通过**（Flash 起步，¥0.38，181 s）· r7 **通过**（¥0.21，107 s） | 稳定通过 |

花费：ab2 全部 §6。**Flash 起步（`--start-level 2`）的 4 次运行里 3 次通过（92–181 秒、¥0.13–0.38），1 次被一次网络抖动打断（已修，#19）；本地 glm 先试的 7 次运行里它只封过 1 个很小的包，一次失败的尝试要 1–12 分钟（见 §4）。**

## 2. 逐条发现与修复 / Findings and fixes（每一条都有真实账本证据）

| # | 样本/轮 | 症状（账本里看到的） | 根因 | 修复 | 零 token 测试 |
|---|---|---|---|---|---|
| 1 | bank r1 | 38 次调用后“token 预算用完”；10 文件任务一半都没写完 | 评测沿用了 V1 的单次上限（40 次调用、按等级的 token）；V2 每一步一次调用，10 文件要 80–150 次 | `max_model_calls`、`per_run_scale` 可配置；**V2 默认 150 次**（`Console._v2_budget`，用户显式给的不改） | `test_a_v2_task_gets_a_larger_default_call_budget…` |
| 2 | bank r1 | 接线包 `app` 排在第一个，猜着写了不存在的包 | 依赖顺序靠“用途里出现包名”，规格用中文写用途时一个也匹配不到 | 名字是 app/main/cli/…的包依赖所有其它包（`INTEGRATORS`） | `test_a_wiring_package_waits…` |
| 3 | bank r1 | 模型多写了一个 `,"}`，7 步工作随整个生成阶段作废 | 一次 JSON 写坏就让整个阶段失败重来 | 不可用的回答当作“被拒绝的一步”，带原因让模型重来；`RoleRuntime.call(continue_after_failure=True)` | `test_an_unusable_reply_is_a_refused_step…` |
| 4 | bank r1 | app 被推迟一次后第二次轮到时依赖的包仍未写好 | 推迟只允许一次 | 推迟次数随包数增长，并防止两个包互相等待 | （行为由原有的推迟测试覆盖；“互相等待”的防护没有单独的测试） |
| 5 | bank r2 | 入口包 `bankledger` 13 步全耗在 parser 的错误上 | 判断“自己的文件”用了前缀 `bankledger/`，把所有子包都算成自己的 | 只算本包自己的目录，不含子包 | `test_the_entry_package_does_not_own…` |
| 6 | bank r2 | 第三次尝试把已封包的 4 个包又重写一遍 | 沿用只看上一次的 `package_sealed`，不看它自己沿用的包 | 沿用 `package_sealed` ∪ `package_reused` | `test_a_third_attempt_reuses…`（去掉修复会失败） |
| 7 | bank r2 | 修复工人第一步就 `finish`，连续 3 次，什么都没改 | 弱模型的协议行为 | 修复模式里没改过东西不能 finish；`allowed_tools`（约束解码的 schema 只留允许的工具）；finish 失败的回答里给出下一步确切的 JSON | `test_a_repair_worker_cannot_finish…`、`test_a_weak_model_cannot_pick_finish…` |
| 8 | bank r2 | 修复的第 2、3 次全失败：“角色输入已变” | 调用 id 被截断到 100 字符，修复范围的包名很长，所有步骤共用一个 id | id 改成“轮次.步号:目录”（步号在前） | `test_long_directory_names_never_merge_worker_steps` |
| 9 | bank r3 | 第一次修复把 2 个失败改成了 5 个，坏版本被拿去验证 | 修复工人越改越糟，交出的是最后的状态 | **只交出检查结果最好的状态**；没有比起点好的就原样返回（协调器按“没有改动”处理）；起点先真实跑一遍检查 | `test_a_repair_that_never_beats_the_starting_point…` |
| 10 | bank r5 | `command` 包永远封不了：`tests/test_command_parser.py` 被归给入口包 | `package_of` 只认 `test_<文件名>`，不认“包名_文件名” | 按词数打分，唯一最高分的包认领；还没有测试文件时 run_check 给出明确提示而不是看不懂的回溯 | `TestsFollowTheirPackageTests` |
| 11 | bank r5/r6 | 工人在 interfaces 里只看到签名，猜错 store 的返回类型，16 步修不好；反复 read_file 被拒 | 别的包只给签名；上下文只留最近 3 次工具结果 | 直接依赖的包以**全文**给（≤14000 字符）；工人读过的文件从此以全文留在 interfaces 里 | `test_the_direct_dependencies_are_shown_in_full…` |
| 12 | bank r1–r5 | 105 次工人调用全在云端，本地 glm 一次没用上 | 准入按整份规格估计输入，10 文件规格放不进本地窗口 | V2 按“一步的上下文”估计（≈12 KB）：`admission_need` / `repair_need` | `test_a_worker_is_admitted_by_its_own_small_context…` |
| 13 | inventory r1 | 整个任务被拦截：“要的是库/函数包” | 规则 `写一个…库` 匹配了“库存”，`包` 匹配了“包含” | 后面不能紧跟别的汉字 | `test_a_word_that_merely_starts_with_library…` |
| 14 | inventory r−2 | 规划三次都失败，消息只是“字段无效” | 模型各错一个字段：只有 summary / 多了 `acceptance_note` / 缺 `acceptance` | 多余的顶层字段丢掉；缺什么写进错误消息，重试时模型才能改对 | `PlannerSpecShapeTests` |
| 15 | grades r1 | gpa 包 13 步：测试里的期望值算错了（89·7+92 写成 91.125），工人五次只改实现，实际值一点没变 | 工人分不清“测试错还是实现错” | “实际值一点没变”时明确提示重算期望；失败详情里给出 unittest 的逐行差异并标明 `-` 是实际、`+` 是期望 | `test_an_unchanged_actual_value_points_at_the_expectation`、`FailureDetailsTests` |
| 16 | grades r2–r5 | 同一断言一再失败，工人在两个都不对的值之间摇摆（`91.0` / `91.00`、`91.13` / `91.33`） | 测试期望来自模型自己或 Tester 的算错的示例 | **断言裁判**（已有的盲选机制，单次 80%、三次多数 88%）接入工人：同一断言第二次失败时，把真正不同的两行打乱成 A/B 让 Flash 独立选 3 次，结论放在结果最前面；`assertion_diffs` 从差异块取值（多行断言消息会被缩写成 `[13 chars]`）；规则：示例与验收冲突时以验收为准 | `test_the_referee_is_asked…`、`AssertionDiffsTests` |
| 17 | grades r3 | 工人连续写 3 个 note、重复读自己的文件，20 步耗尽 | 无意义的动作 | 连续 note 被拒；`read_file` 自己的文件被拒并告诉下一步；步数 16→20 | `test_a_second_note_in_a_row…` |
| 18 | grades r3 | 本地模型连续 `finish` 好几次，每次同样失败，十分钟过去 | 没改任何东西就重跑同一个失败的检查 | 上次失败后没改过文件：run_check/finish 被拒，并且 schema 里也不再有它们 | `test_the_same_failing_check_is_not_run_again…` |
| 19 | bank r7 | 28 次调用里一次“服务没有回答”，整个任务失败 | 付费云调用不自动重放的原则对“每步只值几分钱的循环”代价太高 | **有意的例外**：这一步算失败，稍等后继续，连续 3 次才放弃 | `test_one_silent_model_call_does_not_fail_the_whole_task` |
| 20 | 真实探针 | 修复工人连改 12 次文件一次也没跑检查 | 没有反馈的盲改 | 修复模式里每次改完工具自己跑检查并接在结果后面，顺便记下最好的状态 | （修复测试已改成“改完即封包”） |

## 3. 只重开失败的包（本次的主要功能）/ Reopening only the failing package

流程：验证失败 → V1 的归属分析/Diagnoser 照旧 → 实现修复这一步由 V2 的工人完成（`V2Generation._propose_repair`），而不是一次性的补丁调用。
1. **范围**：失败证据里出现的实现文件（路径或点号模块名）所在的包 + 出现的测试文件所属的包；找不到就是全部包；**第二次修复起扩大到全部包**（第一次只看出问题的包没修好，说明根因可能在别处）。写范围是这些包的实现文件，**测试冻结**（两次写测试的尝试直接抛“repair cannot change tests”，协调器据此转去修订测试）。
2. **起点**：先在草稿上真实跑一遍检查，得到按“你的文件”整理过的失败清单，同时是“有没有变好”的基准。
3. **只交出最好的状态**：工人可能越改越糟（真实记录里 2 个失败变成 5 个）；记录每次检查的问题数，最后交出问题最少的那个状态；没有比起点好的就原样返回，由协调器按“没有改动”处理（诊断/升级/改测试），不会拿一个更糟的版本去验证。卡住（同一失败 3 次）时，已有改善就交出改善，否则才抛出让上层升级。
4. 其余沿用：本地先、有界升级、预算、Gate。

**真实探针**（`scripts/eval/repair_probe.py`，不重新生成项目，只测“失败之后”）：拿一个通过的 bank 运行，在某个包里埋 bug（把一个函数的返回值换成 `None`），真实验证失败后让 Flash 走“继续自动修复”：

| 埋 bug 的包 | 修复范围（来自证据） | 工人步数 | 起点→终点问题数 | 改动的文件 | 额外花费 | 结果 |
|---|---|---|---|---|---|---|
| parsing | app + parsing | 2 | 5 → 0 | 只有 `parsing/parser.py` | ¥0.026（5 次调用） | Gate 通过 |
| reporting | app + reporting | 2 | 5 → 0 | 只有 `reporting/format.py` | ¥0.006（1 次调用） | Gate 通过 |
| rules | app + rules | 2 | 3 → 0 | 只有 `rules/transfer.py` | ¥0.020（5 次调用） | Gate 通过 |

（第四次埋在 app 里的 bug 没有让任何测试失败，没有可修的东西，不算。）
**局限**：埋的是“返回值变 None”这类明显的 bug；真正的跨包语义错误（例如错误优先级）更难，这条路径在真实生成的验证失败上还没有被一次成功的运行走完过（bank r3 的两次修复发生在第 9、20 条修复之前，结果是变糟）。

## 4. 本地 glm 作为包工人：诚实的数据
- 7 次本地先试的生成（bank r6、inventory r1、grades r1–r5）里，**每次都在“第一步 finish / 写不出能过的测试”上卡住**，只封过 1 个很小的包（grades r4 的 roster）；一次失败的尝试要 1–12 分钟（glm 每步 3–160 秒）。
- 已做的缓解（V2 默认）：每个等级只试一次（`attempts_per_level.generation=1`，因为有两次本地尝试的 5 次里第二次 5/5 以同样方式卡住）；约束解码里去掉不该选的工具；没改文件不让重跑检查。这些让它更快地失败，**没有让它成功**。
- 结论：**在这套工具协议下，glm-4.7-flash 不是可用的包工人。** 评测里用 `--start-level 2`（生成直接从 Flash 起步）；本地模型仍适合 Triage 之类的窄角色。是否换别的本地模型（qwen3.6、qwen3.8 等）做工人，**没有测**。
- Flash 起步后，三个样本的通过运行都只用 700–800 个本地 token（只有 Triage）。

## 5. 与 V1 的对照（同样三个样本、同样模型、Flash+glm 默认 V1 流程）
V1 对照还在跑（本地 glm 的修复很慢），结果见本文末尾的“V1 对照”小节；目前已有：bank **通过**，¥0.073、558 秒、7 次调用、2 次验证（Flash 写整包 24 秒，之后是本地 glm 修复）。

## 6. 花费与测试
- ab2 评测（bank/inventory/grades 的所有轮次 + V1 对照）合计见 `.masa/ab2/summary.json`；探针 4 次约 ¥0.1；本阶段总计 ≤ ¥10。
- 测试：新增约 25 个零 token 测试（`tests/test_v2_workers.py` 38 个、`test_python_pack.py`、`test_triage.py`）。

## 7. 仍未验证 / Still unverified
- 更多样本（本次只有 3 个 10 文件任务；mid 级单文件任务没有重跑）。
- 真正的“生成后验证失败 → V2 修复 → 通过”只在探针里（埋 bug）验证过；还没有在一次真实生成里自然发生过。
- 断言裁判在工人里的效果：触发过几次，结论多数合理（grades r6 的运行里它对一个断言投了 implementation_wrong），但**没有单独的 A/B**。
- 包的依赖顺序仍是启发式；规划者输出显式依赖（`depends_on`）没有做。
- 其它本地模型当工人没有测。
- Go 语言包下的 V2 在真实任务上没有重跑。
