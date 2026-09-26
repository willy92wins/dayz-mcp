param([Parameter(Mandatory = $true)][string]$Script)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$workRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '_work'))
$runRoot = Join-Path $workRoot ([Guid]::NewGuid().ToString('N').Substring(0, 12))
$passed = 0
$failed = 0
$scenarios = @(
    @{ Id = 'S1'; Text = 'Build Successful'; Code = 0; Write = $true; Prior = $true; Success = $true },
    @{ Id = 'S2'; Text = '[ERROR]: Build failed'; Code = 0; Write = $false; Prior = $true; Success = $false },
    @{ Id = 'S3'; Text = '[ERROR]: Build failed'; Code = 0; Write = $true; Prior = $true; Success = $false },
    @{ Id = 'S4'; Text = 'Build Successful'; Code = 0; Write = $false; Prior = $true; Success = $false },
    @{ Id = 'S5'; Text = ''; Code = 0; Write = $false; Prior = $true; Success = $false },
    @{ Id = 'S6'; Text = 'Build Successful'; Code = 3; Write = $true; Prior = $true; Success = $false },
    @{ Id = 'S7'; Text = 'Build Successful'; Code = 0; Write = $true; Prior = $false; Success = $true },
    @{ Id = 'S8'; Text = 'Build Successful'; Code = 0; Write = $true; Prior = $true; Success = $true },
    @{ Id = 'S9'; Text = 'Build Successful'; Code = 0; Write = $true; Prior = $true; Success = $false },
    @{ Id = 'S10'; Text = 'Build Successful'; Code = 0; Write = $true; Prior = $true; Success = $true },
    @{ Id = 'S11'; Text = 'Build Successful'; Code = 0; Write = $true; Prior = $true; Success = $false },
    @{ Id = 'S12'; Text = 'Build Successful'; Code = 0; Write = $true; Prior = $true; Success = $false },
    @{ Id = 'S13'; Text = 'Build Successful'; Code = 0; Write = $true; Prior = $true; Success = $false },
    @{ Id = 'S14'; Text = 'Build Successful'; Code = 0; Write = $true; Prior = $true; Success = $true },
    @{ Id = 'S15'; Text = 'Build Successful'; Code = 0; Write = $true; Prior = $true; Success = $false },
    @{ Id = 'S16'; Text = 'Build Successful'; Code = 0; Write = $true; Prior = $true; Success = $true }
)


function Write-FixtureBytes([object[]]$Values) {
    $path = [string]$Values[0]
    $bytes = [byte[]]$Values[1]
    [IO.File]::WriteAllBytes($path, $bytes)
    if ((Get-Item -LiteralPath $path).Length -ne $bytes.Length -or
        [Convert]::ToBase64String([IO.File]::ReadAllBytes($path)) -cne [Convert]::ToBase64String($bytes)) {
        throw ('Fixture write/readback mismatch: ' + $path)
    }
}
function Write-FixtureText([object[]]$Values) {
    Write-FixtureBytes @([string]$Values[0], $Values[2].GetBytes([string]$Values[1]))
}

# Emit an uncompressed PBO with one directory entry and an end marker.
function New-FixturePbo([string]$EntryName) {
    $stream = New-Object IO.MemoryStream
    $writer = New-Object IO.BinaryWriter($stream)
    try {
        # A payload decoy must not count as a directory entry.
        $data = [Text.Encoding]::ASCII.GetBytes("// payload decoy.c`0")
        $writer.Write([Text.Encoding]::ASCII.GetBytes($EntryName))
        $writer.Write([byte]0)
        foreach ($value in @([uint32]0, [uint32]$data.Length, [uint32]0, [uint32]0, [uint32]$data.Length)) {
            $writer.Write([uint32]$value)
        }
        $writer.Write([byte]0)
        for ($i = 0; $i -lt 5; $i++) { $writer.Write([uint32]0) }
        $writer.Write($data)
        $writer.Flush()
        return ,$stream.ToArray()
    }
    finally { $writer.Dispose(); $stream.Dispose() }
}

# Removal is limited to an explicitly checked descendant of this workspace's _work.
function Remove-FixtureTree([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($workRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Unsafe cleanup target'
    }
    if (Test-Path -LiteralPath $full) {
        Remove-Item -LiteralPath $full -Recurse -Force
    }
}

function Get-ByteIdentity([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '<missing>' }
    return [Convert]::ToBase64String([IO.File]::ReadAllBytes($Path))
}

function Test-TempArgument([string]$Log, [string]$Drive, [string]$Cwd) {
    if (-not (Test-Path -LiteralPath $Log -PathType Leaf)) { throw 'C1: argument log missing' }
    $lines = @(Get-Content -LiteralPath $Log)
    if ($lines.Count -eq 0) { throw 'C1: argument log empty' }
    foreach ($line in $lines) {
        # Accept -temp="path", "-temp=path", and unquoted paths without spaces.
        $tokens = @([regex]::Matches($line, '(?:[^\s"]+|"[^"]*")+') | ForEach-Object {
            $_.Value.Replace('"', '')
        })
        $temps = @($tokens | Where-Object { $_.StartsWith('-temp=', [StringComparison]::OrdinalIgnoreCase) })
        if ($temps.Count -ne 1) { throw 'C1: expected exactly one -temp= argument per invocation' }
        $value = $temps[0].Substring(6)
        if ([string]::IsNullOrWhiteSpace($value)) { throw 'C1: empty -temp= path' }
        $absolute = $value
        if (-not [IO.Path]::IsPathRooted($absolute)) { $absolute = Join-Path $Cwd $absolute }
        $absolute = [IO.Path]::GetFullPath($absolute)
        if ($value.StartsWith($Drive, [StringComparison]::OrdinalIgnoreCase) -or
            $absolute.StartsWith($Drive, [StringComparison]::OrdinalIgnoreCase)) {
            throw ('C1: temp is on DAYZ_WORK_DRIVE: ' + $value)
        }
    }
}

foreach ($case in $scenarios) {
    $root = Join-Path $runRoot $case.Id
    $savedEnv = @{}
    $process = $null
    $junctionPath = $null
    $job = [IntPtr]::Zero
    $exitCode = '<not-started>'
    $stdout = ''
    $observed = 'not-run'
    $reason = $null
    $expected = 'rejection'
    if ($case.Success) { $expected = 'success' }
    $stdoutPath = Join-Path $root 'stdout.txt'
    $stderrPath = Join-Path $root 'stderr.txt'
    try {
        # Resolve the subject as a file, but never read its contents.
        $subject = (Resolve-Path -LiteralPath $Script).ProviderPath
        if (-not (Test-Path -LiteralPath $subject -PathType Leaf)) { throw 'Subject is not a file' }
        $mod = 'AF59Fixture'
        $layout = Join-Path $root 'af59'
        $dev = Join-Path $layout ('dev\' + $mod + '_dev')
        $drive = Join-Path $layout 'P'
        $source = Join-Path $drive $mod
        $addons = Join-Path $drive ('Mods\@' + $mod + '\Addons')
        $pbo = Join-Path $addons ($mod + '.pbo')
        $abDir = Join-Path $layout 'ab'
        $stub = Join-Path $abDir 'AddonBuilder.cmd'
        $temp = Join-Path $root 'tmp out'
        $cwd = Join-Path $root 'cwd'
        $argLog = Join-Path $root 'arguments.log'
        $payload = Join-Path $root 'new payload.bin'
        $sentinel = Join-Path $temp ('dayz-addonbuilder\' + $mod + '\nested\stale.bin')
        $wipeLog = Join-Path $root 'wipe.log'
        $sibling = Join-Path $temp 'dayz-addonbuilder\other-mod\keep.bin'
        $preparedAddons = $addons
        if ($case.Id -eq 'S16') { $preparedAddons = Join-Path $layout ('mapped\@' + $mod + '\Addons') }
        foreach ($dir in @((Join-Path $dev 'tools'), (Join-Path $dev '_server'),
            (Join-Path $dev '_client'), (Join-Path $source 'scripts\5_Mission'),
            $preparedAddons, $abDir, $temp, $cwd)) {
            $null = New-Item -ItemType Directory -Path $dir -Force
        }
        if ($case.Id -eq 'S16') {
            $junctionPath = Join-Path $drive 'Mods'
            $mapping = Join-Path $layout 'mapped'
            $null = New-Item -ItemType Junction -Path $junctionPath -Target $mapping
            $link = Get-Item -LiteralPath $junctionPath -Force
            if (-not ($link.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
                [IO.Path]::GetFullPath([string]$link.Target[0]) -ne [IO.Path]::GetFullPath($mapping)) {
                throw 'R4: fixture junction was not established'
            }
        }
        Write-FixtureText ((Join-Path $source 'config.cpp'), 'class CfgPatches { class AF59Fixture { requiredAddons[] = {}; }; };', [Text.Encoding]::ASCII)
        Write-FixtureText ((Join-Path $source 'scripts\5_Mission\dummy.c'), '// Fixture source only.', [Text.Encoding]::ASCII)
        Write-FixtureBytes @($stub, [IO.File]::ReadAllBytes((Join-Path $PSScriptRoot 'stub-addonbuilder.cmd')))
        $oldBytes = [byte[]]@(0, 255, 13, 10, 65, 70, 53, 57, 1)
        $entryName = 'scripts\5_Mission\dummy.c'
        if ($case.Id -eq 'S9') { $entryName = 'config.cpp' }
        $newBytes = New-FixturePbo $entryName
        if ($case.Id -eq 'S10') {
            # Deliberately unsupported metadata, NOT claimed to be a valid real PBO.
            $header = New-Object byte[] 21
            [BitConverter]::GetBytes([uint32]0x56657273).CopyTo($header, 1)
            $header[5] = 1
            $newBytes = [byte[]]($header + $newBytes)
        }
        if ($case.Id -eq 'S11') {
            # A standard, recognized header still promises the original payload size.
            $newBytes = [byte[]]$newBytes[0..($newBytes.Length - 2)]
        }
        if ($case.Id -eq 'S12') {
            Write-FixtureText ((Join-Path $source 'outside.c'), '// Also required.', [Text.Encoding]::ASCII)
        }
        if ($case.Id -eq 'S13') {
            Remove-FixtureTree (Join-Path $source 'scripts')
            Write-FixtureText ((Join-Path $source 'outside.c'), '// Only script.', [Text.Encoding]::ASCII)
            $newBytes = New-FixturePbo 'config.cpp'
        }
        if ($case.Id -eq 'S14') {
            # Frozen, untouched AddonBuilder -packonly artefact. See fixtures/README.md.
            $fixture = Join-Path $PSScriptRoot 'fixtures\DayZ_MCP_fence_DCC8730F.pbo'
            $hash = (Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash
            if ($hash -ne 'DCC8730FEB98FF2A4F7C203075D799428C53A669360E3499311AA3B4D31AFED3') {
                throw 'R3: real fixture identity mismatch'
            }
            $newBytes = [IO.File]::ReadAllBytes($fixture)
            # The frozen directory inventory independently records nine .c entries.
            for ($n = 2; $n -le 9; $n++) {
                Write-FixtureText ((Join-Path $source ('scripts\5_Mission\fixture' + $n + '.c')), '// Count fixture.', [Text.Encoding]::ASCII)
            }
        }
        $valveLog = Join-Path $root 'valve.log'
        if ($case.Id -eq 'S16') {
            $stubBody = [IO.File]::ReadAllText($stub)
            $probe = '@if defined DAYZ_ALLOW_PLAIN_MODS (echo set>"%AF59_VALVE_LOG%") else (echo absent>"%AF59_VALVE_LOG%")'
            Write-FixtureText ($stub, ($probe + "`r`n" + $stubBody), [Text.Encoding]::ASCII)
        }
        if ($case.Id -eq 'S8') {
            foreach ($file in @($sentinel, $sibling)) {
                $null = New-Item -ItemType Directory -Path (Split-Path $file) -Force
                Write-FixtureText ($file, 'stale sentinel', [Text.Encoding]::ASCII)
            }
            # Observe the stale file at builder entry, not merely after deployment.
            $stubBody = [IO.File]::ReadAllText($stub)
            $probe = '@if exist "%AF59_TEMP_SENTINEL%" (echo present>"%AF59_WIPE_LOG%") else (echo absent>"%AF59_WIPE_LOG%")'
            Write-FixtureText ($stub, ($probe + "`r`n" + $stubBody), [Text.Encoding]::ASCII)
        }
        Write-FixtureBytes ($payload, $newBytes)
        if ($case.Prior) {
            Write-FixtureBytes ($pbo, $oldBytes)
            [IO.File]::SetLastWriteTimeUtc($pbo, [DateTime]::UtcNow.AddHours(-2))
        }
        $before = Get-ByteIdentity $pbo
        $writeFlag = '0'
        if ($case.Write) { $writeFlag = '1' }
        $overrides = @{
            DAYZ_ALLOW_PLAIN_MODS = '1'
            AF59_VALVE_LOG = $valveLog
            AF59_TEMP_SENTINEL = $sentinel; AF59_WIPE_LOG = $wipeLog
            DAYZ_DEV_ROOT = $dev; DAYZ_WORK_DRIVE = $drive; DAYZ_ADDONBUILDER = $stub
            DAYZ_GAME_PATH = (Join-Path $root 'absent game')
            DAYZ_DIAG_PATH = (Join-Path $root 'absent diag')
            DAYZ_TOOLS_PATH = (Join-Path $root 'absent tools')
            TEMP = $temp; TMP = $temp
            AF59_ARGS_LOG = $argLog; AF59_PBO = $pbo; AF59_PAYLOAD = $payload
            AF59_STUB_TEXT = $case.Text; AF59_STUB_EXIT = [string]$case.Code; AF59_STUB_WRITE = $writeFlag
        }
        if ($case.Id -in @('S15', 'S16')) { $overrides.DAYZ_ALLOW_PLAIN_MODS = $null }
        foreach ($name in $overrides.Keys) {
            $savedEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
            [Environment]::SetEnvironmentVariable($name, $overrides[$name], 'Process')
        }
        if ($case.Id -in @('S15', 'S16') -and
            $null -ne [Environment]::GetEnvironmentVariable('DAYZ_ALLOW_PLAIN_MODS', 'Process')) {
            throw 'R4: escape variable was not removed'
        }
        # Windows SDK 10.0.26100.0/um/jobapi2.h:38,62,70; handleapi.h:38.
        # Compile under the scenario TEMP, and own only this scenario's process tree.
        if (-not ('AF59.Job' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace AF59 {
    public static class Job {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern IntPtr CreateJobObjectW(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool TerminateJobObject(IntPtr job, uint exitCode);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool CloseHandle(IntPtr handle);
    }
}
'@
        }
        $job = [AF59.Job]::CreateJobObjectW([IntPtr]::Zero, $null)
        if ($job -eq [IntPtr]::Zero) { throw 'Could not create timeout job' }
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $subject + '" -Mod "' + $mod + '" -Build -BuildOnly'
        $process = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $arguments `
            -WorkingDirectory $cwd -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        $null = $process.Handle
        if (-not [AF59.Job]::AssignProcessToJobObject($job, $process.Handle)) {
            throw ('Could not assign timeout job: ' + [Runtime.InteropServices.Marshal]::GetLastWin32Error())
        }
        if (-not $process.WaitForExit(60000)) {
            $exitCode = '<timeout>'
            $observed = 'timeout'
            if (-not [AF59.Job]::TerminateJobObject($job, 124)) { throw 'timeout; process tree termination failed' }
            $null = $process.WaitForExit(5000)
            throw 'timeout (60 s)'
        }
        $exitCode = $process.ExitCode
        $stdout = [IO.File]::ReadAllText($stdoutPath)
        $marker = $stdout.Contains('[ok] deployed:')
        $observed = 'invalid-output'
        if ($exitCode -eq 0 -and $marker) { $observed = 'success' }
        if ($exitCode -ne 0 -and -not $marker) { $observed = 'rejection' }
        $issues = @()
        if ($observed -ne $expected) { $issues += 'verdict mismatch' }
        if ($case.Id -ne 'S15' -and -not (Test-Path -LiteralPath $argLog -PathType Leaf)) { $issues += 'stub was not invoked' }
        $after = Get-ByteIdentity $pbo
        if ($case.Id -in @('S2', 'S4', 'S5') -and $after -cne $before) {
            $issues += 'previous PBO bytes changed or PBO missing'
        }
        if ($case.Success -and $after -cne [Convert]::ToBase64String($newBytes)) {
            $issues += 'new PBO bytes missing or incorrect'
        }
        if ($case.Id -eq 'S8') {
            if (-not (Test-Path -LiteralPath $wipeLog) -or
                [IO.File]::ReadAllText($wipeLog).Trim() -ne 'absent') {
                $issues += 'C2: stale temp survived until builder entry'
            }
            if (Test-Path -LiteralPath $sentinel) { $issues += 'C2: stale temp still exists' }
            if (-not (Test-Path -LiteralPath $sibling) -or
                [IO.File]::ReadAllText($sibling) -ne 'stale sentinel') {
                $issues += 'C2: another mod temp was changed'
            }
        }
        if ($case.Id -eq 'S9') {
            if (-not $stdout.Contains('PBO carries 0 .c entries but the source has 1')) {
                $issues += 'C3: missing script-count rejection'
            }
            if ($after -cne [Convert]::ToBase64String($newBytes)) { $issues += 'C3: incomplete new PBO not delivered by stub' }
        }
        if ($case.Id -eq 'S10') {
            if (-not $stdout.Contains('[warn] PBO script check INCONCLUSIVE (unsupported format):') -or
                $stdout.Contains('[ok] script check:')) { $issues += 'R1: unsupported header must warn without claiming a verified count' }
        }
        if ($case.Id -eq 'S11') {
            if (-not $stdout.Contains('Invalid PBO: Truncated PBO payload.')) { $issues += 'R1: proven truncation was not rejected as invalid' }
        }
        if ($case.Id -eq 'S12' -and -not $stdout.Contains('PBO carries 1 .c entries but the source has 2')) {
            $issues += 'R2a: outside script did not raise source threshold'
        }
        if ($case.Id -eq 'S13' -and -not $stdout.Contains('PBO carries 0 .c entries but the source has 1')) {
            $issues += 'R2b: source without scripts directory was not checked'
        }
        if ($case.Id -eq 'S14' -and (-not $stdout.Contains('[ok] script check: 9/9 .c entries present in the PBO') -or
            $stdout.Contains('INCONCLUSIVE'))) { $issues += 'R3: real PBO was not counted exactly' }
        if ($case.Id -in @('S11', 'S12', 'S13') -and $after -cne [Convert]::ToBase64String($newBytes)) {
            $issues += 'R1/R2: builder did not deliver the intended failing PBO'
        }
        if ($case.Id -eq 'S15') {
            if (Test-Path -LiteralPath $argLog) { $issues += 'R4: builder ran on a plain destination without attestation' }
            if ($after -cne $before) { $issues += 'R4: rejected destination PBO changed' }
            if (-not $stdout.Contains('Mods destination is not a junction/link:')) { $issues += 'R4: wrong rejection cause' }
        }
        if ($case.Id -eq 'S16') {
            if (-not (Test-Path -LiteralPath $valveLog) -or [IO.File]::ReadAllText($valveLog).Trim() -ne 'absent') {
                $issues += 'R4: builder did not observe absent escape variable'
            }
            if (-not $stdout.Contains('[ok] script check: 1/1 .c entries present in the PBO')) { $issues += 'R4: mapped build skipped script check' }
            if ((Get-ByteIdentity (Join-Path $preparedAddons ($mod + '.pbo'))) -cne $after) { $issues += 'R4: output did not reach mapping target' }
        }
        if ($case.Id -eq 'S1') {
            try { Test-TempArgument $argLog $drive $cwd }
            catch { $issues += $_.Exception.Message }
        }
        if ($issues.Count -gt 0) { throw ($issues -join '; ') }
    }
    catch {
        $reason = $_.Exception.Message
    }
    finally {
        try {
            if ($job -ne [IntPtr]::Zero) {
                if (-not [AF59.Job]::TerminateJobObject($job, 124)) { throw 'Process tree cleanup failed' }
            }
            if ($null -ne $process -and -not $process.HasExited) {
                $process.Kill()
                if (-not $process.WaitForExit(5000)) { throw 'Process termination did not finish' }
            }
        }
        catch { $reason = ([string]$reason + '; ' + $_.Exception.Message) }
        if ($job -ne [IntPtr]::Zero) { $null = [AF59.Job]::CloseHandle($job) }
        foreach ($name in $savedEnv.Keys) {
            [Environment]::SetEnvironmentVariable($name, $savedEnv[$name], 'Process')
        }
        if ($null -ne $process) { $process.Dispose() }
        if ($null -ne $reason) {
            if (Test-Path -LiteralPath $stdoutPath) {
                $stdout = (@(Get-Content -LiteralPath $stdoutPath -Tail 8) -join ' | ')
            }
            if ([string]::IsNullOrEmpty($stdout)) { $stdout = '<empty>' }
            $reason += '; stdout-tail=' + $stdout
            if (Test-Path -LiteralPath $stderrPath) {
                $stderrTail = @(Get-Content -LiteralPath $stderrPath -Tail 4) -join ' | '
                if ($stderrTail) { $reason += '; stderr-tail=' + $stderrTail }
            }
        }
        try {
            if ($null -ne $junctionPath -and (Test-Path -LiteralPath $junctionPath)) {
                $fullLink = [IO.Path]::GetFullPath($junctionPath)
                if (-not $fullLink.StartsWith([IO.Path]::GetFullPath($root) + '\', [StringComparison]::OrdinalIgnoreCase) -or
                    -not ((Get-Item -LiteralPath $fullLink -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    throw 'Unsafe fixture junction cleanup'
                }
                # Delete only the checked junction, never recurse through its target.
                [IO.Directory]::Delete($fullLink)
            }
            Remove-FixtureTree $root
            if ($case.Id -eq $scenarios[-1].Id) { Remove-FixtureTree $runRoot }
        }
        catch { $reason = ([string]$reason + '; cleanup failed: ' + $_.Exception.Message) }
    }
    if ($null -eq $reason) {
        $passed++
        Write-Output ($case.Id + ' PASS expected=' + $expected + ' observed=' + $observed + ' exit=' + $exitCode)
    }
    else {
        $failed++
        $reason = $reason -replace '[\r\n]+', ' '
        Write-Output ($case.Id + ' FAIL expected=' + $expected + ' observed=' + $observed + ' exit=' + $exitCode + ' reason=' + $reason)
    }
}
Write-Output ('PASS=' + $passed + ' FAIL=' + $failed)
if ($passed -eq $scenarios.Count -and $failed -eq 0) { exit 0 }
exit 1
