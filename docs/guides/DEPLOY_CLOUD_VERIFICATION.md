# 云部署验收记录（2026-10-09）

## 已部署状态

- 访问入口：<http://43.134.116.107/apps/verdict/>。
- 首版标记：`preview-20261009`，为当时开发工作区的快照，包含未提交内容；未创建正式 GitHub Release。
- 服务器镜像身份：`sha256:41e407762c452fe7dd2926ab3f3bcff7edc79a925e5f2a3c4165af078085dba0`。
- 容器：`masa-cloud-masa-1`，健康；进程用户 `masa`，根文件系统只读，内存上限 805306368 字节，CPU 上限 1 核，进程上限 256，重启策略 `unless-stopped`。
- 启动后空闲内存观察值约 24.4 MiB；这不是任务运行时的峰值估算。
- 外部端口仅绑定服务器 `127.0.0.1:8765`。公网复用原 Caddy 的 80 端口。
- `masa-update.timer` 已启用且 active，约每 5 分钟检查；首次查询输出 `No published stable Release yet.`。
- 13 份演示评测结果已导入独立数据卷，经现有导出器扫描未包含已知 API 密钥。

## 通过的验证

1. 服务器上运行部署、模型限制、模型快照和更新器相关单元测试：**23 项通过**。更新器随后新增了不同 Docker 镜像存储方式的身份处理，相应 **4 项更新器测试再次通过**，覆盖校验失败、活动任务延期、失败恢复上一版、成功后保存版本记录。
2. 前端 `npm run build` 成功，配置 `/apps/verdict/` 前缀；`npm test` **40 项通过**。
3. 三阶段 Dockerfile 在受限 BuildKit 中真实构建成功；镜像大小观察值约 **575 MB**。
4. 在 1 核 / 768 MB / 只读根文件系统 / 非 root / 禁网环境下，真实 Linux runner **6/6 通过**：`go test`、`go vet`、格式检查、失败测试如实报告、超时且无孤儿进程、符号链接拒绝。
5. 正式容器内运行 `check_cloud.py`：页面、session、bootstrap、本地模型接口及 provider 拦截通过；没有付费模型调用。
   另起一个无公网端口、禁网的完整 API 模式容器（`MASA_DEMO=0`、`MASA_LOCAL_MODELS=0`），同一检查也通过；检查后已移除该临时容器。
6. Windows 本机对公网入口进行 GET 验证：页面 200、JS/CSS 200、设置子路由 200；bootstrap 显示 `demo=true`、`local_models.allowed=false`、`runner_ready=true`；创建任务 POST 返回 403。
7. 原网站 `/`、原 Agent `/apps/policy/`、原后端 `/api/policy/v1/health` 全部 200。原 Caddy、后端、数据库容器仍显示连续运行 13 天；此次没有重启它们。
8. 本地默认 Compose、云端 Compose 配置可解析；Python 更新器和 GitHub Actions YAML 语法校验通过。

## 验证边界

- GitHub Actions 工作流已保存为 `.github/workflows/release-cloud.yml`，但没有替另一位正在开发的 agent 提交、推送或发布 Release。因此尚未实测一次完整的「正式 GitHub Release → 服务器」自动更新。服务器安装、定时查询、发布包安装及回滚控制逻辑已分别验证。
- 没有运行付费模型生成任务；用户选的是只读演示。
- 域名 `alemz.site` 在本次检查中无法解析，HTTPS 未验证成功，当前使用 IP HTTP。
- 浏览器自动化工具两次超时，因此公网验证采用实际 HTTP 请求；没有声称完成视觉交互验收。
- Windows Docker Desktop 在修复残留通信目录后曾恢复，随后重启再次出现相同错误；没有执行恢复出厂或删除用户镜像/卷。云端运行和后续 GitHub 构建不依赖它。

教程和运维命令见 [DEPLOY_CLOUD.md](DEPLOY_CLOUD.md)。
