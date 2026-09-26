$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'build.ps1')
. (Join-Path $PSScriptRoot 'activate.ps1')
Push-Location $projectRoot
try {
    Push-Location (Join-Path $projectRoot 'runner')
    try {
        go test -count=1 ./...
        if ($LASTEXITCODE -ne 0) { throw 'Go tests failed.' }
        go vet ./...
        if ($LASTEXITCODE -ne 0) { throw 'Go vet failed.' }
    } finally { Pop-Location }
    python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Python tests failed.' }
} finally { Pop-Location }
