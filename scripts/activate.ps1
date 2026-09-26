# Dot-source: . .\scripts\activate.ps1
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:UV_CACHE_DIR = Join-Path $projectRoot '.cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $projectRoot '.tools\python'
$env:GOPATH = Join-Path $projectRoot '.cache\gopath'
$env:GOMODCACHE = Join-Path $projectRoot '.cache\go-mod'
$env:GOCACHE = Join-Path $projectRoot '.cache\go-build'
$env:GOTOOLCHAIN = 'local'
$env:GOWORK = 'off'
$env:GOENV = 'off'
$env:CGO_ENABLED = '0'
$goBin = Join-Path $projectRoot '.tools\go\bin'
if (Test-Path -LiteralPath (Join-Path $goBin 'go.exe')) {
    $env:GOROOT = Join-Path $projectRoot '.tools\go'
    if (($env:Path -split ';') -notcontains $goBin) { $env:Path = "$goBin;$env:Path" }
}
$pythonActivate = Join-Path $projectRoot '.venv\Scripts\Activate.ps1'
if (Test-Path -LiteralPath $pythonActivate) { . $pythonActivate }
Write-Host "MASA project environment: $projectRoot"
