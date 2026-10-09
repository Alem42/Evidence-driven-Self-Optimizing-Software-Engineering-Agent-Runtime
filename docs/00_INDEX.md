# 文档入口

更新：2026-10-07。当前事实以 **PROJECT_OVERVIEW** 和代码为准；后续路线以 **ROADMAP 顶部的“当前路线”** 为准。

## 先读
| 文档 | 回答什么 |
|---|---|
| [PROJECT_OVERVIEW](PROJECT_OVERVIEW.md) | 项目是什么、一次任务怎么跑、Gate 是什么、模块地图、做了/没做什么 |
| [ROADMAP](ROADMAP.md) | **当前路线**、定位与岗位要求对照、动态角色的路线选择、各项优化的理由与出处 |
| [STRUCTURE](STRUCTURE.md) | 代码结构与依赖方向 |
| [M2_DYNAMIC_ROLES_AND_EVAL](M2_DYNAMIC_ROLES_AND_EVAL.md) | M2 做了什么、真实评测的数据（含简短/详细描述，给改简历用） |
| [RESUME_PROJECT_DESCRIPTION](RESUME_PROJECT_DESCRIPTION.md) | 简历（精简/标准版）、面试问答、数据备忘 |

## 使用指南（guides/）
[工作台](guides/USER_WORKBENCH.md) · [本地模型](guides/USER_LOCAL_MODELS.md) · [Go 环境](guides/USER_GO_ENVIRONMENT_AND_TESTING.md) · [路由与预算](guides/USER_ROUTING_AND_BUDGET.md) · [评测](guides/USER_BENCHMARK.md) · [**测试可靠性（白话讲解 + 面试说法）**](guides/TEST_RELIABILITY_EXPLAINED.md) · [Docker 部署](guides/DEPLOY_DOCKER.md) · [**MCP 服务**](guides/MCP.md) · [Ollama 控制页](guides/USER_OLLAMA_CONTROLLER.md) · [Runtime 与恢复](guides/USER_LOCAL_RUNTIME_AND_RECOVERY.md)

## 设计（design/）
[动态角色（RoleSpec / 指挥者 / 调优器 / MCP）](design/DYNAMIC_ROLES.md) · [上下文工程](design/CONTEXT_ENGINEERING.md) · [10 文件项目实测与优化方向](progress/PROGRESS_2026-10-10_001_large-project.md) · [优化方向的实施](progress/PROGRESS_2026-10-10_002_optimization-directions.md) · [三个开关的配对评测与入口规则修复](progress/PROGRESS_2026-10-10_003_switch-pairs.md) · [经验库](design/IDEA_EXPERIENCE_LIBRARY.md) · [最小骨架](design/DESIGN_SYSTEM_ARCHITECTURE.md) · [硬件监控](design/DESIGN_OPTIONAL_HARDWARE_MONITOR.md)

## 参考（reference/，仍有参考价值但不再更新）
[低质量模型的 16 项主流做法](reference/LOW_MODEL_QUALITY.md) · [多模型 Runtime 原始设计](reference/MULTI_MODEL_RUNTIME_PLAN.md)（§8–§10 的优先级已被 ROADMAP 取代）· [面试技术问答](reference/INTERVIEW_TECHNICAL_QA.md) · [**测试可靠性与任务难度表**](reference/TEST_RELIABILITY.md) · [上下文画像](reference/CONTEXT_PROFILE.md) · [上下文影子重放](reference/CONTEXT_SHADOW.md)

## 开发接续
[handoff/CURRENT](handoff/CURRENT.md)（当前断点）· [长期约定](MEMORY_PROJECT.md) · [实施入口](LLM_IMPLEMENTATION_GUIDE.md) · [验收场景](scenarios/SCENARIO_SEEDED_RANDOM.md) · progress/ 只保留 2026-10-04 以来的迭代记录（更早的在 git 历史里）
