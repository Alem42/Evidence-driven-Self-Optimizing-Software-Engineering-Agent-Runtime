# 语言包：Go 解耦完成，Python 作为第二个语言跑通

2026-10-13。依据 [LANGUAGE_PACKS](../design/LANGUAGE_PACKS.md) 的规划，按用户确认的范围（**纵切片：先把 Go 解耦出来，再加 Python；不做太多测试**）实施了 P0–P7 和 P8 的一部分。V2 工人循环（[RUNTIME_V2](../design/RUNTIME_V2.md)）按计划排在语言包之后，**尚未开始**。

## 做了什么（按阶段，每步一次提交）
| 阶段 | 内容 | 验证 |
|---|---|---|
| P0 | 语言字面量耦合护栏（`tests/test_language_coupling.py` + 基线）：核心里“指名某种语言的字面量”只许减少 | 基线 355 处 / 53 个文件 |
| P1 | 语言包接口 `languages/base.py`、管理器 `registry.py`（发现、清单校验、就绪探测、每包配置、外部目录）、Go 包外壳 | 一致性测试 |
| P2 | Go 专属代码用 `git mv` 搬进 `languages/go/`（goimports、入口规则、导入检查、测试静态检查、索引器、单文件评审、变异评审） | 全量测试 |
| P3a–g | 核心改为向包询问：操作 id 与图、引擎画像、工作区根文件、规格/文件校验、生成阶段就地检查、规划、契约、泄漏、片段、失败解析与归属、断言解析、可行性规则、项目角色提示词（**整族原样搬进 Go 包，逐字节不变**）、schema、本地传输提示、补丁、修复上下文 | 每步：全量测试 + 黄金提示词 + 默认流程等价检查 `IDENTICAL` |
| P4 | 运行器（Go 程序）中立化：操作由语言包生成的清单 `--ops` 给出，`--go` 仍等价于内置 Go 清单；请求校验、环境、“先构建再运行”都来自清单 | Go 单元测试（含清单外操作被拒绝）+ 运行器集成测试不改即通过 |
| P5 | **Python 语言包**：布局（顶层包 + 薄入口 `__main__.py` + `tests/`）、`ast` 实现的语法/导入修复/空转测试/入口规则/未知内部导入/导入名检查、unittest 与 pytest 失败解析、提示词、可行性规则、`lint.py`/`launcher.py`、评测判官 | 17 + 个 Python 包测试，其中 **脚本化模型 + 真实运行器的端到端**（规划→生成→真实 unittest→Gate，零 token） |
| P5b | 语言选择与每包设置的后端：`languages.json`、`/api/languages`、bootstrap 里的语言列表、任务语言与就绪检查、CLI 的 `--language` 与操作校验 | 设置 API 测试 |
| P6 | 评测按语言：判官分派（Go 判官搬进 Go 包）、`scripts/eval/lang_eval.py`（只用 Flash） | 判官测试：好的通过、错的被拒绝 |
| P7 | 前端：新任务页的语言选择、**设置 → 语言**（默认语言、Python 解释器与测试框架、额外包目录、就绪状态）、Python 高亮、测试/入口/清单文件识别、操作名标签 | tsc + 43 个前端测试 |
| P8（部分） | `masa lang list / info / check`、一致性检查模块、《新增一种语言》指南 | 一致性测试、CLI 测试 |

## 结果
- **耦合**：核心里的语言字面量 355 处 → 78 处（53 → 31 个文件）；剩下的主要是注释/文档字符串，以及 `go_executable` 这个历史属性名（账本画像与旧测试用它）。
- **Go 行为不变**：全量后端测试 584 通过（4 个既有 ERROR 是被导入的 `test_*` 函数被 pytest 收集），黄金提示词逐字节相同，`loop_equivalence.py` 全程 `IDENTICAL`。Go 的操作 id（`go_test` 等）与账本数据不变，旧状态库无需迁移。
- **新增 Python 包的代价**：核心只加了几个钩子（`allow_empty`、`entrypoint_schema`、`relative_path`、`override_seeds`、`runner_env`、`samples`），没有为 Python 改任何 Go 路径。

## Python 的真实验证（只用 Flash，Pro 与本地模型都没用，累计约 ¥0.77）
用 `lang_eval.py` 在独立判官下跑 L0–L4 的小任务，分五轮，**每一轮之后修一个真实问题**：

| 轮 | 现象（账本里的证据） | 修复 |
|---|---|---|
| 1–2 | 整包生成里模型写的 `pyproject.toml` 与规格不一致，被校验拒绝，重试 5 次耗尽 token | 清单与空 `__init__.py` 由规格确定（`seed_files` + `override_seeds`），模型写什么都不算 |
| 3 | 修复阶段报 `index_unavailable: go.mod is required`（Go 的语法索引被用在 Python 上）；测试比较子进程原始字节，Windows 输出 CRLF 导致失败 | 没有 `index` 能力的语言不走语法索引；提示词要求比较解码后的文本 |
| 4 | 6 个任务 4 个通过、**2 个“假通过”**（Gate 通过、判官拒绝）：`__main__.py` 从包的 `__init__` 导入 `run`，而 `run` 定义在 `app/app.py`——单元测试都过，真正运行报 ImportError | 新的确定性检查：`from M import n` 中 M 是项目内已有文件却没有定义 n（双向检查，并指出 n 实际定义在哪） |
| 5 | wordfreq、csv_sum、lcs、knap 全部通过判官 | — |

**如实的结论**：第 3–5 轮合计 12 次运行，9 次通过判官、2 次假通过（已用检查堵上）、1 次 Gate 失败（CRLF，已修）。第 5 轮 4/4 通过只是**修完上一轮问题之后的复测**，样本很小，不能说明通过率；它证明的是“Python 包已经能走完整条流程，并且假通过能被判官发现、能被确定性检查堵住”。（提交 `74d9eed` 的说明里写的“8 of 10”不准确，以本表为准。）

## 模拟用户的测试（独立 agent，通过真实前端与 CLI）
第一轮（Go 解耦完成之后、Python 之前）测了：首页与全部设置页、一次完整的自动任务（¥0.017）、手动模式含故意制造语法错误与自动修复、预检拦截、历史/刷新/取消、CLI 的 run/status/report。**没有发现回归**，发现并已处理：
- 预检漏掉“Python 脚本 / Rust 命令行工具 / Java program”这类说法（正则只认“用 X”），导致规划者问“用 Go 还是 Python”，选 Python 后**悄悄生成了 Go**。已扩大规则，并且现在两个包都会拦截“要求了别的语言”，提示去选择对应语言。
- CLI 接受语言与操作不匹配的组合（只有 HTTP 校验）。已统一校验。
- 刷新页面后自动弹出上一个任务的报告，盖住新任务页。已改为只对本页面会话里启动的任务自动弹出。
- 路由设置里取消“不限”后输入 `0.4` 得到 `10.4`。已改为清空得到 0（不再误当作“不限”）。
- 未处理（记录在案）：任务详情页对经过“批准”的任务显示“暂无角色调用”（调用记录在更早的隐藏版本上）；计划卡片用原始操作 id；CLI `report` 不显示失败原因；浏览器连接中断会在诊断页留下 `ConnectionAbortedError` 记录；`frontend/dist` 是跨域构建，用 `--static-dir` 同源托管需要 `VITE_API_BASE=` 重新构建。
第二轮（Python 与新设置页）的结果见下一节（若该 agent 已返回）。

## 没做到 / 仍然诚实的局限
- **V2 工人循环没有开始**。语言包接口现在稳定了，可以开始。
- **Python 没有网络隔离、没有格式化检查**（与 Go 的运行时同样没有网络隔离；Go 用 gofmt 检查，Python 第一版不做）。静态检查只有语法，没有“未定义名字”分析。
- **Python 只验证了 Flash**；本地模型和 Pro 路径没测。pytest 路径只有单元测试，没有真实运行。
- 没有做：多语言混合项目、Docker 的“只含 Python”镜像变体、Python 评测任务集的系统化基线（只有上面的小样本）。
- 提示词的质量靠评测迭代；Python 的 Tester/Repair 提示词目前只被真实运行覆盖了一部分（规划、开发、诊断；修订测试与重写没触发）。
- `application/orchestration/workflows.py` 里节点标签仍写“gofmt 格式化”（Go 与 Python 共用同一个 `fix-v1` 定义；改标签会改变 Go 的事件文本，等价检查会报警）。

## 数据与代码位置
`src/masa/languages/`（接口、管理器、一致性、两个包）、`runner/internal/runner/ops.go`（操作清单）、`scripts/eval/lang_eval.py`、`.masa/langeval_py/summary.json`（Python 的真实运行记录）、`docs/guides/ADD_A_LANGUAGE.md`。
