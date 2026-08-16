#Requires -Version 5.1
[CmdletBinding()]
param(
  [int]$Port = 8765,
  [int]$DayZPort = 2402,
  [int]$WaitInGameSeconds = 300,
  [int]$ClientTimeoutSeconds = 90,
  [string]$Python = "python",
  [string]$MissionFolder = "dayzOffline.chernarusplus",
  [string]$GamePath = "",
  [string]$DiagPath = "",
  [string]$ModSource = "",
  [switch]$NoStop
)

$ErrorActionPreference = "Stop"

function Info($Message) {
  Write-Host "[fase3] $Message" -ForegroundColor Cyan
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

$ProjectDev = Split-Path -Parent $PSScriptRoot
$ProjectsRoot = Split-Path -Parent $ProjectDev
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
$VerdictFile = Join-Path $PSScriptRoot "fase3-verdict.json"
$RunId = Get-Date -Format "yyyyMMdd_HHmmss"
$WorkRoot = Join-Path (Join-Path $ProjectDev "_fase3") "run_$RunId"
$ServerProfiles = Join-Path $WorkRoot "server_profiles"
$ClientProfiles = Join-Path $WorkRoot "client_profiles"
$Logs = Join-Path $WorkRoot "logs"
$MissionRoot = Join-Path $WorkRoot "mpmissions"
$MissionWs = Join-Path $MissionRoot $MissionFolder
$ServerCfg = Join-Path $WorkRoot "serverDZ.cfg"
$KeyFile = Join-Path $WorkRoot "fase3.key"
$ServerConfigFile = Join-Path $ServerProfiles "dayz_mcp.json"
$MissionConfigFile = Join-Path $MissionWs "dayz_mcp.json"
$PythonStdout = Join-Path $Logs "mcp_server.stdout.log"
$PythonStderr = Join-Path $Logs "mcp_server.stderr.log"
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
if ($ModSource -ne "" -and -not (Test-Path -LiteralPath $ModSource)) {
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
$init += "	bool m_mcpSceneSet;"
$init += ""
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
$init += ""
$init += "	// Deterministic scene so the readiness brightness band separates front-end from world in any"
$init += "	// run, independent of the real clock. Runs ONCE after the world is up (post-spawn), so it"
$init += "	// wins over WorldData's initial weather roll regardless of init ordering. Mirrors the vanilla"
$init += "	// intro-scene pin (scripts\5_mission\dayzintroscene.c:73-81): SetLimits clamps the phenomenon"
$init += "	// and Set drives the forecast to the same value. Moderate overcast flattens the sun angle so"
$init += "	// the settled-world mean stays in a narrow daytime band; freezing the clock keeps it there."
$init += "	override void OnUpdate(float timeslice)"
$init += "	{"
$init += "		super.OnUpdate(timeslice);"
$init += "		if (m_mcpSceneSet)"
$init += "			return;"
$init += "		m_mcpSceneSet = true;"
$init += "		// SetDate (P:\scripts\3_game\global\world.c:51): fixed diurnal hour, matches serverDZ.cfg."
$init += "		GetGame().GetWorld().SetDate(2024, 6, 15, 10, 0);"
$init += "		Weather wx = GetGame().GetWeather();"
$init += "		// MissionWeather + SetWeatherUpdateFreeze (weather.c:383/393): stop the controller from"
$init += "		// re-rolling overcast away from the pinned value during the run."
$init += "		wx.MissionWeather(true);"
$init += "		wx.SetWeatherUpdateFreeze(true);"
$init += "		float overcast = 0.7;"
$init += "		wx.GetOvercast().SetLimits(overcast, overcast);"
$init += "		wx.GetRain().SetLimits(0.0, 0.0);"
$init += "		wx.GetSnowfall().SetLimits(0.0, 0.0);"
$init += "		wx.GetFog().SetLimits(0.0, 0.0);"
$init += "		wx.GetOvercast().Set(overcast, 0, 0);"
$init += "		wx.GetRain().Set(0.0, 0, 0);"
$init += "		wx.GetSnowfall().Set(0.0, 0, 0);"
$init += "		wx.GetFog().Set(0.0, 0, 0);"
$init += "		// SetTimeMultiplier(0) (world.c:19) freezes the whole sim (incl. animations); safe here -"
$init += "		// the player only stands and this runs well after spawn, so no animation is pending."
$init += "		GetGame().GetWorld().SetTimeMultiplier(0);"
$init += "		Print(""[MCP-POC] scene_pinned date=2024/06/15/10:00 overcast="" + overcast + "" timeMult=0"");"
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
# Deterministic scene: pin the engine clock to a fixed diurnal datetime instead of SystemTime, and
# zero the accelerations so it cannot drift toward dawn/dusk during the run. World brightness then no
# longer depends on the real wall clock (a noon-clear or midnight run would push the settled-world
# mean outside the readiness band). 10:00 mid-June is robustly post-sunrise on chernarusplus
# (worlddata.c:69-70 Jul sunrise ~03:26, Jan ~08:54). init.c re-asserts date/weather/freeze in-world
# (the authoritative pin); this cfg removes real-clock dependence from the engine's starting point.
$cfg += 'serverTime="2024/06/15/10/00";'
$cfg += 'serverTimeAcceleration=0;'
$cfg += 'serverNightTimeAcceleration=0;'
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
# Client peer (MCPClientBridge) reads $profile:dayz_mcp.json from its OWN profile dir
# (client_profiles); the client does not resolve $mission: to the server mission. Mirror the
# server config here or the client never initializes ("config not found").
$ClientConfigFile = Join-Path $ClientProfiles "dayz_mcp.json"
if (-not (Test-Path -LiteralPath $ClientProfiles)) { New-Item -ItemType Directory -Force -Path $ClientProfiles | Out-Null }
Set-Content -LiteralPath $ClientConfigFile -Encoding ASCII -Value $jsonConfig
$TelemetryDir = Join-Path $MissionWs "dayz_mcp"
if (-not (Test-Path -LiteralPath $TelemetryDir)) {
  New-Item -ItemType Directory -Force -Path $TelemetryDir | Out-Null
}
$FixtureFile = Join-Path $TelemetryDir "telemetry_fixture.jsonl"
$BadFixtureFile = Join-Path $TelemetryDir "telemetry_bad.jsonl"
$FixtureText = '{"fixture_id":"fx1","value":42.0,"seq":0}' + "`n" + '{"fixture_id":"fx2","value":7.5,"seq":1}'
[System.IO.File]::WriteAllText($FixtureFile, $FixtureText, [System.Text.Encoding]::ASCII)
[System.IO.File]::WriteAllText($BadFixtureFile, '{bad-json}', [System.Text.Encoding]::ASCII)
Info "telemetry fixture -> $FixtureFile bytes=$((Get-Item -LiteralPath $FixtureFile).Length)"
Info "bad telemetry fixture -> $BadFixtureFile bytes=$((Get-Item -LiteralPath $BadFixtureFile).Length)"
Info ("config profiles -> {0} exists={1} bytes={2}" -f $ServerConfigFile,(Test-Path $ServerConfigFile),((Get-Item $ServerConfigFile -EA SilentlyContinue).Length))
Info ("config mission -> {0} exists={1} bytes={2}" -f $MissionConfigFile,(Test-Path $MissionConfigFile),((Get-Item $MissionConfigFile -EA SilentlyContinue).Length))
Info ("config client -> {0} exists={1} bytes={2}" -f $ClientConfigFile,(Test-Path $ClientConfigFile),((Get-Item $ClientConfigFile -EA SilentlyContinue).Length))

Remove-Item -LiteralPath $PythonStdout, $PythonStderr, $ClientStdout, $ClientStderr, $VerdictFile, "$VerdictFile.tmp" -Force -ErrorAction SilentlyContinue

$WorkDriveRoot = Ensure-DayZWorkDrive $ProjectsRoot
if ($ModSource -ne "") {
  $DeployModPath = $ModSource
} else {
  $DeployModPath = Join-Path (Join-Path $WorkDriveRoot "Mods") "@DayZ_MCP"
}
if (-not (Test-Path -LiteralPath $DeployModPath)) {
  throw "Missing deployed mod path: $DeployModPath. Build/deploy the DayZ_MCP PBO first or pass -ModSource."
}
Info "using filepatching mod source $DeployModPath"

$py = $null
$srv = $null
$client = $null
$launchTime = Get-Date
$spawnActual = $null
$verdictText = ""
$clientExitCode = 1

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
  # -noPause keeps the client simulating/rendering while it is NOT the foreground window. Without it
  # a backgrounded client pauses, the in-world view stops animating, and the liveness readiness gate
  # (which distinguishes the animating world view from a static front-end overlay) can never confirm
  # in-world. The host grab uses PrintWindow (no focus needed), so the client stays in the background.
  $clientArgs = "-filePatching `"-profiles=$ClientProfiles`" `"-mod=$DeployModPath`" -connect=127.0.0.1 -port=$DayZPort -window -x=1280 -y=720 -noPause"
  Info "starting DayZDiag client"
  $client = Start-Process -FilePath $DiagPath -ArgumentList $clientArgs -WorkingDirectory $GamePath -WindowStyle Normal -PassThru
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
    # Target the fase3 client window for the host grab via its UNIQUE client-profiles path (a robust
    # substring of only this client's command line; LFQuad uses a different path). This avoids both the
    # "largest DayZ window of any client" collision and the launcher-pid != window-pid mismatch that
    # made --client-pid fail (run_20260615_191154). --client-pid is passed too as a fallback.
    # --ready-timeout covers client world-load + the launch overlay clearing (lags server player-ready).
    $clientPyArgs = "`"$ClientPy`" --mode phase3 --port $Port --keyfile `"$KeyFile`" --spawn `"$spawnActual`" --output `"$VerdictFile`" --timeout $ClientTimeoutSeconds --client-pid $($client.Id) --client-cmdline-match `"$ClientProfiles`" --ready-timeout 200"
    Info "running phase3 client (grab cmdline-match=$ClientProfiles)"
    $clientProc = Start-Process -FilePath $Python -ArgumentList $clientPyArgs -RedirectStandardOutput $ClientStdout -RedirectStandardError $ClientStderr -WindowStyle Hidden -PassThru -Wait
    $clientExitCode = $clientProc.ExitCode
    $verdictText = Read-SharedText $VerdictFile
  } else {
    Info "player did not become ready within ${WaitInGameSeconds}s; skipping Phase3 client suite"
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

$stdout = Read-SharedText $PythonStdout
$stderr = Read-SharedText $PythonStderr
$clientStdoutText = Read-SharedText $ClientStdout
$clientStderrText = Read-SharedText $ClientStderr
$logRootsFinal = @($ServerProfiles, $ClientProfiles)
$markers = Get-McpMarkers $logRootsFinal $launchTime
$evidence = Get-CombinedEvidence $logRootsFinal $launchTime
$serverRpt = Find-NewestFile $ServerProfiles $launchTime @("*.RPT")
$clientRpt = Find-NewestFile $ClientProfiles $launchTime @("*.RPT")
$serverScript = Find-NewestFile $ServerProfiles $launchTime @("script*.log")
$clientScript = Find-NewestFile $ClientProfiles $launchTime @("script*.log")
$Phase3NormalComparisonText = ""
try {
  if ($verdictText) {
    $Phase3Verdict = $verdictText | ConvertFrom-Json
    if ($Phase3Verdict.normal_comparison) {
      $Phase3NormalComparisonText = $Phase3Verdict.normal_comparison | ConvertTo-Json -Depth 20
    }
  }
} catch {
  $Phase3NormalComparisonText = "Could not parse normal_comparison: $($_.Exception.Message)"
}

Write-Host "===== FILEPATCHING MOD SOURCE BEGIN ====="
Write-Host $DeployModPath
Write-Host "===== FILEPATCHING MOD SOURCE END ====="
Write-Host "===== PYTHON SERVER STDOUT BEGIN ====="
if ($stdout) { Write-Host $stdout }
Write-Host "===== PYTHON SERVER STDOUT END ====="
if ($stderr) {
  Write-Host "===== PYTHON SERVER STDERR BEGIN ====="
  Write-Host $stderr
  Write-Host "===== PYTHON SERVER STDERR END ====="
}
Write-Host "===== fase3 CLIENT STDOUT BEGIN ====="
if ($clientStdoutText) { Write-Host $clientStdoutText }
Write-Host "===== fase3 CLIENT STDOUT END ====="
if ($clientStderrText) {
  Write-Host "===== fase3 CLIENT STDERR BEGIN ====="
  Write-Host $clientStderrText
  Write-Host "===== fase3 CLIENT STDERR END ====="
}
Write-Host "===== MCP MARKERS BEGIN ====="
if ($markers) { Write-Host $markers }
Write-Host "===== MCP MARKERS END ====="
Write-Host "===== Phase3 NORMAL COMPARISON BEGIN ====="
if ($Phase3NormalComparisonText) { Write-Host $Phase3NormalComparisonText }
Write-Host "===== Phase3 NORMAL COMPARISON END ====="
Write-Host "===== LOG EVIDENCE BEGIN ====="
if ($evidence) { Write-Host $evidence }
Write-Host "===== LOG EVIDENCE END ====="
Write-Host "===== fase3 VERDICT BEGIN ====="
if ($verdictText) { Write-Host $verdictText }
Write-Host "===== fase3 VERDICT END ====="
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


