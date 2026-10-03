# 当前接续点

**2026-10-03 晚**：新增任务报告（usage.py）与自动化修复（gofmt 规范化/契约重试/测试规格仲裁/Go 预检），见进展007；`scripts/e2e_live.py` 可用任意 profile 做真实端到端并打印逐次用量，`scripts/show_failures.py` 打印版本链失败证据。

**2026-10-03 前端 v3**：前后端已分离。后端 `python -m masa serve`（纯 API，/api/session 发令牌，CORS 白名单），前端在 `frontend/`（React19+TS+Vite+TanStack Query+zustand+CodeMirror），`npm run dev` 或 `scripts/dev.ps1`。旧 `frontend/src` 与 `interfaces/http/static` 已删除。结构、扩展点、已知后端卡顿根因与待办见 [PLAN_FRONTEND_REDESIGN](../PLAN_FRONTEND_REDESIGN.md)（第 5、6 节）。下一步后端优先：只读连接/增量接口/变更游标，再做路由与预算；前端按扩展点接入。

2026-10-03。先 git status，读 MEMORY/STATUS/PLAN_NEXT_STAGE 和 [进展005](../progress/PROGRESS_2026-10-03_005_local-recovery.md)。代码讲解在 guides/USER_LOCAL_RUNTIME_AND_RECOVERY。

本轮修复模型快照标量事件破坏 Projects.view/Console.artifact；新事件为对象，旧证据兼容读取。用户原任务 9620c8ba3e5946b3b53ab28666e5cd84 实际 waiting_for_input，14.61秒生成问题，真实 HTTP 已恢复读取，未代答。

console.lock 在构造 Console/Jobs 前取得，保护整个服务生命周期；bootstrap.active_job 反映真实 worker；独立轮询保留跟踪。取消不等待模型锁，已收响应保存后拒绝应用，迟到状态不能覆盖 cancelled。故障日志忽略的 service-errors.jsonl；GET /api/diagnostics 和 /api/hardware 接入折叠控制页。

新原生本地生成 files-v1：固定 go.mod，实现先于测试，initial/file:2 等持久调用，gen_progress/partial_files_ref。单文件与修复 schema 限定路径，Domain 再核对；CLI entrypoint package main 前置检查。旧无 generation_mode 草稿仍 bulk；未知请求不重放。

真实测试发现 _test.go 私有包导入及 cannot use/invalid operation 类型错误误走实现修复，已补分类和反例回归；语义断言不因此自动修测试。多文件质量仍需提升，worker completed 不能当 Gate succeeded。

默认 profile 9225807dc987489e8ce482aa6fcaae12，GLM，上下文16384、输出8192、600秒、thinking disabled。另有 Qwen 本地比较配置，默认仍GLM。安装tag必须实时读取，不能猜。全部本轮推理本地，无 API 回退。

Qwen uppercase 明确测试修订最终 dd9ea5f962dc437bb523a34a79c44423：Go/Gate、五项独立 CLI 探针通过；证据 .masa/local-qwen-reviewed-acceptance.json。GLM sum fc668a... 经澄清规划成功，但生成/测试修订仍失败，最终思考调用98aa6c...输出不完整；失败全保留。下一步先提高本地测试质量和准确失败分类，再 digest/上下文/总预算，不扩大复杂路由。

Python186、前端13/构建、Go runner测试通过；视觉未验收。服务启动用新版，读取正在执行的任务用既有 HTTP 或只读 SQLite，别为查询初始化 Jobs/Console（会恢复任务）。每部分验证后提交，密钥/.masa/.tools 不入 Git，核心函数中英文注释。
