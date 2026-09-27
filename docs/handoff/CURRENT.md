# 中断接续点 / Current handoff

更新：2026-09-28。本轮已完成并验证最小多文件生成链路，没有未完成代码留在工作区。每次恢复仍须检查 Git 与实际文件。

## 当前提交

- 5097de8：中断接续文件。
- 701b240：Developer 多文件生成、批准发布、Runtime 内容绑定与测试。
- 47da258：前端逐文件审核与自动验证。
- 后续文档提交记录见 git log，不在本文件自引用提交号。

## 已完成

已确认 Planner 规格 → 独立 Developer 草稿 run → artifact 中的完整文件集合 → 人工逐文件编辑批准 → 临时准备目录 → Runtime 复制完整快照 → test/vet/fmt → 独立 Gate。

规划/草稿 run 仍禁止直接执行。执行 run 的 project_bundle.approval_ref 绑定完整文件；重复批准同一内容复用执行 run，修改已批准内容被拒。源方案与草稿保留。执行项目位于 .masa/workspaces/<执行 run ID>。

核心文件：src/masa/project_generation.py；project_plan.py；runtime.py；adapters/chat.py；web/service.py；frontend/src/ProjectCodeReview.jsx。

## 验证事实

90 项 Python、4 项前端测试、生产构建通过。另以固定模型提案运行实际 Go 工具，3 项检查通过、执行阶段 0 模型调用。新增路径限制后规划 3 项回归通过。

本轮没有真实付费 LLM 调用，尚未评价实际多文件生成质量；浏览器视觉/点击未验收。不要将模拟模型测试写成真实 DeepSeek 测试。

## 下一步

优先真实小型 CLI 生成验收、生成失败后的反馈重试，再做固定入口 go run、参数/输出/停止。详细范围见 ../PLAN_NEXT_STAGE.md。

已知限制：单次响应最大 8192 token，复杂项目可能截断；文件 20 个、单文件 60 KB、总 300 KB；仅标准库 Go CLI；模型生成测试不等于独立业务验收；没有 ZIP 导出/常驻服务/自动依赖安装。暂无分文件重试，失败保留账本，可显式重新生成。

服务重启会丢失密钥，不写入任何交接文件。不要重复执行未知结果的付费请求。尚未加入跨进程并发审批的专用锁，HTTP 单 Console 使用串行锁；不要同时用自定义脚本批准同一草稿。

## 接续命令与协议

git status --short
.venv\Scripts\python.exe -m unittest discover -s tests
npm --prefix frontend test
npm --prefix frontend run build

额度紧张时立即记录已提交/未验证内容、测试结果和明确下一步，并提交可验证增量。不得为了清空工作区把失败功能写成完成。

## 最新覆盖记录（2026-09-28）
真实 DeepSeek 规划/Tester/Developer 已通过，总计 3821 tokens；执行 run aa386729de444b25afd8e8b0114984ce，3 工具检查和独立 4 CLI 场景通过。代码提交 ca1e443。用户授权本地密钥保存已实现，.masa/provider-keys.local.json 被忽略；不再要求每次重新输入。接续以 PLAN_NEXT_STAGE 的 CLI 前端运行为准，上文未进行真实测试的描述属于之前阶段。

## 最新接续：可观察流程与规划恢复
提交 fa1c5fb、5481331、cf6c5ec。94 Python / 4 frontend / build 通过。真实恢复用户方案 c3e3e648f7fe4bb8866ee650faa4a1a7，等待人工确认；两次 Tester 重试共 2145 tokens。新增 jobs 后台轮询、阶段条、来源链日志复制、Windows 打开工作区、一键确认方案并生成。coverage_warning 是待审核补充，不是验证证据。详见 progress/PROGRESS_2026-09-28_005_workflow-observability.md。下一步仍是前端 go run。
