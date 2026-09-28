# MASA 文档入口

2026-09-28 更新。当前事实以状态页为准，设计文档不等于已实现。

| 入口 | 用途 |
|---|---|
| [当前状态](STATUS_PROJECT.md) | 已实现、限制与验收 |
| [长期路线](PLAN_PRIORITY_ROADMAP.md) | 分阶段交付 |
| [下一阶段](PLAN_NEXT_STAGE.md) | 下一轮范围和验收 |
| [生成代码](guides/USER_GENERATE_CODE.md) | 真实模型、人审与验证 |
| [规划项目](guides/USER_PROJECT_PLANNING.md) | Planner 架构、Tester 检查与人工确认 |
| [LLM 实施入口](LLM_IMPLEMENTATION_GUIDE.md) | 每次会话操作协议 |
| [长期记忆](MEMORY_PROJECT.md) | Git、注释、文档持续约定 |
| [面试技术 20 问](guides/USER_INTERVIEW_TECHNICAL_QA.md) | 当前实现、建议方案与愿景边界 |
| [最新报告](progress/PROGRESS_2026-09-28_007_controlled-repair.md) | 本轮变化 |
| [项目修复](guides/USER_PROJECT_REPAIR.md) | 失败证据、修复草稿和冻结测试的人审 |

文档分区：

- guides/：使用指南与环境；USER_ 前缀。旧 quickstart 是专项示例。
- design/：架构与模块设计；DESIGN_ 前缀。
- progress/：PROGRESS_日期_序号_主题.md，历史验收，不覆盖当前状态。
- archive/plans/：被替代的计划，仅供参考，不再决定实施顺序。
- templates/：每轮进展模板。

只维护一个 PLAN_NEXT_STAGE.md，不继续堆积并行的“下一阶段”文档。
