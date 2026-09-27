# 进展报告：Go 代码理解与证据上下文

日期：2026-09-27；任务：P1-02、P1-03；结果：完成最小实现并接入真实运行链。按用户要求，本轮连续推进两项相依任务。

## 完成了什么

Go runner 现在能提取 AST 声明、方法接收者、imports、源码范围、注释、测试候选和语法调用。Python 将索引绑定 snapshot/profile/索引器版本，提供可解释检索与源码证据。语法错误标记 partial，动态调用保留 unknown，不声称精确调用图。

源码观察会持久化为 run 内记忆；假设保持 inferred，过期和冲突材料被过滤，失效沿依赖传播。Context Builder 保留必需契约、工具结果及未解决项，按角色挑选材料，记录 included/omitted 与预算。开启功能后，每次真实 AgentLoop 请求都使用这一流程。

## 文件与提交

| 提交 | 功能与入口 |
|---|---|
| `011aef4` | [Go AST 索引器](../../runner/internal/indexer/index.go)，复用 runner 隔离与时限 |
| `b469620` | [检索](../../src/masa/intelligence.py)、[记忆](../../src/masa/memory.py)、[上下文](../../src/masa/context.py)、实际 loop 接线及测试 |
| `8dc4fc5` | 前端启用开关、能力标记、上下文报告摘要 |

本轮起始工作区干净，无需新增重复基线提交。新增函数继续中英文注释，核心版本校验、预算和发布逻辑带解释。文档与本报告另行提交。

## 验证结果

- 最终 51 项 Python 测试通过；Go tests 和 go vet 通过；前端 Vite 构建通过。
- 新增 10 项 Python 集成测试及 Go AST 测试：同名接收者、匿名函数、语法损坏、索引输出超限不发布、旧索引拒绝、记忆失效/隔离/冲突、必需内容保护、模型实际输入与去重。
- 演示 run `3d966205631b497abab202fef2dc9443` 成功：补丁 → AST → 两次带证据的 scripted 请求 → Go 检查 → Gate，3 次工具预算。`inspect` 命令已验证，可查看候选和上下文清单。

## 取舍与下一步

采用小仓库词法扫描，没有 FTS/embedding；记忆按整个快照保守失效，没有跨 run 复用。预算是 UTF-8 字节硬限制，非实测 token；角色目前只是材料视图。四角色协作、动态图和真实 provider 尚未完成。前端视觉/点击验收仍待可用浏览器，不与构建通过混淆。

下一项 P1-04 四角色与有界定向交接，再推进 P1-05；接续读 [当前状态](../STATUS_PROJECT.md)。运行方式和限制见 [使用指南](../guides/USER_CODE_INTELLIGENCE_GUIDE.md)。
