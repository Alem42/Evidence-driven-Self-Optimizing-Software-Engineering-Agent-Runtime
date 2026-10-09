# 当前断点（接续用）

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
