# 当前接续点

2026-09-29。先读 ../progress/PROGRESS_2026-09-29_002_durable-roles-and-app-run.md，再检查 git status/git log。上一轮已实现 RoleRuntime、SQLite jobs、应用运行和诊断驱动修复上下文。本轮补齐保存响应后的修复草稿恢复；真实 API + Go 验证通过。

## 下一次直接处理

1. application/console.py 自动流程仍拒绝 repair/test_revision/test_format/planning_retry 恢复。连接 generation.resume_revision，但只复用已完成响应，未知请求不得静默重发。
2. 补齐草稿已发布 awaiting_review、job 尚未保存 plan_id/draft_id 的崩溃窗口。
3. 持久化重复断言计数和已评估的 verification id，防止恢复重置预算或重复计数。
4. 修正前端仅凭 planning/generating 元数据判断 working 的情况；重启后应显示恢复入口。刷新 interrupted job 列表避免陈旧卡片。
5. 再统一角色 Step/Attempt，独立测试评审与中型仓库任务随后推进。

## 已验证事实

真实修复草稿 d912ad0a9dc74bbf97023172cd32dec6 → 验证 a4d566c7882d432a8be3908ebc0f5895 succeeded。单次真实模型调用，响应后故障注入并数据库重开，恢复无模型调用。随机数固定区间和求和正常/非法参数的真实程序执行均通过。详细证据与验证数量见进展文档。

保留 .masa 旧快照，不直接改历史代码。密钥本地保存、不打印、不提交。按模块验证后 git commit，核心函数添加中英注释。UI 8765 可能仍运行之前提交版本，新代码需重启加载。没有浏览器视觉验收。不要把局部检查点恢复宣称为完整自适应角色 DAG。
