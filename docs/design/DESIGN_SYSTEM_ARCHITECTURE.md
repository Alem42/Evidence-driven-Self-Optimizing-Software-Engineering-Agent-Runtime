# MASA 系统架构：项目工作台与证据驱动 Runtime

2026-09-28。本文取代旧的并列架构入口。项目目标仍是 Evidence-driven Software Engineering Agent Runtime；Self-Optimizing 是长期能力目标，当前不声称已实现自主优化。

## 1. 用户主线

选择 API / 模型 → 输入需求 → Planner 设计结构 → Tester 提出验证方案 → 人工确认并生成 → Developer 生成完整代码与测试 → 人工审核代码 → Executor 执行真实检查 → Gate 核对证据。

失败的验证可以进入 Developer 修复分支，保留原测试和模块，再审核、验证。前端按项目组织历史，图显示角色阶段，图下面显示响应、审核和实际结果。模型完成、审核通过、测试通过是三种不同事实。

## 2. 后端目录与职责

| 目录 | 主要内容 | 边界 |
|---|---|---|
| `domain/` | `models.py` 运行/图/预算；`proposals.py` 规格、检查和文件校验；`projects.py` 项目摘要 | 纯数据与规则，不调用模型、数据库或应用服务 |
| `application/` | `planning.py`、`generation.py`、`projects.py`、`console.py`、`reports.py`、`single_file.py` | 串联用例；项目视图从历史证据投影；Console 暂时承担应用装配及后台任务 |
| `runtime/` | `engine.py`、`graph.py`、`tools.py`、`patches.py` | 状态推进、预算、工具许可、检查图编译、独立 Gate；不负责 HTTP 或 UI |
| `agents/` | `protocol.py`、`execution.py`、`handoffs.py`、`routing.py`、`scripted.py` | 角色指令、响应契约、角色调用与受限策略；不能自己授权执行 |
| `intelligence/` | `index.py`、`context.py`、`memory.py` | 代码索引、上下文选择、可追溯记忆；尚未覆盖所有项目生成阶段 |
| `infrastructure/` | `llm.py`、`store.py`、`runner.py`、`settings.py`、`locking.py`、`workspaces.py` | HTTP 提供商、SQLite/artifact、Go 执行器、配置和本地文件实现 |
| `interfaces/` | `cli.py`、`http/server.py`、`http/static/` | 参数/请求/响应与静态文件；不在路由中新增业务策略 |

依赖方向：接口调用应用；应用组织 Runtime、角色、Intelligence 和基础设施；Runtime 校验 domain 规则，通过现有 Store/Runner 执行。当前是实用分层，不是严格依赖倒置框架：Runtime 仍使用具体基础设施，Console 仍有部分查询和任务管理。避免为目录整齐建立大量一行包装类、通用插件总线或微服务。

读取顺序：`interfaces/http/server.py` → `application/console.py` → `planning.py / generation.py` → `agents/protocol.py` → `runtime/engine.py`。代码理解主线另读 `intelligence/context.py` 和 `index.py`。

## 3. 数据与角色图

沿 `parent_run_id` 找到稳定的项目根标识。同一项目包含方案记录、代码草稿记录和验证运行；当前版本是读取投影，没有新增 Project / Revision 数据表，也没有重写历史数据库。

`GET /api/projects` 返回项目导航；`GET /api/projects/{run_id}` 返回选中版本的来源链、角色阶段和版本列表。`model_requested`、`model_completed`、`model_failed` 驱动角色状态；审批状态驱动人工节点；真实 step 状态驱动 Executor / Gate。读取使用同一数据库快照。

目前角色图是动态状态与修复来源链的可视化投影；未来阶段以 pending 预告。它还不是 Planner 任意修改节点和边的调度图。实际检查图由 Tester 从 go_test / go_vet / go_fmt_check 中提议，Runtime 校验并附加独立 Gate。不得把这两种图混为一谈，也不得在前端凭计时器宣布模型完成。

## 4. 前端组织

`frontend/src/main.jsx` 仅挂载；`app/App.jsx` 负责页面装配，`app/useWorkbench.js` 统一项目选择和后台任务轮询，`app/styles.css` 管理工作台样式；`api/client.js` 统一请求；`features/projects/` 负责角色图、方案审核、代码审核、结果、修复和诊断；`features/settings/` 负责 API 管理。

主页面不再同时展示单文件生成、多个创建表单和所有底层控制按钮。日志、检查依赖图和版本记录收进诊断区。旧单文件 API / CLI 能力保留，主工作台以完整项目为入口。产物仍由 Vite 构建至 `src/masa/interfaces/http/static/`，单个 Python 服务提供，无新增前端服务器依赖。

## 5. 必须保留的约束

- 人审绑定当前 artifact 引用，过期方案不能误批准；规划批准不授权执行占位工作区。
- 发布完整文件快照后再运行；修复不能改原测试、go.mod 或目录契约。
- 模型只提议；Runtime 的工具白名单、预算、输出上限和 Gate 不能移到前端。
- SQLite 记录真实事件与不可变 artifact 引用；失败记录保留，重试创建来源明确的新记录。
- API 密钥仅存在本地忽略配置；页面/日志/Git 不回传明文密钥。
- 角色响应展示结构化结果和证据，不展示或伪造模型内部思维链。

## 6. 下一步扩展边界

先补齐前端运行生成 CLI 的真实闭环，再把规划/生成的角色调用纳入可恢复的持久调度。Reviewer、任意自适应图、跨任务经验学习和模型路由按可验证收益逐步引入。记忆首先保存证据来源与有效期；代码理解首先服务实际修改范围和测试选择；不要先上向量数据库、全自动自修改或复杂分布式通信。
