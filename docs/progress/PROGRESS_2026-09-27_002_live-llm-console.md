# 进展：真实 DeepSeek 接线与可操作控制台

日期：2026-09-27；任务：P1-06a、UI-02 部分；结果：本增量完成，P1 整体继续推进。

Git 基线 `18ad140`（开始时工作区干净）；后端增量 `7c8254c`，React 增量 `c74acc8`。验收脚本、Go 样例与本报告另作一个提交，方便分段回滚。

## 这轮完成了什么

现在可在前端管理多组 API，选择真实模型启动任务，并让图单步或自动推进。真实 DeepSeek V4 Pro 已通过已有的上下文、工具账本与 Gate 链路跑完成功和失败案例；密钥通过隐藏输入注入本地服务内存，不写配置、源码、Git 或本报告。

## 主要改动

| 模块 | 改动与作用 |
|---|---|
| [chat.py](../../src/masa/adapters/chat.py)、[agent.py](../../src/masa/agent.py) | 有界 JSON 提案、精确 schema、无自动付费重试、usage 与耗时；模型不能绕过真实证据 |
| [profiles.py](../../src/masa/web/profiles.py)、[service.py](../../src/masa/web/service.py) | 多 API 元数据管理、内存密钥、连接测试、运行配置冻结；完整验证图与单步恢复 |
| [SettingsDialog.jsx](../../frontend/src/SettingsDialog.jsx)、[main.jsx](../../frontend/src/main.jsx) | API 添加/修改/删除/默认/测试；真实模型选择；自动继续与实际 provider 展示 |
| [go-complex](../../tests/fixtures/go-complex/ledger_test.go)、[test_chat.py](../../tests/test_chat.py) | 跨文件转账回滚、边界与溢出；协议攻击、虚假成功、重复调用与预算拒绝 |
| [smoke_live.py](../../scripts/smoke_live.py) | 可重复的 9 请求真实验收，隐藏输入密钥，结果只保存非秘密统计 |

## 验证结果

| 验证 | 结果 |
|---|---|
| `scripts/test.ps1` | 59 项 Python 测试、Go tests 与 vet 通过 |
| `frontend` 中 `npm run build` | React/Vite 构建通过 |
| DeepSeek 复杂 Go 样例 | `72e84c19c6f34386ae9b422c7a3f2198`，test/vet/fmt 与 Gate 全通过；6 次模型、4 次工具（含 AST） |
| DeepSeek 原始缺陷 Todo | `c04e167a7a0641738aea9979b5639224`，failed 符合预期；2 次模型、2 次工具（含 AST） |
| 浏览器视觉与点击 | 未完成：Computer Use 返回无可用浏览器；HTTP 接口已自动验证 |

加上连接探测，共 9 请求：输入 16,808、输出 438、总计 17,246 tokens。按已核对的 [DeepSeek 峰时价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)估算约 ¥0.16（所有输入按缓存未命中计），实际扣费未知，以提供商账单为准。用户授权测试总预算 ¥100；本轮没有自动重试或运行额外付费评测。

测试开发中曾发现新断言把协议违规预期写成 failed；实际 Runtime 正确停在 needs_attention。已按既有状态语义修正断言，并验证具体拒绝原因，未放宽执行策略。

## 未完成与风险

当前测试证明真实模型能提出允许的验证动作并解释结果，不证明它已经具备复杂修复能力。四角色交接、动态重规划与自动补丁还未接入。完整图是固定模板，三个验证节点串行运行同一模型。

费用字段未知时为 null；目前没有货币级硬预算，靠次数、输出上限和截止时间控制调用。服务重启会丢弃内存密钥；取消也不能撤回已发出的 API 请求。

## 下一轮从哪里接续

从 P1-04 四角色结构化交接开始，复用当前 provider/AgentLoop 和证据上下文，再推进 P1-05 有限修复。不要重复本轮付费测试；先读 [模型协议设计](../design/DESIGN_MODEL_PROVIDER.md) 与 [当前状态](../STATUS_PROJECT.md)。

本轮新版服务在 `http://127.0.0.1:8766`，密钥已注入其会话内存。操作步骤见 [真实模型指南](../guides/USER_LIVE_LLM_GUIDE.md)。原 8765 服务如果仍开着，需要自行重启后才会加载新的后端。
