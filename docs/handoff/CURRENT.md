# 当前接续点

2026-09-29。最新进展：../progress/PROGRESS_2026-09-29_003_auto-recovery.md；上一增量详见 002_durable-roles-and-app-run.md。先 git status/git log，再继续。

## 当前已完成

持久 RoleRuntime、SQLite jobs、CLI 应用运行、诊断驱动修复上下文。自动 repair/test_revision 可恢复已保存响应/已发布草稿，规划和生成发布窗口已处理，重复断言计数持久化。前端 busy 改看实际 worker，中断列表自动刷新。未知请求禁止静默重发。

真实随机数自动恢复复用 d912ad0a9dc74bbf97023172cd32dec6 → a4d566c7882d432a8be3908ebc0f5895 succeeded，attempt=1，无新 API 请求。上一轮真实模型故障注入与 Go 验证见 002。

## 下一步

1. 提取 WorkflowCoordinator，统一检查点转换，降低 application/console.py 的复杂度。
2. 补 test_format/planning_retry 和创建修复 run 前的中断；目前明确拒绝，不会自动重发模型。
3. 统一角色 Step/Attempt，补真实进程终止恢复测试和测试修订恢复矩阵。
4. 再推进独立测试评审、上下文收益评估和中型仓库任务。

保留 .masa 历史快照，不打印/提交密钥。核心函数中英注释，按模块验证并提交 Git。8765 现有服务可能未加载新代码，需重启。未做浏览器视觉验收。当前不等于完整任意自适应角色 DAG。

## 2026-09-29 最新追加：原路线继续，主动澄清并入

本轮完成 WorkflowCheckpoint 拆分，121 Python 测试通过，真实历史随机数自动恢复复验通过（无新模型调用）。先读 ../progress/PROGRESS_2026-09-29_004_checkpoint-and-clarification.md 和 ../PLAN_RUNTIME_CLARIFICATION.md。

下一步统一角色 Step/Attempt 与 invocation_id，兼容已有 (run_id,purpose) 缓存；随后实现 Planner 问题/回答闭环。澄清目前仅设计，未上线。完整 Coordinator 尚未提取。原定独立测试评审、上下文收益评估和中型仓库任务必须继续，不能被新需求替代。
