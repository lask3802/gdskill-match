# GD Skill Match — one-command launcher (Windows / PowerShell)
#   ./run.ps1            run on the default version, open the browser
#   ./run.ps1 -Fetch     re-download fresh data from gsv.fun first
#   ./run.ps1 -Port 9000 -Version galaxywave_delta
param(
  [int]$Port = 8770,
  [string]$Version = "galaxywave_delta",
  [switch]$Fetch
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

$proc = Join-Path $PSScriptRoot "data\processed\$Version\matrix.npz"
$raw  = Join-Path $PSScriptRoot "data\raw\players_drum_$Version.jsonl"

if ($Fetch -or -not (Test-Path $raw)) {
  Write-Host "[run] fetching data from gsv.fun (version=$Version) ..." -ForegroundColor Cyan
  python pipeline/fetch_data.py --version $Version
}
if ($Fetch -or -not (Test-Path $proc)) {
  Write-Host "[run] building dataset ..." -ForegroundColor Cyan
  python pipeline/build_dataset.py --version $Version
}

Write-Host "[run] starting server on http://127.0.0.1:$Port" -ForegroundColor Green
Start-Process "http://127.0.0.1:$Port/"
python server/app.py --port $Port --version $Version
