# Plan — Fase 3 (Visual): camera_set / camera_get + capture_screenshot  (v2, post-review-adversarial)

> Spec para implementación por Codex (paso 3). **v2** — incorpora la **revisión adversarial de 3 subagentes (2026-06-09)** (ver `AI/10_Projects/DayZ_MCP/reviews/2026-06-09-adversarial-review-fase3-plan.md`). **SUP-1/SUP-2 + los 6 puntos RATIFICADOS por el usuario 2026-06-09** (D-11/D-12/D-13); opcional R22 Codex en frío.
> Research consolidado: `AI/10_Projects/DayZ_MCP/research/2026-06-09-fase3-visual.md`. APIs: `verified-apis.md §Visual`.
> Etiquetas: **[EXACT]** = verificado host-direct contra código real (cita `path:line`) · **[DESIGN]** = pseudocódigo a implementar · **[verify in-game]** = correcto offline, falta el spike · **[ASSUMPTION]** = supuesto operativo sin cita.
> Traza a product-spec grupo D: **D1** (`camera_set`/`camera_get` posan y miden la cámara, conservan roll) y **D2** (`capture_screenshot` PNG por window-grab, síncrono, ≤~25k tokens, tras gate de estabilidad). Intent D: "'ver' multi-ángulo para verdicts que requieren píxeles".

## Decisiones fijadas (Grill Modo B, 2026-06-09 — adjudicadas con el usuario)
1. **Alcance**: D1 + D2 juntas (un ciclo de test in-game, R5). **Peer cliente MÍNIMO para Visual**; la conducción client-auth B3 NO se diseña aquí (Q4 = "Mínimo para Visual"), solo una nota de extensión.
2. **CONFLICT-1 (D2)** — "A presupuesto de tokens": `<1MB` → **≤ ~25.000 tokens/respuesta (Claude Code, issue #9152)** + downscale agresivo small-by-default. En `product-spec.md`.
3. **CONFLICT-2 (D2)** — "Síncrono + readiness": `capture_screenshot` **síncrono con readiness-gate + timeout, sin job-id async** expuesto. En `product-spec.md`.

## Supersesiones por recon host-direct (RATIFICADAS 2026-06-09 — D-11/D-12)
> Dos hallazgos de esta sesión corrigen el research; el **verificador de citas los confirmó host-direct** (review doc §Confirmado). **Ratificadas por el usuario 2026-06-09** (D-11/D-12).
- **SUP-1 — D1 se mide sobre el `Camera` entity, NO sobre el indexado.** Todo call-site script de `SetCameraEx`/`SetCamera(int)` está en `scriptcamera.c` bajo `#ifdef GAME_TEMPLATE` **[EXACT]** `scriptcamera.c:1`/`:172`/`:48`/`:52`/`:73`/`:135` (gamelib, NO build DayZ). → en DayZ el indexado no mueve/mide el viewport. Medida = `Camera.GetCurrentCamera().GetTransform(out mat)`; aplicación = `Camera`/`FreeDebugCamera` entity.
- **SUP-2 — transporte = poller `RestContext` propio del cliente (T-A), NO RPC server→cliente.** `MissionGameplay` **sin `OnRPC`** **[EXACT]** `missiongameplay.c:1` (grep 0); receptor sería `modded DayZGame.OnRPC` `dayzgame.c:3086` con id custom sobre `ERPCs.RPC_END` (centinela "DO NOT APPEND" `erpcs.c:207`, riesgo de colisión). Como ya hay bridge HTTP probado (A1-A5), el peer cliente corre su propio `RestContext`. RPC = **fallback T-B documentado** si T-A falla. **T-A confirmado viable**: `CreateRestApi`/`GetRestApi` sin `#ifdef SERVER` ni guard `IsDedicated` **[EXACT]** `scripts\3_game\http\restapi.c:181-183`; el `MCPBridge` ya hizo `GetRestApi() ?? CreateRestApi()` server-side `MCPBridge.c:110-113`. **Cero precedente vanilla** → confirmar con el ping de Spike 0.

## Revisión adversarial aplicada (2026-06-09; 3 subagentes — arq/traza · citas R2 · riesgos in-game)
> Detalle completo en el review doc. Aquí, qué cambió en v2. Núcleo (SUP-1/SUP-2/T-A) confirmado; bloqueadores = superficie de impl ausente + 2 APIs mal-citadas + bugs que habrían quemado ciclos.
- **P1-c [EXACT]**: FOV getter `GetCameraFovDeg`/`GetCurrentCameraFovDeg` **NO existen** (no compilan). Real = **`Camera.GetCurrentFOV()` ESTÁTICO** (FOV de la cámara activa, radianes) `camera.c:13`. Sin getter por-instancia → D1 mide FOV de la activa (lo es post-`SetActive`).
- **P1-d [EXACT]**: `CreateObject(string,vector,bool create_local=false,…)` `game.c:690` — en cliente **`create_local=true` OBLIGATORIO** (vanilla `cameratoolsmenu.c:71`/`dayzintroscene.c`), si no el engine intenta spawn networked no-autorizado.
- **P1-e [EXACT]**: el "roll exacto" vía `SetTransform` no tiene precedente vanilla en cámara activa → **`SetOrientation`(yaw/pitch/roll) primario** (roll-capaz, `cameratoolsmenu.c:628`); `SetTransform(vector mat[4])` `enentity.c:356` **experimental** (sub-spike de fidelidad). Medida = `GetTransform(out mat)` `enentity.c:288`.
- **P1-f**: "FreeDebugCamera exige PlayerBase" mal-citado — `developerfreecamera.c:46-49` es el `else/LogError` del **caller**, no requisito de engine sobre `SetActive` (`:40`). Reframe abajo.
- **P1-g**: `SetTimeMultiplier` es **server-authoritative** `world.c:19` → el freeze de escena lo emite el **server bridge**, no `MCPClientBridge`.
- **P1-a/P1-b**: el routing Python de 2 colas y el `--mode phase3` **no existen** → especificados como [DESIGN] en Paso 4 (no "clonar").
- **P2-a**: el window-grab "validado" capturó la ventana **equivocada** (868×517 ≠ 1280×720) → el selector va a **Spike 0 offline**, por clase+pid.
- **P2-b**: ~25k tokens ≈ **~320×180 px** máx → D2 = "verdict visual grueso"; calibración offline.
- **P2-c**: "reusar" la maquinaria job = **port** (engine en `MCPBridge.c`, sin base compartida) → extraer `MCPJobRunner` base.
- Paths corregidos: `scripts\3_game\global\world.c`, `scripts\3_game\http\restapi.c`. `m_Jobs` = `MCPBridge.c:38`.

## Invariantes heredadas (NO re-decidir)
- Fase 0 (A1-A5) + Fase 1 (B1/B2; B3 client-auth diferida) + Fase 2 (C1/C2) PASS in-game. NO re-testear.
- **Transporte**: HTTP async sobre `127.0.0.1`, key en **query string** (no header), **401** sin/mala key, **400** fuera de whitelist; `*_now` PROHIBIDO en el tick → solo `GET(cb,req)`/`POST(cb,req,data)` **[EXACT]** `scripts\3_game\http\restapi.c:103/123/141`.
- **Enforce 1.29**: sin ternario `?:`; `Print(string.Format(...))`; sin `new` en ticks; arrays por componentes (`VectorToArray`), nunca `=vector`; `$mission:` (no `$profile:`).
- **Fail-closed (R6)** también en el peer cliente: misma key (del `MCPConfig`) + bind 127.0.0.1; poll sin key → 401; comando fuera de whitelist → rechazado.
- **Gate de carga** = `[MCP-POC] config loaded` in-game vía PBO desplegado `P:\Mods\@DayZ_MCP` (LL-117). Re-deploy: AddonBuilder `-packonly` (`tools/run-step0.ps1` `Invoke-DayZMcpPboBuild`). `-DayZPort 2402`; parar otras instancias DayZDiag.
- **SetTimeMultiplier(0) congela TODA la sim** (animaciones incl.) **[EXACT]** `scripts\3_game\global\world.c:15-19` → condicionar DESPUÉS de mover/asentar la cámara, y **server-side** (P1-g).

---

## Spike 0 — de-risks OFFLINE (antes de CUALQUIER rebuild) 🔎
> Lo de mayor ahorro de toda la review: 3 incógnitas son host-side puro. Resolverlas evita que Spike 3 pase con imagen de ventana-equivocada/over-budget y evita construir el transporte equivocado. **Ninguna necesita rebuild de @DayZ_MCP.**
1. **Ping `RestApi` client-side** (gate de T-A): ~20 líneas en el `@MCPTest` ya probado (filepatching client-side). `modded MissionGameplay.OnUpdate` hace una vez `GetRestApi()??CreateRestApi()` → `GetRestContext("http://127.0.0.1:PORT/")` → `GET(cb,"/ping?key=…")` + Print del callback. **Si aterriza en el Python → T-A confirmado, todo el transporte aguanta. Si no → T-B (RPC) ANTES de escribir `MCPClientBridge`.**
2. **Selector de ventana Win32** (sin DayZ build, arregla P2-a): lanzar `@MCPTest` a tamaño raro (`-x=1284 -y=724`) + dedicated server; `EnumWindows`+`GetWindowThreadProcessId`+`GetWindowRect`+`GetWindowText`+clase → predicado **determinista** (`pid==cliente && clase==render-DirectX (no ConsoleWindowClass) && rect≈pedido`). Pin `SetProcessDpiAwareness`. Capturar y mirar el PNG.
3. **Calibración de tokens** (sin DayZ build, arregla P2-b): un screenshot DayZ 1280×720 existente → downscale 160/240/320/480px → PNG→base64 → **contar con el tokenizer real** → hard-codear el target. + averiguar si `MAX_MCP_OUTPUT_TOKENS` sube el cap en este Claude Code (podría anular la tensión).

## Arquitectura de Fase 3 (la pieza nueva)
Fases 0-2 = 3 actores (Python host ↔ server bridge `MissionServer`). Fase 3 añade 2:
- **Peer CLIENTE** (`modded MissionGameplay` + `MCPClientBridge`, en el proceso cliente renderizado) — aplica/mide la cámara y postea al Python por su **propio `RestContext`** (T-A). 1ª vez que el proyecto levanta el lado cliente.
- **Window-grab host-side** (server Python, .NET `CopyFromScreen`) — captura la ventana del cliente; la imagen **NO pasa por el engine** (research conv. #4).

| Tool | Dueño | Por qué |
|---|---|---|
| `camera_set` / `camera_get` | **peer CLIENTE** | la cámara renderizada vive en el cliente |
| `capture_screenshot` | **Python host** (en proceso, no `/enqueue`) | la imagen es window-grab externo |
| escena (time/weather/freeze) | **server bridge** | world-state server-authoritative (P1-g) |

---

## Paso 0 — Contrato de mensajes (DTOs)
**[EXACT] Estado actual** `MCPMessages.c`: `MCPArgs` :8-33; `MCPResult` :128-149 (sin ctor; arrays `new` lazy en call-site, p.ej. `MCPBridge.c:1682`). `MCPMessages.c` compila en **ambos** peers → el peer cliente ve las clases nuevas.

**[DESIGN] Ampliar `MCPArgs`** (arrays nuevos → `new` en ctor :27-32):
```
int    camera;        // índice/handle lógico (default 0)
float  fov;           // D1: FOV vertical RADIANES (Camera.SetFOV), 0 = no tocar
ref array<float> cam_pos;       // D1 set: posición (3)
ref array<float> cam_orientation;// D1 set PRIMARIO: yaw/pitch/roll (3) → SetOrientation (roll-capaz)
ref array<float> cam_matrix;    // D1 set EXPERIMENTAL: base 4x4 plana = 12 floats → SetTransform
string cam_mode;      // "orient"(default) | "lookat" | "matrix"(exp) | "free"(FreeDebugCamera)
ref array<float> look_at;       // D1 cam_mode=lookat (3)
int    settle_ticks;  // ticks de asentamiento (wall-clock) antes de medir (default ~3) [verify]
string capture_scale; // D2: "small"(default,~320x180) | "tiny"(~160x90) | "full"(raro: casi nunca cabe)
int    max_tokens;    // D2: presupuesto (default ~25000)
```
**[DESIGN] Ampliar `MCPResult`** (sub-DTO ref, patrón `raycast`/`telemetry`): `ref MCPCamera camera;` (D1). **D2 es 100% host-side → `MCPResult` NO se toca para la imagen** (la imagen va por `ImageContent` del host; ver Paso 3).

**[DESIGN] Clases nuevas** (arrays `new` en ctor):
```
class MCPCamera { bool ok; string applied_mode;
  ref array<float> pos;        // medido (3) ← VectorToArray
  ref array<float> matrix;     // medido: base 4x4 plana (12) ← MatrixToArray
  ref array<float> dir;        // medido: forward (3)
  float fov;                   // medido (radianes) = Camera.GetCurrentFOV()
  bool  interpolation_complete;
  bool  viewport_moved;        // advisory (GetCurrentCamera()!=null) — la verdad es el match pos/matriz
  string error; }
class MCPCameraValidation { bool ok; string error; int mode_id; vector pos, orient, look_at; float fov; } // transient, NO serializa (mold MCPSpawnValidation :176)
```
**[DESIGN] Helper matriz↔array** (no existe; `VectorToArray` 3-comp **[EXACT]** `MCPBridge.c:1061-1067`). `SetTransform(vector mat[4])` **[EXACT]** `enentity.c:356`, `GetTransform(out vector mat[4])` **[EXACT]** `enentity.c:288` — **`vector mat[4]` es C-array fijo, NO `array<vector>`**:
```
void MatrixToArray(vector m[4], array<float> a){ a.Clear(); for(int r=0;r<4;r++){ a.Insert(m[r][0]); a.Insert(m[r][1]); a.Insert(m[r][2]); } } // 12 floats
void ArrayToMatrix(array<float> a, out vector m[4]){ for(int r=0;r<4;r++) m[r]=Vector(a.Get(r*3),a.Get(r*3+1),a.Get(r*3+2)); }
```

## Paso 1 — Peer cliente (modded MissionGameplay + MCPClientBridge)
**[DESIGN] Archivos nuevos** en `DayZ_MCP/scripts/5_Mission/` (el glob `config.cpp:27` los incluye, **sin tocar config** — verificado):
- `MissionGameplay.c` — `modded class MissionGameplay` (molde `MissionServer.c` **[EXACT]** `:1/:8/:19`): en `OnMissionStart` crea `m_ClientBridge = new MCPClientBridge(...)`; en `OnUpdate(timeslice)` → `m_ClientBridge.OnTick(timeslice)`. Solo instancia en el cliente (el engine elige raíz por peer) → sin guard `IsClient`.
- `MCPClientBridge.c` — poller cliente slim. Reusa: `RestContext` propio a `127.0.0.1:PORT` (**key del mismo `MCPConfig` `MCPMessages.c:1-6`** — P2-f), `GET /poll?peer=client`, `POST /result`, `RestCallback` (mold `MCPCallbacks.c`), DTOs compartidos. Deadlines = **tiempo de pared** (`m_ElapsedS`, no ticks — lección B2). Whitelist del peer = solo `camera_set`/`camera_get` (+ `unknown_command` fail-closed).
- **Maquinaria job/readiness (P2-c — es un PORT, no "reusa")**: el engine de jobs vive en `MCPBridge.c` (server-side, sin base compartida). **Recomendado: extraer `MCPJobRunner` base** (compone en `MCPBridge` y `MCPClientBridge`) — evita el copy-port y la regresión "wall-clock no ticks" (R7). Si copy-port: portar `m_Jobs`(`:38`)/`ProcessJobs`(`:1291-1341`)/`IsJobReady`(`:1343-1355`)/fase(`ProcessDriveProbe :1460-1485`)/`PostJobSuccess`(`:1660`)/`m_ElapsedS`(`:75`) y **stripear** spawn/raycast/telemetry. Presupuestar como ítem grande.

**[verify in-game] Topología (Spike 1)**: `MissionGameplay` instancia client-side en el setup dedicated server + cliente conectado (el mismo que fase 2 exige, LL-093); el `RestContext` del proceso cliente alcanza el Python (singleton por proceso → no colisiona con el del server). **Pre-confirmado por Spike 0.1 offline.**

## Paso 2 — D1 `camera_set` / `camera_get` (Camera entity primario — SUP-1)
**[DESIGN] `camera_set`** (en `MCPClientBridge`):
1. `ValidateCameraArgs`: `cam_mode`∈{orient,lookat,matrix,free}; orient→`cam_pos`+`cam_orientation` (3+3); lookat→`cam_pos`+`look_at`; matrix→`cam_matrix` 12; `fov>=0`. Fuera → `result.ok=false; error="bad_args"`.
2. **Contexto (cuando hay player local)**: `GetGame().GetMission().PlayerControlDisable(INPUT_EXCLUDE_ALL)` **[EXACT mold]** `missiongameplay.c:859` (evita que la cámara del jugador pelee — P2-d) + `GetGame().GetMission().GetHud().Show(false)` **[EXACT mold]** `cameratoolsmenu.c:96` (HUD fuera del screenshot). Teardown revierte.
3. **Aplicar** (job `kind="camera_set"`, `phase=APPLY`). **Borrar la cámara previa** si existe (`g_Game.ObjectDelete(m_ActiveCam)`, mold `dayzintroscene.c:44`) y retener la nueva en `m_ActiveCam` (GC):
   - `cam_mode=="orient"` (PRIMARIO, roll-capaz — P1-e): `Camera cam = Camera.Cast(g_Game.CreateObject("staticcamera", pos, true))` **[EXACT]** `game.c:690` (**`create_local=true` obligatorio** — P1-d) / mold `cameratoolsmenu.c:71-72`; `cam.SetActive(true)` `camera.c:45`; `cam.SetPosition(pos)` `:379`; `cam.SetOrientation(Vector(yaw,pitch,roll))` **[EXACT]** `cameratoolsmenu.c:628`; si `fov>0` `cam.SetFOV(fov)` `camera.c:67` (radianes).
   - `cam_mode=="lookat"`: `cam.SetPosition(pos)` + `cam.LookAt(look_at)` `camera.c:80` (+`SetFOV`). **Roll-lossy** (LookAt no expresa roll).
   - `cam_mode=="matrix"` (EXPERIMENTAL — sub-spike de roll): `ArrayToMatrix(cam_matrix,m); cam.SetTransform(m)` `enentity.c:356`. Comparar fidelidad de roll vs "orient".
   - `cam_mode=="free"` (fallback): `FreeDebugCamera fc=FreeDebugCamera.GetInstance()` `camera.c:90`; **`player.DisableSimulation(true)` antes** (mold `missionbenchmark.c:375` — P2-e); `fc.SetActive(true)` `developerfreecamera.c:40`; `fc.SetPosition`/`SetOrientation`|`LookAt`/`SetFOV` `missionbenchmark.c:338/341/377`. Teardown = **`fc.SetActive(false)`** (mold `:360`), **NUNCA** `DisableFreeCamera(player,true)` (teleporta — `developerfreecamera.c:58-59`).
4. **Settle (phase=SETTLE)**: `IsInterpolationComplete()` **[EXACT]** `camera.c:29` **O** `settle_ticks` agotados (wall-clock). El freeze de escena (`SetTimeMultiplier(0)`) **NO aquí** — lo emite el server bridge tras el ACK (P1-g, Paso 3).
5. **Medir + responder (phase=REPORT)**: `MCPCamera c=new MCPCamera(); c.ok=true; c.applied_mode=cam_mode;` — `Camera gc=Camera.GetCurrentCamera()` **[EXACT]** `camera.c:7` (si `null` → cámara de jugador activa → `c.viewport_moved=false; error="player_camera_active"`); `gc.GetTransform(m)` **[EXACT]** `enentity.c:288`/`cameratoolsmenu.c:451` → `MatrixToArray(m,c.matrix)`; `VectorToArray(gc.GetWorldPosition(),c.pos)`; `c.dir=m[2]`; `c.fov=Camera.GetCurrentFOV()` **[EXACT estático]** `camera.c:13`; `c.interpolation_complete=true`. `result.camera=c`.

**[DESIGN] `camera_get`** (read-only síncrono, mold `DispatchSceneRaycast` **[EXACT]** `MCPBridge.c:545`/`return true`): como el paso 5 sin aplicar; si `GetCurrentCamera()==null` → `ok=true`, `viewport_moved=false`, pose read-only roll-lossy de `g_Game.GetCurrentCameraPosition/Direction` **[EXACT]** `game.c:730/731` marcada como tal.

**D1 PASS** = `camera_get` tras `camera_set` (mode `orient`) → `matrix`==comandada (tolerancia tras ~0.05 m, base ~1e-3) **incluido roll** (comparar `matrix[0]`/`[1]`, no solo forward) + `fov` con tolerancia. (Sub-spike: ¿`matrix`(SetTransform) iguala o mejora el roll de `orient`?)

## Paso 3 — D2 `capture_screenshot` (host-side, síncrono, presupuesto de tokens)
**[DESIGN] Pipeline** (síncrono para el cliente MCP — CONFLICT-2; orquestado por el Python):
1. **Freeze server-side (P1-g)**: tras el ACK de settle de `camera_set` (el cliente posteó `interpolation_complete`), el Python ordena al **server bridge** `SetDate`+weather+`SetTimeMultiplier(0)` **[EXACT]** `scripts\3_game\global\world.c:15-19`. (El cliente NUNCA congela.) `[verify in-game]`: que el freeze server-side detiene las animaciones del render cliente (replicación).
2. **Window-grab host-side** (Python, en proceso, NO `/enqueue`): localizar la ventana del cliente con el **selector determinista de Spike 0.2** (clase render-DirectX + `pid==cliente`, NO `MainWindowHandle` ni título/tamaño — P2-a). `Graphics.CopyFromScreen` **[EXACT, single-grab validado]** `MCPTest\mcp-windowgrab-test.ps1:80`. Forzar la ventana topmost/foreground + no-ocluida + DPI-aware antes del grab.
3. **Gate de estabilidad EXTERNO [DESIGN, no validado]**: capturar **N frames** (best-of-N, no exigir convergencia perfecta) con **threshold calibrado offline** (~1-3%, NO 0.1% — follaje/agua/grain animan en render-time — P2-g); si no converge en `timeout` → aceptar el mejor de N (no `isError`). Métrica = mean abs pixel delta.
4. **Downscale a presupuesto (CONFLICT-1)**: a `capture_scale` (default `small` ~320×180; `tiny` ~160×90; `full` casi nunca cabe — P2-b) hasta **≤ ~25.000 tokens** base64. Target hard-codeado desde Spike 0.3.
5. **Entregar**: `{type:"image", data:<base64>, mimeType:"image/png"}` **[EXACT spec MCP]**; errores de negocio (ventana no encontrada, no-negra falló) → **`isError:true`**, no excepción de protocolo.

**D2 PASS** = imagen **no-negra con contenido real** (nonBlackRatio alto, no uniforme), **≤ presupuesto de tokens**, tras gate de estabilidad, síncrona, sin job-id. **Reformulado a "verdict visual grueso"** (¿escena no-negra? ¿el vehículo está? encuadre aproximado), NO detalle fino (~320px no lo permite — P2-b).

## Paso 4 — Harness Python + verdict (phase3)
- **P1-a · Routing por peer [DESIGN]** (NO existe — `mcp_server.py` es cola única `:155-168`): **dos colas nombradas por peer**; `GET /poll?peer=` lee el param con **default `server`** (back-compat: el poller `MissionServer` actual sigue intacto); `/result` sin cambio (keyed por `id`).
- **P1-b · Cliente [DESIGN]** (`mcp_client.py`): `choices` += `"phase3"` `:1026`; output-name → `fase3-verdict.json` `:1035`; enqueue por dominio (camera_* → cola cliente); **`capture_screenshot` lo ejecuta el harness en proceso (CopyFromScreen), NO va por `/enqueue`/`/await`**.
- **Whitelist** += `camera_set`/`camera_get` (cola cliente) y server (`scene_freeze`). Fail-closed (401/400) intactos + **test negativo phase3 contra la cola cliente** (mirror de A4 — P2-f).
- **`run-fase3.ps1`** (de `run-fase2.ps1`): dedicated server + **cliente conectado y renderizado** (no headless), readiness, matriz D1/D2, `fase3-verdict.json` con gate PASS/FAIL. La captura window-grab se integra aquí (host-side).
- **Anti-tautología (LL-115, R22)**: la pose esperada de D1 la fija el harness (comando), NO la lectura del bridge; match exacto `0.000` en el diff de pose = **sospechoso** (revisar procedencia). La imagen D2 se valida por propiedad independiente (nonBlackRatio/contenido), no por igualdad con un fixture del propio capturador.

## Mapeo cliente/servidor (obligatorio)
- `camera_set`/`camera_get` → **client-side** `MCPClientBridge`. El server bridge NO los maneja.
- `capture_screenshot` → **host-side** Python (window-grab en proceso). La imagen no entra al `MCPResult`.
- `scene_freeze` (time/weather/`SetTimeMultiplier`) → **server bridge** (server-authoritative — P1-g), orquestado por el Python tras el ACK del cliente.
- **A verificar in-game**: que un `Camera`/`FreeDebugCamera` activado desde `MCPClientBridge` (fuera de `UIScriptedMenu`) mueve el viewport (precedente fuerte: `dayzintroscene.c:44-56`, cliente puro sin menú/player); que `GetCurrentCamera()` lo refleja; que el `RestContext` cliente alcanza el Python (Spike 0.1); que el freeze server-side detiene el render cliente.

## Spikes (orden) + matriz de validación
**Spike 0 — OFFLINE (sin rebuild)**: (0.1) ping RestApi client-side en `@MCPTest`; (0.2) selector de ventana Win32; (0.3) calibración de tokens. **Gate de todo lo demás.**
**Spike 1 — Topología** (presupone Gates 1-3 de routing/harness hechos — P2/Spike1): `MissionGameplay` instancia client-side + `MCPClientBridge` round-trip al Python (un `camera_get` trivial devuelve algo).
**Spike 2 — D1** (Camera entity, SUP-1):
- `cam_mode=orient`: set pose con roll ≠ 0 → `camera_get` matrix==comandada **con roll** (compara base). 
- `cam_mode=matrix` (sub-spike): ¿iguala/mejora el roll? Elegir primario por el dato.
- `cam_mode=lookat`: forward apunta al target; pos coincide.
- `cam_mode=free` (si los Camera-entity no mueven viewport): FreeDebugCamera (DisableSimulation+SetActive; teardown sin teleport).
- (opcional barato) disproof indexado: `SetCameraEx(0,mat)` no cambia `GetCurrentCamera()` → confirma SUP-1.
- NEG: args malos → `bad_args`; `GetCurrentCamera()==null` → `player_camera_active` sin crash.
**Spike 3 — D2**: `capture_screenshot` tras `camera_set` + `scene_freeze` server-side → imagen no-negra, contenido real, best-of-N estable, **≤ target de Spike 0.3**, síncrona. NEG: ventana no encontrada → `isError`; (la inestabilidad ya no es error, es best-of-N).
**Backpressure**: batch D1/D2 respeta cap por tick del peer cliente sin jobs colgados.

## Gates de validación (orden)
0. **Spike 0 offline** PASS (T-A confirmado, selector determinista, target de tokens fijado) — **antes de escribir `MCPClientBridge`**.
1. **Source/compile-clean** (Enforce 1.29; arrays por componentes; matriz vía `MatrixToArray`; `MCPJobRunner`/port). Compila en ambos peers.
2. **Deploy**: AddonBuilder `-packonly` → `P:\Mods\@DayZ_MCP`; verificar por contenido que el PBO trae `MissionGameplay`/`MCPClientBridge`; gate de carga `[MCP-POC] config loaded` **en el cliente**.
3. **Harness**: 2 colas + whitelist + `--mode phase3` + `run-fase3.ps1` con cliente renderizado.
4. **In-game** (cliente conectado y renderizado): Spike 1 → 2 → 3 agrupados.
5. **Elegir path D1** (orient vs matrix vs free) por el dato; `decision-log.md` (nuevo D-NN) + HANDOFF.
6. **Cierre**: D1/D2 ✓ en product-spec; ratificar SUP-1/SUP-2 → Changelog si procede.

## Fuera de alcance (R25)
- **Conducción B3 / client-auth driving** (fase diferida): peer cliente MÍNIMO para Visual (Q4). Nota: el mismo `MissionGameplay`/poller podría más tarde alojar la conducción, pero NO se especula aquí sin su probe.
- **Multi-cam off-screen** (`SetWidgetWorld`+`RenderTargetWidget`): display-only sin readback (`enwidgets.c:705/236`). Fuera.
- **`MakeScreenshot`**: roto en diag (T165276) → window-grab. No se intenta arreglar.
- **RPC server→cliente (T-B)**: fallback documentado, NO implementado salvo que T-A (Spike 0.1) falle. (Si T-B: id `const int = ERPCs.RPC_END + 1000` anclado al centinela, NUNCA literal pequeño — `erpcs.c:207`.)
- **MCP stdio / packaging** (fase 4).

## Riesgos (con mitigación)
- **`Camera` entity activado desde un poller no mueve el viewport** → Spike 2; precedente fuerte `dayzintroscene.c:44-56` (cliente puro). Plan B = FreeDebugCamera (DisableSimulation+SetActive). Plan C = T-B (RPC al camera-tools existente).
- **`RestContext` cliente no alcanza el Python** → Spike 0.1 (offline). Mitigación = T-B.
- **Token budget vs imagen útil** (P2-b): ~320×180 máx → D2 = verdict grueso; calibrar (0.3); ver si `MAX_MCP_OUTPUT_TOKENS` sube.
- **Window-grab equivocado/ocluido/DPI** (P2-a): selector por clase+pid (0.2); forzar topmost+foreground+DPI-aware; verificar render-cuando-no-foreground (`-noPause`).
- **Freeze no detiene el render cliente** (P1-g): server-side; `[verify in-game]` que la replicación detiene animaciones.
- **Job machinery = port** (P2-c): extraer `MCPJobRunner` base; presupuestar.
- **`SetTransform` no fija roll en cámara activa** (P1-e): `SetOrientation` primario; `matrix` solo sub-spike.
- **`staticcamera` es built-in, NO `CfgVehicles`** → NO añadir guard `ConfigIsExisting` (false-negative).
- **Refs vivas**: retener `m_ActiveCam`; borrar la previa en re-set (leak/stacking).

## Ratificado por el usuario (2026-06-09)
1. **SUP-1** (D1 vía Camera entity, no indexado) — ✅ ratificado (D-11).
2. **SUP-2** (transporte T-A poller cliente, no RPC) — ✅ ratificado, «rest>rpc» (D-12). Gateado por el ping de Spike 0.1; T-B (RPC) solo si el ping falla.
3. **`SetOrientation` primario** vs `SetTransform` para roll — ✅ SetOrientation primario; `SetTransform` = sub-spike (D-13).
4. **Tools separadas** (`camera_*` vs `capture_screenshot`, no combinadas) — ✅ separadas (D-13).
5. **`MCPJobRunner` base compartida** (cliente+server) vs copy-port — ✅ base compartida, anti-R7 (D-13).
6. **Topología de test**: dedicated server + cliente renderizado — ✅ confirmada.

---
**Siguiente paso**: ✅ ratificado (D-11/D-12/D-13) → correr **Spike 0 (offline, sin rebuild)** que valida el transporte (ping RestApi client-side) y fija el target de tokens; (opcional) R22 Codex en frío; luego Codex implementa por pasos (0→4).
