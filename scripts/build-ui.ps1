$ErrorActionPreference = 'Stop'
# 前端独立构建（类型检查 + 单元测试 + 产物在 frontend/dist）；后端不再托管前端。
# Standalone frontend build: typecheck, unit tests, bundle to frontend/dist. The backend no longer serves it.
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $projectRoot 'frontend')
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency install failed.' }
    npm test
    if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed.' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
