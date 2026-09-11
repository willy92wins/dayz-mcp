# af59 — convergencia de `dayz-test.ps1`

Hay 12 ficheros en `copies/` (las 12 versiones distintas). El orquestador midio 18 arboles; las 6 copias identicas a alguna de estas 12 no estan en el workspace. Baseline de comparacion: `copies/LFGungame_dev.ps1` (514 lineas, ancestro) y `copies/LFQuad2_dev.ps1` (645, la mas reciente). Codigo despojado de comentarios: las 12 comparten 398-406 de las 408 lineas utiles de Gungame (ratio 0.78-0.99). Eso ya decide el tono: **no hay 12 disenos, hay un script y un punado de parches**.

## CLASIFICACION

Los cubos (a)/(b)/(c) clasifican **diferencias**, no el esqueleto identico. De las ~600 lineas de una copia tipica (mediana 589-592: A6_SR2M / MERCEDES / LFHeli / A6_MK47):

| Cubo | Lineas | % de ~600 | Que es |
|---|---:|---:|---|
| Nucleo identico (no es divergencia) | **~430** | **72%** | lifecycle MCP, preflight, mission/cfg, launch, Wait-ServerBind |
| **(a) configuracion de mod** | **~45** | **8%** | parametros CLI + perfiles derivados de `DevRoot` |
| **(b) deriva funcional real** | **~75** | **12%** | packonly, temp wipe, include-list, VPP Permissions, credenciales, gates |
| **(c) divergencia accidental** | **~50** | **8%** | comentarios, codigo muerto, backports a medias, el mismo arreglo en dos sitios |

Suma 600. **(b) no es mayoria.** Contra el union (Gungame + parches + SUB_BRZ + gates LFQuad) (b) crece hasta ~250 lineas, pero ~170 de esas son el plugin Forza de SUB_BRZ, que ya se auto-desactiva. El script comun cabe en ~560-620 lineas mas un gancho opcional.

### (a) CONFIGURACION DE MOD — parametros del script compartido

Ninguna copia hornea el nombre del mod, la lista extra, el puerto ni el perfil. `ExtraMods` default es `''` y `BaseMods` es `'@CF;@Dabs Framework;@VPPAdminTools'` en las 12. El `-Mod` obligatorio y `DevRoot = Split-Path -Parent $PSScriptRoot` ya parametrizan el arbol. Lista que el compartido tiene que exponer (union de los `param(`):

- Identidad: `Mod` (obligatorio), `Source`
- Load order: `ExtraMods`, `BaseMods`, `NoBaseMods`, `ServerMods`
- Mision / red: `Mission`, `Mode`, `Port`, `ServerWait`
- Cliente: `Width`, `Height`, `PlayerName`, `NoFilePatching`, `NoPause`
- Admin: `AdminSteamId`, `AdminPass`
- Ciclo de vida: `RunId`, `Kill`, `Preflight`, `Retail` (hoy todas hacen `Die` al entrar; ver (c))
- Build: `Build`, `Clean`, `PackOnly`, `BuildOnly`, `Release`, `IncludeList`
- Ganchos (no config de mapa, pero si de arbol): `Verify`, `VerifyOnly`, `VerifyP3d`, `PreBuildScript`, `PostBuildScript`

Perfiles (`_server`, `_client`, `serverDZ.cfg`) no son parametro: salen de `DevRoot`. Puerto 2302 / mission chernarus / ServerWait 60 son defaults; SUB_BRZ pone `ServerWait = 120` en `copies/SUB_BRZ_dev.ps1:54` — eso es (a), no un algoritmo distinto.

### (b) DERIVA FUNCIONAL REAL — una decision por item

1. **Autodetect `-packonly` si no hay `.p3d/.paa/.rvmat`.** Ausente en Gungame (`copies/LFGungame_dev.ps1:347-350`); presente desde kt (`copies/kt_roadkill_armed_dev.ps1:352-358`) en adelante. **Comun, para todos.** Sin esto un mod de scripts binariza a un PBO de cientos de bytes.

2. **Wipe de `temp` antes de cada build.** En A6_SR2M / LFHeli / MERCEDES / GunRacks / LF_VStorage / LFPowerGrid / SUB_BRZ (`copies/A6_SR2M_dev.ps1:408`). No en Gungame, kt, ExpandedBuilding, A6_MK47, LFQuad2. **Comun, para todos.** Es el arreglo del staging rancio; no es gusto de un mod.

3. **Lista `-include=` para que binarize copie `.c`.** Cinco mecanismos, un solo proposito:
   - A6_MK47 genera un `.txt` en `%TEMP%` (`copies/A6_MK47_dev.ps1:417-419`)
   - GunRacks usa `tools\include.lst` si existe (`copies/GunRacks_dev.ps1:458-461`)
   - LFPowerGrid usa `$src\include.lst` y avisa si falta (`copies/LFPowerGrid_dev.ps1:424-427`)
   - SUB_BRZ escribe `_addonbuilder_include.lst` con `*.c;*.asi;*.anm` (`copies/SUB_BRZ_dev.ps1:619-623`)
   - LFQuad2 solo incluye en `-Release`, y el default es `-packonly` (`copies/LFQuad2_dev.ps1:466-475`)
   **Comun: generar la lista de MK47 por defecto; `-IncludeList` la sustituye.** No perder el chequeo de `.c` de PowerGrid (`copies/LFPowerGrid_dev.ps1:437-445`) — **comun**. El default packonly de LFQuad2 **no** se impone al resto: es optimo para iterar un quad, letal si alguien espera ODOL. El comun binariza cuando hay assets **y** pasa include.

4. **VPP dual layout (JSON + `Permissions\SuperAdmins.txt` + `credentials.txt`).** ~40 lineas extra vs el JSON-only de Gungame/kt/LFQuad2. Esta en MK47/SR2M/Heli/MERCEDES/PowerGrid/GunRacks/VStorage/SUB_BRZ (`copies/A6_MK47_dev.ps1:266-327`). **Comun, para todos.** El JSON-only es un backport incompleto, no una politica.

5. **`Initialize-LifecycleCredentials` (GunRacks, LF_VStorage).** Lee `DAYZ_MCP_CLIENT_ID_JSON` / `DAYZ_MCP_LEASE_TOKEN` del proceso y los borra (`copies/GunRacks_dev.ps1:124-135`). **Comun, para todos.** Es alcance de credencial, no un extra de GunRacks. El comentario `CLAIM-R21-TEST-CREDENTIAL-SCOPE-TEMPLATE` es (c) y se borra.

6. **`Mode = none` (solo MK47, `copies/A6_MK47_dev.ps1:38` y `:584`).** Equivale a `BuildOnly`. **Comun como alias**, no se pierde.

7. **`-NoPause` (solo MERCEDES, `copies/MERCEDES_AMGLF_dev.ps1:543`).** **Opcional, para todos** (switch apagado por defecto). Una linea.

8. **`-BuildOnly` / `-Release` + `Invoke-BuildGate` (LFQuad2).** BuildOnly **comun**. Release+BankRev **opcional via `-PostBuildScript` / `-Release`**. Los tokens `LFQUAD-DBG` etc. (`copies/LFQuad2_dev.ps1:69-70`, `:355-382`) **no entran en el comun**: son de este mod. Si se copian, el release gate del quad se convierte en el release gate de un Mercedes.

9. **Gates Forza G3/G7/artifact (SUB_BRZ, ~170 lineas, `copies/SUB_BRZ_dev.ps1:415-584`).** **Opcional, gancho `-Verify`.** Ya hacen WARN+return si faltan las tools (`:441-442`, `:548-550`). No se pierde; no se arrastra al resto. Ver seccion siguiente.

10. **`-binarizeFullLogs` (solo PowerGrid, `copies/LFPowerGrid_dev.ps1:429`).** **Comun.** No cambia el PBO; solo hace visibles los `!>` vacios.

11. **Politica Retail.** Todas mueren al entrar con `-Retail` (`copies/LFQuad2_dev.ps1:617`, igual en las otras). `DayZ_BE.exe` en MK47 (`copies/A6_MK47_dev.ps1:79`) y `$ServerExe` / `$DedicatedExe` en 8 copias **nunca se ejecutan**. Eso es (c) codigo muerto, no una politica viva. El comun mantiene el `Die` de cuarentena.

### (c) DIVERGENCIA ACCIDENTAL — se unifica sin discusion

- Comentarios `DAYZ_INFRA.md L113-124` vs `seccion "Variables de entorno..."` (`copies/LFHeli_dev.ps1:70` vs `copies/A6_SR2M_dev.ps1:71`). LFHeli y A6_SR2M son ratio 0.998 en codigo util; las 14 lineas del unified diff son prosa.
- `$ServerExe` asignado y nunca leido. `Start-Server` usa siempre `$DiagExe` (`copies/A6_SR2M_dev.ps1:528`).
- `$ServerMods` usado en Gungame y LFQuad2 **sin declararlo** en `param(` (`copies/LFGungame_dev.ps1:456`). En 5.1 vale `$null`. Se declara en el comun.
- Mensajes distintos del timeout de bind (LFQuad2 vs SUB_BRZ). Mismo control.
- Temp wipe y VPP dual aplicados a unas copias y no a otras: el *intento* es (b); el *reparto irregular* es (c).
- El bug af59 (ExitCode + `Test-Path $pbo`) esta **literalmente en las 12**. No es deriva: es el tronco. `copies/LFGungame_dev.ps1:350-353`, `copies/LFQuad2_dev.ps1:477-480`, `copies/SUB_BRZ_dev.ps1:625-628`.

## LAS QUE VAN POR DELANTE

### SUB_BRZ (809 lineas, 68% vs LFQuad2) — no es el comun

Lo de mas, medido contra el tronco SR2M/Heli (~589 lineas), son ~220 lineas:

- Bloque Forza (`copies/SUB_BRZ_dev.ps1:108-122`): `$ForzaTools`, `$PboTool`, `$VerifyCar = 'brz'`, control `civiliansedan_mlod.p3d`, rutas absolutas del usuario.
- `Invoke-PyGate` / `Invoke-G3-StructuralVerify` / `Invoke-G7-OfflineDiff` / `Invoke-ArtifactGates` (`:415-584`).
- Cableado en `Invoke-Build` antes y despues de AddonBuilder (`:596-598`, `:635-640`) y `VerifyOnly` en Main (`:780-785`).
- `ServerWait = 120` y params `Verify`/`VerifyP3d`.

No es un superconjunto util del launcher. Es el tronco **mas** un plugin de contrato p3d (Tarea 6 / Task 11) que habla con `C:\Users\guill\ForzaDayZ\`. El propio comentario dice que si las tools o el p3d faltan, WARN y skip — pensado para reusarse en un mod no-Forza. Si el comun sale de aqui, cada arma y cada rack arrastran PYTHONPATH de un venv `_temaB-work`, `PboViewer.exe` y `profiles\brz.json`.

**Veredicto:** experimento de producto (coche Forza), no abandonado, no canon. El comun **no** nace de SUB_BRZ. El gancho `-Verify` / `-PreBuildScript` conserva el valor.

### LFGungame + kt_roadkill — si, son la base vieja

Codigo util: ratio **0.987**. kt = Gungame + `PackOnly` + heuristica hasAssets + warn de PBO &lt; 4096 (`copies/kt_roadkill_armed_dev.ps1:348-367`). Todas las demas conservan 398-406 de las 408 lineas utiles de Gungame. No es una rama alternativa: es el commit 0. Lo que falta ahi (VPP Permissions, temp wipe, include, PackOnly en Gungame) son backports, no decisiones de Gungame.

LFQuad2 **no** es un mejor tronco. Anade gates buenos (BankRev, `config.bin` en release) pero:

- Tokens de debug del quad hardcodeados (`copies/LFQuad2_dev.ps1:69-70`).
- Default `-packonly` invertido respecto al resto (`:473-475`).
- Sigue poniendo `-temp=` en `P:\temp\$Mod` (`:458`) y el mismo `ExitCode` + `Test-Path` (`:477-480`).
- `-Retail` muere en Main `:617` **antes** de que `:463` pueda usarlo para forzar release: codigo muerto.

El comun sale del tronco SR2M/Heli/MERCEDES (casi identicos, 0.996-0.998) mas los parches portables (include generado, chequeo `.c`, credenciales, BuildOnly) mas el hunk de af59. No sale de LFQuad2 ni de SUB_BRZ.

## DISENO PROPUESTO

### Donde vive y como se invoca

Un solo script:

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz-test.ps1`

(o, si se quiere desacoplar del MCP, un hermano `_infra\dayz-test.ps1` en el mismo disco; la ruta absoluta se pinnea en el shim).

Cada `<Mod>_dev\tools\dayz-test.ps1` pasa a ser un shim de ~12 lineas que **no** duplica logica:

```powershell
#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$Mod,
    [Parameter(ValueFromRemainingArguments = $true)] [object[]]$Rest
)
$shared = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz-test.ps1'
& $shared @PSBoundParameters
```

`DevRoot` se sigue calculando desde `$PSScriptRoot` del **shim** (el tools del mod), no desde el script compartido: el compartido tiene que aceptar `-DevRoot` o usar `Get-PSCallStack` / un parametro que el shim pasa. Mas simple y 5.1-safe: el shim asigna `$env:DAYZ_DEV_ROOT` al padre de su `$PSScriptRoot` y el comun lo lee. Un arbol no migrado sigue con su copia local; uno migrado llama al comun. **No hay que tocar los 18 a la vez.**

### Parametros (cubo a)

Los de la lista de CLASIFICACION. Defaults identicos a Gungame salvo `ServerMods = ''`, `AdminPass = 'dayzadmin'`, `PackOnly`/`BuildOnly` presentes. `ServerWait` default 60; un mod lento (SUB_BRZ) lo pasa en el shim o en la invocacion.

### Que pasa con cada (b)

| Item | Destino |
|---|---|
| packonly auto | comun |
| temp wipe | comun, **fuera de P:** (af59) |
| include generado + `-IncludeList` | comun |
| conteo `.c` estilo PowerGrid | comun |
| `-binarizeFullLogs` | comun |
| VPP dual + AdminPass | comun |
| LifecycleCredentials | comun |
| Mode none / BuildOnly | comun |
| NoPause | switch comun, default off |
| LFQuad BuildGate / tokens | se queda en `LFQuad2_dev\tools\build-gate.ps1`; el shim pasa `-PostBuildScript` |
| Forza G3/G7/artifact | se queda en `SUB_BRZ_dev\tools\forza-gates.ps1`; el shim pasa `-Verify` |
| Retail Die | comun (cuarentena intacta) |

### Migracion sin romper (incremental)

Orden, un arbol por vez. Las sesiones de agente vivas en otros mods no se enteran.

1. **Aterrizar el comun en DayZ_MCP_dev/tools** con el hunk af59. No borrar ninguna copia.
2. **Canario:** un arbol sin sesion viva (si hay duda, ExpandedBuilding o kt). Sustituir `tools\dayz-test.ps1` por el shim. Probar `-Preflight` y `-Build -Mode none` (o `-BuildOnly`).
3. **Resto idle**, uno a uno. El shim puede convivir con un `dayz-test.ps1.bak` local.
4. **Arboles con agente vivo:** no se tocan hasta que esa sesion acabe. Mientras, si hace falta el arreglo af59 ya, se puede copiar **solo** el hunk de `Invoke-Build` (paso 1 es el comun; este es un parche temporal de 40 lineas). Eso no exige los 18.
5. **SUB_BRZ y LFQuad2 los ultimos**, con el sidecar de gates y el parametro gancho. Hasta entonces su copia local sigue siendo la verdad.
6. Nada de golpe salvo el paso 1 (un fichero nuevo, cero mods rotos).

Prohibido: un commit que reescriba los 18 `tools\dayz-test.ps1` el mismo dia.

### Arreglo af59 — hunk exacto (PowerShell 5.1)

Hoy las 12 hacen esto (ejemplo mas reciente):

```458:482:copies/LFQuad2_dev.ps1
    $temp   = Join-Path $WorkDrive "temp\$Mod"
    ...
    $p = Start-Process -FilePath $AddonBuilder -ArgumentList $abArgs -Wait -NoNewWindow -PassThru
    if ($p.ExitCode -ne 0) { Die "AddonBuilder failed (exit $($p.ExitCode)). Check config.cpp / paths." }
    $pbo = Join-Path $target "$Mod.pbo"
    if (-not (Test-Path $pbo)) { Die "Build reported success but $pbo is missing." }
    Invoke-BuildGate -Pbo $pbo -Release:$buildRelease
    Ok "deployed: $pbo"
```

`$WorkDrive` default `P:\` (`copies/LFQuad2_dev.ps1:84`), asi que `-temp=` vive **dentro** de P:. `Start-Process` no captura stdout, asi que `"Build Successful"` / `"[ERROR]: Build failed"` se ignoran. Exit 0 + PBO viejo = `[ok] deployed`.

Sustitucion en el comun (sin `&&`, `||`, `??` ni ternario):

```powershell
    # af59: AddonBuilder temp must live outside P:. A concurrent -addon="P:" build
    # keeps foreign files open; "Clearing temp folder" then fails, exit code stays 0,
    # and Test-Path still sees yesterday's PBO.
    $tempRoot = Join-Path $env:TEMP 'dayz-addonbuilder'
    if (-not $tempRoot -or $tempRoot -like 'P:\*') {
        $tempRoot = Join-Path $env:LOCALAPPDATA 'Temp\dayz-addonbuilder'
    }
    $temp = Join-Path $tempRoot $Mod
    if (Test-Path -LiteralPath $temp) {
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }

    $pbo = Join-Path $target "$Mod.pbo"
    $mtimeBefore = $null
    $hashBefore = $null
    if (Test-Path -LiteralPath $pbo) {
        $prev = Get-Item -LiteralPath $pbo
        $mtimeBefore = $prev.LastWriteTimeUtc
        $hashBefore = (Get-FileHash -LiteralPath $pbo -Algorithm SHA256).Hash
    }

    $stdoutLog = Join-Path $env:TEMP ("dayz-ab-" + $Mod + ".out.txt")
    $stderrLog = Join-Path $env:TEMP ("dayz-ab-" + $Mod + ".err.txt")
    foreach ($log in @($stdoutLog, $stderrLog)) {
        if (Test-Path -LiteralPath $log) { Remove-Item -LiteralPath $log -Force }
    }

    $abArgs = @("`"$src`"", "`"$target`"", "-prefix=$Mod", "`"-temp=$temp`"")
    # ... PackOnly / include / -clear unchanged ...

    $p = Start-Process -FilePath $AddonBuilder -ArgumentList $abArgs -Wait -NoNewWindow -PassThru -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
    $abOut = ''
    if (Test-Path -LiteralPath $stdoutLog) { $abOut += [IO.File]::ReadAllText($stdoutLog) }
    if (Test-Path -LiteralPath $stderrLog) { $abOut += [IO.File]::ReadAllText($stderrLog) }

    $failedText = $false
    if ($abOut -match '\[ERROR\]:\s*Build failed') { $failedText = $true }
    $okText = $false
    if ($abOut -match 'Build Successful') { $okText = $true }

    if ($p.ExitCode -ne 0) {
        Die "AddonBuilder failed (exit $($p.ExitCode)). Check config.cpp / paths."
    }
    if ($failedText -or -not $okText) {
        Die "AddonBuilder printed a failed or incomplete build (exit $($p.ExitCode)). Refusing to treat the existing PBO as new."
    }
    if (-not (Test-Path -LiteralPath $pbo)) {
        Die "Build reported success but $pbo is missing."
    }

    $now = Get-Item -LiteralPath $pbo
    $hashAfter = (Get-FileHash -LiteralPath $pbo -Algorithm SHA256).Hash
    if ($mtimeBefore -ne $null) {
        if ($now.LastWriteTimeUtc -le $mtimeBefore -or $hashAfter -eq $hashBefore) {
            Die "PBO was not replaced (mtime/hash unchanged). AddonBuilder exit 0 is not a successful deploy."
        }
    }
    Ok "deployed: $pbo ($($now.Length) b)"
```

Las dos detecciones que el brief midio (mtime/sha **y** `Build Successful`) van juntas: un falso "Successful" con PBO intacto sigue muriendo por hash; un PBO tocado por otra sesion sin "Successful" muere por el texto.

Borrador del `Invoke-Build` completo: `dayz-test-shared.ps1.propuesta` en esta raiz (no es un `.ps1` de produccion).

## MERECE LA PENA

**Si.** Con los numeros delante, no por defecto.

Si (b) hubiera sido el 60% del fichero, 18 copias seria la forma correcta. No lo es. El 72% es el mismo launcher MCP. El 8% es parametros que **ya existen**. El 12% de (b) es casi todo *arreglos que alguien anadio en un arbol y no copio*: VPP Permissions, temp wipe, include, credenciales. Eso no es "cada mod adapta el molde"; es "el molde se rompio 12 veces". El 8% de (c) es ruido.

Los dos unicos (b) que no deben entrar en el tronco — gates Forza (~170 lineas) y tokens/release del Quad (~90 lineas) — ya tienen forma de plugin. Forzarlos al resto seria el molde que no encaja. Dejarlos fuera y converger el resto **es** el molde que encaja.

El bug que motiva af59 esta en las 12 copias con el mismo `if ($p.ExitCode -ne 0)` + `Test-Path $pbo`. Sin comun, el hunk se pega 18 veces y en tres meses vuelve a divergir. Con comun + shims incremental, se pega una vez y los arboles con agente vivo no se tocan hasta que puedan.

Coste: un script nuevo + shims de 12 lineas, un mod por dia. No es un proyecto. Es barato **porque** la clasificacion no salio (b).

## LO QUE NO PUDE VERIFICAR

- Las 6 copias de las 18 que no estan en `copies/`. El brief dice 18 arboles / 12 versiones; aqui solo hay las 12. Si alguna "duplicada" no lo era, esa divergencia no esta en este informe.
- Ejecucion real de AddonBuilder, BankRev, DayZ o el daemon MCP: el brief lo prohibe. El hunk af59 esta escrito contra el fallo descrito y contra el texto de las 12 copias, no contra una reproduccion nueva.
- Si `Start-Process -RedirectStandardOutput` captura **toda** la consola de AddonBuilder en esta maquina (el brief afirma que buscar `Build Successful` funciona; no lo he vuelto a medir).
- Contenido de `include.lst` reales en los arboles (no viajan con estas copias). GunRacks/PowerGrid dependen de un fichero que aqui no esta.
- Que `Get-FileHash` sobre un PBO de ~100 MB sea aceptable en el loop de iteracion; si no, basta mtime+texto y se deja el sha como opt-in.
- Sesiones de agente vivas ahora mismo: no enumere procesos ni arboles fuera de este workspace.
- Si `$env:TEMP` en algun host de build apunta a P: (el hunk tiene fallback a LocalAppData).
