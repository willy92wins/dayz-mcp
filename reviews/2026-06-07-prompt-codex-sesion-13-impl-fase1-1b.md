# Prompt Codex — sesión 13 · implementación fase 1: 1b (vehicle_enter + vehicle_drive PROBE)

> Patrón: implementation-handoff (post-R22, plan v2). Scope-bounded: SOLO 1b, sobre la infra de sesión 12.
> Generado 2026-06-07. Tras esto: R21 estructural de la fase, luego test in-game agrupado (R5).

```
===== PROMPT INICIO =====

Tarea: implementar 1b de la fase 1 (Control) de DayZ-MCP — el handler `vehicle_enter` (B2) y el
`vehicle_drive` PROBE (B3, instrumentación de decisión). Esta sesión cubre ÚNICAMENTE 1b, EXTENDIENDO
la infra ya implementada en sesión 12 (DTO MCPArgs, m_Jobs, ProcessJobs/IsJobReady, Dispatch,
backpressure). NO reescribas la infra de Paso 0. NO añadas otros comandos.

## Carga inicial obligatoria (leer antes de tocar nada)
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-07-fase1-control.md
   (PLAN v2 — §1b.1 vehicle_enter, §1b.2 vehicle_drive PROBE; vinculante).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c
   (infra de sesión 12 a EXTENDER: m_Jobs, ProcessJobs, IsJobReady, Dispatch, MCPSpawnValidation).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c
   (DTOs: MCPArgs ya tiene seat/throttle/duration; MCPResult/MCPJob a extender para el probe).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-harness-apis.md
   (firmas Enforce verificadas — NO inventes APIs).
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\CLAUDE.md
   (invariantes: async only, Enforce 1.29 SIN ternario ?:, fail-closed).
6. C:\Users\guill\ObsidianVault\AI\30_Sessions\2026-06-07-DayZ_MCP-sesion12-paso0-1a-codex.md
   (handoff sesión 12: infra reutilizable + deuda).

Source vanilla para verificar firmas: C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\ .
NO toques el transporte probado (poll/result/backoff/Shutdown). NO releas el research consolidado.

## Alcance acotado — 1b del plan v2

### 1b.1 — vehicle_enter handler (B2)  (MCPBridge.c, en Dispatch tras el branch world_spawn)
- `else if (command.cmd == "vehicle_enter")`:
  - `GetGame().GetPlayers(m_Players)`; `Human h = Human.Cast(m_Players.Get(0))`. Si no hay player → error "no_players".
  - Localizar el Transport: usar `command.args.pos` (validar como en world_spawn: pos.Count()==3) → `GetObjectsAtPosition3D(pos, R, m_ReadyObjects, m_ReadyProxyCargos)` y elegir el primer Object casteable a `Transport`. Si ninguno → error "no_vehicle".
  - `int crew = 0; int seatAnim = veh.GetSeatAnimationType(crew);` (transport.c:475); `HumanCommandVehicle cmd = h.StartCommand_Vehicle(veh, crew, seatAnim);` (human.c:1492); si cmd → `cmd.SetVehicleType(veh.GetAnimInstance());` (transport.c:465).
  - Crear `MCPJob{kind:"seat", subject:veh}`; `Dispatch` devuelve false (diferido).
- Readiness "seat" en IsJobReady (predicado CONFLICT-2, constante nombrada):
  `h.GetCommand_Vehicle() != null && !h.GetCommand_Vehicle().IsGettingIn() && h.GetCommand_Vehicle().GetVehicleSeat() == DayZPlayerConstants.VEHICLESEAT_DRIVER`.
  (Guarda el Human en el job o re-resuélvelo en IsJobReady vía GetPlayers.) Result ok → `{seated:true, seat:driver}`.

### 1b.2 — vehicle_drive PROBE (B3, INSTRUMENTACIÓN test-only)
> Anchor obligatorio en el código: `// MCP-PROBE B3 server-side — DECISION DATA, no es la tool final`.
> Es un diagnóstico, NO la tool de producto. Su salida es DATO, no un PASS.

`[DESIGN] máquina de estados` como `MCPJob{kind:"drive_probe"}` con un campo de fase + contadores de tick.
El handler `else if (command.cmd == "vehicle_drive")` crea el job; la lógica corre en ProcessJobs:
- **PREP**: `CarScript car = CarScript.Cast(h.GetCommand_Vehicle().GetTransport())` (el player DEBE estar sentado;
  si no → error "not_seated"). Si `car.WheelCountPresent() < car.WheelCount()` (car.c:352/349) → `car.OnDebugSpawn()`
  UNA vez (añade ruedas+fluidos+partes vitales; carscript OnDebugSpawn). `vehicle_fixture_ready = (WheelCountPresent()==WheelCount())`.
  NO avanzar de PREP hasta fixture_ready (con su propio sub-deadline).
- **IGNITE**: `car.EngineStart()` (car.c:244); registrar `engine_on_server = car.EngineIsOn()` (car.c:241) — server-side.
- **DRIVE**: `car.SetHandbrake(0)` (car.c:220); `car.ShiftTo(CarGear.FIRST)` (car.c:271); `car.SetThrottle(throttle)` (throttle = args.throttle si >0, si no 1.0).
- **SAMPLE**: durante N ticks (args.duration>0 ? como ticks : default ~60): registrar `speedo_max = max(GetSpeedometer())` (car.c:113) y `pos_delta` (|GetPosition()-posInicial|) y `net_strategy = car.GetNetworkMoveStrategy()` (pawn.c:218).
- **REPORT**: PostResult con DATO: `{vehicle_fixture_ready, engine_on_server, speedo_max, pos_delta, net_strategy}`.
  Si nunca fixture_ready → reportar `{vehicle_fixture_ready:false}` (el speedo NO es interpretable).

Extiende MCPResult/MCPJob con los campos que necesites (engine_on_server bool, speedo_max float, pos_delta float,
net_strategy int, vehicle_fixture_ready bool, phase int, sample_ticks int, start_pos).

## Tests / verificación (esta sesión)
- **NO hay test in-game en esta sesión** (el test agrupado R5 viene DESPUÉS de la R21 estructural). 
- **Requisito:** el bridge DEBE compilar (AddonBuilder -packonly Build Successful + smoke sin compile errors,
  como en sesión 12). Pega la evidencia de compilación.
- Los tests Python de sesión 12 deben seguir verdes (no rompas el server): `python -m unittest discover` desde tools\ → OK.

## Restricciones críticas (vinculantes)
1. **Async only**; **Enforce 1.29 SIN ternario `?:`** (usa if/else, como hizo sesión 12).
2. **NO reescribas la infra de Paso 0** (backpressure, validación, readiness map): solo AÑADE los kinds "seat" y
   "drive_probe" a IsJobReady/ProcessJobs y los branches a Dispatch. `query_player_state` y `world_spawn` intactos.
3. **El probe es instrumentación test-only** con su anchor; NO lo conviertas en la tool final de vehicle_drive
   (esa se decide tras ver el DATO in-game). TENTACIÓN A RESISTIR: "limpiar" el probe en una tool de producto.
4. **NO toques la deuda P3 conocida** (`MAX_QUEUE`=64 > `MAX_PENDING`=32 burst-drop): NO la arregles aquí; la R21
   decide. Solo NO la empeores.
5. **NO improvises fuera del plan**: si una firma no compila o un paso del plan no encaja, NO improvises — anótalo
   en Bloque C con la sección del plan + interpretación conservadora aplicada.
6. **NO self-review / R21 en esta sesión**: implementa, compila, y para.

## Output esperado al cerrar (bloques A/B/C/D)
### Bloque A — Archivos creados/modificados (paths absolutos + tamaño aprox).
### Bloque B — Evidencia de compilación (AddonBuilder + smoke) + output literal de `python -m unittest discover` (sesión 12 sigue verde).
### Bloque C — Hallazgos (firmas que no encajaron, decisiones del probe, "Sin hallazgos" si ninguno). En particular: cómo resolviste localizar el vehículo en vehicle_enter y la prep del coche en el probe.
### Bloque D — Handoff: estado fase 1 completa (Paso 0 + 1a + 1b), lista para R21 estructural + test in-game agrupado (R5). Deuda.

===== PROMPT FIN =====
```
