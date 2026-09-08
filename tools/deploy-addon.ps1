# Sync the addon source tree into the mod folder the game filepatches from.
#
# DayZ reads loose scripts from P:\<PBOPREFIX>\ when -filePatching is on, which is
# ..\DayZ_MCP\ -- a different tree from the one edited here. Nothing kept the two in
# step, so an in-game test could run sources days older than the working tree without
# saying so. This copies source -> deploy and reports what moved.
#
# Only files present in the source are written. Everything else in the destination is
# left alone: CLAUDE.md and the .bak_* copies of earlier manual deploys live there and
# are not ours to remove.
#
# No backup is taken. The source is under git in DayZ_MCP_dev, so any deployed file is
# recoverable from there -- but that is only true while the deploy tree is a copy. A
# destination newer than its source means someone edited the deploy tree directly, and
# copying over it would destroy work git never saw. That case stops the run and needs
# -Force.

[CmdletBinding()]
param(
    [string]$Source,
    [string]$Destination,
    [switch]$Check,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
if (-not $Source)      { $Source      = Join-Path $root 'addon' }
if (-not $Destination) { $Destination = Join-Path (Split-Path -Parent $root) 'DayZ_MCP' }

function Fail([string]$m) { Write-Host "[deploy-addon] $m" -ForegroundColor Red; exit 1 }
function Info([string]$m) { Write-Host "[deploy-addon] $m" }

if (-not (Test-Path -LiteralPath $Source -PathType Container))      { Fail "Source not found: $Source" }
if (-not (Test-Path -LiteralPath $Destination -PathType Container)) { Fail "Destination not found: $Destination" }
foreach ($t in @($Source, $Destination)) {
    if (-not (Test-Path -LiteralPath (Join-Path $t '$PBOPREFIX$') -PathType Leaf)) {
        Fail "No `$PBOPREFIX`$ in $t -- that file is what makes it an addon tree."
    }
}

$srcPrefix  = (Get-Content -LiteralPath (Join-Path $Source '$PBOPREFIX$') -Raw).Trim()
$dstPrefix  = (Get-Content -LiteralPath (Join-Path $Destination '$PBOPREFIX$') -Raw).Trim()
if ($srcPrefix -ne $dstPrefix) { Fail "Prefix mismatch: source '$srcPrefix' vs destination '$dstPrefix'." }

$files = Get-ChildItem -LiteralPath $Source -Recurse -File
if (-not $files) { Fail "Source has no files: $Source" }

$same = @(); $changed = @(); $new = @(); $conflicts = @()

foreach ($f in $files) {
    $rel = $f.FullName.Substring($Source.Length).TrimStart('\', '/')
    $dst = Join-Path $Destination $rel
    if (-not (Test-Path -LiteralPath $dst -PathType Leaf)) {
        $new += [pscustomobject]@{ Rel = $rel; Src = $f.FullName; Dst = $dst }
        continue
    }
    $a = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash
    $b = (Get-FileHash -LiteralPath $dst      -Algorithm SHA256).Hash
    if ($a -eq $b) { $same += $rel; continue }

    $entry = [pscustomobject]@{
        Rel = $rel; Src = $f.FullName; Dst = $dst
        SrcTime = $f.LastWriteTimeUtc
        DstTime = (Get-Item -LiteralPath $dst).LastWriteTimeUtc
    }
    # A deploy copy that is newer than its source was not produced by this script.
    if ($entry.DstTime -gt $entry.SrcTime) { $conflicts += $entry } else { $changed += $entry }
}

Info "source      : $Source"
Info "destination : $Destination"
Info "unchanged   : $($same.Count)"
Info "to update   : $($changed.Count)"
Info "to create   : $($new.Count)"
if ($conflicts.Count -gt 0) { Info "CONFLICTS   : $($conflicts.Count)" }

foreach ($e in $conflicts) {
    Write-Host ("  [conflict] {0}" -f $e.Rel) -ForegroundColor Yellow
    Write-Host ("             deploy copy is NEWER: {0:yyyy-MM-dd HH:mm:ss}Z vs source {1:yyyy-MM-dd HH:mm:ss}Z" -f $e.DstTime, $e.SrcTime)
}
foreach ($e in $changed) { Write-Host ("  [update]   {0}" -f $e.Rel) }
foreach ($e in $new)     { Write-Host ("  [create]   {0}" -f $e.Rel) }

if ($conflicts.Count -gt 0 -and -not $Force) {
    Fail "Refusing to overwrite $($conflicts.Count) file(s) edited in the deploy tree. Inspect them, move the change into $Source, then rerun. Use -Force only after deciding the deploy copy is disposable."
}

if ($Check) { Info 'Check only; nothing written.'; exit 0 }

$work = @($changed) + @($new) + @($conflicts | Where-Object { $Force })
if ($work.Count -eq 0) { Info 'Already in sync.'; exit 0 }

$failed = 0
foreach ($e in $work) {
    $dir = Split-Path -Parent $e.Dst
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Copy-Item -LiteralPath $e.Src -Destination $e.Dst -Force
    # OneDrive truncates silently; the copy is not done until it reads back identical.
    $a = (Get-FileHash -LiteralPath $e.Src -Algorithm SHA256).Hash
    $b = (Get-FileHash -LiteralPath $e.Dst -Algorithm SHA256).Hash
    if ($a -ne $b) { Write-Host ("  [FAILED]   {0}: hash mismatch after copy" -f $e.Rel) -ForegroundColor Red; $failed++ }
    else           { Write-Host ("  [ok]       {0}  {1}" -f $e.Rel, $a.Substring(0, 12)) }
}

if ($failed -gt 0) { Fail "$failed file(s) did not verify after copy." }
Info "Deployed $($work.Count) file(s). Relaunch the game to load them; -filePatching reads them at mission start."
exit 0
