# MASA 当前项目状态

最后更新：2026-09-27。当前阶段：P0 与 P1-01～P1-03 最小实现已完成；下一核心任务 P1-04。UI-01 浏览器验收仍待完成。

## 已确认与已完成

- 用户认可先前的证据驱动规划，本轮进一步给出动态图、经验、路由和自优化方向。
- 已完成 P1-01 受控写入；本轮继续完成 P1-02 AST/检索与 P1-03 记忆/上下文最小链路，不扩展 P2。
- 本机 Go 1.27.1、Python 3.12.7 venv 已用于实际构建和测试；最新 51 项 Python 测试、Go 测试和 go vet 通过。Node 24.17.0、npm 11.13.0 用于 React/Vite 构建，前端构建通过。
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
| P1-02 | done | Go AST、地图、imports/声明/测试候选、全量 generation、精确/词法/一跳检索；显式 partial/unknown |
| P1-03 | done | Run 内来源记忆、整快照/profile 失效及依赖传播、角色材料视图、必需内容保护、字节预算、实际 AgentLoop manifest |
| P1-04～P1-08 | todo | 后续核心 demo |
| UI-01 | in_progress | React 控制台实现、构建、HTTP 验收通过；浏览器视觉与点击验收待完成 |
| P2-01～P2-05 | deferred | 先交付 P1，按数据与预算选择优化 |
| P3 扩展 | deferred | 无默认实现任务 |

已创建 src/masa、runner、tests 和 examples/go-todo。Go 示例故意保留原始缺陷；修复只写运行副本。当前框架使用离线 scripted provider，补丁由显式 JSON 提供，不能称为真实 LLM 自动修复系统。

## 下一项工作

下一核心任务 P1-04：复用 AgentLoop，接入四角色输出及有界定向交接、receipt 去重；随后 P1-05 增加受约束图修复。先读 DESIGN_INTELLIGENCE_CONTEXT 第 8 节和 DESIGN_ADAPTIVE_OPTIMIZATION。role 视图不是已经实现四角色协作；不得把当前 Developer 上下文直接当作已开放写权限。

本轮使用说明：[USER_CODE_INTELLIGENCE_GUIDE](USER_CODE_INTELLIGENCE_GUIDE.md)。继续中英文注释和按增量 commit。下一轮可在本轮基础上连续完成多个有依赖关系的可验收任务，以加快 P1 进度。

## 未决事项与阻塞

框架没有外部阻塞。真实 provider/model/API key 和费用上限在 P1-06 前确定。当前仅支持 Windows 工具执行，串行调度，一次 run 选择一种 Go 检查；未知工具结果或补丁内容冲突停在 needs_attention。已实现验证前补丁恢复；执行图内修复、新建/删除文件、并行验证和跨平台执行尚未实现。

旧 `MASA_AI_Architecture_and_Implementation.md` 在本轮开始时磁盘不存在；未做删除恢复，新的 DESIGN_RUNTIME_ARCHITECTURE 是补写文档。IDE 旧标签可能仍显示旧路径，请从 docs 重新打开。

## 最新报告与接续日志

最新报告：[PROGRESS_2026-09-27_001_intelligence-context](progress/PROGRESS_2026-09-27_001_intelligence-context.md)。

本轮演示 run `3d966205631b497abab202fef2dc9443`：补丁 → AST → 上下文 → Go 检查 → Gate，succeeded，2 次 scripted 调用、3 次工具预算。新增 runner 哈希使旧未完成 run 的恢复继续受 tool_profile 校验约束。检索采用显式词法扫描，记忆整快照保守失效，无类型绑定/精确调用图/FTS/跨 run 复用或真实 token 用量。

P1-01 保留示例 run：`c1fbc15a8bb546dba282ba9c2e218988`，succeeded，2 次 scripted 调用、2 次工具预算（补丁 + Go 测试）。基线 fcc8d6e；样例 ba0b85c；补丁 c72b7e9；真实集成验收 01a6536。41 项 Python 测试通过，其中包含真实子进程写后退出和重启恢复。

UI 验证：前端构建、32 项 Python 测试、Go tests/vet 通过。真实 HTTP 演示 run `97a2a69f9cda4314acd478a9e0cb3f8e` 经暂停恢复成功，仅 1 次工具调用。浏览器工具无可用浏览器，未完成页面点击验收。API 配置入口已实现，真实 provider 接线仍待 P1-06。

验证入口：`powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test.ps1`。保留的演示 run：`b252c572cb84425ca5465676ceb74bb3`，状态 succeeded，2 次 scripted 调用、1 次真实 go_test；经历暂停后恢复，没有重复工具调用。可用 `masa report <run_id>` 查看。

后续每轮必须更新本文件并新增进展报告。状态只能在实际实现和验收完成后改为 done；失败/中断保留具体下一步。
