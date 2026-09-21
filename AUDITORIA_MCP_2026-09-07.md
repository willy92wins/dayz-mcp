# Auditoría completa DayZ MCP — sobreingeniería, optimización, unificación

**Fecha:** 2026-09-07 · **Ámbito:** `DayZ_MCP_dev` (el MCP, no PowerGrid) · **Autor:** auditoría automatizada con 3 lanes paralelas + verificación de citas
**Fichero autocontenido:** todo lo necesario para entender y actuar está aquí dentro. No requiere leer HANDOFF, GATES ni auditorías previas.

## 0. Alcance y metodología

**Qué se auditó (leído directamente):**
- `addon/scripts/5_Mission/*.c` (8 ficheros) + `addon/scripts/4_World/MCP_CarScript.c` — tamaños verificados: `MCPBridge.c` 80.958 B, `MCPClientBridge.c` 93.901 B, `MCPDialogController.c` 17.138 B, `MCPMessages.c` 10.475 B, `MCPJobRunner.c` 1.941 B, `MCPCallbacks.c` 1.147 B, `MissionServer.c` 443 B, `MissionGameplay.c` 409 B, `MCP_CarScript.c` 15.127 B.
- `tools/dayz_mcp/*.py`: **64 ficheros** verificados por conteo (2026-09-07). Monstruos: `server.py` ~4.867 l, `process_lifecycle.py` ~4.130 l, `loopback.py` ~3.711 l, `session_coordination.py` ~3.561 l.
- `tools/*.py` primer nivel: **17 ficheros** (incluye `g0_abba_gate.py` 1.816 l, `g0_site_gate.py` 731 l, `install_mcp.py` 1.140 l, `build_native_launcher.py` 1.064 l).
- `tools/tests/test_*.py`: **185 activos** + 6 helpers + 26 `.bak_*`. Gates ejecutables: 12. Checks: 4. `gates/*.md`: 38 ficheros.

**Qué NO se verificó (límites honestos):**
- No se lanzó el juego (DayZDiag/DZC real). Todo lo marcado `[POTENTIAL]` requiere 1 vuelo in-game o medición con `py-spy`/conteo de spawns.
- No se midió duración real de la suite de tests ni CPU idle del daemon (se propone cómo medirlo en §7).
- Las líneas citadas son aproximadas ±5 líneas (el árbol cambia entre sesiones); la función/símbolo citado es el ancla estable. Citas verificadas por grep el 2026-09-07: `MAX_DISPATCH_PER_TICK`, `SPAWN_READY_RADIUS`, `m_CallbackRefs`, `EncodeQueryValue`, `pollHz`, `POLL_INTERVAL_S=0.05`, `netstat -ano`, `MAX_TAIL_BYTES=256KB`.

**Convención de severidad:** `[ALTA]` = corregir pronto (carga, crecimiento sin cota, duplicación grande). `[MEDIA]` = refactorizar en el próximo tramo. `[BAJA]` = pulido. `[POTENTIAL]` = no estoy seguro, incluyo hipótesis + cómo confirmarla. Todo lo dudoso va como POTENTIAL por petición expresa.

---

## 1. Resumen ejecutivo (la conclusión en 30 segundos)

1. **El problema nº 1 es duplicación, no rendimiento.** `MCPBridge.c` (servidor) y `MCPClientBridge.c` (cliente) son ~70% el mismo fichero con otro nombre: `TryInit`, FSM de poll, `EncodeQueryValue`, `DrainPending`, `PostResult`, drive-probe de 5 fases, `IsVehicleFixtureReady`. Se pueden borrar **~400 líneas** con una `MCPBridgeBase` + `MCPHttpUtil` + `MCPDriveProbe` compartidos. Es el cambio con mejor ratio líneas-eliminadas/hora.
2. **El problema nº 2 es N relojes donde debería haber 1.** En Enforce hay 4-5 loops (`MissionServer/Gameplay::OnUpdate` → `Bridge/ClientBridge::OnTick` + `MCPJobRunner::Tick` + `DialogController::Tick`); en Python hay 7 (parent-death, idle, lease-heartbeat 45 s, box-heartbeat 30 s, reaper, `wait_for` pollers 0,05-1,0 s, health-probe por llamada). La propuesta §6 los colapsa en un `MCP_TickHub` (Enforce) y un `SupervisorTick` 1 s (Python).
3. **El problema nº 3 es polling + subprocess en caliente.** `POLL_INTERVAL_S=0.05` como norma (`tools/dayz_mcp/server.py:78,1034,1668`), `sleep(0.05)` en `control_client.py:548`, `take_announcement` con `sleep(0.001)` (`native_launcher_backend.py:1280-1286`), `netstat -ano` por llamada (`orphan_guard.py:378,471`), re-escaneo de logs de hasta 64 MB por waiter (`server.py:2450-2499`). Todo tiene mitigación barata (caché TTL 1-1,5 s, `LogFollower` incremental, `threading.Event`, backoff).
4. **La capa de tests es sobredimensionada:** 185 suites, god-files (`test_process_lifecycle.py` 4.675 l, `test_daemon.py` 2.115 l), 67 imports test→test, `retail_quarantine` en 8+ suites, `launcher_registry` en 7, 4 mecanismos de contrato distintos, `.bak` y logs en el árbol. Se puede recortar ~30% sin perder señal (§4).
5. **Nada de esto impide que el MCP funcione.** La arquitectura (bridge REST loopback + daemon + gates) es sólida y está bien instrumentada. La deuda es de crecimiento, no de diseño roto. Scores en §8.

---

## 2. ENFORCE SCRIPT — findings

### 2.1 Ticks y procesos no armonizados (el tick global que pides)

- **E-TICK-01 [MEDIA] 4 loops de misión + 2 relojes auxiliares.** `addon/scripts/5_Mission/MissionServer.c:19-28` (`OnUpdate → Bridge::OnTick`) y `MissionGameplay.c:19-28` (`OnUpdate → ClientBridge::OnTick`) hacen lo mismo (acumular tiempo, `ProcessJobs`, `DrainPending`, `StartPoll`). Además `MCPJobRunner.c:90-94` (`Tick`) y `MCPDialogController.c:266-278` (`Tick`) son otros 2 relojes. `MCPBridge.c:103-137` (`OnTick`) y `MCPClientBridge.c:261-315` (`OnTick`) duplican fases.
  **Propuesta:** `MCP_TickHub` (o base `MCPBridgeBase`) con fases fijas `Jobs → Pending → Poll`; `MissionServer/Gameplay::OnUpdate` llaman al hub. `CarScript::OnInput` se queda fuera a propósito (va atado a sim física) — documentarlo.
- **E-TICK-02 [MEDIA] `MCP_CarScript.c:603-664` corre en CADA coche cada frame sim** aunque el MCP esté inactivo; `MCPVehicleTrace.Capture(this,dt)` en `:663` se invoca siempre.
  **Propuesta:** primera línea tras `super` (antes del chequeo de deadline `:608`): `if (!MCPCarDrive.s_Active && !MCPVehicleTrace.s_Active) return;`. Cero `GetGame()` en el camino frío.
- **E-TICK-03 [MEDIA] `MCPBridge.c:2821-2871` `ProcessJobs` (servidor) NO usa `MCPJobRunner`**; el cliente sí (`MCPClientBridge.c:265-268`). Dos implementaciones del mismo loop (iteración inversa, `deadline_s`, `Remove`).
  **Propuesta:** migrar el servidor a `MCPJobRunner` con `MCP_ProcessJob/MCP_IsJobReady/MCP_Post*` como hace el cliente (`:2532-2598`). Elimina ~50 líneas y unifica el reloj (`m_ElapsedS` vs `JobRunner.GetElapsedS()`).
- **E-TICK-04 [BAJA] Doble reloj en diálogo.** `MCPDialogController.c:230-240` (`Open` guarda `m_DeadlineNowS` de JobRunner + `m_DeadlineTickS` de `GetTickTime`); `DeadlineReached:944-961` y `ElapsedS:963-977` consultan ambos.
  **Propuesta:** un solo reloj (el `m_Clock`/JobRunner inyectado en `:1729`). Eliminar `m_OpenedTickS/m_DeadlineTickS/m_HasTickDeadline` (~15 líneas).

### 2.2 Duplicación Bridge ↔ ClientBridge

- **E-DUP-01 [ALTA] `TryInit` clonado.** `MCPBridge.c:139-212` vs `MCPClientBridge.c:317-390` (~70 líneas idénticas: `GetRestApi/CreateRestApi`, `$profile/$mission:dayz_mcp.json`, loopback-check, `pollHz` — verificado `MCPBridge.c:195-197` y `MCPClientBridge.c:371-373` —, `SetHeader`). Solo difiere `m_PollCtx`.
  **Propuesta:** `MCPBridgeUtil::TryInitConfig(out url,key,instance,pollHz)` estático compartido.
- **E-DUP-02 [ALTA] FSM HTTP clonada.** `StartPoll/OnPollSuccess/OnPollFail/ReloadKeyAfterFailure/GetPollVersion/EncodeQueryValue/DrainPending/QueuePendingOrFail/PostResult/PostCommandError/ReleaseCallback/Log`. Verificado: `EncodeQueryValue` en `MCPBridge.c:2072` vs `MCPClientBridge.c:3832`; `GetPollVersion` en `:2064` vs `:3824`; `ReloadKeyAfterFailure` en `:380` vs `:577` (hasta el comentario está copiado). Los 4 callbacks (`MCPCallbacks.c:1-73` vs `MCPClientBridge.c:1-95`) colapsan a 2 genéricos.
  **Propuesta:** clase base `MCPBridgeBase` o `MCPHttpUtil` estático. Ahorro estimado ~300 líneas.
- **E-DUP-03 [ALTA] Drive-probe duplicado.** Constantes `DRIVE_PROBE_PHASE_*` (`MCPBridge.c:21-25`) vs `DRIVE_CLIENT_PHASE_*` (`MCPClientBridge.c:153-157`); `ProcessDriveProbe*` (`:2990-3160`) vs `ProcessDriveProbeClient*` (`:2605-3125`); `IsVehicleFixtureReady` (`:2953-2988`) verbatim igual que `IsDriveClientVehicleFixtureReady` (`:3165-3200`); `EncodeNetworkMoveStrategy` (`:3199-3217` vs `:3202-3220`); `CaptureDriveProbeOwnership` (`:3162-3187` vs `:3137-3163`).
  **Propuesta:** un único `MCPDriveProbe.c` con FSM parametrizada por sumidero de control (servidor: `SetThrottle/SetBrake` directos `:3101-3107`; cliente: `MCPCarDrive.Set` con deadman `:3080,3101`).
- **E-DUP-04 [MEDIA] 5 helpers de búsqueda esférica casi iguales.** `FindTransportNear` (`MCPBridge.c:2769-2789`), `FindTransportNearClient` (`MCPClientBridge.c:2632-2652`), `SelectVehicleGetInTransport` (`:2686-2742`), `FindNearestObjectNearClient` (`:1990-2037`), `FindUniqueObjectNearType` (`MCPBridge.c:1565-1610`), más 3 bloques inline en `DispatchVehiclePrepareFixture:1008-1028`, `DispatchTelemetryObjectAt:2236-2256`, `DispatchEntitiesQuery:1407-1429`.
  **Propuesta:** `MCPWorldQuery::FindAt(pos,radius,predicate)` único reutilizando `m_ReadyObjects/m_ReadyProxyCargos`. Hoy cada verbo limpia y rellena los mismos 2 arrays.
- **E-DUP-05 [BAJA] Doble supresión de input.** `MCPClientBridge.c:3887-3933` (`Suppress/RestoreGameplay`) vs `MCPDialogController.c:855-932` (`Lock/UnlockInput`). Dos sistemas con flags paralelos (`m_ControlsSuppressed/m_PlayerSimulationDisabled` vs `m_ControlsLocked/m_FocusLocked`, ambos con `PlayerControlDisable(INPUT_EXCLUDE_ALL)`).
  **Propuesta:** un `MCPInputLock` con refcount compartido por diálogo y cámara/drive.

### 2.3 Hot path / mala optimización

- **E-HOT-01 [MEDIA] `EncodeQueryValue` reconstruye strings char a char en cada poll.** `MCPBridge.c:235,238` (`StartPoll` concatena `key+caps+inst` con `+` y re-codifica `SERVER_CAPABILITIES` (`:32`, ~200 chars) cada ~200 ms; igual cliente `:421,423` con `CLIENT_POLL_CAPS`. `value.Substring(i,1)+ToAscii` por carácter.
  **Propuesta:** pre-codificar `caps` y `ver` una vez en `TryInit`/primer poll (ya se cachea `m_PollVersion`; extender a `m_CapsEncoded`).
- **E-HOT-02 [MEDIA] `GetGame()` sin cachear (87 ocurrencias).** Ejemplos: `DispatchSurfaceQuery:1088,1107,1108` triple `GetGame()` por comando; `ValidateSpawnArgs:2540,2552`; `IsClientInGame:659`; `Suppress/RestoreGameplay:3894-3932` hace `Cast(GetGame().GetPlayer())+Cast(GetGame().GetMission())` en cada fase.
  **Propuesta:** `GameBase g = GetGame();` local por `Dispatch/OnTick`; pasar `player/mission` como parámetro a las fases del probe.
- **E-HOT-03 [MEDIA] `GetPlayers(m_Players)` enumera todos los jugadores por comando.** `ResolveRaycastIgnore:2134-2135`, `GetFirstHuman:2653-2654`, `FindHumanByUid:2677-2678`, `BuildAllPlayers:3373-3374`, `BuildPlayerState:3401-3402`. Además `m_Players` es array compartido con `Clear()`+relleno — aliasing si un dispatch reentra.
  **Propuesta:** cachear la lista 1 vez por `OnTick` (o `GetPlayer()` directo si solo se necesita el primero); no compartir el array entre `Resolve*` y `Build*` sin copia.
- **E-HOT-04 [ALTA] `IsSpawnReady:2887-2912` hace `GetObjectsAtPosition3D(pos, 8.0)` CADA TICK por cada job `spawn` pendiente** (verificado `MCPBridge.c:2897` + `SPAWN_READY_RADIUS=8.0` en `:13`). Radio 8 m en broadphase cada frame hasta `deadline_s` 5 s.
  **Propuesta:** no sondear; `CreateObjectEx` es síncrono — diferir 1 frame sim y dar `ok` con `pos_real`, o sondear a 5 Hz máx. con contador.
- **E-HOT-05 [MEDIA] `TakeNearestEntities:2740-2767` es selección O(n·limit).** `DispatchEntitiesQuery:1392-1405` permite `radius≤200 m`, `limit≤128`.
  **Propuesta:** una pasada guardando top-k por inserción o un solo sort parcial.
- **E-HOT-06 [MEDIA] `MCPVehicleTrace.Start:166-173` pre-aloca hasta 8.192 objetos** (`max_samples≤8192`, `MCPClientBridge.c:1167`) con `new` en `while`. `CaptureNow:420-559` por muestra (20-60 Hz) llama ~20 getters (`GetPosition/Direction/Orientation/Throttle/Steering/Brake/Handbrake/WheelCount/Contact×4/AngVel×4/EngineIsOn/Gear/Owner/NetworkID/Velocity`).
  **Propuesta:** mantener el pool (bien), bajar el techo documentado (8.192×~40 campos es mucho para Enforce) y fijar 20 Hz como default (`MCPMessages.c:143` ya lo es). No añadir más campos por muestra.
- **E-HOT-07 [MEDIA] `CaptureContact:244-275`, líneas `:252-260`: triple `IndexOf("wheel"/"Wheel"/"WHEEL")` por cada contacto físico** (evento caliente).
  **Propuesta:** `zoneName.ToLower()` una vez + un solo `IndexOf("wheel")`.
- **E-HOT-08 [BAJA] `CarScript::OnInput:655-660` llama 5 setters CADA frame** (`SetThrottle/Steering/Brake/Handbrake/SetBrakesActivateWithoutDriver(false)`) mientras el control está activo.
  **Propuesta:** `SetBrakesActivateWithoutDriver(false)` una sola vez en `MCPCarDrive.Set` (`:11-20`).
- **E-HOT-09 [MEDIA] UI: `CountUiWidgetsNamed:2138-2172` DFS recursivo de TODO el workspace por `ResolveUniqueUiWidget:2118-2134`** (1-2 veces por verbo UI vía `ResolveUiRoot:2045-2113`). `CollectUiNodes:2427-2460` recursivo hasta 512 nodos (`UI_TREE_MAX_LIMIT:159`); cada `FillUiNode:2362-2425` con 4 `Cast` + getters; `BuildUiMatchedPath:2228-2258` + `UiSiblingOrdinal:2263-2296` (scan de hermanos) + `EncodeUiPathSegment:2304-2339` (char a char) por comando.
  **Propuesta:** (a) `CollectUiNodes` iterativo o default 256 (ya existe `UI_TREE_DEFAULT_LIMIT:158` — no subirlo); (b) cachear el walk por tick si llegan `ui_tree` en ráfaga; (c) documentar que `root+path` cuesta 2 walks.
- **E-HOT-10 [BAJA] `InvokeUiClick:2466-2530` aloca `new Param4` en `:2488,2507` por ancestro** + `GetScript/GetUserData` por nivel + fallback `GetUIManager().GetMenu().OnClick:2518-2527`.
  **Propuesta:** aceptable (1 click por comando); opcionalmente cachear handler por `matched_path` con TTL 1 s.
- **E-HOT-11 [BAJA] Logs en camino tibio.** `DispatchWorldSpawn:550,552,560,562` 4 `Log` por spawn; `PostResult:3460` / cliente `:4004` concatenan 5+ piezas por resultado; `MCPDialogController.c:170,187,209,262,435` `Print(string.Format(...))` por probe/open/finish.
  **Propuesta:** borrar los 4 logs de fase de spawn (debug leftover), dejar 1; envolver `Print` de diálogo en `#ifdef MCP_DEBUG`.

### 2.4 Buffers / POST / JSON

- **E-BUF-01 [MEDIA] `PostResult` hace POST por comando** (`MCPBridge.c:3434-3461`, cliente `:3975-4005`: `new JsonSerializer + WriteToString + new MCPResultCallback + POST`; verificado `m_CallbackRefs.Insert` en `:3451`/`:3992`). Con `MAX_DISPATCH_PER_TICK=4` (verificado `:3`/`:132`) son hasta 4 serializaciones+POST por tick + 1 GET a 5 Hz. Sin coalescing. `m_CallbackRefs` **no tiene bound** (solo `ReleaseCallback` en `:3480-3490`/`:4026-4032`).
  **Propuesta:** mantener 1:1 (el daemon lo espera por `id`), pero si `m_CallbackRefs.Count()>16` diferir `DrainPending` un tick.
- **E-BUF-02 [BAJA] Backpressure con inanición.** `OnTick:122-125` (servidor) y `:301-304` (cliente) no hacen poll si `m_Pending.Count()>8`. Si el daemon deja de consumir `result`, el pending nunca baja y el poll muere para siempre.
  **Propuesta:** permitir 1 poll cada N s (p.ej. 5 s) aunque haya backlog, para drenar/cancelar.
- **E-BUF-03 [BAJA] `m_Pending` acotado (32 servidor `:4`, 16 cliente `:133`) — bien. `m_Jobs` no:** un `id` duplicado hace `m_Jobs.Insert(job.id)` sobrescribiendo (`DispatchWorldSpawn:579`, `DispatchVehicleEnter:704`, cliente `:887,958,999`) sin `Contains` previo.
  **Propuesta:** `if (m_Jobs.Contains(id)) { PostCommandError("duplicate_id"); return; }`.
- **E-BUF-04 [BAJA] Pool de telemetría sobredimensionado.** `m_TelemetryFixtureLinePool` 64 objetos pre-alocados en ctor (`MCPBridge.c:85-90`) para `DispatchTelemetryFixtureJsonl:2280-2356`, que solo retiene `last_valid` (`:2336-2338`). Límites buenos (`max_lines≤64 :1893`, `MAX_JSONL_LINE_CHARS=4096 :16`).
  **Propuesta:** eliminar el pool, reusar 1 objeto scratch + `last_valid`.

### 2.5 Errores / robustez

- **E-ERR-01 [MEDIA] `PostResult:3435-3439` descarta en silencio si `!m_Configured||!m_Ctx`** (igual cliente `:3976-3980`). Un comando aceptado puede no tener respuesta nunca.
  **Propuesta:** `Log("result dropped id=...")` como mínimo.
- **E-ERR-02 [MEDIA] `OnPollSuccess:279-303` difiere TODO lo que venga tras el primer `world_spawn`** (`deferFromWorldSpawn`), incluidos comandos no relacionados — head-of-line blocking.
  **Propuesta:** solo diferir `world_spawn` entre sí.
- **E-ERR-03 [MEDIA] Jobs con `kind` desconocido cuelgan hasta timeout.** Servidor `IsJobReady:2873-2885` devuelve `false` para kinds no `spawn/seat`; cliente `MCP_ProcessJob:2532-2562` igual para no-cámara/drive/getin/dialog. Resultado: 5 s colgado (`JOB_TIMEOUT_S:6`, `CAMERA_JOB_TIMEOUT_S:135`) y `timeout` genérico.
  **Propuesta:** `else { job.error="unknown_job_kind"; return true; }` — fail-fast.
- **E-ERR-04 [ALTA] `Shutdown` asimétrico y frágil.** Servidor `:3492-3542` (50 l) vs cliente `:4058-4195` (~140 l con `postedTerminal/pollDistinct`, loops `DetachBridge`, `reset()` condicional). Se llama desde `~MissionServer:3-6` / `~MissionGameplay:3-6` y además `~MCPClientBridge:237-240` → doble `Shutdown` posible; toca `GetGame()/RestoreGameplay/ReleaseCamera/MCPVehicleTrace.Abort` en plena destrucción.
  **Propuesta:** idempotencia explícita (`if (m_ShutDown) return;`) y no tocar cámara/sim si `!GetGame()||!GetMission()`.
- **E-ERR-05 [BAJA] `pollHz` sin cota superior** (`TryInit:195-198` solo `>0`; verificado). Un `dayz_mcp.json` con `pollHz:1000` = REST cada frame.
  **Propuesta:** `m_PollHz = Math.Clamp(cfg.pollHz, 1.0, 10.0)`.
- **E-ERR-06 [BAJA] `IsFiniteFloat` asimétrico.** Servidor `:2490-2502` rechaza `NaN` y `±float.MAX`; cliente `:3759-3767` solo `value!=value` (NaN), acepta `+inf`. Un `throttle=inf` cliente pasa el filtro (`DispatchVehicleControl:1065`).
  **Propuesta:** una sola implementación en `MCPMessages.c`/`MCPBridgeUtil` (la del servidor).

### 2.6 GUI + config

- **E-GUI-01 [BAJA] `addon/config.cpp:1-36` correcto y mínimo** (idéntico en ambas copias `DayZ_MCP_dev`/`DayZ_MCP`). Solo vigilar que no diverjan.
- **E-GUI-02 [BAJA] `gui/layouts/mcp_dialog.layout` coherente con `MAX_FIELDS=6`** (`MCPDialogController.c:30`, `Row0..Row5` en `:61-288`, `BtnOk/Yes/No/Submit/Cancel`, `Error`). Botones en rojo con `EmptyHighlight` — cosmético. `priority 2000` + `visible 0` inicial — bien.

---

## 3. HARNESS PYTHON — findings

### 3.1 Sobreingeniería (wrappers de wrappers)

- **P-ARC-01 [ALTA] Cadena `session_*` con 5 fachadas.** Real en `tools/dayz_mcp/control_client.py:225` (`session_status`), `:228` (`lifecycle_status`), `:321` (`session_acquire`), `:489` (`session_heartbeat`), `:502` (`session_release`), `:658` (`session_acquire_wait`). Forwarding puro en `tools/dayz_mcp/server.py:1302-1373` (`ClientRuntime.*` → `self._control.<mismo>` + 1 retry en `:1284-1300` `_control_with_lazy_spawn`). Segundas closures en `server.py:3261,3288,3335,3343,3361` (modo embebido). 3 `Protocol` que repiten la firma (`lease_supervisor.py:16-21`, `native_launcher_transaction.py:25-38`, `dayz_test_tool.py:66`). `tools/g0_abba_gate.py:798-856` (`GateClient`) reimplementa la máquina `enqueue→/session/wait→active` de `control_client.py:708-748`.
  **Propuesta:** un único `SessionClient` + decorador `with_lazy_spawn+retry` en `control_client.py`; `ClientRuntime` y closures delegan; `GateClient` importa o se marca `deprecated`.
- **P-ARC-02 [ALTA] Stack launcher de 7 capas.** `secure_launcher.py:78-168` → `native_launcher_transaction.py:677-803` → `native_launcher_backend.py:1566` (1.548 l) → `native_bundle.py` (772 l) + `launcher_registry.py` (399 l) + `native_broker_protocol.py` (220 l) + `native_debug_state.py` (261 l) + `native_child_announcement.py` (107 l). Síntoma: `secure_launcher.py:105-115` recibe 2 args para ignorarlos (`del accredited_paths, heartbeat_supervisor`); `:233-240` wrapper de 1 línea; `:27-75` `_IncrementalRedactor` con `O(n²)` asumido en comentario.
  **Propuesta:** colapsar a 2 capas (transacción + backend); VPP-preflight (500 l en `native_launcher_transaction.py:100-606`) a `vpp_preflight.py` propio.
- **P-ARC-03 [ALTA] Autoridad daemon en 6 ficheros.** `daemon_policy_contract.py:76` (119 l) vs `daemon_policy.py:319` (371 l) vs `normal_daemon_policy.py:49,158` (147 l) vs `host_config.py:40` (`DaemonProvenance`, 1.097 l) vs `daemon_credential.py:54` (342 l) vs `accredited_daemon_transport.py` (288 l). `server.py:1119-1178` revalida campo a campo lo que `host_config.require_matching_keyfile` ya hace.
  **Propuesta:** un `daemon_authority.py` (`DaemonAuthority{port,argv,keyfile,exe,cwd,generation}` + `load/verify/serialize`); `doctor.py:113` (`_DaemonPolicy` test-double) lo reutiliza.
- **P-ARC-04 [MEDIA] `effective_schema` en 4 ficheros (~650 l).** `effective_schema.py` (274 l) + `effective_schema_catalog.py` (111 l) + `effective_schema_core.py` (196 l) + `effective_schema_runtime_validators.py` (69 l).
  **Propuesta:** fusionar a 1 módulo (o 2: schema + validators).
- **P-ARC-05 [MEDIA] `dayz_test_*` 6 ficheros con duplicación admitida.** `dayz_test_tool.py` (1.571 l) + `dayz_test_worker.py` (660 l) + `dayz_test_request.py` (426 l) + `dayz_test_storage.py` (584 l) + `dayz_test_readiness.py` (145 l) + `dayz_test_modes.py` (193 l). `native_launcher_transaction.py:250-280` admite duplicación (`Same composition as dayz_test_worker._mods... duplicated because worker is sealed`).
  **Propuesta:** extraer `mod_list.py` + `run_paths.py` compartidos.
- **P-ARC-06 [MEDIA] Triple sellado/bundle/registry.** `tools/build_native_launcher.py` (1.064 l) vs `tools/dayz_mcp/launcher_registry_update.py` (753 l) vs `native_bundle.py` (772 l) vs `launcher_registry.py` (399 l). Tres lecturas `open(rb)+sha256+verify` casi idénticas (`launcher_registry.py:183,201,214,267`, `launcher_registry_update.py:81,141`).
  **Propuesta:** `bundle_io.py` único (`hash_file/verify_bundle/open_launcher`); `build_native_launcher` queda como CLI fino.
- **P-ARC-07 [MEDIA] Gates top-level que replican producto.** `tools/g0_abba_gate.py` (1.816 l), `g0_site_gate.py` (731 l), `gate4a_mcp_client.py` (489 l), `p0s_gate.py` (300 l), `tramoA/B_*` (72-171 l), `_session_coordination/h8_*_gate.py` (1.176-1.506 l): cada uno con su propio `session_acquire_wait`, `bridge_status`, `compute_ready`, parse de `netstat` (ej. `g0_abba_gate.py:801-844` vs `control_client.py:658-760`).
  **Propuesta:** mover a `tools/gates/` con `GateHarness(SessionClient)` común.

### 3.2 Mala optimización

- **P-OPT-01 [ALTA] Polling `sleep(0.05)` como norma.** Verificado: `server.py:78` (`POLL_INTERVAL_S=0.05`) usado en `:1034`, `:1668`; `control_client.py:548` (`sleep(0.05)`); `daemon.py:248-262,287-304,1500,1578`; `orphan_guard.py:567-574` (`wait_port_free` 3 s/0,05 s); `native_launcher_backend.py:1280-1286` (`take_announcement: sleep(0.001)` — busy-wait 1 ms); `steam_preflight.py:27,320,339` (0,2 s); `process_lifecycle.py:2820` (`sleep` bajo lock).
  **Propuesta:** `wait_utils.py: await_event_or_poll(pred,interval,deadline)` con intervalos adaptativos (0,05→0,5→1,0) + `POLL_FAST/POLL_SLOW`; `take_announcement` con `threading.Event`.
- **P-OPT-02 [ALTA] `subprocess netstat` en caliente.** Verificado: `orphan_guard.py:378` (TCP, timeout 10), `:471` (UDP, timeout 3); consumidores en `process_lifecycle.py:3882`, `server.py:3091,3127` (cada `BOX_WAIT_POLL_S=1.0 s`), `doctor.py:155,170`. Un `wait_for_box(600 s)` → ~600 `session_box_status` → cientos de spawns `netstat.exe`.
  **Propuesta:** `PortSnapshotCache(ttl=1.5 s)` compartido (ya existe `_BOX_OCCUPANCY_CACHE_S=1.5` en `process_lifecycle.py:45` pero solo para box); preferir `psutil.net_connections` 1 vez por tick; `netstat` solo fallback con caché.
- **P-OPT-03 [ALTA] Lecturas de log completas por poll.** `server.py:2716-2792` (`while deadline` → `:2764-2765` `probe_paths + _new_log_lines()` por fichero e iteración); `:2387-2395` (`_marker_rewound` abre `open(rb)+fstat+read(256KB)` por fichero); `:2450-2499` (`_scan_log_for_pattern`, `max_bytes=64MB, chunk=1MB` en `:148-149`, `carry+=chunk` en `:2479` → O(n²) para logs de 130k líneas; el comentario `:137-143` admite 132k/165k líneas). `log_tail.py:19` (`MAX_TAIL_BYTES=256KB`, verificado) bien, pero `read_since(:158-180)` hace seek+read+crc32-512B 2× por llamada.
  **Propuesta:** un `LogFollower` único por run (tail incremental, publica a suscriptores `wait_for`); `lookback_from="launch"` indexa una vez.
- **P-OPT-04 [MEDIA] Reintentos sin backoff.** `server.py:1287-1300` (1 retry inmediato), `orphan_guard.py:774-786,1039-1056` (`sleep(0.5)` fijo), `process_lifecycle.py:2800-2820` (intervalo fijo), `daemon_credential.py:186-240`, `steam_preflight.py:366`. Grep `backoff` solo en comentario (`orphan_guard.py:769`).
  **Propuesta:** `retry.py: async_retry(attempts, base=0.1, cap=5.0, jitter)` en los 5 sitios + `Retry-After` de `session_coordination.py:2797-2816`.
- **P-OPT-05 [MEDIA] Timeouts dispersos sin `timeouts.py`.** Muestra: `server.py:70-84` (300/1,0/0,05/600/1,0/30/0,5), `daemon.py:1515` (1,0), `doctor.py:158,173` (15/10), `orphan_guard.py:380,473` (10/3), `native_process_guard.py:217` (5,0), `process_lifecycle.py:2696` (5,0), `loopback.py:2958` (35,0), `ui_dialog.py:30` (60,0), `lease_supervisor.py:12-13` (45/120), `session_coordination.py:15-49` (120/600/30/0,05), `steam_preflight.py:27` (0,2), `mcp_capture.py:62-63` (0,12).
  **Propuesta:** `timeouts.py` central + override por env en tests.
- **P-OPT-06 [MEDIA, POTENTIAL] `mcp_capture.py` subprocess + PIL por captura** (`:9` subprocess, `:88` `GRAB_SCRIPT=mcp-grab.ps1`, `:18-83` budgets JPEG/PNG con `LANCZOS`, `DEFAULT_FRAME_COUNT=4`, `FRAME_INTERVAL=0.12`). Si las capturas son frecuentes en gates, cachear geometría y reutilizar el proceso PowerShell.
  **Confirmar:** medir frecuencia antes de refactorizar.

### 3.3 Procesos no armonizados (Python)

- **P-TICK-01 [ALTA] 7 loops que deberían ser 1.** parent-death watchdog (hilo bloqueante, `orphan_guard.py:588-601,604-657`), idle watchdog (hilo, `:1165-1199`), lease heartbeat (asyncio, `lease_supervisor.py:103-129`, 45 s), box-claim heartbeat (asyncio infinita, `server.py:3044-3063`, 30 s), run reaper (`daemon.py:1588-1589`), `wait_for/wait_for_box/ui_dialog` pollers (`server.py:2716,3089,2850`, 0,05-1,0 s), health-probe por llamada (`server.py:1380-1448`, `daemon_credential.py:54-`). Además `control_client.py:636-656` y `lease_supervisor.py:185-213` duplican `shield+delayed_cancellation`.
  **Propuesta:** `SupervisorTick` (§6.2): 1 `asyncio.Task` en daemon + 1 en cliente, cada 1 s: `check_lease/check_box/check_idle/check_reaper`. Solo parent-death queda event-driven. Elimina 3 hilos + 2 tasks por sesión.
- **P-TICK-02 [ALTA] Triple reclaim/kill.** `orphan_guard.py:664-850` (`try_reclaim_port`) vs `:971-1136` (`try_reclaim_unresponsive_listener`, 2 snapshots idénticos) vs `process_lifecycle.py:2741-2779` (`_classify_registered_process/_partition`) + `native_process_guard.py:194-301` (`terminate/discover`). `tools/process-guard.ps1:1-139` reimplementa `Get-IdentityFromProcess` — cuarto port de `native_process_guard.py:70-93` (`identity_hashes`).
  **Propuesta:** `ProcessAuthority`: `snapshot(pid)→Identity / classify→owned|gone|foreign|unknown / terminate(record)`; el `.ps1` llama al `.py`, no reimplementa. `try_reclaim_*` → `reclaim(port, policy={ancestry|health})`.
- **P-TICK-03 [MEDIA] Retail/box probes duplicados.** `orphan_guard.py:288` (`snapshot_retail_processes`) + `:389-398` (`_DAYZ_IMAGE_NAMES`) vs `process_lifecycle.py:49-51` (mismo set, distinto orden) vs `doctor.py:1178-1189`; cableado en `daemon.py:526,597`, `loopback.py:887,2757`, `process_lifecycle.py:1153,1634-1637`. UDP: `orphan_guard.py:482` vs `process_lifecycle.py:3882` (filtro DayZ/banda 2302-3000).
  **Propuesta:** `host_inventory.py: snapshot_retail()+snapshot_udp()` con TTL compartido; 3 consumidores, 1 snapshot por tick.
- **P-TICK-04 [MEDIA] `session_status` enriquecido en 3 sitios.** `server.py:1343-1344` (crudo) vs `:1346-1365` (`session_box_status`) vs `:3147-3171` (`_session_status_blocked_on`) + `:2918` (`_box_from_status`) + `loopback.py:3318-3324` / `session_coordination.py:1843` (`box_wait_touch`). `lease_supervisor.py:152-182` revalida lo que `control_client.py:554-581` ya valida.
  **Propuesta:** `session_view.py: SessionStatus{owner,box,pending,...}+blocked_on()`; `session_box_status` = `session_status(box_wait=...)`; un solo `assert_idle(status)`.
- **P-TICK-05 [MEDIA] Identidad copiada + import circular.** `native_process_guard.py:183-192` vs `process_lifecycle.py:2730-2739` (`_identity_matches`) vs `:2708-2727` (`_record_from_snapshot`; reparseado en `orphan_guard.py:1113` con imports diferidos `:695-696,1005-1006` — acoplamiento circular `orphan_guard↔process_lifecycle`).
  **Propuesta:** `process_identity.py` (`ProcessRecord + matches/record_from_snapshot`); rompe el ciclo.

---

## 4. TESTS Y GATES — findings

### 4.1 Sobreingeniería (tests que testan tests)

- **T-ARC-01 [ALTA] Contrato que se auto-afirma.** `tools/tests/test_daemon_contract.py:73` (`assertIs(daemon.build_daemon_argv, contract.build_daemon_argv)`) + `:33-71` reconstruye el argv esperado copiando la implementación.
  **Propuesta:** eliminar `:73` y `:36-71`; 1 test conductual `build_daemon_argv(SimpleNamespace(port=2302)) == [...--port 2302...]`.
- **T-ARC-02 [ALTA] AST-gate sobre el propio árbol.** `test_daemon_contract.py:93-143` (`find_spec`, `:138 node.func.value.id=="daemon_contract"`, `:140 assertIn(...)`) verifica que `daemon.py` importe al contrato. Es lint, no comportamiento.
  **Propuesta:** eliminar; cubierto por `test_sources_are_statically_analysable.py:58-75`.
- **T-ARC-03 [MEDIA] Triple watchdog de docs.** `tools/tests/test_docs_truth.py:219-388` (659 l) + `tools/checks/check_readme_cites.py:1-50` (mismo regex `CITE_RE:19-21`) + `tools/tests/test_install_mcp.py:1264` (`test_readme_tool_count_matches_instantiated_app`).
  **Propuesta:** unificar en `checks/`; `test_docs_truth` invoca al check, no duplica regex.
- **T-ARC-04 [ALTA] Test que testea el check.** `tools/tests/test_launcher_registry_update.py:992-1029` (`_load_checker_module` + `_run_checker_against_fixture` con 6 `patch.object`); el check dice explícito `Lives outside tests/ on purpose` (`check_native_launcher_registry.py:27-32`). `test_secure_launcher.py:60` y `test_task9_launcher_migration.py:329` delegan en el mismo check por comentario.
  **Propuesta:** una sola suite `test_launcher_registry.py`; el check es el único oráculo.
- **T-ARC-05 [MEDIA] Meta-tests en el gate unit.** `test_sources_are_statically_analysable.py:44-56` (BOM), `test_no_literal_drive_letters.py:177`, `test_packaging_declarations.py:47`, `test_command_validation_coverage.py:1-10`, `test_dependency_lock.py:207`, `test_effective_schema*.py` (4 ficheros, 196+189+280+57 l).
  **Propuesta:** mover a `checks/` + pre-commit; fuera del gate unit.

### 4.2 Mala optimización (e2e / sleeps / timeouts)

- **T-OPT-01 [ALTA] E2E con proceso vivo convertible a unit.** `tools/tests/test_client_credential_rotation_e2e.py:198-199` (`Popen` daemon real) + `:544-613` (`exercise_live_mcp_process` con `stdio_client`, `ClientSession`, `read_timeout_seconds=10`, `asyncio.run`). `tools/tests/test_session_e2e.py:29-72` (`IntegrationDaemon` HTTP real + `serve_forever(poll_interval=0.01)` + `thread.join(timeout=2.0)`).
  **Propuesta:** unit con `_FakeRuntime` como `test_wait_for_launch_and_contract.py:55-80`; 1 solo `e2e` tras flag `DAYZ_MCP_LIVE=1`.
- **T-OPT-02 [ALTA] `venv` real por caso.** `tools/tests/test_bug046_startup_deadlock.py:1411-1415` (`python -m venv --without-pip`, `timeout=60.0`) + `:1443-1449` (`timeout=30.0`) + `:737,804,882,947,1030,1070,1131` (`Popen`).
  **Propuesta:** mockear intérprete o 1 venv por sesión; marcar `slow`; timeout 60→15.
- **T-OPT-03 [MEDIA] Timeouts largos y sleeps fijos.** `test_parent_watchdog.py:283,286,290` (`_await_json` 10/20 s), `test_mcp_capture.py:33` (20 s), `test_p0s_gate.py:234` (`TimeoutExpired(claude.exe, 30 s)`), `test_install_mcp.py:1215` (idem), `test_port_reclaim.py:596,649` (10 s), `test_parent_watchdog.py:151,176,204` (`sleep(0.05/0.2)`), `test_bug046_startup_deadlock.py:1060-1147` (9× `sleep(0.005-0.02)`).
  **Propuesta:** `wait_until(predicate)` existente (`test_task7_final_authority_regressions.py:22-28`); tope 2 s unit, resto a `slow`.
- **T-OPT-04 [MEDIA, POTENTIAL] Gates in-game sin flag live.** `g0_abba_gate.py` (1.816 l) + `g0_site_gate.py` (731 l) + `tramoA_verbs_gate.py` (72 l) + `tramoB_getin_gate.py` (79 l) + `test_g0_abba_verdict.py:18-50` (`sample()` sintético). Grep `skipUnless.*live` vacío — ningún driver declara si exige DayZ vivo.
  **Confirmar:** si requieren juego, envolver tras `DAYZ_MCP_LIVE` y dejar `test_g0_abba_verdict` como único gate unit.

### 4.3 Duplicación gate ↔ test ↔ check

- **T-DUP-01 [ALTA] `retail_quarantine` en 8+ suites.** `test_loopback.py:92-168` (5 casos), `test_process_lifecycle.py:1992-2025`, `test_retail_quarantine.py:148,190`, `test_server_response_truth.py:42-68` + `test_bad_args_messages.py:14-15` (misma `RETAIL_QUARANTINE_RECIPE` literal), `test_bug104_reap_under_quarantine.py:292-374`, `test_task7_review_regressions.py:571-745,1096-1127`, `test_task7_final_lifecycle_regressions.py:36-86`.
  **Propuesta:** receta canónica en `server.py`; un solo `test_retail_quarantine.py` conductual; resto se elimina o se vuelve tabla parametrizada que importa la receta.
- **T-DUP-02 [ALTA] `launcher_registry` en 7 sitios.** `test_launcher_registry_update.py` (962 l) + `test_secure_launcher.py:18-136` + `test_native_bundle.py:28-31` + `test_registry_lock.py:25-67` + `test_task9_launcher_migration.py:146-155` + `test_dependency_lock.py:16-24` + `check_native_launcher_registry.py:37-48`.
  **Propuesta:** fusionar en `test_launcher_registry.py` + 1 check; `test_registry_lock.py` (3 asserts) se pliega.
- **T-DUP-03 [ALTA] H8 duplicado y gigante.** `h8_distributed_codex_gate.py` (1.506 l) + `h8_real_codex_gate.py` (1.176 l) + `test_h8_distributed_gate.py:40-57` + `process_guard_gate.py` (351 l). Dos gates "codex" con roster/sha casi idénticos.
  **Propuesta:** un solo `h8_gate.py`; `h8_real` como thin-wrapper o eliminado.
- **T-DUP-04 [MEDIA] Task7: 4 ficheros encadenados (4.335 l).** `test_task7_final_authority_regressions.py:12-18` importa de `test_task7_review_regressions` + `test_task7_rereview_regressions:33`; `test_task7_final_lifecycle_regressions.py:9-10` idem (1.631+1.297+1.103+304 l). `test_authority_invariants_are_gated.py:32` importa de `test_session_coordination`.
  **Propuesta:** `tests/helpers_authority.py` (`Clock/IDENTITY/Sequence/AuditSink`); cada `test_task7_*` autocontenido; prohibir herencia entre tests.
- **T-DUP-05 [MEDIA] Cadena `wait_for` de 3 eslabones.** `test_wait_for.py` (696 l) ← `test_wait_for_launch_and_contract.py:38-43` ← `test_wait_for_requires_a_live_run.py:32-36`. Romper un helper intermedio rompe 2 suites.
  **Propuesta:** `tests/helpers_wait_for.py` (`_FakeRuntime/_live_process/_profiles/_live_run`); `test_wait_for_marker.py:19-20` lo consume sin importar tests entre sí.
- **T-DUP-06 [MEDIA] Acoplamiento test→test generalizado (67 casos).** Ej.: `test_box_occupancy.py:34-35,1914,1986,2011` ← `test_mcp_tools` + `test_process_lifecycle` + `test_client_mode._fixture_client_runtime`; `test_mcp_tools.py:199,270,332,382,702,796,859,927` reimporta `_fixture_client_runtime`; `test_ui_enforce_contract.py:16-19` importa `TELEMETRY_REGION_SHA256` de `test_vehicle_telemetry_contract`; `test_session_acquire_wait.py:16-17`, `test_wait_for.py:22-23`, `test_lote_v_products.py:176,246`.
  **Propuesta:** prohibir `from tests.test_* import`; fixtures a `tests/helpers_*.py` o `tests/fixtures/`.

### 4.4 Contratos no armonizados + fixtures

- **T-CON-01 [ALTA] 10 contratos, 4 mecanismos.** `test_daemon_contract.py:13-16` (import+`assertIs`) vs `test_messages_contract.py:22-60` (parser propio `_without_comments`) vs `test_vehicle_telemetry_contract.py:14-24` (`sha256="31d4012c..."` pineado + regex) vs `test_wait_for_launch_and_contract.py:46-80` (TempDir+logs reales). Sin tabla verbo→oráculo.
  **Propuesta:** `tests/contracts/` con harness único (oráculo declarado + mutación roja obligatoria vía `lote_harness/mutation_gate.py:38-53`); shas a `fixtures/`.
- **T-CON-02 [MEDIA] Triple fuente vehicle-trace.** `tools/schemas/vehicle-trace-v1.json` vs `tools/tests/fixtures/vehicle_trace/positive_20hz.json` + `negative_mutations.json` vs `tools/fixtures/vehicle-trace-civilian-sedan-control-v1.json` + `test_vehicle_trace.py:489` + `test_vehicle_trace_contract.py:191` + `test_vehicle_telemetry_contract.py:431` + `test_vehicle_prepare_fixture.py:146`.
  **Propuesta:** una sola fuente `schemas/` + generador de mutaciones; `vehicle_prepare_fixture` como factory, no suite.
- **T-CON-03 [MEDIA] Fixtures gigantes inline.** `test_tool_registry_fingerprint.py:5-8` (blobs JSON de KB en el `.py`), `test_client_mode.py` (1.448 l, `_fixture_client_runtime` consumido en 15+ suites), `test_box_occupancy.py` (2.059 l) + `test_process_lifecycle.py` (4.675 l) con dicts argv/`DayZDiag_x64.exe` repetidos (`test_box_occupancy.py:77-99,137-175`).
  **Propuesta:** blobs a `tests/fixtures/` comparados por sha; partir god-files por dominio.
- **T-CON-04 [BAJA] Basura `.bak` y logs en el árbol.** 26 `*.bak_pre_fencing_20260819` / `*.bak-20260907-*` + `tests/lote2_*.log`, `lote2_suite.log`, `_tmp_captures/`. Ruido para grep y `rglob("*.py")` (`test_sources_are_statically_analysable.py:36-40`).
  **Propuesta:** eliminar `.bak` (ya en git), mover logs a `reports/` ignorado.
- **T-CON-05 [BAJA, POTENTIAL] `mutation_gate.py` infrautilizado.** `tools/lote_harness/mutation_gate.py:1-21` exige base verde + mutante rojo + restore por sha — justo lo que T-ARC-01/02 y T-CON-01 necesitan — pero ningún `test_*contract.py` lo invoca.
  **Propuesta:** 1 llamada `mutation_gate` por contrato en CI.

---

## 5. CONSOLIDADO POTENTIAL (todo lo dudoso, junto)

| ID | Dónde | Hipótesis | Cómo confirmarlo |
|----|-------|-----------|------------------|
| E-P01 | `MCPDialogController.c:453-457` + `gui/layouts/mcp_dialog.layout:26` | `Title` es `MultilineTextWidgetClass` pero se castea a `TextWidget`; si no hereda, `CacheWidgets` falla con `missing_widget:Title` y el diálogo nunca abre | 1 vuelo que abra el diálogo y mire `script.log` |
| E-P02 | `MCP_CarScript.c:3-145` (`MCPCarDrive` + `MCPVehicleTrace` todo `static`) | Un solo coche/trace por cliente; si el vehículo se borra externamente, `s_Car` queda colgando (`ClearState:572-598` solo desde `Clear/Abort`) | Borrar el vehículo del fixture en juego y pedir otro trace |
| E-P03 | `MCPClientBridge.c:512-521` (`PollContextUrl`, comentario admite que `GetRestContext` cachea por string y `reset()` sería no-op) | Todo `m_PollCtx/m_PollCallbackRefs/watchdog 30 s` podría ser código muerto si el contexto es compartido | Log de punteros de contexto en 2 polls seguidos |
| E-P04 | `MCPBridge.c:812-858` (`DispatchPlayerRespawn`: replica `InGameMenu.GameRespawn` + `SimulateDeath(true)` + `ShowDeadScreen` + `DestroyAllMenus/Continue/Close`) | El orden `RespawnPlayer` antes de `SimulateDeath` puede dejar doble jugador en algunas builds | 1 vuelo de respawn en cliente real |
| E-P05 | `MCPBridge.c:1697-1717` (`exec_enforce` → `ExecuteEnforceScript(expr)` arbitrario por REST loopback) | Por diseño, pero sin cap de longitud ni allowlist de `main_fn` | Decidir: cap `expr.Length()<4096` + log de hash, mantener loopback-check |
| E-P06 | UI recursiva sin cota de profundidad (`CountUiWidgetsNamed`, `CollectUiNodes`) | Árbol degenerado podría desbordar pila de script; mitigado por `limit` de nodos pero no de profundidad | Añadir `depth≤32` y listo (barato, hacer igual) |
| P-P01 | `tools/mcp_capture.py:18-83` | Subprocess + PIL por captura solo duele si las capturas son frecuentes | Contar capturas/día en gates; si >50/día, cachear geometría |
| T-P01 | Gates `g0_*/tramo*` sin `skipUnless(live)` | No se sabe qué gates exigen DayZ vivo | `grep -r DAYZ_MCP_LIVE tools/` + 1 pasada en máquina sin juego |
| T-P02 | `mutation_gate.py` | Infrautilizado pero quizá sea intencionado (lentitud en CI) | Medir 1 contrato cableado y su coste |

---

## 6. PROPUESTA DE UNIFICACIÓN (el plan mínimo que lo arregla)

### 6.1 Enforce: `MCP_TickHub` + `MCPBridgeBase` + `MCPDriveProbe` + `MCPWorldQuery`

```
MissionServer::OnUpdate(dt) ──┐
                              ├──► MCP_TickHub.Tick(dt, bridge) : Jobs → Pending → Poll
MissionGameplay::OnUpdate(dt) ┘         ▲                    ▲
                                        │                    │
                          MCPBridge ────┘      MCPClientBridge┘
                          (hereda MCPBridgeBase: TryInit/Poll/Encode/Version/
                           Drain/Queue/Post/Callbacks/IsFiniteFloat/Shutdown)
                          (usa MCPDriveProbe de 5 fases + MCPWorldQuery::FindAt
                           + MCPJobRunner + MCPInputLock refcount)
CarScript::OnInput — FUERA del hub (física), con early-out si MCP inactivo.
```

Pasos: (1) crear `MCPBridgeBase.c` + `MCPHttpUtil` y hacer heredar a ambos bridges (~400 l menos: E-DUP-01/02/03); (2) crear `MCPDriveProbe.c` + `MCPWorldQuery` (E-DUP-03/04); (3) servidor adopta `MCPJobRunner` (E-TICK-03), diálogo pierde su reloj (E-TICK-04); (4) guards baratos: `pollHz clamp` (E-ERR-05), `duplicate_id` (E-BUF-03), `unknown_job_kind` fail-fast (E-ERR-03), `result dropped` log (E-ERR-01), 1 poll lento con backlog (E-BUF-02); (5) hot path: early-out `OnInput` (E-TICK-02), `ToLower` en contactos (E-HOT-07), caps pre-codificado (E-HOT-01), `GameBase` local (E-HOT-02), borrar 4 logs de spawn (E-HOT-11), borrar pool de 64 (E-BUF-04).

### 6.2 Python: `SupervisorTick` + `HostInventory` + `SessionClient` + `ProcessAuthority`

```
┌─ SupervisorTick (1 asyncio.Task por proceso, TICK=1 s) ─────────────┐
│ check_lease (45 s) · check_box (30 s) · check_idle · check_reaper   │
│ + wait_for/wait_for_box/ui_dialog suscritos (no pollers propios)    │
│ + PortSnapshotCache TTL 1,5 s + LogFollower incremental             │
└─────────────────────────────────────────────────────────────────────┘
SessionClient único (control_client.py) · ProcessAuthority (snapshot/
classify/terminate) · DaemonAuthority único · bundle_io único ·
retry.py + timeouts.py centrales · GateHarness común en tools/gates/
```

Orden: **P0** tick+snapshot (`supervisor_tick.py` + `host_inventory.py` — mata P-OPT-01/02, P-TICK-01/03; medible en `netstat`/s y CPU idle) y autoridad sesión/proceso (`session_client.py` + `process_identity.py` — mata P-ARC-01, P-TICK-02/04/05). **P1** colapsar launchers/daemon-authority (P-ARC-02/03/06) y `LogFollower`+`retry/timeouts` (P-OPT-03/04/05). **P2** archivar gates a `tools/gates/` con harness común (P-ARC-07, T-DUP-03).

### 6.3 Tests: 8 movimientos, ~30% menos sin perder señal

1. `tests/helpers_{wait_for,authority,client_runtime}.py`, prohibir `from tests.test_* import` (T-DUP-04/05/06). 2. Fusionar quarantine/registry/docs (T-DUP-01/02, T-ARC-03). 3. `tests/contracts/` único + una fuente trace/telemetry (T-CON-01/02). 4. Etiquetar `slow`, quitar `venv`/Popen/stdio vivo del path unit (T-OPT-01/02/03). 5. Borrar 26 `.bak`, mover logs (T-CON-04). 6. Cablear `mutation_gate` por contrato (T-CON-05).

---

## 7. PROPUESTAS ADICIONALES (nuevas, no pedidas pero baratas)

1. **Métrica de ticks Enforce:** contador `m_TickCount/m_DispatchedPerTick_max` logueado cada 60 s en `OnTick` — hoy no se sabe si `MAX_DISPATCH_PER_TICK=4` se queda corto o sobra. Coste: 5 líneas.
2. **`depth≤32` en los 2 walks UI recursivos** (cierra E-P06 sin vuelo).
3. **`MCP_DEBUG` condicional global:** `#define MCP_DEBUG` en `MCPMessages.c`, todos los `Print/Log` de fase tras él — los logs tibios (E-HOT-11) dejan de costar en release.
4. **Contrato `pollHz` validado en Python también:** el daemon acepta hoy cualquier `pollHz` del JSON; validar `1≤pollHz≤10` al leer `dayz_mcp.json` (espejo de E-ERR-05).
5. **Contador de `netstat`/s en `doctor.py`:** exponer `netstat_spawns_total` para verificar P-OPT-02 antes/después con un número, no a ojo.
6. **`DAYZ_MCP_LIVE` obligatorio en todo test que haga `Popen`/`venv`/`stdio_client`:** grep de CI que falla si un `test_*.py` con `Popen` no tiene `skipUnless(DAYZ_MCP_LIVE)` (cierra T-P01 de forma estructural).
7. **Linter `no-test-imports-test`:** prohibir `from tests.test_` vía `test_sources_are_statically_analysable.py` (cierra T-DUP-06 de forma estructural).
8. **TTL único documentado:** tabla en `QUICKSTART.md` con los 6 TTL (`lease 45/120`, `box 30/600`, `session 120`, `wait_for_box 600`, `poll 0.05/0.5/1.0`) — hoy hay que leer 6 ficheros (P-OPT-05) para saber cuánto dura qué.

---

## 8. PUNTUACIÓN POR PARTES (sobre 10)

| Parte | Nota | Qué está bien | Qué está mal / mejoraría |
|-------|------|---------------|--------------------------|
| Bridge servidor (`MCPBridge.c`) | **6,5** | Dispatch por verbos claro, `MAX_DISPATCH_PER_TICK`, pending acotado, límites JSONL | 70% duplicado con cliente; `IsSpawnReady` por tick; `GetGame()` sin cachear; `Shutdown` frágil; `pollHz` sin cota |
| Bridge cliente (`MCPClientBridge.c`) | **6,0** | Usa `MCPJobRunner`, `UI_TREE_DEFAULT_LIMIT`, deadman en drive | Peor duplicación (poll+probe+callbacks copiados); `Suppress` vs `Lock` paralelos; `IsFiniteFloat` débil; `Shutdown` 140 l |
| `MCP_CarScript.c` + trace | **7,0** | Pool pre-alocado, 20 Hz default, `ClearState/Abort` existen | Sin early-out (corre por coche y frame); `IndexOf×3` en contactos; techo 8.192 muestras excesivo; estado `static` único (E-P02) |
| Diálogo/UI (`MCPDialogController.c` + layout) | **7,5** | `MAX_FIELDS=6` coherente con layout, `priority/visible` bien, reloj inyectable | Doble reloj; `Print` por probe; posible cast `Multiline→Text` (E-P01); DFS sin cota de profundidad |
| Mensajes/config (`MCPMessages.c`, `MCPJobRunner.c`, `MCPCallbacks.c`, `Mission*.c`, `config.cpp`) | **8,0** | Mínimos, claros, `config.cpp` byte-idéntico en ambas copias | `MCPJobRunner` solo lo usa el cliente; `Mission*.c` duplican `OnUpdate`; nada más |
| Daemon/server core (`server.py`, `daemon.py`, `loopback.py`, `runtime_state.py`) | **5,5** | Bien instrumentado (markers, lookback, box-claim), comentarios con mediciones reales (132k líneas) | God-files, `sleep(0.05)` sistémico, re-escaneo 64 MB O(n²), 5 fachadas `session_*`, timeouts dispersos |
| Sesión/procesos/lifecycle (`session_coordination.py`, `process_lifecycle.py`, `orphan_guard.py`, `native_*`, `lease_supervisor.py`) | **5,0** | Cobertura real (reclaim, quarantine, watchdog padre, identidad) | 7 loops, triple reclaim, `netstat` por llamada, identidad copiada + import circular, `.ps1` que reimplementa |
| Launcher/native/seguridad (`secure_launcher.py`, `native_launcher_*`, `launcher_registry*`, `daemon_*policy*`, `security_runtime_audit.py`) | **5,0** | Sellado+verificación+preflight existen y son serios | 7 capas, daemon-authority en 6 ficheros, triple bundle/registry, wrapper de 1 línea, `O(n²)` admitido |
| Tests/gates/contratos | **4,5** | Cobertura enorme, oráculos reales (sha, regex, TempDir), `mutation_gate` bien diseñado | 185 suites, god-files, 67 imports test→test, quarantine×8, registry×7, 4 mecanismos de contrato, e2e sin flag, `.bak` en árbol |
| Docs/handoff (`HANDOFF.md`, `GATES.md`, `gates/`, `QUICKSTART.md`) | **6,0** | Trazabilidad obsesiva, decisiones registradas, `LIVE-STATE` acotado | 3 capas de gates sin índice común (8 ROOT vs 195 en `gates/`), `PROJECT-MAP.md` a mano, TTL dispersos sin tabla |

**Nota global ponderada: 6,1/10.** Sólido y funcional, con deuda de crecimiento (duplicación + polling + tests) muy localizada y con plan de pago claro (§6). Nada exige reescribir; todo cabe en P0/P1/P2 por orden.

---

## 9. ANEXO — inventario verificado 2026-09-07

- `addon/scripts`: 9 `.c` (medidos por `Get-ChildItem`: `MCPBridge.c` 80.958 B … `MissionServer.c` 409 B).
- `tools/dayz_mcp`: 64 `.py` (conteo PowerShell). Top: `server.py` ~4.867 l, `process_lifecycle.py` ~4.130 l, `loopback.py` ~3.711 l, `session_coordination.py` ~3.561 l.
- `tools/*.py`: 17 ficheros.
- `tools/tests`: 185 `test_*.py` + 6 helpers + 26 `.bak_*`.
- Grep de verificación: `MAX_DISPATCH_PER_TICK=4` (`MCPBridge.c:3`, `MCPClientBridge.c:132`), `SPAWN_READY_RADIUS=8.0` (`MCPBridge.c:13`, uso `:2897`), `m_CallbackRefs` (`:54,78,232,3451,3480-3531` y espejo cliente), `EncodeQueryValue` (`:2072`/`:3832`), `pollHz>0` (`:195`/`:371`), `POLL_INTERVAL_S=0.05` (`server.py:78,1034,1668`), `netstat -ano` (`orphan_guard.py:378,471`, `doctor.py:171`), `MAX_TAIL_BYTES=256KB` (`log_tail.py:19`).
- Auditorías previas (`AUDITORIA_*.md`) no se re-auditan aquí; esta es la foto actual del árbol.

*Fin del informe técnico. La valoración de producto (§10) se añadió el mismo día tras la discusión posterior.*

---

## 10. PRODUCTO — el MCP instalado y operado por una IA (añadido tras la auditoría)

**Contexto que cambia el veredicto:** el consumidor no es el modder directamente, sino una IA a la que el modder delega instalación y operación. Lo que cuenta entonces no es la UI sino: instalador único idempotente, errores máquina-legibles con remedio, operaciones reintentables y contratos estables.

### 10.1 Veredicto de producto

**Útil y con buen encaje agente-herramienta, pero hoy no es instalable/operable de forma autónoma y fiable.** Los verbos (spawn, teleport, capture, trace, `ui_tree`) mapean bien a llamadas de agente y el loop REST-loopback + daemon + gates funciona. Pero un agente no puede depurar solo: setup repartido en 6 ficheros con TTL dispersos (P-OPT-05), fallos ambiguos (poll que muere con backlog E-BUF-02, `result dropped` silencioso E-ERR-01, respawn delicado E-P04), e2e sin flag `DAYZ_MCP_LIVE` (T-P01) y 7 capas de launcher (P-ARC-02). Sin cerrar eso no hay adopción fuera.

### 10.2 Findings de producto (con trazabilidad a la auditoría)

- **PRD-01 [ALTA] Sin `dry-run`: el único oráculo es lanzar el juego.** Carísimo para iterar un agente. Deriva de T-OPT-01/04 y P-OPT-03.
  **Propuesta:** `doctor --dry-run` que valide config, P:\, puertos, PBO y contratos sin DayZ vivo; puerta de entrada obligatoria antes de cualquier `dayz_test_run`.
- **PRD-02 [ALTA] Errores sin remedio máquina-legible.** Causa + comando exacto de remedio ausentes en `result dropped` (E-ERR-01), `timeout` genérico por `kind` desconocido (E-ERR-03), inanición de poll (E-BUF-02).
  **Propuesta:** todo error devuelve `{causa, remedio_comando, reintentable: bool}`; `unknown_job_kind` fail-fast ya propuesto en E-ERR-03.
- **PRD-03 [MEDIA] Sesiones no reanudables ni presupuestadas.** `lease` sin `owner/intent` visible, `blocked_on` en 3 sitios (P-TICK-04), sin cuotas (polls, `netstat`, tiempo). Un agente reintenta a ciegas hasta colgar el poll.
  **Propuesta:** `session_view.py` (P-TICK-04) + `SessionStatus{owner,box,pending,blocked_on,presupuesto}` + contador `netstat_spawns_total` en `doctor.py` (propuesta §7.5).
- **PRD-04 [MEDIA, POTENTIAL] Superficie inestable para construir encima.** Sin verbos versionados con schema pineado, ningún ecosistema de agentes puede fijar contratos.
  **Propuesta:** paquete mínimo versionado: 5 verbos estables (`spawn, teleport, capture, trace, ui_tree`) con schema + changelog; resto experimental. Confirmar con 1 consumidor externo antes de congelar.

### 10.3 Qué recortar para venderlo fuera ("difícil de vender fuera sin recortar" — aclaración)

Lo que se vende es el núcleo (PBO + daemon + 5 verbos + instalador único). Fuera del pack van, como andamiaje interno opcional: las 7 capas de launcher → 1 instalador (P-ARC-02/06); los gates gigantes (`g0_*`, `h8_*`, `tramo*`) → pack opcional (P-ARC-07, T-DUP-03); las 6 autoridades de daemon/sesión → 1 sola (P-ARC-01/03, P-TICK-04/05); los 185 tests → solo `doctor --fix` en verde de cara al comprador (T-DUP-01/02, T-ARC-05).

### 10.4 Propuestas de producto priorizadas

1. **`install_mcp` único + `doctor --fix` que deje verde sin preguntas** (cierra PRD-01/02; prerrequisito de todo lo demás).
2. **Sesiones reanudables y cuotas visibles** (PRD-03; barato tras P-TICK-04).
3. **Paquete mínimo versionado de 5 verbos** (PRD-04; congela superficie y permite ecosistema).
