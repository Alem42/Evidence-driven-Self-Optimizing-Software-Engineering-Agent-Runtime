# 语言包：把 Go 专属内容抽出来，流程对所有语言通用（详细规划，待审核）

状态：**已实施 P0–P7 与 P8 的一部分（Go 解耦 + Python 包），结果见 [PROGRESS_2026-10-13_001](../progress/PROGRESS_2026-10-13_001_language-packs.md)**；本文保留规划原文。2026-10-12 起草。目标是：同一套流程（账本、Gate、预算、路由、V1/V2 工作流、评测、前端）+ 可插拔的“语言包”（Go、Python、以后的 C# 等）+ 一个管理语言包的管理器。
本文分三部分：现状盘点与工作量（第 1–3 节），目标架构与接口（第 4–9 节），迁移计划与验收（第 10–14 节）。末尾第 15 节是需要你拍板的问题。
与 [RUNTIME_V2.md](RUNTIME_V2.md) 的关系：V2 只通过语言包操作使用编译/测试/格式化，所以**语言包接口先于 V2 的工人循环**；V2 的运行视图（M0）不依赖语言包，可以并行。

## 1. 目标与非目标
**目标**
1. 核心（账本、运行时/Gate、路由与预算、流程引擎、角色注册表、评测框架、HTTP/MCP/CLI、前端骨架）**不出现任何具体语言的名字**。
2. 每种语言是一个自包含的“包”（目录），声明它自己的：项目布局、工具链与操作、确定性检查、失败解析、提示词片段、可行性规则、评测判官、界面提示。
3. 新增一种语言 = 写一个包 + 通过**一致性测试套件**，不改核心。
4. **Go 行为字节级不变**：Go 包是第一个使用者，默认流程等价检查（`scripts/eval/loop_equivalence.py`）与黄金提示词（`tests/golden`）必须一直保持相同。
5. 加入 Python 作为第二个包，用它**检验**抽象是否真的通用。

**非目标（本轮不做）**
- 多语言混合项目（一个任务里同时有 Go 和 Python）。一个任务只有一种语言。
- 外部包的在线安装/市场。先支持“内置包 + 用户配置的本地目录”。
- 重写 Go 编写的沙箱执行器；只让它变成“语言中立的受限命令执行器”。
- Go 专属的可选能力（`go_index` 语法索引、单文件评审、变异评审）搬进 Go 包并标成**可选能力**，Python 包第一版不提供，界面按能力隐藏。

## 2. 现状盘点：Go 耦合在哪里
方法：全量 grep（`gofmt`、`go.mod`、`.go`、`_test.go`、`go_test/go_vet/go_fmt_check`、`go-cli`、`GOROOT` 等）加逐文件阅读。行数是文件总行数，“Go 相关”是我阅读后的**估计**，不是精确统计。
分类：**A** 纯语言知识（整体搬进 Go 包）；**B** 混合（中立逻辑留核心、语言部分抽出）；**C** 接线（只需改成向语言包询问）；**D** 已经中立。

### 2.1 核心之外的 Go 工具链：沙箱执行器（Go 程序）
| 文件 | 行 | 类 | 说明 |
|---|---|---|---|
| `runner/internal/runner/execute.go` | 171 | B | 操作名硬编码（`go_test`→`go test -json -count=1 ... ./...`、`go_vet`、`go_fmt_check`→`gofmt -l .`、`app_run`→`go build` 再运行、`go_index`）；干净环境变量表是 Go 的（GOCACHE、GOFLAGS=-mod=readonly、GOPROXY=off…） |
| `runner/internal/runner/protocol.go` | 102 | B | `Validate` 里写死了允许的 5 个操作名 |
| `runner/cmd/masa-runner/main.go` | 98 | B | `--go` 参数；其余（工作进程隔离、取消、超时）是中立的 |
| `runner/internal/runner/contain_*.go` | 96 | D | Windows Job Object / Linux 进程组，中立 |
| `runner/internal/indexer/*.go` | 285 | A | 用 `go/parser` 提取符号与调用，Go 专属能力 |

### 2.2 Python 核心里的 Go 耦合
| 文件 | 行 | 类 | Go 相关（估） | 说明 / 去向 |
|---|---|---|---|---|
| `domain/models.py` | 93 | C | 2 | `OPERATIONS = {go_test, go_vet, go_fmt_check}`、`Node.operation` 默认值 → 由注册表提供 |
| `domain/proposals.py` | 300 | B | ~170 | `validate_spec`（模块名、`cmd/app/main.go`、`.go`/`go.mod` 规则、`_test.go`）、`preflight_go`、`validate_files`、`_entry_package`、`_imports` → 中立骨架留下，语言规则进包 |
| `agents/protocol.py` | 353 | B | ~200 | 每个角色的提示词里的 Go 指令（标准库、gofmt、`package main`、绝对路径二进制…）→ 模板 + 语言包片段（**必须字节一致**） |
| `agents/schemas.py` | 64 | B | ~30 | `.go`/`_test.go` 路径过滤、`checks` 的键 `go_test/go_vet/go_fmt_check` |
| `infrastructure/runner.py` | 94 | B | ~15 | 构造函数要 `go_executable`；环境变量白名单含 GOROOT 等 |
| `infrastructure/workspaces.py` | 66 | C | 2 | “必须有 go.mod” → `pack.root_marker` |
| `infrastructure/settings.py` | 391 | C | ~10 | 默认 `go_test` 操作与 `allowed_tools` |
| `infrastructure/llm.py` | 365 | B | ~6 | 本地传输覆盖里写死了 Go 的 `go.mod` 与 `go_test` 提示 → 语言包的“本地传输提示” |
| `runtime/engine.py` | 257 | B | ~30 | `_profile` 绑定 go/gofmt 二进制哈希；`compile_project_checks` 的操作→节点名映射 |
| `runtime/graph.py` | 98 | B | ~30 | 默认图的三个节点写死了 `go_*` |
| `runtime/patches.py` | 155 | B | ~5 | “只写已跟踪的 .go 实现文件，拒绝 _test.go” → 包的 `is_source/is_test` |
| `application/generation.py` | 552 | B | ~250 | gofmt/goimports 就地规范化、语法错误检查（`gofmt -e`）、导入检查、入口检查、契约检查、逐文件顺序、go.mod 由规格生成、`_test.go` 判断 |
| `application/planning.py` | 244 | C | ~30 | 写 `go.mod` 种子、`template: 'go-cli'`、`go_test` 必选 |
| `application/orchestration/coordinator.py` | 1022 | C | ~30 | 文案与 `.go`/`_test.go` 判断（变异评审、测试文件还原路径、Tester 提示里的检查名） |
| `application/orchestration/workflows.py`/`flow.py` | 164/130 | C | ~6 | 节点标签“gofmt 格式化”等 |
| `application/checks/goimports.py` | 170 | A | 全部 | 补缺失/删多余的 import、`_mask`（掩码字符串/注释） |
| `application/checks/entrypoint.py` | 34 | A | 全部 | main.go 不许读输入的规则 |
| `application/checks/imports.py` | 49 | B | ~35 | 内部包导入检查：中立思路（“导入了规格里没有的内部模块”），Go 的导入解析是语言部分 |
| `application/checks/testlint.py` | 44 | A | 全部 | 空转测试检查（Go 测试语法） |
| `application/checks/contract.py` | 76 | B | ~25 | 契约校验中立；“导出名”正则是 Go 的（大写开头）与声明检测 |
| `application/checks/ownership.py` | 139 | B | ~100 | 解析 `go test -json`/编译器输出为“归属 + 类别（编译/准备/断言/格式）+ 文件:行” → 中立数据模型 + 语言包的解析器 |
| `application/checks/check_policy.py` | 114 | B | ~70 | 只因 gofmt 失败 → 无模型格式化；断言行解析（`_test.go:12:`） |
| `application/checks/snippets.py` | 36 | B | 3 | 路径正则 `\.go:` → 由包提供“定位正则” |
| `application/checks/referee.py` | 101 | B | ~25 | 断言消息解析（`got X, want Y`）是 Go 风格；打乱/表决是中立 |
| `application/checks/triage.py` | 103 | B | ~60 | 规则引擎中立；规则文本（标准库、`go test` 无法验证性能…）是语言的 |
| `application/checks/leaks.py` | 36 | B | 3 | 泄漏词表中立，文件后缀过滤是 Go 的 |
| `application/checks/consensus.py`、`case_audit.py` | 56/45 | D | 0 | 盲推导与审计，中立 |
| `application/orchestration/context_plan.py` | 96 | B | `outline_go` | 轮廓提取是语言的（Go：声明；Python：def/class 签名） |
| `application/review/single_file.py` | 277 | A | 全部 | P0 的单文件 Go 评审：Go 专属能力 |
| `application/review/mutation.py` | 64 | A | 全部 | 变异评审：Go 专属能力 |
| `application/review/test_review.py` | 38 | C | 1 | `go_test` 过滤 |
| `intelligence/index.py` | 289 | A | 全部 | 依赖 runner 的 `go_index`：Go 专属能力 |
| `intelligence/context.py`、`repair_context.py` | 148/30 | C | ~4 | 默认操作与保留 `go.mod`/`_test.go` |
| `application/console.py` | 920 | C | ~6 | 默认操作 `go_test`、校验 `OPERATIONS` |
| `interfaces/cli.py`、`mcp_server.py` | 127/230 | B | ~70 | `--go` 参数；MCP `verify_project` 只允许 `.go`、`go.mod`、`go.sum` 与三个检查名 |
| `bench/oracle.py` | 74 | B | ~50 | 判官：`go build` 然后逐用例运行二进制 → 由包提供 `judge` |
| `bench/tasks*.py` | 870 | D | 0 | 参考实现是 Python，任务目标语句不含语言名（已核对，只有文件头注释写了“Go 命令行任务”） |
| `application/orchestration/router.py` 等 | — | D | 0 | 路由、预算、冻结、角色运行时、账本：中立 |

### 2.3 周边
| 位置 | Go 相关 | 说明 |
|---|---|---|
| 前端 | ~10 处 | `status.ts` “Go 验证”；`text.ts` 的 `.go` 诊断正则与 Go JSON 测试输出转换；`CodeEditor` Go 高亮；`CodeView`/`useCode` 的 `_test.go`/`go.mod`；`ReportDialog` 操作名映射；`NewTask` 文案与 `runner_ready` |
| 测试 | 67 个文件中约 42 个含 Go 字样 | 绝大多数是假模型/假执行器使用 `go_test` 与 `FILES` 夹具。**只要 Go 包的操作 id 不变，这些测试几乎不用改**（这是保持 id 不变的主要理由） |
| 脚本/部署 | `build.ps1`、`go-toolchain.json`、`verify-env.ps1`、Dockerfile（Go 工具链阶段）、`docker_smoke.py` | 需要“按语言包安装工具链”的说法；镜像可以有“只含 Python 的变体” |
| 文档 | 多篇指南 | 语言无关的部分要和 Go 专属部分分开 |

## 3. 工作量分析（估计，不是承诺）
- **纯 Go 知识（A）**：Python 约 1,900 行 + Go 约 290 行（索引器）→ 搬家，逻辑基本不改。
- **混合（B）**：约 12 个文件、其中约 700 行需要拆开（中立部分留下、语言部分抽出）。这是最费脑的部分：`generation.py`、`protocol.py`、`proposals.py`、`ownership.py`、`check_policy.py`。
- **接线（C）**：约 15 个文件、每个改 2–30 行，改成“向包询问”。
- **其余约 9,000 行不动**。
- **新增**：语言包接口与管理器约 600–800 行；Python 包约 1,500–2,000 行 + 约 600 行测试；运行器中立化 Go 约 300 行；前端约 300 行。
- **总量**：新增与改动合计约 5–6 千行，占现有 Python 代码（约 1.35 万行）的一半左右的“触碰面”，但真正“改逻辑”的部分只有 B 类的约 700 行。
- **时间（按“一次连续工作≈一个上下文窗口”为单位的估计）**：约 12–14 次，细分见第 11 节；API 花费主要在 Python 包的真实验证，约 ¥3–6（只用 Flash 与本地模型）。
- **最大的风险不在工作量，而在“Go 行为不能变”**：提示词字节一致、事件序列一致、账本兼容。所以第 10 节把“防回归网”放在第一步。

## 4. 目标架构
```
src/masa/
  domain/            中立：模型、提案校验骨架、令牌估算…（不认识任何语言）
  languages/         ★ 新增：语言包体系
    base.py          接口（Protocol）与数据类型：LanguagePack、Layout、OperationDef、Diagnostic、Capability…
    registry.py      ★ 管理器：发现/加载/校验/就绪探测/默认语言
    conformance.py   一致性检查的公共函数（供测试套件与 `masa lang check` 使用）
    go/              Go 包（把现有 Go 专属代码搬进来）
      pack.json      清单：id、版本、api_version、操作表、布局、能力、界面提示、工具链需求
      pack.py        导出 PACK（实现 LanguagePack）
      layout.py  ops.py  checks/ (goimports, entrypoint, imports, testlint, contract, leaks…)
      failures.py (ownership 解析、断言解析、snippets 定位) 
      prompts/  (提示词片段，按角色分文件)
      triage_rules.py  outline.py  oracle.py
      index/  review/  (可选能力：索引、单文件评审、变异评审)
      fixtures/ (一致性测试用的小项目与“坏样本”)
    python/          Python 包（新）
  application/ ...   中立：只通过 registry 取包，不 import languages/go
runner/              中立的受限命令执行器（Go 程序）；操作由清单给出
```
**依赖方向（用测试强制）**：`languages/<x>` 只能依赖 `domain` 与标准库（以及 `languages.base`）；核心只能依赖 `languages.base/registry`，**不能 import 任何具体语言包**。耦合 lint 测试（第 12 节）扫描核心目录里的语言字面量，白名单只减不增。

## 5. 语言包接口（LanguagePack）
下面是接口的分组与每个成员的“Go 现在是怎么做的”。声明性的内容放 `pack.json`，逻辑放 `pack.py`。**必需**＝所有语言都要实现；**可选**＝用 `capabilities` 声明，没声明的功能核心自动关闭并在界面隐藏。

### 5.1 身份与布局（声明性，必需）
| 成员 | 含义 | Go | Python（草案） |
|---|---|---|---|
| `id` / `title` / `version` / `api_version` | 标识与版本；`api_version` 用于和核心协商 | `go` / Go / 1 / 1 | `python` |
| `source_exts` / `manifest_files` | 允许的源文件后缀、允许的清单文件 | `.go`；`go.mod`（`go.sum` 仅 MCP 输入） | `.py`；`pyproject.toml` |
| `is_test(path)` / `is_source(path)` | 测试文件与实现文件的判定（冻结、归属、补丁都靠它） | `_test.go` 结尾 | `tests/` 下或文件名 `test_*.py` |
| `root_marker` | 工作区根必须有的文件（现 `go.mod`） | `go.mod` | `pyproject.toml` |
| `entrypoint(spec)` | 入口文件路径 | `cmd/app/main.go` | `<module>/__main__.py` |
| `layout_rules` | 目录规则（Go：根目录不许有 `.go`、代码放 `internal/<name>/`；Python：每个包目录要有 `__init__.py`、测试放 `tests/`） | 现有 `validate_spec` 中的规则 | 对应规则 |
| `min_files / max_files` | 规格文件数范围 | 3..20 | 3..20 |

### 5.2 项目种子与规格校验（必需）
- `seed_files(spec) -> {path: content}`：由规格确定、**不由模型生成**的文件（Go：`go.mod`；Python：`pyproject.toml`）。
- `validate_spec(spec)`：在中立校验（字段、路径可移植性、冲突、验收条数、大小）之后调用的语言规则（模块名格式、入口、扩展名、层级）。
- `validate_file_proposal(files, spec, target)`、`validate_bundle(files, spec)`：单文件/整包校验（Go：`package main` 检查、`_imports` 预检、契约中的包）。

### 5.3 工具链与操作（必需；这是和运行器的接口）
- `toolchain.probe(settings) -> Readiness`：能不能用（Go：找 `go`/`gofmt`；Python：找解释器），给界面显示“就绪/缺什么”，替代现在的 `runner_ready` 布尔值。
- `toolchain.fingerprint()`：把**具体的二进制路径与哈希**写进账本的 `profile`（现有 `Runtime._profile` 对 go/gofmt 做的事），证据因此绑定到工具链。
- `operations`: `{kind: OperationDef}`，`kind ∈ {test, static, format, build, run, index}`。`OperationDef` 包含：账本里的 **operation id**（Go：`go_test`/`go_vet`/`go_fmt_check`/`app_run`/`go_index`，**保持不变**）、展示名、命令模板（可执行文件角色 + 参数 + 超时倍数）、成功判定（例如 `gofmt -l` 的“stdout 非空即失败”）、环境变量白名单与固定变量。
- `required_kinds = {test}`；`static`、`format` 可缺省（Python 第一版没有格式化检查，Gate 图就只有 test + static）。

### 5.4 确定性检查（必需，零 token）
这些都是现在散在 `generation.py` 等处的“就地检查”，统一成一个入口：
- `normalize(files, paths) -> (files, changes)`：无模型的规范化（Go：goimports 式补删导入 + gofmt；Python：语法检查之外无格式化，可做“删未用导入/补标准库导入”）。
- `quick_checks(files, paths, spec, contract) -> {path: message}`：语法（Go：`gofmt -e`；Python：`ast.parse`，**进程内，不需要工具链**）、提示词泄漏、未知内部导入、入口规则、空转测试、契约导出是否声明。消息文本是给模型的重写指令，语言包自己写。
- `contract_exports(source, dir) -> [name]`：契约检查要用的“包里声明了哪些导出”（Go：大写开头的 func/type/var/const；Python：不以下划线开头的顶层 def/class/变量）。
- 中立逻辑留核心：泄漏词表（`FORBIDDEN`）、契约校验、内部导入检查的“思路”（包给出 `imports_of(source)` 与 `internal_module_dirs(spec)` 即可）。

### 5.5 失败解析（必需）
- `parse_failures(operation_results) -> [Diagnostic]`：把某次验证的原始输出解析成中立的 `Diagnostic(owner, kind, path, line, message)`；`owner ∈ {implementation, test}`，`kind ∈ {compile, setup, assertion, format}`。Go：解析 `go test -json`、编译器、vet、gofmt；Python：解析 `unittest` 输出（`File "...", line N` 回溯 + `AssertionError: a != b`）。核心的“归属分析/失败分类/无模型格式化策略/Diagnoser 触发”**只消费 `Diagnostic`**。
- `assertion_of(message) -> {call, got, want}`：断言裁判用（Go：`got X, want Y`；Python：约定测试写成 `assertEqual(actual, expected)`，`first != second` 即 got != want）。
- `location_regex`：给“出错行附近源码片段”和前端跳转用。
- `test_function_source(files, path, line)`：裁判需要的失败测试函数源码。

### 5.6 提示词片段（必需，字节一致是硬约束）
核心有模板；包提供片段并按角色命名：`planner.rules`、`tester.checks`、`developer.rules`、`developer.file_rules`、`repair.rules`、`test_revision.rules`、`local_transport.*`……Go 包的片段就是把现有 `protocol.py` 里的 Go 句子**原样**搬过去，模板在片段之间只放原来就有的连接符。`tests/golden/roles_golden.json`（每个提示词分支逐字节）是唯一的裁判：搬家前后必须逐字节相同。Python 包写自己的片段，**之后**才允许改动措辞。

### 5.7 可行性规则、轮廓、评测判官、界面提示（必需，除能力外）
- `triage_rules`：`[(规则id, 级别, 正则/条件, 说明, 改写建议)]`，引擎在核心。Go：非标准库、网络、GUI、库而非命令行…；Python：第三方包、网络、GUI…
- `outline(source) -> str`：签名摘要（Go：现有 `outline_go`；Python：AST 生成 `def name(args) -> ret` 与 `class` + 方法签名）。V2 的工人、分层上下文都靠它。
- `oracle.judge(files, task) -> {passed, built, cases}`：独立判官（Go：`go build` 后逐用例跑二进制；Python：`python -E -s -B -m <module>` 逐用例）。评测任务的目标语句保持语言中立。
- `ui`：`highlight`（编辑器语言）、`diagnostic_regex`、`test_label` 等，通过 bootstrap 接口给前端，前端不再写死。

### 5.8 可选能力（`capabilities`）
`index`（语法索引与检索）、`single_file_review`、`mutation_review`、`formatter`（有无格式化检查）。Go 全有；Python 第一版只有 `formatter=false`。核心遇到未声明的能力：功能关闭、界面隐藏、MCP/CLI 返回明确的“该语言不支持”。

## 6. 账本与数据兼容（不能破坏已有状态库）
1. **Go 的 operation id 不变**（`go_test` 等），所以已有账本、事件、报表、测试夹具全部继续有效。Python 的 id：`py_test`、`py_static`（`py_run` 对应 `app_run`）。核心把 operation 当不透明字符串，由注册表解析“id → (语言, kind)”。
2. 运行数据增加 `language`（字符串）；**缺省视为 `go`**，旧数据无需迁移。`project_plan.template` 保持 `go-cli`（Go）/ 新值 `py-cli`（Python）。
3. `profile` 增加 `language: {id, version, digest}`（包清单与包代码的哈希），和现有的二进制哈希同样用于“证据绑定”。旧运行没有这个字段则按 `go` 的旧行为校验。
4. 流程定义（`fix-v1` 等）里的节点标签从包取（“gofmt 格式化（无模型）”→“格式化（无模型）：<包给的名字>”）；**Go 的标签文字保持不变**，因为等价检查会比较事件。
5. `Node.operation` 的默认值与 `OPERATIONS` 常量改为由注册表按任务语言提供；`models.py` 里保留 `OPERATIONS` 作为“所有已注册操作 id 的并集”的懒属性，避免一次改太多调用点。

## 7. 语言包管理器（`languages/registry.py`）
- **发现**：(1) 内置：扫描 `masa/languages/*/pack.json`；(2) 用户目录：设置项 `packs_dir`（或环境变量 `MASA_PACKS_DIR`）下的包目录；(3) 之后可加 Python entry points（组名 `masa.languages`）。
- **加载与校验**：清单 schema 校验（沿用 `roles/jsonschema.py`）；`api_version` 与核心兼容；必需成员齐全；操作表合法（id 唯一、不与其他包冲突、命令模板里没有 shell 元字符）；失败的包只是“不可用”，不影响其他包，原因显示在界面。
- **就绪探测**：每个包的 `toolchain.probe`，结果缓存并可手动刷新；界面“设置 → 语言”显示每个包：版本、能力、工具链就绪情况、清单哈希。
- **默认语言**：设置项（默认 `go`）；任务创建时可选，写入 `run.data.language`，之后所有层都从这里读。
- **CLI**：`masa lang list | info <id> | check <id>`（`check` 运行一致性套件，见第 12 节）。HTTP：`GET /api/languages`；`bootstrap` 返回当前语言的 `ui` 提示。
- **安全**：外部包是**在我们进程里执行的 Python 代码**，等同于插件，只从用户明确配置的目录加载，界面显示哈希，不自动下载；包只能声明命令模板，**真正执行都经过运行器的清单白名单**，包不能让运行器执行未声明的命令。

## 8. 运行器中立化（Go 程序，改动小）
现状：操作名与参数在 Go 代码里写死。目标：**命令表由 Python 侧在启动时通过清单文件提供**，运行器只执行表里的操作。
```
masa-runner --workspace W --ops ops.json        # 新；--go X 保留为 Go 包的兼容简写
ops.json = { "version": 1,
  "env":  {"allow": [...], "set": {"GOFLAGS":"-mod=readonly", ...}, "path_prepend": ["<toolchain bin>"]},
  "operations": {
     "go_test":      {"exe":"<abs go>", "args":["test","-json","-count=1","-timeout={timeout_ms}ms","./..."]},
     "go_fmt_check": {"exe":"<abs gofmt>", "args":["-l","."], "fail_if_stdout_nonempty": true},
     "app_run":      {"build": {"exe":"<abs go>","args":["build","-o","{out}","./cmd/app"]}, "exe":"{out}", "argv_from_request": true},
     "go_index":     {"builtin":"go_index"} } }
```
- 请求里仍只有**操作名**（外加 `app_run` 的 argv）；不在表里的操作名直接拒绝；`ops.json` 由受信任的 Python 侧生成，模型碰不到。
- `Request.Validate` 不再硬编码名字，而是查表；安全限制（超时、输出上限、参数个数/长度、无符号链接工作区）**不变**。
- 干净环境变量表来自清单（Go 的内容与现在逐项相同）。工作进程隔离、取消、Job Object/进程组**一行不改**。
- Python 的操作：`python -E -s -B -m unittest discover -s tests -t .`；入口运行：`python -E -s -B -m <module>`（`-E` 忽略 PYTHON* 环境变量，`-s` 禁用用户 site，`-B` 不写 `.pyc` 以免污染只读快照；不用 `-I`，因为 `-I` 会去掉 `-m` 需要的当前目录）。
- 兼容测试：Go 包生成的清单与旧硬编码行为逐项对比（同一请求，结果事件相同）。
- Python-only 的用户**不需要安装 Go 工具链**：运行器是预编译二进制；Docker 可做不含 Go 工具链的镜像变体。

## 9. 提示词拆分的做法（风险最高的一步，单独说明）
1. 先**机械地**把 `protocol.py` 每个角色的指令字符串，按“是否出现 Go 专属词”切成“中立前缀/中立句子”和“Go 句子”。切分点只落在现有字符串字面量的边界上（它们本来就是一串拼接的句子）。
2. 模板函数 `compose(role, pack)` 把句子按原顺序拼回去；Go 包的片段文件就是被切出来的 Go 句子，原文不动。
3. 黄金测试逐字节比较（已有，`tests/test_roles_registry.py` 与 `tests/golden/`）。**任何一个字符的差异都视为失败。**
4. 只有 Go 包通过黄金测试后，才写 Python 包的片段。Python 的提示词质量靠评测迭代，不靠黄金测试。

## 10. Python 包的设计草案
| 项目 | 决定（草案） |
|---|---|
| 项目布局 | 顶层包名 = `spec.module`（小写标识符，如 `bankledger`）；`bankledger/__main__.py` 是**薄入口**（只调用 `app.run(sys.argv[1:], sys.stdin, sys.stdout, sys.stderr)` 并退出）；子包 `bankledger/parser/__init__.py` 等；测试 `tests/test_<pkg>.py`；种子文件 `pyproject.toml`（由规格生成，模型不写） |
| 测试框架 | 标准库 `unittest`（不依赖 pytest，满足“只用标准库、无第三方”的现有约束）；约定断言写成 `assertEqual(actual, expected)` |
| 静态检查 | `py_static`：进程内 `ast` 做语法 + 未定义名/未用导入的简单分析（可由运行器以 `python -E -s -B <pack>/lint.py .` 执行，也可只在 `quick_checks` 里做）；第一版**没有格式化检查** |
| 就地检查 | `ast.parse`（语法，不需要工具链）；未知内部导入（`import bankledger.xxx` 不在规格里）；入口规则（`__main__.py` 不许 `input()`、`sys.stdin.read`、`open`…）；空转测试（无断言、`assertTrue(True)`）；泄漏词表复用；契约导出（顶层 def/class/变量） |
| 导入规范化 | 用 `sys.stdlib_module_names` 判断标准库：删未使用的导入；`module.attr` 用到但没导入的标准库模块自动补 |
| 失败解析 | 解析 `unittest` 文本：`ERROR:`（导入/语法错误 → 类别 compile/setup）、`FAIL:`（断言）、回溯里最后一个在 `tests/` 下的帧作为 `path:line` |
| 轮廓 | `ast` 提取 `def`/`class` 签名与首行 docstring |
| 评测判官 | 逐用例 `python -E -s -B -m <module> args`，stdin 注入，比较 stdout/退出码（与 Go 判官同一套归一化） |
| 沙箱 | 与 Go 相同的运行器（超时/输出上限/进程组）。**网络**：Go 路径靠 `GOPROXY=off` 禁止下载依赖，运行时并没有网络隔离；Python 同样没有，文档里如实写明，不假装隔离 |
| 工具链 | 配置的解释器路径（默认当前 Python；Docker 里 3.12），指纹写入 `profile` |
| 能力 | 无 `index`/`single_file_review`/`mutation_review`；无 `formatter` |
| 可行性规则 | 第三方包、网络、GUI、库而非命令行（与 Go 平行） |

## 11. 迁移计划（阶段与步骤）
原则：**每一步都能单独回滚、每一步结束全量测试 + 提示词黄金 + 等价检查全绿、每一步一次提交**。不一次大改。
### P0 防回归网（约 0.5 次）
1. **耦合 lint 测试** `tests/test_language_coupling.py`：扫描核心目录（除 `languages/`、`runner/`）里的语言字面量（`go_test`、`go_vet`、`go_fmt_check`、`gofmt`、`go.mod`、`.go`、`_test.go`、`go-cli`、`cmd/app/main.go`…），**白名单 = 现在的全部命中，只允许减少**。它既是进度条，也是防止以后又被写回去的护栏。
2. 扩充等价检查场景：覆盖“生成阶段就地重写”“仅格式失败”“导入修复”（脚本化假模型，零 token），保存基线 `.masa/eq_pre_packs.json`。
3. 记录当前测试数与耗时作为基线。
验收：lint 通过（白名单=现状）；等价检查 IDENTICAL。
### P1 接口与管理器骨架 + Go 包“外壳”（约 1 次）
1. `languages/base.py`（接口、数据类型）、`registry.py`、`pack.json` schema。
2. `languages/go/pack.py`：**外壳**——每个方法先直接调用现有模块（还没搬家），`pack.json` 写入现有操作表、布局、能力。
3. 一致性套件 v0：对 Go 包跑（清单有效、布局谓词一致、种子文件、坏样本被 `quick_checks` 抓住、输出解析样本）。
验收：核心**一行未改**，全量测试通过；一致性套件对 Go 通过。
### P2 搬家（约 0.5–1 次，纯移动，用 `git mv` 保留历史）
`checks/{goimports,entrypoint,imports,testlint}.py`、`outline_go`、`review/{single_file,mutation}.py`、`intelligence/index.py`、Go 失败解析部分 → `languages/go/`；旧路径留**一行转发垫片**，下一阶段结束后再删。
验收：全量测试；耦合 lint 白名单不增。
### P3 核心改为向包询问（约 3 次，最大的一块，按风险从低到高分步，每步一提交）
- **P3a 操作与图**：`OPERATIONS`、`Node.operation` 默认、`default_policy`/`collaboration_policy`、`compile_project_checks` 的映射、`console`/`cli`/`mcp` 的校验 → 注册表。
- **P3b 工作区与规格校验**：`workspaces.root_marker`、`validate_spec`/`validate_files`/`validate_file_proposal` 拆成“中立骨架 + `pack.validate_*`”、`planning` 的种子文件与 `template`。
- **P3c 生成阶段就地检查**：`generation.py` 里的 gofmt/导入/语法/入口/契约/泄漏全部改走 `pack.normalize` 与 `pack.quick_checks`（保持检查**顺序**与消息**文字**不变）。
- **P3d 失败分析**：`ownership`/`check_policy`/`snippets`/`referee`/`coordinator` 里的 `.go`/`_test.go` 判断 → 中立 `Diagnostic` + `pack.parse_failures/assertion_of/location_regex`。
- **P3e 提示词**：`protocol.py`/`schemas.py`/`llm.py` → 模板 + 片段（第 9 节）。**黄金逐字节**。
- **P3f 流程与评审**：`workflows.py`/`flow.py` 标签、`context_plan.outline`、`patches`（`is_source/is_test`）、可选能力开关（`index`、`single_file_review`、`mutation_review`）。
- **P3g 引擎画像**：`Runtime._profile` 用 `toolchain.fingerprint`，加 `language` 字段（旧运行缺省 go）。
验收（每一步）：全量测试；黄金；**等价检查 IDENTICAL**；耦合 lint 白名单收缩；P3e 之后核心里应只剩“默认语言 = go”这一类配置性引用。
### P4 运行器中立化（约 1.5 次）
Go 侧：`--ops` 清单、`Validate` 查表、`execute.go` 从表取命令、保留 `--go` 兼容；Go 单元测试 + 与旧行为对比的集成测试；Python 侧 `infrastructure/runner.py` 从包生成清单。Windows 与 Linux（Docker）各跑一遍既有集成测试。
验收：`tests/test_runner_integration.py` 不改即通过；新增“清单外操作被拒绝”的测试。
### P5 Python 包（约 3 次）
布局/种子/规格校验 → 运行器清单（unittest、入口运行）→ `quick_checks`/`normalize` → `parse_failures`/`assertion_of` → 提示词片段（先抄 Go 的结构再改）→ `outline` → 可行性规则 → 一致性套件 → **脚本化假模型端到端测试**（零 token）→ 一次真实冒烟（Flash，约 ¥0.3）。
验收：一致性套件通过；假模型端到端通过；真实冒烟 1 个小任务过独立判官。
### P6 评测（约 1 次）
`bench` 增加 `language` 维度；判官走 `pack.oracle`；任务目标语句已核对为语言中立，只需改文件头说明；Python 判官单元测试；`p2_eval`/`pair_table` 参数化。
验收：同一批 L2–L6 任务在 Python 上能跑；Go 结果不变。
### P7 前端（约 1 次）
bootstrap 带 `language` 与 `ui`；`status.ts`/`text.ts`/`CodeEditor`（按 `highlight` 切语言，需要 `@codemirror/lang-python`）/`CodeView`/`useCode`/`ReportDialog`/`NewTask` 去硬编码；新任务页增加语言选择；设置页“语言”。
验收：前端测试通过；Go 页面截图/文字不变。
### P8 管理器体验与文档（约 1 次）
`masa lang list/info/check`、`/api/languages`、设置页；写《如何新增一种语言》（含 C# 的清单草案与需要实现的成员表）；Docker 镜像变体；更新 STRUCTURE/INDEX。
验收：按文档新增一个最小“空语言包”能被加载并通过一致性套件的必需项。

**合计约 12–14 次连续工作。** 如果只想先证明抽象成立，可以走**纵切片**：P0 → P1 → P2 → P3a/b/c/d/e/g → P4 → P5（只做 Python 的必需项）→ 一次 Python 真实冒烟；P3f、P6、P7、P8 之后再补。纵切片约 8–9 次。

## 12. 测试与验收策略
1. **耦合 lint**（P0）：核心里不允许新增语言字面量；白名单逐阶段收缩，最终应为空或只含“默认语言”配置。
2. **黄金提示词**：Go 逐字节不变（已有）。
3. **默认流程等价**（`loop_equivalence.py`，零 token）：每个 P3 子步骤都跑。
4. **一致性套件**（新）：对注册表里的**每个包参数化**——清单与接口完整性；布局谓词互相一致；种子文件通过自己的规格校验；包自带的“好项目”夹具通过 `quick_checks`（无报错）且（工具链就绪时）真实跑通 `test`；“坏样本集”（语法错、未知内部导入、入口违规、空转测试、契约缺失）全部被 `quick_checks` 抓住且消息非空；已录制的验证输出样本解析出预期的 `Diagnostic`；提示词片段长度在上限内；`outline` 对夹具不报错。新增语言的“完成”定义 = 一致性套件通过 + 1 次真实冒烟。
5. **运行器**：Go 单元测试 + Python 集成测试，Windows 与 Linux 都要；清单外操作被拒绝。
6. **跨语言冒烟**：同一个小任务（如 `sum`）分别用 Go 与 Python 包走完整流程，判官通过。
7. **每一步的回滚**：每个 P3 子步骤独立提交；回滚点 = 上一步提交；垫片与旧路径在整个迁移完成并稳定后才删除。

## 13. 风险与应对
| 风险 | 影响 | 应对 |
|---|---|---|
| 提示词拆分后字节不一致 | Go 行为悄悄变化，评测数据不可比 | 黄金测试逐字节 + 等价检查；切分只落在现有字符串字面量边界 |
| 抽象泄漏：接口按 Go 的形状设计，Python 放不进去 | 后面还要改接口 | 接口里每个成员同时写出 Go 与 Python 草案（第 5、10 节）；P5 前先让 Python 的一致性夹具在 P1 就存在 |
| 一次动太多 | 难以回滚、难以定位回归 | 每步一提交；耦合 lint 与等价检查作为门禁 |
| 与另一会话未提交的云端部署改动冲突 | 工作区里 `Dockerfile`、`docker-compose.yml`、`settings.py`、`llm.py`、`server.py` 已有他人未提交改动 | 迁移期间**只用显式路径 `git add`**；涉及这些文件的步骤（P3e 的 `llm.py`、P3a 的 `settings.py`、P8 的 Docker）先确认对方已提交，或在最小范围内改并明确说明 |
| Python 沙箱弱于预期 | 生成的测试可能做网络/文件系统操作 | 与 Go 同级（运行器进程隔离、超时、输出上限、临时目录）；文档如实写明没有网络隔离 |
| 外部包是任意代码 | 安全 | 仅加载用户配置目录、显示哈希、命令只能来自清单白名单 |
| 前端高亮/依赖增加 | 构建体积 | 按需加载语言包的高亮模块 |
| 评测可比性 | Go 与 Python 难度不同 | 任务集按语言分别记基线；只做同语言内的 A/B |

## 14. 完成标准（整个重构）
1. 耦合 lint 白名单只剩默认语言配置；核心目录里没有 `import masa.languages.go`。
2. Go：全量测试通过、黄金逐字节、等价检查 IDENTICAL、评测任务在 Go 上通过率与重构前同口径一致（用已有 `.masa` 基线对比，只看趋势）。
3. Python：一致性套件通过；L2–L6 中至少 3 个任务经独立判官通过（真实运行）。
4. 新增“空语言包”的文档示例能被管理器加载。
5. V2 的工人循环可以只用 `pack` 接口完成 Go 与 Python 的一次小任务（这是第二阶段目标，不属于本重构的验收，但接口要为它留好：`run_check(kind, scope)`、`outline`、`quick_checks`）。

## 15. 需要你拍板的问题（我的建议在括号里）
1. **范围**：走完整路线（P0–P8，约 12–14 次）还是先走纵切片（约 8–9 次）？（建议：纵切片，Python 跑通后再补 P6–P8。）
2. **Python 测试框架**：`unittest`（标准库）还是允许 `pytest`？（建议：先 `unittest`，保持“标准库、无第三方”的约束；以后作为包的选项加。）
3. **Python 版本与解释器**：用运行 masa 的同一个解释器，还是单独配置？（建议：设置里可配置，默认用当前解释器，写入证据指纹。）
4. **Go 的操作 id 保持不变**（`go_test` 等），Python 用 `py_*`。（建议：同意；这是旧账本和 42 个测试文件不用改的前提。）
5. **可选能力**：索引/单文件评审/变异评审留在 Go 包，Python 第一版不做。（建议：同意。）
6. **包的来源**：第一版只内置 + 用户本地目录，不做 entry points/在线安装。（建议：同意。）
7. **与 V2 的顺序**：语言包 P1–P3 完成后再写 V2 工人循环，V2 的运行视图（M0）现在就可以并行做。（建议：同意；如果你更想先看到可见性改进，我先做 M0，它不改核心。）
8. **与另一会话的云端部署改动**：是否已经提交/合并？迁移会碰 `settings.py`、`llm.py`、`Dockerfile`。（需要你确认，或指定我先避开这些文件。）
