# 当前项目状态

2026-09-28：完成分层目录与项目工作台重构。

已可使用：真实 Planner/Tester 规划、人工确认、Developer 多文件代码与测试生成、人工审核发布、Go 检查、独立 Gate、失败后的受控修复、项目历史与整体日志、API 本地配置。Go 代码索引、上下文预算及基础记忆仍保留。

本轮后端迁至 domain/application/runtime/agents/intelligence/infrastructure/interfaces；角色契约从 HTTP 模型适配器抽出，项目查询使用现有证据投影。前端重建为 app/api/features，以项目为导航，单一创建入口，角色状态图在上，结果与人工审核在下。

验证：100 个 Python 测试通过，4 个前端 Node 测试通过，Vite 构建通过；新版 HTTP 页面、静态资源、项目导航和角色视图检查通过。真实 DeepSeek 完成规划重试与代码生成，发布后的 test/vet/fmt 和 Gate 全部通过。详情见本轮进展报告。

限制：没有可用浏览器自动化会话，尚未完成视觉和真实点击验收。角色图是实际事件的动态投影，尚非任意 Agent Graph 调度；模型 job 尚不能重启续传。生成项目的应用启动入口未实现，下一阶段接入。真实模型生成的测试仍可能不足：本轮小型 CLI 未自动生成入口级测试，不能把 Gate 通过等同于全部业务正确。

旧文档已退出当前入口并归档，使用指南以 USER_WORKBENCH 为准。历史运行数据库、artifact 与工作区未迁移或删除。
