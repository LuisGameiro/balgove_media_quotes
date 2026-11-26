<#
.SYNOPSIS
Operator-run, tombstone-gated blob prune. No tombstone is ever invented.

.DESCRIPTION
Requires an explicit, existing, non-empty tombstone manifest. Without -DryRun the
script passes --execute to `mq blob-prune`; with -DryRun it passes --dry-run
(plan only). Refuses to run when the manifest path is absent or the file is empty.

.PARAMETER Tombstones
Path to the tombstone manifest. Required; there is no default.

.PARAMETER DryRun
Plan only: pass --dry-run instead of --execute.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\ops\blob_prune_scheduled.ps1 -Tombstones .\var\output\tombstones.json

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\ops\blob_prune_scheduled.ps1 -Tombstones .\var\output\tombstones.json -DryRun
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Tombstones,

    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($Tombstones)) {
    throw 'BLOB PRUNE ABORTED: an explicit -Tombstones path is required; no tombstones are invented.'
}
if (-not (Test-Path -LiteralPath $Tombstones -PathType Leaf)) {
    throw "BLOB PRUNE ABORTED: tombstone file not found: $Tombstones"
}
$tombstonePath = (Resolve-Path -LiteralPath $Tombstones).Path
if ((Get-Item -LiteralPath $tombstonePath).Length -le 0) {
    throw "BLOB PRUNE ABORTED: tombstone file is empty: $Tombstones"
}

Set-Location (Join-Path $PSScriptRoot '../..')

$pruneArgs = @('blob-prune', '--tombstones', $tombstonePath)
if ($DryRun) { $pruneArgs += '--dry-run' } else { $pruneArgs += '--execute' }
$mode = if ($DryRun) { 'DRY RUN (no deletions)' } else { 'EXECUTE' }
Write-Host "blob-prune: $mode using $tombstonePath" -ForegroundColor Cyan

& mq @pruneArgs
if ($LASTEXITCODE -ne 0) {
    throw "mq blob-prune failed with exit code $LASTEXITCODE"
}
