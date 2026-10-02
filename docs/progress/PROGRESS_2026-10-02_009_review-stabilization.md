# 稳定化：语义评审失败状态与 source_id

2026-10-02。

## 修复

semantic_review.py 现在捕获模型调用/响应校验异常，事务写入 semantic_review.status=failed、Run.status=failed 和 semantic_review_failed 事件，错误包含 review_id。父项目记录失败通知但不改变审批或 Gate。failed 不能隐式恢复重发；completed 恢复同时校验取消和 deadline。响应已落盘、业务发布前中断仍可复用缓存。

引用改为 sources-v1：Runtime 构建 source_id→原文表，模型只输出 source_id、severity、explanation、suggestion。无需多层索引或复制原文。旧 evidence/evidence_field 协议仍可读，旧运行按原协议恢复，新协议提取结果可重复校验。

## 真实验证

新协议第一次请求收到 HTTP400，已正确保存失败业务状态，运行480f43966c694a5ea909ea5163ef2243。原因定位为新提示未明确包含 JSON 一词，补充后第二次真实 DeepSeek 调用成功。

复杂历史随机数完整计划评审0562797c4dba44caab2d7182f4f435c7：7条 findings 均引用有效 source_id，报告 needs_attention。不表示建议全都正确。其中建议放宽标准库项目 go.mod 检查不适用于当前模块策略；负数和极大整数属于有意义边界，不能删除。进一步强化提示，但未额外重复付费测试，也没有自动采用任何意见。

另一个发现：历史方案明确写当前时间 seed，而此前实现修复版本采用固定 PCG seed。真实工具通过不等于此业务条款已满足。该需求/源码一致性问题纳入下一阶段，不静默改历史快照。评审读取计划，没有读取实现，因此无法自动判断源码符合该条款。

## 验证与范围

语义评审4项通过，覆盖source解析、失败状态/事件和重启不重发。完整回归结果见最终回复。本轮没有新前端功能或浏览器验收。未跟踪的 src/masa/domain.py、runtime.py、workflow.py 是本轮开始前已有文件，未更改或提交。

状态文档已重写为当前事实；历史进展仍保留。下一阶段收敛目标是语义评审前端/HTTP恢复、规格与源码一致性检查、角色状态统一；变异/上下文收益/中型仓库按该基础继续。

最终验证：138项Python完整回归通过；最后增加错误恢复目标校验后，4项语义评审测试再次通过。真实失败状态与已完成报告缓存复用再次验证，无新增API调用，父Gate不变。
