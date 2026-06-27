# GD Skill Match — one-command launcher (Windows / PowerShell)
#   ./run.ps1                       run on the default version + instrument (drum)
#   ./run.ps1 -Fetch                re-download fresh data from gsv.fun first
#   ./run.ps1 -Instrument guitar    build/serve GuitarFreaks (gsv type:g)
#   ./run.ps1 -Port 9000 -Version galaxywave_delta
# The server serves every instrument it finds built under data/processed/<ver>/;
# build both (run once per -Instrument) to get the in-page DM<->GF switcher.
param(
  [int]$Port = 8770,
  [string]$Version = "galaxywave_delta",
  [ValidateSet("drum", "guitar")][string]$Instrument = "drum",
  [switch]$Fetch
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

$proc = Join-Path $PSScriptRoot "data\processed\$Version\$Instrument\matrix.npz"
$raw  = Join-Path $PSScriptRoot "data\raw\players_${Instrument}_$Version.jsonl"

if ($Fetch -or -not (Test-Path $raw)) {
  Write-Host "[run] fetching $Instrument data from gsv.fun (version=$Version) ..." -ForegroundColor Cyan
  python pipeline/fetch_data.py --version $Version --instrument $Instrument
}
if ($Fetch -or -not (Test-Path $proc)) {
  Write-Host "[run] building $Instrument dataset ..." -ForegroundColor Cyan
  python pipeline/build_dataset.py --version $Version --instrument $Instrument
}

Write-Host "[run] starting server on http://127.0.0.1:$Port" -ForegroundColor Green
Start-Process "http://127.0.0.1:$Port/"
python server/app.py --port $Port --version $Version
