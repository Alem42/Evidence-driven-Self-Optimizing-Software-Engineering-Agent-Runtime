# 文档入口

更新：2026-10-06。**先读下面三份**，它们是当前事实；其余是历史记录或某一方面的细节。

## 先读这三份
| 文档 | 回答什么 |
|---|---|
| [PROJECT_OVERVIEW](PROJECT_OVERVIEW.md) | 项目是什么、解决什么问题、一次任务怎么跑、**Gate 到底是什么**、每个术语的白话解释、模块地图、做了/没做什么 |
| [ROADMAP](ROADMAP.md) | 项目定位、主流岗位要求对照、**动态角色的六条路线与选择理由**、重新判断后的路线与优先级、各项优化的理由与出处 |
| [RESUME_PROJECT_DESCRIPTION](RESUME_PROJECT_DESCRIPTION.md) | 精简版/标准版简历、面试问答、数据备忘 |
| [代码结构](STRUCTURE.md) · [**M2 总结与实测**](M2_DYNAMIC_ROLES_AND_EVAL.md)（简短版/详细版描述 + 数据，给你改简历用）· [DESIGN_DYNAMIC_ROLES](DESIGN_DYNAMIC_ROLES.md) | **执行说明（P1、P2 已完成）**：动态角色（RoleSpec、混合式指挥者、工作流调优器、MCP）的设计、分阶段验收与执行约束 |

## 使用指南
[工作台](guides/USER_WORKBENCH.md) · [本地模型](guides/USER_LOCAL_MODELS.md) · [Go 环境](guides/USER_GO_ENVIRONMENT_AND_TESTING.md) · [路由与预算](guides/USER_ROUTING_AND_BUDGET.md) · [**评测**](guides/USER_BENCHMARK.md) · [**Docker 部署**](guides/DEPLOY_DOCKER.md) · [Ollama 控制页](guides/USER_OLLAMA_CONTROLLER.md) · [Runtime 与恢复讲解](guides/USER_LOCAL_RUNTIME_AND_RECOVERY.md)

## 方案与细节（仍有参考价值，优先级以 ROADMAP 为准）
| 文档 | 内容 |
|---|---|
| [PLAN_LOW_MODEL_QUALITY](PLAN_LOW_MODEL_QUALITY.md) | 让低质量模型完成高难度工作：16 项主流做法的出处、理由、实现状态（这份写得最细，保留） |
| [PLAN_MULTI_MODEL_RUNTIME](PLAN_MULTI_MODEL_RUNTIME.md) | 多模型 Runtime 的原始设计与 M0–M3、W0–W7 细化；**§8–§10 的优先级已被 ROADMAP 取代** |
| [PLAN_MULTI_MODEL](PLAN_MULTI_MODEL.md) | R0–R5 的早期细节 |
| [DESIGN_SYSTEM_ARCHITECTURE](design/DESIGN_SYSTEM_ARCHITECTURE.md) | 最小骨架 |
| [DESIGN_OPTIONAL_HARDWARE_MONITOR](design/DESIGN_OPTIONAL_HARDWARE_MONITOR.md) | 硬件监控 |

## 开发接续与历史
- 接续：[CURRENT](handoff/CURRENT.md) · [长期约定](MEMORY_PROJECT.md) · [实施入口](LLM_IMPLEMENTATION_GUIDE.md)
- **progress/** 是历史验收记录（按时间，每份对应一次迭代），最近几份：[进展015 评测基线](progress/PROGRESS_2026-10-05_001_w0-benchmark-baseline.md) · [016 评测发现与修复](progress/PROGRESS_2026-10-05_002_benchmark-findings-and-fixes.md) · [017 流式/取消/stall](progress/PROGRESS_2026-10-05_003_m2-w1-streaming-cancel-stall.md)
- **已过期（只作历史）**：[STATUS_PROJECT](STATUS_PROJECT.md)、[PLAN_NEXT_STAGE](PLAN_NEXT_STAGE.md)、[PLAN_ARCHITECTURE_SIMPLIFICATION](PLAN_ARCHITECTURE_SIMPLIFICATION.md)、[PLAN_FRONTEND_REDESIGN](PLAN_FRONTEND_REDESIGN.md)、[PLAN_MODEL_ROUTING_AND_WORKBENCH](PLAN_MODEL_ROUTING_AND_WORKBENCH.md)、[PLAN_PRIORITY_ROADMAP](PLAN_PRIORITY_ROADMAP.md)、[PLAN_RUNTIME_CLARIFICATION](PLAN_RUNTIME_CLARIFICATION.md)、archive/
- 验收场景：[明确种子的随机数 CLI](scenarios/SCENARIO_SEEDED_RANDOM.md)

规则：当前事实以 PROJECT_OVERVIEW 和代码为准；不要把旧进展里的“待实现”当成当前事实。
