# MASA 当前项目状态

最后更新：2026-09-26。当前阶段：P0 已完成；UI-01 React 控制台已实现并通过自动验收，浏览器验收待完成；P1 尚未开始。

## 已确认与已完成

- 用户认可先前的证据驱动规划，本轮进一步给出动态图、经验、路由和自优化方向。
- 已完成 v0.3 架构取舍、P0～P3 路线；本轮开始实现 P0，不扩展 P1/P2。
- 本机 Go 1.27.1、Python 3.12.7 venv 已用于实际构建和测试；最新 32 项 Python 测试、Go 测试和 go vet 通过。Node 24.17.0、npm 11.13.0 用于 React/Vite 构建。
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
| P1-01 | in_progress | 本轮：Go 样例、任务契约、受控补丁与恢复；基线提交 fcc8d6e |
| P1-02～P1-08 | todo | 后续核心 demo |
| UI-01 | in_progress | React 控制台实现、构建、HTTP 验收通过；浏览器视觉与点击验收待完成 |
| P2-01～P2-05 | deferred | 先交付 P1，按数据与预算选择优化 |
| P3 扩展 | deferred | 无默认实现任务 |

已创建 src/masa、runner 和 tests；P0 示例在 tests/fixtures/go-pass。尚无 P1 examples/go-todo，也没有常驻业务服务。当前框架使用离线 scripted provider，不能称为真实 LLM 自动修复系统。

## 下一项工作

本轮按用户要求提前实现 UI-01：React + Vite、Python 本地 HTTP、任务监控与控制、关联需求变更、内存密钥配置。下一步有可用浏览器时补视觉/点击验收；核心 demo 继续 P1-01。使用见 [USER_CONSOLE_GUIDE](USER_CONSOLE_GUIDE.md)，架构见 [DESIGN_LOCAL_CONSOLE](DESIGN_LOCAL_CONSOLE.md)。

建议接续：阅读 USER_P0_QUICKSTART，运行现有构建/测试；然后按 P1-01 增加 Go 示例契约与受控写工具。当前没有开放代码修改，不能直接将测试工作区上的手工修改当可恢复补丁流程。

## 未决事项与阻塞

框架没有外部阻塞。真实 provider/model/API key 和费用上限在 P1-06 前确定；示例需求在 P1-01 固定。P0 仅支持 Windows 执行，串行调度，一次 run 选择一种 Go 检查；未知工具结果停在 needs_attention。跨平台执行、并行验证和写入恢复尚未实现，属于后续范围。

旧 `MASA_AI_Architecture_and_Implementation.md` 在本轮开始时磁盘不存在；未做删除恢复，新的 DESIGN_RUNTIME_ARCHITECTURE 是补写文档。IDE 旧标签可能仍显示旧路径，请从 docs 重新打开。

## 最新报告与接续日志

最新报告：[PROGRESS_2026-09-26_003_react-console](progress/PROGRESS_2026-09-26_003_react-console.md)。

UI 验证：前端构建、32 项 Python 测试、Go tests/vet 通过。真实 HTTP 演示 run `97a2a69f9cda4314acd478a9e0cb3f8e` 经暂停恢复成功，仅 1 次工具调用。浏览器工具无可用浏览器，未完成页面点击验收。API 配置入口已实现，真实 provider 接线仍待 P1-06。

验证入口：`powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test.ps1`。保留的演示 run：`b252c572cb84425ca5465676ceb74bb3`，状态 succeeded，2 次 scripted 调用、1 次真实 go_test；经历暂停后恢复，没有重复工具调用。可用 `masa report <run_id>` 查看。

后续每轮必须更新本文件并新增进展报告。状态只能在实际实现和验收完成后改为 done；失败/中断保留具体下一步。
