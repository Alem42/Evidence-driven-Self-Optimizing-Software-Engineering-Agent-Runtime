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

## 2026-10-02 最新接续

role_invocations 已实现，默认 initial 兼容旧调用；旧 role_calls 保留并幂等迁移，新调用标识不能绕过未知结果，预算不重置。详见 ../progress/PROGRESS_2026-10-02_001_role-invocations.md。

下一步实现 Planner clarification_request 协议、持久等待和回答接口；RoleRuntime.call 已支持 invocation_id，但 ProjectPlanning 尚未接入新调用。不要宣称澄清已上线。9 月 29 日真实随机数运行现已过期，本轮复验被 deadline 拒绝，后续不要反复运行旧恢复脚本或偷偷改 deadline。需要新运行验证或显式续期设计。

## 2026-10-02 Planner 澄清已接入首版

最新进展 ../progress/PROGRESS_2026-10-02_002_planner-clarification.md。支持 Planner 单轮结构化问题、工作台选项/文本回答、持久等待、回答后独立调用继续；自动模式暂停并继续原任务。真实运行 3c893f93cafe4d46a94c09921455a3bd 经脚本回答和 DB 重开后进入 awaiting_review，共 3 次调用。125 Python 回归通过，前端测试/构建通过；未浏览器验收。

下一步先补 HTTP 和回答后启动前中断测试、浏览器验收；再做 Tester 澄清和独立测试评审，继续上下文收益评估与中型仓库任务。目前只支持 Planner 一轮，不支持中途变更已批准需求后的下游失效。重启服务加载新版。首次真实失败记录保留，不宣称此次执行了新 Go 项目。
