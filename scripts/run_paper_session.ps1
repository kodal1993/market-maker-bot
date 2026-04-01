param(
    [int]$MaxLoops,

    [double]$LoopSeconds,

    [string]$TradesCsv,

    [string]$EquityCsv,

    [string]$ConsoleLog
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not $PSBoundParameters.ContainsKey("MaxLoops")) {
    $manifestPath = Join-Path $repoRoot "logs\sessions\active_paper_session.json"
    if (-not (Test-Path $manifestPath)) {
        throw "Session manifest not found: $manifestPath"
    }

    $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
    $MaxLoops = [int]$manifest.MaxLoops
    $LoopSeconds = [double]$manifest.LoopSeconds
    $TradesCsv = [string]$manifest.TradesCsv
    $EquityCsv = [string]$manifest.EquityCsv
    $ConsoleLog = [string]$manifest.ConsoleLog
}

$tradesDir = Split-Path -Parent $TradesCsv
$equityDir = Split-Path -Parent $EquityCsv
$consoleDir = Split-Path -Parent $ConsoleLog

foreach ($path in @($tradesDir, $equityDir, $consoleDir)) {
    if ($path) {
        New-Item -ItemType Directory -Force $path | Out-Null
    }
}

$env:BOT_MODE = "paper"
$env:MAX_LOOPS = [string]$MaxLoops
$env:LOOP_SECONDS = [string]$LoopSeconds
$env:TRADES_CSV = $TradesCsv
$env:EQUITY_CSV = $EquityCsv

& ".\.venv\Scripts\python.exe" "src\main.py" 2>&1 | Tee-Object -FilePath $ConsoleLog -Append
