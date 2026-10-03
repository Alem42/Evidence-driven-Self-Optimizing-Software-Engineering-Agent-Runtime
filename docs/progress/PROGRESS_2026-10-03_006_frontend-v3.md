# 进展006：前端 v3 与前后端分离

2026-10-03。方案与结构见 [PLAN_FRONTEND_REDESIGN](../PLAN_FRONTEND_REDESIGN.md)。

## 做了什么

- 后端改为纯 API：删除静态托管；新增 `GET /api/session`（仅白名单 Origin 发令牌）、CORS 预检；CLI 增加 `serve`（保留 `ui` 别名）和 `--origin`。
- `Store` 表结构每进程只初始化一次，读取请求不再每次执行建表/PRAGMA（缓解读写争锁，见方案第 6 节）。
- 前端完全重写为 TypeScript：TanStack Query、React Router、zustand、CodeMirror 6；统一状态推导 `deriveStatus`；全局任务跟踪取代组件内轮询循环；图用通用 `{nodes, edges}` 模型；设置中心独立页面。
- 旧 `frontend/src` 与旧 UI 产物全部删除。

## 验证（真实）

- `tsc --noEmit`、`vitest`（4 个文件 21 项）、`vite build` 通过；Python `unittest` 186 项通过（测试按新接口调整：令牌改由 /api/session 获取，静态文件改为 404）。
- 用 `.masa` 的拷贝作状态目录启动后端，浏览器实测：新建页、成功/失败/等待回答任务、代码高亮、编译失败行标红、图展开与版本分支、设置页、明/暗主题；后端读取耗时 `/projects` 约 85ms、`/runs/:id` 约 6ms。

## 没验证

没有调用任何模型，所以生成过程中“跟随执行”“后台任务完成后的跳转/toast”“取消”只通过代码与单元测试保证，未实机观察；没做移动端抽屉、Playwright、键盘无障碍。

## 如何运行

见 [USER_WORKBENCH](../guides/USER_WORKBENCH.md)。
