# MASA 本机开发环境

检查日期：2026-09-26；目录：`E:\Project\AiAgent\Project02`；平台：Windows x64 / PowerShell。

## 检查与安装结果

| 工具 | 检查结果 | 项目处理 |
|---|---|---|
| Go | 初始 PATH 未发现 Go，默认安装目录未发现 | 已安装官方 Go 1.27.1 windows/amd64 到 `.tools/go` |
| Python | 本机有 3.12.7、3.14.5；受限终端初次无法读取 | 复用 3.12.7，创建 `.venv` |
| uv | 0.11.19 | 复用，项目缓存 `.cache/uv` |
| Git | 2.54.0.windows.1 | 可用；当前项目尚未初始化 Git 仓库 |
| Docker CLI | 29.5.3 | 已存在；没有验证 daemon/容器能力，当前 demo 不依赖 |
| Node.js | 24.17.0 | 已存在；当前阶段不需要前端构建 |

Go 从 [官方发布元数据](https://go.dev/dl/?mode=json) 选择稳定版 Windows amd64 ZIP，下载后 SHA-256 校验通过再解压。具体版本、文件大小与校验值记录在 `scripts/go-toolchain.json`，后续安装复用该锁定信息。

环境初始化没有修改系统 PATH、机器执行策略或替换现有 Python。`.tools`、`.cache`、`.venv` 和运行数据 `.masa` 均由 `.gitignore` 排除。后续 P0 已实现，当前代码操作见 [P0 使用说明](USER_P0_QUICKSTART.md)，不要将最初环境检查结果当作全部业务验收。

## 开发时使用

新建 PowerShell 终端，执行：

```powershell
cd E:\Project\AiAgent\Project02
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
. .\scripts\activate.ps1
go version
python --version
```

`Set-ExecutionPolicy` 只影响当前终端进程，因为本机默认禁止执行 ps1；关闭终端后结束。激活脚本必须使用开头的点加空格（dot-source），才能把环境变量应用到当前终端。

不激活也可通过以下路径直接调用。日常建议激活，以保证 Go 缓存和工具链选择一致：

```powershell
.\.tools\go\bin\go.exe version
.\.venv\Scripts\python.exe --version
```

IDE Python 解释器设置为 `E:\Project\AiAgent\Project02\.venv\Scripts\python.exe`。Go SDK 位置为 `E:\Project\AiAgent\Project02\.tools\go`。如果 IDE 未继承项目 PATH，使用该 SDK 路径或在已激活的终端运行 Go 命令。语言服务器 gopls 暂未安装，代码开发阶段需要时再锁定安装版本。

## 复现与验证

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup-env.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-env.ps1
```

setup 脚本依赖已有 uv 和互联网，针对 Windows amd64；下载 Go、校验后安装到项目，并在不存在时创建 Python 3.12 虚拟环境。首次已复用本机 Python 3.12.7。`.python-version` 固定 minor 为 3.12，不保证另一台机器的 patch 版本相同；若未来需要严格复现，再固定完整版本。已有 Go/venv 不会被该脚本主动升级。

verify 脚本检查 Python 正在虚拟环境中运行、SQLite 可读写及 asyncio 可导入，然后在 `.cache/env-smoke` 生成一个独立 Go module，执行 JSON/context 基础编译测试和 `go vet`。这些是工具链烟雾验证，不是未来 runner 的超时、进程树清理或 runtime 恢复测试。

本次实际执行结果：Python 3.12.7 虚拟环境检查通过；SQLite 与 asyncio 检查通过；Go `TestEnvironment` 通过；`go vet ./...` 退出码为 0；验证脚本整体退出码为 0。

首次配置时的 Codex 受限执行环境不能读取用户目录中的 Python，曾误报 `No Python at ...`；普通本机终端可用，当时使用获准的受限环境外执行完成验证。这是历史诊断，不代表当前会话仍受该限制；不要据此重复卸载 Python 或修改系统 PATH。

## 隔离方式

Python 的 `.venv` 隔离第三方依赖，并依赖现有基础解释器。P0 已添加 pyproject.toml 和 uv.lock，使用 `uv sync --locked` 安装本地 masa-runtime 包；业务运行仅使用标准库，setuptools 构建依赖固定版本。

Go 没有需要仿照 Python 创建的 venv。项目使用独立 SDK、模块文件和目录配置：

| 配置 | 值/用途 |
|---|---|
| GOROOT | 项目 `.tools/go`，明确选择安装的 SDK |
| GOPATH | 项目 `.cache/gopath` |
| GOMODCACHE | 项目 `.cache/go-mod` |
| GOCACHE | 项目 `.cache/go-build` |
| GOTOOLCHAIN | `local`，不自动下载其他工具链；升级需明确进行 |
| GOWORK | `off`，避免外部 go.work 影响独立目标仓库 |
| GOENV | `off`，不读取或写入全局 go env 配置 |
| CGO_ENABLED | `0`，当前标准库方案不要求 C 编译器 |

后续 runner 和目标示例各自管理 go.mod/go.sum。当前 smoke module 留在缓存目录，未创建假业务工程。未来若需 CGO、race detector 或依赖特定 Go 版本，应增加相应工具链检查并更新这些约定。

安装机制参考：[Go 官方安装说明](https://go.dev/doc/install)、[uv 项目结构与虚拟环境](https://docs.astral.sh/uv/concepts/projects/layout/)。
