#Requires -Version 5.1
# Swap a mod's live PBO for a given build, only when both hashes are the expected
# ones and no DayZ process runs.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\dev\swap_pbo.ps1 `
#     -Build <built .pbo> -WantNew <SHA-256 of the build> -WantOld <SHA-256 of the live PBO> `
#     [-Backup <rollback copy> -WantBackup <its SHA-256>] [-ModName DayZ_MCP] [-Destination <Addons folder>]
#
# The live PBO is <Destination>\<ModName>.pbo. Destination defaults to the folder
# tools/pack-addon.ps1 builds into: <DAYZ_PATH or the Steam default>\!Workshop\@<ModName>\Addons.
#
# It refuses, before writing anything, when:
#   - a hash is not a full SHA-256 (64 hex digits);
#   - the build's SHA-256 is not -WantNew;
#   - -Backup and -WantBackup do not come together, the backup is the live PBO itself,
#     or the backup's SHA-256 is not -WantBackup (the rollback copy is not intact);
#   - the live PBO's SHA-256 is not -WantOld (it changed since you looked: another
#     session may have swapped in its own build);
#   - any DayZ game process runs (DayZDiag_x64, DayZServer_x64, DayZ_x64, DayZ_BE: the
#     images dayz_mcp/orphan_guard.py calls DayZ). A game with the mod loaded keeps the
#     PBO mapped. This check runs last, right before the copy. DayZ Tools processes
#     (DayZToolsLauncher, AddonBuilder) do not load the mod and do not block the swap.
# After the copy the live PBO must hash to -WantNew, or the script fails and names
# the rollback.
#
# A built PBO says which commit built it (mcp_build.json, written by
# tools/pack-addon.ps1). Check it with tools/dev/pbo_provenance.py before swapping.

param(
  [Parameter(Mandatory = $true)][string]$Build,
  [Parameter(Mandatory = $true)][string]$WantNew,
  [Parameter(Mandatory = $true)][string]$WantOld,
  [string]$Backup = "",
  [string]$WantBackup = "",
  [string]$ModName = "DayZ_MCP",
  [string]$Destination = ""
)

$ErrorActionPreference = "Stop"

# Windows PowerShell 5.1 started from Git Bash inherits a Unix PSModulePath, and
# module cmdlets such as Get-FileHash are then not recognized. Same reset as
# tools/pack-addon.ps1.
if ($PSVersionTable.PSEdition -eq 'Desktop') {
  $machineModules = [Environment]::GetEnvironmentVariable('PSModulePath', 'Machine')
  if ($machineModules) {
    $env:PSModulePath = $machineModules
  }
}

function Assert-Sha256Text([string]$Name, [string]$Value) {
  if ($Value -notmatch '^[0-9A-Fa-f]{64}$') {
    throw "-$Name must be a full SHA-256 (64 hex digits), not '$Value'"
  }
}

function Get-Sha256OrNull([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

if ($ModName -notmatch '^[A-Za-z][A-Za-z0-9_]{0,63}$') {
  throw "ModName must match ^[A-Za-z][A-Za-z0-9_]{0,63}$ (no hyphens): '$ModName'"
}
Assert-Sha256Text 'WantNew' $WantNew
Assert-Sha256Text 'WantOld' $WantOld
$wantNewUpper = $WantNew.ToUpperInvariant()
$wantOldUpper = $WantOld.ToUpperInvariant()
$hasBackup = -not [string]::IsNullOrWhiteSpace($Backup)
if ($hasBackup -ne (-not [string]::IsNullOrWhiteSpace($WantBackup))) {
  throw "-Backup and -WantBackup go together: pass both, or neither."
}
if ($hasBackup) {
  Assert-Sha256Text 'WantBackup' $WantBackup
}

if (-not $Destination) {
  $workshop = "C:\Program Files (x86)\Steam\steamapps\common\DayZ\!Workshop"
  if ($env:DAYZ_PATH) { $workshop = Join-Path $env:DAYZ_PATH "!Workshop" }
  $Destination = Join-Path $workshop "@$ModName\Addons"
}
$live = Join-Path $Destination "$ModName.pbo"

$buildHash = Get-Sha256OrNull $Build
if ($null -eq $buildHash) { throw "Build not found: $Build" }
if ($buildHash -ne $wantNewUpper) {
  throw "The build is not the expected one: $Build is $buildHash, -WantNew is $wantNewUpper."
}

if ($hasBackup) {
  # Provider paths resolve a relative path against $PWD, as the cmdlets below do.
  $backupFull = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Backup)
  $liveFull = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($live)
  if ($backupFull.Equals($liveFull, [StringComparison]::OrdinalIgnoreCase)) {
    throw "The backup is the live PBO itself ($live); the swap would overwrite the rollback."
  }
  $backupHash = Get-Sha256OrNull $Backup
  if ($null -eq $backupHash) { throw "Backup not found: $Backup" }
  if ($backupHash -ne $WantBackup.ToUpperInvariant()) {
    throw "The backup is not intact: $Backup is $backupHash, -WantBackup is $($WantBackup.ToUpperInvariant())."
  }
}

$before = Get-Sha256OrNull $live
if ($null -eq $before) { throw "No live PBO to swap: $live" }
"live before: $before"
if ($before -ne $wantOldUpper) {
  throw "The live PBO is not the expected one: $live is $before, -WantOld is $wantOldUpper."
}

$dayz = @(Get-Process -Name DayZDiag_x64, DayZServer_x64, DayZ_x64, DayZ_BE -ErrorAction SilentlyContinue)
if ($dayz.Count -gt 0) {
  throw ("DayZ is running: " + (($dayz | ForEach-Object { "$($_.Name):$($_.Id)" }) -join ', ') +
         ". Close every DayZ process, then run this again.")
}

Copy-Item -LiteralPath $Build -Destination $live -Force
$after = Get-Sha256OrNull $live
"live after : $after  $((Get-Item -LiteralPath $live).Length) B"
if ($after -ne $wantNewUpper) {
  if ($hasBackup) { $rollback = "restore $Backup" } else { $rollback = "restore the PBO whose SHA-256 is $before" }
  throw "Swap verification failed: $live is $after, not $wantNewUpper; $rollback."
}
"PBO SWAPPED"
