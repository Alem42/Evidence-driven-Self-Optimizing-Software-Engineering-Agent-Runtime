# 云服务器部署与 Release 自动同步教程

服务器：`43.134.116.107`，SSH 用户 `ubuntu`。本机密钥位于 `C:\Users\91598\.ssh\alemlitekey.pem`，密钥内容不进入仓库或 GitHub。

本次部署日期：2026-10-09。用户选择 **只读演示**。

## 1. 访问入口与部署结构

新项目：<http://43.134.116.107/apps/verdict/>。原 AI Agent：<http://43.134.116.107/apps/policy/>。

现有 Caddy 还保留 `alemz.site` 的配置，但本次服务器和 Windows 本机都无法解析该域名，HTTPS 也未能取得证书。目前使用 IP 的 HTTP 入口。域名恢复指向服务器后，再验证 `https://alemz.site/apps/verdict/`；本次没有修改 DNS。

```text
浏览器 → 现有 Caddy（80/443）
          ├─ /                   → 原个人网站
          ├─ /apps/policy/        → 原 AI Agent 前端
          ├─ /api/policy/         → 原 AI Agent 后端
          └─ /apps/verdict/       → masa-cloud 的 masa:8765
                                     ├─ / → 新项目静态前端
                                     └─ /api/ → 新项目 API
```

两个项目使用独立目录与 Compose 项目。新增 Docker 网络 `masa-edge` 只连接现有 Caddy 和新项目。原网站、后端和数据库继续使用 `alem-site_default`。

| 项目 | 目录 / 资源 |
|---|---|
| 原网站与 AI Agent | `/opt/personal-website`，Compose 项目 `alem-site` |
| 新项目 | `/opt/masa`，Compose 项目 `masa-cloud` |
| 主进程 | `masa-cloud-masa-1`，非 root uid 10001 |
| 后端宿主机端口 | `127.0.0.1:8765`，公网通过现有 Caddy 访问 |
| 项目数据 | `masa-cloud_masa-data` → `/data` |
| Go 缓存 | `masa-cloud_masa-go-cache` → `/home/masa/.cache` |
| 当前版本记录 | `/opt/masa/current.json` |
| 上一版记录 | `/opt/masa/previous.json`，第一次部署尚无上一版 |
| 发布配置 | `/opt/masa/releases/<Git commit SHA>/` |
| 自动更新器 | `/opt/masa/deploy/sync_release.py` |
| 定时器 | `masa-update.timer`，每约 5 分钟检查一次 |

服务器实测 2 核、约 2 GB 内存、2 GB swap。新容器上限 1 核、768 MB、256 个进程；日志轮转为每文件 10 MB、最多 3 个文件。容器根文件系统只读，Go 测试使用可执行的 `/tmp` tmpfs，大小上限 128 MB。

## 2. 本地模型限制和只读模式

云端 Compose 明确配置：

```yaml
environment:
  MASA_DEMO: "1"
  MASA_LOCAL_MODELS: "0"
```

`MASA_DEMO=1` 拒绝执行任务、写配置、读取 API 账户等操作。演示允许浏览角色、路由、历史和评测，以及不调用模型的 triage。首版导入了 13 份经过密钥扫描的评测结果；未复制本机模型配置、密钥、数据库或正在运行的任务。

本地模型限制独立于演示模式：即使将来启用完整 API 模式，也会拒绝创建本地 provider、恢复本地模型快照、启用本地模型配置，以及访问全部 Ollama 接口。带密钥的旧本地配置也无法进入路由。标记为云端、实际指向 `localhost`、`127.0.0.1` 或 `::1` 的 provider 同样被拒绝。

原生运行仍保留硬件自动检测和人工覆盖功能；云端 Compose 固定 `0`。不要为了云部署把它改成 `1`。

## 3. 本次首次部署的命令

Windows PowerShell 连接：

```powershell
ssh -i C:\Users\91598\.ssh\alemlitekey.pem ubuntu@43.134.116.107
```

本次首先查看现有项目，而后建立独立目录和网络：

```bash
sudo docker compose ls
sudo docker ps
free -h
sudo mkdir -p /opt/masa/{build,deploy,releases,downloads,bootstrap}
sudo docker network create masa-edge
```

首次镜像使用当前开发工作区的快照，标记为 `preview-20261009`，包含当时另一位 agent 的未提交开发内容。它是部署预览，尚未对应一个正式 GitHub Release。后续正式版本只构建 Release 的 tag 指向的提交。

上传源码时只包含 `Dockerfile`、`.dockerignore`、`pyproject.toml`、`src`、`runner` 和 `frontend`；排除依赖目录、构建产物和前端本地 `.env`。镜像的 `.dockerignore` 另外排除状态、密钥、PEM 文件和开发缓存。

对应的 Windows PowerShell 命令：

```powershell
Set-Location E:\Project\AiAgent\Project02
New-Item -ItemType Directory -Path .cache/cloud-deploy -Force
tar -czf .cache/cloud-deploy/source.tar.gz `
  --exclude=frontend/node_modules --exclude=frontend/dist `
  --exclude=frontend/.env --exclude=frontend/.env.local `
  --exclude=__pycache__ --exclude=*.pyc `
  Dockerfile .dockerignore pyproject.toml src runner frontend
scp -i C:\Users\91598\.ssh\alemlitekey.pem `
  .cache/cloud-deploy/source.tar.gz ubuntu@43.134.116.107:/tmp/
```

进入服务器后解压：

```bash
sudo tar -xzf /tmp/source.tar.gz -C /opt/masa/build
```

这是首次构建备用方式。后续正常 Release 更新直接下载已构建镜像，不重复上传源码。

本机 Docker Hub 访问异常，因此首次构建临时在云服务器进行；构建器受资源限制，之后已关闭。

```bash
cat >/tmp/masa-buildkitd.toml <<'EOF'
[worker.oci]
  max-parallelism = 1
EOF

sudo docker buildx create --name masa-bootstrap \
  --driver docker-container \
  --driver-opt memory=768m \
  --driver-opt memory-swap=1g \
  --driver-opt cpu-quota=100000 \
  --buildkitd-config /tmp/masa-buildkitd.toml

sudo docker buildx build --builder masa-bootstrap --load \
  --build-arg MASA_WEB_BASE=/apps/verdict/ \
  --build-arg MASA_VERSION=preview-20261009 \
  --build-arg MASA_REVISION=26e3581-dirty \
  -t masa-release:preview-20261009 /opt/masa/build

sudo docker buildx rm --keep-state masa-bootstrap
```

该 Dockerfile 三阶段构建：Node 编译前端；Go 编译 Linux runner；Python 运行镜像包含前端、后端、runner 和 Go 工具链。没有安装 Ollama。

初次启动使用经过 SHA-256 校验的本地发布包：

```bash
sudo python3 /opt/masa/deploy/sync_release.py --local /opt/masa/bootstrap
```

本地发布包的结构与 GitHub Release 相同，详见下一节。启动后先校验页面、会话、bootstrap、本地模型拒绝和演示状态，再接入公网。

接入现有 Caddy 的代码：`deploy/connect_existing_caddy.py`。它先备份现有 Caddyfile 和 Compose 配置，接入 `masa-edge` 网络，加入以下规则，然后验证并热加载；失败时恢复原配置。

```caddyfile
redir /apps/verdict /apps/verdict/ 308
handle_path /apps/verdict/* {
    reverse_proxy masa:8765
}
```

现有 Caddy 的 `web` 服务增加：

```yaml
networks: [default, masa-edge]
```

原网站 Compose 文件末尾增加：

```yaml
networks:
  masa-edge:
    name: masa-edge
    external: true
```

脚本使用 `docker network connect` 接通当前运行的 Caddy，随后执行 `caddy validate` 和 `caddy reload`，无需重建原网站或重启原 AI Agent。

## 4. 每次 Release 如何自动到达服务器

仓库是公开仓库，采用 GitHub Release 资产传输，无需服务器 SSH 私钥或 GitHub token。GitHub 提供公开 Release 的查询和资产下载接口：[Release API](https://docs.github.com/en/rest/releases/releases)、[资产 API](https://docs.github.com/en/rest/releases/assets)。

流程：

```text
提交代码并推送 → 发布正式 GitHub Release
  → release-cloud.yml 读取该 tag 的代码
  → 测试限制、构建镜像、验证 Linux runner 和完整 API 模式下的本地模型拦截
  → 上传经过测试的镜像与校验清单
  → 服务器定时器发现新版本
  → 校验 SHA-256，导入固定镜像 ID
  → 检查无活动任务，更新 masa-cloud
  → 检查健康和只读状态
  → 成功：记录版本；失败：恢复上一版
```

需要将本次新增的 `.github/workflows/release-cloud.yml`、`deploy/`、`scripts/ops/check_cloud.py`、相关测试和代码修复一起提交、推送，再发布包含这些文件的 tag。当前开发分支有另一位 agent 的工作，本次没有替它提交、推送或创建正式 Release。

每个成功构建的正式 Release 应包含四个资产：

| 文件 | 用途 |
|---|---|
| `masa-image.tar.gz` | 已验证 Docker 镜像的压缩归档 |
| `compose.cloud.yml` | 该版本的云端配置 |
| `check_cloud.py` | 运行状态及模型限制探测 |
| `masa-release.json` | 提交 SHA、镜像 ID、资产 SHA-256 |

只有上传全部文件后才允许服务器更新。草稿、预发布和只有 Git tag 的版本不会自动部署。定时器跟随 GitHub 的最新正式 Release；连续发布多个版本时会收敛到最新版本。构建时长加上约 5 分钟检查间隔才是实际同步时间。

发布示例，等另一位 agent 完成并提交后执行：

```powershell
# 先确认部署文件已包含在提交内，再选择新的版本号
git status
git tag v0.2.1
git push origin v0.2.1
gh release create v0.2.1 --title "v0.2.1" --generate-notes
gh run list --workflow release-cloud.yml --limit 5
```

若首次 Release 发布早于工作流落地，可在 GitHub Actions 手动运行该工作流，填写已存在的正式 tag。工作流需要位于默认分支才可在界面手动触发。

服务器定时器安装命令：

```bash
sudo install -m 644 /tmp/masa-update.service /etc/systemd/system/masa-update.service
sudo install -m 644 /tmp/masa-update.timer /etc/systemd/system/masa-update.timer
sudo systemctl daemon-reload
sudo systemctl enable --now masa-update.timer
```

更新器校验镜像身份，不使用可漂移的 `latest`。更新保留数据与 Go 缓存卷，保留本项目当前和上一版镜像，仅清理本项目更早的发布镜像，不执行全局 `docker prune`。更新器自身升级需重新安装 `/opt/masa/deploy/sync_release.py`。

## 5. 日常查看、更新、回滚

`masa-compose` 封装当前版本配置和镜像 ID，避免误操作原项目。

```bash
sudo masa-compose ps
sudo masa-compose logs --tail 100 masa
sudo docker stats --no-stream
sudo cat /opt/masa/current.json
sudo systemctl list-timers masa-update.timer
sudo journalctl -u masa-update.service -n 80 --no-pager
```

立即检查新版本：

```bash
sudo systemctl start masa-update.service
```

版本部署失败后查看日志，修复原因再重试：

```bash
sudo python3 /opt/masa/deploy/sync_release.py --force
```

回到上一版：

```bash
sudo python3 /opt/masa/deploy/sync_release.py --rollback
```

回滚后该失败/撤回的镜像会被跳过，防止定时器马上重新装回它。`--force` 可重新尝试。应用有正在执行的任务时更新会延期；只读演示通常没有活动任务。

首次部署尚无上一版；后续更新失败可回滚镜像和 Compose 配置。数据卷不会回滚。未来若新增不兼容的数据迁移，应先设计数据备份与恢复，再发布；当前这套流程不能替代数据库迁移回滚。

备份数据（只读部署可直接备份；完整模式先停服务保证数据库一致）：

```bash
sudo mkdir -p /opt/masa/backups
sudo docker run --rm --network none --user 0 \
  -v masa-cloud_masa-data:/data:ro \
  -v /opt/masa/backups:/backup \
  --entrypoint tar masa-release:preview-20261009 \
  -czf /backup/masa-data.tar.gz -C /data .
```

后续应把命令中的首版镜像替换成仍保留的当前镜像。

## 6. 本次遇到的问题与处理

| 问题 | 原因 / 处理 |
|---|---|
| `root` SSH 认证失败 | 本机现有密钥对应 `ubuntu`；使用 `ubuntu` 登录和 `sudo -n` |
| ubuntu 无法直接操作 Docker | 不在 Docker 用户组；使用已有的 sudo 权限 |
| 默认 Compose 要求未启用代理的域名变量 | 变量插值先于 profile 筛选；将代理独立为 `deploy/compose.proxy.yml` |
| 本地模型带密钥仍能成为路由候选 | ready 判断中 `or key` 绕过类型检查；改为按本地/云端分别判断 |
| 部分 Ollama 接口仍会探测本地进程 | 在 HTTP 分发入口统一拒绝整个 `/api/ollama` 路径族 |
| Docker Desktop 启动异常 | `dockerInference` 和 Secrets Engine 残留 AF_UNIX 通信文件；停掉故障进程并将两个运行目录改名备份，让 Docker 重新创建。未恢复出厂、未删除镜像/卷 |
| 本机拉取基础镜像失败 | `auth.docker.io` 解析返回错误地址；未改变系统 DNS，首次改用服务器上的受限构建器 |
| 修改 Docker Desktop 代理被自动审批拒绝 | 操作未执行，工具未给出具体原因；继续使用服务器受限构建方式 |
| 子路径配置的 TypeScript 报错 | 配置中使用 `process.env` 但未声明 Node 类型；改用 Vite 的 `loadEnv` |
| Go 测试返回 `fork/exec ... permission denied` | tmpfs 禁止执行临时测试二进制；显式给 `/tmp` 增加 `exec`，保留其容量和容器资源限制 |
| 新项目不能抢占 80/443 | 复用现有 Caddy，加入新路径规则和专用网络 |
| 现有域名无法解析 | 首次 HTTPS 探测失败，接入脚本自动恢复原 Caddy 配置；随后改用已存在的 IP HTTP 站点验证并上线，未修改 DNS |

Docker Desktop 后续再次出现相同通信文件错误时，可参考 [Docker 的相关故障报告](https://github.com/docker/desktop-feedback/issues/531)。本次目录备份位于 `%LOCALAPPDATA%\Docker\run.stale-*` 和 `%LOCALAPPDATA%\docker-secrets-engine.stale-*`。此次修复后再重启仍复现过，因此未把它视为彻底解决；云端运行与未来 GitHub Release 构建不依赖本机 Docker Desktop。

构建器资源参数依据 [Docker container driver](https://docs.docker.com/build/builders/drivers/docker-container/) 和 [BuildKit 资源限制](https://docs.docker.com/build/buildkit/configure/)。

## 7. 验证结果和边界

本次验证记录保存在同目录的 `DEPLOY_CLOUD_VERIFICATION.md`。只读展示不调用付费模型，因此此次没有进行付费 API 的任务执行验收。

将来启用完整 API 模式时，先给新路径加认证，再设置 `MASA_DEMO=0`；仅靠 Host/Origin/会话令牌不是用户登录系统。模型 API Key 在页面配置，存入数据卷，不能进入镜像、仓库或 Release 资产。
