$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'activate.ps1')
Push-Location $projectRoot
try {
    uv sync --locked
    if ($LASTEXITCODE -ne 0) { throw 'uv sync failed; run uv lock after dependency changes.' }
    $binDir = Join-Path $projectRoot '.tools\bin'
    New-Item -ItemType Directory -Path $binDir -Force | Out-Null
    Push-Location (Join-Path $projectRoot 'runner')
    try {
        go build -trimpath -o (Join-Path $binDir 'masa-runner.exe') ./cmd/masa-runner
        if ($LASTEXITCODE -ne 0) { throw 'Go build failed.' }
    } finally { Pop-Location }
} finally { Pop-Location }
