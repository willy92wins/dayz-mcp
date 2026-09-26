===== PROMPT INICIO =====

Tarea: arrancar la implementación de **Fase 3 (Visual)** de DayZ-MCP. Esta sesión cubre **únicamente D1** (`camera_set` / `camera_get`): pasos 0, 1 y 2 del plan v2 + el routing Python y el harness de D1 (paso 4 parcial). **D2 (`capture_screenshot` / window-grab) NO se toca** — depende de Spike 0.2/0.3, aún pendientes. No la implementes aquí ni siquiera parcialmente.

Contexto: el transporte T-A (peer cliente con su propio `RestContext` HTTP) está **PROBADO in-game** (Spike 0.1, 2026-06-09: el cliente alcanzó el Python, `[MCP-PING] OK`). SUP-1/SUP-2/D-13 ratificados por el usuario. La review adversarial del plan ya está hecha y aplicada (plan v2 §"Revisión adversarial aplicada").

## Carga inicial obligatoria

Lee estos archivos (rutas absolutas) antes de tocar nada:

1. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-09-fase3-visual.md`
   (plan v2 — la spec. Pasos 0-2 + paso 4 routing. §"Supersesiones" y §"Revisión adversarial aplicada" son VINCULANTES. SUP-1/SUP-2/D-11/D-12/D-13 ya ratificados).
2. `C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\verified-apis.md`
   (§Visual = APIs cámara con `path:line`; §Transporte = `RestApi` client-side CONFIRMADO in-game Spike 0.1 — el contrato que el peer cliente imita).
3. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\spike0\MCPTest_Ping_MissionGameplay.c`
   (patrón `RestApi` client-side PROBADO in-game: `GetRestApi()??CreateRestApi()` → `GetRestContext(url)` → `SetHeader` → `GET(cb,"path?key=")` + `RestCallback`. `MCPClientBridge` lo calca).
4. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c`
   (server bridge: MOLDE del motor de jobs/readiness a extraer en `MCPJobRunner`, + estilo de dispatch/DTOs/`PostResult` + el `TryInit` que lee `MCPConfig`. **Léelo, NO lo modifiques** — ver restricción 4).
5. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c`
   (DTOs a ampliar + struct `MCPJob`).
6. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_server.py`
   (cola única actual; aquí va el routing de 2 colas por peer).
7. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_client.py`
   (harness; aquí va `--mode phase3` + enqueue al dominio cliente + verdict D1).

NO releas el research consolidado ni las mitades `-claude`/`-codex` (ya consolidadas en el plan). Decisiones vinculantes (ya citadas en el plan): **D-11** D1 vía `Camera` entity, **D-12** transporte T-A poller cliente, **D-13** tools separadas + `SetOrientation` primario + `MCPJobRunner` base compartida — en `DayZ_MCP_dev\decisions\decision-log.md`. Molde del harness in-game: `DayZ_MCP_dev\tools\run-fase2.ps1`. Patrón `RestCallback`: `DayZ_MCP\scripts\5_Mission\MCPCallbacks.c`.

## Alcance acotado — D1 (pasos 0, 1, 2 + paso 4 parcial del plan v2)

Los Enforce nuevos van en el mod compilable `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\` (el glob de `config.cpp` ya los incluye — **NO toques `config.cpp`**, verificado en recon). `MCPMessages.c` compila en AMBOS peers, así que el cliente ve las DTOs.

### Paso 0 — DTOs (modifica `MCPMessages.c`)
- Ampliar `MCPArgs` con los campos cámara del plan §Paso 0: `camera`, `fov`, `cam_pos`, `cam_orientation`, `cam_matrix`, `cam_mode`, `look_at`, `settle_ticks`. **DECLARA TAMBIÉN** `capture_scale`/`max_tokens` (D2) pero NO los uses esta sesión. Arrays nuevos → `new` en el ctor (patrón `pos`).
- `MCPResult` += `ref MCPCamera camera;`.
- Clases nuevas EXACTAS del plan §Paso 0: `MCPCamera`, `MCPCameraValidation` (transient, no serializa — molde `MCPSpawnValidation`).
- NO toques las DTOs de raycast/telemetry/spawn.

### Paso 1 — Peer cliente (3 archivos NUEVOS)
- `MCPJobRunner.c`: base de jobs/readiness reutilizable, extraída del patrón de `MCPBridge.c` (`m_Jobs` map, **deadline en TIEMPO DE PARED con `m_ElapsedS`** —no ticks—, `ProcessJobs`/`IsJobReady` router por `kind`, fases, `PostJobSuccess`). SOLO la base; **NO migres `MCPBridge.c` a usarla** (restricción 4).
- `MCPClientBridge.c`: poller cliente que COMPONE `MCPJobRunner`. `RestContext` propio a `127.0.0.1` (key del `MCPConfig` — patrón `TryInit` de `MCPBridge.c`), `GET /poll?peer=client`, `POST /result`, `RestCallback` (molde `MCPCallbacks.c` / ping). Whitelist del peer = `camera_set`/`camera_get` (+ `unknown_command` fail-closed). Helpers `MatrixToArray`/`ArrayToMatrix` (plan §Paso 0).
- `MissionGameplay.c`: `modded class MissionGameplay` (molde `MissionServer.c`): instancia `MCPClientBridge` en `OnMissionStart`, lo tickea en `OnUpdate(timeslice)`. Solo instancia en el cliente; sin guard `IsClient`.

### Paso 2 — D1 `camera_set` / `camera_get` (en `MCPClientBridge`)
Implementa EXACTAMENTE el plan §Paso 2:
- `camera_set` (job `kind="camera_set"`, fases APPLY/SETTLE/REPORT): validar; si hay player local `GetMission().PlayerControlDisable(INPUT_EXCLUDE_ALL)` + `GetMission().GetHud().Show(false)`; borrar la cámara previa (`g_Game.ObjectDelete`); `Camera.Cast(g_Game.CreateObject("staticcamera", pos, true))` — **`create_local=true` OBLIGATORIO** (`game.c:690`); `SetActive(true)`; `cam_mode=="orient"` (PRIMARIO) → `SetPosition` + `SetOrientation(Vector(yaw,pitch,roll))`; `lookat` → `LookAt`; `matrix` (sub-spike) → `SetTransform`; `fov>0` → `SetFOV(fov)` (RADIANES); settle = `IsInterpolationComplete()` O `settle_ticks` (wall-clock); medir → `GetTransform(out mat)`→`MatrixToArray`, `GetWorldPosition`, **`Camera.GetCurrentFOV()` ESTÁTICO** (`camera.c:13`). Retener `m_ActiveCam` viva. `SetTimeMultiplier` NO aquí (es server-side, fuera de D1).
- `camera_get`: read-only síncrono (molde `DispatchSceneRaycast`, `return true` mismo tick); si `GetCurrentCamera()==null` → `ok=true`, `viewport_moved=false`.
- **PROHIBIDO** el indexado `SetCameraEx`/`GetCamera(0,…)` (SUP-1: GAME_TEMPLATE-only, no mueve el viewport DayZ).

### Paso 4 (PARCIAL — SOLO D1) — Python + harness
- `mcp_server.py`: **dos colas por peer**; `GET /poll` lee `?peer=` con **default `server`** (back-compat: el poller `MissionServer` actual intacto); `/result` sin cambio. `WHITELISTED_COMMANDS` += `camera_set`/`camera_get` (cola cliente). Fail-closed 401/400 intacto.
- `mcp_client.py`: `--mode` choices += `phase3`; output → `fase3-verdict.json`; enqueue `camera_*` a la cola cliente; **suite D1**: `camera_set` con pose de **roll ≠ 0** → `camera_get` → compara `matrix` **incluido roll** (`matrix[0]`/`[1]`, no solo forward) + `fov` con tolerancia. Anti-tautología (LL-115): la pose esperada la fija el harness, NO la lectura del bridge; un match exacto `0.000` es sospechoso.
- `run-fase3.ps1` (clon de `run-fase2.ps1`): dedicated server + cliente **RENDERIZADO** (no headless) + `-WaitInGameSeconds ~300` (BUG-009); corre la matriz D1; emite `fase3-verdict.json` con GATE PASS/FAIL.

## Suite de tests automatizados (gate OFFLINE de esta sesión)
Amplía `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_mcp_server.py` (unittest stdlib, molde existente):
- routing de 2 colas: `/poll?peer=client` drena solo la cola cliente; `/poll` (sin peer) = `server` y no toca la cola cliente.
- whitelist `phase3`: `camera_set`/`camera_get` aceptados en la cola cliente; comando fuera de whitelist → rechazado; sin key → 401.

Comando esperado: `python -m unittest discover tests` desde `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\` → **todos verdes**. (El gate in-game D1 lo corre el usuario con `run-fase3.ps1` DESPUÉS; no en esta sesión.)

## Restricciones críticas (vinculantes toda la sesión)
1. **Enforce 1.29**: sin ternario `?:`; `Print(string.Format(...))`; sin `new` en ticks periódicos; `array<float>` por componentes (`VectorToArray`/`MatrixToArray`), NUNCA `=vector`. Compila en AMBOS peers (`MCPMessages` compartido).
2. **SUP-1 (D-11)**: D1 SOLO sobre el `Camera` entity. PROHIBIDO el indexado. `create_local=true`.
3. **SUP-2 (D-12)**: transporte = `RestContext` propio del cliente (PROBADO in-game, ver el ping .c). NO RPC, NO `DayZGame.OnRPC`, NO `ERPCs`.
4. **NO toques `MCPBridge.c` ni `MissionServer.c`** (server bridge = fases 0-2 PASS in-game; cero regresiones). `MCPJobRunner` se crea NUEVO; la **migración del server** a usarlo es **SESIÓN APARTE** — déjala anotada en el handoff (Bloque D).
5. **NO implementes D2** (capture_screenshot, window-grab, frame-diff, downscale, ImageContent) ni los targets de Spike 0.2/0.3. Te tentará porque el plan §Paso 3 lo describe y `MCPArgs` lleva `capture_scale`/`max_tokens` — esos campos solo se **DECLARAN** en Paso 0; su tool es sesión aparte. Resiste.
6. **Python stdlib only** (`http.server` crudo, como fase 0). Dos colas keyed por peer; default `server`.
7. **Fail-closed (R6 / convención del proyecto)** en el peer cliente: key del `MCPConfig`, bind `127.0.0.1`; poll sin/mala key → 401; fuera de whitelist → rechazado.
8. **R21 (doble revisión) NO en esta sesión**: implementa D1 y PARA. No te autorrevises ni hagas cleanup adicional. La review del plan ya está hecha.
9. **NO improvises fuera del plan**: si una firma no compila o algo no encaja, NO improvises — anótalo en Bloque C con `path:line` del plan, aplica interpretación conservadora, marca para revisión.

## Output esperado al cerrar la sesión

### Bloque A — Archivos creados/modificados
Lista con rutas absolutas y tamaño aproximado (Enforce nuevos + `MCPMessages.c` + Python + `run-fase3.ps1` + tests).

### Bloque B — Resultado de los tests
Output COMPLETO y literal de `python -m unittest discover tests` (no parafrasees). Si declaras compile-clean del Enforce, di CÓMO lo verificaste (no hay compilador offline fiable → declara "no verificado offline, gate in-game pendiente").

### Bloque C — Hallazgos durante la implementación
Por hallazgo: sección/archivo del plan afectado, qué no encajaba, acción aplicada (o "marcado para resolución"), sugerencia. Si no hay: "Sin hallazgos."

### Bloque D — Handoff para sesión siguiente
- Estado al cierre (D1 implementado; gate in-game pendiente).
- Deuda explícita: **migración de `MCPBridge.c` a `MCPJobRunner`** (no hecha esta sesión). 
- Próximo paso: Spike 0.2/0.3 (targets de D2) + D2 (`capture_screenshot`).
- Infra reutilizable que dejas lista.

===== PROMPT FIN =====
