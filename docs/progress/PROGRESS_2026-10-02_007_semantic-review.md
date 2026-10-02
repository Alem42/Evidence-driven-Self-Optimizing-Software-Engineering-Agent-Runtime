# 独立模型语义评审首版

2026-10-02。

新增 domain/test_review.py（引用约束）、application/semantic_review.py（独立持久评审）、agents/protocol.py（Reviewer 提示与解码）和 scripts/review_test_semantics.py（脚本入口）。RoleRuntime 管理一次模型预算，保存输入/配置/响应。评审运行 Runtime 禁止工具执行，不影响父项目或 Gate。尚未加入前端、Coordinator 或自动修订。

真实 API：针对成功随机数项目 d50899063f04459daf4bfdd0a806f85f 的完整历史计划，两个新评审调用均返回不匹配引用，被协议拒绝；没有放宽校验，没有宣称成功。另一个显式构造的随机断言冲突负例使用真实 DeepSeek：review_id 43749f5060634e0d8596a70342c878c2，正确逐字引用 all outputs must be different，指出与允许重复冲突并建议改为范围验证，report=needs_attention。负例改变的是评测输入，不是父项目已批准方案。总计三个真实 API 调用，无自动重试。

测试覆盖编造引用拒绝和数据库重开缓存复用；相关模型评审2项与Runtime18项通过。完整回归结果见最终回复。本轮未改变前端，没有浏览器验收。

详细结构与近几轮流程见 ../guides/USER_RUNTIME_STRUCTURE_2026-10-02.md。下一步先改进复杂输入引用稳定性，再接前端；继续原主线统一角色状态、上下文收益、中型任务。

最终完整136项Python回归通过。
