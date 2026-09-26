$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'activate.ps1')
go version
if ($LASTEXITCODE -ne 0) { throw 'Go not available.' }
python -c "import sys, sqlite3, asyncio; assert sys.prefix != sys.base_prefix; c = sqlite3.connect(':memory:'); assert c.execute('select 1').fetchone()[0] == 1; print('Python venv + SQLite + asyncio OK:', sys.version.split()[0]); print('Interpreter:', sys.executable)"
if ($LASTEXITCODE -ne 0) { throw 'Python smoke test failed.' }
$smokeDir = Join-Path $projectRoot '.cache\env-smoke'
New-Item -ItemType Directory -Path $smokeDir -Force | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $smokeDir 'go.mod'), "module masa.local/env-smoke`n`ngo 1.22.0`n", $utf8)
$source = @'
package smoke

import (
    "context"
    "encoding/json"
    "testing"
    "time"
)

func TestEnvironment(t *testing.T) {
    var request struct { ID string `json:"id"` }
    if err := json.Unmarshal([]byte(`{"id":"smoke"}`), &request); err != nil || request.ID != "smoke" {
        t.Fatal("JSON smoke test failed", err)
    }
    ctx, cancel := context.WithTimeout(context.Background(), time.Millisecond)
    defer cancel()
    <-ctx.Done()
    if ctx.Err() != context.DeadlineExceeded { t.Fatal(ctx.Err()) }
}
'@
[IO.File]::WriteAllText((Join-Path $smokeDir 'environment_test.go'), $source, $utf8)
Push-Location $smokeDir
try {
    go test -count=1 -v ./...
    if ($LASTEXITCODE -ne 0) { throw 'Go compilation/test failed.' }
    go vet ./...
    if ($LASTEXITCODE -ne 0) { throw 'Go vet failed.' }
} finally { Pop-Location }
Write-Host 'Environment verification passed. This checks toolchains, not the future MASA runtime.'
