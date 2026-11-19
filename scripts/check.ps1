<#
.SYNOPSIS
Windows-native equivalent of `make check` (docs/02 §7; CHK-10..13).

.DESCRIPTION
Runs the full local/CI gate from the repository root: documentation QA, format check,
lint, strict type-check, tests with 85% coverage, package build and CLI smoke tests.
Requires the activated project virtualenv (or uv) with dev/test extras installed.

.EXAMPLE
.\scripts\check.ps1
.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\check.ps1
#>
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

function Invoke-Step {
    param([string]$Name, [scriptblock]$Command)
    Write-Host "== $Name ==" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
}

Invoke-Step 'docs QA'       { python scripts/docs_qa.py }
Invoke-Step 'format check'  { python -m ruff format --check . }
Invoke-Step 'lint'          { python -m ruff check . }
Invoke-Step 'typecheck'     { python -m mypy --strict src/ }
Invoke-Step 'tests'         { python -m pytest --cov=media_quotes --cov-fail-under=85 }
Invoke-Step 'build'         { python -m build }
Invoke-Step 'CLI smoke'     {
    python -m media_quotes --help | Out-Null
    python -m media_quotes.cli --help | Out-Null
}

Write-Host 'ALL CHECKS PASSED' -ForegroundColor Green
