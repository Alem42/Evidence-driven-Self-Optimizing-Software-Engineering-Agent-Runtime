# 进展013：cmd 弹窗根因 · 前端默认值与“实际模型”显示 · 证据片段 / 尝试摘要 / import 自动修复

2026-10-04。分支 `feature/multi-model`。前置：[进展012](PROGRESS_2026-10-04_004_syntax-gate-strong-spec-no-console-windows.md)。方案与出处：[PLAN_LOW_MODEL_QUALITY](../reference/LOW_MODEL_QUALITY.md)；M2 细化：[PLAN_MULTI_MODEL_RUNTIME §10](../reference/MULTI_MODEL_RUNTIME_PLAN.md)。

## 1. cmd 窗口：实机排查与真正根因

**上一次（进展012）我给我们的子进程加了 `CREATE_NO_WINDOW`，但你反馈仍然弹，而且“连跳三次、瞬间关闭、和本地模型加载/卸载同时出现”。这次按你的要求实机监控。**

方法：`scripts/watch_procs.py`（新增，无需管理员）每 5ms 快照一次进程表，记录新进程、父进程；同时对 Ollama 做一次加载、一次卸载。

观察到的事实（修复前）：
- 加载模型的那一刻：`ollama.exe(serve)` 拉起 `llama-server.exe`、`llama-server.exe`、`ollama.exe` **三个子进程**，每个都带一个 `conhost.exe`，随后出现 `OpenConsole.exe` + `WindowsTerminal.exe`（默认终端是 Windows Terminal，所以会看到一个终端窗口）。卸载时再来一次。**和你看到的“三次”完全吻合。**
- 这三个进程的父进程是 Ollama 服务，**不是我们的代码**。

根因：这个 `ollama serve` 进程（01:43 启动）**没有控制台**——它是我之前做“真实 Ollama 被杀演练”时，`scripts/drill_ollama.py` 在杀掉服务后自己重启的，用了 `DETACHED_PROCESS`。没有控制台的服务，每次拉起控制台子进程，子进程都会各自新建一个控制台窗口。由托盘程序启动的 Ollama 有隐藏的控制台，所以平时不弹。**这是我演练留下的现场，不是产品代码的问题。**

修复：
1. 用隐藏窗口重启了 Ollama 服务（`Start-Process -WindowStyle Hidden`）。
2. 实机复测同样的“加载 + 卸载”：新进程里**没有** `OpenConsole` / `WindowsTerminal`（修复前每个子进程都有），剩下的 `conhost` 都是 VS Code 轮询 git/docker 产生的，与我们无关。
3. `drill_ollama.py` 改为 `CREATE_NO_WINDOW`，并写明不要用 `DETACHED_PROCESS`。
4. 进展012 里给 Python/Go 子进程加的无窗口标志**保留**（它们本来就应该有），但不是这次弹窗的原因。

**如果以后又出现**：先用 `python scripts/watch_procs.py 60` 复现一次，看新进程的父进程是谁；若又是 `ollama.exe`，说明 Ollama 服务又被某个无控制台的方式启动了——请从托盘重启 Ollama，或用 `Start-Process -WindowStyle Hidden "…\ollama.exe" serve`。

## 2. 前端：把最近的更新体现出来

问题：你发现“Planner / Tester 还是由本地模型生成”——**报告是对的，是显示有误导**：任务页各处的“模型”选择框显示的是你的默认模型（常常是本地的），并不代表实际调用。

| 改动 | 内容 |
|---|---|
| 各角色实际使用的模型 | 任务页顶部新增“本任务各角色实际使用的模型”：Planner → API deepseek-v4-pro；Developer → 本地 glm… → API …，**取自账本事件**，不再靠选择框猜 |
| 顶栏模型 | 显示此刻真正在用的模型（`job.current_model`，来自最近一次 `model_requested`），带“本地/API” |
| 活动记录 | `发起请求` 带上模型名；新增 升级、Diagnoser 诊断、释放本地模型、修复仍在收敛（多给一轮）、无改动拒收、任务停止 等条目 |
| 检查器 | 角色调用与每次模型调用都带实际模型 |
| 报告 | 新增“修复过程”：修复子图走过的节点、诊断、释放、延长轮数、拒收；`ModelCall.status` 增加 `abandoned` |
| 默认值 | **自动执行 + 本地优先·有界升级为默认**；有界升级下**不再显示模型选择框**（按设置里的次序执行），只有“固定模型”或“逐步确认”才选模型；说明文字改为实际的分工 |
| 设置 | 左栏顶部“← 返回”（无历史则回主页）与“⌂ 主页”，右侧“✕ 关闭设置”，`Esc` 也可关闭 |
| 建议任务 | 内置 14 个题（含你的 CSV、文本统计、动态规划），每次打开页面**随机抽 3 个**；后续在设置里细化题库/数量（已记入 M2 待确认项） |

测试：前端 31 项（新增 `entities/models.test.ts`），`tsc` 与 `vite build` 通过。**界面我没有在浏览器里逐页点验**，请你看一下并反馈。

## 3. 低质量模型方案（#1 #2 #3 #9）

| 项 | 实现 | 说明 |
|---|---|---|
| #1 确定性自检 | `goimports.py`：补缺失的标准库 import、删没用的（保守：同名局部变量不加、别名/点/非标准库不动、`rand` 有歧义不加）；接在每个文件的 gofmt 之前，事件 `imports_fixed`；此前已有逐文件语法门 | 真实 CSV 任务里已多次触发（`remove errors`、`remove os`），省掉了对应的修复调用 |
| #2 证据片段 | `snippets.py`：每条 `文件:行` 诊断附 ±4 行带行号源码，出错行 `>>`；Windows 反斜杠路径、只有文件名的 `go test` 输出都处理，同名文件不猜 | 提示词要求“就在那里改” |
| #3 尝试摘要 | `attempts.py`：每轮记录 改了什么/谁改的/未解决 N→M/还剩什么，下一轮列最近 4 轮 | 事实来自账本，不是模型自述（与 Reflexion 的区别） |
| #9 约束解码 | **更正：早已实现**（Ollama `format` + 每个角色的 schema）。我之前方案里写“建议下一步做”是错的 | 本次没有新增；方案里改为“待评估两段式输出” |

追加（真实运行里发现）：**测试修订主路径漏了 import 修复**（`undefined: strings` 出现在测试修订产物里），已补并加测试；**Planner 提示词**要求验收项钉死边界语义（表头算不算数据、结尾换行、并列排序、非法值、退出码）并在 `summary` 末尾给 2–3 条实现提示（方案 #11）。

测试：Python 328 项（新增 snippets/attempts/goimports/语法门/测试先行等）。

## 4. 真实运行

两个你提到的任务，ladder（本地 glm-4.7-flash + deepseek-flash / v4-pro），云端上限 20 万 token：

| 任务 | 结果 | 说明 |
|---|---|---|
| 写一个简单的动态规划，自己确定输入和输出，题目写在注释中 | **通过**（8 次调用，云端 1.1 万 token，81 秒） | Planner/Tester/测试文件由 v4-pro，实现由本地 GLM，一次验证即过 |
| 实现 CSV 费用汇总命令行：按类别求和并按金额降序输出 | **未通过**（两次运行，12 次调用，云端约 4 万 token） | 见下 |

**CSV 为什么没过（第二次运行，已核对账本）**：修复轮数里编译错误、测试里缺 import 都被消掉了（`imports_fixed` 多次触发：`remove errors`、`remove os`、`add io`），最后剩下的是一条**测试自己与需求矛盾**的断言：实现输出 `food: 1.00` 在前、`clothing: -0.50` 在后（这正是“按金额降序”），而强模型写的测试期望 `clothing` 在前。Diagnoser（v4-pro）看了测试后给出了“同额按类别升序”的理由，判成“实现有问题”，于是流程把轮数都花在修一个本来正确的实现上，`fix:test` 链一次都没被走到。

这是**同一类问题的第三种表现**：强模型写测试时手算/推理出错（文本统计里是字节数，这里是排序方向），而下游的 Diagnoser 被测试带偏。已写入方案 #12（期望值复核）的补充：**Diagnoser 先“盲测”——不看测试，只根据需求独立写出该用例的期望输出，再与测试期望对比；不一致即判测试有问题**。这是下一步最先做的一项，见 PLAN_LOW_MODEL_QUALITY §5 #12。

另外这次验证了：Ollama `/api/ps` 在运行结束后为空，没有空挂的模型。

## 5. 下一步（顺序见 PLAN_LOW_MODEL_QUALITY §7）

#12 期望值复核 → #14 提示字段泄漏检查 → #4 冻结前红灯检查 → #13 补丁修不动时整体重写 → #7 经验卡（只记录）。M2 见 PLAN_MULTI_MODEL_RUNTIME §10（W0 评测基线最先）。
