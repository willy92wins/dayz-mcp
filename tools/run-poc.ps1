#Requires -Version 5.1
[CmdletBinding()]
param(
  [int]$Port = 8765,
  [int]$DayZPort = 2302,
  [int]$WaitInGameSeconds = 120,
  [int]$ClientTimeoutSeconds = 90,
  [string]$Python = "python",
  [string]$MissionFolder = "dayzOffline.chernarusplus",
  [string]$GamePath = "",
  [string]$DiagPath = "",
  [string]$ModSource = "",
  [string]$AddonBuilderPath = "",
  [switch]$NoStop
)

$ErrorActionPreference = "Stop"

function Info($Message) {
  Write-Host "[poc] $Message" -ForegroundColor Cyan
}

function Read-SharedText($Path) {
  if (-not (Test-Path -LiteralPath $Path)) {
    return ""
  }
  try {
    $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    $sr = New-Object System.IO.StreamReader($fs)
    $text = $sr.ReadToEnd()
    $sr.Close()
    $fs.Close()
    return $text
  } catch {
    return ""
  }
}

function New-PocToken {
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

function Find-NewestFile($Root, $Since, $Patterns) {
  if (-not (Test-Path -LiteralPath $Root)) {
    return $null
  }
  $files = @(Get-ChildItem -LiteralPath $Root -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
    $file = $_
    $matchesPattern = $false
    foreach ($pattern in $Patterns) {
      if ($file.Name -like $pattern) {
        $matchesPattern = $true
      }
    }
    $matchesPattern -and $file.LastWriteTime -ge $Since.AddSeconds(-5)
  } | Sort-Object LastWriteTime -Descending)
  if ($files.Count -eq 0) {
    return $null
  }
  return $files[0].FullName
}

function Get-LogFiles($Roots, $Since) {
  $out = @()
  foreach ($root in $Roots) {
    if (-not (Test-Path -LiteralPath $root)) {
      continue
    }
    $out += @(Get-ChildItem -LiteralPath $root -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
      ($_.Name -like "*.RPT" -or $_.Name -like "script*.log" -or $_.Name -like "crash*.log") -and $_.LastWriteTime -ge $Since.AddSeconds(-5)
    })
  }
  return @($out | Sort-Object LastWriteTime -Descending)
}

function Resolve-AddonBuilderPath($Candidate) {
  if ($Candidate -ne "") {
    if (Test-Path -LiteralPath $Candidate) {
      return (Get-Item -LiteralPath $Candidate).FullName
    }
    throw "Missing AddonBuilder exe: $Candidate"
  }

  $candidates = @()
  if ($env:DAYZ_ADDONBUILDER_PATH) {
    $candidates += $env:DAYZ_ADDONBUILDER_PATH
  }
  if ($env:DAYZ_TOOLS_PATH) {
    $candidates += (Join-Path $env:DAYZ_TOOLS_PATH "Bin\AddonBuilder\AddonBuilder.exe")
  }
  $candidates += "C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\AddonBuilder.exe"

  foreach ($path in $candidates) {
    if ($path -and (Test-Path -LiteralPath $path)) {
      return (Get-Item -LiteralPath $path).FullName
    }
  }

  throw "Missing AddonBuilder.exe. Pass -AddonBuilderPath or set DAYZ_ADDONBUILDER_PATH/DAYZ_TOOLS_PATH."
}

function Resolve-ServerMissionPath($MissionFolder, $GamePath) {
  $candidates = @()
  if ($env:DAYZ_SERVER_PATH) {
    $candidates += (Join-Path (Join-Path $env:DAYZ_SERVER_PATH "mpmissions") $MissionFolder)
  }
  if ($GamePath) {
    $steamCommon = Split-Path -Parent $GamePath
    if ($steamCommon) {
      $candidates += (Join-Path (Join-Path (Join-Path $steamCommon "DayZServer") "mpmissions") $MissionFolder)
    }
  }
  $candidates += (Join-Path "C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions" $MissionFolder)

  foreach ($path in $candidates) {
    if ($path -and (Test-Path -LiteralPath $path)) {
      return (Get-Item -LiteralPath $path).FullName
    }
  }

  throw "Missing DayZServer mission template '$MissionFolder'. Checked: $($candidates -join '; ')"
}

function Ensure-DayZWorkDrive($ProjectsRoot) {
  $workDrive = "P:\"
  if (Test-Path -LiteralPath $workDrive) {
    return $workDrive
  }

  Info "P:\ not mounted; creating transient work drive with subst -> $ProjectsRoot"
  $substOutput = & cmd.exe /c subst P: "$ProjectsRoot" 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to create P: work drive with subst. Output: $substOutput"
  }
  if (-not (Test-Path -LiteralPath $workDrive)) {
    throw "P: work drive is still missing after subst."
  }
  return $workDrive
}

function Invoke-DayZMcpPboBuild($BuilderPath, $WorkDriveRoot) {
  $modName = "DayZ_MCP"
  $source = Join-Path $WorkDriveRoot $modName
  $deployRoot = Join-Path (Join-Path $WorkDriveRoot "Mods") "@$modName"
  $modsRoot = Join-Path $WorkDriveRoot "Mods"
  $addons = Join-Path $deployRoot "Addons"
  $temp = Join-Path (Join-Path $WorkDriveRoot "temp") $modName
  $pbo = Join-Path $addons "$modName.pbo"

  if (-not (Test-Path -LiteralPath $source)) {
    throw "Missing PBO source: $source"
  }
  if (-not (Test-Path -LiteralPath $modsRoot)) {
    throw "Missing P:\Mods junction target: $modsRoot"
  }
  if (-not (Test-Path -LiteralPath $addons)) {
    New-Item -ItemType Directory -Force -Path $addons | Out-Null
  }

  $args = @($source, $addons, "-prefix=$modName", "-temp=$temp", "-clear", "-packonly")
  Info "building PBO with AddonBuilder"
  Info ("AddonBuilder args: " + (($args | ForEach-Object { '"' + $_ + '"' }) -join " "))
  $oldErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $raw = & $BuilderPath @args 2>&1
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $oldErrorActionPreference
  }
  $output = @($raw | ForEach-Object { $_.ToString() }) -join "`r`n"

  if ($exitCode -ne 0) {
    Write-Host "===== ADDONBUILDER OUTPUT BEGIN ====="
    if ($output) { Write-Host $output } else { Write-Host "(no stdout/stderr)" }
    Write-Host "===== ADDONBUILDER OUTPUT END ====="
    throw "AddonBuilder failed with exit code $exitCode"
  }
  if (-not (Test-Path -LiteralPath $pbo)) {
    Write-Host "===== ADDONBUILDER OUTPUT BEGIN ====="
    if ($output) { Write-Host $output } else { Write-Host "(no stdout/stderr)" }
    Write-Host "===== ADDONBUILDER OUTPUT END ====="
    throw "AddonBuilder completed but PBO is missing: $pbo"
  }
  $item = Get-Item -LiteralPath $pbo
  if ($item.Length -le 0) {
    Write-Host "===== ADDONBUILDER OUTPUT BEGIN ====="
    if ($output) { Write-Host $output } else { Write-Host "(no stdout/stderr)" }
    Write-Host "===== ADDONBUILDER OUTPUT END ====="
    throw "AddonBuilder produced an empty PBO: $pbo"
  }

  $bytes = [System.IO.File]::ReadAllBytes($pbo)
  $pboAscii = -join ($bytes | ForEach-Object { if ($_ -ge 32 -and $_ -le 126) { [char]$_ } else { " " } })
  $hasMcpBridgeScript = $pboAscii.Contains("scripts\5_Mission\MCPBridge.c")
  $hasMcpPocMarker = $pboAscii.Contains("[MCP-POC]")
  if (-not ($hasMcpBridgeScript -and $hasMcpPocMarker)) {
    Write-Host "===== ADDONBUILDER OUTPUT BEGIN ====="
    if ($output) { Write-Host $output } else { Write-Host "(no stdout/stderr)" }
    Write-Host "===== ADDONBUILDER OUTPUT END ====="
    throw "AddonBuilder produced a PBO without DayZ_MCP POC scripts: $pbo"
  }

  return [pscustomobject]@{
    Output = $output
    PboPath = $item.FullName
    Size = $item.Length
    ModPath = $deployRoot
  }
}

function Get-CombinedEvidence($Roots, $Since) {
  $linesOut = @()
  $files = Get-LogFiles $Roots $Since
  foreach ($file in $files) {
    $text = Read-SharedText $file.FullName
    if (-not $text) {
      continue
    }
    $matches = @($text -split "`r?`n" | Where-Object {
      $_ -match "DayZ_MCP|@DayZ_MCP|Addons|\.pbo|mod loaded|loaded mod|missionScriptModule|Compile|Cannot|undefined|error|5_Mission|\[MCP-POC\]|PlayerConnect|connect|Client"
    } | Select-Object -First 260)
    if ($matches.Count -gt 0) {
      $linesOut += "### $($file.FullName)"
      $linesOut += $matches
    }
  }
  return ($linesOut -join "`r`n")
}

function Get-McpMarkers($Roots, $Since) {
  $linesOut = @()
  $files = Get-LogFiles $Roots $Since
  foreach ($file in $files) {
    $text = Read-SharedText $file.FullName
    if (-not $text) {
      continue
    }
    $matches = @($text -split "`r?`n" | Where-Object { $_ -match "\[MCP-POC\]" })
    if ($matches.Count -gt 0) {
      $linesOut += "### $($file.FullName)"
      $linesOut += $matches
    }
  }
  return ($linesOut -join "`r`n")
}

function Get-SpawnActual($Roots, $Since) {
  $files = Get-LogFiles $Roots $Since
  foreach ($file in $files) {
    $text = Read-SharedText $file.FullName
    if (-not $text) {
      continue
    }
    foreach ($line in @($text -split "`r?`n")) {
      $idx = $line.IndexOf("spawn_actual=")
      if ($idx -lt 0) {
        continue
      }
      $tail = $line.Substring($idx + "spawn_actual=".Length)
      $matches = [regex]::Matches($tail, "-?\d+(?:\.\d+)?")
      if ($matches.Count -ge 3) {
        return ("{0},{1},{2}" -f $matches[0].Value,$matches[1].Value,$matches[2].Value)
      }
    }
  }
  return $null
}

function New-PocUri($Port, $Path, $Key, $Query) {
  $pairs = @("key=" + [Uri]::EscapeDataString($Key))
  foreach ($name in $Query.Keys) {
    $pairs += ([Uri]::EscapeDataString($name) + "=" + [Uri]::EscapeDataString([string]$Query[$name]))
  }
  return ("http://127.0.0.1:{0}{1}?{2}" -f $Port,$Path,($pairs -join "&"))
}

function Invoke-PocJson($Method, $Path, $Port, $Key, $Body, $Query) {
  $uri = New-PocUri $Port $Path $Key $Query
  if ($Method -eq "POST") {
    $json = $Body | ConvertTo-Json -Compress
    return Invoke-RestMethod -Method Post -Uri $uri -ContentType "application/json" -Body $json -TimeoutSec 5
  }
  return Invoke-RestMethod -Method Get -Uri $uri -TimeoutSec 5
}

function Test-PocPlayerReady($Result) {
  if (-not $Result) {
    return $false
  }
  if (-not ($Result.ok -eq $true -or $Result.ok -eq 1 -or [string]$Result.ok -eq "1")) {
    return $false
  }
  if (-not $Result.state -or -not $Result.state.pos) {
    return $false
  }
  return @($Result.state.pos).Count -eq 3
}

function Format-PocPlayerPos($Result) {
  if (-not $Result -or -not $Result.state -or -not $Result.state.pos) {
    return ""
  }
  $pos = @($Result.state.pos)
  if ($pos.Count -lt 3) {
    return ""
  }
  $culture = [System.Globalization.CultureInfo]::InvariantCulture
  return ("{0},{1},{2}" -f ([double]$pos[0]).ToString("R", $culture),([double]$pos[1]).ToString("R", $culture),([double]$pos[2]).ToString("R", $culture))
}

function Invoke-PocPlayerStateProbe($Port, $Key, $TimeoutSeconds) {
  $enqueue = Invoke-PocJson "POST" "/enqueue" $Port $Key @{ cmd = "query_player_state"; args = @{} } @{}
  $commandId = [int]$enqueue.id
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    $awaited = Invoke-PocJson "GET" "/await" $Port $Key $null @{ id = $commandId }
    if ($awaited.status -eq "done") {
      return $awaited.result
    }
    Start-Sleep -Milliseconds 200
  }
  throw "Timed out waiting for player state probe id=$commandId"
}

function Set-PocJsonProperty($Object, $Name, $Value) {
  if ($Object.PSObject.Properties[$Name]) {
    $Object.$Name = $Value
  } else {
    $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
  }
}

function Write-PocVerdictAtomic($Path, $Verdict) {
  $json = $Verdict | ConvertTo-Json -Depth 80
  $tmp = "$Path.tmp"
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($tmp, $json + "`n", $utf8NoBom)
  Move-Item -LiteralPath $tmp -Destination $Path -Force
  $verify = [System.IO.File]::ReadAllText($Path, $utf8NoBom) | ConvertFrom-Json
  if (-not $verify) {
    throw "Verdict verification failed: $Path"
  }
}

function Update-PocVerdictTest($Path, $Name, $Test, $ClientExitCode) {
  if (Test-Path -LiteralPath $Path) {
    $verdict = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
  } else {
    $verdict = [pscustomobject]@{}
  }

  $previousOverallPass = $false
  if ($verdict.PSObject.Properties["overall_pass"]) {
    $previousOverallPass = ($verdict.overall_pass -eq $true)
  }

  $hasTopLevelError = $false
  if ($verdict.PSObject.Properties["error"] -and $verdict.error) {
    $hasTopLevelError = $true
  }

  if (-not $verdict.tests) {
    Set-PocJsonProperty $verdict "tests" ([pscustomobject]@{})
  }
  Set-PocJsonProperty $verdict.tests $Name ([pscustomobject]$Test)

  $requiredTests = @(
    "A1_authoritative_position",
    "A2_async_nonblocking",
    "A3_correlation_ids",
    "A4_fail_closed_security",
    "A5_resilience_backoff_recovery"
  )
  $allRequiredPresentAndPass = $true
  foreach ($requiredName in $requiredTests) {
    if (-not $verdict.tests.PSObject.Properties[$requiredName]) {
      $allRequiredPresentAndPass = $false
      continue
    }
    $requiredTest = $verdict.tests.$requiredName
    if (-not ($requiredTest.pass -eq $true)) {
      $allRequiredPresentAndPass = $false
    }
  }

  $clientPassed = ($ClientExitCode -eq 0)
  $overallPass = ($previousOverallPass -and $clientPassed -and -not $hasTopLevelError -and $allRequiredPresentAndPass)
  Set-PocJsonProperty $verdict "overall_pass" $overallPass
  Write-PocVerdictAtomic $Path $verdict
  return [System.IO.File]::ReadAllText($Path)
}

function Get-CurrentRunLogFiles($Roots) {
  $out = @()
  foreach ($root in $Roots) {
    if (-not (Test-Path -LiteralPath $root)) {
      continue
    }
    $out += @(Get-ChildItem -LiteralPath $root -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
      $_.Name -like "*.RPT" -or $_.Name -like "script*.log" -or $_.Name -like "crash*.log"
    })
  }
  return @($out | Sort-Object LastWriteTime -Descending)
}

function Get-PocBackoffEvidence($Roots, $Since) {
  $linesOut = @()
  $files = Get-CurrentRunLogFiles $Roots
  foreach ($file in $files) {
    $text = Read-SharedText $file.FullName
    if (-not $text) {
      continue
    }
    $matches = @($text -split "`r?`n" | Where-Object { $_ -match "\[MCP-POC\] poll .*backoff_s=" })
    if ($matches.Count -gt 0) {
      $linesOut += "### $($file.FullName)"
      $linesOut += $matches
    }
  }
  return @($linesOut)
}

function Get-PocCompileErrorEvidence($Roots, $Since) {
  $linesOut = @()
  $files = Get-CurrentRunLogFiles $Roots
  foreach ($file in $files) {
    $text = Read-SharedText $file.FullName
    if (-not $text) {
      continue
    }
    $matches = @($text -split "`r?`n" | Where-Object { $_ -match "Can'?t compile|Cannot compile|Compile error" })
    if ($matches.Count -gt 0) {
      $linesOut += "### $($file.FullName)"
      $linesOut += $matches
    }
  }
  return @($linesOut)
}

$ProjectDev = Split-Path -Parent $PSScriptRoot
$ProjectsRoot = Split-Path -Parent $ProjectDev
if ($ModSource -eq "") {
  $ModSource = Join-Path $ProjectsRoot "DayZ_MCP"
}
if ($GamePath -eq "") {
  if ($env:DAYZ_GAME_PATH) {
    $GamePath = $env:DAYZ_GAME_PATH
  } else {
    $GamePath = "C:\Program Files (x86)\Steam\steamapps\common\DayZ"
  }
}
if ($DiagPath -eq "") {
  if ($env:DAYZ_DIAG_PATH) {
    $DiagPath = $env:DAYZ_DIAG_PATH
  } else {
    $DiagPath = Join-Path $GamePath "DayZDiag_x64.exe"
  }
}

$ServerPy = Join-Path $PSScriptRoot "mcp_server.py"
$ClientPy = Join-Path $PSScriptRoot "mcp_client.py"
$VerdictFile = Join-Path $PSScriptRoot "poc-verdict.json"
$RunId = Get-Date -Format "yyyyMMdd_HHmmss"
$WorkRoot = Join-Path (Join-Path $ProjectDev "_poc") "run_$RunId"
$ServerProfiles = Join-Path $WorkRoot "server_profiles"
$ClientProfiles = Join-Path $WorkRoot "client_profiles"
$Logs = Join-Path $WorkRoot "logs"
$MissionRoot = Join-Path $WorkRoot "mpmissions"
$MissionWs = Join-Path $MissionRoot $MissionFolder
$ServerCfg = Join-Path $WorkRoot "serverDZ.cfg"
$KeyFile = Join-Path $WorkRoot "poc.key"
$ServerConfigFile = Join-Path $ServerProfiles "dayz_mcp.json"
$MissionConfigFile = Join-Path $MissionWs "dayz_mcp.json"
$PythonStdout = Join-Path $Logs "mcp_server.stdout.log"
$PythonStderr = Join-Path $Logs "mcp_server.stderr.log"
$PythonStdoutA5 = Join-Path $Logs "mcp_server.a5-relaunch.stdout.log"
$PythonStderrA5 = Join-Path $Logs "mcp_server.a5-relaunch.stderr.log"
$ClientStdout = Join-Path $Logs "mcp_client.stdout.log"
$ClientStderr = Join-Path $Logs "mcp_client.stderr.log"

if (-not (Test-Path -LiteralPath $ServerPy)) {
  throw "Missing server script: $ServerPy"
}
if (-not (Test-Path -LiteralPath $ClientPy)) {
  throw "Missing client script: $ClientPy"
}
if (-not (Test-Path -LiteralPath $DiagPath)) {
  throw "Missing DayZ diag exe: $DiagPath"
}
if (-not (Test-Path -LiteralPath $ModSource)) {
  throw "Missing mod source: $ModSource"
}

$ServerMissionPath = Resolve-ServerMissionPath $MissionFolder $GamePath

foreach ($dir in @($WorkRoot, $ServerProfiles, $ClientProfiles, $Logs, $MissionRoot)) {
  if (-not (Test-Path -LiteralPath $dir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
  }
}

Info "workroot $WorkRoot"
Info "copying full mission $ServerMissionPath -> $MissionWs"
Copy-Item -LiteralPath $ServerMissionPath -Destination $MissionWs -Recurse -Force
$requiredMissionEntries = @("db", "env", "storage_1", "cfgplayerspawnpoints.xml", "cfgeconomycore.xml")
foreach ($entry in $requiredMissionEntries) {
  $entryPath = Join-Path $MissionWs $entry
  if (-not (Test-Path -LiteralPath $entryPath)) {
    throw "Copied mission is incomplete; missing $entryPath"
  }
}
$missionEntryCount = @(Get-ChildItem -LiteralPath $MissionWs -Force).Count
Info "mission copied with $missionEntryCount top-level entries"

$InitFile = Join-Path $MissionWs "init.c"
$init = @()
$init += "void main()"
$init += "{"
$init += "}"
$init += ""
$init += "class MCPPOCMission: MissionServer"
$init += "{"
$init += "	override PlayerBase CreateCharacter(PlayerIdentity identity, vector pos, ParamsReadContext ctx, string characterName)"
$init += "	{"
$init += "		vector fixedPos = Vector(6063.018555, 0, 1931.907227);"
$init += "		fixedPos[1] = GetGame().SurfaceY(fixedPos[0], fixedPos[2]);"
$init += "		Entity playerEnt = GetGame().CreatePlayer(identity, characterName, fixedPos, 0, ""NONE"");"
$init += "		Class.CastTo(m_player, playerEnt);"
$init += "		GetGame().SelectPlayer(identity, m_player);"
$init += "		if (m_player)"
$init += "		{"
$init += "			m_player.SetPosition(fixedPos);"
$init += "			Print(""[MCP-POC] spawn_actual="" + fixedPos[0] + "" "" + fixedPos[1] + "" "" + fixedPos[2]);"
$init += "		}"
$init += "		return m_player;"
$init += "	}"
$init += ""
$init += "	override void StartingEquipSetup(PlayerBase player, bool clothesChosen)"
$init += "	{"
$init += "	}"
$init += "};"
$init += ""
$init += "Mission CreateCustomMission(string path)"
$init += "{"
$init += "	return new MCPPOCMission();"
$init += "}"
Set-Content -LiteralPath $InitFile -Encoding ASCII -Value ($init -join "`r`n")

$cfg = @()
$cfg += 'hostname="DayZ_MCP POC";'
$cfg += 'password="";'
$cfg += 'passwordAdmin="";'
$cfg += 'maxPlayers=10;'
$cfg += 'verifySignatures=0;'
$cfg += 'forceSameBuild=0;'
$cfg += 'disableVoN=1;'
$cfg += 'vonCodecQuality=0;'
$cfg += 'serverTime="SystemTime";'
$cfg += 'serverTimeAcceleration=1;'
$cfg += 'serverNightTimeAcceleration=1;'
$cfg += 'serverTimePersistent=0;'
$cfg += 'allowFilePatching=1;'
$cfg += 'instanceId=1;'
$cfg += 'class Missions { class DayZ { template="' + $MissionFolder + '"; }; };'
Set-Content -LiteralPath $ServerCfg -Encoding ASCII -Value ($cfg -join "`r`n")

$key = New-PocToken
Set-Content -LiteralPath $KeyFile -Encoding ASCII -Value $key
$jsonConfig = @{
  url = "http://127.0.0.1:$Port/"
  key = $key
  pollHz = 5
} | ConvertTo-Json -Compress
Set-Content -LiteralPath $ServerConfigFile -Encoding ASCII -Value $jsonConfig
Set-Content -LiteralPath $MissionConfigFile -Encoding ASCII -Value $jsonConfig
Info ("config profiles -> {0} exists={1} bytes={2}" -f $ServerConfigFile,(Test-Path $ServerConfigFile),((Get-Item $ServerConfigFile -EA SilentlyContinue).Length))
Info ("config mission -> {0} exists={1} bytes={2}" -f $MissionConfigFile,(Test-Path $MissionConfigFile),((Get-Item $MissionConfigFile -EA SilentlyContinue).Length))

Remove-Item -LiteralPath $PythonStdout, $PythonStderr, $PythonStdoutA5, $PythonStderrA5, $ClientStdout, $ClientStderr, $VerdictFile, "$VerdictFile.tmp" -Force -ErrorAction SilentlyContinue

$AddonBuilderPath = Resolve-AddonBuilderPath $AddonBuilderPath
$WorkDriveRoot = Ensure-DayZWorkDrive $ProjectsRoot
$Build = Invoke-DayZMcpPboBuild $AddonBuilderPath $WorkDriveRoot
$DeployModPath = $Build.ModPath
Info "PBO ready $($Build.PboPath) ($($Build.Size) bytes)"

$py = $null
$srv = $null
$client = $null
$launchTime = Get-Date
$spawnActual = $null
$verdictText = ""
$clientExitCode = 1
$a5Text = ""

try {
  $pyArgs = "`"$ServerPy`" --port $Port --keyfile `"$KeyFile`""
  Info "starting Python POC server on 127.0.0.1:$Port"
  $py = Start-Process -FilePath $Python -ArgumentList $pyArgs -RedirectStandardOutput $PythonStdout -RedirectStandardError $PythonStderr -WindowStyle Hidden -PassThru
  Start-Sleep -Seconds 1
  if (-not (Get-Process -Id $py.Id -ErrorAction SilentlyContinue)) {
    throw "Python server exited early. stderr: $(Read-SharedText $PythonStderr)"
  }

  $launchTime = Get-Date
  $srvArgs = "-server -filePatching `"-config=$ServerCfg`" `"-profiles=$ServerProfiles`" `"-mission=$MissionWs`" `"-mod=$DeployModPath`" -port=$DayZPort"
  Info "starting DayZDiag server"
  $srv = Start-Process -FilePath $DiagPath -ArgumentList $srvArgs -WorkingDirectory $GamePath -WindowStyle Hidden -PassThru
  Info "DayZDiag server pid $($srv.Id)"

  Start-Sleep -Seconds 8
  $clientArgs = "-filePatching `"-profiles=$ClientProfiles`" `"-mod=$DeployModPath`" -connect=127.0.0.1 -port=$DayZPort -window -x=1280 -y=720"
  Info "starting DayZDiag client"
  $client = Start-Process -FilePath $DiagPath -ArgumentList $clientArgs -WorkingDirectory $GamePath -WindowStyle Hidden -PassThru
  Info "DayZDiag client pid $($client.Id)"

  $deadline = (Get-Date).AddSeconds($WaitInGameSeconds)
  $logRoots = @($ServerProfiles, $ClientProfiles)
  $playerReady = $false

  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    $spawnActual = Get-SpawnActual $logRoots $launchTime
    if ($spawnActual) {
      Info "spawn marker found $spawnActual"
    }
    if ($srv -and -not (Get-Process -Id $srv.Id -ErrorAction SilentlyContinue)) {
      Info "DayZDiag server exited before spawn marker"
      break
    }
    try {
      $probeResult = Invoke-PocPlayerStateProbe $Port $key 2.5
      $probeOk = Test-PocPlayerReady $probeResult
      $probePos = Format-PocPlayerPos $probeResult
      $probeError = $probeResult.error
      Info ("player probe ok={0} pos={1} error={2}" -f $probeOk,$probePos,$probeError)
      if ($probeOk) {
        $playerReady = $true
        if (-not $spawnActual) {
          Info "player ready; waiting up to 15s for spawn_actual marker flush"
          $spawnMarkerDeadline = (Get-Date).AddSeconds(15)
          while ((Get-Date) -lt $spawnMarkerDeadline -and -not $spawnActual) {
            Start-Sleep -Milliseconds 500
            $spawnActual = Get-SpawnActual $logRoots $launchTime
          }
          if ($spawnActual) {
            Info "spawn marker found after player-ready $spawnActual"
          }
        }
        break
      }
    } catch {
      Info "player probe not ready: $($_.Exception.Message)"
    }
  }

  if ($playerReady -and -not $spawnActual) {
    throw "Player ready but spawn_actual marker was not found after 15s; refusing to use player state as A1 fixture"
  }

  if ($playerReady) {
    $clientPyArgs = "`"$ClientPy`" --port $Port --keyfile `"$KeyFile`" --spawn `"$spawnActual`" --output `"$VerdictFile`" --timeout $ClientTimeoutSeconds"
    Info "running POC client"
    $clientProc = Start-Process -FilePath $Python -ArgumentList $clientPyArgs -RedirectStandardOutput $ClientStdout -RedirectStandardError $ClientStderr -WindowStyle Hidden -PassThru -Wait
    $clientExitCode = $clientProc.ExitCode
    $verdictText = Read-SharedText $VerdictFile

    $a5Start = Get-Date
    Info "A5 stopping Python server for 6s"
    if (-not ($py -and (Get-Process -Id $py.Id -ErrorAction SilentlyContinue))) {
      throw "A5 cannot stop Python server because it is not running"
    }
    Stop-Process -Id $py.Id -Force -ErrorAction Stop
    $py = $null
    Start-Sleep -Seconds 6

    $srvAliveAfterOutage = $false
    if ($srv -and (Get-Process -Id $srv.Id -ErrorAction SilentlyContinue)) {
      $srvAliveAfterOutage = $true
    }
    $backoffEvidence = @(Get-PocBackoffEvidence @($ServerProfiles) $a5Start)
    $backoffLines = @($backoffEvidence | Where-Object { $_ -match "\[MCP-POC\] poll .*backoff_s=" })
    $noSpam = ($backoffLines.Count -gt 0 -and $backoffLines.Count -le 8)
    $compileErrorEvidence = @(Get-PocCompileErrorEvidence @($ServerProfiles) $a5Start)
    $noCompileErrors = ($compileErrorEvidence.Count -eq 0)
    $crashFiles = @(Get-LogFiles @($ServerProfiles) $a5Start | Where-Object { $_.Name -like "crash*.log" } | ForEach-Object { $_.FullName })

    Info "A5 restarting Python POC server on 127.0.0.1:$Port"
    $py = Start-Process -FilePath $Python -ArgumentList $pyArgs -RedirectStandardOutput $PythonStdoutA5 -RedirectStandardError $PythonStderrA5 -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 1
    if (-not (Get-Process -Id $py.Id -ErrorAction SilentlyContinue)) {
      throw "Python server failed to restart for A5. stderr: $(Read-SharedText $PythonStderrA5)"
    }

    $a5Result = $null
    $a5PostRelaunchOk = $false
    $a5PostRelaunchPos = ""
    $a5Error = ""
    try {
      $a5Key = (Get-Content -LiteralPath $KeyFile -Raw).Trim()
      $a5Result = Invoke-PocPlayerStateProbe $Port $a5Key 20
      $a5PostRelaunchOk = Test-PocPlayerReady $a5Result
      $a5PostRelaunchPos = Format-PocPlayerPos $a5Result
      if (-not $a5PostRelaunchOk) {
        $a5Error = "post_relaunch_result_not_ready"
      }
    } catch {
      $a5Error = $_.Exception.Message
    }

    Start-Sleep -Milliseconds 500
    $backoffEvidence = @(Get-PocBackoffEvidence @($ServerProfiles) $a5Start)
    $backoffLines = @($backoffEvidence | Where-Object { $_ -match "\[MCP-POC\] poll .*backoff_s=" })
    $noSpam = ($backoffLines.Count -gt 0 -and $backoffLines.Count -le 8)
    $compileErrorEvidence = @(Get-PocCompileErrorEvidence @($ServerProfiles) $a5Start)
    $noCompileErrors = ($compileErrorEvidence.Count -eq 0)
    $crashFiles = @(Get-LogFiles @($ServerProfiles) $a5Start | Where-Object { $_.Name -like "crash*.log" } | ForEach-Object { $_.FullName })

    $a5Pass = ($srvAliveAfterOutage -and $noSpam -and $noCompileErrors -and $a5PostRelaunchOk -and $crashFiles.Count -eq 0)
    $a5Test = [ordered]@{
      pass = $a5Pass
      server_stopped_seconds = 6
      server_alive_after_outage = $srvAliveAfterOutage
      backoff_line_count = $backoffLines.Count
      backoff_lines = @($backoffLines)
      no_spam = $noSpam
      no_compile_errors = $noCompileErrors
      compile_error_lines = @($compileErrorEvidence)
      crash_files = @($crashFiles)
      post_relaunch_ok = $a5PostRelaunchOk
      post_relaunch_pos = $a5PostRelaunchPos
      post_relaunch_result = $a5Result
      error = $a5Error
    }
    $verdictText = Update-PocVerdictTest $VerdictFile "A5_resilience_backoff_recovery" $a5Test $clientExitCode
    $a5TextLines = @(
      "server_alive_after_outage=$srvAliveAfterOutage",
      "backoff_line_count=$($backoffLines.Count)",
      "no_spam=$noSpam",
      "no_compile_errors=$noCompileErrors",
      "post_relaunch_ok=$a5PostRelaunchOk",
      "post_relaunch_pos=$a5PostRelaunchPos",
      "crash_files=$($crashFiles.Count)",
      "error=$a5Error",
      "backoff evidence:"
    )
    $a5TextLines += @($backoffEvidence)
    $a5TextLines += "compile error evidence:"
    $a5TextLines += @($compileErrorEvidence)
    $a5Text = ($a5TextLines | ForEach-Object { [string]$_ }) -join "`r`n"
  } else {
    Info "player did not become ready within ${WaitInGameSeconds}s; skipping A1/A2/A3 client suite"
  }
} finally {
  if (-not $NoStop) {
    if ($client -and (Get-Process -Id $client.Id -ErrorAction SilentlyContinue)) {
      Stop-Process -Id $client.Id -Force -ErrorAction SilentlyContinue
    }
    if ($srv -and (Get-Process -Id $srv.Id -ErrorAction SilentlyContinue)) {
      Stop-Process -Id $srv.Id -Force -ErrorAction SilentlyContinue
    }
    if ($py -and (Get-Process -Id $py.Id -ErrorAction SilentlyContinue)) {
      Stop-Process -Id $py.Id -Force -ErrorAction SilentlyContinue
    }
  }
}

$stdout = @((Read-SharedText $PythonStdout), (Read-SharedText $PythonStdoutA5)) | Where-Object { $_ }
$stdout = $stdout -join "`r`n"
$stderr = @((Read-SharedText $PythonStderr), (Read-SharedText $PythonStderrA5)) | Where-Object { $_ }
$stderr = $stderr -join "`r`n"
$clientStdoutText = Read-SharedText $ClientStdout
$clientStderrText = Read-SharedText $ClientStderr
$logRootsFinal = @($ServerProfiles, $ClientProfiles)
$markers = Get-McpMarkers $logRootsFinal $launchTime
$evidence = Get-CombinedEvidence $logRootsFinal $launchTime
$serverRpt = Find-NewestFile $ServerProfiles $launchTime @("*.RPT")
$clientRpt = Find-NewestFile $ClientProfiles $launchTime @("*.RPT")
$serverScript = Find-NewestFile $ServerProfiles $launchTime @("script*.log")
$clientScript = Find-NewestFile $ClientProfiles $launchTime @("script*.log")

Write-Host "===== ADDONBUILDER OUTPUT BEGIN ====="
if ($Build -and $Build.Output) {
  Write-Host $Build.Output
} else {
  Write-Host "(no stdout/stderr)"
}
Write-Host "===== ADDONBUILDER OUTPUT END ====="
if ($Build) {
  Write-Host "PBO: $($Build.PboPath) ($($Build.Size) bytes)"
}
Write-Host "===== PYTHON SERVER STDOUT BEGIN ====="
if ($stdout) { Write-Host $stdout }
Write-Host "===== PYTHON SERVER STDOUT END ====="
if ($stderr) {
  Write-Host "===== PYTHON SERVER STDERR BEGIN ====="
  Write-Host $stderr
  Write-Host "===== PYTHON SERVER STDERR END ====="
}
Write-Host "===== POC CLIENT STDOUT BEGIN ====="
if ($clientStdoutText) { Write-Host $clientStdoutText }
Write-Host "===== POC CLIENT STDOUT END ====="
if ($clientStderrText) {
  Write-Host "===== POC CLIENT STDERR BEGIN ====="
  Write-Host $clientStderrText
  Write-Host "===== POC CLIENT STDERR END ====="
}
Write-Host "===== MCP MARKERS BEGIN ====="
if ($markers) { Write-Host $markers }
Write-Host "===== MCP MARKERS END ====="
Write-Host "===== A5 RESILIENCE BEGIN ====="
if ($a5Text) { Write-Host $a5Text }
Write-Host "===== A5 RESILIENCE END ====="
Write-Host "===== LOG EVIDENCE BEGIN ====="
if ($evidence) { Write-Host $evidence }
Write-Host "===== LOG EVIDENCE END ====="
Write-Host "===== POC VERDICT BEGIN ====="
if ($verdictText) { Write-Host $verdictText }
Write-Host "===== POC VERDICT END ====="
Write-Host "WORKROOT: $WorkRoot"
if ($serverRpt) { Write-Host "SERVER_RPT: $serverRpt" }
if ($clientRpt) { Write-Host "CLIENT_RPT: $clientRpt" }
if ($serverScript) { Write-Host "SERVER_SCRIPT: $serverScript" }
if ($clientScript) { Write-Host "CLIENT_SCRIPT: $clientScript" }

$gatePass = $false
try {
  $gateVerdict = $verdictText | ConvertFrom-Json
  $gatePass = ($clientExitCode -eq 0 -and $gateVerdict.overall_pass -eq $true)
} catch {
  $gatePass = $false
}

if ($gatePass) {
  Write-Host "GATE=PASS"
  exit 0
}

Write-Host "GATE=FAIL"
exit 1
