# MCP 服务

把 MASA 的**验证**和**评测**暴露给任何 MCP 客户端（Claude Desktop、Claude Code、其它 Agent）。这是暴露层：全部复用现有的 Runtime、Runner、Gate、路由与评测，没有另写一套。实现在 `src/masa/interfaces/mcp_server.py`，测试在 `tests/test_mcp.py`。

## 工具
| 工具 | 作用 | 备注 |
|---|---|---|
| `verify_project(files, checks, timeout_seconds)` | 在隔离的临时工作区里运行白名单检查，返回 **Gate 的裁决**与每项检查的证据 | `files` 是 `{路径: 内容}`；只接受相对路径的 `.go`、`go.mod`、`go.sum`；最多 64 个文件、总共 512 KB；`checks` 只能是 `go_test / go_vet / go_fmt_check`；输出截断到 4000 字符；不接受任何命令字符串 |
| `route_preview(role, history, need_tokens, candidates)` | 纯函数 `route()` 的只读预览：下一次尝试用哪个模型、为什么 | 不传 `candidates` 时用已保存的模型配置 |
| `get_run_report(run_id)` | 读取某次运行的报告 | 只读；`run_id` 必须是 32 位十六进制 |
| `run_benchmark(suite, max_cloud_tokens, max_minutes)` | 真实评测 | **会调用模型、花钱，默认拒绝**：服务端环境里设 `MASA_MCP_ALLOW_BENCH=1` 才允许；token ≤ 50 万、时间 ≤ 30 分钟 |

`verify_project` 的裁决来自这次隔离运行的账本（工具证据），不是任何声明。

## 安装与启动
```bash
pip install "mcp>=1.2"                 # 可选依赖；核心不需要
python scripts/ops/mcp_server.py       # stdio
```
环境变量：`MASA_STATE`（状态目录，默认 `.masa`）、`MASA_RUNNER`、`MASA_GO`（默认 `.tools` 下的 runner 和 Go）、`MASA_MCP_ALLOW_BENCH`。

## 接入 Claude Code / Claude Desktop
Claude Code：
```bash
claude mcp add masa -- python E:/Project/AiAgent/Project02/scripts/ops/mcp_server.py
```
Claude Desktop（`claude_desktop_config.json`）：
```json
{ "mcpServers": { "masa": { "command": "python", "args": ["E:/Project/AiAgent/Project02/scripts/ops/mcp_server.py"] } } }
```

## 验证状态
- 单元测试：输入校验（路径穿越、绝对路径、二进制、超大、非白名单检查）、Gate 裁决、输出截断、临时目录清理、路由预览、报告、花钱工具默认拒绝；用官方 SDK 在进程内列出并调用工具。
- 真实 runner：用本机的 Go 工具链验证通过与失败两种情况（找到并修复了一个真实缺陷：Windows 上文本模式写文件会把 LF 变成 CRLF，gofmt 因此误判）。
- **没有验证**：用真实的 MCP 客户端（Claude Desktop/Code）端到端接入；`run_benchmark` 的真实运行。
