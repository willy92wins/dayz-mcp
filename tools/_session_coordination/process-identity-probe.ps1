param(
  [Parameter(Mandatory = $true)]
  [Alias("Pid")]
  [int]$ProcessId
)

$ErrorActionPreference = "Stop"

try {
  $proc = Get-Process -Id $ProcessId -ErrorAction Stop
  $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
  $out = [ordered]@{
    pid = $ProcessId
    creation_time_utc = $proc.StartTime.ToUniversalTime().ToString("o")
    executable_path = [string]$proc.Path
    command_line = [string]$cim.CommandLine
    identity_complete = $false
    error = $null
  }
  $out.identity_complete = [bool](
    $out.creation_time_utc -and
    $out.executable_path -and
    $out.command_line
  )

  if (-not $out.identity_complete) {
    $out.error = "identity_incomplete"
    $out | ConvertTo-Json -Compress
    exit 3
  }

  $out | ConvertTo-Json -Compress
  exit 0
} catch {
  [ordered]@{
    pid = $ProcessId
    creation_time_utc = $null
    executable_path = $null
    command_line = $null
    identity_complete = $false
    error = [string]$_.FullyQualifiedErrorId
  } | ConvertTo-Json -Compress
  exit 3
}
