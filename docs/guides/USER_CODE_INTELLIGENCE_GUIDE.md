# Go 代码证据、记忆与上下文

P1-02/P1-03 的最小实现已经接通：Go 提取 AST，Python 负责检索、版本核对、记忆和上下文预算。真实模型、四角色协作尚未接入。

## 如何运行

```powershell
.venv\Scripts\python.exe scripts/demo_patch.py
.venv\Scripts\python.exe -m masa run --repo examples/go-todo --goal "Inspect NormalizeTitle and Create, verify repair" --patch .cache/todo-patch.json --intelligence
```

这会消耗 3 次工具预算：补丁、索引、Go 检查。2 次 scripted 模型请求都经过 Context Builder；第二次包含真实工具结果。默认不带 `--intelligence` 的旧流程继续兼容。

查看指定运行的地图、候选代码和角色上下文：

```powershell
.venv\Scripts\python.exe -m masa inspect <run_id> --query NormalizeTitle --role developer --budget-bytes 8000
```

`inspect` 优先使用已持久化的同版本索引；没有缓存时会执行索引，消耗工具预算并受原 run 的时限/取消状态约束。它也会记录上下文和源码记忆，因此不是纯只读诊断命令。

前端：重启本地服务，在「新建任务」勾选「Go AST / 代码证据上下文」。在执行时间线查看 `index_published`、`memory_recorded`、`context_built`；打开 artifact 查看索引、实际输入与筛选清单。界面尚无专用代码浏览器，复用现有证据面板。本轮前端构建通过，浏览器视觉/点击验收仍待完成。

## 能看到什么

- **地图与索引**：文件、包、imports、声明、接收者、签名、位置、注释文本、测试候选、匿名函数和调用表达式。解析失败显示诊断及 partial；输出超限时拒绝发布 generation。
- **检索解释**：精确符号/路径、标识符词法、注释匹配、一跳调用与同包测试候选，附匹配原因、覆盖范围和不确定性。中文只能匹配现有中文注释，不自动翻译成英文语义。
- **版本检查**：snapshot、profile、索引器二进制身份参与 generation；缓存命中仍核对整个副本。旧索引不能用于新快照。
- **记忆**：实际选入上下文的源码观察持久化且去重。任意解释默认 candidate/inferred，不能自动变成 observed。不同 run 隔离，冲突保留，过期记录保留历史但不作为当前事实。
- **上下文**：保留任务、权限、输出契约、真实工具结果和显式必需的未解决项。Planner 偏签名，Tester 优先测试，其余角色使用源码范围。每次记录 included/omitted、原因、输入字节和预算。

## 当前取舍

索引是纯语法分析：build inclusion 为 unknown，不加载依赖，不推断真实调用绑定或测试覆盖。候选调用可能实际是类型转换。没有类型分析、向量库、FTS5、增量索引或模型摘要；词法扫描对小仓库更简单，返回值明确标注后端，后续可按实测替换。

记忆当前采用整个 snapshot/profile 保守失效，在下次查询时执行状态更新，并沿依赖传播。尚不做未改文件的跨版本复用或跨 run 记忆晋升。显式 required 的假设仍标 inferred；若过期或冲突，则阻止构建，不能静默删除。

默认上下文上限为 32,000 UTF-8 字节，预留 1,024 字节；这是确定性本地预算，不是模型实测 token。必需内容超限则报错，不截断契约或拆散工具结果。真实 provider 的 tokenizer 和 usage 将在 P1-06 接入。源码片段上限 16 KiB，候选最多 40 个；大仓库可能需增大 run 输出预算或后续分页。

Go runner 本次增加了能力，二进制哈希随之变化。旧的未完成 run 可能按既有规则拒绝在新工具链上恢复（tool_profile_mismatch）；请保留旧工具链或新建运行，不绕过证据校验。
