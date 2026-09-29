# 检查点拆分与主动澄清方案

2026-09-29。

已实现：application/workflow.py 的 WorkflowCheckpoint 从 console.py 接管阶段检查点和重复断言计数规则，不新增数据库副本。增加数据库重开、同一验证幂等计数、第三次相同断言停止以及新断言重置的测试。完整 121 项 Python 回归通过。

真实历史 DeepSeek 随机数任务的自动恢复脚本再次通过，复用验证 a4d566c7882d432a8be3908ebc0f5895，attempt=1；本轮无新 API 调用，也未重新执行该项目 Go 检查。此次验证的是编排重构不破坏已有记录恢复。

新增设计见 ../PLAN_RUNTIME_CLARIFICATION.md。主动提问尚未上线；明确区分提问、审批和 Gate，定义结构化问题、持久等待、幂等回答、角色新调用与需求版本失效。先 Planner 后 Tester。

原路线继续：先统一 Role Step/Attempt 与调用标识，再实现 Planner 澄清；随后独立测试评审、上下文收益对照评估、中型仓库任务。当前 WorkflowCheckpoint 只是 Coordinator 的第一步，完整执行循环仍在 console.py，勿声称已经完成 Coordinator 提取。
