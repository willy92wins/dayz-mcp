param(
  [int]$Port = 8765,
  [string]$KeyFile = "",
  [string]$ServerProfiles = "",
  [string]$ClientProfiles = "",
  [string]$MissionPath = "",
  [string]$ExpectedGameVersion = "",
  [double]$IdleTimeoutSeconds = 1800,
  [switch]$AllowLegacy,
  [switch]$Register
)

$ErrorActionPreference = "Stop"
$ToolsRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ToolsRoot ".venv-mcp"
$Requirements = Join-Path $ToolsRoot "requirements-mcp.txt"
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

function Invoke-Python {
  param([string[]]$Arguments)
  $py = Get-Command py -ErrorAction SilentlyContinue
  if ($py) {
    & $py.Source -3.14 @Arguments
    return
  }
  & python @Arguments
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
    '--exec-allowlist', '--client-platform', '--task-label'
  )
  $booleanFlags = @(
    '--require-version', '--enable-exec-enforce', '--no-daemon-autospawn',
    '--client', '--daemon', '--embedded'
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
    '--exec-allowlist', '--client-platform', '--task-label'
  )
  $booleanFlags = @(
    '--require-version', '--enable-exec-enforce', '--no-daemon-autospawn',
    '--client', '--daemon', '--embedded'
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

if (-not (Test-Path -LiteralPath $VenvDir)) {
  Write-Host "creating venv $VenvDir"
  Invoke-Python @("-m", "venv", $VenvDir)
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r $Requirements
& $VenvPython -m pip install -e $ToolsRoot

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
$serverArgs = @("-m", "dayz_mcp", "--client", "--keyfile", $KeyFile, "--port", "$Port")
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
  Write-Host ""
  Write-Host "registering dayz-mcp with Claude Code and Codex (idempotent)"
  # `claude mcp add` does NOT overwrite an existing name (it errors/no-ops) and its
  # default scope is LOCAL, not USER. Adding without removing the existing USER-scope
  # entry first silently leaves the old EMBEDDED registration in place -> sessions keep
  # binding :Port and fight each other (the failure this guard exists to prevent).
  # Remove-then-add at explicit user scope makes registration idempotent.
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
  Write-Host "default is registration print-only; rerun with -Register to mutate Claude/Codex config."
}
