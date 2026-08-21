#Requires -Version 5.1
# Pack addon/ into <DayZ>\!Workshop\@<ModName>\Addons\<ModName>.pbo with AddonBuilder.
#
# AddonBuilder resolves the source through the P:\ work drive, not through the path you
# type: DayZ Tools require it and $PBOPREFIX$ is interpreted relative to it. So -Source
# must be reachable as P:\<something>. Substituting the drive is the caller's job; this
# script only refuses clearly to run without it, naming what it looked for.
#
# Launched with no arguments AddonBuilder opens its GUI and never returns, so every
# invocation here passes source and destination positionally.

param(
  [string]$ModName = "DayZ_MCP",
  [string]$Source = "",
  [string]$Destination = "",
  [string]$ToolsPath = "",
  [switch]$Clear
)

$ErrorActionPreference = "Stop"

function Resolve-AddonBuilder {
  param([string]$Explicit)
  $roots = @()
  if ($Explicit) { $roots += $Explicit }
  if ($env:DAYZ_TOOLS_PATH) { $roots += $env:DAYZ_TOOLS_PATH }
  $roots += "C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools"
  $tried = @()
  foreach ($root in $roots) {
    $candidate = Join-Path $root "Bin\AddonBuilder\AddonBuilder.exe"
    $tried += $candidate
    if (Test-Path -LiteralPath $candidate) { return $candidate }
  }
  throw ("AddonBuilder.exe not found. Tried, in order:`n  " + ($tried -join "`n  ") +
         "`nSet DAYZ_TOOLS_PATH to your DayZ Tools folder, or pass -ToolsPath.")
}

if ($ModName -notmatch '^[A-Za-z][A-Za-z0-9_]{0,63}$') {
  # The name doubles as a C-style identifier in CfgPatches, so a hyphen does not parse.
  throw "ModName must match ^[A-Za-z][A-Za-z0-9_]{0,63}$ (no hyphens): '$ModName'"
}

if (-not $Source) { $Source = "P:\$ModName" }
if (-not $Destination) {
  $workshop = "C:\Program Files (x86)\Steam\steamapps\common\DayZ\!Workshop"
  if ($env:DAYZ_PATH) { $workshop = Join-Path $env:DAYZ_PATH "!Workshop" }
  $Destination = Join-Path $workshop "@$ModName\Addons"
}

if (-not (Test-Path -LiteralPath $Source)) {
  throw "Source not found: $Source`nAddonBuilder reads through the P:\ work drive; see README."
}
$prefixFile = Join-Path $Source '$PBOPREFIX$'
if (-not (Test-Path -LiteralPath $prefixFile)) {
  throw "No `$PBOPREFIX`$ in $Source -- that file is what makes it an addon source tree."
}

$builder = Resolve-AddonBuilder -Explicit $ToolsPath
$temp = Join-Path $env:TEMP "dayz-pack-$ModName"
if (-not (Test-Path -LiteralPath $Destination)) {
  New-Item -ItemType Directory -Force -Path $Destination | Out-Null
}

# An include list is the only way to keep edit-mechanism leftovers out of the PBO.
# Without it AddonBuilder packs the whole folder: measured 2026-08-21, three
# `.bak_*` copies of the bridge (~220 kB of stale source) shipped inside the pbo.
$includeList = Join-Path $Source "include.lst"
$args = @($Source, $Destination, "-prefix=$ModName", "-temp=$temp", "-binarizeFullLogs")
if (Test-Path -LiteralPath $includeList) {
  $args += "-include=$includeList"
} else {
  Write-Warning "No include.lst in $Source -- AddonBuilder will pack every file in the tree, backups included."
}
if ($Clear) { $args += "-clear" }

Write-Host "AddonBuilder: $builder"
Write-Host "  source     : $Source"
Write-Host "  destination: $Destination"
# AddonBuilder writes progress to stderr ("Setting breakpad minidump AppID = ..."), and
# under $ErrorActionPreference='Stop' PowerShell 5.1 turns any native stderr line into a
# terminating NativeCommandError -- aborting a build that is in fact fine. Only the exit
# code decides here.
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try { & $builder @args } finally { $ErrorActionPreference = $previousPreference }
if ($LASTEXITCODE -ne 0) { throw "AddonBuilder failed with exit code $LASTEXITCODE" }

$pbo = Join-Path $Destination "$ModName.pbo"
if (-not (Test-Path -LiteralPath $pbo)) { throw "AddonBuilder reported success but $pbo is missing" }
"{0}  ({1:N0} bytes)" -f $pbo, (Get-Item -LiteralPath $pbo).Length
