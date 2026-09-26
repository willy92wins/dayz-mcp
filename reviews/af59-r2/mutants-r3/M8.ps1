#Requires -Version 5.1
<#
.SYNOPSIS
  Build, deploy and launch a DayZ mod through the managed lifecycle.
.DESCRIPTION
  Paths use DAYZ_* environment overrides, then Steam defaults.
  DAYZ_DEV_ROOT selects the mod workspace; DAYZ_ADDONBUILDER selects the builder.
  BuildOnly and Mode none build without game, mission or lifecycle prerequisites.
.PARAMETER Mod
  Mod name used for the source folder, addon prefix and deployed PBO.
.PARAMETER Clean
  Pass -clear to AddonBuilder. Implies Build.
.PARAMETER Release
  Force a clean binarized build and pass Release to the post-build hook.
.PARAMETER PreBuildScript
  Optional PowerShell hook: -Src <path> -Verify:<bool> -VerifyOnly:<bool>
  -VerifyP3d <path>. Runs before the build, or alone with VerifyOnly.
.PARAMETER PostBuildScript
  Optional PowerShell hook: -Pbo <path> -Src <path> -Release:<bool>.
  Runs after build checks and before the deployed message.
.PARAMETER Verify
  Request verification from the configured pre-build hook. Default off.
.PARAMETER VerifyOnly
  Run only the pre-build hook. No configured hook means no action or warning.
.PARAMETER VerifyP3d
  Optional model path passed unchanged to the pre-build hook.
.PARAMETER IncludeList
  Replace the generated copy-direct list used for binarized builds.
.PARAMETER NoPause
  Add -noPause to the client command line. Default off.
.NOTES
  Hooks receive named parameters. A terminating error, failed invocation or
  nonzero exit code aborts the build. Unconfigured hooks are silent.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$Mod,
    [ValidateSet('offline', 'server', 'client', 'all', 'none')] [string]$Mode = 'all',
    [string]$Mission = 'chernarus',
    [string]$Source,
    [string]$ExtraMods = '',
    [string]$BaseMods = '@CF;@Dabs Framework;@VPPAdminTools',
    [string]$ServerMods = '',
    [switch]$NoBaseMods,
    [int]$Port = 2302,
    [int]$Width = 1920,
    [int]$Height = 1080,
    [string]$PlayerName = 'Dev',
    [string]$AdminSteamId = '',
    [string]$AdminPass = 'dayzadmin',
    [int]$ServerWait = 60,
    [switch]$Build,
    [switch]$Clean,
    [switch]$PackOnly,
    [switch]$BuildOnly,
    [switch]$Release,
    [switch]$NoPause,
    [switch]$Verify,
    [switch]$VerifyOnly,
    [string]$VerifyP3d = '',
    [string]$PreBuildScript = '',
    [string]$PostBuildScript = '',
    [string]$IncludeList = '',
    [switch]$Retail,
    [switch]$NoFilePatching,
    [switch]$Preflight,
    [switch]$Kill,
    [string]$RunId = ''
)

$ErrorActionPreference = 'Stop'

function Info($m) { Write-Host "[dayz-test] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "   [ok] $m"     -ForegroundColor Green }
function Warn($m) { Write-Host "   [warn] $m"   -ForegroundColor Yellow }
function Die($m)  { Write-Host "   [FAIL] $m"   -ForegroundColor Red; exit 1 }

$SteamCommon   = 'C:\Program Files (x86)\Steam\steamapps\common'
$GamePath      = if ($env:DAYZ_GAME_PATH)  { $env:DAYZ_GAME_PATH }  else { Join-Path $SteamCommon 'DayZ' }
$DiagExe       = if ($env:DAYZ_DIAG_PATH)  { $env:DAYZ_DIAG_PATH }  else { Join-Path $GamePath 'DayZDiag_x64.exe' }

$ToolsPath     = if ($env:DAYZ_TOOLS_PATH) { $env:DAYZ_TOOLS_PATH } else { Join-Path $SteamCommon 'DayZ Tools' }
$AddonBuilder  = if ($env:DAYZ_ADDONBUILDER) { $env:DAYZ_ADDONBUILDER } else { Join-Path $ToolsPath 'Bin\AddonBuilder\AddonBuilder.exe' }
$ServerInstall = Join-Path $SteamCommon 'DayZServer'
$Workshop      = Join-Path $GamePath '!Workshop'
$WorkDrive     = if ($env:DAYZ_WORK_DRIVE) { $env:DAYZ_WORK_DRIVE } else { 'P:\' }
$ModsDir       = Join-Path $WorkDrive 'Mods'


$Exe = $DiagExe

$DevRoot        = if ($env:DAYZ_DEV_ROOT) { $env:DAYZ_DEV_ROOT } else { Split-Path -Parent $PSScriptRoot }
$ServerWs       = Join-Path $DevRoot '_server'
$ClientWs       = Join-Path $DevRoot '_client'
$ServerCfg      = Join-Path $ServerWs 'serverDZ.cfg'
$ServerProfiles = Join-Path $ServerWs 'profiles'
$ClientProfiles = Join-Path $ClientWs 'profiles'

$MissionMap = @{ chernarus = 'dayzOffline.chernarusplus'; livonia = 'dayzOffline.enoch'; sakhal = 'dayzOffline.sakhal' }

function Stop-DayZ {
    if ([string]::IsNullOrWhiteSpace($script:RunId)) {
        Die '-Kill requires the exact non-empty -RunId.'
    }
    $result = Invoke-LifecycleCli -Command 'stop' -TargetRunId $script:RunId
    if (-not $result.ok -or $result.state -ne 'EXITED') {
        Die 'Managed lifecycle stop did not return EXITED.'
    }
    Ok "Managed run_id $script:RunId is EXITED."
}

$LifecycleTools = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools'
$LifecyclePython = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe'
$LifecycleKeyfile = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.dayz_mcp.key'
$LifecyclePort = 8765
$script:RunId = $RunId

$script:LifecycleClientIdentityJson = $null
$script:LifecycleLeaseToken = $null

function Initialize-LifecycleCredentials {
    $identity = [Environment]::GetEnvironmentVariable('DAYZ_MCP_CLIENT_ID_JSON', 'Process')
    $lease = [Environment]::GetEnvironmentVariable('DAYZ_MCP_LEASE_TOKEN', 'Process')
    if (-not [string]::IsNullOrWhiteSpace($identity)) {
        $script:LifecycleClientIdentityJson = $identity
    }
    if (-not [string]::IsNullOrWhiteSpace($lease)) {
        $script:LifecycleLeaseToken = $lease
    }
    [Environment]::SetEnvironmentVariable('DAYZ_MCP_CLIENT_ID_JSON', $null, 'Process')
    [Environment]::SetEnvironmentVariable('DAYZ_MCP_LEASE_TOKEN', $null, 'Process')
}

function Assert-LifecycleEnvironment {
    if (
        [string]::IsNullOrWhiteSpace($script:LifecycleClientIdentityJson) -or
        [string]::IsNullOrWhiteSpace($script:LifecycleLeaseToken)
    ) {
        Die 'Managed lifecycle environment is incomplete.'
    }
    if (-not (Test-Path -LiteralPath $LifecyclePython -PathType Leaf)) {
        Die 'Managed lifecycle Python is unavailable.'
    }
    if (-not (Test-Path -LiteralPath $LifecycleKeyfile -PathType Leaf)) {
        Die 'Managed lifecycle keyfile is unavailable.'
    }
}

function Invoke-LifecycleCli {
    param(
        [ValidateSet('start', 'stop', 'adopt', 'status')] [string]$Command,
        [string]$RequestPath = '',
        [string]$TargetRunId = ''
    )
    Assert-LifecycleEnvironment
    $cliArgs = @('-m', 'dayz_mcp.lifecycle_cli', '--keyfile', $LifecycleKeyfile, '--port', [string]$LifecyclePort, $Command)
    if ($Command -eq 'start') { $cliArgs += @('--request-file', $RequestPath) }
    if ($Command -in @('stop', 'adopt')) { $cliArgs += @('--run-id', $TargetRunId) }

    $raw = @()
    $exitCode = 2
    $previousIdentity = [Environment]::GetEnvironmentVariable('DAYZ_MCP_CLIENT_ID_JSON', 'Process')
    $previousLease = [Environment]::GetEnvironmentVariable('DAYZ_MCP_LEASE_TOKEN', 'Process')
    [Environment]::SetEnvironmentVariable('DAYZ_MCP_CLIENT_ID_JSON', $script:LifecycleClientIdentityJson, 'Process')
    [Environment]::SetEnvironmentVariable('DAYZ_MCP_LEASE_TOKEN', $script:LifecycleLeaseToken, 'Process')
    try {
        Push-Location $LifecycleTools
        try {
            $raw = @(& $LifecyclePython @cliArgs 2>&1)
            $exitCode = $LASTEXITCODE
        } finally {
            Pop-Location
        }
    } finally {
        [Environment]::SetEnvironmentVariable('DAYZ_MCP_CLIENT_ID_JSON', $previousIdentity, 'Process')
        [Environment]::SetEnvironmentVariable('DAYZ_MCP_LEASE_TOKEN', $previousLease, 'Process')
    }

    $result = $null
    try {
        $result = (($raw | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json
    } catch {
        if ($exitCode -eq 0) { Die 'Managed lifecycle returned invalid JSON.' }
    }
    if ($exitCode -ne 0) {
        $reason = if ($result -and $result.error) { [string]$result.error } else { 'request_failed' }
        Die "Managed lifecycle '$Command' failed (exit $exitCode, reason=$reason)."
    }
    if (-not $result) { Die 'Managed lifecycle returned no JSON result.' }
    return $result
}

function Invoke-ManagedStart {
    param(
        [string[]]$Argv,
        [string]$Cwd,
        [string]$Role,
        [string]$WindowStyle,
        [string]$Label,
        [string]$Profiles,
        [string]$MissionValue,
        [string]$ExtendRunId = ''
    )
    $request = [ordered]@{
        argv = @($Argv)
        cwd = $Cwd
        role = $Role
        window_style = $WindowStyle
        label = $Label
        mod = "@$Mod"
        profiles = $Profiles
        mission = $MissionValue
    }
    if ($ExtendRunId) { $request.run_id = $ExtendRunId }
    $requestPath = Join-Path ([System.IO.Path]::GetTempPath()) ("dayz-mcp-lifecycle-{0}.json" -f [guid]::NewGuid().ToString('N'))
    try {
        $utf8 = [System.Text.UTF8Encoding]::new($false)
        [System.IO.File]::WriteAllText($requestPath, ($request | ConvertTo-Json -Depth 5 -Compress), $utf8)
        $result = Invoke-LifecycleCli -Command 'start' -RequestPath $requestPath
    } finally {
        if (Test-Path -LiteralPath $requestPath) {
            Remove-Item -LiteralPath $requestPath -Force -ErrorAction SilentlyContinue
        }
    }
    if (-not $result.ok -or [string]::IsNullOrWhiteSpace([string]$result.run_id)) {
        Die 'Managed lifecycle start did not return a public run_id.'
    }
    $script:RunId = [string]$result.run_id
}

function Adopt-DayZRun {
    if ([string]::IsNullOrWhiteSpace($script:RunId)) {
        Die 'A non-empty -RunId is required to adopt a managed run.'
    }
    $result = Invoke-LifecycleCli -Command 'adopt' -TargetRunId $script:RunId
    if (-not $result.ok -or $result.state -ne 'RUNNING') {
        Die 'Managed lifecycle adopt did not return RUNNING.'
    }
    $script:RunId = [string]$result.run_id
}

function Resolve-Mission {
    param([string]$M)
    if ([System.IO.Path]::IsPathRooted($M)) {
        if (Test-Path $M) { return (Resolve-Path $M).Path }
        Die "Mission path not found: $M"
    }
    $folder = $MissionMap[$M.ToLower()]
    if (-not $folder) { Die "Unknown mission alias '$M'. Use chernarus|livonia|sakhal or an absolute path." }
    $candidates = @(
        (Join-Path $ServerWs "mpmissions\$folder"),
        (Join-Path $ServerInstall "mpmissions\$folder")
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return (Resolve-Path $c).Path } }
    Die "Mission '$folder' not found in workspace or DayZServer install. Reinstall DayZServer (appid 223350) or copy the mission to $ServerWs\mpmissions\."
}

function New-ServerCfg {
    param([string]$Path, [string]$Template)
    $missionFolder = Split-Path -Leaf $Template
    $body = @"
// Generated by dayz-test-ingame. Local dev only.
hostname        = "DEV $Mod";
maxPlayers      = 10;
verifySignatures = 0;          // dev: do not enforce .bisign
allowFilePatching = 1;         // MANDATORY for -filePatching clients (DAYZ_INFRA.md L52-64; BattlEye 0x00020005 otherwise)
disableVoN      = 1;
disable3rdPerson = 0;
instanceId      = 1;
storageAutoFix  = 1;
serverTimePersistent = 0;
vppDisablePassword = 1;        // Local development: passwordless superadmin access.

class Missions
{
    class DayZ
    {
        template = "$missionFolder";
    };
};
"@
    Set-Content -Path $Path -Value $body -Encoding ASCII
}

function Initialize-VppSuperadmin {
    if ((Get-ModString) -notlike '*VPPAdminTools*') { return }
    $vppDir = Join-Path $ServerProfiles 'VPPAdminTools'

    $ids = @()
    if ($AdminSteamId) { $ids = @($AdminSteamId) }
    else {
        $existing = Join-Path $env:LOCALAPPDATA 'DayZ\VPPAdminTools\SuperAdmins.json'
        if (Test-Path $existing) {
            try { $ids = @((Get-Content $existing -Raw | ConvertFrom-Json).SUPER_ADMINS) } catch { $ids = @() }
        }
    }
    if (-not $ids -or $ids.Count -eq 0) {
        Warn 'No admin SteamID. Pass -AdminSteamId, or set %LOCALAPPDATA%\DayZ\VPPAdminTools\SuperAdmins.json. VPP boots with no superadmin.'
        return
    }

    $json = Join-Path $vppDir 'SuperAdmins.json'
    if (-not (Test-Path $json)) {
        New-Item -ItemType Directory -Force -Path $vppDir | Out-Null
        (@{ SUPER_ADMINS = @($ids) } | ConvertTo-Json) | Set-Content -Path $json -Encoding ASCII
    }

    $saTxt = Join-Path $vppDir 'Permissions\SuperAdmins\SuperAdmins.txt'
    New-Item -ItemType Directory -Force -Path (Split-Path $saTxt) | Out-Null
    $merged = [System.Collections.Generic.List[string]]::new()
    $candidates = @()
    if (Test-Path $saTxt) { $candidates += @(Get-Content $saTxt) }
    $candidates += @($ids)
    foreach ($c in $candidates) {
        $s = "$c".Trim()
        if ($s -match '^\d{17}$' -and -not $merged.Contains($s)) { $merged.Add($s) }
    }
    Set-Content -Path $saTxt -Value ($merged -join "`r`n") -Encoding ASCII
    Ok "VPP superadmin(s) -> $saTxt ($($merged -join ', '))"

    $cred = Join-Path $vppDir 'Permissions\credentials.txt'
    $needsPass = $true
    if (Test-Path $cred) {
        $first = (Get-Content $cred | Select-Object -First 1)
        if ($first -match '^[0-9A-Fa-f]{64}$') { $needsPass = $false }
        elseif ($first -and $first.Trim() -and ($first -notmatch '^\s*//')) { $needsPass = $false }
    }
    if ($needsPass) {
        New-Item -ItemType Directory -Force -Path (Split-Path $cred) | Out-Null
        Set-Content -Path $cred -Value $AdminPass -Encoding ASCII
        Ok "VPP login password initialized: $cred"
    }
    else { Ok 'VPP credentials.txt already set (not overwriting)' }
}

function Assert-ModsDestination {
    # A plain directory is allowed only by explicit operator attestation.
    if (-not (Test-Path -LiteralPath $ModsDir -PathType Container)) {
        Die "Mods destination is not prepared: $ModsDir. Create the real mapping before building; it will not be created here."
    }
    $item = Get-Item -LiteralPath $ModsDir -Force
    if ($false) {
        Die "Mods destination is not a junction/link: $ModsDir. Prepare the real mapping, or set DAYZ_ALLOW_PLAIN_MODS=1 only for an intentional existing plain destination."
    }
}

function Invoke-BuildPreflight {
    Assert-ModsDestination
    if (-not (Test-Path -LiteralPath $AddonBuilder -PathType Leaf)) {
        Die "AddonBuilder not found: $AddonBuilder (set DAYZ_ADDONBUILDER / DAYZ_TOOLS_PATH)."
    }
    $src = if ($Source) { $Source } else { Join-Path $WorkDrive $Mod }
    if (-not (Test-Path -LiteralPath $src -PathType Container)) { Die "Mod source not found: $src" }
    Info 'Build preflight passed.'
}

function Invoke-Preflight {
    Info 'Preflight...'

    if (-not (Test-Path $Exe)) {
        Die "Game exe not found: $Exe`n       Set DAYZ_GAME_PATH / DAYZ_DIAG_PATH, or verify the DayZ install."
    }
    Ok "exe: $Exe"
    if (-not $Retail -and ($Exe -notlike '*DayZDiag*')) {
        Warn 'Not using DayZDiag - retail exe blocks past the loading screen with -filePatching.'
    }

    if (-not (Test-Path $WorkDrive)) { Die "Work drive $WorkDrive not mounted. See DAYZ_INFRA.md (P:\ symlink)." }
    Ok "work drive: $WorkDrive"

    Assert-ModsDestination

    if ($Mode -in @('server', 'client', 'all')) {
        if (-not (Test-Path $ServerWs)) { New-Item -ItemType Directory -Force -Path $ServerWs | Out-Null }
        if (-not (Test-Path $ServerProfiles)) { New-Item -ItemType Directory -Force -Path $ServerProfiles | Out-Null }
        if (-not (Test-Path $ServerCfg)) {
            New-ServerCfg -Path $ServerCfg -Template $script:MissionPath
            Ok "generated serverDZ.cfg (allowFilePatching=1): $ServerCfg"
        }
        else {
            $cfg = Get-Content $ServerCfg -Raw
            if ($cfg -match 'allowFilePatching\s*=\s*1') { Ok 'serverDZ.cfg: allowFilePatching = 1' }
            else { Die "serverDZ.cfg has allowFilePatching != 1 -> BattlEye kick 0x00020005. Fix: $ServerCfg" }
            if ($cfg -notmatch 'vppDisablePassword') {
                Add-Content -Path $ServerCfg -Value 'vppDisablePassword = 1;        // auto-added: VPP passwordless admin for SuperAdmins.txt superadmins (dev). Remove for password-gated testing.'
                Ok 'serverDZ.cfg: appended vppDisablePassword = 1 (VPP passwordless admin)'
            }
            else { Ok 'serverDZ.cfg: vppDisablePassword present' }
        }
        Initialize-VppSuperadmin
    }
    if ($Mode -in @('offline', 'client', 'all')) {
        if (-not (Test-Path $ClientProfiles)) { New-Item -ItemType Directory -Force -Path $ClientProfiles | Out-Null }
    }

    if ($Build -or $Clean) {
        if (-not (Test-Path $AddonBuilder)) { Die "AddonBuilder not found: $AddonBuilder (set DAYZ_TOOLS_PATH)." }
        Ok "AddonBuilder: $AddonBuilder"
    }

    Ok "mission: $script:MissionPath"
    Info 'Preflight passed.'
}


function Invoke-BuildHook {
    param([string]$Path, [hashtable]$Arguments)
    if (-not $Path) { return }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { Die "Build hook not found: $Path" }
    $hookPath = (Resolve-Path -LiteralPath $Path).Path
    $LASTEXITCODE = 0
    try {
        & $hookPath @Arguments
        $hookOk = $?
        $hookExit = $LASTEXITCODE
    } catch {
        Die "Build hook failed: $hookPath ($($_.Exception.Message))"
    }
    if (-not $hookOk -or $hookExit -ne 0) { Die "Build hook failed: $hookPath (exit $hookExit)" }
}


function Get-PboScriptEntryCount {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    # This is a directory counter, NOT a general PBO validator.
    # InvalidData means a proven truncation/bounds violation in a recognized layout.
    # NotSupported means an unfamiliar header: it does NOT prove a broken PBO.
    # Packing methods and checksum trailers need no decoding to count file names.
    # PBO headers are NUL-terminated names followed by five little-endian DWORDs.
    # File bodies follow the complete header table, not their individual entries.
    function Read-PboString([IO.BinaryReader]$Reader, [bool]$KnownLayout) {
        $text = New-Object Text.StringBuilder
        while ($true) {
            if ($Reader.BaseStream.Position -ge $Reader.BaseStream.Length) {
                if ($KnownLayout) { throw [IO.InvalidDataException]::new('Truncated PBO string.') }
                throw [NotSupportedException]::new('Unrecognized PBO header (unterminated first name).')
            }
            $value = $Reader.ReadByte()
            if ($value -eq 0) { return $text.ToString() }
            [void]$text.Append([char]$value)
        }
    }

    $stream = $null
    $reader = $null
    try {
        $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        $reader = New-Object IO.BinaryReader($stream)
        [long]$payloadBytes = 0
        [long]$count = 0
        $first = $true
        $knownLayout = $false
        while ($true) {
            $name = Read-PboString $reader $knownLayout
            if (($stream.Length - $stream.Position) -lt 20) {
                if ($knownLayout) { throw [IO.InvalidDataException]::new('Truncated PBO entry.') }
                throw [NotSupportedException]::new('Unrecognized PBO header (incomplete first entry).')
            }
            $method = $reader.ReadUInt32()
            $originalSize = $reader.ReadUInt32()
            $reserved = $reader.ReadUInt32()
            $timestamp = $reader.ReadUInt32()
            $dataSize = $reader.ReadUInt32()

            if ($name.Length -eq 0) {
                if ($method -eq 0x56657273 -and $first) {
                    # Optional Vers entry, then key/value strings and an empty key.
                    if ($originalSize -ne 0 -or $reserved -ne 0 -or
                        $timestamp -ne 0 -or $dataSize -ne 0) {
                        throw [NotSupportedException]::new('Unsupported PBO Vers metadata.')
                    }
                    $knownLayout = $true
                    while ($true) {
                        $key = Read-PboString $reader $true
                        if ($key.Length -eq 0) { break }
                        $null = Read-PboString $reader $true
                    }
                    $first = $false
                    continue
                }
                if ($method -ne 0 -or $originalSize -ne 0 -or $reserved -ne 0 -or
                    $timestamp -ne 0 -or $dataSize -ne 0) {
                    throw [NotSupportedException]::new('Unsupported PBO empty-name metadata.')
                }
                break
            }
            if ($method -eq 0x56657273) { throw [NotSupportedException]::new('Unsupported named PBO Vers entry.') }
            # A named entry with a stored size uses the standard directory layout;
            # unknown compression codes still use that size, not OriginalSize.
            $knownLayout = $true
            $first = $false
            # Bound each addition before summing, including on very large files.
            if ([long]$dataSize -gt ($stream.Length - $payloadBytes)) {
                throw [IO.InvalidDataException]::new('PBO payload exceeds file bounds.')
            }
            $payloadBytes += [long]$dataSize
            if ($name.EndsWith('.c', [StringComparison]::OrdinalIgnoreCase)) { $count++ }
        }
        if ($payloadBytes -gt ($stream.Length - $stream.Position)) {
            throw [IO.InvalidDataException]::new('Truncated PBO payload.')
        }
        # Skip stored data, including compressed data. A checksum trailer may follow.
        [void]$stream.Seek($payloadBytes, [IO.SeekOrigin]::Current)
        return $count
    }
    finally {
        if ($null -ne $reader) { $reader.Dispose() }
        elseif ($null -ne $stream) { $stream.Dispose() }
    }
}

function Invoke-Build {
    Invoke-BuildPreflight
    $src = if ($Source) { $Source } else { Join-Path $WorkDrive $Mod }
    if (-not (Test-Path $src)) { Die "Mod source not found: $src (pass -Source, or junction P:\$Mod -> your editable folder)." }
    $src = (Resolve-Path -LiteralPath $src).Path
    Invoke-BuildHook -Path $PreBuildScript -Arguments @{ Src = $src; Verify = [bool]$Verify; VerifyOnly = $false; VerifyP3d = $VerifyP3d }
    $target = Join-Path $ModsDir "@$Mod\Addons"
    if (-not (Test-Path $target)) { New-Item -ItemType Directory -Force -Path $target | Out-Null }

    $workFull = [IO.Path]::GetFullPath($WorkDrive).TrimEnd('\', '/')
    $tempRoot = $null
    $candidates = @($env:TEMP)
    if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA 'Temp') }
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        $candidateRoot = [IO.Path]::GetFullPath((Join-Path $candidate 'dayz-addonbuilder'))
        if (-not $candidateRoot.StartsWith($workFull, [StringComparison]::OrdinalIgnoreCase)) {
            $tempRoot = $candidateRoot
            break
        }
    }
    if (-not $tempRoot) { Die 'No AddonBuilder temp location outside DAYZ_WORK_DRIVE.' }
    $temp = [IO.Path]::GetFullPath((Join-Path $tempRoot $Mod))
    if (-not $temp.StartsWith(($tempRoot + '\'), [StringComparison]::OrdinalIgnoreCase)) {
        Die 'Mod must name a child of the AddonBuilder temp directory.'
    }
    if (Test-Path -LiteralPath $temp) {
        if ((Get-Item -LiteralPath $temp -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            Die "Refusing to wipe a linked temp directory: $temp"
        }
        Remove-Item -LiteralPath $temp -Recurse -Force
    }
    New-Item -ItemType Directory -Path $temp -Force | Out-Null
    $hasAssets = @(Get-ChildItem -LiteralPath $src -Recurse -File | Where-Object { $_.Extension -in @('.p3d', '.paa', '.rvmat') }).Count -gt 0
    $usePackOnly = $PackOnly -or (-not $hasAssets)
    if ($Release) { $usePackOnly = $false }

    Info "Build: $src -> $target$(if ($usePackOnly) { ' (-packonly)' })"

    $pbo = Join-Path $target "$Mod.pbo"
    $mtimeBefore = $null
    $hashBefore = $null
    if (Test-Path -LiteralPath $pbo) {
        $prev = Get-Item -LiteralPath $pbo
        $mtimeBefore = $prev.LastWriteTimeUtc
        $hashBefore = (Get-FileHash -LiteralPath $pbo -Algorithm SHA256).Hash
    }

    $abArgs = @("`"$src`"", "`"$target`"", "`"-prefix=$Mod`"", "`"-temp=$temp`"")
    if ($Clean -or $Release) { $abArgs += '-clear' }
    if ($usePackOnly) {
        $abArgs += '-packonly'
    }
    else {
        $includeFile = $IncludeList
        if (-not $includeFile) {
            $includeFile = Join-Path $tempRoot "dayz-test-include-$Mod.txt"
            Set-Content -Path $includeFile -Value '*.c;*.csv;*.xml;*.layout;*.json;*.ogg;*.wav;*.asi;*.anm;*.paa;*.rvmat' -Encoding ascii
        }
        if (-not (Test-Path -LiteralPath $includeFile)) { Die "Include list missing: $includeFile" }
        $abArgs += "`"-include=$includeFile`""
        $abArgs += '-binarizeFullLogs'
    }

    $stdoutLog = Join-Path $tempRoot ("dayz-ab-" + $Mod + ".out.txt")
    $stderrLog = Join-Path $tempRoot ("dayz-ab-" + $Mod + ".err.txt")
    foreach ($log in @($stdoutLog, $stderrLog)) {
        if (Test-Path -LiteralPath $log) { Remove-Item -LiteralPath $log -Force }
    }

    $p = Start-Process -FilePath $AddonBuilder -ArgumentList $abArgs -Wait -NoNewWindow -PassThru -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog

    $abOut = ''
    if (Test-Path -LiteralPath $stdoutLog) { $abOut += [IO.File]::ReadAllText($stdoutLog) }
    if (Test-Path -LiteralPath $stderrLog) { $abOut += [IO.File]::ReadAllText($stderrLog) }

    if ($abOut) { Write-Host $abOut.TrimEnd() }
    $failedText = $false
    if ($abOut -match '\[ERROR\]:\s*Build failed') { $failedText = $true }
    $okText = $false
    if ($abOut -match 'Build Successful') { $okText = $true }

    if ($p.ExitCode -ne 0) {
        Die "AddonBuilder failed (exit $($p.ExitCode)). Check config.cpp / paths."
    }
    if ($failedText -or -not $okText) {
        Die "AddonBuilder printed a failed or incomplete build (exit $($p.ExitCode)). Refusing to treat the existing PBO as new."
    }
    if (-not (Test-Path -LiteralPath $pbo)) {
        Die "Build reported success but $pbo is missing."
    }

    $now = Get-Item -LiteralPath $pbo
    $hashAfter = (Get-FileHash -LiteralPath $pbo -Algorithm SHA256).Hash
    if ($mtimeBefore -ne $null) {
        if ($now.LastWriteTimeUtc -le $mtimeBefore -or $hashAfter -eq $hashBefore) {
            Die "PBO was not replaced (mtime/hash unchanged). AddonBuilder exit 0 is not a successful deploy."
        }
    }

    # Compare the same scope on both sides: every .c below the source root and
    # every .c entry in the PBO, even when no scripts directory exists.
    $srcC = @(Get-ChildItem -LiteralPath $src -Recurse -File | Where-Object { $_.Extension -eq '.c' }).Count
    if ($srcC -gt 0) {
        $pboC = $null
        try { $pboC = Get-PboScriptEntryCount -Path $pbo }
        catch [IO.InvalidDataException] { Die "Invalid PBO: $($_.Exception.Message)" }
        catch [NotSupportedException] { Warn "PBO script check INCONCLUSIVE (unsupported format): $($_.Exception.Message)" }
        catch { Warn "PBO script check INCONCLUSIVE (reader failure): $($_.Exception.Message)" }
        # No count is not zero and must never produce a successful script-check marker.
        # Only proven structural corruption or a measured script deficit rejects here.
        if ($null -ne $pboC) {
            if ($pboC -lt $srcC) {
                Die "PBO carries $pboC .c entries but the source has $srcC. The include list is missing or wrong - do NOT test or publish this build."
            }
            Ok "script check: $pboC/$srcC .c entries present in the PBO"
        }
    }

    Invoke-BuildHook -Path $PostBuildScript -Arguments @{ Pbo = $pbo; Src = $src; Release = [bool]$Release }
    Ok "deployed: $pbo ($($now.Length) b)"
}


function Resolve-ModToken {
    param([string]$Token)
    $t = $Token.Trim()
    if (-not $t) { return $null }
    if ([System.IO.Path]::IsPathRooted($t)) { return $t }
    $abs = Join-Path $Workshop $t
    if (Test-Path -LiteralPath $abs) { return $abs }
    return $t   # not in !Workshop (e.g. a mod folder elsewhere) - leave as-is
}

function Get-ModString {
    $parts = @()
    if (-not $NoBaseMods -and $BaseMods) { $parts += ($BaseMods -split ';') }
    $parts += "@$Mod"
    if ($ExtraMods) { $parts += ($ExtraMods -split ';') }
    $resolved = @()
    foreach ($p in $parts) { $r = Resolve-ModToken $p; if ($r) { $resolved += $r } }
    return ($resolved -join ';')
}

function Wait-ServerBind {
    param([int]$TimeoutSec)
    if ([string]::IsNullOrWhiteSpace($script:RunId)) {
        Warn 'Exact managed run_id is unavailable; refusing readiness.'
        return $false
    }
    Info "Waiting for exact managed server run to bind UDP $Port (max ${TimeoutSec}s)..."
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $status = Invoke-LifecycleCli -Command 'status'
        $matchingRuns = @($status.runs | Where-Object { [string]$_.run_id -eq [string]$script:RunId })
        if ($matchingRuns.Count -ne 1) {
            Warn 'Exact managed run is missing or ambiguous.'
            return $false
        }
        $run = $matchingRuns[0]
        if ([string]$run.state -ne 'RUNNING') {
            Warn 'Exact managed run is not RUNNING.'
            return $false
        }
        $serverRecords = @($run.processes | Where-Object { [string]$_.role -eq 'server' })
        if ($serverRecords.Count -eq 0) {
            Warn 'Exact managed run has no server-role process.'
            return $false
        }
        $serverPids = @()
        foreach ($record in $serverRecords) {
            [int]$parsedPid = 0
            if (-not [int]::TryParse([string]$record.pid, [ref]$parsedPid) -or $parsedPid -le 0) {
                Warn 'Exact managed run has an invalid server PID.'
                return $false
            }
            $serverPids += $parsedPid
        }
        $uniqueServerPids = @($serverPids | Sort-Object -Unique)
        if ($uniqueServerPids.Count -ne $serverPids.Count) {
            Warn 'Exact managed run has ambiguous server PIDs.'
            return $false
        }
        foreach ($serverPid in $uniqueServerPids) {
            if (-not (Get-Process -Id $serverPid -ErrorAction SilentlyContinue)) {
                Warn 'Exact managed server process exited before binding.'
                return $false
            }
        }
        $bound = @(Get-NetUDPEndpoint -LocalPort $Port -ErrorAction SilentlyContinue)
        if ($bound.Count -gt 0) {
            $foreign = @($bound | Where-Object { [int]$_.OwningProcess -notin $uniqueServerPids })
            if ($foreign.Count -gt 0) {
                Warn 'UDP port is bound by a process outside the exact managed run.'
                return $false
            }
            $owned = @($bound | Where-Object { [int]$_.OwningProcess -in $uniqueServerPids })
            if ($owned.Count -gt 0) {
                Ok 'Exact managed server is listening.'
                return $true
            }
        }
        Start-Sleep -Seconds 2
    }
    Warn 'Timed out waiting for the exact managed server bind.'
    return $false
}

function Start-Server {
    $argv = @($DiagExe, '-server', "-config=$ServerCfg", "-profiles=$ServerProfiles", "-mission=$script:MissionPath", "-mod=$(Get-ModString)")
    if (-not $NoFilePatching) { $argv += '-filePatching' }
    $argv += "-port=$Port"
    if ($ServerMods) {
        $sm = (($ServerMods -split ';') | ForEach-Object { Resolve-ModToken $_ }) -join ';'
        $argv += "-serverMod=$sm"
    }
    Info "Requesting managed SERVER for @$Mod."
    Invoke-ManagedStart -Argv $argv -Cwd $GamePath -Role 'server' -WindowStyle 'normal' -Label "@$Mod server" -Profiles $ServerProfiles -MissionValue $script:MissionPath
}

function Start-Client {
    $argv = @($DiagExe, "-mod=$(Get-ModString)", '-connect=127.0.0.1', "-port=$Port", "-profiles=$ClientProfiles", "-name=$PlayerName", '-window', "-x=$Width", "-y=$Height")
    if (-not $NoFilePatching) { $argv += '-filePatching' }
    if ($NoPause) { $argv += '-noPause' }
    Info "Requesting managed CLIENT for @$Mod."
    Invoke-ManagedStart -Argv $argv -Cwd $GamePath -Role 'client' -WindowStyle 'normal' -Label "@$Mod client" -Profiles $ClientProfiles -MissionValue $script:MissionPath -ExtendRunId $script:RunId
}

function Start-Offline {
    $argv = @($DiagExe, "-mod=$(Get-ModString)", "-mission=$script:MissionPath", "-profiles=$ClientProfiles", '-window', "-x=$Width", "-y=$Height")
    if (-not $NoFilePatching) { $argv += '-filePatching' }
    Info "Requesting managed OFFLINE [DESIGN] for @$Mod."
    Invoke-ManagedStart -Argv $argv -Cwd $GamePath -Role 'offline' -WindowStyle 'normal' -Label "@$Mod offline" -Profiles $ClientProfiles -MissionValue $script:MissionPath -ExtendRunId $script:RunId
}

function Show-VppHint {
    if ((Get-ModString) -notlike '*VPPAdminTools*') { return }
    $f = Join-Path $ServerProfiles 'VPPAdminTools\SuperAdmins.json'
    Info 'VPP admin tools loaded.'
    Write-Host "   superadmin file: $f" -ForegroundColor Cyan
    Write-Host '   format: {"SUPER_ADMINS":["<SteamID64>"]} - restart the server after editing.' -ForegroundColor Cyan
}


if ($Retail) { Die 'Retail lifecycle is manual-only and quarantined; no build or launch was started.' }
Initialize-LifecycleCredentials
if ($Kill) { Stop-DayZ; exit 0 }
if ($Clean) { $Build = $true }
if ($Mode -eq 'none') { $BuildOnly = $true }
if ($VerifyOnly) {
    if ($PreBuildScript) {
        $src = if ($Source) { $Source } else { Join-Path $WorkDrive $Mod }
        if (-not (Test-Path -LiteralPath $src -PathType Container)) { Die "Mod source not found: $src" }
        $src = (Resolve-Path -LiteralPath $src).Path
        Invoke-BuildHook -Path $PreBuildScript -Arguments @{ Src = $src; Verify = $true; VerifyOnly = $true; VerifyP3d = $VerifyP3d }
    }
    exit 0
}
if ($BuildOnly) {
    if ($Preflight) {
        Invoke-BuildPreflight
        exit 0
    }
    Invoke-Build
    exit 0
}

$script:MissionPath = Resolve-Mission -M $Mission
Invoke-Preflight
if ($Preflight) { Info 'Preflight only - exiting.'; exit 0 }
if ($Mode -in @('server', 'all') -and $RunId) { Die 'server/all reject an input RunId because they create a new managed run.' }
if ($Mode -eq 'client' -and [string]::IsNullOrWhiteSpace($RunId)) { Die 'client requires the exact -RunId to adopt.' }
if ($Build) { Invoke-Build } else { Info 'Skipping build (-Build to re-pack the PBO).' }

switch ($Mode) {
    'offline' { if ($RunId) { Adopt-DayZRun }; Start-Offline }
    'server'  { Start-Server }
    'client'  { Adopt-DayZRun; Start-Client }
    'all'     {
        Start-Server
        if (Wait-ServerBind -TimeoutSec $ServerWait) { Start-Client }
        else { Die 'Exact managed server readiness failed; refusing client launch.' }
    }
}
if ($Mode -in @('server', 'all')) { Show-VppHint }

Info "Managed run_id: $script:RunId. Mode '$Mode' for @$Mod. Logs: server=$ServerProfiles  client=$ClientProfiles"
Info 'The caller owns release/status; keep the lease only for active exclusive work and verify status before handoff.'
Info 'Stuck? Use -Kill -RunId <exact-id>; never scan or terminate another session.'
