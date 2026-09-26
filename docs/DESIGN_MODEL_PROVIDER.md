# 有界真实模型适配器

任务 P1-06a：按用户要求提前接入一个 provider，以真实验证已有 Runtime。P1-06 四角色接线仍依赖 P1-04；本增量不是完整多 Agent 修复系统。

## 调用链与职责

`React → Console → Settings.provider → ChatProvider → AgentLoop → Tools → Gate`

- `adapters/chat.py` 使用标准库调用 OpenAI-compatible `/chat/completions`，不引入 SDK 或第二套 AgentLoop。
- `web/profiles.py` 管理最多 20 个命名配置；`settings.py` 保留兼容导入。配置元数据原子保存，支持旧版单配置迁移。
- 创建时绑定 provider；run 保存不含秘密的 model_profile。恢复寻找同身份配置，而非跟随默认 API 切换。找不到配置或内存密钥时拒绝恢复；不静默切回 scripted。
- 前端明确选择 Scripted / Live LLM；单验证图与完整验证图共用串行调度器。完整图包含 test、vet、format 三个独立验证节点及依赖三者的最终 Gate，不是 Planner 生成的动态图。

## 提案协议与执行边界

JSON mode 只帮助输出合法 JSON；本地仍校验结构。只接受两种精确字段集合：

```json
{"type":"tool_call","operation":"go_test","arguments":{}}
```

```json
{"type":"final","summary":"基于实际工具结果的简短判断"}
```

operation 必须等于节点所选操作，不能带命令参数。截断、拒绝、未知字段、非法 envelope 和重复工具请求不能继续执行。无真实工具结果时 final 不生效。模型文字不能覆盖 exit code 或 Gate 的账本校验。仅展示结构化提案和结果摘要，不记录提供商返回的 reasoning_content。

这轮有意不使用提供商原生 function calling：当前是小型 JSON action 协议；后续增加角色 schema 时保持 Runtime 的权限校验独立于提供商格式。

## 预算、错误与用量

- 调用前保存实际 context_ref 并扣 model_calls；失败或重启不返还已扣次数。
- 无自动付费重试。HTTP 错误只公开状态码，不读取可能回显敏感内容的错误正文。
- 每次输入最多 262,144 bytes，响应最多 1 MiB；输出 token 上限 64～8,192 可配置，请求 socket 超时 1～60 秒，读取时检查总截止时间。慢响应跨读取可能略超截止，返回后再次检查 run 截止时间。
- 取消不能撤回已经发给提供商的请求；等待响应/超时后丢弃结果，费用仍可能发生。Go 工具取消沿用原进程树终止能力。
- 记录提供商返回的 prompt/completion/total tokens 和调用耗时。未知账单金额为 null，失败请求缺 usage 也不写零费用。货币级硬预算尚未实现；本轮真实验收通过固定 9 次请求和每次最多 512 输出 token 控制开销。

## 凭据与本地 API

密钥只在当前服务内存，配置 GET 只返回 key_configured；不落源码、provider.json、SQLite 或 artifact。切换 Base URL 清除该配置旧密钥；重定向被拒绝，不把 Authorization 转发到新地址。模型摘要在 JSON 解码后再次脱敏，错误响应不暴露原文。

`POST /api/settings` 支持 save / select / delete，`new:true` 新建，`id` 指定配置，`clear_key:true` 清除密钥；空 api_key 保留已有密钥。`POST /api/settings/test` 使用保存后的配置进行一次协议探测，不发送仓库内容。测试结果只在会话中保留，配置或密钥变更使其失效。保存成功与连接测试成功分开表示。

真实任务会发送需求、Context Builder 选入的源码证据和工具结果。Go 执行环境不继承 API 密钥。

## DeepSeek 已验证配置与参考

Base URL `https://api.deepseek.com`；模型 `deepseek-v4-pro`；输出参数 `max_tokens`；验收使用 `thinking:{"type":"disabled"}`，输出上限 512。UI 新配置默认输出上限 2,048，可修改。关闭思考用于先验证接线，不代表复杂推理能力的评测结果。

参考：[DeepSeek Chat Completion](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)、[DeepSeek 模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[OpenAI Structured Outputs / JSON mode](https://platform.openai.com/docs/guides/structured-outputs)。参数支持与价格以调用时官方资料为准。
