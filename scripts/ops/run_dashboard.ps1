<#
.SYNOPSIS
Start the Streamlit dashboard for Media Quotes.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\ops\run_dashboard.ps1
#>
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '../..')

$Python = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }

if (-not $env:DASHBOARD_PASSWORD) {
    if (Test-Path ".env") {
        $envMatch = Get-Content ".env" | Select-String "^DASHBOARD_PASSWORD=(.+)"
        if (-not $envMatch -or -not $envMatch.Matches[0].Groups[1].Value.Trim()) {
            Write-Host "WARNING: DASHBOARD_PASSWORD is not set in environment or .env. The dashboard requires a password to log in." -ForegroundColor Yellow
        }
    } else {
        Write-Host "WARNING: DASHBOARD_PASSWORD is not set and .env not found. The dashboard requires a password to log in." -ForegroundColor Yellow
    }
}

Write-Host "Starting Media Quotes Streamlit Dashboard on 127.0.0.1:8501 ..." -ForegroundColor Cyan
& $Python -m streamlit run src/media_quotes/dashboard.py --server.address 127.0.0.1 @args
