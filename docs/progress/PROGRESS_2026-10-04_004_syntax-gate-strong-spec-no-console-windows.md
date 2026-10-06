# 进展012：逐文件语法门 · 规格与测试用最高等级 · 子进程不再弹 cmd 窗口

2026-10-04。分支 `feature/multi-model`。前置：[进展011](PROGRESS_2026-10-04_003_m1-diagnoser-declarative-fix-flow.md)。分析与方案全文见 [PLAN_LOW_MODEL_QUALITY](../reference/LOW_MODEL_QUALITY.md)。

## 1. 为什么新任务一开始会弹出几个 cmd 窗口

Windows 上，**没有控制台的父进程**（从桌面/后台启动的 API 服务）每启动一个控制台程序就会弹一个窗口。我们启动的子进程有四处没加“无窗口”标志：
1. Python → `masa-runner.exe`（`infrastructure/runner.py`）
2. Python → `gofmt.exe`（`generation.py` 两处）
3. Go runner 内部 → worker 自身（`cmd/masa-runner/main.go`）
4. Go runner → `go build / go test / go vet` 与被验证的程序（`internal/runner/execute.go`）

已全部加上 `CREATE_NO_WINDOW`：Python 侧 `infrastructure/proc.NO_WINDOW`，Go 侧 `HideWindow(cmd)`（`contain_windows.go` / `contain_other.go`）。Go 测试通过，`.tools/bin/masa-runner.exe` 已重新构建。**需要重启 API 服务才会生效。**无法在无头环境里看到窗口，所以请你重启后实际确认；若还有窗口，请告诉我是哪个程序名。

## 2. “文本统计 CLI”为什么没自我修复

结论、证据与方案见 PLAN_LOW_MODEL_QUALITY §2：冻结的**测试文件**一开始就有语法错误（弱模型把提示词里的 `previous_attempt_error` 文本抄进了字符串），而 4 轮自动修复额度全被“实现优先”占满，修复实现又被禁止改测试。不是 runner，也不是 plan，是**测试在生成阶段被写坏、且没有人在生成时检查**。

## 3. 做了什么

| 项 | 内容 |
|---|---|
| 逐文件语法门 | 每个 Go 文件写完立刻 `gofmt -e`；有语法错误就地带 `文件:行:列` 让模型重写，最多 2 次（本地免费）；整包再兜底，云端单次生成走阶段重试/升级。`SYNTAX_RETRIES=2`，模型调用上限随之放宽 |
| 规格与测试用最高等级 | 默认 `prefer_highest_roles=[planner, tester, diagnoser]`；Developer/Repair 仍从低等级起步逐级上推。可在路由默认值里改 |
| **测试先行，由强模型写** | 发现：`*_test.go` 一直是本地 Developer 在实现之后写的（Tester 只出检查方案），且规格里没有函数签名，所以测试与实现各自发明 API（`Count` 既当类型又当函数，强模型修 3 轮也消不掉）。现在：本地起步时由 Tester 等级（最高）**先写测试、定义公共 API**，本地模型对着冻结的测试写实现（提示词要求“测试冻结、按其用到的标识符与签名实现”）。`RoleRuntime.call(freeze_key=…)`：同一个 run 里不同“职位”各自冻结模型配置（真实任务里因此被拒绝过，已加回归测试） |
| 测试修订/仲裁用最高等级 | `project_test_revision` 加入 `prefer_highest_roles`：让弱模型裁决“测试错还是实现错”并改测试，是 fix:ambiguous 里最差的一步 |
| 按进展延长修复轮数 | 固定 4 轮在“正在收敛”时截断（真实任务第 4 轮只差 1 个断言）。现在未解决条目数每创新低一次多给一轮，最多再加 4 轮（`rounds_extended` 事件）；没有进展仍在 4 轮停 |
| 弱模型 JSON 修复 | 上一步已做：截断 JSON 视为“已收到”→重试/升级 |
| 子进程无窗口 | 见 §1 |

测试：Python 304 项、前端 29 项通过；新增 `SyntaxGateTests`（修复前不存在该行为）与默认路由测试。

## 4. 真实验证

任务：“写一个文本统计 CLI：读取标准输入，输出行数、单词数和字节数。”，ladder，本地 glm-4.7-flash + deepseek-flash/v4-pro。逐步调整后的观察：

| 运行 | 现象 | 结论 |
|---|---|---|
| 调整前（你的失败） | 冻结测试有语法错误，修实现无法触及 | 见 PLAN §2 |
| A：Planner/Tester 最高等级 | 规格无签名，`Count` 类型/函数重名，强模型 3 轮没消掉 | 测试仍由本地 Developer 写 → 做测试先行 |
| B：测试先行 | 强模型写的测试质量好（边界、UTF-8、大输入）；编译错误被升级链逐级消掉；最后一次 `fix:ambiguous` 修订测试走了本地 → 改为最高等级。另：不同职位的冻结配置冲突被真实运行暴露并修复 |
| C/D | 4 轮耗尽时只剩 1 个断言（`"   
	  "` 应为 1 行，实现数成 2 行）；错误数单调下降 → 加入“按进展延长轮数” |

| E | 错误数 4→3→2 单调收敛并因进展多给了 2 轮；但最后的断言是**测试期望值算错**（`"hello world
foo bar
"` 实为 20 字节，测试写 19），强模型 3 次“不改实现”后工作流停下，Diagnoser 被测试带偏判成“实现问题” → 新增：**最强模型对实现连续无改动 ⇒ 转去修订测试一次**（`flip_to_tests`，提示词要求逐条手算期望值） |
| F（最终） | 该路径在真实运行中被走到：`repair → diagnose → fix:test（v4-pro）→ repair`。仍未通过：实现与测试在“无结尾换行时的字节数”上各执一词（实现 +1，测试不 +1），这是**规格没钉死边界语义**，不是流程缺口 |

**结论**：这个任务在当前 runtime 下还不能稳定通过。能稳定做到的是：测试先由强模型写好且质量不错、编译错误被升级链逐级消掉、轮数随进展延长、该改测试时会改测试。剩下的失败来自 **规格不够具体**（边界语义）和 **弱模型选了易错的实现方案**（`bufio.Scanner` 逐行计数）。对应的 runtime 方案见 PLAN_LOW_MODEL_QUALITY §5 的 #3 尝试摘要、#4 冻结前红灯检查，以及新增建议 #11（Planner 在 `acceptance` 里钉死边界语义并给出**实现提示**，如“字节数用 io.ReadAll/Copy 直接计数，不要逐行”）。

## 5. 后续（M1 范围内，按顺序）

0. Planner 钉死边界语义并给实现提示（见上，最直接）。
1. 失败证据为每条 `file:line:col` 附 ±5 行源码片段。
2. 尝试摘要：把前几轮“试过什么、结果如何”加入修复上下文。
3. 本地模型用 Ollama `format` 传 schema（约束解码）。
4. 测试冻结前的“红灯检查”（存根实现上必须能编译且至少一个失败）。
5. 两侧都有阻塞时，同一轮先后修实现与测试（现在是先实现后测试，额度被实现占满会饿死测试）。
6. 经验卡（先只记录，人审后再注入）。
