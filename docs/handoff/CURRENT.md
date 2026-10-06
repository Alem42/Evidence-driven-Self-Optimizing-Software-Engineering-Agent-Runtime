# 当前断点（接续用）

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
