<#
.SYNOPSIS
  Install DayZ MCP and, with -Register, register dayz-mcp on a client that does not already have it.

.DESCRIPTION
  Without -Register the script installs the venv and prints the registration
  commands. -Register adds dayz-mcp for Claude Code and Codex only when neither
  client already has that server. The check reads each CLI's stdout and stderr
  separately: "not found" is on stderr. If a registration exists, or the check
  is missing or unreadable, the script stops and does not remove it. A new
  install never removes a name. It probes again immediately before delegating.
  The registration itself is `install_mcp.py --register` (one lock, journal and
  recovery). -ReplaceExistingRegistration is the only path that passes
  --allow-option-removal, and only when a registration is present.

.PARAMETER Register
  Add dayz-mcp when Claude and Codex do not already have it, by running
  install_mcp.py --register. Does not remove an existing registration.

.PARAMETER ReplaceExistingRegistration
  With -Register, delegate with --allow-option-removal when a registration is
  already present. That flag lets the Python installer drop options the
  current registration carries.
#>
param(
  [int]$Port = 8765,
  [string]$KeyFile = "",
  [string]$ServerProfiles = "",
  [string]$ClientProfiles = "",
  [string]$MissionPath = "",
  [string]$ExpectedGameVersion = "",
  [double]$IdleTimeoutSeconds = 1800,
  [switch]$AllowLegacy,
  [switch]$Register,
  [switch]$ReplaceExistingRegistration,
  [switch]$SkipKnowledgePack,
  [switch]$ClaudeNoProgressiveDisclosure,
  [switch]$NoSupervised,
  [string]$Instance = "",
  [string]$GamePath = "",
  [switch]$ValidateOnly,
  [switch]$ValidateRegistrationRollback,
  [switch]$ValidateRegisterInvocation
)

$ErrorActionPreference = "Stop"

# The direct Claude-remove seam is gone. Registration rollback lives in
# install_mcp.py register_transaction. This switch only names that refusal
# and exits before any install or registration side effect.
if ($ValidateRegistrationRollback) {
  [Console]::Error.WriteLine('registration_rollback_seam_removed')
  exit 2
}

function Exit-DayZMcpSelector {
  param([string]$Code)
  [Console]::Error.WriteLine($Code)
  exit 1
}

# Same rules as dayz_mcp.server_cli.validate_entry_selector. Glued
# -Instance= / --instance= forms do not bind to $Instance; they remain here.
foreach ($extra in @($args)) {
  $text = [string]$extra
  if ($text -match '(?i)instance' -or $text -match '(?i)game-?path' -or $text -match '(?i)gamepath') {
    Exit-DayZMcpSelector "unconsumed_selector_argument"
  }
}
function Test-DayZMcpAbsoluteGamePath {
  param([string]$Value)
  # Same results as os.path.isabs on this interpreter: a drive plus a root
  # (C:\ or C:/), or a UNC/device path that starts with two separators.
  # Drive-relative C:relative and root-relative \relative are not absolute.
  if ($Value.IndexOf([char]0) -ge 0) { return $false }
  $normalized = $Value.Replace('/', '\')
  if ($normalized.StartsWith('\\')) { return $true }
  return [regex]::IsMatch($normalized, '\A[A-Za-z]:\\')
}

if ($Instance -ne "") {
  $tokenMatches = [regex]::IsMatch(
    $Instance,
    '\A[a-z0-9](?:[a-z0-9-]{0,31})?\z',
    [System.Text.RegularExpressions.RegexOptions]::None
  )
  if (-not $tokenMatches -or $Instance.Contains('--') -or $Instance.StartsWith('-') -or $Instance.EndsWith('-') -or $Instance -ceq 'default') {
    Exit-DayZMcpSelector "invalid_instance_token"
  }
}
if ($GamePath -ne "") {
  if (-not (Test-DayZMcpAbsoluteGamePath $GamePath)) {
    Exit-DayZMcpSelector "invalid_game_path"
  }
}
if (Test-Path Env:\DAYZ_MCP_INSTANCE) {
  if ($env:DAYZ_MCP_INSTANCE -cne $Instance) {
    Exit-DayZMcpSelector "instance_environment_conflict"
  }
}
if (Test-Path Env:\DAYZ_MCP_PORT) {
  if ($env:DAYZ_MCP_PORT -cne "$Port") {
    Exit-DayZMcpSelector "instance_environment_conflict"
  }
}
if (Test-Path Env:\DAYZ_MCP_GAME_PATH) {
  if ($env:DAYZ_MCP_GAME_PATH -cne $GamePath) {
    Exit-DayZMcpSelector "instance_environment_conflict"
  }
}
if ($ValidateOnly) {
  exit 0
}

$ToolsRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ToolsRoot ".venv-mcp"
$Requirements = Join-Path $ToolsRoot "requirements-mcp.txt"
# Exact pip, not --upgrade (dc90): --upgrade took whatever pip was newest, so a
# pip release could break a fresh install with no change here. CI installs the
# same pin and pyproject.toml [build-system] pins setuptools;
# tests/test_packaging_declarations.py gates all three.
$PipRequirement = "pip==26.2.1"
# Floor matches pyproject.toml requires-python. The py launcher has no range
# syntax; Resolve-HostPython reads `py -0p` and refuses anything older.
$MinPythonVersion = [version]"3.11"
if ($KeyFile -eq "") {
  $KeyFile = Join-Path $ToolsRoot ".dayz_mcp.key"
}

function New-DayZMcpToken {
  $bytes = New-Object byte[] 32
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $rng.GetBytes($bytes)
  } finally {
    $rng.Dispose()
  }
  $token = [Convert]::ToBase64String($bytes)
  $token = $token.TrimEnd("=")
  $token = $token.Replace("+", "-")
  $token = $token.Replace("/", "_")
  return $token
}

function ConvertFrom-PyLauncherList {
  param([string]$ListText)
  $rows = @()
  if (-not $ListText) { return $rows }
  foreach ($line in ($ListText -split '\r?\n')) {
    $match = [regex]::Match($line, '^\s*-V:(\S+)\s+(\*)?\s*(.+?)\s*$')
    if (-not $match.Success) { continue }
    $tag = $match.Groups[1].Value
    $versionMatch = [regex]::Match($tag, '(\d+)\.(\d+)(?:\.(\d+))?')
    if (-not $versionMatch.Success) { continue }
    $patch = 0
    if ($versionMatch.Groups[3].Success -and $versionMatch.Groups[3].Value) {
      $patch = [int]$versionMatch.Groups[3].Value
    }
    $exe = $match.Groups[3].Value.Trim()
    if (-not $exe -or $exe -eq '*') { continue }
    $major = [int]$versionMatch.Groups[1].Value
    $minor = [int]$versionMatch.Groups[2].Value
    $rows += [pscustomobject]@{
      Tag = $tag
      Version = [version]"$major.$minor.$patch"
      Default = [bool]$match.Groups[2].Value
      Path = $exe
    }
  }
  return $rows
}

function Select-PythonFromLauncherList {
  param(
    [string]$ListText,
    [version]$Minimum = $MinPythonVersion
  )
  $chosen = $null
  foreach ($row in @(ConvertFrom-PyLauncherList $ListText)) {
    if ($row.Version -lt $Minimum) { continue }
    if (
      $null -eq $chosen -or
      $row.Version -gt $chosen.Version -or
      (
        $row.Version -eq $chosen.Version -and
        $row.Default -and
        -not $chosen.Default
      )
    ) {
      $chosen = $row
    }
  }
  if ($null -eq $chosen) { return $null }
  return [string]$chosen.Path
}

function Get-PythonFileVersion {
  param([string]$Exe)
  if (-not $Exe) { return $null }
  $previous = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $out = & $Exe -c "import sys; print('%d.%d.%d' % (sys.version_info[0], sys.version_info[1], sys.version_info[2]))" 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    $text = if ($out -is [array]) { [string]$out[-1] } else { [string]$out }
    $text = $text.Trim()
    if ($text -notmatch '^\d+\.\d+\.\d+$') { return $null }
    return [version]$text
  } catch {
    return $null
  } finally {
    $ErrorActionPreference = $previous
  }
}

function Resolve-HostPython {
  $py = Get-Command py -ErrorAction SilentlyContinue
  if ($py) {
    $list = & $py.Source -0p 2>&1 | Out-String
    $exe = Select-PythonFromLauncherList -ListText $list
    if (-not $exe) {
      throw "Python $MinPythonVersion or newer is required; the py launcher listed no matching interpreter."
    }
    $ver = Get-PythonFileVersion $exe
    if ($null -eq $ver -or $ver -lt $MinPythonVersion) {
      throw "Python at $exe is below the required $MinPythonVersion floor."
    }
    return $exe
  }
  $python = Get-Command python -ErrorAction SilentlyContinue
  if (-not $python) {
    throw "Python $MinPythonVersion or newer is required; neither the py launcher nor python.exe was found."
  }
  $ver = Get-PythonFileVersion $python.Source
  if ($null -eq $ver -or $ver -lt $MinPythonVersion) {
    $shown = if ($null -eq $ver) { "unreadable" } else { "$ver" }
    throw "python.exe reports $shown; Python $MinPythonVersion or newer is required."
  }
  return $python.Source
}

function Invoke-Python {
  param([string[]]$Arguments)
  $exe = Resolve-HostPython
  Write-Host "using $exe"
  & $exe @Arguments
}

function Write-DayZMcpConfig {
  param([string]$Path, [string]$JsonConfig)
  if ($Path -eq "") { return }
  $dir = Split-Path -Parent $Path
  if ($dir -and -not (Test-Path -LiteralPath $dir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
  }
  Set-Content -LiteralPath $Path -Encoding ASCII -Value $JsonConfig
  Write-Host "wrote config $Path"
}

function Test-SamePath {
  param([string]$Actual, [string]$Expected)
  if (-not $Actual -or -not $Expected) { return $false }
  try {
    return [string]::Equals(
      [IO.Path]::GetFullPath($Actual),
      [IO.Path]::GetFullPath($Expected),
      [StringComparison]::OrdinalIgnoreCase
    )
  } catch {
    return $false
  }
}

function Get-TextField {
  param([string]$Text, [string]$Name)
  $matches = [regex]::Matches(
    $Text,
    '(?m)^\s*' + [regex]::Escape($Name) + ':\s*(.*?)\s*$'
  )
  foreach ($match in $matches) {
    $match.Groups[1].Value.Trim()
  }
}

function Get-TextFlagValues {
  param([string]$ArgsText, [string]$Flag)
  $pattern = '(?:^|\s)' + [regex]::Escape($Flag) +
    '\s+(.+?)(?=\s+(?:-m|--[A-Za-z][A-Za-z0-9-]*)(?:\s|$)|$)'
  foreach ($match in [regex]::Matches($ArgsText, $pattern)) {
    $match.Groups[1].Value.Trim().Trim('"')
  }
}

function Get-TextFlagCount {
  param([string]$ArgsText, [string]$Flag)
  $pattern = '(?:^|\s)' + [regex]::Escape($Flag) + '(?=\s|$)'
  return [regex]::Matches($ArgsText, $pattern).Count
}

function Test-CanonicalTextArguments {
  param([string]$ArgsText, [object[]]$ExpectedArguments)
  $valueFlags = @(
    '-m', '--port', '--keyfile', '--expected-game-version', '--idle-timeout',
    '--exec-allowlist', '--exec-audit-path', '--client-platform', '--task-label', '--tool-pack',
    '--instance', '--game-path'
  )
  $booleanFlags = @(
    '--require-version', '--enable-exec-enforce', '--no-daemon-autospawn',
    '--no-progressive-disclosure', '--client', '--supervised', '--daemon', '--embedded'
  )
  $allowed = @($valueFlags) + @($booleanFlags)
  $matches = [regex]::Matches($ArgsText, '(?<!\S)(-{1,2}\S+)')
  if ($matches.Count -eq 0 -or $ArgsText.Substring(0, $matches[0].Index).Trim()) {
    return $false
  }
  $actual = [ordered]@{}
  for ($index = 0; $index -lt $matches.Count; $index++) {
    $option = $matches[$index].Groups[1].Value
    if (
      $option.Contains('=') -or
      -not ($allowed -ccontains $option) -or
      $actual.Contains($option)
    ) { return $false }
    $valueStart = $matches[$index].Index + $matches[$index].Length
    $valueEnd = if ($index + 1 -lt $matches.Count) {
      $matches[$index + 1].Index
    } else {
      $ArgsText.Length
    }
    $trailing = $ArgsText.Substring($valueStart, $valueEnd - $valueStart).Trim()
    if ($booleanFlags -ccontains $option) {
      if ($trailing) { return $false }
      $actual[$option] = $null
    } else {
      if (-not $trailing) { return $false }
      $actual[$option] = $trailing.Trim('"')
    }
  }

  $expected = [ordered]@{}
  for ($index = 0; $index -lt $ExpectedArguments.Count;) {
    $option = $ExpectedArguments[$index]
    if (
      $option -isnot [string] -or
      $option.Contains('=') -or
      -not ($allowed -ccontains $option) -or
      $expected.Contains($option)
    ) { return $false }
    if ($booleanFlags -ccontains $option) {
      $expected[$option] = $null
      $index++
    } else {
      if (
        $index + 1 -ge $ExpectedArguments.Count -or
        $ExpectedArguments[$index + 1] -isnot [string] -or
        -not $ExpectedArguments[$index + 1] -or
        $ExpectedArguments[$index + 1].StartsWith('-')
      ) { return $false }
      $expected[$option] = [string]$ExpectedArguments[$index + 1]
      $index += 2
    }
  }
  if ($actual.Count -ne $expected.Count) { return $false }
  foreach ($option in $expected.Keys) {
    if (
      -not $actual.Contains($option) -or
      [string]$actual[$option] -cne [string]$expected[$option]
    ) { return $false }
  }
  return $true
}

function Test-ClaudeRegistration {
  param(
    [string]$Text,
    [string]$ExpectedCommand,
    [string]$ExpectedKeyFile,
    [string]$ExpectedPort,
    [object[]]$ExpectedArguments
  )
  $types = @(Get-TextField $Text 'Type')
  $commands = @(Get-TextField $Text 'Command')
  $argumentFields = @(Get-TextField $Text 'Args')
  if ($types.Count -ne 1 -or $commands.Count -ne 1 -or $argumentFields.Count -ne 1) {
    return $false
  }
  $type = $types[0]
  $command = $commands[0]
  $argsText = $argumentFields[0]
  $modules = @(Get-TextFlagValues $argsText '-m')
  $ports = @(Get-TextFlagValues $argsText '--port')
  $keyfiles = @(Get-TextFlagValues $argsText '--keyfile')
  $platforms = @(Get-TextFlagValues $argsText '--client-platform')
  return (
    $type -ceq 'stdio' -and
    (Test-SamePath $command $ExpectedCommand) -and
    (Test-CanonicalTextArguments $argsText $ExpectedArguments) -and
    $modules.Count -eq 1 -and $modules[0] -ceq 'dayz_mcp' -and
    (Get-TextFlagCount $argsText '--client') -eq 1 -and
    (Get-TextFlagCount $argsText '--daemon') -eq 0 -and
    (Get-TextFlagCount $argsText '--embedded') -eq 0 -and
    $ports.Count -eq 1 -and $ports[0] -ceq $ExpectedPort -and
    $keyfiles.Count -eq 1 -and (Test-SamePath $keyfiles[0] $ExpectedKeyFile) -and
    $platforms.Count -eq 1 -and $platforms[0] -ceq 'claude'
  )
}

function Get-ArrayFlagCount {
  param([object[]]$Arguments, [string]$Flag)
  return @($Arguments | Where-Object { $_ -is [string] -and $_ -ceq $Flag }).Count
}

function Test-ExactArrayValue {
  param(
    [object[]]$Arguments,
    [string]$Flag,
    [string]$Expected
  )
  $matches = @()
  for ($index = 0; $index -lt $Arguments.Count; $index++) {
    if ($Arguments[$index] -is [string] -and $Arguments[$index] -ceq $Flag) {
      $matches += $index
    }
  }
  if ($matches.Count -ne 1) { return $false }
  $valueIndex = $matches[0] + 1
  if ($valueIndex -ge $Arguments.Count -or $Arguments[$valueIndex] -isnot [string]) {
    return $false
  }
  return $Arguments[$valueIndex] -ceq $Expected
}

function Test-CanonicalArrayArguments {
  param([object[]]$Arguments, [object[]]$ExpectedArguments)
  if ($Arguments.Count -ne $ExpectedArguments.Count) { return $false }
  $valueFlags = @(
    '-m', '--port', '--keyfile', '--expected-game-version', '--idle-timeout',
    '--exec-allowlist', '--exec-audit-path', '--client-platform', '--task-label', '--tool-pack',
    '--instance', '--game-path'
  )
  $booleanFlags = @(
    '--require-version', '--enable-exec-enforce', '--no-daemon-autospawn',
    '--no-progressive-disclosure', '--client', '--supervised', '--daemon', '--embedded'
  )
  $allowed = @($valueFlags) + @($booleanFlags)
  $seen = @{}
  for ($index = 0; $index -lt $Arguments.Count;) {
    if (
      $Arguments[$index] -isnot [string] -or
      $ExpectedArguments[$index] -isnot [string] -or
      $Arguments[$index] -cne $ExpectedArguments[$index]
    ) { return $false }
    $option = [string]$Arguments[$index]
    if (
      $option.Contains('=') -or
      -not ($allowed -ccontains $option) -or
      $seen.ContainsKey($option)
    ) { return $false }
    $seen[$option] = $true
    if ($booleanFlags -ccontains $option) {
      $index++
    } else {
      if (
        $index + 1 -ge $Arguments.Count -or
        $Arguments[$index + 1] -isnot [string] -or
        $ExpectedArguments[$index + 1] -isnot [string] -or
        $Arguments[$index + 1] -cne $ExpectedArguments[$index + 1] -or
        -not $Arguments[$index + 1] -or
        $Arguments[$index + 1].StartsWith('-')
      ) { return $false }
      $index += 2
    }
  }
  return $true
}

function Test-CodexJsonShape {
  param([string]$Text)
  foreach ($name in @('transport', 'type', 'command', 'args')) {
    $pattern = '"' + [regex]::Escape($name) + '"\s*:'
    if ([regex]::Matches($Text, $pattern).Count -ne 1) { return $false }
  }
  return $true
}

function Test-CodexRegistration {
  param(
    [object]$Config,
    [string]$ExpectedCommand,
    [string]$ExpectedKeyFile,
    [string]$ExpectedPort,
    [object[]]$ExpectedArguments
  )
  if (
    $Config -isnot [pscustomobject] -or
    $null -eq $Config.transport -or
    $Config.transport -isnot [pscustomobject] -or
    $Config.transport -is [Array]
  ) { return $false }
  $transport = $Config.transport
  if (
    $transport.type -isnot [string] -or
    $transport.command -isnot [string] -or
    $transport.args -isnot [Array]
  ) { return $false }
  $arguments = @($transport.args)
  if (@($arguments | Where-Object { $_ -isnot [string] }).Count -ne 0) {
    return $false
  }
  return (
    $transport.type -ceq 'stdio' -and
    (Test-SamePath ([string]$transport.command) $ExpectedCommand) -and
    (Test-CanonicalArrayArguments $arguments $ExpectedArguments) -and
    (Test-ExactArrayValue $arguments '-m' 'dayz_mcp') -and
    (Get-ArrayFlagCount $arguments '--client') -eq 1 -and
    (Get-ArrayFlagCount $arguments '--daemon') -eq 0 -and
    (Get-ArrayFlagCount $arguments '--embedded') -eq 0 -and
    (Test-ExactArrayValue $arguments '--port' $ExpectedPort) -and
    (Test-ExactArrayValue $arguments '--keyfile' $ExpectedKeyFile) -and
    (Test-ExactArrayValue $arguments '--client-platform' 'codex')
  )
}

# Windows PowerShell 5.1 turns a native command's stderr into an ErrorRecord.
# With $ErrorActionPreference = 'Stop', both `2>&1` and `2>file` then throw
# before the exit code or the text can be kept (a not-found CLI exits 1 with
# the phrase on stderr and an empty stdout). Process redirection never enters
# that error stream. A .cmd shim cannot be Process.FileName when
# UseShellExecute is false, so cmd.exe runs a wrapper that calls the resolved
# shim and returns its exit code.
# CommandLineToArgvW rules (the same encoding CPython uses for a native
# CreateProcess). A trailing backslash inside a quoted argument must be
# doubled or it escapes the closing quote. cmd.exe then parses that line
# again, so a native .exe is started directly and never goes through a
# batch wrapper.
function Format-DayZMcpNativeCommandLine {
  param([string[]]$Arguments)
  $parts = @()
  foreach ($argument in @($Arguments)) {
    $text = [string]$argument
    $quote = ($text.Length -eq 0) -or $text.Contains(' ') -or $text.Contains("`t")
    $builder = New-Object System.Text.StringBuilder
    if ($quote) { [void]$builder.Append('"') }
    $slashes = 0
    foreach ($ch in $text.ToCharArray()) {
      if ($ch -eq '\') {
        $slashes++
        continue
      }
      if ($ch -eq '"') {
        if ($slashes -gt 0) { [void]$builder.Append('\', ($slashes * 2)) }
        [void]$builder.Append('\"')
        $slashes = 0
        continue
      }
      if ($slashes -gt 0) {
        [void]$builder.Append('\', $slashes)
        $slashes = 0
      }
      [void]$builder.Append($ch)
    }
    if ($slashes -gt 0) {
      $count = if ($quote) { $slashes * 2 } else { $slashes }
      [void]$builder.Append('\', $count)
    }
    if ($quote) { [void]$builder.Append('"') }
    $parts += $builder.ToString()
  }
  return ($parts -join ' ')
}

function Invoke-NativeRegistrationCommand {
  param([string]$CommandPath, [string[]]$Arguments)
  $failed = @{ ExitCode = $null; Stdout = ''; Stderr = '' }
  $wrapper = $null
  $process = $null
  try {
    $extension = [IO.Path]::GetExtension($CommandPath)
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.UseShellExecute = $false
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.CreateNoWindow = $true
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $start.StandardOutputEncoding = $utf8
    $start.StandardErrorEncoding = $utf8
    if ($extension -match '(?i)^\.(exe|com)$') {
      $start.FileName = $CommandPath
      $start.Arguments = Format-DayZMcpNativeCommandLine @($Arguments)
    } else {
      # A .cmd shim cannot be Process.FileName when UseShellExecute is false.
      $wrapper = Join-Path ([IO.Path]::GetTempPath()) ('dayz-mcp-reg-' + [guid]::NewGuid().ToString('n') + '.cmd')
      # Shim probes only pass mcp/get tokens. Keep the quoted form those
      # shims log. Native Python does not use this branch.
      $command = '"' + ($CommandPath -replace '"', '""') + '"'
      foreach ($argument in @($Arguments)) {
        $command += ' "' + ([string]$argument -replace '"', '""') + '"'
      }
      [IO.File]::WriteAllLines(
        $wrapper,
        [string[]]@('@echo off', $command, 'exit /b %ERRORLEVEL%'),
        [Text.Encoding]::Default
      )
      $comspec = $env:ComSpec
      if (-not $comspec) { $comspec = 'cmd.exe' }
      $start.FileName = $comspec
      $start.Arguments = '/d /c "' + ($wrapper -replace '"', '""') + '"'
    }
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $start
    if (-not $process.Start()) { return $failed }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $exitCode = $process.ExitCode
    if (($exitCode -isnot [int]) -or ($exitCode -is [bool])) { return $failed }
    return @{
      ExitCode = $exitCode
      Stdout = [string]$stdoutTask.Result
      Stderr = [string]$stderrTask.Result
    }
  } catch {
    return $failed
  } finally {
    if ($process) { $process.Dispose() }
    if ($wrapper) { Remove-Item -LiteralPath $wrapper -ErrorAction SilentlyContinue }
  }
}

function Test-DayZMcpNativePe {
  param([string]$Path)
  $stream = $null
  try {
    $stream = [IO.File]::Open($Path, 'Open', 'Read', 'Read')
    $dos = New-Object byte[] 64
    if ($stream.Read($dos, 0, 64) -ne 64) { return $false }
    if ($dos[0] -ne 0x4D -or $dos[1] -ne 0x5A) { return $false }
    $peOffset = [BitConverter]::ToUInt32($dos, 0x3C)
    if ($peOffset -lt 64) { return $false }
    $stream.Position = [int64]$peOffset
    $pe = New-Object byte[] 26
    if ($stream.Read($pe, 0, 26) -ne 26) { return $false }
    if ($pe[0] -ne 0x50 -or $pe[1] -ne 0x45 -or $pe[2] -ne 0 -or $pe[3] -ne 0) { return $false }
    $machine = [BitConverter]::ToUInt16($pe, 4)
    $magic = [BitConverter]::ToUInt16($pe, 24)
    return ($machine -eq 0x8664) -and ($magic -eq 0x20B)
  } catch {
    return $false
  } finally {
    if ($stream) { $stream.Dispose() }
  }
}

function Resolve-DayZMcpNativeClientExecutable {
  param([ValidateSet('claude', 'codex')][string]$Role)
  $expected = "$Role.exe"
  $candidates = @()
  $found = Get-Command $expected -CommandType Application -ErrorAction SilentlyContinue
  if ($found -and $found.Source) { $candidates += [string]$found.Source }
  $shim = Get-Command "$Role.cmd" -ErrorAction SilentlyContinue
  if ($shim -and $shim.Source) {
    $candidates += (Join-Path (Split-Path -Parent $shim.Source) $expected)
    try {
      $text = [IO.File]::ReadAllText($shim.Source)
    } catch {
      $text = ''
    }
    foreach ($match in [regex]::Matches($text, '(?i)(?<path>[A-Za-z]:\\[^"\r\n|<>]*\\' + [regex]::Escape($expected) + ')')) {
      $candidates += $match.Groups['path'].Value
    }
  }
  foreach ($candidate in $candidates) {
    if (-not $candidate) { continue }
    if ([IO.Path]::GetFileName($candidate) -ine $expected) { continue }
    if (-not (Test-Path -LiteralPath $candidate)) { continue }
    if (-not (Test-DayZMcpNativePe $candidate)) { continue }
    return (Resolve-Path -LiteralPath $candidate).Path
  }
  return ''
}

function Get-ClientRegistrationProbe {
  param(
    [ValidateSet('claude', 'codex')][string]$Client,
    [string]$ServerName = 'dayz-mcp'
  )
  $commandName = if ($Client -eq 'claude') { 'claude' } else { 'codex.cmd' }
  $command = Get-Command $commandName -ErrorAction SilentlyContinue
  if (-not $command) {
    return @{
      Client = $Client
      CommandMissing = $true
      ExitCode = $null
      Stdout = ''
      Stderr = ''
    }
  }
  if ($ServerName -eq 'dayz-mcp') {
    $arguments = @('mcp', 'get', 'dayz-mcp')
  } else {
    $arguments = @('mcp', 'get', $ServerName)
  }
  if ($Client -eq 'codex') {
    $arguments += '--json'
  }
  $captured = Invoke-NativeRegistrationCommand -CommandPath $command.Source -Arguments $arguments
  return @{
    Client = $Client
    CommandMissing = $false
    ExitCode = $captured.ExitCode
    Stdout = [string]$captured.Stdout
    Stderr = [string]$captured.Stderr
    ServerName = $ServerName
  }
}

# Absent matches the not-found text these CLIs print on stderr
# (reports/security/installer-not-found-fixtures-v1.json): Claude exit 1,
# empty stdout, `No MCP server named "dayz-mcp".`; Codex exit 1, empty
# stdout, `No MCP server named 'dayz-mcp' found`. A registration is present
# only as exit 0, a shape on stdout, and empty stderr (install_mcp.py treats
# any stderr on a zero exit as registration_probe_ambiguous). Anything else
# is unreadable. -ReplaceExistingRegistration overrides a parsed registration
# only; an unreadable probe still refuses.
function Get-RegistrationReplaceDecision {
  param(
    $Claude,
    $Codex,
    [bool]$ReplaceExistingRegistration
  )
  $classify = {
    param($Name, $Grammar, $Probe)
    if ($Probe -isnot [System.Collections.IDictionary]) {
      return @{ Name = $Name; Kind = 'unreadable'; Detail = "${Name}: probe is missing" }
    }
    if ([bool]$Probe['CommandMissing']) {
      return @{ Name = $Name; Kind = 'unreadable'; Detail = "${Name}: command missing" }
    }
    $stdout = $Probe['Stdout']
    $stderr = $Probe['Stderr']
    $exitCode = $Probe['ExitCode']
    if ($stdout -isnot [string]) {
      return @{ Name = $Name; Kind = 'unreadable'; Detail = "${Name}: stdout is missing" }
    }
    if ($stderr -isnot [string]) {
      return @{ Name = $Name; Kind = 'unreadable'; Detail = "${Name}: stderr is missing" }
    }
    if (($exitCode -isnot [int]) -or ($exitCode -is [bool])) {
      return @{ Name = $Name; Kind = 'unreadable'; Detail = "${Name}: exit code is missing" }
    }
    $serverName = [string]$Probe['ServerName']
    if (-not $serverName) { $serverName = 'dayz-mcp' }
    $absentName = [regex]::Escape($serverName)
    $absentPattern = if ($Grammar -eq 'claude') {
      "No MCP server named `"$absentName`"\."
    } else {
      "No MCP server named '$absentName' found"
    }
    $absentPhrase = [regex]::IsMatch($stderr, $absentPattern)
    $shape = $false
    if ($Grammar -eq 'claude') {
      $shape = (
        [regex]::IsMatch($stdout, '(?m)^\s*Type:\s*\S') -and
        [regex]::IsMatch($stdout, '(?m)^\s*Command:\s*\S') -and
        [regex]::IsMatch($stdout, '(?m)^\s*Args:\s*\S') -and
        [regex]::IsMatch($stdout, [regex]::Escape($serverName))
      )
    } elseif ($stdout.Trim()) {
      try {
        $parsed = $stdout | ConvertFrom-Json
        $transport = $parsed.transport
        $shape = (
          $null -ne $transport -and
          $transport -isnot [System.Array] -and
          ($transport.type -is [string]) -and [bool]$transport.type -and
          ($transport.command -is [string]) -and [bool]$transport.command -and
          $null -ne $transport.args
        )
      } catch {
        $shape = $false
      }
    }
    if (($exitCode -eq 0) -and $stderr.Trim()) {
      return @{ Name = $Name; Kind = 'unreadable'; Detail = "${Name}: unparseable output" }
    }
    if (($exitCode -eq 0) -and $shape -and -not $absentPhrase) {
      return @{ Name = $Name; Kind = 'present'; Detail = '' }
    }
    if (($exitCode -eq 1) -and $absentPhrase -and -not $shape) {
      return @{ Name = $Name; Kind = 'absent'; Detail = '' }
    }
    if (($exitCode -eq 0) -or $absentPhrase) {
      return @{ Name = $Name; Kind = 'unreadable'; Detail = "${Name}: unparseable output" }
    }
    if ($exitCode -eq 1) {
      return @{ Name = $Name; Kind = 'unreadable'; Detail = "${Name}: exit code 1 without the not-found message" }
    }
    return @{ Name = $Name; Kind = 'unreadable'; Detail = "${Name}: exit code $exitCode" }
  }
  $claudeResult = & $classify 'Claude' 'claude' $Claude
  $codexResult = & $classify 'Codex' 'codex' $Codex
  $unreadable = @()
  if ($claudeResult.Kind -eq 'unreadable') { $unreadable += $claudeResult }
  if ($codexResult.Kind -eq 'unreadable') { $unreadable += $codexResult }
  if ($unreadable.Count) {
    $detail = ($unreadable | ForEach-Object { $_.Detail }) -join '; '
    return @{
      Action = 'refuse'
      Reason = "registration check failed for ${detail}. Stopped before removing anything."
    }
  }
  $present = @()
  if ($claudeResult.Kind -eq 'present') { $present += $claudeResult }
  if ($codexResult.Kind -eq 'present') { $present += $codexResult }
  if ($present.Count -and -not $ReplaceExistingRegistration) {
    $names = ($present | ForEach-Object { $_.Name }) -join ' and '
    return @{
      Action = 'refuse'
      Reason = "dayz-mcp is already registered for $names. install-mcp.ps1 -Register does not replace an existing registration. Re-register with python tools/install_mcp.py --register (it keeps options the registration already carries unless --allow-option-removal is passed), or pass -ReplaceExistingRegistration to replace it with this script."
      AllowOptionRemoval = $false
    }
  }
  return @{
    Action = 'proceed'
    Reason = ''
    AllowOptionRemoval = [bool]($present.Count -gt 0 -and $ReplaceExistingRegistration)
  }
}

function Get-DayZMcpInstallerErrorToken {
  param([string]$Text)
  if (-not $Text) { return '' }
  $match = [regex]::Match($Text, '"error"\s*:\s*"([A-Za-z0-9_:-]+)"')
  if ($match.Success) { return $match.Groups[1].Value }
  $line = [regex]::Match($Text, '(?m)^(registration_[A-Za-z0-9_:-]+)\s*$')
  if ($line.Success) { return $line.Groups[1].Value }
  return ''
}

function Get-DayZMcpInstallerRegisterArguments {
  param(
    [bool]$AllowOptionRemoval,
    [string]$ClaudeExe,
    [string]$CodexExe
  )
  $installer = Join-Path $ToolsRoot 'install_mcp.py'
  $timeout = "$IdleTimeoutSeconds"
  $argv = @(
    $installer,
    '--register',
    '--skip-knowledge-pack',
    '--port', "$Port",
    '--keyfile', $KeyFile,
    '--idle-timeout-seconds', $timeout,
    '--claude-exe', $ClaudeExe,
    '--codex-exe', $CodexExe
  )
  if ($Instance -ne '') { $argv += @('--instance', $Instance) }
  if ($GamePath -ne '') { $argv += @('--game-path', $GamePath) }
  if ($ServerProfiles -ne '') { $argv += @('--server-profiles', $ServerProfiles) }
  if ($ClientProfiles -ne '') { $argv += @('--client-profiles', $ClientProfiles) }
  if ($MissionPath -ne '') { $argv += @('--mission-path', $MissionPath) }
  if ($ExpectedGameVersion -ne '') { $argv += @('--expected-game-version', $ExpectedGameVersion) }
  if ($AllowLegacy) { $argv += '--allow-legacy' }
  if ($NoSupervised) { $argv += '--no-supervised' }
  if ($ClaudeNoProgressiveDisclosure) { $argv += '--claude-no-progressive-disclosure' }
  if ($AllowOptionRemoval) { $argv += '--allow-option-removal' }
  return $argv
}

function Invoke-DayZMcpInstallerRegister {
  param([string]$Python, [string[]]$Arguments)
  $command = $Python
  $commandArgs = @('-B') + @($Arguments)
  if ($env:DAYZ_MCP_REGISTER_SEAM) {
    $command = $env:DAYZ_MCP_REGISTER_SEAM
    $commandArgs = @($Python) + $commandArgs
  }
  $captured = Invoke-NativeRegistrationCommand -CommandPath $command -Arguments $commandArgs
  $combined = ([string]$captured.Stdout) + "`n" + ([string]$captured.Stderr)
  $token = Get-DayZMcpInstallerErrorToken $combined
  $exitCode = $captured.ExitCode
  if (($exitCode -isnot [int]) -or ($exitCode -is [bool])) {
    $exitCode = 1
    if (-not $token) { $token = 'registration_invocation_failed' }
  }
  return @{
    ExitCode = [int]$exitCode
    Token = $token
    Stdout = [string]$captured.Stdout
    Stderr = [string]$captured.Stderr
  }
}

function Submit-DayZMcpRegistration {
  param(
    [string]$Python,
    [bool]$AllowOptionRemoval,
    [string]$ClaudeExe,
    [string]$CodexExe
  )
  $argv = Get-DayZMcpInstallerRegisterArguments -AllowOptionRemoval:$AllowOptionRemoval -ClaudeExe $ClaudeExe -CodexExe $CodexExe
  return Invoke-DayZMcpInstallerRegister -Python $Python -Arguments $argv
}

function Exit-DayZMcpRegistrationResult {
  param($Result)
  if ($Result.Stdout) { Write-Host $Result.Stdout }
  if ($Result.ExitCode -eq 0) { return }
  if ($Result.Stderr) { [Console]::Error.WriteLine($Result.Stderr) }
  if ($Result.Token) { [Console]::Error.WriteLine($Result.Token) }
  exit $Result.ExitCode
}

if ($ValidateRegisterInvocation) {
  if (-not $env:DAYZ_MCP_REGISTER_SEAM) {
    [Console]::Error.WriteLine('registration_seam_required')
    exit 2
  }
  $claudeExe = Resolve-DayZMcpNativeClientExecutable -Role claude
  $codexExe = Resolve-DayZMcpNativeClientExecutable -Role codex
  if (-not $claudeExe -or -not $codexExe) {
    [Console]::Error.WriteLine('installer_cli_not_native_exe')
    exit 2
  }
  $registerResult = Submit-DayZMcpRegistration -Python (Join-Path $VenvDir 'Scripts\python.exe') -AllowOptionRemoval:([bool]$ReplaceExistingRegistration) -ClaudeExe $claudeExe -CodexExe $codexExe
  Exit-DayZMcpRegistrationResult $registerResult
  exit 0
}

if (-not (Test-Path -LiteralPath $VenvDir)) {
  Write-Host "creating venv $VenvDir"
  Invoke-Python @("-m", "venv", $VenvDir)
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
& $VenvPython -m pip install $PipRequirement
if ($LASTEXITCODE -ne 0) {
  throw "pip install $PipRequirement failed"
}
& $VenvPython -m pip install -r $Requirements
if ($LASTEXITCODE -ne 0) {
  throw "pip install -r $Requirements failed"
}
& $VenvPython -m pip install -e $ToolsRoot
if ($LASTEXITCODE -ne 0) {
  throw "pip install -e $ToolsRoot failed"
}
# pywin32 (296b): the stdio Steam check reads the real HKCU and the daemon is
# spawned outside the client's app container, both through WMI (wmi_host). Without
# it both fall back to the app's virtualized registry view; refuse the install here
# instead of letting that fallback happen at runtime.
& $VenvPython -c "import pythoncom, win32com.client"
if ($LASTEXITCODE -ne 0) {
  throw "pywin32 is missing from $VenvDir (import pythoncom, win32com.client failed); install requirements-mcp.txt again"
}

if (-not $SkipKnowledgePack) {
  $knowledgePackArgs = @("-m", "dayz_mcp.knowledge_pack", "install")
  if ($Instance -ne "") {
    # A named install does not own the global skills directory.
    $knowledgePackArgs += @("--instance", $Instance)
  } elseif ($Register) {
    $knowledgePackArgs += "--sync"
  }
  & $VenvPython @knowledgePackArgs
  if ($LASTEXITCODE -ne 0) {
    throw "Knowledge Pack installation failed"
  }
} else {
  Write-Host "Knowledge Pack installation skipped by -SkipKnowledgePack."
}

if (-not (Test-Path -LiteralPath $KeyFile)) {
  $key = New-DayZMcpToken
  Set-Content -LiteralPath $KeyFile -Encoding ASCII -Value $key
  Write-Host "generated keyfile $KeyFile"
} else {
  $key = (Get-Content -LiteralPath $KeyFile -Raw).Trim()
  Write-Host "using existing keyfile $KeyFile"
}

if (-not $key) {
  throw "empty keyfile: $KeyFile"
}

$jsonConfig = @{
  url = "http://127.0.0.1:$Port/"
  key = $key
  pollHz = 5
} | ConvertTo-Json -Compress

$SampleRoot = Join-Path $ToolsRoot "_mcp_config"
Write-DayZMcpConfig (Join-Path $SampleRoot "server_profiles\dayz_mcp.json") $jsonConfig
Write-DayZMcpConfig (Join-Path $SampleRoot "client_profiles\dayz_mcp.json") $jsonConfig
Write-DayZMcpConfig (Join-Path $SampleRoot "mpmissions\dayzOffline.chernarusplus\dayz_mcp.json") $jsonConfig

if ($ServerProfiles -ne "") {
  Write-DayZMcpConfig (Join-Path $ServerProfiles "dayz_mcp.json") $jsonConfig
}
if ($ClientProfiles -ne "") {
  Write-DayZMcpConfig (Join-Path $ClientProfiles "dayz_mcp.json") $jsonConfig
}
if ($MissionPath -ne "") {
  Write-DayZMcpConfig (Join-Path $MissionPath "dayz_mcp.json") $jsonConfig
}

# Sessions register in CLIENT mode (broker refactor): they proxy bridge calls to
# a shared daemon that owns :Port and is lazily spawned (detached) on first use,
# so MANY Cowork sessions can drive ONE game at once. The daemon-policy flags
# below travel in this command and are forwarded to the daemon spawn. Bare
# `-m dayz_mcp` (no mode flag) stays EMBEDDED for CI/offline and the in-game gates.
$serverArgs = @("-m", "dayz_mcp", "--client")
# The supervisor gives server_reload and the lease handoff across a worker
# recycle; the box runs this way. -NoSupervised opts out (0f68).
if (-not $NoSupervised) {
  $serverArgs += @("--supervised")
}
$serverArgs += @("--keyfile", $KeyFile, "--port", "$Port")
if ($ExpectedGameVersion -ne "") {
  $serverArgs += @("--expected-game-version", $ExpectedGameVersion)
}
if (-not $AllowLegacy) {
  $serverArgs += @("--require-version")
}
# Idle self-shutdown: the daemon releases :Port and exits after N seconds with no
# game polling NOR client requests, so a hung/abandoned session never holds the
# port forever. 0 disables. Forwarded to the daemon spawn.
$serverArgs += @("--idle-timeout", "$IdleTimeoutSeconds")
if (-not [string]::IsNullOrEmpty($Instance)) {
  $serverArgs += @("--instance", $Instance)
}
if (-not [string]::IsNullOrEmpty($GamePath)) {
  $serverArgs += @("--game-path", $GamePath)
}
$ServerName = if (-not [string]::IsNullOrEmpty($Instance)) { "dayz-mcp-$Instance" } else { "dayz-mcp" }

$claudeArgs = $serverArgs + @('--client-platform','claude')
# Claude Code does not re-list tools after tools/list_changed (#93). The server
# already lists the full catalog for --client-platform claude; the switch is still
# accepted and adds the explicit flag. Codex keeps the compact default.
if ($ClaudeNoProgressiveDisclosure) {
  $claudeArgs += @('--no-progressive-disclosure')
}
$codexArgs  = $serverArgs + @('--client-platform','codex')
$quotedClaudeArgs = ($claudeArgs | ForEach-Object { '"' + ($_ -replace '"','\"') + '"' }) -join " "
$quotedCodexArgs = ($codexArgs | ForEach-Object { '"' + ($_ -replace '"','\"') + '"' }) -join " "
$claudeCommand = "claude mcp add $ServerName -s user -- `"$VenvPython`" $quotedClaudeArgs"
$codexCommand = "codex.cmd mcp add $ServerName -- `"$VenvPython`" $quotedCodexArgs"

Write-Host ""
Write-Host "Claude Code registration command:"
Write-Host $claudeCommand
Write-Host ""
Write-Host "Codex registration command:"
Write-Host $codexCommand
Write-Host ""
Write-Host "Claude .mcp.json equivalent:"
$mcpJson = @{
  mcpServers = @{
    $ServerName = @{
      type = "stdio"
      command = $VenvPython
      args = $claudeArgs
    }
  }
} | ConvertTo-Json -Depth 8
Write-Host $mcpJson

if ($Register) {
  $registrationDecision = Get-RegistrationReplaceDecision -Claude (Get-ClientRegistrationProbe -Client claude -ServerName $ServerName) -Codex (Get-ClientRegistrationProbe -Client codex -ServerName $ServerName) -ReplaceExistingRegistration:([bool]$ReplaceExistingRegistration)
  if ($registrationDecision.Action -ne 'proceed') {
    throw $registrationDecision.Reason
  }
  Write-Host ""
  Write-Host "registering dayz-mcp with Claude Code and Codex"
  # The probes above can go stale before the installer lock. On a new install,
  # look again. Option removal is only for a registration that is present.
  if (-not $ReplaceExistingRegistration) {
    $registrationDecision = Get-RegistrationReplaceDecision -Claude (Get-ClientRegistrationProbe -Client claude -ServerName $ServerName) -Codex (Get-ClientRegistrationProbe -Client codex -ServerName $ServerName) -ReplaceExistingRegistration:$false
    if ($registrationDecision.Action -ne 'proceed') {
      throw $registrationDecision.Reason
    }
  }
  $claudeExe = Resolve-DayZMcpNativeClientExecutable -Role claude
  $codexExe = Resolve-DayZMcpNativeClientExecutable -Role codex
  if (-not $claudeExe -or -not $codexExe) {
    throw "installer_cli_not_native_exe"
  }
  # DAYZ_MCP_REGISTER_DELEGATE
  $registerResult = Submit-DayZMcpRegistration -Python $VenvPython -AllowOptionRemoval:([bool]$registrationDecision.AllowOptionRemoval) -ClaudeExe $claudeExe -CodexExe $codexExe
  # DAYZ_MCP_REGISTER_DELEGATE_END
  Exit-DayZMcpRegistrationResult $registerResult
  Write-Host "VERIFY OK: registration delegated to install_mcp.py --register."
} else {
  Write-Host ""
  Write-Host "default is registration print-only; rerun with -Register to add dayz-mcp when Claude and Codex do not already have it. An existing registration is left unchanged (a new install does not remove it): re-register with python tools/install_mcp.py --register, or pass -ReplaceExistingRegistration."
}
