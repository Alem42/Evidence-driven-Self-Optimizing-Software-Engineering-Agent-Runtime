# 前端 v3：前后端分离的工程 Agent 工作台

2026-10-03。已实施（浏览器实测见 [进展006](progress/PROGRESS_2026-10-03_006_frontend-v3.md)）。本文覆盖此前所有前端布局约定（含“检查图不再展示”“中央四标签+状态侧栏”）。

## 1. 定位

任务驱动的工程 Agent 工作台：左侧任务历史与二级入口，中央是任务的决策流 / 代码 / 验证 / 日志，顶部是可变的执行图，右侧是节点详情。目标是支撑后续的多 Agent、多模型、可长时间运行的系统；前端只投影后端事实，不创造业务状态。

## 2. 前后端分离

| 项 | 做法 |
|---|---|
| 后端 | `masa serve`（`ui` 为别名）只提供 API，不再托管任何静态文件；`--origin` 可重复，默认放行本机 Vite 的 5173/4173 |
| 前端 | `frontend/`：React 19 + TypeScript + Vite，独立运行（`npm run dev`），`VITE_API_BASE` 指向后端，默认 `http://127.0.0.1:8765` |
| 会话令牌 | 不再注入 HTML。前端从 `GET /api/session` 取令牌，**仅白名单 Origin 能取到**；403 时前端自动重取一次（后端重启后） |
| CORS | 仅白名单 Origin 回显 `Access-Control-Allow-Origin`；Host 校验、`Sec-Fetch-Site: cross-site` 拒绝、令牌校验全部保留 |
| 删除 | `src/masa/interfaces/http/static/`、`pyproject` 的 package-data |

## 3. 布局与信息架构

```
任务历史(进行中/待我处理/最近) │ 顶栏：任务名·真实状态·模型·计时·文件进度·tok/s·跟随执行·停止
＋新任务 / 搜索              │ 图条：Planner─Tester─确认方案─Developer─审核代码─Executor─Gate（可展开成画布 + 版本分支）
设置 / 本地模型 / 主题        │ 标签：对话·决策 │ 代码 │ 验证 │ 日志与版本
                            │ 内容区 ………………………………………………………… │ Inspector（节点详情，可折叠/拖宽）
                            │ 预留 Composer（追加需求，后端支持需求版本化后开放）
```

- **路由即状态**：`/`、`/task/:runId?tab=&file=&line=&node=`、`/settings/:section`。刷新、前进后退可还原。
- **对话·决策**：需求卡 → 当前适用的决策卡（澄清选项卡 / 方案结构化编辑 / 代码审核 / 失败修复）→ 真实验证卡（重新验证、运行程序）→ 活动记录。需要你处理时，标签与图节点出现角标。
- **代码**：CodeMirror 6（Go 高亮），审核阶段可编辑、其余只读；文件标记“改动/已编辑/冻结”；可与修复前内容 diff；源码检查与编译失败的 `file:line` 在行内标红，验证页的失败线索可点击跳转。编辑草稿存于本地 store，切换标签或任务不丢；批准提交的正是可见内容。
- **图**：`entities/graph.ts` 把现有 stages/versions 投影成通用 `{nodes, edges}`，用最长路径分层布局，天然支持分叉与并行。后端以后输出真正的动态图时，只需替换适配器。版本树中修复/测试修订显示为分支。
- **设置中心**（独立页面）：模型配置、本地运行时（Ollama + 硬件）、路由与预算（仅展示候选顺序，其余明确标“未实现”）、Harness（后端真实能力标记）、诊断。
- **跟随执行**开关：开启时跟随执行中的版本；手动选择历史/节点会自动关闭，后台事件只弹 toast，不再抢页面。

## 4. 代码结构

```
frontend/src
  api/        client（令牌/CORS）· types · queries（TanStack Query）· jobs（全局任务跟踪）
  app/        App 路由 · Shell（可拖宽布局）· activity（JobTracker + 跟随跳转）· nav
  entities/   status（唯一状态推导）· graph · profiles · text   ← 纯函数，全部有 vitest
  stores/     ui（zustand：偏好、草稿、toast）
  features/   sidebar · topbar · newtask · thread(+cards) · graph · code · verification · history · inspector · settings
  shared/     ui 基元 · Toasts
  styles/     tokens（明/暗）· app.css
```

关键约束：服务端状态只在 Query；组件不写轮询循环（后台任务由 `ActivityProvider` 统一跟踪，组件卸载不丢任务）；全局 `working` 只用于“开始新模型任务”类按钮，浏览/编辑/看历史永不被锁。

## 5. 如何接入后续后端能力

| 后端新增 | 前端落点 |
|---|---|
| 动态图 / 并行节点 | 新增适配器返回 `Graph`，`GraphPanel` 换数据源；`NodeKind` 已含 `router` |
| 模型路由 / 升级 / 预算 | `settings/RoutingSection`（编辑）；`Inspector`（每次调用的模型/升级原因/用量）；`entities/graph` 的 `escalate` 边 |
| 追加需求（需求版本化） | `TaskPage.Composer` 已预留；需求卡升级为版本时间线 |
| token 流式 | 活动记录/Inspector 订阅同一事件通道；见下 |
| 实时推送 | `api/queries.ts` 当前是自适应轮询（活动 1s / 空闲 5–6s）；后端提供 `/api/changes?since=seq` 或 SSE 后，仅替换这里的 refetchInterval 为失效触发 |
| 非 Go 项目 | `Harness` 设置分区、新建任务页增加语言/模板选项 |

## 6. 已知后端问题（已搁置，按需处理）

前端已避免放大这些问题，根因在后端：

1. **（已缓解）** 每个请求新建 `Store` 都会执行建表与 `PRAGMA`，导致读取与后台写入争锁。现已改为进程内只初始化一次；真正的“只读连接”（`mode=ro`/`query_only`）仍未做。
2. `/api/projects`、`/api/projects/:id` 每次全表加载 runs 并沿父链找根，历史越多越慢；应改增量/缓存。
3. `/api/runs/:id` 每次返回最多 500 条事件；应拆 `summary` 与 `events?after=` 分页。
4. `artifact()` 授权每次扫描该 run 的全部事件；应缓存引用集合。
5. 没有变更游标/SSE，只能轮询；`events.seq` 可直接当游标。
6. 后端任务仍串行，一个任务运行时无法并发开始第二个模型任务（前端只在启动按钮处提示）。
7. 还没有“追加需求版本化”“token 流式预览”“路由/预算”接口。

## 7. 验收状态

类型检查、vitest（21 项）、前端构建、Python 186 项通过；用复制的真实状态目录在浏览器实测：新建页、成功/失败/等待回答任务、代码高亮与错误行标红、图展开与版本分支、设置页、明/暗主题。**未覆盖**：真实模型生成中的实时跟随（未调用模型）、移动端侧栏抽屉、键盘无障碍细节、Playwright 端到端。
