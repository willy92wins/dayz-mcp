# DayZ-MCP Release Runbook

This runbook publishes a thin GitHub Release containing a PBO built on a Windows
host with DayZ Tools. `make_release.py` does not run AddonBuilder; it accepts an
already-built PBO and stages five verifiable release assets.

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

## 2. Build and sign the PBO

The packer header states that the source must be reachable through `P:\` and that
DayZ Tools are required
([`tools/pack-addon.ps1:2-10`](../tools/pack-addon.ps1#L2-L10)). Do not run this step
on a host without that environment.

[EXACT]

```powershell
$BuildDir = Join-Path ([System.IO.Path]::GetTempPath()) `
  ("dayz-mcp-release-build-" + [guid]::NewGuid().ToString("N"))
.\tools\pack-addon.ps1 -ModName DayZ_MCP -Destination (Join-Path $BuildDir "@DayZ_MCP\Addons")
```

With no `-Destination` the script writes into `<DayZ>\!Workshop\@DayZ_MCP\Addons`,
the PBO test runs load, and keeps no copy of the one it replaces. Build into a
folder of its own; step 5 swaps the PBO in.

It packs the committed `addon/` at HEAD, which step 1 left clean. Before packing
it writes `mcp_build.json` into the staged addon root: the commit, the `addon/`
tree id and the build time in UTC. The file is not in git; the PBO carries it in
its root, so a PBO says which commit built it. Enforce never reads it:
`addon/config.cpp` compiles only `scripts/4_World` and `scripts/5_Mission`.
The script prints the resulting `DayZ_MCP.pbo` path and byte size after
AddonBuilder succeeds
([`tools/pack-addon.ps1:620-620`](../tools/pack-addon.ps1#L620-L620)). Record that
printed path; do not substitute a source-tree file or an older deployed PBO.

Sign that PBO with `DSSignFile.exe` from `Bin\DsUtils` in DayZ Tools. The
private key `DayZ_MCP.biprivatekey` lives in a local folder outside the
repository, outside OneDrive and outside the build folder, with an offline
copy; it is never copied beside the PBO and never enters the repository.

[EXACT]

```powershell
$Pbo = "C:\path\printed\by\pack-addon\DayZ_MCP.pbo"
$PrivateKey = "C:\path\outside\the\repo\DayZ_MCP.biprivatekey"
& "C:\path\to\DayZ Tools\Bin\DsUtils\DSSignFile.exe" $PrivateKey $Pbo
if ($LASTEXITCODE -ne 0) { throw "DSSignFile failed" }
if (-not (Test-Path -LiteralPath "$Pbo.DayZ_MCP.bisign")) {
  throw "DSSignFile did not leave $Pbo.DayZ_MCP.bisign"
}
```

## 3. Stage the release assets

[EXACT]

```powershell
.\tools\.venv-mcp\Scripts\python.exe .\tools\dev\pbo_provenance.py $Pbo . HEAD
if ($LASTEXITCODE -ne 0) { throw "The PBO is not the build of HEAD" }
.\tools\.venv-mcp\Scripts\python.exe .\tools\make_release.py `
  --pbo $Pbo `
  --out .\dist
```

`pbo_provenance.py` compares every PBO entry with `addon/` at HEAD byte for byte,
requires the header's `prefix` to be the one `addon/$PBOPREFIX$` declares, and
requires `mcp_build.json` to name HEAD's commit and `addon/` tree; it exits
non-zero on any difference, extra entry or missing file. `make_release.py` reads
the marker too and refuses a PBO built from another commit than the `git_sha` it
records (`pbo_commit_mismatch`), one without a valid marker (`pbo_marker_missing`,
`pbo_marker_invalid`) and bytes that are not a PBO (`pbo_unreadable`).

`make_release.py` also reads the `.bisign` beside the PBO,
`tools/release/DayZ_MCP.bikey` and `tools/release/mod.cpp`, and runs
`DSCheckSignatures` from DayZ Tools (found through `DAYZ_TOOLS_PATH` or the
registry). It refuses with `private_key_present` when a `.biprivatekey` file is
found in the repository, beside the PBO or under `--out`. It refuses with
`release_key_missing` when `tools/release/DayZ_MCP.bikey` is missing or empty.
It refuses with `mod_cpp_missing` when `tools/release/mod.cpp` is missing or
empty. It refuses with `bisign_missing` when the `.bisign` beside the PBO is
missing. It refuses with `bisign_wrong_key` when the `.bisign` does not start
with the `.bikey` bytes. It refuses with `signature_check_unavailable` when
`DSCheckSignatures` cannot be run. It refuses with `bisign_invalid` when
`DSCheckSignatures` does not report exactly one OK signature for the PBO.
It refuses with `requirements_lock_missing` when
`tools/release/requirements.lock.txt` is missing or empty. It refuses with
`wheel_build_failed` when pip cannot build the wheel. It refuses with
`wheel_name_unexpected` when the built wheel is not named
`dayz_mcp_tools-X.Y.Z-py3-none-any.whl`.

`make_release.py` also builds the wheel with the pip of the venv, and for that
it needs network, because pip downloads `setuptools==84.0.0`.

The command creates exactly these publishable assets:

- `dist/DayZ_MCP-vX.Y.Z-addon.zip`, containing
  `@DayZ_MCP/Addons/DayZ_MCP.pbo`,
  `@DayZ_MCP/Addons/DayZ_MCP.pbo.DayZ_MCP.bisign`,
  `@DayZ_MCP/Keys/DayZ_MCP.bikey`, `@DayZ_MCP/mod.cpp` and
  `@DayZ_MCP/VERSION.json`;
- `dist/VERSION.json`;
- `dist/dayz_mcp_tools-X.Y.Z-py3-none-any.whl`, with the release
  `VERSION.json` inside it;
- `dist/dayz-mcp-requirements.txt`, which is the lock in
  `tools/release/requirements.lock.txt` plus one line that pins the wheel by
  its release URL and its sha256;
- `dist/SHA256SUMS.txt`, with eight lines.

`VERSION.json` records `version`, `bridge_version`, `git_sha`, `pbo_sha256`, and
`built_utc`. The bridge value comes from
[`addon/scripts/5_Mission/MCPMessages.c:1`](../addon/scripts/5_Mission/MCPMessages.c#L1).

## 4. Recompute and verify every checksum

`SHA256SUMS.txt` includes the four files inside the ZIP, the ZIP itself,
`VERSION.json`, the wheel and `dayz-mcp-requirements.txt`. This check extracts
the ZIP and recomputes all eight hashes; it does not trust values returned by
the staging code.

[EXACT]

```powershell
$Dist = (Resolve-Path .\dist).Path
$ZipName = "DayZ_MCP-v$Version-addon.zip"
$WheelName = "dayz_mcp_tools-$Version-py3-none-any.whl"
$VerifyDir = Join-Path ([System.IO.Path]::GetTempPath()) `
  ("dayz-mcp-release-verify-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $VerifyDir | Out-Null
Expand-Archive -LiteralPath (Join-Path $Dist $ZipName) -DestinationPath $VerifyDir

$ExtractedPbo = Join-Path $VerifyDir "@DayZ_MCP\Addons\DayZ_MCP.pbo"
$ExtractedBisign = Join-Path $VerifyDir "@DayZ_MCP\Addons\DayZ_MCP.pbo.DayZ_MCP.bisign"
$ExtractedBikey = Join-Path $VerifyDir "@DayZ_MCP\Keys\DayZ_MCP.bikey"
$ExtractedModCpp = Join-Path $VerifyDir "@DayZ_MCP\mod.cpp"
$ExtractedVersion = Join-Path $VerifyDir "@DayZ_MCP\VERSION.json"
$ExtractedFiles = @(Get-ChildItem -LiteralPath $VerifyDir -Recurse -File)
if ($ExtractedFiles.Count -ne 5) {
  throw "ZIP layout mismatch: expected five extracted files"
}
foreach ($ExtractedFile in @($ExtractedPbo, $ExtractedBisign, $ExtractedBikey, $ExtractedModCpp, $ExtractedVersion)) {
  if (-not (Test-Path -LiteralPath $ExtractedFile)) {
    throw "ZIP layout mismatch: missing $ExtractedFile"
  }
}

$Targets = [ordered]@{
  "DayZ_MCP.pbo" = $ExtractedPbo
  "DayZ_MCP.pbo.DayZ_MCP.bisign" = $ExtractedBisign
  "DayZ_MCP.bikey" = $ExtractedBikey
  "mod.cpp" = $ExtractedModCpp
  $ZipName = Join-Path $Dist $ZipName
  "VERSION.json" = Join-Path $Dist "VERSION.json"
  $WheelName = Join-Path $Dist $WheelName
  "dayz-mcp-requirements.txt" = Join-Path $Dist "dayz-mcp-requirements.txt"
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
if ($Expected.Count -ne 8) { throw "SHA256SUMS must contain exactly eight entries" }

foreach ($AssetName in $Targets.Keys) {
  if (-not $Expected.ContainsKey($AssetName)) {
    throw "SHA256SUMS entry missing: $AssetName"
  }
  $Actual = (Get-FileHash -LiteralPath $Targets[$AssetName] -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($Actual -ne $Expected[$AssetName]) {
    throw "SHA-256 mismatch for $AssetName"
  }
}
if ((Get-FileHash -LiteralPath $ExtractedVersion -Algorithm SHA256).Hash -ne
    (Get-FileHash -LiteralPath (Join-Path $Dist "VERSION.json") -Algorithm SHA256).Hash) {
  throw "Extracted VERSION.json does not match dist VERSION.json"
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$WheelArchive = [System.IO.Compression.ZipFile]::OpenRead((Join-Path $Dist $WheelName))
try {
  $WheelVersionEntry = $WheelArchive.GetEntry("dayz_mcp/VERSION.json")
  if (-not $WheelVersionEntry) {
    throw "Wheel is missing dayz_mcp/VERSION.json"
  }
  $WheelVersionStream = $WheelVersionEntry.Open()
  try {
    $WheelVersionMemory = New-Object System.IO.MemoryStream
    $WheelVersionStream.CopyTo($WheelVersionMemory)
    $WheelVersionBytes = $WheelVersionMemory.ToArray()
  } finally {
    $WheelVersionStream.Dispose()
  }
} finally {
  $WheelArchive.Dispose()
}
$DistVersionBytes = [System.IO.File]::ReadAllBytes((Join-Path $Dist "VERSION.json"))
if (-not [System.Linq.Enumerable]::SequenceEqual([byte[]]$WheelVersionBytes, [byte[]]$DistVersionBytes)) {
  throw "Wheel dayz_mcp/VERSION.json does not match dist VERSION.json"
}
$RequirementsLines = @(Get-Content -LiteralPath (Join-Path $Dist "dayz-mcp-requirements.txt"))
if ($RequirementsLines.Count -eq 0) {
  throw "dayz-mcp-requirements.txt is empty"
}
$WheelLineStarts = @($RequirementsLines | Where-Object { $_.StartsWith("dayz-mcp-tools") })
if ($WheelLineStarts.Count -ne 1) {
  throw "dayz-mcp-requirements.txt must have exactly one line starting with dayz-mcp-tools"
}
$ExpectedWheelLine = "dayz-mcp-tools @ https://github.com/willy92wins/dayz-mcp/releases/download/v$Version/$WheelName --hash=sha256:$($Expected[$WheelName])"
if ($RequirementsLines[-1] -ne $ExpectedWheelLine) {
  throw "Last line of dayz-mcp-requirements.txt is not the wheel line for $WheelName"
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

## 5. Play one game with the release PBO

The release's CHANGELOG section names the DayZ build it supports. Verify that
before the tag: swap the PBO from step 2 in where test runs load it, then play one
run on that build. A run that fails stops the release here. Every session's runs
load that PBO, so hold the lease (`session_acquire_wait`) for the swap.

Test runs load the mod from the `mods_root` of the `DayZ_MCP` project in the
host's `launcher-policy.json`
([`tools/README-mcp.md`](../tools/README-mcp.md#native-launcher-host-policy)).
When that folder is not `!Workshop`, set `$Addons` to its `@DayZ_MCP\Addons`
before the block. Set `$Old` to the SHA-256 of the PBO you expect to replace, one
you know: the previous release's `pbo_sha256`, or the hash your last swap
installed. Check a PBO you do not know with `tools/dev/pbo_provenance.py` first.
The swap refuses any other PBO.

[EXACT]

```powershell
$New = (Get-FileHash -LiteralPath $Pbo -Algorithm SHA256).Hash.ToLowerInvariant()
if ($New -ne $Manifest.pbo_sha256) { throw "This PBO is not the one in the ZIP" }
if (-not $Addons) {
  $Workshop = "C:\Program Files (x86)\Steam\steamapps\common\DayZ\!Workshop"
  if ($env:DAYZ_PATH) { $Workshop = Join-Path $env:DAYZ_PATH "!Workshop" }
  $Addons = Join-Path $Workshop "@DayZ_MCP\Addons"
}
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\dev\swap_pbo.ps1 `
  -Build $Pbo -WantNew $New -WantOld $Old -Destination $Addons
if ($LASTEXITCODE -ne 0) { throw "swap_pbo.ps1 did not swap the PBO" }
```

The previous PBO stays beside the live one as `DayZ_MCP.pbo.swapped_out_*`. Then
release the lease: `dayz_test_run` takes its own and refuses to start while your
session holds one. Run
`dayz_test_run(project="DayZ_MCP", mode="all", no_file_patching=true)` on that
DayZ build. Without `-filePatching` the game reads the mod's files from the PBO
only. Adopt the run with `session_acquire_wait`, since `dayz_test_close` needs the
lease, and call `bridge_status`. The lease lapses 120 s after the last call that
renews it
([`tools/dayz_mcp/session_coordination.py:15`](../tools/dayz_mcp/session_coordination.py#L15)),
and `session_status` does not renew it: call `session_heartbeat` before then, and
adopt the run again if it lapsed. While the run is up, check which game and which
PBO run with its profile folders and port. Set `$Profiles` to the `artifacts_paths`
that `dayz_test_run` returned and `$Port` to the port you passed it (2302 by
default). The block reads only the `DayZDiag_x64.exe` processes that run with one
of those profile folders and that port, and stops unless they are one server and
one client. Both started after the swap: right before it moves the PBO in,
`swap_pbo.ps1` checks that no DayZ process runs
([`tools/dev/swap_pbo.ps1:318-323`](../tools/dev/swap_pbo.ps1#L318-L323)). The
block cannot tell them from the processes of another run of the same project and
instance that another session starts on that port after the swap: the profile
folders do not carry the run's id.

[EXACT]

```powershell
$ErrorActionPreference = "Stop"
$Peers = @(Get-CimInstance Win32_Process -Filter "Name = 'DayZDiag_x64.exe'" |
  Where-Object {
    $Line = [string]$_.CommandLine
    $Root = [regex]::Match($Line,
      '"-profiles=(?<v>[^"]*)"|(?<!\S)-profiles=(?<v>\S+)')
    $Root.Success -and $Root.Groups["v"].Value -in $Profiles -and
      $Line -match "(?<!\S)-port=$Port(?!\S)"
  })
if ($Peers.Count -ne 2) {
  throw "Expected the run's server and client, found $($Peers.Count)"
}
$Loaded = @(foreach ($Peer in $Peers) {
  $Line = [string]$Peer.CommandLine
  if ($Line -match '(?<!\S)-filePatching(?!\S)') {
    throw "PID $($Peer.ProcessId) runs with -filePatching"
  }
  $Mod = @([regex]::Matches($Line, '"-mod=(?<v>[^"]*)"|(?<!\S)-mod=(?<v>\S+)') |
    ForEach-Object { $_.Groups["v"].Value -split ";" } |
    Where-Object { $_ -and (Split-Path -Leaf $_) -eq "@DayZ_MCP" })
  if ($Mod.Count -ne 1) {
    throw "PID $($Peer.ProcessId) does not load one @DayZ_MCP"
  }
  $Hash = (Get-FileHash -LiteralPath (Join-Path $Mod[0] "Addons\DayZ_MCP.pbo") `
    -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($Hash -ne $New) {
    throw "PID $($Peer.ProcessId) loads another PBO from $($Mod[0])"
  }
  $Exe = (Get-Item -LiteralPath $Peer.ExecutablePath).VersionInfo
  [pscustomobject]@{
    pid = $Peer.ProcessId
    started_utc = $Peer.CreationDate.ToUniversalTime().ToString("o")
    role = if ($Line -match '(?<!\S)-server(?!\S)') { "server" } else { "client" }
    exe = $Peer.ExecutablePath
    file_version = $Exe.FileVersion
    product_version = $Exe.ProductVersion
    mod = $Mod[0]
    pbo_sha256 = $Hash
  }
})
if (@($Loaded | Where-Object { $_.role -eq "server" }).Count -ne 1) {
  throw "Expected one server and one client"
}
$Loaded | ConvertTo-Json
```

Then close the run with `dayz_test_close` and release the lease. Save this
evidence before the tag:

- From `bridge_status`, both peers: `version` `<bridge_version>~<game version>`
  with the game version the CHANGELOG names, `version_state` `ok`, capabilities
  `match`, and one `run_id`. The poll handshake compares the game version only when
  `expected_game_version` is set
  ([`tools/dayz_mcp/core.py:69-70`](../tools/dayz_mcp/core.py#L69-L70)), so `ok`
  alone does not prove the build.
- The JSON above: for each of the two processes the block read, the process id
  and start time, the role, the executable and its version, and the `@DayZ_MCP`
  folder it runs with, whose PBO had the release's `pbo_sha256` when the block
  hashed it.
- From `dayz_test_close`: `graceful`, `roles` with the server and the client, each
  with `termination_line` true, `logout_players` not empty with every logout
  finished, and no `warnings`. `player_state_not_saved` means the logout wait timed
  out.
- The server and client RPT logs, and the server's script log. The server's RPT
  shows the player's connection, the `[Disconnect]` lines and
  `--- Termination successfully completed ---`; `[Logout]: Player … finished` is
  in its script log. Both RPTs also log
  `ANIMATION (E): Can't load …/Anims/cfg/skeletons.anim.xml`: the mod ships no
  animations, and that line does not stop a run.

## 6. Tag and create the GitHub Release

Create the tag at the clean commit recorded in `VERSION.json`, push that tag, and
require GitHub CLI to find the remote tag before publishing. Upload only the five
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
  ".\dist\dayz_mcp_tools-$Version-py3-none-any.whl" `
  ".\dist\dayz-mcp-requirements.txt" `
  ".\dist\SHA256SUMS.txt" `
  --verify-tag `
  --title "DayZ-MCP v$Version" `
  --generate-notes
```

## 7. Post-release checks

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
  "VERSION.json",
  "dayz_mcp_tools-$Version-py3-none-any.whl",
  "dayz-mcp-requirements.txt"
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

Install the published release in a new venv, in a new temporary folder, from
the downloaded files only:

[EXACT]

```powershell
uv python install 3.14 --no-bin --no-registry
if ($LASTEXITCODE -ne 0) { throw "uv python install 3.14 failed" }
$ManagedPython = uv python find --managed-python 3.14
if ($LASTEXITCODE -ne 0) { throw "uv python find --managed-python 3.14 failed" }
$ManagedPython = ([string]$ManagedPython).Trim()
$InstallDir = Join-Path ([System.IO.Path]::GetTempPath()) `
  ("dayz-mcp-release-install-" + [guid]::NewGuid().ToString("N"))
& $ManagedPython -m venv --without-pip $InstallDir
if ($LASTEXITCODE -ne 0) { throw "Creating the release install venv failed" }
$InstallPython = Join-Path $InstallDir "Scripts\python.exe"
uv pip install --python $InstallPython --link-mode=copy --require-hashes -r (Join-Path $DownloadDir "dayz-mcp-requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Installing the published release failed" }
$InstalledVersionBytes = [System.IO.File]::ReadAllBytes((Join-Path $InstallDir "Lib\site-packages\dayz_mcp\VERSION.json"))
$DownloadedVersionBytes = [System.IO.File]::ReadAllBytes((Join-Path $DownloadDir "VERSION.json"))
if (-not [System.Linq.Enumerable]::SequenceEqual([byte[]]$InstalledVersionBytes, [byte[]]$DownloadedVersionBytes)) {
  throw "Installed dayz_mcp/VERSION.json does not match the downloaded VERSION.json"
}
```

Matching checksums tie the published PBO to step 5's check: the `@DayZ_MCP` of
both processes it read, the server and the client with the run's profile folders
and port, held a PBO with the same `pbo_sha256` when the check ran. Finally, run
the read-only doctor documented in
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

## 8. Publish the Workshop item

Publish the `@DayZ_MCP` folder that step 7 extracted and verified when it
repeated the step 4 check with `$Dist` set to `$DownloadDir`: in that
PowerShell session, `$VerifyDir\@DayZ_MCP`, with its five files. Point the
Publisher from DayZ Tools at that `@DayZ_MCP` folder itself; pointed at its
`Addons` folder, the Publisher fails without uploading anything. Never publish
the build folder. Use the Publisher because SteamCMD with the owner's account
closes the owner's Steam client session.

## Updating the dependency lock

Rebuild the lock when `tools/requirements-mcp.txt` changes, or to raise the
dependencies. From the repository root:

[EXACT]

```powershell
uv pip compile tools/requirements-mcp.txt --generate-hashes --python-platform x86_64-pc-windows-msvc --python-version 3.14 -o tools/release/requirements.lock.txt
```

The lock is for the Python 3.14 that the install recipe installs.
`tools/tests/test_packaging_declarations.py` checks that the lock pins the
same versions as `tools/requirements-mcp.txt`.

## Rotating the signing key

If the signing key is lost or leaked, create a new key with `DSCreateKey`, put
the new `.bikey` in `tools/release` and in the Workshop item, re-sign the PBO
and publish again; every server must replace the `.bikey` in its `keys` folder
with the new one.

## Development tools (`tools/dev/`)

Tools for working on this repository and promoting a build; each documents its
parameters in its header.

- `tools/dev/setup_worktree.sh <worktree-dir> <new-branch> [base-ref]` creates a git
  worktree with its own `tools/.venv-mcp`, installed like CI, then checks from an
  empty directory that the editable install resolves `dayz_mcp` inside that
  worktree and that no junction or symlink lies on the path to its venv (cloud-file
  placeholders such as OneDrive's pass). `--check <worktree-dir>` runs only the check.
- `tools/dev/pbo_provenance.py <pbo> <repo> <ref>` compares a built PBO file by file
  with `addon/` at `<ref>` and checks its header `prefix` against
  `addon/$PBOPREFIX$` and its `mcp_build.json`; it exits 0 only when everything
  matches.
- `tools/dev/swap_pbo.ps1 -Build <pbo> -WantNew <sha256> -WantOld <sha256>` replaces
  the deployed PBO only when the build and the live file have the expected hashes
  and no DayZ game process runs. It holds the live PBO locked from its hash to the
  swap, installs the bytes it hashed through renames that never overwrite a file,
  and keeps the previous PBO beside it as
  `<ModName>.pbo.swapped_out_<UTC time>_<hash prefix>`. `-Backup` with `-WantBackup`
  also requires an intact rollback copy that is not the live file under another
  name.
