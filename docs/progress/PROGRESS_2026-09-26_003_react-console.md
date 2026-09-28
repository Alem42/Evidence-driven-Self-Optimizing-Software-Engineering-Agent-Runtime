# 进展报告：React 本地运行控制台

日期：2026-09-26；任务：UI-01；结果：实现与自动验收完成，浏览器视觉及点击验收待完成。

## 这轮完成了什么

现在可通过 CLI 启动浏览器控制台，在页面创建 Go 检查任务，查看运行图、节点进度、事件、模型请求/响应、工具输出与快照。支持暂停、恢复、取消；修改需求会创建关联任务，保留原记录。按你的补充要求使用 React + Vite，Python 同源托管构建产物，日常不需要单独启动 Node。

API 设置可填写模型、URL 和密钥；密钥仅留在当前服务内存。真实模型尚未接入，界面明确显示 scripted 模式，保存配置不会触发模型调用。

## 主要改动

| 模块 | 改动与作用 |
|---|---|
| [frontend](../../frontend/src/main.jsx) | React 单页、图与证据组件；锁文件固定构建依赖 |
| [web service](../../src/masa/application/console.py) | HTTP 与 runtime 分层；独立工作线程和 SQLite 连接 |
| [runtime](../../src/masa/runtime/engine.py) | 持久化暂停意图；需求变更关联及审计事件 |
| [web tests](../../tests/test_web.py) | API 流程、访问约束、artifact 归属、密钥不落盘验证 |
| [使用指南](../archive/guides/USER_CONSOLE_GUIDE.md) | 启动、操作流程、配置和当前限制 |

## 验证结果

- `npm run build --prefix frontend` 通过；React 19.3.0、Vite 8.3.1，安装审计报告 0 漏洞。
- `scripts/test.ps1` 通过：32 项 Python 测试，Go tests 与 go vet 均通过。
- 真实 HTTP → Runtime → Go 验证完成，保留 run `97a2a69f9cda4314acd478a9e0cb3f8e`：暂停后恢复成功，2 次脚本模型调用、1 次工具调用，没有重复执行工具。
- 当前控制服务已在 `http://127.0.0.1:8765` 启动。
- 浏览器工具返回无可用浏览器，因此未进行实际 React 页面渲染、截图或点击验收；不能把 HTTP 测试等同于浏览器验收。

## 未完成与下一步

UI-01 保留最后的浏览器验收项：按使用指南创建任务、查看证据、恢复、修改需求、检查窄屏布局。真实 LLM、代码修复、多 Agent 和动态图重规划仍属 P1，逐行流式日志与复杂布局后置。接续先读取 [状态](../STATUS_PROJECT.md) 和 [控制台设计](../archive/design/DESIGN_LOCAL_CONSOLE.md)，有可用浏览器时补验收，再继续 P1-01。
