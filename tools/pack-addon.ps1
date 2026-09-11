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

# A running DayZ keeps the destination PBO mapped, so the copy at the end of the build
# fails. Cheap to say so before spending a minute binarizing for nothing.
$live = @(Get-Process DayZDiag_x64, DayZ_x64, DayZServer_x64 -ErrorAction SilentlyContinue)
if ($live.Count -gt 0) {
  Write-Warning ("$($live.Count) DayZ process(es) running (pids: " + (($live | ForEach-Object { $_.Id }) -join ', ') +
                 "). If any has $ModName loaded, the destination PBO is locked and the copy will fail. " +
                 "The gate at the end of this script will say so.")
}

Write-Host "AddonBuilder: $builder"
Write-Host "  source     : $Source"
Write-Host "  destination: $Destination"
# AddonBuilder writes progress to stderr ("Setting breakpad minidump AppID = ..."), and
# under $ErrorActionPreference='Stop' PowerShell 5.1 turns any native stderr line into a
# terminating NativeCommandError -- aborting a build that is in fact fine. Only the exit
# code decides here.
# Snapshot before the build: on success AddonBuilder MOVES its temp PBO to the
# destination, leaving nothing to compare against afterwards. The write time of
# what is there now is the only "before" this script will get.
$pboPath = Join-Path $Destination "$ModName.pbo"
$stampBefore = try { (Get-Item -LiteralPath $pboPath -ErrorAction Stop).LastWriteTimeUtc } catch { [datetime]::MinValue }

$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try { & $builder @args } finally { $ErrorActionPreference = $previousPreference }
if ($LASTEXITCODE -ne 0) { throw "AddonBuilder failed with exit code $LASTEXITCODE" }

$pbo = Join-Path $Destination "$ModName.pbo"
if (-not (Test-Path -LiteralPath $pbo)) { throw "AddonBuilder reported success but $pbo is missing" }

# Existence is not delivery. AddonBuilder can pack correctly and still fail its own final
# copy -- it prints "[ERROR]: Build failed" after "Copying PBO" and exits 0 anyway -- which
# leaves a stale PBO in place that passes any existence check. Measured 2026-09-03..09-08:
# five days of in-game tests ran the 09-03 build while the tree moved underneath.
# The build AddonBuilder actually produced is the authority; compare by hash.
function Get-PboHashOrNull([string]$Path) {
  # A destination held by a running DayZ can refuse even a read. For this gate
  # "cannot read it" and "it differs" are the same verdict: not proven to be the
  # build. Returning $null keeps both on the one path that explains itself.
  try { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash }
  catch { return $null }
}

$built = Join-Path $temp "$ModName.pbo"
if (Test-Path -LiteralPath $built) {
  # The temp PBO survived, which means AddonBuilder could not complete its own copy.
  $builtHash = (Get-FileHash -LiteralPath $built -Algorithm SHA256).Hash
  if ((Get-PboHashOrNull $pbo) -ne $builtHash) {
    Write-Warning "Destination PBO is not proven to be the build that just ran; retrying the copy AddonBuilder could not finish."
    $why = $null
    try { Copy-Item -LiteralPath $built -Destination $pbo -Force -ErrorAction Stop }
    catch { $why = $_.Exception.Message }
    if (-not $why -and (Get-PboHashOrNull $pbo) -ne $builtHash) {
      $why = "the copy reported success but the destination still does not match the build"
    }
    if ($why) {
      $stamp = try { (Get-Item -LiteralPath $pbo -ErrorAction Stop).LastWriteTime } catch { "unreadable" }
      throw ("The PBO was BUILT but never reached the game -- the game keeps loading the old one, " +
             "and every in-game test measures that.`n" +
             "  built      : $built`n" +
             "  destination: $pbo (still $stamp)`n" +
             "  reason     : $why`n" +
             "Close every DayZ that has $ModName loaded, then copy the built file over the destination.")
    }
    Write-Host "  recovered  : copied the build over the destination"
  }
  Write-Host "  verified   : destination matches the build ($($builtHash.Substring(0, 16)))"
} else {
  # AddonBuilder moved its temp PBO, so the only evidence left is that the destination
  # was actually written. An unchanged write time means it was not.
  $stampAfter = (Get-Item -LiteralPath $pbo).LastWriteTimeUtc
  if ($stampAfter -le $stampBefore) {
    throw ("The destination PBO was not written by this build -- the game keeps loading the old one, " +
           "and every in-game test measures that.`n" +
           "  destination: $pbo`n" +
           "  write time : $stampAfter UTC, unchanged since before the build`n" +
           "Close every DayZ that has $ModName loaded and run this again.")
  }
  Write-Host ("  verified   : destination written by this build ($($stampAfter.ToString('yyyy-MM-dd HH:mm:ss')) UTC, " +
              "$((Get-FileHash -LiteralPath $pbo -Algorithm SHA256).Hash.Substring(0, 16)))")
}

"{0}  ({1:N0} bytes)" -f $pbo, (Get-Item -LiteralPath $pbo).Length
