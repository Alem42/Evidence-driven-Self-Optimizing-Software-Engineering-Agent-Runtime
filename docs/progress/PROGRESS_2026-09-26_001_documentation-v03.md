# 进展报告：明确自优化方向并整理实施文档

日期：2026-09-26；任务：DOC-03；结果：文档设计与整理完成，业务代码未开始。

## 这轮完成了什么

项目保留 MASA 短名称，将 Evidence-driven Self-Optimizing Software Engineering Agent Runtime 作为目标定位。核心 demo 先实现受约束的自适应流程；只有经过历史经验、独立评估和策略晋升，才声称具备自优化能力。

四个方向都纳入了设计，但顺序有取舍：动态图内核与默认路由先搭框架；P1 加入证据协作、有限重规划与经验记录；P2 再做经验复用、多模型规则和离线策略优化。delta、模型摘要和增量索引不再阻塞第一版。

## 主要改动

| 文件 | 作用 |
|---|---|
| [用户说明](../guides/USER_PROJECT_GUIDE.md) | 用实际修复任务解释项目、亮点与阶段 |
| [LLM 实施入口](../LLM_IMPLEMENTATION_GUIDE.md) | 规定分轮开发、验收、中断接续和每轮报告 |
| [优先级路线](../PLAN_PRIORITY_ROADMAP.md) | P0/P1 必须实现，P2 优化，P3 可选 |
| [Runtime 总纲](../design/DESIGN_RUNTIME_ARCHITECTURE.md) | 补写当前磁盘缺失的总纲，统一执行边界 |
| [优化设计](../design/DESIGN_ADAPTIVE_OPTIMIZATION.md) | 图版本、经验、路由、策略晋升与回退 |
| [领域详细设计](../design/DESIGN_INTELLIGENCE_CONTEXT.md) | 保留前序细节，协调新旧范围与链接 |

所有项目维护的 Markdown 集中到 docs，原愿景原样移入 archive。旧 IDE 标签可能仍指向根目录，请从 [文档入口](../00_INDEX.md) 打开新文件。

## 验证结果

12 份 Markdown 的编码与代码围栏检查通过，40 个本地链接均可解析；19 个当前实施任务 ID 无重复、依赖无环；根目录不再保留项目 Markdown。未运行业务测试，因为没有创建业务代码；本轮也未重新安装或验证环境工具链。

## 未完成与风险

P0～P2 都尚未实现，自优化收益未测量。旧总纲文件在本轮开始时已不在磁盘，新总纲是依据已确认设计补写，未声称还原旧文件。真实模型与费用上限需要在接入时确定，不阻塞 P0。

## 下一轮从哪里接续

用户要求开始实现后，读取 [当前状态](../STATUS_PROJECT.md)，执行 P0-01：建立最小 Python CLI、Go module 和测试入口。每轮结束新增报告并更新状态，避免只留下聊天摘要。
