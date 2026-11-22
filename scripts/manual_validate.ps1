<#
.SYNOPSIS
Manual quote-retrieval validation without Dagster.

.DESCRIPTION
Runs the same fixture-backed collection pipeline used by the Dagster asset:
validate -> doctor -> run -> audit. No Dagster daemon or UI is started.
Exit code 2 (EXIT_GAPS — partial success with T0 mandatory source coverage gaps)
is treated as expected in blocked/degraded mode.

.PARAMETER Date
Report date in YYYY-MM-DD format.

.PARAMETER DryRun
Plan only; do not write digest/export/log/audit artifacts.

.PARAMETER Persist
Set PERSIST_TO_POSTGRES=true for this PowerShell session before running.

.PARAMETER OfflineMarker
Pass the deprecated --offline flag for compatibility. Runtime collection is already
fixture/T0-gated; every mq command is offline without this flag.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\manual_validate.ps1 -Date 2026-11-19

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\manual_validate.ps1 -Date 2026-11-19 -DryRun

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\manual_validate.ps1 -Date 2026-11-19 -Persist
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Date,

    [switch]$DryRun,

    [switch]$Persist,

    [switch]$OfflineMarker
)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

$Python = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }

if ($Persist) {
    $env:PERSIST_TO_POSTGRES = 'true'
    Write-Host 'PERSIST_TO_POSTGRES=true for this session' -ForegroundColor Yellow
}

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Command,
        [int[]]$AllowedExitCodes = @(0)
    )
    Write-Host "== $Name ==" -ForegroundColor Cyan
    & $Command
    if ($AllowedExitCodes -notcontains $LASTEXITCODE) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

Invoke-Step 'validate' { & $Python -m media_quotes validate }
Invoke-Step 'doctor'   { & $Python -m media_quotes doctor }

$runArgs = @('-m', 'media_quotes', 'run', '--date', $Date)
if ($DryRun) { $runArgs += '--dry-run' }
if ($OfflineMarker) { $runArgs += '--offline' }
if ($DryRun -or $OfflineMarker) { $runArgs += '--verbose' }
Invoke-Step 'run' -AllowedExitCodes @(0, 2) -Command { & $Python @runArgs }

if (-not $DryRun) {
    Invoke-Step 'audit' -AllowedExitCodes @(0, 2) -Command { & $Python -m media_quotes audit --date $Date }
    Write-Host 'MANUAL VALIDATION COMPLETE' -ForegroundColor Green
}
else {
    Write-Host 'MANUAL DRY RUN COMPLETE (no audit expected)' -ForegroundColor Green
}
