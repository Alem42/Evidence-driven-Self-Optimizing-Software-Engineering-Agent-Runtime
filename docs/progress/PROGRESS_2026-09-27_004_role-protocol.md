# 进展：P1-04a 四角色只读协议

日期：2026-09-27；基线：`7f2bc73`，开始时工作区干净。结果：只读协议切片完成，P1-04 整体仍在进行。

## 完成了什么

Planner、Developer、Tester、Reviewer 现在可以在同一 Runtime 图里执行，各自拥有明确权限。只读角色输出有身份、run、快照和证据引用；Tester 才能请求 Go 工具。交接写入 SQLite receipt，并与事件原子提交，重复消费去重。Reviewer 不收到作者摘要或测试结论。

交接内容先进入 ContextBuilder 必需部分再做预算裁剪，避免“清单记录的输入”和“真正发给模型的输入”不一致。Gate 会重新检查角色结果、交接记录及工具账本，不能只相信节点成功状态。

CLI 增加 `--collaboration`；前端增加 Scripted 只读演示入口，图中显示角色，报告和时间线可回查交接证据。

## 验证结果

- 真实 Go 样例 run：`2aa5a8c6afbf45de82dc3bffceb68733`，succeeded，5 次 scripted 调用、2 次真实工具调用（AST + go_test）、3 条交接；没有新增付费模型调用。
- Python 全套 72 项测试通过；新增角色权限、身份/来源/快照拒绝、恢复去重、receipt 冲突与事务回滚、缺失 receipt 拒绝、共享预算、必需上下文超限和 HTTP 入口回归。
- 前端 2 项布局测试、Vite 构建通过。当前环境未做浏览器视觉/点击验收；Go runner 未修改，实际 Go 调用已用于本轮演示。
- 首次联调运行 `6ae1da01d6414ec0b3324ce5b7e27c1a` 的 5 份 context manifest 与实际模型请求逐一一致；最终运行另外加入 run_id 身份绑定，跨任务结果不能混用。

## 边界与下一步

这是 scripted 的运行机制验收，不能宣称已经有真实四角色修复或独立语义审查。Developer 不能写代码；通用 role_result 当前只接受快照清单引用，还没有 plan、patch 和 review verdict 的语义结构。

现有服务进程未重启，保留内存密钥；使用前端新入口需先重启后端，之后重新输入 API Key。旧服务上该入口禁用，避免新 UI 误调旧后端。

实现合同见 [DESIGN_ROLE_HANDOFF_PROTOCOL](../archive/design/DESIGN_ROLE_HANDOFF_PROTOCOL.md)。验证后已生成 [下一阶段预计更新：P1-04b](../archive/plans/PLAN_P1_04B_CONTROLLED_REPAIR.md)，明确一次受控补丁、新旧快照的 Gate 规则及恢复验收。按该计划继续，不提前进入多轮自优化。
