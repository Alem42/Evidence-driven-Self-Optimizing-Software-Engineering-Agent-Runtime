# 代码结构（2026-10-07 整理后）

只动了结构，没动行为：全量测试 475 通过（与整理前一致），差分检查 `scripts/eval/loop_equivalence.py` 与改动前的提交逐项相同。

```
src/masa/
  domain/            纯数据与规则（模型、提案校验、令牌估算、澄清、评审规则）
  roles/             RoleSpec 注册表：角色即数据（specs/*.json + *.prompt.md）
  agents/            提示词/校验器/schema 的分发（读 roles），脚本化假模型
  runtime/           账本驱动的执行：engine（Gate）、roles（角色调用）、tools、patches、live
  infrastructure/    外部世界：store、llm/streaming、ollama、runner、settings、hardware、capability…
  intelligence/      上下文与记忆：索引、修复上下文、记忆
  application/
    orchestration/   编排：coordinator、flow/workflows（声明式修复子图）、router/routing（预算与路由）、conductor、attempts
    checks/          确定性检查与分析（0 token）：ownership、check_policy、testlint、leaks、goimports、entrypoint、snippets、triage
    review/          评审与变异：semantic/source/test_review、single_file、mutation
    (根)             console（对外的门面）、projects、planning、generation、applications、reports、usage
  bench/             评测：任务、独立判官、运行器、报告
  tuning/            工作流调优器（数据空间 + 搜索）
  interfaces/        命令行与 HTTP 服务（含静态托管与演示模式）
scripts/
  *.ps1              开发环境入口（构建、激活、测试），保持在根
  eval/              评测与分析：bench、p2_eval、tune、failure_taxonomy、loop_equivalence、calibrate_tokens…
  smoke/             真实环境冒烟与验收：smoke_*、e2e_live、docker_smoke、drill_ollama、accept_*…
  ops/               运维：make_demo_state、watch_procs
frontend/            React 前端
runner/              Go 的沙箱执行器（Windows Job Object / Linux 进程组）
tests/               后端测试（平铺；tests/golden 是黄金样本）
docs/                文档入口见 00_INDEX.md
```

依赖方向（从上到下只能向下依赖）：interfaces → application → runtime/agents/roles/intelligence → infrastructure/domain。
**还没做**（风险更高，等需要时再做）：拆分 `application/console.py`（约 900 行的门面）；`agents/` 与 `roles/` 的进一步合并；`docs/` 里 PLAN_* 的归并。
