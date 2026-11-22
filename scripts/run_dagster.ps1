<#
.SYNOPSIS
Start the Dagster development UI for the media-quotes daily digest and governance assets.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\run_dagster.ps1
#>
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

$Python = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }
$DagsterHome = Join-Path (Get-Location) "var\dagster"
New-Item -ItemType Directory -Force -Path $DagsterHome | Out-Null
$env:DAGSTER_HOME = $DagsterHome

Write-Host "Dagster assets: daily_quotes_digest, gold_set_evaluation, retention_readiness_report"
Write-Host "Asset checks: export_schema_valid, audit_gates_pass, coverage_gaps_disclosed, t0_rights_boundary_enforced, quote_rights_limits_respected, gold_set_thresholds_met"
Write-Host "Jobs: daily_quotes_digest_job, governance_audit_job"
Write-Host "Partitions: daily from 2026-10-01"
Write-Host "Schedule: daily_digest_schedule at 06:00 UTC (stopped by default)"
Write-Host "Sensor: review_queue_sla_sensor (300s interval, stopped by default)"
Write-Host "Persistence: set PERSIST_TO_POSTGRES=true before starting to write run rows/audit events"
Write-Host "Starting Dagster UI with $Python ..."

& $Python -m dagster dev -m media_quotes.orchestration
