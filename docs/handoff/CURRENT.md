# 当前接续点

**2026-10-05（下午）**：见进展016。评测实时显示、23 题、云端逐文件生成、入口规则（main.go 不许读输入）、Tester 重试带原因。**真实对比：混合（本地起步）目前不如纯云端**（57% vs 79%，云端 token 基本持平，慢 1.75 倍），简历里“降本 62%”已删除；精简版简历 378 字。用量提醒：用户在意 token，之后只重跑单个任务。已知剩余：Tester 偶发无效输出、coins 的整包语法门、maze/L9 过不去。

**2026-10-05（白天）**：见进展015。W0 评测基线完成（设置→评测、scripts/bench.py、17 题 10 级、独立判官、三层上限）；评测抓到假通过（hello 的退出码）、wc 的修复改测试失败，均已修。纯云端基线：L0–L6 约 92%，L7–L8 云端能做，L9 regex 死在“云端一次生成被截断”（下一步：云端也逐文件生成）。**本地评测基线待 GPU 空出来再补**（用户在用显卡，不要跑本地模型）。

**2026-10-05 凌晨**：见进展014。新增：Diagnoser 逐条核对 + 确定性改判（修了“测试源码从未送进 Diagnoser”的 bug）、整体重写（fix-v1 的 rewrite 节点）、提示泄漏检查（leaks.py）、测试静态检查（testlint.py）、module 前缀修复、模型梯子上限 4→6、测试文件紧凑提示。CSV 任务真实通过，但盲测核对与整体重写**尚未被真实运行触发**（仅测试覆盖）。下一步：W0 评测基线（多次重复统计通过率）。

**2026-10-04（深夜）**：见进展013。**cmd 弹窗根因**：我的 Ollama 演练脚本用 DETACHED_PROCESS 重启了 `ollama serve`，无控制台的服务每次加载/卸载模型拉起的 3 个子进程各弹一个窗口；已用隐藏窗口重启并实机复测，脚本已改；监控工具 `scripts/watch_procs.py`。前端：各角色实际使用的模型（取自账本）、默认自动+有界升级且不再选模型、设置返回/关闭、随机建议任务、报告“修复过程”。后端：证据源码片段、前几轮账本摘要、确定性 import 修复、Planner 钉死边界语义。M2 已细化为 W0–W7（PLAN_MULTI_MODEL_RUNTIME §10）。**重启 API 服务**后新 runner/前端才生效。

**2026-10-04（晚）**：见进展012 与 PLAN_LOW_MODEL_QUALITY。新增：逐文件语法门、测试先行（强模型写测试）、Planner/Tester/测试修订最高等级、按进展延长轮数、实现无改动⇒转修订测试、子进程不弹 cmd（需重启 API 服务）。文本统计 CLI 仍未稳定通过（规格边界语义/弱模型方案）；下一步见 PLAN §5：尝试摘要、证据带源码片段、约束解码、冻结前红灯检查、Planner 钉死边界语义。M1 不推进 M2，路线待重新明确。

**2026-10-04 M1 后半完成**：见进展011。新增 application/{ownership,flow,workflows}.py（归属分析、声明式引擎、fix-v1）、Diagnoser 角色、无进展拒收、`POST /api/runs/<id>/auto-fix`（失败后一键继续）、`GET /api/workflows/fix-v1`、失败卡“自动修复（推荐）”。真实模拟暴露并修复：云端返回截断 JSON 被当成传输失败而不升级。下一步：Worker 租约/自动续跑、报告页显示修复子图轨迹、M2。显卡归用户，跑完确认 /api/ps 为空。

**2026-10-04 M0.5 收尾 + M1 前半完成**：见进展010。新增：application/triage.py（可行性预检）、infrastructure/orphans.py（孤儿 llama-server 检测/清理）、providers.py（API 账户：官方 /models 与余额）、TransportFailure + 账本层免费本地重试（roles.py `_free_retry`）、failure_signature 卡死检测、模型次序/API 账户/路由默认值三个设置页。下一步：Diagnoser、声明式工作流、Worker 租约。**运维要点**：杀 ollama serve 会留下占显存的孤儿 llama-server（本地运行时页可清理，scripts/drill_ollama.py 已自动清）；跑完本地模型要卸载，并确认显存回落。

**2026-10-04 M0.5 前两项完成**：硬上下文拦截（domain/tokens.py、llm.py）、崩溃恢复演练（tests/test_recovery_drill.py，真硬杀子进程）、真实升级验证；共修 4 个缺陷（被中断验证、澄清后重规划、规划重试挤占修复轮次、准入过严）。恢复语义：已完成调用不重复；结果未知的请求明确停下不重放；被中断的验证新建验证 run。下一步：M0.5 剩余项，再 M1（首项：幂等分级——免费本地调用结果未知时自动重试，演练场景 B/C 目前需人工）。显卡归用户，跑完本地模型要卸载（keep_alive 0）。

**2026-10-03 M0 完成**：分支 feature/multi-model 已实现任务级预算与路由（routing.py 纯策略、router.py、coordinator 全部模型调用经路由器、报告 routing 区块、前端策略/预算/报告）。下一步 M0.5（杀进程恢复演练、提供方硬上下文拦截、同级轮换、按角色起始等级、可编辑预算）再 M1（Diagnoser、声明式图、worker 租约）。详见进展008 §5。注意：真实本地→云升级端到端未验证；用户需要 GPU 时不要跑本地模型。

**2026-10-03 深夜**：git 已修复（main 原为无关历史的 Initial commit，现已并入 develop 并快进，二者同为 f5fd53d）；新分支 `feature/multi-model`，下一步按 PLAN_MULTI_MODEL_RUNTIME 做 M0（任务级预算 + RoutingSnapshot + route() + 修复升级阶梯）。

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
