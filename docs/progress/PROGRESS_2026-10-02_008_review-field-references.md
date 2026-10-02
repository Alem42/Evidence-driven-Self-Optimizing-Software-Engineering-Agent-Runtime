# 语义评审字段引用增量

2026-10-02。

将 Reviewer 引用从自由复制文本扩展为 evidence_field：模型提供 acceptance/check/case 索引和字段名，Runtime 从对应输入提取原文。旧 evidence 协议仍可读；提取后的报告再次校验保持幂等；字段/索引不合法、报告原文冲突仍拒绝。提示给出有效索引范围。

验证：语义评审3项、模型协议11项通过，git diff --check通过。真实随机数完整计划新增两次 DeepSeek 调用分别因验收索引、用例索引非法被拒绝，未改写父项目或 Gate。本轮未获得复杂评审成功，未添加自动重试或继续重复消耗费用。

经验：仅更改引用格式不足以解决模型对多层数组索引的错误。下一步应由 Runtime 预分配扁平 source_id（例如 acceptance:0、check:0:purpose、case:0:0:expected），模型只引用允许的 ID，Runtime 查表提取原文；保留业务验收关联单独校验。先在相同真实输入验证稳定，再接前端，不能在当前失败率下默认开启自动评审。

新测试代码在 tests/test_semantic_review.py，协议在 domain/test_review.py 和 agents/protocol.py。此前结构解释仍见 ../guides/USER_RUNTIME_STRUCTURE_2026-10-02.md。上下文收益、中型仓库任务保持路线，不增加角色层数。
