<#
.SYNOPSIS
Consolidated turnkey execution script for Media Quotes.

.DESCRIPTION
Executes the full verified sequence from clean state to final deliverables:
1. Docker Postgres verification / startup
2. Automated local CI gate (lint, types, tests, package build)
3. Database initialization & schema check
4. Offline fixture-backed collection & manual validation
5. Deterministic gold-set evaluations (synthetic-v1 & synthetic-v2)
6. Dagster asset materializations
7. Optional Streamlit dashboard launch

.PARAMETER Date
Report partition date to validate (default: 2026-11-19).

.PARAMETER SkipCI
Skip the full CI check (tests + lint + types) for rapid execution.

.PARAMETER LaunchDashboard
Start the Streamlit dashboard upon successful verification.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\ops\run_all.ps1

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\ops\run_all.ps1 -Date 2026-11-19 -LaunchDashboard
#>
param(
    [string]$Date = "2026-11-19",
    [switch]$SkipCI,
    [switch]$LaunchDashboard
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "../..")

$Python = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }
$Dagster = if (Test-Path ".\.venv\Scripts\dagster.exe") { ".\.venv\Scripts\dagster.exe" } else { "dagster" }

function Step {
    param([string]$Title, [scriptblock]$Action, [int[]]$AllowedExitCodes = @(0))
    Write-Host "`n>>> $Title" -ForegroundColor Cyan
    & $Action
    if ($AllowedExitCodes -notcontains $LASTEXITCODE) {
        throw "Step '$Title' failed with exit code $LASTEXITCODE"
    }
}

Write-Host "=== Media Quotes Turnkey Verification Pipeline ===" -ForegroundColor Green
Write-Host "Target Date: $Date | Root: $PWD`n"

# 1. Database
Step "Checking & Starting PostgreSQL Stack" {
    docker compose up -d --wait
}

# 2. CI Gate
if (-not $SkipCI) {
    Step "Running Local CI Quality Gate (lint, types, tests, build)" {
        powershell -ExecutionPolicy Bypass -File .\scripts\ci\check.ps1
    }
} else {
    Write-Host ">>> Skipping CI Gate (-SkipCI specified)" -ForegroundColor Yellow
}

# 3. Bootstrap Schema
Step "Bootstrapping Database Schema & Syncing Sources" {
    & $Python -m media_quotes bootstrap
}

# 4. Pipeline Execution & Audit (fixture replay; exit 2 means disclosed coverage gaps)
Step "Executing End-to-End Pipeline & Audit ($Date)" {
    & $Python -m media_quotes validate
    if ($LASTEXITCODE -ne 0) { throw "validate failed with exit code $LASTEXITCODE" }
    & $Python -m media_quotes run --date $Date
    if ($LASTEXITCODE -notin @(0, 2)) { throw "run failed with exit code $LASTEXITCODE" }
    & $Python -m media_quotes audit --date $Date
    if ($LASTEXITCODE -notin @(0, 2)) { throw "audit failed with exit code $LASTEXITCODE" }
}

# 5. Benchmark Evaluations
Step "Evaluating Synthetic Benchmark v1 (Date: 2026-11-19)" {
    & $Python -m media_quotes eval --gold-set synthetic-v1 --date 2026-11-19
}

Step "Evaluating Synthetic Benchmark v2 (Date: 2026-11-23)" {
    & $Python -m media_quotes eval --gold-set synthetic-v2 --date 2026-11-23
}

# 6. Dagster Materialization
Step "Materializing Dagster Production Assets" {
    & $Dagster asset materialize -m media_quotes.orchestration --select "daily_quotes_digest,gold_set_evaluation,retention_readiness_report" --partition $Date
}

Write-Host "`n=== ALL VERIFICATION STEPS PASSED SUCCESSFULLY ===" -ForegroundColor Green
Write-Host "Deliverables (fixture scope):"
Write-Host "  - Digest: var/output/digests/fixtures/$Date.md"
Write-Host "  - Quotes: var/output/quotes/fixtures/quotes-$Date.jsonl"
Write-Host "  - Audit:  var/output/audit/fixtures/audit-$Date.md"

if ($LaunchDashboard) {
    Write-Host "`nLaunching Governance Dashboard..." -ForegroundColor Cyan
    & $Python -m streamlit run src/media_quotes/dashboard.py --server.address 127.0.0.1
}
