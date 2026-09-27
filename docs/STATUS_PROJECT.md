# MASA 当前项目状态

最后更新：2026-09-27。P0、P1-01～03、P1-06a 与 P1-04a 只读角色协议已完成。下一核心任务 P1-04b 一次受控修复；P1-04 整体仍 in_progress。UI-01 浏览器验收仍待完成。

## 已确认与已完成

- 用户认可先前的证据驱动规划，本轮进一步给出动态图、经验、路由和自优化方向。
- 已完成 P1-01 受控写入、P1-02 AST/检索与 P1-03 记忆/上下文最小链路。本轮用同一 Runtime 接入真实模型，不扩展 P2。
- 本机 Go 1.27.1、Python 3.12.7 venv 已用于实际构建和测试；最新 59 项 Python 测试、Go 测试和 go vet 通过。Node 24.17.0、npm 11.13.0 用于 React/Vite 构建，前端构建通过。
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
| P1-04 | in_progress | 04a done：只读角色 envelope、权限、定向交接、receipt、Gate 重查；04b 语义契约与补丁待实现 |
| P1-05 | todo | 受约束重规划尚未实现 |
| P1-06 | in_progress | P1-06a done：真实兼容 HTTP、schema/usage/超时、冻结配置与 DeepSeek 真测；四角色接线待 P1-04 |
| P1-07、P1-08 | todo | 经验评价、完整修复端到端验收 |
| UI-01 | in_progress | React 控制台实现、构建、HTTP 验收通过；浏览器视觉与点击验收待完成 |
| P2-01～P2-05 | deferred | 先交付 P1，按数据与预算选择优化 |
| P3 扩展 | deferred | 无默认实现任务 |

已创建 src/masa、runner、tests 和 examples/go-todo。Go 示例故意保留原始缺陷；修复只写运行副本。当前可选择离线 scripted 或真实 DeepSeek 验证；补丁仍由显式 JSON 提供，不能称为真实 LLM 自动修复系统。

## 下一项工作

本轮 P1-04a 已完成：72 项 Python 测试、2 项前端测试和构建通过，真实 Go 只读角色 run `2aa5a8c6afbf45de82dc3bffceb68733` 成功（5 scripted / 2 tool / 3 receipt），无新增 API 费用。后续按 [PLAN_P1_04B_CONTROLLED_REPAIR](PLAN_P1_04B_CONTROLLED_REPAIR.md) 实现角色语义契约和一次补丁；旧规划证据与新快照验收必须区分，不能简单解除 Patches 守卫。

2026-09-27 小型整理已完成：图依赖分层、workflow 模板归位、请求参数严格校验、跨任务取消回归。最新 Python 全套 61 项测试、前端布局 2 项测试和构建通过；本轮无付费调用，未重启现有服务。下一次大更新按 [PLAN_NEXT_MAJOR_UPDATE](PLAN_NEXT_MAJOR_UPDATE.md) 分段实施。

下一核心任务 P1-04b：在现有只读协议上增加 plan/patch/review 语义契约与一次受控写入；随后 P1-05 增加有限图修复。先读 DESIGN_ROLE_HANDOFF_PROTOCOL 和下一阶段计划。当前四角色只运行 scripted，Developer 尚无写权限，Reviewer ready 不代表语义审查通过。

本轮使用说明：[USER_LIVE_LLM_GUIDE](USER_LIVE_LLM_GUIDE.md)，协议边界：[DESIGN_MODEL_PROVIDER](DESIGN_MODEL_PROVIDER.md)。继续中英文注释和按增量 commit。下一轮可连续推进 P1-04 与其后依赖任务；不要重复花费额度证明本轮已验收的接线。

## 未决事项与阻塞

框架没有外部阻塞。用户选择 DeepSeek V4 Pro，授权测试总开销不超过 ¥100，已执行本轮 9 次调用；密钥仅服务内存，后续重启需重新输入，禁止从聊天复制到文件。当前支持 Windows 工具执行、串行调度、单检查或 test/vet/format 完整验证图；未知工具结果、协议违规或补丁冲突停在 needs_attention。执行图内修复、新建/删除文件、并行验证和跨平台执行尚未实现。模型调用有次数/输出/时间限制，尚无货币级硬限额。

旧 `MASA_AI_Architecture_and_Implementation.md` 在本轮开始时磁盘不存在；未做删除恢复，新的 DESIGN_RUNTIME_ARCHITECTURE 是补写文档。IDE 旧标签可能仍显示旧路径，请从 docs 重新打开。

## 最新报告与接续日志

最新报告：[PROGRESS_2026-09-27_004_role-protocol](progress/PROGRESS_2026-09-27_004_role-protocol.md)。真实模型验收见 [DeepSeek 进展](progress/PROGRESS_2026-09-27_002_live-llm-console.md)。

本轮真实模型 run：`72e84c19c6f34386ae9b422c7a3f2198`（复杂 Go 样例，完整验证 succeeded，6 model / 4 tool）；`c04e167a7a0641738aea9979b5639224`（Todo 原始缺陷，预期 failed，2 model / 2 tool）。含探测合计 17,246 tokens，按峰时未命中缓存价格估算 ¥0.163098，实扣未知。本轮服务地址 `http://127.0.0.1:8766`；默认 API 配置已就绪，重启前连接测试记录不会持久化。仅验证真实推理接线，未评价代码修复能力。

上一轮演示 run `3d966205631b497abab202fef2dc9443`：补丁 → AST → 上下文 → Go 检查 → Gate，succeeded，2 次 scripted 调用、3 次工具预算。新增 runner 哈希使旧未完成 run 的恢复继续受 tool_profile 校验约束。检索采用显式词法扫描，记忆整快照保守失效，无类型绑定/精确调用图/FTS/跨 run 复用。

P1-01 保留示例 run：`c1fbc15a8bb546dba282ba9c2e218988`，succeeded，2 次 scripted 调用、2 次工具预算（补丁 + Go 测试）。基线 fcc8d6e；样例 ba0b85c；补丁 c72b7e9；真实集成验收 01a6536。41 项 Python 测试通过，其中包含真实子进程写后退出和重启恢复。

历史 UI 验证：最初前端构建、32 项 Python 测试、Go tests/vet 通过。真实 HTTP 演示 run `97a2a69f9cda4314acd478a9e0cb3f8e` 经暂停恢复成功，仅 1 次工具调用。本轮新增多 API 管理、完整图、单步/自动继续；浏览器工具再次返回空列表，仍未完成页面视觉及点击验收。

验证入口：`powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test.ps1`。保留的演示 run：`b252c572cb84425ca5465676ceb74bb3`，状态 succeeded，2 次 scripted 调用、1 次真实 go_test；经历暂停后恢复，没有重复工具调用。可用 `masa report <run_id>` 查看。

后续每轮必须更新本文件并新增进展报告。状态只能在实际实现和验收完成后改为 done；失败/中断保留具体下一步。
