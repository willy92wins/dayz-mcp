#Requires -Version 5.1
# S0 gate launcher (Fase 5 drivability spike).
# Brings up DayZDiag server+client against the ALREADY-RUNNING broker daemon on :8765
# (does NOT start its own loopback). Leaves processes running; the gate is driven
# separately via raw /enqueue + MCP tools. Spawn is deterministic; time is NOT frozen
# (the car must simulate). Derived from run-fase3.ps1 (verified setup) minus the python
# server, the scene time-freeze, and the phase3 client suite.
[CmdletBinding()]
param(
  [int]$Port = 8765,
  [int]$DayZPort = 2402,
  [string]$MissionFolder = "dayzOffline.chernarusplus"
)
$ErrorActionPreference = "Stop"
function Info($m) { Write-Host "[s0] $m" -ForegroundColor Cyan }

$ToolsRoot   = $PSScriptRoot
$ProjectDev  = Split-Path -Parent $ToolsRoot
$ProjectsRoot= Split-Path -Parent $ProjectDev
$GamePath    = if ($env:DAYZ_GAME_PATH) { $env:DAYZ_GAME_PATH } else { "C:\Program Files (x86)\Steam\steamapps\common\DayZ" }
$DiagPath    = if ($env:DAYZ_DIAG_PATH) { $env:DAYZ_DIAG_PATH } else { Join-Path $GamePath "DayZDiag_x64.exe" }
$KeyFile     = Join-Path $ToolsRoot ".dayz_mcp.key"

if (-not (Test-Path $DiagPath)) { throw "Missing diag exe: $DiagPath" }
if (-not (Test-Path $KeyFile))  { throw "Missing keyfile: $KeyFile" }
if (-not (Test-Path "P:\"))     { throw "P: not mounted" }
$DeployMod = "P:\Mods\@DayZ_MCP"
if (-not (Test-Path (Join-Path $DeployMod "Addons\DayZ_MCP.pbo"))) { throw "Deployed PBO missing under $DeployMod" }

$key = (Get-Content -LiteralPath $KeyFile -Raw).Trim()

# Resolve a complete DayZServer mission template to copy.
$missionCandidates = @()
if ($env:DAYZ_SERVER_PATH) { $missionCandidates += (Join-Path (Join-Path $env:DAYZ_SERVER_PATH "mpmissions") $MissionFolder) }
$steamCommon = Split-Path -Parent $GamePath
if ($steamCommon) { $missionCandidates += (Join-Path (Join-Path (Join-Path $steamCommon "DayZServer") "mpmissions") $MissionFolder) }
$missionCandidates += (Join-Path "C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions" $MissionFolder)
$ServerMissionPath = $null
foreach ($c in $missionCandidates) { if ($c -and (Test-Path -LiteralPath $c)) { $ServerMissionPath = (Get-Item -LiteralPath $c).FullName; break } }
if (-not $ServerMissionPath) { throw "Missing DayZServer mission '$MissionFolder'. Checked: $($missionCandidates -join '; ')" }

$RunId = Get-Date -Format "yyyyMMdd_HHmmss"
$WorkRoot       = Join-Path (Join-Path $ProjectDev "_s0") "run_$RunId"
$ServerProfiles = Join-Path $WorkRoot "server_profiles"
$ClientProfiles = Join-Path $WorkRoot "client_profiles"
$MissionRoot    = Join-Path $WorkRoot "mpmissions"
$MissionWs      = Join-Path $MissionRoot $MissionFolder
$ServerCfg      = Join-Path $WorkRoot "serverDZ.cfg"
foreach ($d in @($WorkRoot,$ServerProfiles,$ClientProfiles,$MissionRoot)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }

Info "workroot $WorkRoot"
Info "copying mission $ServerMissionPath -> $MissionWs"
Copy-Item -LiteralPath $ServerMissionPath -Destination $MissionWs -Recurse -Force

# init.c: deterministic spawn on a flat/road area, NO time freeze (car must simulate).
$init = @()
$init += "void main()"
$init += "{"
$init += "}"
$init += ""
$init += "class S0Mission: MissionServer"
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
$init += "	return new S0Mission();"
$init += "}"
Set-Content -LiteralPath (Join-Path $MissionWs "init.c") -Encoding ASCII -Value ($init -join "`r`n")

# serverDZ.cfg: allowFilePatching so the client filepatch handshake passes; time runs normally.
$cfg = @()
$cfg += 'hostname="DayZ_MCP S0";'
$cfg += 'password="";'
$cfg += 'passwordAdmin="";'
$cfg += 'maxPlayers=10;'
$cfg += 'verifySignatures=0;'
$cfg += 'forceSameBuild=0;'
$cfg += 'disableVoN=1;'
$cfg += 'allowFilePatching=1;'
$cfg += 'instanceId=1;'
$cfg += 'class Missions { class DayZ { template="' + $MissionFolder + '"; }; };'
Set-Content -LiteralPath $ServerCfg -Encoding ASCII -Value ($cfg -join "`r`n")

# dayz_mcp.json -> the running daemon on :Port with the daemon key. Server reads $mission: and
# $profile:; client reads $profile: from ITS profile dir. Seed all three.
$jsonConfig = @{ url = "http://127.0.0.1:$Port/"; key = $key; pollHz = 5 } | ConvertTo-Json -Compress
Set-Content -LiteralPath (Join-Path $ServerProfiles "dayz_mcp.json") -Encoding ASCII -Value $jsonConfig
Set-Content -LiteralPath (Join-Path $MissionWs "dayz_mcp.json") -Encoding ASCII -Value $jsonConfig
Set-Content -LiteralPath (Join-Path $ClientProfiles "dayz_mcp.json") -Encoding ASCII -Value $jsonConfig
# Telemetry dir the bridge may expect under the mission.
$TelemetryDir = Join-Path $MissionWs "dayz_mcp"
New-Item -ItemType Directory -Force -Path $TelemetryDir | Out-Null

$srvArgs = "-server -filePatching `"-config=$ServerCfg`" `"-profiles=$ServerProfiles`" `"-mission=$MissionWs`" `"-mod=$DeployMod`" -port=$DayZPort"
Info "starting DayZDiag SERVER"
$srv = Start-Process -FilePath $DiagPath -ArgumentList $srvArgs -WorkingDirectory $GamePath -WindowStyle Hidden -PassThru
Info "server pid $($srv.Id)"
Start-Sleep -Seconds 8

$clientArgs = "-filePatching `"-profiles=$ClientProfiles`" `"-mod=$DeployMod`" -connect=127.0.0.1 -port=$DayZPort -window -x=1280 -y=720 -noPause"
Info "starting DayZDiag CLIENT"
$client = Start-Process -FilePath $DiagPath -ArgumentList $clientArgs -WorkingDirectory $GamePath -WindowStyle Normal -PassThru
Info "client pid $($client.Id)"

# Persist run metadata for the gate driver / teardown.
$meta = @{
  run_id = $RunId; work_root = $WorkRoot; port = $Port; dayz_port = $DayZPort
  server_pid = $srv.Id; client_pid = $client.Id
  server_profiles = $ServerProfiles; client_profiles = $ClientProfiles
} | ConvertTo-Json -Compress
Set-Content -LiteralPath (Join-Path $ToolsRoot "_s0-run.json") -Encoding ASCII -Value $meta

Write-Host "===== S0 LAUNCH OK ====="
Write-Host "SERVER_PID=$($srv.Id)"
Write-Host "CLIENT_PID=$($client.Id)"
Write-Host "WORKROOT=$WorkRoot"
Write-Host "META=$(Join-Path $ToolsRoot '_s0-run.json')"
Write-Host "Processes left RUNNING. Drive the gate, then teardown by PID."
