# P0 使用说明：运行、暂停、恢复与检查证据

适用版本：MASA 0.1.0，Windows x64。P0 已实现离线验证框架，不会修改用户代码，不调用真实 LLM。scripted provider 只按确定规则提出工具调用；Go 检查实际执行，结果不是模拟数据。

## 1. 构建与测试

从项目根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test.ps1
```

build 使用 `uv sync --locked` 安装当前包，再构建 `.tools/bin/masa-runner.exe`。Python 和 Go 业务代码均无第三方运行时依赖；Python 构建工具版本固定于 pyproject.toml。Go 未使用第三方模块，因此不需要空 go.sum。

test 会先构建，再运行 Go 单元测试、go vet 和 Python 单元/集成测试。测试包含真实编译、超时、取消和子进程树清理，不需要 API key。第一次编译可能比后续慢。

## 2. 运行最小示例

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
. .\scripts\activate.ps1
masa --version
masa run --repo .\tests\fixtures\go-pass
```

会输出一个 run ID 和 JSON 状态。默认图为 `verify(agent) → gate`。verify 内部执行两次 scripted 响应：提出 go_test → 接收真实结果 → 提交摘要。Gate 重新读取工具账本后判断，摘要不能宣布成功。

本目录是 P0 测试夹具，并非 P1 的 Todo 示例产品。运行期间将源文件复制到 `.masa/workspaces/<run_id>`；源目录不会被该工具修改。

可以单独验证另外两个 operation：

```powershell
masa run --repo .\tests\fixtures\go-pass --operation go_vet
masa run --repo .\tests\fixtures\go-pass --operation go_fmt_check
```

一个 P0 run 只验证选定 operation，不能把 go_test 成功解释为三个检查和隐藏验收都通过。format check 只报告格式问题，不写文件。

## 3. 暂停与恢复

```powershell
masa run --repo .\tests\fixtures\go-pass --pause-after 1 --deadline-seconds 1800
masa status <run_id>
masa resume <run_id>
masa events <run_id>
masa report <run_id>
```

把 `<run_id>` 替换成实际输出的 ID。暂停发生在 verify 结果已经落盘、Gate 尚未运行的节点边界；resume 使用已保存工具结果，不再执行一次 go test。status 是结构数据，events 是时间线，report 输出可读 Markdown 到终端。

deadline 从创建 run 时计时，暂停也计入；超过 deadline 不会偷偷获得新预算。恢复要求使用相同工具二进制，工作副本也必须匹配原快照。

## 4. 取消、失败和不确定结果

在第二个终端调用 `masa cancel <run_id>`，正在运行的 runtime 会读取取消标记并终止工具。暂停任务可记录取消请求，再通过 resume 落成 cancelled 状态。终端 Ctrl+C 同样停止当前执行。

状态含义：

| 状态 | 意义 |
|---|---|
| succeeded | 本次选定检查通过，当前证据匹配快照 |
| failed | 检查失败、预算耗尽或 deadline 到期 |
| paused | 用户要求的节点边界暂停 |
| cancelled | 已处理取消/中断 |
| needs_attention | 工作区变化、产物损坏、工具结果不确定等，禁止自动猜测 |

进程异常退出后，有确切落盘结果的工具可以复用；只有 intent 没有 result 时，resume 返回 needs_attention，**不会重放未知调用**。P0 不提供跳过该保护的强制恢复选项。确认旧执行停止并排查原因后，可以从源仓库新建 run，保留旧账本。

退出码：0 表示 succeeded/paused 或只读查询成功；1 表示执行结果非成功；2 表示参数、配置或系统错误。不要只看进程退出码，检查报告中的具体原因。

## 5. 文件与协议

- SQLite：`.masa/runtime.sqlite3`，记录 runs、steps、attempts、tool_calls、events。
- 产物：`.masa/artifacts/<sha256>.json`，内容寻址并在读取时检查哈希。
- 工作副本：`.masa/workspaces/<run_id>`，复制清单含非 Git 跟踪文件；排除 .git、缓存、虚拟环境和 .env 类文件。
- 源码默认上限：32 MiB / 4000 文件，拒绝 symlink/junction；源目录不能包含目标 state 目录。
- 自定义状态目录使用全局参数：`masa --state-dir E:\somewhere\masa-state run --repo ...`。

Go runner 收到一行 JSON 后执行一个 operation；stdin 在执行期间保持打开，`{"cancel":"request_id"}` 或 stdin EOF 表示取消。它供 Python adapter 使用，不建议用会立即关闭 stdin 的普通管道直接运行。`masa-runner --version` 可独立使用。

Windows coordinator 与 worker 在启动后、执行仓库代码前加入 kill-on-close Job Object；进程退出时清理其后代。参考 [Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。当前非 Windows 构建会明确拒绝执行，尚未实现跨平台进程 containment。

## 6. 边界与下一阶段

P0 串行调度通用图；没有四角色并行、代码写工具、AST 索引、完整 Context Builder、跨任务记忆和自优化。Go 工具只在可信本地项目运行，默认离线模块获取、只读 go.mod，测试代码仍具备普通进程权限；这不是不可信代码沙箱。

默认 runner/Go 路径适用于当前工作区的 editable 安装，可通过 `--runner` 和 `--go` 显式指定。发布成独立 wheel 的资源定位与跨平台分发后置。

下一项为 P1-01：小型 Go 业务示例、受控补丁和写入恢复。不要把当前 fixture 或 scripted provider 描述成已实现自动修复。
