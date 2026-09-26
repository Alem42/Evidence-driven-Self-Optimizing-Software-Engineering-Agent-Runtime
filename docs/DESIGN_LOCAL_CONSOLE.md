# 本地 React 控制台设计

任务 UI-01：按用户要求提前提供可观测界面，不改变 P1/P2 核心实施顺序。

## 边界与数据流

`React → 同源 JSON HTTP → Console application service → Runtime / Store → Go runner`

- `frontend/src/main.jsx`：组件、交互状态和轮询；`api.js`：统一请求与错误处理；`style.css`：自包含样式。React + Vite，不增加路由、全局状态库和 UI 库。
- Vite 构建到 `src/masa/web/static`。构建产物作为 Python package data 分发。普通用户只启动 Python；前端开发者需要 Node/npm。当前无独立开发服务器流程，修改后构建并刷新。
- `web/server.py` 负责 HTTP 与访问约束，`web/service.py` 负责用例和后台执行，`web/settings.py` 隔离配置。HTTP 不直接操作 runner 或执行任意命令。
- 每个 HTTP 请求及工作线程独立建立 SQLite 连接。详情读取使用读事务；单服务只允许一个 worker，全局 runtime owner lock 继续约束其他 CLI/服务进程。
- Graph 展示直接读取 GraphSpec 的节点与依赖。WorkflowGraph 组件使用独立 graphLayout 按依赖分层，适合小图；同层不表示并行执行。后续可单独替换布局，不修改后端合同。完整验证模板位于 Python workflow 层，由 Web 选择。

## HTTP 合同

| 路径 | 方法 | 用途 |
|---|---|---|
| `/api/bootstrap` | GET | 默认仓库、runner 可用性、已实现能力 |
| `/api/runs` | GET / POST | 最近 100 条运行摘要 / 创建并启动 |
| `/api/runs/{id}` | GET | run、steps、最近 500 events、tools、attempts |
| `/api/runs/{id}/pause` | POST | 请求节点边界暂停 |
| `/api/runs/{id}/resume` | POST | 恢复已暂停或中断的执行 |
| `/api/runs/{id}/cancel` | POST | 请求取消 |
| `/api/runs/{id}/revise` | POST | 新建关联任务，保留原证据 |
| `/api/runs/{id}/artifacts/{hash}` | GET | 经 run 引用归属与哈希校验的证据 |
| `/api/runs/{id}/report` | GET | Markdown 运行报告 |
| `/api/settings` | GET / POST | 非秘密配置、密钥存在状态 / 更新配置与内存密钥 |
| `/api/settings/test` | POST | 一次真实 JSON 提案协议连接测试 |

创建参数为 `repo, goal, operation, pause_after`，可选 `budget` 与既有 Budget 字段相同。默认 deadline 1800 秒，受服务端额度上限限制。所有 API 要求 `X-MASA-Token`，页面初次加载注入会话值；不放入 URL。

P1-06a 新增创建参数 `provider: scripted|live`、`api_profile_id`、`intelligence`、`full_checks`。完整图 test/vet/format → Gate，仍由固定模板和串行 scheduler 执行。resume 可传 `pause_after:true` 在下一个完成节点暂停；省略则自动推进。配置管理与调用边界见 [DESIGN_MODEL_PROVIDER](DESIGN_MODEL_PROVIDER.md)。

## 不变量与扩展

暂停意图落盘到 run_controls，执行线程在节点边界处理；恢复时清除该意图。修改需求是新 run，不重用旧证据充当新结论。原运行目标不可被页面覆盖。

凭据与运行状态分离：密钥仅服务内存，API 不回显、不打印请求日志、不入 SQLite。URL 不接受用户信息、query 或 fragment。后续接入模型必须保持这一边界，并明确注入 provider；不能让“配置已保存”自动被解释为“模型已验证可用”。

页面所有运行文本由 React 转义渲染，不使用 HTML 注入。服务只允许本机 Host/Origin、静态文件白名单、限长 JSON 请求与当前 run 引用的 artifact。本地会话 token 防范跨站请求，不构成多用户身份认证。

P1 新增角色、上下文证据和 graph revision 时复用相同读接口，扩展结构化事件；P2 成本数据存在后再增加成本统计。实时日志/SSE、事件分页、大图布局和更完整的 HITL graph revision 后置。

选型参考：[React 从零构建应用](https://react.dev/learn/build-a-react-app-from-scratch)、[Vite 构建选项](https://vite.dev/config/build-options)。
