# Docker 部署（展示用）/ Docker deployment (for showcase)

一个镜像、一个进程：同源托管前端 + API，默认是**只读演示模式**。不含本地模型（Ollama），也不包含任何 API 密钥。
One image, one process: it serves the frontend and the API from the same origin, **read-only demo mode by default**. No local model (Ollama) and no API keys inside.

## 1. 最快上手 / Quick start

```bash
docker compose up --build -d
# 打开 / open  http://localhost:8765
```

默认只绑定 `127.0.0.1`。想让局域网访问，设 `MASA_BIND_ADDRESS=0.0.0.0`，并把访问用的 `host:port` 加进 `MASA_ALLOWED_HOSTS`（见 §3）。

## 2. 演示模式能做什么 / What demo mode does

| 能 / allowed | 不能 / refused (403) |
|---|---|
| 浏览项目、历史、评测结果、角色表（`/api/roles`）、路由与工作流、预检（triage，纯规则，不调用模型） | 发起/取消/批准任务、启动评测、`/api/accounts`（会用到 API 密钥）、`/api/ollama`、`/api/hardware`、`/api/diagnostics` |

页面顶部会显示“演示模式（只读）”横幅。
想有内容可展示：

```bash
python scripts/make_demo_state.py            # 只复制 .masa/bench/results/*.json，并扫描密钥，发现即中止
MASA_STATE=./demo-state docker compose up -d # 目录要对容器用户 uid 10001 可读写
```

不设 `MASA_STATE` 时用命名卷 `masa-data`，历史为空。

## 3. 环境变量 / Environment variables

| 变量 | 默认 | 含义 |
|---|---|---|
| `MASA_DEMO` | `1` | `1` = 只读演示；`0` = 完整模式（见 §5） |
| `MASA_BIND` | `0.0.0.0`（镜像内） | 进程监听地址；对外暴露由 compose 的 `MASA_BIND_ADDRESS` 控制（默认 `127.0.0.1`） |
| `MASA_ALLOWED_HOSTS` | compose 里 localhost + `MASA_DOMAIN` | 允许的 `Host` 头，逗号分隔。其他 Host 一律 403（防 DNS rebinding） |
| `MASA_ORIGINS` | compose 里 localhost + `https://$MASA_DOMAIN` | 允许取得会话令牌的 `Origin` |
| `MASA_STATIC_DIR` | `/app/web` | 前端构建产物目录 |
| `MASA_PORT` / `MASA_STATE` / `MASA_DOMAIN` | `8765` / `masa-data` / 空 | 仅 compose 使用 |

## 4. 公网展示：Caddy 反向代理 / Public host behind Caddy

自动 HTTPS（Let's Encrypt）+ 基本认证，见 [deploy/Caddyfile](../../deploy/Caddyfile)。

```bash
docker run --rm caddy:2 caddy hash-password --plaintext 'your-password'   # 得到哈希 / get the hash
export MASA_DOMAIN=demo.example.com MASA_BASIC_USER=demo MASA_BASIC_HASH='<hash>'
docker compose --profile proxy up --build -d
```

注意：哈希里有 `$`，写进 `.env` 文件时要写成 `$$`。DNS 要先指到这台机器，80/443 要放行。

## 5. 完整模式（会调用云端模型，花 token）/ Full mode

```bash
docker run --rm -p 127.0.0.1:8765:8765 -e MASA_DEMO=0 -e MASA_ALLOWED_HOSTS=localhost:8765 -v masa-data:/data masa
```

**完整模式没有登录**，只靠 Host/Origin/令牌防本机的恶意网页。所以：
- 不要把 8765 直接暴露到公网；必须放在带认证的代理（§4）后面。
- API 密钥只通过页面的“账号”写入 `/data`（卷里），**永远不要** COPY 进镜像；`.dockerignore` 已排除 `provider-keys.local.json` 与 `.masa`。
- 镜像里没有 Ollama；本地模型不可用，路由请只选云端。

## 6. 镜像里有什么 / What is in the image

- 三阶段构建：node 构建前端（`VITE_API_BASE` 为空 = 同源）→ golang 构建 Linux 版 `masa-runner` → `python:3.12-slim` + Go 工具链（验证阶段真的运行 `go build/test/vet`）。
- 非 root 用户 `masa`（uid 10001）；compose 里 `read_only`、`cap_drop: ALL`、`no-new-privileges`，仅 `/data`、`/tmp`、缓存可写。
- 健康检查：`GET /`。
- Linux 上 runner 的隔离方式：Windows 用 Job Object；Linux 把被测进程放进新会话，超时/取消时整组 `SIGKILL`，不留孤儿进程。

## 7. 验证 / Verifying

```bash
docker build -t masa:test .
docker run -d --name masa-test -p 18765:8765 -e MASA_ALLOWED_HOSTS=localhost:18765,127.0.0.1:18765 masa:test
export MSYS_NO_PATHCONV=1   # Git Bash on Windows：否则 /tmp/... 会被改写成 Windows 路径
docker cp scripts/docker_smoke.py masa-test:/tmp/docker_smoke.py
docker exec masa-test python /tmp/docker_smoke.py                # 预期 ALL PASS
docker rm -f masa-test
```

`docker_smoke.py` 在容器里验证真实的 Linux runner：go test/vet/gofmt 通过、失败测试被如实报告、超时返回 `timeout` 且没有孤儿 `calc.test` 进程、工作区里的符号链接被拒绝。
单元测试：`tests/test_deploy.py`（演示拦截、静态托管与目录穿越、Host/Origin、环境变量默认值、进程组）。

## 8. 已验证与未验证 / Verified vs not

- 已验证（真实容器）：镜像构建、健康检查、非 root、前端与 SPA 回退、演示拦截、Host/Origin 拦截、Linux runner 烟测 6/6。
- 仅单元测试：环境变量默认值、`kill_group`（Windows 上跳过，容器烟测覆盖了同一机制）。
- 未验证：Caddy + 公网域名 + 自动 HTTPS（需要真实域名）；完整模式下的云端任务在容器里端到端跑通。
