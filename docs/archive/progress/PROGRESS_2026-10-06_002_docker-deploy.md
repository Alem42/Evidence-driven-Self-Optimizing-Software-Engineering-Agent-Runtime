# Docker 部署（展示用）/ Docker deployment

分支 `feature/dynamic-roles`。目标：把项目打成一个镜像，先部署做展示（不含本地模型）；M2 的 P2–P4 下一步再做。

## 做了什么
- **同源单进程**：服务端新增 `--bind/--host/--static-dir/--demo`（均有 `MASA_*` 环境变量），同时托管前端与 API；前端用空 `VITE_API_BASE` 构建。
- **演示只读模式**：只放行展示用的 GET 与纯规则的 triage；发起任务、账号（密钥）、Ollama、硬件、诊断一律 403；前端显示横幅。
- **安全**：Host 白名单（防 DNS rebinding）、同源判断（含 TLS 代理后的 https）、无 Origin 的同源页面需 `Sec-Fetch-Site: same-origin` 才发令牌、静态文件防目录穿越、CSP 等响应头。
- **Linux runner**：`contain_other.go` 在 Linux 可用（符号链接检查用 `Lstat`）；Python 侧子进程放进新会话，超时/取消整组 SIGKILL。
- **交付物**：`Dockerfile`（三阶段）、`.dockerignore`、`docker-compose.yml`（只读根文件系统、cap_drop ALL、可选 Caddy 代理）、`deploy/Caddyfile`、`scripts/make_demo_state.py`、`scripts/docker_smoke.py`、`docs/guides/DEPLOY_DOCKER.md`。

## 验证（如实）
- 真实容器：构建成功、健康、非 root、前端/SPA 回退/资源缓存、演示拦截、错误 Host 与跨站 Origin 被拒；容器内 Linux runner 烟测 6/6（go test/vet/gofmt、失败如实报告、超时无孤儿进程、符号链接被拒）。
- 测试：后端 429 通过（新增 `tests/test_deploy.py`；`test_server_lifecycle` 的一条断言随 `make_server` 新增关键字参数而更新），前端 33 通过。另有 4 个既有 ERROR（`test_auto_project`/`test_testlint` 里被导入的 `test_*` 函数被 pytest 当成用例收集），与本次改动无关。
- 未验证：Caddy + 真实域名 + 自动 HTTPS；完整模式在容器里端到端跑云端任务。

## 之后可优化
- 演示状态目前只带评测结果；可加“回放一次真实运行”的静态样例。
- 完整模式缺登录，公网必须经带认证的代理；若要多人使用需要真正的账号体系。
- 镜像未含 Ollama；本地模型需另起 sidecar 容器并在路由里配置。
