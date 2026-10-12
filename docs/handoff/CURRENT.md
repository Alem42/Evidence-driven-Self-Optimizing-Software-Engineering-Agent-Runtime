# 当前断点（接续用）

**2026-10-13（凌晨，对照完成）**：Planner 示例（策略 `planner_examples`，默认关）已实现，并做了 V1/V2 同预算配对（Python 6 个小任务×三轮，本地 glm + Flash，累计约 ¥2.94）。修复后：V2 12/12、V1 11/12，V2 平均快约 37%，元/通过略低但在噪声内、调用多 80%；示例合并进测试反而更差（9/12，且示例期望值有时是错的），仅作指引（`examples_as_tests=false`）结果互相矛盾，n 太小。**决定：两个开关保持默认关；V2 保持可选，只宣称“更快”。** 详见 progress/PROGRESS_2026-10-13_003。未做：更大样本、10 文件任务、Go 下的 V2、wc 类字节语义、V2 只重开失败的包。

**2026-10-13（深夜）**：复盘了用户的两次真实运行（大写转换、FizzBuzz，见 progress/PROGRESS_2026-10-13_002）：失败不是 Tester/Planner 的问题，主要是本地大模型冷加载被 30 秒连接时限掐断后原样重试 4 次（已修：本地连接时限=首字时限、同阶段不卸载本地模型、可达却无响应两次就升级）、规格 summary 与验收矛盾、Tester 空 cases 数组被拒（已容忍）、弱模型的自导入（已加确定性检查）。V2 补了依赖顺序、推迟、卡住检测、失败后沿用已封包的包（零 token 测试 14 个；多包真实验证未做）。**待决策**：小任务走 V2 solo 快路径并做与 V1 的同预算对照；Planner 加“具体示例”。

**2026-10-13（晚）**：Runtime V2 第一个切片已实现并推送：`request.runtime=v2` 时用“包工人循环”生成草稿（角色 package_worker、写范围由工具强制、自动封包、小任务合成一个包、账本事件 worker_step/package_sealed），验证/Gate/修复仍是 V1 的；真实 Flash 的 Python 4/4 通过判官（¥0.027–0.063/个，样本很小）。第二轮模拟用户测试的 7 类问题已修（见 progress/PROGRESS_2026-10-13_001）。后端 599 测试通过、等价检查 IDENTICAL。**下一步**：V2 的失败重开出错的包、依赖顺序、卡住检测/升级、与 V1 的同预算配对（小样本）、运行视图；本地模型（glm-4.7-flash）冒烟。

**2026-10-13**：语言包 P0–P7 与 P8 部分完成并推送：Go 从核心解耦（耦合字面量 355→78，Go 行为逐字节不变、等价检查 IDENTICAL），运行器改为按语言包生成的操作清单执行，**Python 包**跑通（脚本化端到端 + 真实 Flash 约 ¥0.77，判官下 9/12 通过、2 次假通过已用“导入名检查”堵上），前端有语言选择与 设置→语言（解释器、unittest/pytest、额外包目录），`masa lang list/info/check`。详见 progress/PROGRESS_2026-10-13_001。**下一步**：V2 工人循环（design/RUNTIME_V2.md，M0 运行视图可先做）；第二轮模拟用户测试（Python 与新设置页）的报告若已返回见该进展文档。工具：`scripts/eval/lang_eval.py`（Flash only）、docs/guides/ADD_A_LANGUAGE.md。

**2026-10-12**：冻结键 bug 已修（`dab8707`：本地复核用自己的 `triage_snapshot_ref`）。用户决定：新建与 V1 **并存**的 Runtime V2（按包推进的自主工人循环，Pro 只做规划、其余用本地模型 + Flash），并把 Go 专属内容抽成**语言包**以支持 Python/C# 等。两份设计稿已写完，**代码未动，等用户审核**：design/RUNTIME_V2.md、design/LANGUAGE_PACKS.md（含 Go 耦合盘点、接口、迁移阶段 P0–P8、纵切片方案、第 15 节待拍板问题）。注意：工作区里另一会话有未提交的云端部署改动（Dockerfile、compose、settings.py、llm.py、server.py 等），迁移时只用显式路径 git add。

**2026-10-10（三）**：补充 ¥4 做三个新开关的配对评测，花 ¥3.62，**查出并修掉一个默认路径上的大问题**：规划者让 `main.go` “读取 stdin”，而入口检查禁止这样做，规格里又没有能放 `Run` 的包，于是 bank 基线三轮整包重写耗尽 token（¥1.72，失败）。修复：文件重写用尽仍不合格就立刻失败（不再写后面的文件）+ 规划者提示词写明入口规则（提交 `02118fe`，循环等价性仍 IDENTICAL，提示词快照只改 planner 两项）。修复后 bank 三个臂（关/契约/裁判）各 1 次全部通过，基线只花 ¥0.17；样本太小不能下结论，`context_budget` 在云端窗口下不会触发所以没单独跑。**待用户选**（A 保持关闭 / B 补配对 / C 先验证默认流程，推荐 C 再 B）：见 progress/PROGRESS_2026-10-10_003_switch-pairs.md。下一个值得做的优化：阶段重试时复用已合格的文件（未实现）。

**2026-10-10（二）**：四个优化方向都已实施：导入检查（默认开）、包间契约 `package_contract`、断言裁判 `assertion_referee`（默认关）、大项目任务 inventory/grades；裁判二选一可靠性 88%（3 次多数）。累计约 ¥19.9（批准 ¥20）已用尽，三个新开关没有配对评测，下一步需要预算时按 progress/PROGRESS_2026-10-10_002 的“没有做到的”补。

**2026-10-10**：经验库已停用（接入点失效、控件隐藏、代码保留）；规模自适应分层上下文已接入（`context_budget`，默认关闭，小任务原样）；新增大项目任务 `bank`（L10，按 id 运行）并做了第一次 10 文件实测（¥0.29，未通过）：主要问题是跨包契约不一致与测试期望收敛不了，上下文大小不是瓶颈。优化方向见 progress/PROGRESS_2026-10-10_001_large-project.md。累计花费约 ¥19.6（批准 ¥20），重复测试已按要求停止。

**2026-10-09**：新增并真实评测了 Tester 用例审计（`test_audit`，默认关闭，唯一有正面信号）、经验库最小版（`library`，留出 0 命中）、上下文分层框架（影子模式）、实验结果页、MCP 协议层验证。累计花费约 ¥19.3（用户批准总额 ¥20）。下一步候选：用例审计复测（每组 30+）、失败信息结构化（路径 2）、降本调优（Planner 用 Flash）、大项目评测层级。详见进展 2026-10-09 与 guides/TEST_RELIABILITY_EXPLAINED.md。

**2026-10-07**：分支 `feature/dynamic-roles`。刚完成：结构整理（见 STRUCTURE.md）、MCP 服务（guides/MCP.md）、文档整理（删除过期文档与 2026-10-04 之前的进展，旧内容在 git 历史里）、ROADMAP 增加“当前路线”。待用户决定：经验库是否做最小原型；工具方向（建议先做 best-of-N 与“测试更可靠”，见 ROADMAP 当前路线）；是否拆 `application/console.py`。

**状态速览**
- 默认流程 = 之前正常的流程：指挥者等动态角色**默认关闭**，差分检查（`scripts/eval/loop_equivalence.py`）证明与 `ceda2ae` 逐项相同。
- 真实评测结论（进展 005、M2 总结）：指挥者未见通过率收益且更贵；费用约 89% 在 Pro。
- 测试：后端约 485（`PYTHONPATH=src python -m pytest -q`，4 个既有 ERROR 是被导入的 `test_*` 函数被 pytest 当成用例收集，与功能无关）；前端 40（`cd frontend && npx vitest run`）。
- 回滚点：结构整理之前 `951ad90`。

**工具与坑（务必看）**
- 打补丁：工具输入里的双反斜杠会被折叠；用 `chr(10)` / Write / Edit，打完立刻 `ast.parse`；CRLF 文件做多行替换前先规范化。新文件用 Write，不要在一条 Bash 里放多个大 heredoc。
- 真实评测花钱：先设上限；`scripts/eval/p2_eval.py` 有硬性费用上限；重跑只跑单个任务；显卡归用户。
- 后台任务的“完成”通知只是外层 shell 退出，等日志里的结束标记；会阻塞的服务（如 MCP stdio）不要直接在前台启动。
- 提交信息不加 Co-Authored-By。

**文档入口**：[00_INDEX](../00_INDEX.md)。

**测试可靠性测量完成（2026-10-07，花费约 ¥1.1）**：见进展 003 与 reference/TEST_RELIABILITY.md；组件 `application/checks/consensus.py`（盲推导+表决+机械比较，带测试）已就绪但**还没接进流程**，等用户在 A（Tester 用例审计）/B/C 中选。

**已完成（2026-10-07）**：best-of-N（验证选择的多次尝试），真实 A/B 未见收益（见进展 002），保留为可选开关、默认关闭；下一步建议“让测试更可靠”（ROADMAP 当前路线第 2 条）。设计：策略 `best_of_n`（1=关，默认）；在修复/修订测试阶段，第一个候选之外再让最便宜的合格模型生成 N-1 个候选，逐个验证，选“通过 > 未解决条目最少”的；选中的 verified run 记入 job['preverified']，主循环跳过重复验证。A/B 用 `scripts/eval/p2_eval.py --arms off,bon3`，费用上限 ¥6。
