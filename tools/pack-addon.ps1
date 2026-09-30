#Requires -Version 5.1
# Pack addon/ into <DayZ>\!Workshop\@<ModName>\Addons\<ModName>.pbo with AddonBuilder.
#
# The tree packed is a stage of git-tracked addon/ at -Ref (default), or of
# -Source when that switch is passed -- not the live worktree. AddonBuilder
# resolves the source through the P:\ work drive, not through the path you
# type: DayZ Tools require it and $PBOPREFIX$ is interpreted relative to it.
# So the stage folder must be reachable as P:\<something> unless -StageOnly.
# Substituting the drive is the caller's job; this script only refuses
# clearly to run without it, naming what it looked for.
#
# Launched with no arguments AddonBuilder opens its GUI and never returns, so every
# invocation here passes source and destination positionally.
#
# Before packing, the stage gets one file that is not in git, mcp_build.json in its
# root: the commit, the addon/ tree id and the build time in UTC. The PBO carries it,
# and tools/dev/pbo_provenance.py checks a built PBO against it.

param(
  [string]$ModName = "DayZ_MCP",
  [string]$Source = "",
  [string]$Destination = "",
  [string]$ToolsPath = "",
  [string]$Ref = "HEAD",
  [string]$StageRoot = 'P:\temp\dayz-pack-stage',
  [switch]$Clear,
  [switch]$PackOnly,
  [switch]$StageOnly
)

$ErrorActionPreference = "Stop"

# Windows PowerShell 5.1 started from Git Bash inherits a Unix PSModulePath,
# and module cmdlets such as Get-FileHash are then not recognized. Reset to
# the machine path before any module cmdlet runs.
if ($PSVersionTable.PSEdition -eq 'Desktop') {
  $machineModules = [Environment]::GetEnvironmentVariable('PSModulePath', 'Machine')
  if ($machineModules) {
    $env:PSModulePath = $machineModules
  }
}

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

function Get-RelativePosix([string]$Root, [string]$FullName) {
  $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
  $full = [IO.Path]::GetFullPath($FullName)
  if (-not $full.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Path $full is not under $rootFull"
  }
  $rel = $full.Substring($rootFull.Length).TrimStart('\', '/')
  return ($rel -replace '\\', '/')
}

function Get-FileSha256Upper([string]$Path) {
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    $stream = [IO.File]::OpenRead($Path)
    try {
      $hash = $sha.ComputeHash($stream)
    } finally {
      $stream.Dispose()
    }
    return ([BitConverter]::ToString($hash) -replace '-', '').ToUpperInvariant()
  } finally {
    $sha.Dispose()
  }
}

function Test-IsExcludedFileName([string]$Name) {
  if ($Name.StartsWith('.bak_', [StringComparison]::OrdinalIgnoreCase)) { return $true }
  if ($Name.IndexOf('_bak_', [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
  if ($Name.IndexOf('.bak_', [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
  if ($Name.EndsWith('.md', [StringComparison]::OrdinalIgnoreCase)) { return $true }
  return $false
}

function ConvertTo-JsonArray {
  param($Items)
  $parts = New-Object System.Collections.Generic.List[string]
  if ($null -ne $Items) {
    foreach ($item in $Items) {
      $parts.Add((ConvertTo-Json -InputObject $item -Compress -Depth 5))
    }
  }
  if ($parts.Count -eq 0) { return '[]' }
  return ('[' + [string]::Join(',', $parts.ToArray()) + ']')
}

function ConvertTo-JsonValue {
  param($Value)
  if ($null -eq $Value) { return 'null' }
  return (ConvertTo-Json -InputObject $Value -Compress -Depth 5)
}

function Get-StagedFileRecords([string]$Root) {
  $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
  $items = New-Object System.Collections.Generic.List[object]
  $stack = New-Object System.Collections.Generic.Stack[System.IO.DirectoryInfo]
  $stack.Push((New-Object IO.DirectoryInfo $rootFull))
  while ($stack.Count -gt 0) {
    $dir = $stack.Pop()
    foreach ($info in $dir.EnumerateFileSystemInfos()) {
      if (($info.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
      if ($info -is [IO.DirectoryInfo]) {
        $stack.Push($info)
        continue
      }
      if ($info -is [IO.FileInfo]) {
        $rel = Get-RelativePosix -Root $rootFull -FullName $info.FullName
        $hash = Get-FileSha256Upper $info.FullName
        $items.Add(([pscustomobject]@{ path = $rel; sha256 = $hash }))
      }
    }
  }
  $sorted = New-Object System.Collections.Generic.List[object]
  foreach ($entry in ($items | Sort-Object -Property path)) {
    $sorted.Add($entry)
  }
  return ,$sorted
}

function Copy-SourceTreeToStage {
  param(
    [IO.DirectoryInfo]$From,
    [string]$ToRoot,
    [string]$FromRoot,
    [System.Collections.Generic.List[string]]$Excluded
  )
  foreach ($info in $From.EnumerateFileSystemInfos()) {
    $rel = Get-RelativePosix -Root $FromRoot -FullName $info.FullName
    if (($info.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      Write-Warning "Skipped reparse point: $rel"
      $Excluded.Add($rel)
      continue
    }
    if ($info -is [IO.DirectoryInfo]) {
      Copy-SourceTreeToStage -From $info -ToRoot $ToRoot -FromRoot $FromRoot -Excluded $Excluded
      continue
    }
    if (Test-IsExcludedFileName $info.Name) {
      Write-Warning "Excluded: $rel"
      $Excluded.Add($rel)
      continue
    }
    $dest = Join-Path $ToRoot ($rel -replace '/', [IO.Path]::DirectorySeparatorChar)
    $destDir = [IO.Path]::GetDirectoryName($dest)
    if (-not (Test-Path -LiteralPath $destDir)) {
      New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    }
    [IO.File]::Copy($info.FullName, $dest, $true)
  }
}

function New-PackStageFolder([string]$Root, [string]$Name) {
  if ([string]::IsNullOrWhiteSpace($Root)) {
    throw "StageRoot is empty"
  }
  $batch = Join-Path $Root ([guid]::NewGuid().ToString('D'))
  $stage = Join-Path $batch $Name
  New-Item -ItemType Directory -Force -Path $stage | Out-Null
  return [IO.Path]::GetFullPath($stage)
}

function Invoke-Git {
  param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)][string[]]$ArgumentList
  )
  # Native git warnings on stderr become NativeCommandError when that stream is
  # redirected. Stop would abort a command that exited 0; only $LASTEXITCODE
  # decides, matching the AddonBuilder invocation.
  $previousPreference = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    & git -C $RepoRoot @ArgumentList
  } finally {
    $ErrorActionPreference = $previousPreference
  }
}

function Get-PathWithTrailingSeparator([string]$Path) {
  $full = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
  return ($full + [IO.Path]::DirectorySeparatorChar)
}

function Test-NormalizedPathOverlap([string]$Left, [string]$Right) {
  # Trailing separator so C:\x\addon2 is not treated as inside C:\x\addon.
  $a = Get-PathWithTrailingSeparator $Left
  $b = Get-PathWithTrailingSeparator $Right
  if ($a.Equals($b, [StringComparison]::OrdinalIgnoreCase)) { return $true }
  if ($a.StartsWith($b, [StringComparison]::OrdinalIgnoreCase)) { return $true }
  if ($b.StartsWith($a, [StringComparison]::OrdinalIgnoreCase)) { return $true }
  return $false
}

function Get-GitBlobId([string]$FilePath) {
  $data = [IO.File]::ReadAllBytes($FilePath)
  $header = [Text.Encoding]::ASCII.GetBytes("blob $($data.Length)")
  $payload = New-Object byte[] ($header.Length + 1 + $data.Length)
  [Array]::Copy($header, 0, $payload, 0, $header.Length)
  $payload[$header.Length] = 0
  if ($data.Length -gt 0) {
    [Array]::Copy($data, 0, $payload, $header.Length + 1, $data.Length)
  }
  $sha1 = [System.Security.Cryptography.SHA1]::Create()
  try {
    $hash = $sha1.ComputeHash($payload)
    return ([BitConverter]::ToString($hash) -replace '-', '').ToLowerInvariant()
  } finally {
    $sha1.Dispose()
  }
}

function ConvertFrom-GitLsTreeLine([string]$Line) {
  $text = $Line.TrimEnd("`r")
  $tab = $text.IndexOf([char]9)
  if ($tab -lt 0) {
    throw "git ls-tree line is not '<mode> <type> <id><TAB><path>': $text"
  }
  $meta = $text.Substring(0, $tab)
  $gitPath = $text.Substring($tab + 1)
  $parts = $meta -split ' ', 3
  if ($parts.Count -ne 3 -or [string]::IsNullOrWhiteSpace($parts[0]) -or
      [string]::IsNullOrWhiteSpace($parts[1]) -or [string]::IsNullOrWhiteSpace($parts[2])) {
    throw "git ls-tree line is not '<mode> <type> <id><TAB><path>': $text"
  }
  return [pscustomobject]@{
    Mode = $parts[0]
    Type = $parts[1]
    Id   = $parts[2]
    Path = $gitPath
  }
}

function Test-OrdinalInList {
  param(
    [System.Collections.Generic.List[string]]$Items,
    [string]$Value
  )
  foreach ($item in $Items) {
    if ([string]::Equals([string]$item, $Value, [StringComparison]::Ordinal)) { return $true }
  }
  return $false
}

function Assert-StageMatchesGitTree {
  param(
    [string]$StageDir,
    [System.Collections.Generic.List[object]]$Entries,
    [string]$CommitSha
  )
  $treeRels = New-Object System.Collections.Generic.List[string]
  $blobEntries = New-Object System.Collections.Generic.List[object]
  foreach ($entry in $Entries) {
    $gitPath = [string]$entry.Path
    if ($gitPath.StartsWith('"')) {
      throw "Refusing to pack git-quoted path (rename the file so git does not quote it): $gitPath"
    }
    if ($entry.Mode -eq '120000' -or $entry.Mode -eq '160000') {
      throw "Refusing to pack git symlink or submodule (mode $($entry.Mode)): $gitPath"
    }
    if (-not $gitPath.StartsWith('addon/')) {
      throw "git ls-tree path is not under addon/: $gitPath"
    }
    $rel = $gitPath.Substring(6)
    $treeRels.Add($rel)
    if ($entry.Type -eq 'blob') {
      $blobEntries.Add([pscustomobject]@{
        Path = $rel
        Id   = ([string]$entry.Id).ToLowerInvariant()
      })
    }
  }

  $stagedRels = New-Object System.Collections.Generic.List[string]
  foreach ($record in (Get-StagedFileRecords -Root $StageDir)) {
    $stagedRels.Add([string]$record.path)
  }

  $missing = New-Object System.Collections.Generic.List[string]
  $extra = New-Object System.Collections.Generic.List[string]
  $different = New-Object System.Collections.Generic.List[string]
  foreach ($rel in $treeRels) {
    if (-not (Test-OrdinalInList -Items $stagedRels -Value $rel)) { $missing.Add($rel) }
  }
  foreach ($rel in $stagedRels) {
    if (-not (Test-OrdinalInList -Items $treeRels -Value $rel)) { $extra.Add($rel) }
  }
  foreach ($blob in $blobEntries) {
    if (-not (Test-OrdinalInList -Items $stagedRels -Value $blob.Path)) { continue }
    $stagedFile = Join-Path $StageDir ($blob.Path -replace '/', [IO.Path]::DirectorySeparatorChar)
    $got = Get-GitBlobId $stagedFile
    if (-not [string]::Equals($got, [string]$blob.Id, [StringComparison]::Ordinal)) {
      $different.Add($blob.Path)
    }
  }

  if ($missing.Count -gt 0 -or $extra.Count -gt 0 -or $different.Count -gt 0) {
    $bits = New-Object System.Collections.Generic.List[string]
    if ($missing.Count -gt 0) { $bits.Add('missing: ' + ($missing -join ', ')) }
    if ($extra.Count -gt 0) { $bits.Add('extra: ' + ($extra -join ', ')) }
    if ($different.Count -gt 0) { $bits.Add('different: ' + ($different -join ', ')) }
    throw ("Staged tree does not match the commit tree at $CommitSha. " +
           ($bits -join '; ') +
           '. .gitattributes (export-ignore, export-subst, eol, filters) can make git archive differ from the commit.')
  }
}

if ($ModName -notmatch '^[A-Za-z][A-Za-z0-9_]{0,63}$') {
  # The name doubles as a C-style identifier in CfgPatches, so a hyphen does not parse.
  throw "ModName must match ^[A-Za-z][A-Za-z0-9_]{0,63}$ (no hyphens): '$ModName'"
}

$folderMode = -not [string]::IsNullOrWhiteSpace($Source)
$excluded = New-Object System.Collections.Generic.List[string]
$commitSha = $null
$addonTreeSha = $null
$sourceKind = 'git'
$fromRoot = $null
$fromInfo = $null
# The build marker written into the staged addon root before packing (see below).
$markerName = 'mcp_build.json'

# Validate Source and resolve Destination before creating the stage. A StageRoot
# inside Source copies into itself; a StageRoot equal to Destination writes under
# Destination even with -StageOnly.
if ($folderMode) {
  $sourceKind = 'folder'
  if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
    throw "Source not found: $Source`nAddonBuilder reads through the P:\ work drive; see README."
  }
  $fromRoot = [IO.Path]::GetFullPath($Source)
  $fromInfo = New-Object IO.DirectoryInfo $fromRoot
  if (($fromInfo.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Source is a reparse point (junction or symlink); refusing to follow it: $fromRoot"
  }
  if (Test-Path -LiteralPath (Join-Path $fromRoot $markerName)) {
    throw ("Source carries $markerName at its root: $fromRoot`n" +
           "That name is reserved for the build marker this script writes; remove the file.")
  }
}

if (-not $Destination) {
  $workshop = "C:\Program Files (x86)\Steam\steamapps\common\DayZ\!Workshop"
  if ($env:DAYZ_PATH) { $workshop = Join-Path $env:DAYZ_PATH "!Workshop" }
  $Destination = Join-Path $workshop "@$ModName\Addons"
}

$stageRootFull = [IO.Path]::GetFullPath($StageRoot)
$destinationFull = [IO.Path]::GetFullPath($Destination)
if ($folderMode -and (Test-NormalizedPathOverlap $stageRootFull $fromRoot)) {
  throw "StageRoot overlaps Source: $stageRootFull $fromRoot"
}
if (Test-NormalizedPathOverlap $stageRootFull $destinationFull) {
  throw "StageRoot overlaps Destination: $stageRootFull $destinationFull"
}

$stage = New-PackStageFolder -Root $StageRoot -Name $ModName
$batchDir = Split-Path -Parent $stage

if ($folderMode) {
  Copy-SourceTreeToStage -From $fromInfo -ToRoot $stage -FromRoot $fromRoot -Excluded $excluded
} else {
  # git archive reads the commit tree, so a junction or a dirty worktree file
  # cannot enter the stage.
  $repoRoot = Split-Path -Parent $PSScriptRoot
  $resolved = Invoke-Git -RepoRoot $repoRoot -ArgumentList @('rev-parse', '--verify', "$Ref^{commit}")
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($resolved)) {
    throw "Could not resolve git ref '$Ref' to a commit in $repoRoot."
  }
  $commitSha = ([string]$resolved).Trim()
  $treeResolved = Invoke-Git -RepoRoot $repoRoot -ArgumentList @('rev-parse', '--verify', "${commitSha}:addon")
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($treeResolved)) {
    throw "Could not resolve addon/ at $commitSha in $repoRoot."
  }
  $addonTreeSha = ([string]$treeResolved).Trim()
  $treeRaw = Invoke-Git -RepoRoot $repoRoot -ArgumentList @('ls-tree', '-r', $commitSha, '--', 'addon')
  if ($LASTEXITCODE -ne 0) {
    throw "git ls-tree of addon/ at $commitSha failed with exit $LASTEXITCODE"
  }
  $treeLines = @()
  if ($null -ne $treeRaw -and "$treeRaw" -ne '') { $treeLines = @($treeRaw) }
  $treeEntries = New-Object System.Collections.Generic.List[object]
  foreach ($line in $treeLines) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    $treeEntries.Add((ConvertFrom-GitLsTreeLine $line))
  }
  foreach ($entry in $treeEntries) {
    if ($entry.Mode -eq '120000' -or $entry.Mode -eq '160000') {
      throw "Refusing to pack git symlink or submodule (mode $($entry.Mode)): $($entry.Path)"
    }
    if ([string]::Equals([string]$entry.Path, "addon/$markerName", [StringComparison]::OrdinalIgnoreCase)) {
      throw ("$($entry.Path) is tracked at ${commitSha}. That name is reserved for the build marker " +
             "this script writes; rename the file.")
    }
  }
  $zipFile = Join-Path $batchDir 'addon.zip'
  # One-shot -c: git archive --format=zip otherwise writes CRLF on Windows
  # (core.autocrlf), which would not match the blob bytes.
  $null = Invoke-Git -RepoRoot $repoRoot -ArgumentList @(
    '-c', 'core.autocrlf=false', 'archive', '--format=zip', '-o', $zipFile, "${commitSha}:addon"
  )
  if ($LASTEXITCODE -ne 0) {
    throw "git archive of ${commitSha}:addon failed with exit $LASTEXITCODE"
  }
  Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null
  [System.IO.Compression.ZipFile]::ExtractToDirectory($zipFile, $stage)
  # git archive applies .gitattributes; the stage must still equal the commit tree.
  Assert-StageMatchesGitTree -StageDir $stage -Entries $treeEntries -CommitSha $commitSha
}

# fb-20260819-024951-e307: a PBO has to say which commit built it, or deploying a
# stale one silently reverts someone else's work. AddonBuilder -packonly packs every
# file of the stage (fb-63c9), so this marker reaches the PBO root; it is not in git.
# tools/dev/pbo_provenance.py expects exactly this one extra entry and checks its
# commit against the ref it compares with. Enforce never reads it: config.cpp
# compiles only scripts/4_World and scripts/5_Mission. A binarizing build (a tree
# with .p3d/.paa/.rvmat) honours include.lst and would leave the marker out.
$builtUtc = [DateTime]::UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture)
$markerJson = ('{"commit":' + (ConvertTo-JsonValue $commitSha) +
  ',"tree":' + (ConvertTo-JsonValue $addonTreeSha) +
  ',"built_utc":' + (ConvertTo-JsonValue $builtUtc) +
  ',"source":' + (ConvertTo-JsonValue $sourceKind) + '}')
[IO.File]::WriteAllText((Join-Path $stage $markerName), ($markerJson + "`n"), (New-Object System.Text.UTF8Encoding $false))

$prefixFile = Join-Path $stage '$PBOPREFIX$'
if (-not (Test-Path -LiteralPath $prefixFile)) {
  throw "No `$PBOPREFIX`$ in $stage -- that file is what makes it an addon source tree."
}

$files = Get-StagedFileRecords -Root $stage
$excluded.Sort()

if ($StageOnly) {
  $json = ('{"commit":' + (ConvertTo-JsonValue $commitSha) +
    ',"source":' + (ConvertTo-JsonValue $sourceKind) +
    ',"stage":' + (ConvertTo-JsonValue $stage) +
    ',"files":' + (ConvertTo-JsonArray $files) +
    ',"excluded":' + (ConvertTo-JsonArray $excluded) + '}')
  Write-Output $json
  exit 0
}

$stageFull = [IO.Path]::GetFullPath($stage)
if (-not $stageFull.StartsWith('P:\', [StringComparison]::OrdinalIgnoreCase)) {
  throw ("Stage folder is not under P:\ (AddonBuilder reads through the P:\ work drive): $stageFull")
}

$Source = $stageFull

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

# Same predicate as dayz_mcp.pack_only / dayz_test_worker: .p3d .paa .rvmat.
# Binarize uses -addon=P: and any broken config.cpp under P:\ fails the pack
# (fb-20260915-005408-bcd8). Scripts-only trees must pass -packonly.
function Test-HasBinarizableAssets {
  param([string]$Root)
  $suffixes = @('.p3d', '.paa', '.rvmat')
  try {
    $hit = Get-ChildItem -LiteralPath $Root -Recurse -File -Force -ErrorAction Stop |
      Where-Object { $suffixes -contains $_.Extension.ToLowerInvariant() } |
      Select-Object -First 1
    return $null -ne $hit
  } catch {
    return $false
  }
}
if ($PackOnly -or -not (Test-HasBinarizableAssets -Root $Source)) {
  $args += "-packonly"
}

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
