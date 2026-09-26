[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'activate.ps1')
$toolsDir = Join-Path $projectRoot '.tools'
New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
$goExe = Join-Path $toolsDir 'go\bin\go.exe'
$lockPath = Join-Path $PSScriptRoot 'go-toolchain.json'
if (-not (Test-Path -LiteralPath $goExe)) {
    if (Test-Path -LiteralPath $lockPath) {
        $release = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
    } else {
        $releases = Invoke-RestMethod -Uri 'https://go.dev/dl/?mode=json' -TimeoutSec 60
        $stable = $releases | Where-Object { $_.stable } | Select-Object -First 1
        $release = $stable.files | Where-Object { $_.os -eq 'windows' -and $_.arch -eq 'amd64' -and $_.kind -eq 'archive' } | Select-Object -First 1
        if (-not $release) { throw 'No stable Windows amd64 Go archive found.' }
        $release | ConvertTo-Json | Set-Content -LiteralPath $lockPath -Encoding UTF8
    }
    $archive = Join-Path $toolsDir $release.filename
    if (-not (Test-Path -LiteralPath $archive)) {
        Invoke-WebRequest -Uri "https://go.dev/dl/$($release.filename)" -OutFile $archive -UseBasicParsing -TimeoutSec 300
    }
    $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    if ($actualHash -ne $release.sha256) { throw "Go archive checksum mismatch: $archive" }
    Expand-Archive -LiteralPath $archive -DestinationPath $toolsDir -Force
}
. (Join-Path $PSScriptRoot 'activate.ps1')
& $goExe version
if ($LASTEXITCODE -ne 0) { throw 'Go verification failed.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.venv\Scripts\python.exe'))) {
    uv venv --python 3.12 (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python venv creation failed.' }
}
. (Join-Path $PSScriptRoot 'activate.ps1')
python --version
if ($LASTEXITCODE -ne 0) { throw 'Python verification failed.' }
