param([Parameter(Mandatory = $true)][string]$Script)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$workRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '_work'))
$runRoot = Join-Path $workRoot ([Guid]::NewGuid().ToString('N').Substring(0, 12))
$passed = 0
$failed = 0
$scenarios = @(
    @{ Id = 'H11'; Text = 'Build Successful'; Code = 0; Write = $true; Prior = $false; Success = $false; Missing = $true; Flags = '-BuildOnly' },
    @{ Id = 'H12'; Text = 'Build Successful'; Code = 0; Write = $true; Prior = $false; Success = $false; Missing = $true; Flags = '-Mode none' },
    @{ Id = 'H13'; Text = 'Build Successful'; Code = 0; Write = $true; Prior = $false; Success = $false; Missing = $true; Flags = '-BuildOnly -Preflight' },
    @{ Id = 'H14'; Text = 'Build Successful'; Code = 0; Write = $true; Prior = $false; Success = $false; Missing = $false; Flags = '-BuildOnly' },
    @{ Id = 'H15'; Text = 'Build Successful'; Code = 0; Write = $true; Prior = $false; Success = $false; Missing = $false; Flags = '-Mode none' },
    @{ Id = 'H16'; Text = 'Build Successful'; Code = 0; Write = $true; Prior = $false; Success = $false; Missing = $false; Flags = '-Mode none -Preflight' }
)

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
        foreach ($dir in @((Join-Path $dev 'tools'), (Join-Path $dev '_server'),
            (Join-Path $dev '_client'), (Join-Path $source 'scripts\5_Mission'),
            $addons, $abDir, $temp, $cwd)) {
            $null = New-Item -ItemType Directory -Path $dir -Force
        }
        [IO.File]::WriteAllText((Join-Path $source 'config.cpp'), 'class CfgPatches { class AF59Fixture { requiredAddons[] = {}; }; };', [Text.Encoding]::ASCII)
        [IO.File]::WriteAllText((Join-Path $source 'scripts\5_Mission\dummy.c'), '// Fixture source only.', [Text.Encoding]::ASCII)
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'stub-addonbuilder.cmd') -Destination $stub
        $oldBytes = [byte[]]@(0, 255, 13, 10, 65, 70, 53, 57, 1)
        $entryName = 'scripts\5_Mission\dummy.c'
        if ($case.Id -eq 'S9') { $entryName = 'config.cpp' }
        $newBytes = New-FixturePbo $entryName
        if ($case.Id -eq 'S8') {
            foreach ($file in @($sentinel, $sibling)) {
                $null = New-Item -ItemType Directory -Path (Split-Path $file) -Force
                [IO.File]::WriteAllText($file, 'stale sentinel', [Text.Encoding]::ASCII)
            }
            # Observe the stale file at builder entry, not merely after deployment.
            $stubBody = [IO.File]::ReadAllText($stub)
            $probe = '@if exist "%AF59_TEMP_SENTINEL%" (echo present>"%AF59_WIPE_LOG%") else (echo absent>"%AF59_WIPE_LOG%")'
            [IO.File]::WriteAllText($stub, $probe + "`r`n" + $stubBody, [Text.Encoding]::ASCII)
        }
        [IO.File]::WriteAllBytes($payload, $newBytes)
        if ($case.Prior) {
            [IO.File]::WriteAllBytes($pbo, $oldBytes)
            [IO.File]::SetLastWriteTimeUtc($pbo, [DateTime]::UtcNow.AddHours(-2))
        }
        $modsPath = Join-Path $drive 'Mods'
        if ($case.Missing) { Remove-FixtureTree $modsPath }
        $before = Get-ByteIdentity $pbo
        $writeFlag = '0'
        if ($case.Write) { $writeFlag = '1' }
        $overrides = @{
            DAYZ_ALLOW_PLAIN_MODS = ''
            AF59_TEMP_SENTINEL = $sentinel; AF59_WIPE_LOG = $wipeLog
            DAYZ_DEV_ROOT = $dev; DAYZ_WORK_DRIVE = $drive; DAYZ_ADDONBUILDER = $stub
            DAYZ_GAME_PATH = (Join-Path $root 'absent game')
            DAYZ_DIAG_PATH = (Join-Path $root 'absent diag')
            DAYZ_TOOLS_PATH = (Join-Path $root 'absent tools')
            TEMP = $temp; TMP = $temp
            AF59_ARGS_LOG = $argLog; AF59_PBO = $pbo; AF59_PAYLOAD = $payload
            AF59_STUB_TEXT = $case.Text; AF59_STUB_EXIT = [string]$case.Code; AF59_STUB_WRITE = $writeFlag
        }
        foreach ($name in $overrides.Keys) {
            $savedEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
            [Environment]::SetEnvironmentVariable($name, $overrides[$name], 'Process')
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
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $subject + '" -Mod "' + $mod + '" -Build ' + $case.Flags
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
        if (Test-Path -LiteralPath $argLog) { $issues += 'stub must not be invoked' }
        if ($case.Missing -and (Test-Path -LiteralPath $modsPath)) { $issues += 'Mods was fabricated' }
        if (-not $stdout.Contains('Mods destination is not')) { $issues += 'missing destination diagnostic' }
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
            Remove-FixtureTree $root
            if ($case.Id -eq 'H16') { Remove-FixtureTree $runRoot }
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
if ($passed -eq 6 -and $failed -eq 0) { exit 0 }
exit 1
