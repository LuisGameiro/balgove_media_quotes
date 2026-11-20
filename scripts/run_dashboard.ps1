<#
.SYNOPSIS
Start the Streamlit offline/degraded-mode dashboard for Media Quotes.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\run_dashboard.ps1
#>
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

$Python = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }

Write-Host "Starting Media Quotes Streamlit Dashboard ..." -ForegroundColor Cyan
& $Python -m streamlit run src/media_quotes/dashboard.py
