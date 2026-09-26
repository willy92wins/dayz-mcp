#Requires -Version 5.1
# ONE-LAUNCH diagnostic for the phase-3 visual capture (2026-06-14). Spawns the player + AKS74U via
# a read-only copy of the A6 gate mission, drives the client camera over the HTTP bridge (lookat the
# player = SUBJECT, then lookat the sky = CONTROL), and grabs each view with ALL THREE methods
# (printwindow / foreground / screen). From the single run we learn, without guessing:
#   * A1  which grab method returns a LIVE frame of the DayZ window (vs a stale desktop region)
#   * A2  whether camera_set actually re-points the RENDERED view (SUBJECT vs CONTROL must differ)
#   * A4  evidence PNG of the player + AKS74U framed (the winning method's SUBJECT grab)
# Pure diagnosis; does NOT modify the bridge or the A6 pipeline. Analyse the PNGs/JSON afterward with
# mcp-grab-diag-analyze.py.
[CmdletBinding()]
param(
  [int]$Port = 8765, [int]$DayZPort = 2402, [int]$WaitInGameSeconds = 220,
  [double]$CamDX = 1.6, [double]$CamDY = 1.45, [double]$CamDZ = 1.6,
  [double]$LookDY = 1.25, [double]$Fov = 0.70
)
$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$tools = Split-Path -Parent $ScriptDir
$Python = Join-Path $tools '.venv-mcp\Scripts\python.exe'
$ServerPy = Join-Path $tools 'mcp_server.py'
$Grab = Join-Path $tools 'mcp-grab.ps1'
$GateInit = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\A6_SR2M_dev\_gate\gate_mcp_init.c'  # READ-ONLY use
$game = 'C:\Program Files (x86)\Steam\steamapps\common\DayZ'
$Diag = Join-Path $game 'DayZDiag_x64.exe'
$Workshop = Join-Path $game '!Workshop'
$Mods = ("$Workshop\@DayZ_MCP;$Workshop\@A6_AnimRTTest")
$OutDir = Join-Path $ScriptDir '_grabdiag'
if (Test-Path $OutDir) { Remove-Item $OutDir -Recurse -Force -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force $OutDir | Out-Null

function Info($m){ Write-Host "[grab-diag] $m" -ForegroundColor Cyan }
function Kill-DayZ { foreach($n in 'DayZDiag_x64','DayZServer_x64','DayZ_x64','BEServer_x64'){ Get-Process -Name $n -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue }; Get-Process -Name 'python' -EA SilentlyContinue | Where-Object { $_.Path -like '*\.venv-mcp\*' } | Stop-Process -Force -EA SilentlyContinue; Start-Sleep -Milliseconds 800 }
function Read-Shared($p){ try { $fs=[System.IO.File]::Open($p,'Open','Read','ReadWrite'); $sr=New-Object IO.StreamReader($fs); $t=$sr.ReadToEnd(); $sr.Close(); $fs.Close(); return ($t -split "`r?`n") } catch { return @() } }
function New-Token { $b=New-Object byte[] 32; ([Security.Cryptography.RandomNumberGenerator]::Create()).GetBytes($b); ([Convert]::ToBase64String($b)).TrimEnd('=').Replace('+','-').Replace('/','_') }

$script:Key = $null
function BuildUri($apath,$q){ $pairs=@("key=" + [System.Uri]::EscapeDataString($script:Key)); foreach($k in $q.Keys){ $pairs += ([System.Uri]::EscapeDataString($k)+"="+[System.Uri]::EscapeDataString([string]$q[$k])) }; return ('http://127.0.0.1:' + $Port + $apath + '?' + ($pairs -join '&')) }
function Enqueue($cmd,$cmdArgs,$peer){ $body=@{cmd=$cmd; args=$cmdArgs}; if($peer){ $body.peer=$peer }; Invoke-RestMethod -Method Post -Uri (BuildUri '/enqueue' @{}) -ContentType 'application/json' -Body ($body|ConvertTo-Json -Depth 6 -Compress) -TimeoutSec 6 }
function Await($id,$timeoutS){ $dl=(Get-Date).AddSeconds($timeoutS); while((Get-Date)-lt $dl){ $a=Invoke-RestMethod -Method Get -Uri (BuildUri '/await' @{id=$id}) -TimeoutSec 6; if($a.status -eq 'done'){ return $a.result }; Start-Sleep -Milliseconds 250 }; return $null }
function Run($cmd,$cmdArgs,$peer,$timeoutS){ $e=Enqueue $cmd $cmdArgs $peer; return (Await ([int]$e.id) $timeoutS) }
function CameraLookAt($cp,$la){ $cargs=@{ cam_mode='lookat'; cam_pos=@($cp[0],$cp[1],$cp[2]); look_at=@($la[0],$la[1],$la[2]); fov=$Fov; settle_ticks=4 }; return (Run 'camera_set' $cargs 'client' 20) }
function GrabMethod($method,$png){ $j = & powershell -NoProfile -ExecutionPolicy Bypass -File $Grab -ProcessName 'DayZDiag_x64' -CapturePng $png -Method $method; try { return ($j | Select-Object -Last 1 | ConvertFrom-Json) } catch { return $null } }
function Wait-Marker($marker,$roots,$deadline){ while((Get-Date)-lt $deadline){ Start-Sleep -Seconds 2; if(-not(Get-Process -Name 'DayZDiag_x64' -EA SilentlyContinue)){ return $false }; $ln=@(); foreach($r in $roots){ Get-ChildItem $r -Recurse -Include '*.RPT','script*.log' -EA SilentlyContinue | ForEach-Object { $ln += Read-Shared $_.FullName } }; if(@($ln|Where-Object{ $_ -match [regex]::Escape($marker) }).Count -gt 0){ return $true } }; return $false }

Kill-DayZ
$WorkRoot = Join-Path $OutDir 'work'
$ServerProfiles = Join-Path $WorkRoot 'server_profiles'; $ClientProfiles = Join-Path $WorkRoot 'client_profiles'
$MissionRoot = Join-Path $WorkRoot 'mpmissions'; $MissionFolder = 'dayzOffline.chernarusplus'; $MissionWs = Join-Path $MissionRoot $MissionFolder
$ServerCfg = Join-Path $WorkRoot 'serverDZ.cfg'; $KeyFile = Join-Path $WorkRoot 'diag.key'
foreach($d in @($WorkRoot,$ServerProfiles,$ClientProfiles,$MissionRoot)){ New-Item -ItemType Directory -Force $d | Out-Null }
$srcMission = "C:\Program Files (x86)\Steam\steamapps\common\DayZServer\mpmissions\$MissionFolder"
if(-not (Test-Path $srcMission)){ throw "missing DayZServer mission $srcMission" }
Copy-Item -LiteralPath $srcMission -Destination $MissionWs -Recurse -Force
Copy-Item -LiteralPath $GateInit -Destination (Join-Path $MissionWs 'init.c') -Force
$cfg = @('hostname="grab diag";','password="";','passwordAdmin="";','maxPlayers=4;','verifySignatures=0;','forceSameBuild=0;','disableVoN=1;','serverTimePersistent=0;','allowFilePatching=1;','instanceId=1;',('class Missions { class DayZ { template="'+$MissionFolder+'"; }; };'))
Set-Content -LiteralPath $ServerCfg -Encoding ASCII -Value ($cfg -join "`r`n")
$script:Key = New-Token; Set-Content -LiteralPath $KeyFile -Encoding ASCII -Value $script:Key
$json = @{ url="http://127.0.0.1:$Port/"; key=$script:Key; pollHz=5 } | ConvertTo-Json -Compress
Set-Content -LiteralPath (Join-Path $ServerProfiles 'dayz_mcp.json') -Encoding ASCII -Value $json
Set-Content -LiteralPath (Join-Path $ClientProfiles 'dayz_mcp.json') -Encoding ASCII -Value $json
Set-Content -LiteralPath (Join-Path $MissionWs 'dayz_mcp.json') -Encoding ASCII -Value $json
New-Item -ItemType Directory -Force (Join-Path $MissionWs 'dayz_mcp') | Out-Null

$py = Start-Process -FilePath $Python -ArgumentList "`"$ServerPy`" --port $Port --keyfile `"$KeyFile`"" -RedirectStandardOutput "$WorkRoot\py.out.log" -RedirectStandardError "$WorkRoot\py.err.log" -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 2; Info "python bridge pid $($py.Id) on 127.0.0.1:$Port"
if (@(Get-NetTCPConnection -LocalPort $Port -State Listen -EA SilentlyContinue).Count -lt 1) { Kill-DayZ; throw "python bridge did not bind $Port" }
try { $probe = Enqueue 'query_player_state' @{} $null; Info "enqueue sanity ok id=$($probe.id)" } catch { Kill-DayZ; throw "enqueue sanity FAILED: $($_.Exception.Message)" }

$srv = Start-Process -FilePath $Diag -ArgumentList "-server -filePatching `"-config=$ServerCfg`" `"-profiles=$ServerProfiles`" `"-mission=$MissionWs`" `"-mod=$Mods`" -port=$DayZPort" -WorkingDirectory $game -WindowStyle Hidden -PassThru
Info "server pid $($srv.Id)"; Start-Sleep -Seconds 8
$cli = Start-Process -FilePath $Diag -ArgumentList "-filePatching `"-profiles=$ClientProfiles`" `"-mod=$Mods`" -connect=127.0.0.1 -port=$DayZPort -window -x=1280 -y=720 -noPause" -WorkingDirectory $game -WindowStyle Normal -PassThru
Info "client pid $($cli.Id)"
$roots = @($ServerProfiles,$ClientProfiles)
$launch = Get-Date; $deadline = $launch.AddSeconds($WaitInGameSeconds)
$inv = [System.Globalization.CultureInfo]::InvariantCulture

$found = $false; $script:CP=$null; $script:LA=$null; $script:SKY=$null; $px=0;$pyy=0;$pz=0
while((Get-Date)-lt $deadline){
  Start-Sleep -Seconds 3
  $ln=@(); foreach($r in $roots){ Get-ChildItem $r -Recurse -Include '*.RPT','script*.log' -EA SilentlyContinue | ForEach-Object { $ln += Read-Shared $_.FullName } }
  $sl = @($ln | Where-Object { $_ -match 'spawn_actual=' }) | Select-Object -First 1
  if($sl){
    $tail = $sl.Substring($sl.IndexOf('spawn_actual=') + 'spawn_actual='.Length)
    $mm = [regex]::Matches($tail, '-?\d+(?:\.\d+)?')
    if($mm.Count -ge 3){
      $px=[double]::Parse($mm[0].Value,$inv); $pyy=[double]::Parse($mm[1].Value,$inv); $pz=[double]::Parse($mm[2].Value,$inv)
      $script:CP = @(($px + $CamDX), ($pyy + $CamDY), ($pz + $CamDZ))
      $script:LA = @($px, ($pyy + $LookDY), $pz)
      $script:SKY = @($script:CP[0], ($script:CP[1] + 40.0), $script:CP[2])
      $found = $true; Info ("player {0:F2} {1:F2} {2:F2} -> cam {3:F2} {4:F2} {5:F2}" -f $px,$pyy,$pz,$script:CP[0],$script:CP[1],$script:CP[2]); break
    }
  }
  if(-not(Get-Process -Id $srv.Id -EA SilentlyContinue)){ Info 'server died'; break }
  Write-Host ("   .. +{0}s waiting spawn marker" -f [int]((Get-Date)-$launch).TotalSeconds)
}

$script:rows = @()
function CapAll($targetName,$lookat){
  $cr = CameraLookAt $script:CP $lookat
  Info ("$targetName camera_set ok={0} viewport_moved={1} applied={2}" -f $cr.ok,$cr.camera.viewport_moved,$cr.camera.applied_mode)
  Start-Sleep -Seconds 2
  foreach($m in @('printwindow','foreground','screen')){
    $png = Join-Path $OutDir ("{0}_{1}.png" -f $targetName,$m)
    $g = GrabMethod $m $png
    $mean = -1; $nb = -1; $sha=''; $ok=$false
    if($g){ $ok=[bool]$g.ok; if($g.stats){ $mean=$g.stats.meanBrightness; $nb=$g.stats.nonBlackRatio }; $sha=$g.sha256 }
    $script:rows += [pscustomobject]@{ target=$targetName; method=$m; ok=$ok; mean=$mean; nonblack=$nb; sha=$sha; png=$png }
    Info ("  {0,-11} ok={1} mean={2:F1} nonblack={3:F3} sha={4}" -f $m,$ok,$mean,$nb,($sha.Substring(0,[Math]::Min(12,$sha.Length))))
  }
  return $cr
}

$camOk = $false
if($found -and (Wait-Marker '[GATE-READY-AKS74U]' $roots $deadline)){
  Info 'AKS74U ready -> capturing SUBJECT (lookat player) and CONTROL (lookat sky)'
  Start-Sleep -Seconds 3
  $crSubj = CapAll 'subject' $script:LA
  # liveness rep of subject via printwindow (second grab, same view)
  Start-Sleep -Milliseconds 500
  $glive = GrabMethod 'printwindow' (Join-Path $OutDir 'subject_printwindow_live.png')
  $crCtrl = CapAll 'control' $script:SKY
  $camOk = ($crSubj -and ($crSubj.ok -eq $true -or $crSubj.ok -eq 1))
} else {
  Info 'player/AKS74U never became ready'
}

# diagnostics + summary
$ln=@(); foreach($r in $roots){ Get-ChildItem $r -Recurse -Include '*.RPT','script*.log' -EA SilentlyContinue | ForEach-Object { $ln += Read-Shared $_.FullName } }
$gate=@($ln|Where-Object{ $_ -match '\[GATE|\[MCP-POC\]|MCP-CLIENT' }) | Select-Object -Last 12
$summary = [pscustomobject]@{ found=$found; player=@($px,$pyy,$pz); cam=$script:CP; rows=$script:rows }
$summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $OutDir 'summary.json') -Encoding UTF8
Kill-DayZ
Write-Host "`n================ GRAB DIAG ================"
Write-Host ("playerFound={0}" -f $found)
$script:rows | Format-Table target,method,ok,mean,nonblack -AutoSize | Out-String -Width 200 | Write-Host
$gate | ForEach-Object { Write-Host "  $_" }
Write-Host "OUTDIR: $OutDir"
