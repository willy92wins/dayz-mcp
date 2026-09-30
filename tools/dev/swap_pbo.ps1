#Requires -Version 5.1
# Swap a mod's live PBO for a given build, only when both hashes are the expected
# ones and no DayZ process runs, with no window in which another process can change
# the files between the checks and the swap.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\dev\swap_pbo.ps1 `
#     -Build <built .pbo> -WantNew <SHA-256 of the build> -WantOld <SHA-256 of the live PBO> `
#     [-Backup <rollback copy> -WantBackup <its SHA-256>] [-ModName DayZ_MCP] [-Destination <Addons folder>]
#
# The live PBO is <Destination>\<ModName>.pbo. Destination defaults to the folder
# tools/pack-addon.ps1 builds into: <DAYZ_PATH or the Steam default>\!Workshop\@<ModName>\Addons.
#
# How it swaps:
#   1. It reads the build once and hashes those bytes. The bytes it installs are the
#      ones it hashed, whatever happens to the build file afterwards.
#   2. It opens the live PBO with share mode none and delete access, and hashes it
#      through that handle. Until the file is moved aside no other process can read,
#      write, replace, rename or delete it: a second swap, an AddonBuilder copy or a
#      starting game all fail to open it, so two swaps cannot interleave.
#   3. It writes the validated bytes to a new file beside the live one (created new,
#      held the same way), flushes it to disk and hashes it back.
#   4. It hashes the live PBO once more under its lock, moves it aside by renaming it
#      through its own handle, so the file moved is exactly the one hashed, and renames
#      the new file into place. Neither rename replaces an existing file: if anything
#      appears at the live path in between, nothing is overwritten, the previous PBO
#      goes back when the path is still free, and the script says where each file is.
#   5. The previous PBO stays beside the live one as
#      <ModName>.pbo.swapped_out_<UTC time>_<its hash prefix>. DayZ loads only *.pbo, and
#      the swap deletes nothing. A process killed between the two renames leaves no
#      <ModName>.pbo, the previous PBO under that name and the new one as
#      <ModName>.pbo.swap_new_<id>: rename either back to <ModName>.pbo.
# -Backup names an independent rollback copy. The script refuses one that is the live
# PBO under another name (same volume serial and file id: a hard link, or a link to
# it), holds it open against writers during the swap, and hashes it again at the end.
#
# It refuses, before moving anything, when:
#   - a hash is not a full SHA-256 (64 hex digits);
#   - the build's SHA-256 is not -WantNew;
#   - -Backup and -WantBackup do not come together, the backup is the live PBO itself,
#     or the backup's SHA-256 is not -WantBackup;
#   - the live PBO is missing, or in use (a running DayZ, another swap, another program);
#   - the live PBO's SHA-256 is not -WantOld (it changed since you looked: another
#     session may have swapped in its own build);
#   - any DayZ game process runs (DayZDiag_x64, DayZServer_x64, DayZ_x64, DayZ_BE: the
#     images dayz_mcp/orphan_guard.py calls DayZ). This check runs last, right before
#     the move. DayZ Tools processes (DayZToolsLauncher, AddonBuilder) do not block it.
#
# A built PBO says which commit built it (mcp_build.json, written by
# tools/pack-addon.ps1). Check it with tools/dev/pbo_provenance.py before swapping.

param(
  [Parameter(Mandatory = $true)][string]$Build,
  [Parameter(Mandatory = $true)][string]$WantNew,
  [Parameter(Mandatory = $true)][string]$WantOld,
  [string]$Backup = "",
  [string]$WantBackup = "",
  [string]$ModName = "DayZ_MCP",
  [string]$Destination = ""
)

$ErrorActionPreference = "Stop"

# Windows PowerShell 5.1 started from Git Bash inherits a Unix PSModulePath, and
# module cmdlets are then not recognized. Same reset as tools/pack-addon.ps1.
if ($PSVersionTable.PSEdition -eq 'Desktop') {
  $machineModules = [Environment]::GetEnvironmentVariable('PSModulePath', 'Machine')
  if ($machineModules) {
    $env:PSModulePath = $machineModules
  }
}

# The file operations .NET does not expose: an open with share mode none and delete
# access, the volume serial and file id of an open file, and a rename or delete
# through that same handle. A rename by handle takes a full path: a bare name would
# resolve against the process's current directory.
if (-not ('DayZMcpDevTools.SwapPboFile' -as [type])) {
  Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace DayZMcpDevTools
{
    public static class SwapPboFile
    {
        [StructLayout(LayoutKind.Sequential)]
        private struct ByHandleFileInformation
        {
            public uint FileAttributes;
            public uint CreationLow, CreationHigh, AccessLow, AccessHigh, WriteLow, WriteHigh;
            public uint VolumeSerialNumber, FileSizeHigh, FileSizeLow, NumberOfLinks, FileIndexHigh, FileIndexLow;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFileW(string fileName, uint desiredAccess, uint shareMode,
            IntPtr securityAttributes, uint creationDisposition, uint flagsAndAttributes, IntPtr templateFile);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetFileInformationByHandle(SafeFileHandle file, out ByHandleFileInformation information);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetFileInformationByHandle(SafeFileHandle file, int informationClass,
            IntPtr information, uint size);

        // share: the FILE_SHARE_* bits (1 read, 2 write, 4 delete) other handles keep; 0 is exclusive.
        public static FileStream TryOpen(string path, bool write, bool delete, uint share, bool createNew, out int error)
        {
            uint access = 0x80000000u | (write ? 0x40000000u : 0u) | (delete ? 0x00010000u : 0u);
            SafeFileHandle handle = CreateFileW(path, access, share, IntPtr.Zero, createNew ? 1u : 3u, 0x80u, IntPtr.Zero);
            if (handle.IsInvalid)
            {
                error = Marshal.GetLastWin32Error();
                handle.Dispose();
                return null;
            }
            error = 0;
            return new FileStream(handle, write ? FileAccess.ReadWrite : FileAccess.Read);
        }

        // Volume serial and file id: the same value for every name of one file.
        public static string Identity(FileStream stream)
        {
            ByHandleFileInformation info;
            if (!GetFileInformationByHandle(stream.SafeFileHandle, out info))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            return info.VolumeSerialNumber.ToString("X8") + ":" + info.FileIndexHigh.ToString("X8") + info.FileIndexLow.ToString("X8");
        }

        // FILE_RENAME_INFO with ReplaceIfExists FALSE: never replaces an existing file.
        // Returns 0, or the Win32 error (183 when the target exists).
        public static int Rename(FileStream stream, string fullPath)
        {
            int lengthOffset = 2 * IntPtr.Size;
            int nameOffset = lengthOffset + 4;
            int size = nameOffset + (fullPath.Length + 1) * 2;
            IntPtr buffer = Marshal.AllocHGlobal(size);
            try
            {
                Marshal.Copy(new byte[size], 0, buffer, size);
                Marshal.WriteInt32(buffer, lengthOffset, fullPath.Length * 2);
                Marshal.Copy(fullPath.ToCharArray(), 0, IntPtr.Add(buffer, nameOffset), fullPath.Length);
                return SetFileInformationByHandle(stream.SafeFileHandle, 3, buffer, (uint)size) ? 0 : Marshal.GetLastWin32Error();
            }
            finally
            {
                Marshal.FreeHGlobal(buffer);
            }
        }

        // FILE_DISPOSITION_INFO: the file goes when its last handle closes.
        public static int MarkForDeletion(FileStream stream)
        {
            IntPtr buffer = Marshal.AllocHGlobal(4);
            try
            {
                Marshal.WriteInt32(buffer, 1);
                return SetFileInformationByHandle(stream.SafeFileHandle, 4, buffer, 1u) ? 0 : Marshal.GetLastWin32Error();
            }
            finally
            {
                Marshal.FreeHGlobal(buffer);
            }
        }
    }
}
'@
}

function Assert-Sha256Text([string]$Name, [string]$Value) {
  if ($Value -notmatch '^[0-9A-Fa-f]{64}$') {
    throw "-$Name must be a full SHA-256 (64 hex digits), not '$Value'"
  }
}

function Get-Sha256Hex([byte[]]$Bytes) {
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    return ([BitConverter]::ToString($sha.ComputeHash($Bytes)) -replace '-', '')
  } finally {
    $sha.Dispose()
  }
}

function Read-StreamBytes([System.IO.FileStream]$Stream) {
  $Stream.Position = 0
  $buffer = New-Object byte[] ([int]$Stream.Length)
  $read = 0
  while ($read -lt $buffer.Length) {
    $count = $Stream.Read($buffer, $read, $buffer.Length - $read)
    if ($count -le 0) { throw "Short read from $($Stream.Name)" }
    $read += $count
  }
  return ,$buffer
}

function Open-SwapFile([string]$Path, [bool]$Write, [bool]$Delete, [uint32]$Share, [bool]$CreateNew) {
  $err = 0
  $stream = [DayZMcpDevTools.SwapPboFile]::TryOpen($Path, $Write, $Delete, $Share, $CreateNew, [ref]$err)
  return [pscustomobject]@{ Stream = $stream; Error = $err }
}

function Get-FullPath([string]$Path) {
  # Relative to $PWD, as the cmdlets resolve it.
  return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
}

if ($ModName -notmatch '^[A-Za-z][A-Za-z0-9_]{0,63}$') {
  throw "ModName must match ^[A-Za-z][A-Za-z0-9_]{0,63}$ (no hyphens): '$ModName'"
}
Assert-Sha256Text 'WantNew' $WantNew
Assert-Sha256Text 'WantOld' $WantOld
$wantNewUpper = $WantNew.ToUpperInvariant()
$wantOldUpper = $WantOld.ToUpperInvariant()
$hasBackup = -not [string]::IsNullOrWhiteSpace($Backup)
if ($hasBackup -ne (-not [string]::IsNullOrWhiteSpace($WantBackup))) {
  throw "-Backup and -WantBackup go together: pass both, or neither."
}
if ($hasBackup) {
  Assert-Sha256Text 'WantBackup' $WantBackup
}

if (-not $Destination) {
  $workshop = "C:\Program Files (x86)\Steam\steamapps\common\DayZ\!Workshop"
  if ($env:DAYZ_PATH) { $workshop = Join-Path $env:DAYZ_PATH "!Workshop" }
  $Destination = Join-Path $workshop "@$ModName\Addons"
}
$live = Join-Path $Destination "$ModName.pbo"
$liveFull = Get-FullPath $live
$liveDir = [IO.Path]::GetDirectoryName($liveFull)

# 1. The build, read once: these exact bytes are hashed and installed.
$buildFull = Get-FullPath $Build
if (-not (Test-Path -LiteralPath $buildFull -PathType Leaf)) { throw "Build not found: $Build" }
$buildBytes = [IO.File]::ReadAllBytes($buildFull)
$buildHash = Get-Sha256Hex $buildBytes
if ($buildHash -ne $wantNewUpper) {
  throw "The build is not the expected one: $Build is $buildHash, -WantNew is $wantNewUpper."
}

$old = $null
$new = $null
$backupStream = $null
$sidecar = $null
$movedAside = $false
$installed = $false
try {
  # The backup's file id, taken before any lock so an alias gets its own message.
  if ($hasBackup) {
    $backupFull = Get-FullPath $Backup
    $probe = Open-SwapFile $backupFull $false $false 7 $false
    if ($null -eq $probe.Stream) {
      if ($probe.Error -eq 2 -or $probe.Error -eq 3) { throw "Backup not found: $Backup" }
      throw "Cannot open the backup $Backup (Win32 error $($probe.Error))."
    }
    try { $backupId = [DayZMcpDevTools.SwapPboFile]::Identity($probe.Stream) } finally { $probe.Stream.Dispose() }
    $probe = Open-SwapFile $liveFull $false $false 7 $false
    if ($null -eq $probe.Stream) {
      if ($probe.Error -eq 2 -or $probe.Error -eq 3) { throw "No live PBO to swap: $live" }
      throw "Cannot open the live PBO $live (Win32 error $($probe.Error))."
    }
    try { $liveId = [DayZMcpDevTools.SwapPboFile]::Identity($probe.Stream) } finally { $probe.Stream.Dispose() }
    if ($backupId -eq $liveId) {
      throw ("The backup is the live PBO itself under another name (same file id ${liveId}: a hard link, " +
             "or a link to $live); the swap would take the rollback with it. Pass an independent copy.")
    }
  }

  # 2. The live PBO, locked: share mode none, delete access.
  $opened = Open-SwapFile $liveFull $false $true 0 $false
  if ($null -eq $opened.Stream) {
    if ($opened.Error -eq 2 -or $opened.Error -eq 3) { throw "No live PBO to swap: $live" }
    if ($opened.Error -eq 32) {
      throw "The live PBO is in use (a running DayZ, another swap, or another program): $live"
    }
    throw "Cannot lock the live PBO $live (Win32 error $($opened.Error))."
  }
  $old = $opened.Stream
  $oldId = [DayZMcpDevTools.SwapPboFile]::Identity($old)
  $before = Get-Sha256Hex (Read-StreamBytes $old)
  "live before: $before"
  if ($before -ne $wantOldUpper) {
    throw "The live PBO is not the expected one: $live is $before, -WantOld is $wantOldUpper."
  }

  # The backup, held against writers and deleters until the end.
  if ($hasBackup) {
    $opened = Open-SwapFile $backupFull $false $false 1 $false
    if ($null -eq $opened.Stream) {
      throw ("The backup cannot be opened while the live PBO is locked (Win32 error $($opened.Error)): " +
             "it is the live PBO under another name, or another program holds it: $Backup")
    }
    $backupStream = $opened.Stream
    $heldId = [DayZMcpDevTools.SwapPboFile]::Identity($backupStream)
    if ($heldId -ne $backupId -or $heldId -eq $oldId) {
      throw "The backup changed while it was being checked: $Backup"
    }
    $backupHash = Get-Sha256Hex (Read-StreamBytes $backupStream)
    if ($backupHash -ne $WantBackup.ToUpperInvariant()) {
      throw "The backup is not intact: $Backup is $backupHash, -WantBackup is $($WantBackup.ToUpperInvariant())."
    }
  }

  # 3. The new file beside the live one, from the validated bytes, read back from disk.
  $tempFull = Join-Path $liveDir ("$ModName.pbo.swap_new_" + [guid]::NewGuid().ToString('N'))
  $opened = Open-SwapFile $tempFull $true $true 0 $true
  if ($null -eq $opened.Stream) { throw "Cannot create $tempFull (Win32 error $($opened.Error))." }
  $new = $opened.Stream
  $new.Write($buildBytes, 0, $buildBytes.Length)
  $new.Flush($true)
  if ((Get-Sha256Hex (Read-StreamBytes $new)) -ne $wantNewUpper) {
    throw "The copy of the build did not read back as -WantNew: $tempFull"
  }

  # No DayZ game, checked last before the move.
  $dayz = @(Get-Process -Name DayZDiag_x64, DayZServer_x64, DayZ_x64, DayZ_BE -ErrorAction SilentlyContinue)
  if ($dayz.Count -gt 0) {
    throw ("DayZ is running: " + (($dayz | ForEach-Object { "$($_.Name):$($_.Id)" }) -join ', ') +
           ". Close every DayZ process, then run this again.")
  }

  # 4. Still -WantOld under the same lock; then the two renames, neither over a file.
  if ((Get-Sha256Hex (Read-StreamBytes $old)) -ne $wantOldUpper) {
    throw "The live PBO changed under its lock: $live"
  }
  $stamp = [DateTime]::UtcNow.ToString("yyyyMMdd'T'HHmmss'Z'", [Globalization.CultureInfo]::InvariantCulture)
  $sidecar = Join-Path $liveDir ("$ModName.pbo.swapped_out_" + $stamp + "_" + $before.Substring(0, 16))
  $rc = [DayZMcpDevTools.SwapPboFile]::Rename($old, $sidecar)
  if ($rc -ne 0) {
    throw "Could not move the live PBO aside to $sidecar (Win32 error $rc); nothing was changed."
  }
  $movedAside = $true
  $rc = [DayZMcpDevTools.SwapPboFile]::Rename($new, $liveFull)
  if ($rc -ne 0) {
    throw ("Could not rename the new build into $live (Win32 error $rc; 183 means another process " +
           "created that file meanwhile). Nothing was overwritten.")
  }
  $installed = $true

  # 5. Verified through the handle, then through the path: the path names our file.
  $newId = [DayZMcpDevTools.SwapPboFile]::Identity($new)
  $viaHandle = Get-Sha256Hex (Read-StreamBytes $new)
  $new.Dispose()
  $new = $null
  $opened = Open-SwapFile $liveFull $false $false 1 $false
  if ($null -eq $opened.Stream) { throw "Swap verification failed: cannot open $live (Win32 error $($opened.Error))." }
  try {
    $pathId = [DayZMcpDevTools.SwapPboFile]::Identity($opened.Stream)
    $after = Get-Sha256Hex (Read-StreamBytes $opened.Stream)
    $size = $opened.Stream.Length
  } finally {
    $opened.Stream.Dispose()
  }
  "live after : $after  $size B"
  if ($viaHandle -ne $wantNewUpper -or $after -ne $wantNewUpper -or $pathId -ne $newId) {
    throw "Swap verification failed: $live is $after, not $wantNewUpper; the previous PBO is $sidecar."
  }
  if ($hasBackup -and (Get-Sha256Hex (Read-StreamBytes $backupStream)) -ne $WantBackup.ToUpperInvariant()) {
    throw "The backup changed during the swap: $Backup"
  }
  "previous PBO kept as: $sidecar"
  "PBO SWAPPED"
} finally {
  if ($movedAside -and -not $installed -and $null -ne $old) {
    # The new build never reached the live path: put the previous PBO back, over nothing.
    $back = [DayZMcpDevTools.SwapPboFile]::Rename($old, $liveFull)
    if ($back -ne 0) {
      Write-Warning "The previous PBO stays at $sidecar (Win32 error $back putting it back at $live)."
    }
  }
  if ($null -ne $new) {
    if (-not $installed) { [void][DayZMcpDevTools.SwapPboFile]::MarkForDeletion($new) }
    $new.Dispose()
  }
  if ($null -ne $backupStream) { $backupStream.Dispose() }
  if ($null -ne $old) { $old.Dispose() }
}
