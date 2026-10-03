# 当前状态

2026-10-03。这里只记录事实；实施顺序见 PLAN_NEXT_STAGE，接续见 handoff/CURRENT。

- 已修复本地 Planning 假卡死：模型快照旧标量事件导致项目视图异常；原任务实际在等待需求回答，现在真实 HTTP 能读取问题。没有代答原任务。
- 已修复轮询丢失 job、规划等待误标完成、刷新漏报角色 worker、重复服务误中断任务、取消被迟到结果覆盖、角色期限未传给传输层，以及项目报告误称无 LLM。
- 新 Ollama 草稿逐文件生成并保存恢复点；旧草稿/云端 bulk 兼容。单文件和修复有路径契约，CLI 入口须 package main；未知响应不自动重发。
- 测试私有包导入及编译器类型错误正确走测试修订；普通断言失败不因此自动修测试，实现修复仍冻结测试。
- 前端 v3（2026-10-03）：与后端彻底分离，TypeScript 重写；后端 `masa serve` 只提供 API。任务历史、顶栏真实状态、可展开的执行图（含版本分支）、对话·决策流、CodeMirror 代码页（出错行标红）、验证、日志、独立设置中心。详见 [PLAN_FRONTEND_REDESIGN](PLAN_FRONTEND_REDESIGN.md)。
- 任务报告（2026-10-03）：`GET /api/projects/:id/report` 汇总 token/耗时/工具调用，前端顶栏常驻并在任务终态自动弹窗；自动流程新增 gofmt 规范化、带原因的契约重试、测试-规格仲裁、Go 预检。详见 [进展007](progress/PROGRESS_2026-10-03_007_usage-report-and-auto-fixes.md)。
- 前端常驻阶段/时间/最近实际速率/文件进度，明确待答、待审、取消收尾和失败。Ollama 控制页已有硬件与后台诊断折叠面板。硬件只读有界缓存、不提权；故障日志不保存请求正文或密钥。
- 已有标准库 Go CLI 规划、单轮澄清、审批、发布快照、真实检查、独立 Gate、运行与关联修订。Coordinator 串行，RoleRuntime/Jobs 持久化；没有任意动态图或并行决策 Agent。

本轮真实调用仅使用本地 GLM 与 Qwen。Qwen 大小写转换 CLI 经明确测试修订后，run dd9ea5f962dc437bb523a34a79c44423 的 Go 检查/Gate 与五项独立 CLI 探针通过；不是自动流程首次成功。GLM 多文件求和仍失败，思考对照达到输出上限，不能宣称通用本地项目生成已稳定。案例见 [进展005](progress/PROGRESS_2026-10-03_005_local-recovery.md)。

默认仍为 glm-4.7-flash:latest，Qwen 比较配置可另选。固定配置快照已实现；本地权重 digest、上下文准入、跨阶段总预算、自动升级和辅助路由待做。当前非流式；速率来自已完成请求。不是完整安全沙箱，不自动安装第三方依赖。

Python 186 项、前端 vitest 21 项、tsc 与前端构建、Go runner 测试通过；新前端已用真实状态拷贝在浏览器实测（未调用模型，生成中的实时跟随未实机观察）。启动方式见 [工作台](guides/USER_WORKBENCH.md)。
