# P3 工作流调优器（AFlow-lite）/ Workflow tuner

分支 `feature/dynamic-roles`。**没有做过任何真实调优运行**（没有调用模型、没有花 token）：搜索逻辑、预算、补丁校验、覆盖项传递都由离线测试证明；`python scripts/tune.py --dry-run` 已在本机跑通（只打印计划）。

## 做了什么
- **可调空间是数据**（`tuning/space.py`）：配置 = 相对用户已保存设置的增量 `{policy, drop_edges}`；补丁 = 至多 3 条操作（`set` 策略项 / `drop_edge` / `keep_edge`）。可调项：`max_escalations`、`stuck_after`、`diagnose_max`、`planner_retries`、各链尝试次数、`diagnose`、`conductor*`、哪些角色直接用最高等级、起始等级；可去掉的边只限 `rewrite_due / needs_diagnosis / arbitrate_due / flip_to_tests / noop_diagnose / noop_retry`（去掉后总有 `always` 兜底边，图仍合法）。应用补丁后必须同时通过 `validate_policy()` 与 `flow.validate()`，否则整个补丁丢弃；模型产出的东西从不被执行。
- **评分**（`tuning/score.py`）：加权通过率 − λ·(每次通过的云端 token/1 万) − μ·(假通过 %)；没有任何通过时成本按全部花掉的云端 token 算。
- **提议者**（`tuning/proposer.py`）：`LLMProposer`（最强的就绪云端模型，经新增的数据角色 `workflow_tuner`，其 schema 限制补丁形状）和 `RandomProposer`（不花 token，作对照与离线测试）。提议者崩溃/乱写 = 这一轮没有提议，搜索继续。
- **逐代淘汰搜索**（`tuning/search.py`）：先评估基线，每代由幸存者提议若干补丁，逐个评估，淘汰较差的一半；候选必须比最优高出 `min_gain`（默认 2 分）才取代它（小样本噪声不算改进）；非法/重复补丁丢弃且不评估；整体预算（候选数、云端 token、分钟）到顶立即停，传给每次评测的 token 上限随剩余预算缩小。产物 `.masa/tuning/<id>/`：`gen_NN.json`、`state.json`、`best.json`、`report.md`（报告写明样本量与“需要更大套餐复核”）。
- **评测运行器支持覆盖项**（`bench/runner.py`）：`config['overrides']` 在构造时校验；策略增量走任务请求的 `policy`，图走 `Console.start_autonomous_project_job(..., workflow=…)`（只有进程内调用方能传，HTTP 接口与评测请求都不接受；图仍须通过 `flow.validate`，只能引用已注册的 guard 与动作）。从不写用户设置。评测机制计数新增 `conductor / conductor_rejected / skeptic / code_review`。
- **命令行** `scripts/tune.py`：`--dry-run` 只打印计划与上限；`--proposer llm|random`；`--apply <id>` 把最优**策略**写回路由设置（去掉的边属于“预设”，只经评测覆盖使用，不写回）。

## 测试
后端 473 通过（新增 `tests/test_tuning.py` 25 项），前端 33 通过。变异验证：让协调器忽略 `workflow` 覆盖，对应测试失败。旧断言只改了一处：`test_roles_registry` 的配置角色集合里加上 `workflow_tuner`。

## 没验证的（重要）
- **从未真实运行过调优**：不知道 LLM 提议者提出的补丁质量，也不知道在真实评测上能否找到高于噪声的改进。快速套餐只有 6 题，评分噪声大；`min_gain` 只是缓解，不是统计保证。
- 评分里的 λ、μ 是经验默认值（每 1 万 token 扣 1 分、每 1% 假通过扣 1 分），没有标定。
- RoleSpec 提示词片段（`tunable`）本阶段没有放进搜索空间。
- 真实烟测建议（需要用户批准 token）：`python scripts/tune.py --suite canary --generations 1 --width 2 --total-tokens 150000 --proposer random`。
