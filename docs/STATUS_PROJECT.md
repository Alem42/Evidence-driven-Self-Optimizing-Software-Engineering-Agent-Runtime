# MASA 当前项目状态

最后更新：2026-09-26。当前阶段：P0 与 P1-01 已完成；UI-01 实现及自动验收通过，浏览器验收待完成；下一核心任务 P1-02。

## 已确认与已完成

- 用户认可先前的证据驱动规划，本轮进一步给出动态图、经验、路由和自优化方向。
- 已完成 v0.3 架构取舍、P0～P3 路线；本轮完成 P1-01 受控写入，不扩展 P2。
- 本机 Go 1.27.1、Python 3.12.7 venv 已用于实际构建和测试；最新 41 项 Python 测试、Go 测试和 go vet 通过。Node 24.17.0、npm 11.13.0 用于 React/Vite 构建。
- 项目维护的 Markdown 已归入 docs；原计划归档；缺失的旧总纲由新的架构总纲承接。

## 任务状态

| 任务 | 状态 | 说明 |
|---|---|---|
| DOC-03 文档与优化取舍 | done | 当前文档批次，不属于业务 P0 |
| P0-01 | done | Python CLI、Go module、固定构建依赖和 uv.lock |
| P0-02 | done | 声明式 DAG、依赖/环/节点/最终 Gate 校验、可替换默认 policy |
| P0-03 | done | SQLite 状态事件事务、artifact 哈希、工作副本与快照 |
| P0-04 | done | Go stdio runner，真实 test/vet/fmt check，Windows 进程树清理 |
| P0-05 | done | 工具账本、scripted loop、预算、取消、单模型路由记录 |
| P0-06 | done | 节点边界恢复、已落盘结果复用、未知结果禁止重放、CLI 报告；回归通过 |
| P1-01 | done | Go 多文件样例、公开契约；受控补丁、前后哈希、部分写入恢复及真实进程退出验收；仅验证前写已有实现文件 |
| P1-02～P1-08 | todo | 后续核心 demo |
| UI-01 | in_progress | React 控制台实现、构建、HTTP 验收通过；浏览器视觉与点击验收待完成 |
| P2-01～P2-05 | deferred | 先交付 P1，按数据与预算选择优化 |
| P3 扩展 | deferred | 无默认实现任务 |

已创建 src/masa、runner、tests 和 examples/go-todo。Go 示例故意保留原始缺陷；修复只写运行副本。当前框架使用离线 scripted provider，补丁由显式 JSON 提供，不能称为真实 LLM 自动修复系统。

## 下一项工作

下一核心任务 P1-02：Go AST 全量索引与代码检索。先阅读 DESIGN_INTELLIGENCE_CONTEXT，基于 examples/go-todo 设计声明/import/测试候选契约。UI-01 浏览器验收有可用浏览器时补做，不阻塞核心代码工作。

本轮使用说明：[USER_P1_PATCH_QUICKSTART](USER_P1_PATCH_QUICKSTART.md)。新增函数中英文注释，核心恢复与权限逻辑附解释；每个验证完成的增量 commit，后续继续遵循。

## 未决事项与阻塞

框架没有外部阻塞。真实 provider/model/API key 和费用上限在 P1-06 前确定。当前仅支持 Windows 工具执行，串行调度，一次 run 选择一种 Go 检查；未知工具结果或补丁内容冲突停在 needs_attention。已实现验证前补丁恢复；执行图内修复、新建/删除文件、并行验证和跨平台执行尚未实现。

旧 `MASA_AI_Architecture_and_Implementation.md` 在本轮开始时磁盘不存在；未做删除恢复，新的 DESIGN_RUNTIME_ARCHITECTURE 是补写文档。IDE 旧标签可能仍显示旧路径，请从 docs 重新打开。

## 最新报告与接续日志

最新报告：[PROGRESS_2026-09-26_004_controlled-patches](progress/PROGRESS_2026-09-26_004_controlled-patches.md)。

P1-01 保留示例 run：`c1fbc15a8bb546dba282ba9c2e218988`，succeeded，2 次 scripted 调用、2 次工具预算（补丁 + Go 测试）。基线 fcc8d6e；样例 ba0b85c；补丁 c72b7e9；真实集成验收 01a6536。41 项 Python 测试通过，其中包含真实子进程写后退出和重启恢复。

UI 验证：前端构建、32 项 Python 测试、Go tests/vet 通过。真实 HTTP 演示 run `97a2a69f9cda4314acd478a9e0cb3f8e` 经暂停恢复成功，仅 1 次工具调用。浏览器工具无可用浏览器，未完成页面点击验收。API 配置入口已实现，真实 provider 接线仍待 P1-06。

验证入口：`powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test.ps1`。保留的演示 run：`b252c572cb84425ca5465676ceb74bb3`，状态 succeeded，2 次 scripted 调用、1 次真实 go_test；经历暂停后恢复，没有重复工具调用。可用 `masa report <run_id>` 查看。

后续每轮必须更新本文件并新增进展报告。状态只能在实际实现和验收完成后改为 done；失败/中断保留具体下一步。
