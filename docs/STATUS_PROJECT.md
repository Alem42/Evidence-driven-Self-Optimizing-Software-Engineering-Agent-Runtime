# 当前项目状态

## 面试材料补充（2026-09-28）

已整理 [技术 20 问](guides/USER_INTERVIEW_TECHNICAL_QA.md)，区分源码事实与目标设计；见 [本轮文档报告](progress/PROGRESS_2026-09-28_006_interview-technical-qa.md)。仅文档变更，不增加产品能力，下一步仍按 PLAN_NEXT_STAGE 接入前端程序运行。

更新：2026-09-28。实施顺序以 [路线](PLAN_PRIORITY_ROADMAP.md) 和 [下一阶段](PLAN_NEXT_STAGE.md) 为准，历史报告不覆盖此页。

## 已实现

- B1/B2/B3 基础链路：Planner 项目结构提案 → Tester 验证方案 → Runtime 契约校验/检查图编译 → 前端编辑确认；复用 run/artifact 与两次调用预算，规划不执行占位项目。真实上游 Planner 质量尚未验收。[使用说明](guides/USER_PROJECT_PLANNING.md)。

- Python Runtime：持久化图、节点状态、预算、暂停/继续/取消、隔离快照、工具证据与独立 Gate。
- Go Runner：test/vet/格式检查、AST 索引、进程限制；受控补丁和恢复。
- 代码上下文、版本化记忆、只读角色协议、真实模型与多 API 配置；密钥仅服务内存。
- 自然语言生成单个 Go 实现文件；人审/编辑/批准后写入副本并验证；反馈生成关联草稿。
- 前端分为运行结果、代码与审核、执行过程；图、决策、原始证据保留，结果直接显示日志/退出码/耗时/截断。
- 审核有效且已结束的快照一键重验，建立关联新运行；外部改动导致快照不匹配时拒绝重用。
- 新生成任务与重跑使用三个 tool 检查节点 + 独立 Gate；重跑零模型调用。角色与检查动作不再在此路径混用。

## 尚未实现与边界

尚无多文件生成、项目导出、go run 应用启动、依赖安装、多轮自动修复、完整真实角色决策链、自优化评估。当前“运行”是 harness 的 test/vet/fmt；无验收测试不能证明业务正确。旧 live 图和只读角色演示保留兼容，尚未整体迁移至未来 CheckPlan。

调度串行；密钥重启需重新输入；暂停计入 deadline；模型/工具身份影响恢复。浏览器视觉与真实点击验收尚未完成，不能用构建通过替代。

## 本轮与下一轮

[长期记忆](MEMORY_PROJECT.md) 已加入 LLM 必读，这是仓库持久记忆，不是账户记忆。文档已分 guides/design/progress/archive/plans；最新验收见 [报告](progress/PROGRESS_2026-09-28_001_workspace-console.md)。

最新报告：[Planner 项目结构](progress/PROGRESS_2026-09-28_002_project-planner.md)。下一步：Developer 消费已批准规格，生成多文件提案；人审后完整发布项目并验证。go run 应用执行再后续接入。

C implemented: approved specification -> multi-file draft -> human approval -> isolated Go project verification. See progress/PROGRESS_2026-09-28_003_project-generation.md.

## 最新真实验收（2026-09-28）
完整多文件生成链路已通过真实 DeepSeek 小型 CLI 验收，3821 tokens；执行 aa386729de444b25afd8e8b0114984ce。支持用户选择本地持久密钥保存，重启加载，不提交 Git。下一步前端程序运行。详见 progress/PROGRESS_2026-09-28_004_live-project.md。

## 最新 UI 与恢复
已支持后台角色进度、来源链日志复制、打开代码文件夹、一键确认方案并生成。Tester 缺覆盖改为显式人工审核补充，不伪装通过。94 Python 测试通过；真实用户失败方案已恢复为 c3e3e648f7fe4bb8866ee650faa4a1a7。详见 progress/PROGRESS_2026-09-28_005_workflow-observability.md。

## 最新测试能力
Tester 有具体输入/预期/层级案例，Agent 选择 1..3 允许检查，Runtime 编译并添加独立 Gate。现实 CSV 项目经过一次提示改进及人工补导入后，12 个生成测试、16 个独立 CLI 场景、2 个变异检查通过；并非零干预生成成功。详见 progress/PROGRESS_2026-09-28_006_realistic-tests.md。

## 最新受控修复
已支持失败项目 → 真实证据修复草稿 → 冻结测试的人审 → 新版本验证，前端有修复卡片和前后版本入口。真实 DeepSeek 修复缺导入成功，未人工改模型修复内容，全部原检查和 16 CLI 验收通过。下一步前端程序运行。最新报告：progress/PROGRESS_2026-09-28_007_controlled-repair.md。
