<#
.SYNOPSIS
Windows-native equivalent of `make check` (docs/02-architecture.md).

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

$Python = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }

function Invoke-Step {
    param([string]$Name, [scriptblock]$Command)
    Write-Host "== $Name ==" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
}

Invoke-Step 'docs QA'       { & $Python scripts/docs_qa.py }
Invoke-Step 'format check'  { & $Python -m ruff format --check . }
Invoke-Step 'lint'          { & $Python -m ruff check . }
Invoke-Step 'typecheck'     { & $Python -m mypy --strict src/ }
Invoke-Step 'tests'         { & $Python -m pytest --cov=media_quotes --cov-fail-under=85 }
Invoke-Step 'build'         { & $Python -m build }
Invoke-Step 'CLI smoke'     {
    & $Python -m media_quotes --help | Out-Null
    & $Python -m media_quotes.cli --help | Out-Null
}

Write-Host 'ALL CHECKS PASSED' -ForegroundColor Green
