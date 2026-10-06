# syntax=docker/dockerfile:1
# MASA / Verdict 的容器镜像：同一个进程托管前端和 API，默认是**只读演示模式**（不能发起任务、不接触 API 密钥）。
# Container image: one process serves the frontend and the API; **read-only demo mode by default** (cannot start tasks, never touches API keys).
#
#   docker build -t masa .
#   docker run --rm -p 8765:8765 masa                       # 打开 http://localhost:8765  / open http://localhost:8765
#   docker run --rm -p 8765:8765 -e MASA_DEMO=0 -v masa-data:/data masa   # 完整模式（见 docs/guides/DEPLOY_DOCKER.md）/ full mode

# ───────── 1) 前端：同源构建（VITE_API_BASE 为空 = API 与页面同源）/ frontend, same-origin build ─────────
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
ENV VITE_API_BASE=
RUN npm run build

# ───────── 2) Go 工具链 + runner / Go toolchain + the runner ─────────
# 与本机开发用的 Go 版本一致（生成项目的 go.mod 写的是 go 1.27.0）。 Same Go version as the dev machine (generated go.mod files say go 1.27.0).
FROM golang:1.27.1-bookworm AS gotool
WORKDIR /src
COPY runner/ ./
RUN CGO_ENABLED=0 go build -trimpath -o /out/masa-runner ./cmd/masa-runner

# ───────── 3) 运行镜像 / runtime image ─────────
FROM python:3.12-slim-bookworm
# Go 工具链：验证阶段要真的运行 go build/test/vet。 The Go toolchain: verification really runs go build/test/vet.
COPY --from=gotool /usr/local/go /usr/local/go
RUN useradd --system --create-home --uid 10001 masa \
    && mkdir -p /data /app/bin /app/web /home/masa/.cache/go-build \
    && chown -R masa:masa /data /home/masa
WORKDIR /app
COPY --from=gotool /out/masa-runner /app/bin/masa-runner
COPY --from=web /web/dist /app/web
COPY pyproject.toml ./
COPY src/ ./src/

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/masa \
    GOCACHE=/home/masa/.cache/go-build \
    PATH=/usr/local/go/bin:$PATH \
    MASA_BIND=0.0.0.0 \
    MASA_STATIC_DIR=/app/web \
    MASA_DEMO=1

USER masa
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request as u; r=u.urlopen(u.Request('http://127.0.0.1:8765/', headers={'Host': '127.0.0.1:8765'}), timeout=4); raise SystemExit(0 if r.status==200 else 1)"
CMD ["python", "-m", "masa", "--state-dir", "/data", "--runner", "/app/bin/masa-runner", "--go", "/usr/local/go/bin/go", "serve", "--port", "8765"]
