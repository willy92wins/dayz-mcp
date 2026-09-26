param(
  [Parameter(Mandatory = $true)]
  [Alias("Pid")]
  [int]$ProcessId
)

$ErrorActionPreference = "Stop"

Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

public static class DayZMcpNativeProcessProbe
{
    public const uint PROCESS_TERMINATE = 0x0001;
    public const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;

    [StructLayout(LayoutKind.Sequential)]
    public struct FILETIME
    {
        public uint dwLowDateTime;
        public uint dwHighDateTime;

        public long ToInt64()
        {
            return ((long)dwHighDateTime << 32) | dwLowDateTime;
        }
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr OpenProcess(
        uint dwDesiredAccess,
        bool bInheritHandle,
        int dwProcessId);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool QueryFullProcessImageName(
        IntPtr hProcess,
        uint dwFlags,
        StringBuilder lpExeName,
        ref int lpdwSize);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GetProcessTimes(
        IntPtr hProcess,
        out FILETIME lpCreationTime,
        out FILETIME lpExitTime,
        out FILETIME lpKernelTime,
        out FILETIME lpUserTime);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool IsProcessInJob(
        IntPtr ProcessHandle,
        IntPtr JobHandle,
        [MarshalAs(UnmanagedType.Bool)] out bool Result);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool CloseHandle(IntPtr hObject);

    public static int LastError()
    {
        return Marshal.GetLastWin32Error();
    }
}
'@

$result = [ordered]@{
  pid = $ProcessId
  desired_access = "PROCESS_QUERY_LIMITED_INFORMATION"
  open_process_succeeded = $false
  open_process_error = $null
  executable_path = $null
  image_query_error = $null
  creation_time_utc = $null
  process_times_error = $null
  in_any_job = $null
  job_query_error = $null
  terminate_access_open_succeeded = $false
  terminate_access_error = $null
  identity_complete = $false
  error = $null
}

$handle = [IntPtr]::Zero
try {
  $handle = [DayZMcpNativeProcessProbe]::OpenProcess(
    [DayZMcpNativeProcessProbe]::PROCESS_QUERY_LIMITED_INFORMATION,
    $false,
    $ProcessId
  )
  if ($handle -eq [IntPtr]::Zero) {
    $result.open_process_error = [DayZMcpNativeProcessProbe]::LastError()
    throw "OpenProcess failed"
  }

  $result.open_process_succeeded = $true

  $capacity = 32768
  $path = [System.Text.StringBuilder]::new($capacity)
  if ([DayZMcpNativeProcessProbe]::QueryFullProcessImageName($handle, 0, $path, [ref]$capacity)) {
    $result.executable_path = $path.ToString()
  } else {
    $result.image_query_error = [DayZMcpNativeProcessProbe]::LastError()
  }

  $creation = New-Object DayZMcpNativeProcessProbe+FILETIME
  $exit = New-Object DayZMcpNativeProcessProbe+FILETIME
  $kernel = New-Object DayZMcpNativeProcessProbe+FILETIME
  $user = New-Object DayZMcpNativeProcessProbe+FILETIME
  if ([DayZMcpNativeProcessProbe]::GetProcessTimes(
    $handle,
    [ref]$creation,
    [ref]$exit,
    [ref]$kernel,
    [ref]$user
  )) {
    $result.creation_time_utc = [DateTime]::FromFileTimeUtc($creation.ToInt64()).ToString("o")
  } else {
    $result.process_times_error = [DayZMcpNativeProcessProbe]::LastError()
  }

  $inJob = $false
  if ([DayZMcpNativeProcessProbe]::IsProcessInJob(
    $handle,
    [IntPtr]::Zero,
    [ref]$inJob
  )) {
    $result.in_any_job = $inJob
  } else {
    $result.job_query_error = [DayZMcpNativeProcessProbe]::LastError()
  }

  $terminateHandle = [DayZMcpNativeProcessProbe]::OpenProcess(
    [DayZMcpNativeProcessProbe]::PROCESS_TERMINATE,
    $false,
    $ProcessId
  )
  if ($terminateHandle -eq [IntPtr]::Zero) {
    $result.terminate_access_error = [DayZMcpNativeProcessProbe]::LastError()
  } else {
    $result.terminate_access_open_succeeded = $true
    [void][DayZMcpNativeProcessProbe]::CloseHandle($terminateHandle)
  }

  $result.identity_complete = [bool](
    $result.executable_path -and
    $result.creation_time_utc
  )
  if (-not $result.identity_complete) {
    $result.error = "native_identity_incomplete"
  }
} catch {
  if (-not $result.error) {
    $result.error = [string]$_.Exception.Message
  }
} finally {
  if ($handle -ne [IntPtr]::Zero) {
    [void][DayZMcpNativeProcessProbe]::CloseHandle($handle)
  }
}

$result | ConvertTo-Json -Compress
if ($result.identity_complete) {
  exit 0
}
exit 3
