# 进展报告：P0 框架已能运行、验证与恢复

日期：2026-09-26；任务：P0-01～P0-06；结果：已完成。

## 这轮完成了什么

现在已有一条真实执行链：CLI 创建任务和工作副本，声明式图调度离线 scripted provider，Go runner 执行检查，SQLite 保存状态和证据，Gate 根据真实结果判定。无需 API key，且不会修改用户源仓库。

支持预算、超时、取消、节点边界暂停与恢复。恢复会复用已落盘结果；工具只有执行意图、没有确定结果时停在 needs_attention，不自动重放。Windows 进程树清理使用 Job Object，强制结束 runner 也能清理后代。

## 主要改动

| 模块 | 作用 |
|---|---|
| [runtime.py](../../src/masa/runtime/engine.py)、[workflow.py](../../src/masa/runtime/graph.py) | 通用图、依赖和 Gate；恢复与预算 |
| [sqlite.py](../../src/masa/infrastructure/store.py)、[workspace.py](../../src/masa/infrastructure/workspaces.py) | 短事务、工具账本、内容哈希与隔离副本 |
| [Go runner](../../runner/cmd/masa-runner/main.go) | test/vet/fmt check、输出上限、超时与进程清理 |
| [构建](../../scripts/build.ps1)/[测试](../../scripts/test.ps1)脚本 | 固定入口，可重复构建和验收 |

## 验证结果

在项目根目录运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test.ps1`：28 项 Python 测试通过，Go 测试及 go vet 通过，整体退出码 0。覆盖非零退出、大输出、取消/超时/强制结束后的父子进程清理、依赖失败、过期快照、预算耗尽和恢复。

保留的实际演示 run 为 `b252c572cb84425ca5465676ceb74bb3`：paused→succeeded，2 次 scripted 响应、1 次真实 go_test，恢复未重复调用工具。实际使用方式见 [P0 快速开始](../archive/guides/USER_P0_QUICKSTART.md)。

## 未完成与限制

P0 串行运行，每个 CLI run 选择一种检查；目前仅支持 Windows 执行。scripted provider 不是真实 LLM，尚无写代码、AST 理解、多 agent 修复或自优化。未知工具结果需人工排查后新建 run；这里不宣称任意写操作都能恢复。

## 下一轮从哪里接续

从 P1-01 开始：固定 Go Todo 示例契约，增加受控补丁及多文件写入恢复。先阅读 [当前状态](../STATUS_PROJECT.md)，不要重复搭建 P0 或直接引入高级优化。
