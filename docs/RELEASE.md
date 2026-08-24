# DayZ-MCP Release Runbook

This runbook publishes a thin GitHub Release containing a PBO built on a Windows
host with DayZ Tools. `make_release.py` does not run AddonBuilder; it accepts an
already-built PBO and stages three verifiable release assets.

## 1. Bump and commit the release version

Set the release version in
[`tools/pyproject.toml:7`](../tools/pyproject.toml#L7). Use the same value, without
the leading `v`, throughout this runbook.

[EXACT]

```powershell
$Version = "X.Y.Z"
git diff -- tools/pyproject.toml
git add tools/pyproject.toml
git commit -m "Bump version to $Version"
if (git status --porcelain) { throw "Release worktree is not clean" }
```

The clean-tree check is intentional. `make_release.py` refuses a dirty worktree
with `dirty_tree` because uncommitted bytes are not represented by `git_sha`.
Use `--allow-dirty` only for an explicitly non-published rehearsal.

## 2. Build the PBO on the DayZ Tools host

The packer header states that the source must be reachable through `P:\` and that
DayZ Tools are required
([`tools/pack-addon.ps1:1-7`](../tools/pack-addon.ps1#L1-L7)). Do not run this step
on a host without that environment.

[EXACT]

```powershell
.\tools\pack-addon.ps1 -Source "P:\addon" -ModName DayZ_MCP
```

The script prints the resulting `DayZ_MCP.pbo` path and byte size after
AddonBuilder succeeds
([`tools/pack-addon.ps1:88-90`](../tools/pack-addon.ps1#L88-L90)). Record that
printed path; do not substitute a source-tree file or an older deployed PBO.

## 3. Stage the release assets

[EXACT]

```powershell
$Pbo = "C:\path\printed\by\pack-addon\DayZ_MCP.pbo"
.\tools\.venv-mcp\Scripts\python.exe .\tools\make_release.py `
  --pbo $Pbo `
  --out .\dist
```

The command creates exactly these publishable assets:

- `dist/DayZ_MCP-vX.Y.Z-addon.zip`, containing only
  `@DayZ_MCP/Addons/DayZ_MCP.pbo`;
- `dist/VERSION.json`;
- `dist/SHA256SUMS.txt`.

`VERSION.json` records `version`, `bridge_version`, `git_sha`, `pbo_sha256`, and
`built_utc`. The bridge value comes from
[`addon/scripts/5_Mission/MCPMessages.c:1`](../addon/scripts/5_Mission/MCPMessages.c#L1).

## 4. Recompute and verify every checksum

`SHA256SUMS.txt` includes the PBO embedded in the ZIP, the ZIP itself, and
`VERSION.json`. This check extracts the PBO and recomputes all three hashes; it
does not trust values returned by the staging code.

[EXACT]

```powershell
$Dist = (Resolve-Path .\dist).Path
$ZipName = "DayZ_MCP-v$Version-addon.zip"
$VerifyDir = Join-Path ([System.IO.Path]::GetTempPath()) `
  ("dayz-mcp-release-verify-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $VerifyDir | Out-Null
Expand-Archive -LiteralPath (Join-Path $Dist $ZipName) -DestinationPath $VerifyDir

$ExtractedPbo = Join-Path $VerifyDir "@DayZ_MCP\Addons\DayZ_MCP.pbo"
$ExtractedFiles = @(Get-ChildItem -LiteralPath $VerifyDir -Recurse -File)
if ($ExtractedFiles.Count -ne 1 -or -not (Test-Path -LiteralPath $ExtractedPbo)) {
  throw "ZIP layout mismatch: expected only @DayZ_MCP/Addons/DayZ_MCP.pbo"
}

$Targets = [ordered]@{
  "DayZ_MCP.pbo" = $ExtractedPbo
  $ZipName = Join-Path $Dist $ZipName
  "VERSION.json" = Join-Path $Dist "VERSION.json"
}
$Expected = @{}
foreach ($Line in Get-Content -LiteralPath (Join-Path $Dist "SHA256SUMS.txt")) {
  if ($Line -notmatch '^(?<sha>[0-9a-f]{64})  (?<name>.+)$') {
    throw "Malformed SHA256SUMS line: $Line"
  }
  if ($Expected.ContainsKey($Matches.name)) {
    throw "Duplicate SHA256SUMS entry: $($Matches.name)"
  }
  $Expected[$Matches.name] = $Matches.sha
}
if ($Expected.Count -ne 3) { throw "SHA256SUMS must contain exactly three entries" }

foreach ($AssetName in $Targets.Keys) {
  if (-not $Expected.ContainsKey($AssetName)) {
    throw "SHA256SUMS entry missing: $AssetName"
  }
  $Actual = (Get-FileHash -LiteralPath $Targets[$AssetName] -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($Actual -ne $Expected[$AssetName]) {
    throw "SHA-256 mismatch for $AssetName"
  }
}
"SHA256SUMS verified"
```

Also inspect the manifest before publishing:

[EXACT]

```powershell
$Manifest = Get-Content -Raw .\dist\VERSION.json | ConvertFrom-Json
if ($Manifest.version -ne $Version) { throw "VERSION.json version mismatch" }
if ($Manifest.pbo_sha256 -ne $Expected["DayZ_MCP.pbo"]) {
  throw "VERSION.json PBO hash mismatch"
}
```

## 5. Tag and create the GitHub Release

Create the tag at the clean commit recorded in `VERSION.json`, push that tag, and
require GitHub CLI to find the remote tag before publishing. Upload only the three
asset names above.

[EXACT]

```powershell
if ((git rev-parse HEAD) -ne $Manifest.git_sha) {
  throw "HEAD does not match VERSION.json git_sha"
}
git tag "v$Version"
git push origin "v$Version"
gh release create "v$Version" `
  ".\dist\DayZ_MCP-v$Version-addon.zip" `
  ".\dist\VERSION.json" `
  ".\dist\SHA256SUMS.txt" `
  --verify-tag `
  --title "DayZ-MCP v$Version" `
  --generate-notes
```

## 6. Post-release checks

Query the published release, require the exact asset set, download fresh copies,
and repeat the checksum procedure from step 4 with `$Dist` set to `$DownloadDir`.

[EXACT]

```powershell
$Release = gh release view "v$Version" `
  --json tagName,isDraft,isPrerelease,assets,url | ConvertFrom-Json
$PublishedNames = @($Release.assets | ForEach-Object { $_.name } | Sort-Object)
$RequiredNames = @(
  "DayZ_MCP-v$Version-addon.zip",
  "SHA256SUMS.txt",
  "VERSION.json"
) | Sort-Object
if (Compare-Object $PublishedNames $RequiredNames) {
  throw "Published release asset set mismatch"
}
if ($Release.tagName -ne "v$Version" -or $Release.isDraft -or $Release.isPrerelease) {
  throw "Published release state mismatch"
}

$DownloadDir = Join-Path ([System.IO.Path]::GetTempPath()) `
  ("dayz-mcp-release-download-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $DownloadDir | Out-Null
gh release download "v$Version" --dir $DownloadDir

$DownloadedManifest = Get-Content -Raw (Join-Path $DownloadDir "VERSION.json") | ConvertFrom-Json
if ((git rev-list -n 1 "v$Version") -ne $DownloadedManifest.git_sha) {
  throw "Published manifest does not identify the tagged commit"
}
```

Finally, call `bridge_status` from an MCP session after loading the released
addon, then run the read-only doctor documented in
[`tools/README-mcp.md:37-44`](../tools/README-mcp.md#L37-L44).

[EXACT]

```powershell
.\tools\.venv-mcp\Scripts\python.exe -m dayz_mcp.doctor `
  --daemon-policy normal `
  --json `
  --require-clean
```

Python and the bridge are version-gated by the poll handshake: the server expects
a bridge version and classifies `<bridge_version>~<game_version>` as accepted or
`version_mismatch`
([`tools/dayz_mcp/core.py:17-47`](../tools/dayz_mcp/core.py#L17-L47)). The
`bridge_version` in `VERSION.json` gives doctor and release diagnostics the
release-side value needed to name a PBO/server mismatch instead of reporting an
opaque failure. The live handshake result remains authoritative.
