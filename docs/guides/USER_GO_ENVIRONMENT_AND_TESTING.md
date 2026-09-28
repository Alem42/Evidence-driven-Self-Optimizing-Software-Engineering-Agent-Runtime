# Go 环境、harness 与手动验证

检查日期：2026-09-29。本文按当前代码说明，后续设想单独列出。

## 1. 这次失败发生在哪里

随机数项目 `f996f79f5b804a3186b346a99681622a` 的核心函数和参数解析测试已经通过，失败在测试为 CLI 构建可执行文件时。

```text
项目根目录/                 go.mod 在这里
  internal/                 逻辑包
  cmd/                      没有 .go 文件
    app/                    main.go 和 main_test.go 在这里
```

Go 测试在被测试包的目录下运行。`cmd/app/main_test.go` 中原来的代码是：

```go
cmd := exec.Command("go", "build", "-o", binPath, ".")
cmd.Dir = ".."
```

`.` 表示构建工作目录里的包，`..` 把工作目录从 `cmd/app` 改成 `cmd`。所以编译器在 `cmd` 中找不到 Go 文件。修复是删除这行 `cmd.Dir`，留在 `cmd/app` 构建 `.`。如果有意切换到模块根目录，构建目标应改为 `./cmd/app`。[Go 命令文档](https://pkg.go.dev/cmd/go)

这次不是缺少 `io`。此前某个版本缺 `io` 导入，本次是你新生成的另一份代码。修好一个版本不会保证模型下次生成的所有版本都正确；本轮同时改了生成提示和通用失败分类，降低同类问题反复发生的可能。

整数求和项目 `53dc4c7efdd34041b5abaa8e86620c17` 有两个测试缺陷：

1. 测试切到模块根目录后仍构建 `.`，但根目录只有 `go.mod`，入口仍在 `cmd/app`。
2. 测试将 `int(^uint(0)>>1)+1` 写作期望结果。这里是在编译期计算一个有 `int` 类型的常量，数值超出可表示范围，测试还没运行就被拒绝。原测试想验证运行时环绕，现保留该语义，将期望值改为可表示的最小整数 `-maxInt-1`。这不等于给所有求和产品规定溢出策略；若产品要求溢出报错，要先修改规格和相应断言。[Go 整数溢出规范](https://go.dev/ref/spec#Integer_overflow)

上述情况都应修订测试。普通 Developer 修复冻结 `_test.go`，所以此前点“修复实现”会出现 `repair cannot change tests`。现在前端和自动模式使用后端同一份分类，识别构建目录错误、测试常量溢出，并提示测试修订；不会为了过关删除测试。

## 2. 直接手动验证修好的项目

| 项目 | 最新通过的验证运行 |
|---|---|
| 随机数 | `68e1db6c7bf04cba8e7952b3f8e9ff68` |
| 整数求和 | `52d50f2b865d481f98bf105d3b90ef6b` |

刷新 `http://127.0.0.1:8765`，在相应项目的历史版本中选择上表运行，可查看全部 Go 检查和 Gate。旧失败版本仍显示失败，这是历史证据。当前 IDE 的旧 `4d162...` 标签页不是这次修复的项目版本。

本轮已生成手动验证副本与 `.exe`。从仓库根目录直接执行：

```powershell
cd E:\Project\AiAgent\Project02

# 应输出 7，退出码 0。
& .\.masa\manual-verification\68e1db6c7bf04cba8e7952b3f8e9ff68\app.exe -min 7 -max 7
$LASTEXITCODE

# 应输出 2，退出码 0。
& .\.masa\manual-verification\52d50f2b865d481f98bf105d3b90ef6b\app.exe 1 -2 3
$LASTEXITCODE

# 应只有错误输出，退出码 2。
& .\.masa\manual-verification\52d50f2b865d481f98bf105d3b90ef6b\app.exe 1 abc 2
$LASTEXITCODE
```

这些二进制位于 Git 忽略的本地目录，仅适用于本机。每个目录下的 `project/` 是可编辑源码副本。修改后需要重新构建 `.exe`：

```powershell
cd E:\Project\AiAgent\Project02
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
. .\scripts\activate.ps1

# 与 harness 一样禁止下载依赖或修改模块文件。
$env:GOPROXY = 'off'
$env:GOFLAGS = '-mod=readonly'

Push-Location .\.masa\manual-verification\52d50f2b865d481f98bf105d3b90ef6b\project
try {
    go test -count=1 ./...
    go vet ./...
    gofmt -l .
    go build -o ..\app.exe ./cmd/app
    if ($LASTEXITCODE -ne 0) { throw '构建失败，请查看上方错误。' }
    & ..\app.exe 1 -2 3
} finally { Pop-Location }
```

随机数可将上面路径替换为其运行 ID。`gofmt -l .` 应没有输出；它不改文件。直接运行命令不会写入 MASA 的 Gate 账本；要产生新的平台验证记录，使用前端重新验证。不要手改 `.masa/workspaces/<run-id>` 中的历史快照，它受内容哈希检查保护；手动实验用这里的副本。

## 3. Go 环境从哪里开始理解

先区分三个层次：

| 层次 | 用途 | 当前项目 |
|---|---|---|
| Go 工具链（SDK） | 编译器、链接器、`go test`、标准库和格式化工具 | `.tools/go`，实测 Go 1.27.1 Windows amd64 |
| 项目模块 | `go.mod` 声明模块名、Go 版本和依赖要求；同一模块下可有多个包目录 | 每个生成项目有自己的 `go.mod` |
| 构建产物 | 编译出的 Windows `.exe` | 标准库、CGO 关闭的 CLI 通常可直接运行，无需再启动 Go 编译器 |

Go 项目通常不需要像 Python 一样给每个项目创建 venv。Python `.venv` 是 MASA Python 服务自己的环境；生成的 Go 项目使用 Go 模块管理依赖。模块文件负责版本声明，缓存负责存放下载和编译结果。[Go 模块说明](https://go.dev/doc/modules/gomod-ref)

`import "io"` 的意思是“这个源文件使用 io 包”。`io` 随 SDK 提供，不是缺一个需要下载安装的插件。`fmt`、`os`、`strings` 同理。每个 Go 源文件分别声明它使用的导入，不能依赖另一个文件写过 import。

第三方包则属于另一个模块，通常需要在 `go.mod` 声明版本，并在模块缓存中取得源码。`go get 包路径@版本` 管理依赖要求，`go mod tidy` 根据源码导入整理依赖；它们不会替你设计缺失的业务代码。[Go 依赖管理](https://go.dev/doc/modules/managing-dependencies)

## 4. 当前环境的具体代码

| 位置 | 职责 |
|---|---|
| `scripts/setup-env.ps1`、`scripts/go-toolchain.json` | 初次下载、校验、安装锁定的 SDK |
| `scripts/activate.ps1` | 为当前 PowerShell 设置 SDK、项目缓存和 Python venv |
| `src/masa/interfaces/cli.py` | 默认选择 `.tools/go/bin/go.exe`、`.tools/bin/masa-runner.exe` |
| `src/masa/infrastructure/runner.py` | 检查二进制存在，启动 Go runner，过滤父环境，交换 JSON，处理取消与超时 |
| `runner/internal/runner/execute.go` | 校验工作区、设置工具工作目录和环境、执行固定命令 |
| `src/masa/infrastructure/workspaces.py` | 复制快照、计算和验证内容哈希 |
| `src/masa/runtime/engine.py` 的 `_gate` | 核对当前快照、节点结果和工具账本是否一致，检查真实状态与退出码 |

runner 固定了以下环境：

| 配置 | 当前含义 |
|---|---|
| `GOROOT` | Python 适配器设置为被选 SDK 的目录 |
| `PATH` | SDK 的 bin，加 Windows System32；不继承完整用户 PATH |
| `GOENV=off`、`GOWORK=off` | 避免全局 Go 配置和外层 go.work 改变目标项目 |
| `GOTOOLCHAIN=local` | 使用选定的本地 Go，不自动下载更高版本 |
| `CGO_ENABLED=0` | 当前无需 C 编译器；依赖 CGO 的项目不在当前支持范围 |
| `GOPROXY=off` | 本次工具执行不从模块代理解析、下载缺失依赖 |
| `GOFLAGS=-mod=readonly` | 检查阶段不自动修改依赖声明 |

工具链版本不满足 `go.mod` 时会报错；当前没有自动切换 SDK 的管理器。Go 自身如何选择工具链见 [官方工具链文档](https://go.dev/doc/toolchain)。

缓存需要特别说明：激活脚本设置 `.cache/go-build`、`.cache/go-mod`、`.cache/gopath`。Python runner 目前继承这些允许的变量，**没有强制所有启动方式都用项目缓存**。本次在未激活的终端检查时，`go env` 返回用户目录缓存。这不影响标准库测试，但会让不同启动方式的缓存位置不同。要保持一致，在激活过的终端启动 MASA；统一环境检查和默认缓存属于后续小改进。

## 5. harness 实际怎样工作

```text
需求 → Planner 项目规格 → Tester 验证方案 → Developer 源码和测试
    → 审核采用 → Runtime 发布新快照并校验检查计划
    → Python Runner → Go Runner → test / vet / format
    → 工具结果写入账本 → Gate 核对 → 成功或带证据的修订
```

`Tester` 先设计测试方案；生成后的 `_test.go` 由真实 `go test` 编译、执行。`Execute` 阶段里看到测试先构建应用是正常的集成测试步骤。本次错误就在这个测试内部的构建步骤。

harness 是这套围绕代码执行的支撑流程：准备工作区、限定工具操作、传递环境、限制时间和输出、收集退出码与日志、保存结果。Runtime 决定能做什么；Go runner 执行固定操作；Gate 核对结果。Gate 不会证明需求已被全部覆盖，也不会自动识别所有错误测试期望。

当前真实命令是 `go test -json -count=1 -timeout=... ./...`、`go vet ./...`、`gofmt -l .`。测试从模块根目录发起，但每个测试包的运行目录是自己的源码目录。`-count=1` 禁止复用上次测试结果，编译缓存仍可复用。

## 6. 检测、导入和安装：现在分别支持什么

| 情况 | 当前行为 | 自动修复能力 |
|---|---|---|
| Go 或 runner 二进制不存在 | 启动执行器时明确报错 | 运行任务不会自动安装；setup/build 脚本负责环境准备 |
| 标准库 import 漏写、未使用 | Go 编译报错并保留诊断 | 有界模型修复可以修改对应源码，之后必须重验；不是编译器自动导入 |
| 测试准备写错目录、常量越界 | 真实测试失败；本轮补齐修复分类 | 创建测试修订并保留断言，重新审核/验证 |
| 第三方模块未声明或缓存缺失 | 当前标准库模式下构建失败 | 没有自动联网安装、执行 go get/tidy 的流程 |
| Go 版本不满足要求 | 本地工具链报错 | 没有自动升级 |

当前的 `gofmt` 只处理格式，不补 import。`goimports` 可以整理、增删导入，是后续可接入的独立工具；它和安装第三方模块是不同步骤。目前项目没有接入它。[goimports 文档](https://pkg.go.dev/golang.org/x/tools/cmd/goimports)

## 7. 隔离、复用和后续顺序

现在每次运行复制项目文件到独立目录，记录批准版本和内容哈希；修复生成新快照。工具环境只继承允许的变量，不把 API 密钥传给测试进程。Windows Job Object 在取消、超时或进程退出时回收子进程。

这些是工作区、环境和进程生命周期隔离，**不是完整容器或系统沙箱**。测试仍以当前用户权限运行；哈希检查能发现快照被修改，但不能阻止它访问工作区外的全部文件。`GOPROXY=off` 限制 Go 模块下载，不等于关闭测试程序的网络能力。

复用方面：SDK、Go 模块缓存和编译缓存可以共享；每次测试结果重新产生，工作区与批准记录分开。当前没有缓存配额、自动清理、按环境规格选择多个 SDK、容器池或测试服务编排。

后续按小步增加，先支持真实需要：

1. 环境预检：展示实际 SDK、缓存路径、CGO、模块要求及缺失项；统一不同启动方式的默认缓存。
2. 导入整理：锁定 goimports 版本，在新草稿中产生可审核差异，然后重新编译验证。
3. 外部依赖：引入明确的依赖准备阶段，限制来源和版本，允许的下载只发生在准备阶段；生成并保存 go.mod/go.sum 后，执行阶段继续只读。不能在每次失败时随意 go get 最新版本。
4. 多版本与复杂环境：按操作系统、架构、Go 版本、CGO 和依赖文件定义环境规格；相同规格复用缓存，有配额和回收策略。确实需要数据库、C 编译器或系统库后，再引入容器与对应运行器。

这些是后续设计安排，本轮完成的是日志分类、两份失败项目修订、真实执行验证及手动副本。
