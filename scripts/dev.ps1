$ErrorActionPreference = 'Stop'
# 同时启动后端 API 与前端开发服务器；Ctrl+C 结束前端后会一并停止后端。
# Start the API and the Vite dev server together; stopping the frontend also stops the API.
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$api = Start-Process -PassThru -NoNewWindow -FilePath $python -ArgumentList '-m', 'masa', 'serve', '--port', '8765'
Push-Location (Join-Path $projectRoot 'frontend')
try {
    if (-not (Test-Path node_modules)) { npm install }
    npm run dev
} finally {
    Pop-Location
    if (-not $api.HasExited) { Stop-Process -Id $api.Id -Force }
}
