# MASA 文档入口

更新：2026-09-26，架构 v0.3 / 实现 0.1.0。P0 框架已实现并验证；实际完成状态见 STATUS_PROJECT。P1/P2 设计不代表已实现能力。

## 项目定位

短名称继续使用 **MASA**，避免更换包名与命令。当前定位为 **Evidence-driven Adaptive Software Engineering Agent Runtime**；目标定位可写 **Evidence-driven Self-Optimizing Software Engineering Agent Runtime**。只有跨任务策略改进经过独立评估后，才把 self-optimizing 作为已实现能力。

## 按读者进入

| 文件 | 用途 | 读者 |
|---|---|---|
| [USER_CODE_INTELLIGENCE_GUIDE.md](USER_CODE_INTELLIGENCE_GUIDE.md) | Go AST、代码检索、版本化记忆与上下文的运行和限制 | 当前代码使用 |
| [progress/PROGRESS_2026-09-27_001_intelligence-context.md](progress/PROGRESS_2026-09-27_001_intelligence-context.md) | P1-02/P1-03 实现与验收 | 最新进展 |
| [USER_P1_PATCH_QUICKSTART.md](USER_P1_PATCH_QUICKSTART.md) | 受控补丁示例、JSON 格式与恢复边界 | 当前代码使用 |
| [USER_GO_TODO_CONTRACT.md](USER_GO_TODO_CONTRACT.md) | Go Todo 修复契约与代码阅读顺序 | 用户与 LLM |
| [progress/PROGRESS_2026-09-26_004_controlled-patches.md](progress/PROGRESS_2026-09-26_004_controlled-patches.md) | P1-01 受控写入与恢复验收 | 最新进展 |
| [USER_CONSOLE_GUIDE.md](USER_CONSOLE_GUIDE.md) | React 控制台启动、任务监控、人工修改与 API 设置 | 浏览器使用入口 |
| [DESIGN_LOCAL_CONSOLE.md](DESIGN_LOCAL_CONSOLE.md) | 本地 HTTP、React、凭据与运行边界 | 控制台开发 |
| [progress/PROGRESS_2026-09-26_003_react-console.md](progress/PROGRESS_2026-09-26_003_react-console.md) | React 控制台实现与验收边界 | 最新进展 |
| [USER_PROJECT_GUIDE.md](USER_PROJECT_GUIDE.md) | 用通俗方式理解项目、亮点、取舍和最终演示 | 用户优先读 |
| [USER_P0_QUICKSTART.md](USER_P0_QUICKSTART.md) | 当前 P0 构建、运行、暂停、恢复和限制 | 使用当前代码 |
| [LLM_IMPLEMENTATION_GUIDE.md](LLM_IMPLEMENTATION_GUIDE.md) | 后续实现的唯一操作入口，分轮执行与完成规则 | 每次编码 LLM 必读 |
| [PLAN_PRIORITY_ROADMAP.md](PLAN_PRIORITY_ROADMAP.md) | P0～P3、任务依赖、验收、可停止版本 | 用户与 LLM |
| [STATUS_PROJECT.md](STATUS_PROJECT.md) | 当前真实状态、下一任务、已知阻塞 | 每次开始与结束必读/更新 |
| [DESIGN_RUNTIME_ARCHITECTURE.md](DESIGN_RUNTIME_ARCHITECTURE.md) | Runtime、Graph、工具、持久化与模块边界 | 架构与实现 |
| [DESIGN_INTELLIGENCE_CONTEXT.md](DESIGN_INTELLIGENCE_CONTEXT.md) | 代码理解、记忆、上下文和通信的详细机制 | 对应任务实现时读取 |
| [DESIGN_ADAPTIVE_OPTIMIZATION.md](DESIGN_ADAPTIVE_OPTIMIZATION.md) | 动态图、经验、模型路由、自优化与评估 | P1/P2 对应任务 |
| [ENV_LOCAL_SETUP.md](ENV_LOCAL_SETUP.md) | 已有开发环境与复现命令 | 环境检查时 |
| [templates/TEMPLATE_PROGRESS.md](templates/TEMPLATE_PROGRESS.md) | 每轮面向用户的进展报告模板 | 每轮结束使用 |
| [progress/PROGRESS_2026-09-26_002_p0-runtime.md](progress/PROGRESS_2026-09-26_002_p0-runtime.md) | P0 实现与验证报告 | 当前进展 |
| [archive/ARCHIVE_ORIGINAL_PROJECT_PLAN.md](archive/ARCHIVE_ORIGINAL_PROJECT_PLAN.md) | 原始愿景，内容原样保留 | 历史，不作为实施要求 |

## 文档职责与冲突处理

最新用户要求优先。LLM_IMPLEMENTATION_GUIDE 管执行规程，PLAN_PRIORITY_ROADMAP 管范围和阶段，STATUS_PROJECT 管完成事实；DESIGN_RUNTIME_ARCHITECTURE 管核心不变量，两份专项 DESIGN 管领域细节。旧任务号只用于追溯，不能作为越过新优先级的理由。

架构冲突必须明确记录并协调修订，不能靠“读到的最后一篇”覆盖安全或正确性条件。常规实现选择不需要重复询问已授权事项；扩大功能范围或遇到真正缺失的信息才澄清。

## 文件命名与统一存放

项目自行维护的 Markdown 全部放在 `docs/`，按 USER_/LLM_/PLAN_/DESIGN_/STATUS_/ENV_ 区分用途；报告放 `progress/PROGRESS_YYYY-MM-DD_NNN_topic.md`，模板放 `templates/`，历史放 `archive/`。工具链、依赖缓存中自带的第三方 Markdown 不迁移。

原 `MASA_Intelligence_Context_and_Collaboration_Design.md` 已移为 DESIGN_INTELLIGENCE_CONTEXT；原 Environment_Setup 已移为 ENV_LOCAL_SETUP；原 Project Plan 已归档。旧 MASA_AI_Architecture_and_Implementation 在本轮开始时磁盘上不存在，新的 DESIGN_RUNTIME_ARCHITECTURE 是依据已确认规划补写的总纲，不声称恢复了原文件字节。

## 给下一轮 LLM 的启动语句

> 请先阅读 docs/LLM_IMPLEMENTATION_GUIDE.md、docs/STATUS_PROJECT.md 和 docs/PLAN_PRIORITY_ROADMAP.md，检查实际文件状态，再从当前最早未完成且依赖满足的任务开始。按阶段实现，不自动引入 P2/P3。每轮结束更新状态并生成 docs/progress 下的用户进展报告。若本轮只要求设计或审查，不启动代码实现。

集中放在 docs 的入口不会自动约束一个完全没有读取它的工具。后续会话应显式引用上述入口；本轮没有为了自动发现而在根目录另放 AGENTS.md，以保持所有项目 Markdown 集中存放。
