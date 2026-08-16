$ErrorActionPreference = "Stop"

function Write-Result([object]$Value, [int]$Code) {
  $Value | ConvertTo-Json -Compress -Depth 8
  exit $Code
}

function Get-Hash([string]$Value) {
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
  } finally {
    $sha.Dispose()
  }
}

function Get-IdentityFromProcess([System.Diagnostics.Process]$proc) {
  $processId = [int]$proc.Id
  $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction Stop
  $creation = $proc.StartTime.ToUniversalTime().ToString("o")
  $path = [string]$proc.Path
  $commandLine = [string]$cim.CommandLine
  if (-not $creation -or -not $path -or -not $commandLine) {
    throw "identity_incomplete"
  }
  $normalizedPath = [IO.Path]::GetFullPath($path).TrimEnd('\').ToLowerInvariant()
  $normalizedCommandLine = $commandLine.Trim()
  return [ordered]@{
    pid = $ProcessId
    creation_time_utc = $creation
    executable_sha256 = Get-Hash $normalizedPath
    command_line_sha256 = Get-Hash $normalizedCommandLine
    identity_complete = $true
  }
}

function Get-Identity([int]$ProcessId) {
  $proc = $null
  try {
    $proc = Get-Process -Id $ProcessId -ErrorAction Stop
  } catch {
    if ([string]$_.FullyQualifiedErrorId -like "NoProcessFoundForGivenId*") {
      Write-Result ([ordered]@{ identity_complete = $false; error = "process_not_found"; pid = $ProcessId }) 4
    }
    throw
  }
  try {
    return Get-IdentityFromProcess $proc
  } finally {
    $proc.Dispose()
  }
}

function Test-ExpectedIdentity([object]$Expected) {
  if (-not $Expected) { return $false }
  $pidValue = 0
  if (-not [int]::TryParse([string]$Expected.pid, [ref]$pidValue) -or $pidValue -le 0) {
    return $false
  }
  if (-not ([string]$Expected.creation_time_utc).Trim()) { return $false }
  if ([string]$Expected.executable_sha256 -notmatch '^[0-9a-fA-F]{64}$') { return $false }
  if ([string]$Expected.command_line_sha256 -notmatch '^[0-9a-fA-F]{64}$') { return $false }
  return $true
}

try {
  $request = ([Console]::In.ReadToEnd() | ConvertFrom-Json)
  if (-not $request -or -not $request.operation) {
    Write-Result ([ordered]@{ error = "invalid_request" }) 2
  }

  if ($request.operation -eq "snapshot") {
    Write-Result (Get-Identity ([int]$request.pid)) 0
  }

  if ($request.operation -eq "discover") {
    $allowed = @{}
    foreach ($name in @($request.allowlisted_names)) {
      if ($name -is [string] -and $name) { $allowed[$name.ToLowerInvariant()] = $true }
    }
    $all = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $selected = New-Object System.Collections.Generic.List[object]
    $frontier = New-Object System.Collections.Generic.Queue[int]
    $frontier.Enqueue([int]$request.root_pid)
    $seen = @{}
    while ($frontier.Count -gt 0) {
      $current = $frontier.Dequeue()
      if ($seen.ContainsKey($current)) { continue }
      $seen[$current] = $true
      $selected.Add((Get-Identity $current))
      foreach ($candidate in $all) {
        if ([int]$candidate.ParentProcessId -eq $current -and $allowed.ContainsKey(([string]$candidate.Name).ToLowerInvariant())) {
          $frontier.Enqueue([int]$candidate.ProcessId)
        }
      }
    }
    Write-Result ([ordered]@{ processes = @($selected); identity_complete = $true }) 0
  }

  if ($request.operation -eq "terminate") {
    $expected = $request.expected
    if (-not (Test-ExpectedIdentity $expected)) {
      Write-Result ([ordered]@{ terminated = $false; error = "process_identity_mismatch"; identity_complete = $false }) 4
    }
    try {
      $proc = Get-Process -Id ([int]$expected.pid) -ErrorAction Stop
    } catch {
      if ([string]$_.FullyQualifiedErrorId -like "NoProcessFoundForGivenId*") {
        Write-Result ([ordered]@{ terminated = $false; error = "process_not_found"; pid = [int]$expected.pid }) 4
      }
      throw
    }
    try {
      try {
        $actual = Get-IdentityFromProcess $proc
      } catch {
        if ($proc.HasExited) {
          Write-Result ([ordered]@{ terminated = $false; error = "process_not_found"; pid = [int]$expected.pid }) 4
        }
        if ([string]$_.Exception.Message -eq "identity_incomplete") {
          Write-Result ([ordered]@{ terminated = $false; error = "process_identity_mismatch"; identity_complete = $false }) 4
        }
        throw
      }
      $match = (
        [int]$actual.pid -eq [int]$expected.pid -and
        [string]$actual.creation_time_utc -ceq [string]$expected.creation_time_utc -and
        [string]$actual.executable_sha256 -ieq [string]$expected.executable_sha256 -and
        [string]$actual.command_line_sha256 -ieq [string]$expected.command_line_sha256
      )
      if (-not $match) {
        Write-Result ([ordered]@{ terminated = $false; error = "process_identity_mismatch"; actual = $actual }) 4
      }
      $proc.Kill()
      if (-not $proc.WaitForExit(5000)) {
        Write-Result ([ordered]@{ terminated = $false; error = "terminate_timeout" }) 3
      }
      Write-Result ([ordered]@{ terminated = $true; pid = [int]$expected.pid }) 0
    } finally {
      $proc.Dispose()
    }
  }

  Write-Result ([ordered]@{ error = "unsupported_operation" }) 2
} catch {
  $message = if ([string]$_.Exception.Message -eq "identity_incomplete") { "identity_incomplete" } else { "guard_failed" }
  Write-Result ([ordered]@{ terminated = $false; identity_complete = $false; error = $message }) 3
}
