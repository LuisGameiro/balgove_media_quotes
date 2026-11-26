<#
.SYNOPSIS
Start the existing Dagster schedules/sensor requested by the ENABLE_* env flags.
Those flags only set default status when definitions load; they do not start anything
that already exists.

.EXAMPLE
$env:ENABLE_TIER1_SCHEDULES = 'true'
powershell -ExecutionPolicy Bypass -File .\scripts\ops\enable_automation.ps1 -EnableSchedules
#>
[CmdletBinding()]
param(
    [switch] $EnableSchedules,
    [switch] $EnableSensor
)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '../..')

$Python = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }
$DagsterHome = Join-Path (Get-Location) "var\dagster"
New-Item -ItemType Directory -Force -Path $DagsterHome | Out-Null
$env:DAGSTER_HOME = $DagsterHome

if (-not $EnableSchedules -and -not $EnableSensor) {
    Write-Error "Pass -EnableSchedules and/or -EnableSensor to start existing Dagster automations."
}
$schedulesRequested = $EnableSchedules -and $env:ENABLE_TIER1_SCHEDULES -eq 'true'
$sensorRequested = $EnableSensor -and $env:ENABLE_REVIEW_SLA_SENSOR -eq 'true'
if (-not $schedulesRequested -and -not $sensorRequested) {
    Write-Error "Neither matching ENABLE_* env flag is 'true'; refusing to start anything."
}

if ($schedulesRequested) {
    & $Python -m dagster schedule start daily_digest_schedule -m media_quotes.orchestration
    if ($LASTEXITCODE -ne 0) { throw "dagster schedule start daily_digest_schedule failed with exit code $LASTEXITCODE" }
    & $Python -m dagster schedule start tier1_six_hourly_schedule -m media_quotes.orchestration
    if ($LASTEXITCODE -ne 0) { throw "dagster schedule start tier1_six_hourly_schedule failed with exit code $LASTEXITCODE" }
    Write-Host "Started existing schedules daily_digest_schedule and tier1_six_hourly_schedule."
}
if ($sensorRequested) {
    & $Python -m dagster sensor start review_queue_sla_sensor -m media_quotes.orchestration
    if ($LASTEXITCODE -ne 0) { throw "dagster sensor start review_queue_sla_sensor failed with exit code $LASTEXITCODE" }
    Write-Host "Started existing sensor review_queue_sla_sensor."
}
