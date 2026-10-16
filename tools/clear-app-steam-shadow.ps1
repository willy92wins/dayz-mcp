<#
.SYNOPSIS
Removes the private copy of Steam's registry keys that an MSIX app (Claude,
Codex) keeps for the processes it starts.

.DESCRIPTION
Claude Desktop and Codex run every process they start inside a registry
virtualization: HKCU writes land in the app's private hive
(%LOCALAPPDATA%\Packages\<family>\SystemAppData\Helium\User.dat) and from then
on hide the real key from every process of that app. Once a Steam started from
one of the app's shells has written HKCU\Software\Valve there, the copy keeps
that Steam's ActiveProcess pid after it exits. A DayZ started later from the
app's shells reads it and stops at "Unable to locate a running instance of
Steam". dayz_test_run is not affected (its daemon runs outside the app), and
session_status names the state with the warning steam_app_view_stale.

Deleting the key from inside the app leaves a marker that still hides the real
one, so the copy is removed offline. Run this as administrator, as the same
user, with the app fully closed (tray included). By default it backs up the
hive and lists the Valve keys in the copy, and changes nothing. -Apply deletes
Software\Valve from the copy, and the app reads the real keys again.

Rollback: with the app closed, copy the backed-up User.dat* files back into the
Helium folder.

.PARAMETER Apply
Delete Software\Valve from the app's copy. Without it nothing is changed.

.PARAMETER PackageFamily
The app's package family name. Claude's is the default; for Codex, read it with
(Get-AppxPackage *Codex*).PackageFamilyName.

.EXAMPLE
.\clear-app-steam-shadow.ps1

.EXAMPLE
.\clear-app-steam-shadow.ps1 -Apply
#>
param(
    [switch]$Apply,
    [string]$PackageFamily = 'Claude_pzs8sxrjxfjjc'
)

$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this from an elevated PowerShell (as administrator).'
}

$helium = Join-Path $env:LOCALAPPDATA "Packages\$PackageFamily\SystemAppData\Helium"
$hive = Join-Path $helium 'User.dat'
if (-not (Test-Path -LiteralPath $hive)) {
    throw "$hive not found. Pass the app's package family with -PackageFamily."
}

# The hive is in use while any process of the app runs; reg load below fails
# then too, this only names the processes.
$package = Get-AppxPackage | Where-Object { $_.PackageFamilyName -eq $PackageFamily }
if ($package) {
    $running = Get-Process | Where-Object {
        $_.Path -and $_.Path.StartsWith($package.InstallLocation, [StringComparison]::OrdinalIgnoreCase)
    }
    if ($running) {
        throw "The app is still running (pids $($running.Id -join ', ')). Close it fully and run this again."
    }
}

# Back up the hive and its transaction logs before anything else. A hive still
# in use fails the copy here, before anything is changed.
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $env:USERPROFILE "DayZ_MCP_backups\app-helium-$PackageFamily-$stamp"
New-Item -ItemType Directory -Path $backup -Force | Out-Null
Get-ChildItem -LiteralPath $helium -Filter 'User.dat*' -Force |
    Copy-Item -Destination $backup
Write-Host "Backup: $backup"

$mountName = 'DayZMcpAppHelium'
& reg.exe load "HKU\$mountName" $hive | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "reg load failed (exit $LASTEXITCODE): the hive is in use. Close the app fully, or restart Windows, and run this again."
}
try {
    $root = "Registry::HKEY_USERS\$mountName"
    $valve = "$root\Software\Valve"
    if (-not (Test-Path -LiteralPath $valve)) {
        Write-Host 'The copy has no Software\Valve: there is nothing to remove.'
        Write-Host 'Top-level keys of the hive, in case its layout differs:'
        Get-ChildItem -LiteralPath $root | ForEach-Object { '  ' + $_.PSChildName }
    }
    else {
        Write-Host 'Valve keys in the copy:'
        Get-ChildItem -LiteralPath $valve -Recurse | ForEach-Object { '  ' + $_.Name }
        $activeProcess = "$valve\Steam\ActiveProcess"
        if (Test-Path -LiteralPath $activeProcess) {
            $copy = Get-ItemProperty -LiteralPath $activeProcess
            Write-Host "ActiveProcess in the copy: pid=$($copy.pid) ActiveUser=$($copy.ActiveUser)"
            $copy = $null
        }
        $steam = Get-Process -Name 'steam' -ErrorAction SilentlyContinue
        Write-Host "Live steam.exe: $(if ($steam) { $steam.Id -join ', ' } else { 'none' })"
        if ($Apply) {
            Remove-Item -LiteralPath $valve -Recurse -Force
            Write-Host 'Deleted Software\Valve from the copy.'
        }
        else {
            Write-Host 'Nothing changed. Run again with -Apply to delete it.'
        }
    }
}
finally {
    # The registry provider keeps key handles until they are collected, and
    # reg unload fails while one is open.
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    & reg.exe unload "HKU\$mountName" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "reg unload failed. Run 'reg unload HKU\$mountName' (or restart Windows) before you open the app."
    }
}

Write-Host ''
Write-Host 'To check: open the app and, from one of its shells, compare'
Write-Host '  (Get-ItemProperty HKCU:\Software\Valve\Steam\ActiveProcess).pid'
Write-Host '  (Get-Process steam).Id'
Write-Host 'They must be the same number.'
