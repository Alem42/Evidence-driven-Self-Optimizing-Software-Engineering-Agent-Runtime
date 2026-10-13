# 文档入口

更新：2026-10-14。项目入口见根目录 [README](../README.md)；当前事实以 **PROJECT_OVERVIEW** 和代码为准；路线与“做到哪一步算做完”以 **ROADMAP（收口计划）** 为准。

## 先读
| 文档 | 回答什么 |
|---|---|
| [PROJECT_OVERVIEW](PROJECT_OVERVIEW.md) | 项目是什么、一次任务怎么跑、Gate 是什么、模块地图、做了/没做什么 |
| [ROADMAP](ROADMAP.md) | **收口计划**：终点（D1–D7）、任务拆分与进度、做减法清单、与主流做法的对照、冻结规则；旧路线在 [archive/ROADMAP_2026-10-07](archive/ROADMAP_2026-10-07.md) |
| [STRUCTURE](STRUCTURE.md) | 代码结构与依赖方向 |
| [M2_DYNAMIC_ROLES_AND_EVAL](M2_DYNAMIC_ROLES_AND_EVAL.md) | M2 做了什么、真实评测的数据（含简短/详细描述，给改简历用） |
| [RESUME_PROJECT_DESCRIPTION](RESUME_PROJECT_DESCRIPTION.md) | 简历（精简/标准版）、面试问答、数据备忘 |

## 使用指南（guides/）
[工作台](guides/USER_WORKBENCH.md) · [本地模型](guides/USER_LOCAL_MODELS.md) · [Go 环境](guides/USER_GO_ENVIRONMENT_AND_TESTING.md) · [路由与预算](guides/USER_ROUTING_AND_BUDGET.md) · [评测](guides/USER_BENCHMARK.md) · [**测试可靠性（白话讲解 + 面试说法）**](guides/TEST_RELIABILITY_EXPLAINED.md) · [Docker 部署](guides/DEPLOY_DOCKER.md) · [**MCP 服务**](guides/MCP.md) · [Ollama 控制页](guides/USER_OLLAMA_CONTROLLER.md) · [Runtime 与恢复](guides/USER_LOCAL_RUNTIME_AND_RECOVERY.md)

## 设计（design/）
[动态角色（RoleSpec / 指挥者 / 调优器 / MCP）](design/DYNAMIC_ROLES.md) · [Runtime V2（按包推进的自主循环，设计稿）](design/RUNTIME_V2.md) · [语言包（多语言抽象，已实施）](design/LANGUAGE_PACKS.md) · [语言包进展与 Python 实测](progress/PROGRESS_2026-10-13_001_language-packs.md) · [新增一种语言](guides/ADD_A_LANGUAGE.md) · [两次真实运行的复盘](progress/PROGRESS_2026-10-13_002_user-runs-analysis.md) · [V1/V2 同预算对照与 Planner 示例](progress/PROGRESS_2026-10-13_003_v2-vs-v1-and-planner-examples.md) · [V2 真实 10 文件样本迭代与“只重开失败的包”](progress/PROGRESS_2026-10-13_004_v2-real-samples-and-repair.md) · [上下文工程](design/CONTEXT_ENGINEERING.md) · [10 文件项目实测与优化方向](progress/PROGRESS_2026-10-10_001_large-project.md) · [优化方向的实施](progress/PROGRESS_2026-10-10_002_optimization-directions.md) · [三个开关的配对评测与入口规则修复](progress/PROGRESS_2026-10-10_003_switch-pairs.md) · [经验库](archive/design/IDEA_EXPERIENCE_LIBRARY.md) · [最小骨架](archive/design/DESIGN_SYSTEM_ARCHITECTURE.md) · [硬件监控](archive/design/DESIGN_OPTIONAL_HARDWARE_MONITOR.md)

## 参考（reference/，仍有参考价值但不再更新）
[低质量模型的 16 项主流做法](reference/LOW_MODEL_QUALITY.md) · [多模型 Runtime 原始设计](reference/MULTI_MODEL_RUNTIME_PLAN.md)（§8–§10 的优先级已被 ROADMAP 取代）· [面试技术问答](reference/INTERVIEW_TECHNICAL_QA.md) · [**测试可靠性与任务难度表**](reference/TEST_RELIABILITY.md) · [**提示排布与缓存命中**](reference/PROMPT_LAYOUT.md) · [上下文画像](reference/CONTEXT_PROFILE.md) · [上下文影子重放](reference/CONTEXT_SHADOW.md)

## 开发接续
[handoff/CURRENT](handoff/CURRENT.md)（当前断点）· [长期约定](MEMORY_PROJECT.md) · [实施入口](LLM_IMPLEMENTATION_GUIDE.md) · [验收场景](scenarios/SCENARIO_SEEDED_RANDOM.md) · progress/ 只保留 2026-10-04 以来的迭代记录（更早的在 git 历史里）
