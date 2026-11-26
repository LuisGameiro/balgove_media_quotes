# One-time installer: adds a `check` function to Windows PowerShell and PowerShell 7 profiles.
$function = @'
function check {
    param([string]$Path = ".")
    $here = Get-Location
    try {
        if ($Path -ne ".") { Set-Location $Path }
        $gate = Join-Path (Get-Location) "scripts\check.ps1"
        if (-not (Test-Path $gate)) { throw "check.ps1 not found at $gate" }
        & $gate
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
