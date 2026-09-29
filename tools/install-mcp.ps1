<#
.SYNOPSIS
  Install DayZ MCP and, with -Register, register dayz-mcp on a client that does not already have it.

.DESCRIPTION
  Without -Register the script installs the venv and prints the registration
  commands. -Register registers dayz-mcp with Claude Code and Codex only when
  neither client already has that server. If a registration exists, or that
  check is missing or unreadable, the script stops before claude/codex
  `mcp remove`. Re-register with `python tools/install_mcp.py --register` from
  the repository root; that path refuses to drop options unless
  --allow-option-removal is passed. -ReplaceExistingRegistration lets this
  script replace an existing registration anyway.

.PARAMETER Register
  Register dayz-mcp when Claude and Codex do not already have it.

.PARAMETER ReplaceExistingRegistration
  With -Register, replace an existing dayz-mcp registration. This drops
  options the current registration carries.
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
  [switch]$NoSupervised
)

$ErrorActionPreference = "Stop"
$ToolsRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ToolsRoot ".venv-mcp"
$Requirements = Join-Path $ToolsRoot "requirements-mcp.txt"
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
    '--exec-allowlist', '--exec-audit-path', '--client-platform', '--task-label', '--tool-pack'
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
    '--exec-allowlist', '--exec-audit-path', '--client-platform', '--task-label', '--tool-pack'
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

function Get-ClientRegistrationProbe {
  param([ValidateSet('claude', 'codex')][string]$Client)
  $commandName = if ($Client -eq 'claude') { 'claude' } else { 'codex.cmd' }
  $command = Get-Command $commandName -ErrorAction SilentlyContinue
  if (-not $command) {
    return @{
      Client = $Client
      CommandMissing = $true
      ExitCode = $null
      Output = ''
    }
  }
  $arguments = @('mcp', 'get', 'dayz-mcp')
  if ($Client -eq 'codex') {
    $arguments += '--json'
  }
  try {
    $output = & $command.Source @arguments 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
  } catch {
    return @{
      Client = $Client
      CommandMissing = $false
      ExitCode = $null
      Output = ''
    }
  }
  if ($null -eq $exitCode -or $exitCode -isnot [int]) {
    return @{
      Client = $Client
      CommandMissing = $false
      ExitCode = $null
      Output = [string]$output
    }
  }
  return @{
    Client = $Client
    CommandMissing = $false
    ExitCode = $exitCode
    Output = [string]$output
  }
}

# Absent matches the not-found text these CLIs print
# (reports/security/installer-not-found-fixtures-v1.json): Claude exit 1
# `No MCP server named "dayz-mcp".`, Codex exit 1
# `No MCP server named 'dayz-mcp' found`. Exit 0 plus a registration shape is
# present. Anything else is unreadable. -ReplaceExistingRegistration overrides
# a parsed registration only; an unreadable probe still refuses.
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
    $output = $Probe['Output']
    $exitCode = $Probe['ExitCode']
    if ($output -isnot [string]) {
      return @{ Name = $Name; Kind = 'unreadable'; Detail = "${Name}: output is missing" }
    }
    if (($exitCode -isnot [int]) -or ($exitCode -is [bool])) {
      return @{ Name = $Name; Kind = 'unreadable'; Detail = "${Name}: exit code is missing" }
    }
    $absentPattern = if ($Grammar -eq 'claude') {
      'No MCP server named "dayz-mcp"\.'
    } else {
      "No MCP server named 'dayz-mcp' found"
    }
    $absentPhrase = [regex]::IsMatch($output, $absentPattern)
    $shape = $false
    if ($Grammar -eq 'claude') {
      $shape = (
        [regex]::IsMatch($output, '(?m)^\s*Type:\s*\S') -and
        [regex]::IsMatch($output, '(?m)^\s*Command:\s*\S') -and
        [regex]::IsMatch($output, '(?m)^\s*Args:\s*\S') -and
        [regex]::IsMatch($output, 'dayz-mcp')
      )
    } elseif ($output.Trim()) {
      try {
        $parsed = $output | ConvertFrom-Json
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
    }
  }
  return @{ Action = 'proceed'; Reason = '' }
}

if (-not (Test-Path -LiteralPath $VenvDir)) {
  Write-Host "creating venv $VenvDir"
  Invoke-Python @("-m", "venv", $VenvDir)
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
  throw "pip install --upgrade pip failed"
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
  if ($Register) {
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

$claudeArgs = $serverArgs + @('--client-platform','claude')
# Claude Code does not re-list tools after tools/list_changed (#93): opt in to
# listing the full catalog before the lease. Codex keeps the default.
if ($ClaudeNoProgressiveDisclosure) {
  $claudeArgs += @('--no-progressive-disclosure')
}
$codexArgs  = $serverArgs + @('--client-platform','codex')
$quotedClaudeArgs = ($claudeArgs | ForEach-Object { '"' + ($_ -replace '"','\"') + '"' }) -join " "
$quotedCodexArgs = ($codexArgs | ForEach-Object { '"' + ($_ -replace '"','\"') + '"' }) -join " "
$claudeCommand = "claude mcp add dayz-mcp -s user -- `"$VenvPython`" $quotedClaudeArgs"
$codexCommand = "codex.cmd mcp add dayz-mcp -- `"$VenvPython`" $quotedCodexArgs"

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
    "dayz-mcp" = @{
      type = "stdio"
      command = $VenvPython
      args = $claudeArgs
    }
  }
} | ConvertTo-Json -Depth 8
Write-Host $mcpJson

if ($Register) {
  $registrationDecision = Get-RegistrationReplaceDecision -Claude (Get-ClientRegistrationProbe -Client claude) -Codex (Get-ClientRegistrationProbe -Client codex) -ReplaceExistingRegistration:([bool]$ReplaceExistingRegistration)
  if ($registrationDecision.Action -ne 'proceed') {
    throw $registrationDecision.Reason
  }
  Write-Host ""
  Write-Host "registering dayz-mcp with Claude Code and Codex"
  # Reached only when both probes were absent, or -ReplaceExistingRegistration
  # was set on a parsed registration. `claude mcp add` does not overwrite an
  # existing name and its default scope is local, so a replace still removes
  # the user-scope entry before adding. An absent name makes remove a no-op.
  & claude mcp remove dayz-mcp -s user
  & claude mcp add dayz-mcp -s user -- $VenvPython @claudeArgs
  $CodexCmd=(Get-Command codex.cmd).Source
  & $CodexCmd mcp remove dayz-mcp
  & $CodexCmd mcp add dayz-mcp -- $VenvPython @codexArgs

  # Self-verify the effective registrations without printing key material.
  $effectiveClaude = (& claude mcp get dayz-mcp 2>&1 | Out-String)
  $claudeOk = Test-ClaudeRegistration $effectiveClaude $VenvPython $KeyFile "$Port" $claudeArgs
  $effectiveCodex = (& $CodexCmd mcp get dayz-mcp --json 2>&1 | Out-String)
  try {
    if (-not (Test-CodexJsonShape $effectiveCodex)) {
      throw "ambiguous Codex registration JSON"
    }
    $codexConfig = $effectiveCodex | ConvertFrom-Json
    $codexOk = Test-CodexRegistration $codexConfig $VenvPython $KeyFile "$Port" $codexArgs
  } catch {
    $codexOk = $false
  }
  if (-not $claudeOk -or -not $codexOk) {
    throw "VERIFY FAILED: effective dayz-mcp registrations do not match dual client contract"
  }
  Write-Host "VERIFY OK: Claude=client/claude, Codex=client/codex, shared port/keyfile."
} else {
  Write-Host ""
  Write-Host "default is registration print-only; rerun with -Register to register dayz-mcp when Claude and Codex do not already have it. An existing registration is left unchanged: re-register with python tools/install_mcp.py --register, or pass -ReplaceExistingRegistration."
}
