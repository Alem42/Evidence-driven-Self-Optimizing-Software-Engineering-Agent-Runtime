# 固定模型恢复与真实项目验收

本轮实现任务模型配置快照，加强本地项目结构化输出，并跑了本地和云端同一随机数任务。

## 做了什么

Settings.provider 将固定配置、profile ID 与版本绑定到 provider。自动任务创建时保存快照；RoleRuntime 首次调用时将无密钥快照与请求意图一起落盘。恢复从快照构造原 provider，仍用原 profile/原地址取凭据。修改模型、等级、输出参数不隐式改变旧任务；删除、禁用、换地址拒绝恢复。旧无快照任务保留兼容路径。

核心代码在 infrastructure/settings.py、runtime/roles.py 和 application/console.py，没有另起状态机。实际模型调用仍由 RoleRuntime 记录，工具验证和 Gate 不变。agents/schemas.py 为 Ollama Planner、Tester、Developer 提供结构约束；schemas 不替代 domain 校验，也不证明测试质量。验收脚本新增 --profile，可显式选择模型而不改默认配置。

## 真实结果

| 模型 | 结果 |
|---|---|
| gemma4:12b | 初次规格字段错误；结构约束后拿到规格，但发生多余澄清、Tester 引用越界；显式复用规格再试时重复检查被拒绝。未进入完整代码闭环 |
| DeepSeek 固定 API | 完整生成，经实现缺 time 导入、测试缺 strconv 导入、格式问题三轮修订后通过 Gate 与25项独立CLI探针 |

云端最终版本 e5e7cbd1fad041cea3bbe5ee32807da4，任务14a60d2152454121973d3e7f3e2e2d26。累计6次模型调用、实际43484 tokens，用量均有返回；未配置可靠费用计算，人民币费用标未知。不是自动本地升级；是显式固定模型对照，不能据单一案例推出通用成功率。

本地失败版本 c1cf5651e9d64934985ecd8a515e3947、6411f45003eb4fd993c4dcc7534b0f96、d53ac61e78c64f8c9f56f8dd6c5fcf95。证据在 .masa/model-project-comparison.json、seeded-random-acceptance.json 和调用账本。没有删除或弱化失败断言，没有自动重放未知请求。

Python151项全量通过；随后快照校验小改动又通过6项模型配置测试。本轮未改前端。提交73ae3bb是配置恢复，44365ce是结构输出与模型验收选择。

## 接下来

快照完成的是固定单模型配置，仍需本地权重 digest 和上下文准入。随后实现全任务预算，再启用本地自修/升级。真实案例确认小模型协议失败需要升级分类，云端代码也仍需要真实工具证据；不允许升级绕开批准规格、冻结测试和 Gate。
