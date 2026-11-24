# One-time installer: adds a `check` function to Windows PowerShell and PowerShell 7 profiles.
$function = @'
function check {
    param([string]$Path = ".")
    $here = Get-Location
    try {
        if ($Path -ne ".") { Set-Location $Path }
        Write-Host "== docs QA ==" -ForegroundColor Cyan
        python scripts/docs_qa.py; if ($LASTEXITCODE -ne 0) { throw "docs QA failed" }
        Write-Host "== format ==" -ForegroundColor Cyan
        python -m ruff format --check .; if ($LASTEXITCODE -ne 0) { throw "format failed" }
        Write-Host "== lint ==" -ForegroundColor Cyan
        python -m ruff check .; if ($LASTEXITCODE -ne 0) { throw "lint failed" }
        Write-Host "== typecheck ==" -ForegroundColor Cyan
        python -m mypy --strict src/; if ($LASTEXITCODE -ne 0) { throw "typecheck failed" }
        Write-Host "== tests ==" -ForegroundColor Cyan
        python -m pytest --cov=media_quotes --cov-fail-under=85; if ($LASTEXITCODE -ne 0) { throw "tests failed" }
        Write-Host "== build ==" -ForegroundColor Cyan
        python -m build; if ($LASTEXITCODE -ne 0) { throw "build failed" }
        Write-Host "== smoke ==" -ForegroundColor Cyan
        python -m media_quotes --help | Out-Null
        python -m media_quotes.cli --help | Out-Null
        Write-Host "ALL CHECKS PASSED" -ForegroundColor Green
    }
    finally { Set-Location $here }
}
'@

$docs = [Environment]::GetFolderPath('MyDocuments')
$profiles = @(
    (Join-Path $docs 'WindowsPowerShell\Microsoft.PowerShell_profile.ps1'),
    (Join-Path $docs 'PowerShell\profile.ps1')
)
if ($PROFILE -and -not ($profiles -contains $PROFILE)) {
    $profiles += $PROFILE
}

foreach ($profile in $profiles) {
    $dir = Split-Path -Path $profile
    if (-not (Test-Path -Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    if (Test-Path -Path $profile) {
        $existing = Get-Content -Path $profile -Raw -ErrorAction SilentlyContinue
        if ($existing -and $existing.Contains('function check {')) {
            Write-Output "skipped (already has check): $profile"
            continue
        }
        Add-Content -Path $profile -Value "`n$function" -Encoding UTF8
    } else {
        Set-Content -Path $profile -Value $function -Encoding UTF8
    }
    Write-Output "installed check function: $profile"
}
