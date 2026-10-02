# LLM 实施入口

开始前依次读取 [长期约定](MEMORY_PROJECT.md)、[当前交接](handoff/CURRENT.md)、[项目状态](STATUS_PROJECT.md)、[下一阶段](PLAN_NEXT_STAGE.md) 和 [系统架构](design/DESIGN_SYSTEM_ARCHITECTURE.md)。先看 git status / log；用户编辑不能覆盖。

当前后端目录已迁至 domain/application/runtime/agents/intelligence/infrastructure/interfaces。旧的 masa.runtime、masa.agent、masa.web 等文件导入路径不再适用。前端从 app/App.jsx 和 features 开始阅读；不要恢复巨型 main.jsx。

实施顺序：明确当前阶段的纵向验收 → 保存接续点 → 实现一个可运行增量 → 边界回归与必要的真实 API 验收 → git commit → 更新人类进展和下一步。模型费用遵循长期授权；密钥只从本地配置加载，不输出。

函数与核心逻辑附中英文注释。已完成能力、部分实现、路线目标必须分开说明。不要为了满足“动态 Graph”而假造完成事件；人工审批和 Gate 独立证据判断必须保留。

常用验证：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
cd frontend
npm test
npm run build
```

真实完整验收使用 `scripts/evaluate_seeded_random.py`，默认调用付费 API；`--run ID` 只复验成功项目，`--plan ID` 是显式新自动任务，复用已批准方案，不恢复失败调用。更小的人工草稿流程使用 `scripts/smoke_project_live.py`。不要无意义重复付费验证。Go runner 有变动补跑 `go test ./...`；前端视觉验收要明确说明是否有真实浏览器，不以构建代替点击。重新安装包使用 uv pip install --python .venv/Scripts/python.exe --no-deps -e .。

每轮结束写 `docs/progress/PROGRESS_日期_序号_主题.md`，更新 CURRENT、STATUS 和 PLAN_NEXT_STAGE。额度将尽时优先保存具体提交、未验证改动、命令、run ID 和恢复步骤；不得将“已编码”标成“已验收”。旧设计已归档，除非调查历史，不应把归档文件当作当前指令。
