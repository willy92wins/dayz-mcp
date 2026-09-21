# Plan — Fase 1 (Control): world_spawn / vehicle_enter / vehicle_drive

> Traza a `product-spec.md` grupo B (B1/B2/B3). Research consolidado:
> `AI/10_Projects/DayZ_MCP/research/2026-06-07-fase1-control.md` (gate discovery→generación satisfecho).
> **Grill Modo B (2026-06-07, confirmado con usuario):** B3 = **probe server-side en el 1er test**.
> Split: **1a** (world_spawn, server-only) → **1b** (vehicle). **Paso 0** prerequisito común.
> Etiquetas: `[EXACT]` = API verificada host-direct (path:line) · `[DESIGN]` = estructura nueva propuesta.

## Changelog del plan
- **v2 (2026-06-07, R22 aplicada):** R22-001 (FAIL) validación fail-closed de args antes de `CreateObjectEx`;
  R22-002 (WARN) backpressure local con `MAX_PENDING`+poll-gating; R22-003 (WARN) fixture del probe vía
  `OnDebugSpawn` + precondición `vehicle_fixture_ready`; R22-005 (NIT) citas a path relativo completo.
  **R22-004 (WARN, product-spec) — APLICADO 2026-06-07** (confirmado con usuario): B2 constante nombrada + B3 probe/diferido en el changelog del product-spec.
- v1: plan inicial (research consolidado + grill Modo B).

## Contexto verificado (host-direct, paths completos — R22-005)
- `[EXACT]` `CreateObjectEx(string, vector, int iFlags, int iRotation=RF_DEFAULT)` — `scripts/3_game/global/game.c:702`. `ConfigIsExisting(string)` — `scripts/3_game/global/game.c:611`. `GetPlayers(out array<Man>)` — `scripts/3_game/global/game.c:947`. `GetObjectsAtPosition3D(...)` — `scripts/3_game/global/game.c:929`.
- `[EXACT]` `ECE_PLACE_ON_SURFACE=1060` — `scripts/3_game/ce/centraleconomy.c:37`; `ECE_NONE=0` — `:7`; `RF_DEFAULT=512` — `:65`.
- `[EXACT]` `StartCommand_Vehicle(Transport,int,int,bool=false)` — `scripts/3_game/human.c:1492`; `GetCommand_Vehicle()` — `:1494`; `HumanCommandVehicle.IsGettingIn()` `:705` / `GetVehicleSeat()` `:696` (`scripts/3_game/human.c:689-734`).
- `[EXACT]` Seat flow: `Transport.CrewPositionIndex` `scripts/3_game/vehicles/transport.c:116` / `GetSeatAnimationType` `:475` / `GetAnimInstance` `:465`; `DayZPlayerConstants.VEHICLESEAT_DRIVER` `scripts/3_game/dayzplayer.c:674` (**valor ≠ 0**, usar la constante).
- `[EXACT]` Car: `EngineStart :244`/`EngineIsOn :241`/`SetThrottle :202`/`SetHandbrake :220`/`ShiftTo :271`/`GetSpeedometer :113`/`Fill :376`/`GetFluidCapacity :359` (`scripts/3_game/vehicles/car.c`); `CheckOperationalRequirements` (`scripts/4_world/entities/vehicles/carscript.c:1980-2016`).
- `[EXACT]` Fixture coche: `OnDebugSpawn()` (`scripts/4_world/entities/vehicles/inheritedcars/hatchback_02.c:387-402`) → `SpawnUniversalParts()` (`carscript.c:3121`, partes vitales) + `FillUpCarFluids()` (`carscript.c:3191`, fuel+coolant+oil) + 4 ruedas. (`SpawnUniversalParts`/`FillUpCarFluids` son `protected` → entrar por `OnDebugSpawn`.)
- `[EXACT]` **B3 client-auth**: `scripts/4_world/classes/useractionscomponent/actions/continuous/vehicles/actionstartengine.c:51-58` evita `EngineStart` en server bajo `NetworkMoveStrategy.PHYSICS`.
- `[EXACT]` Bridge: parse `JsonSerializer.ReadFromString(batch,...)` `DayZ_MCP/scripts/5_Mission/MCPBridge.c:168`; dispatch if/else `:252`; OnPollSuccess batch loop `:193-199`; DTO `MCPCommand{int id; string cmd;}` `DayZ_MCP/scripts/5_Mission/MCPMessages.c:8`; whitelist `DayZ_MCP_dev/tools/mcp_server.py:14`; args dict-check `:119-122`.

---

## Paso 0 — Prerequisitos (bloquean cualquier comando B)

### 0.1 — DTO con args (tipado, NO string crudo)
`[DESIGN]` El parse es `JsonSerializer` con campos tipados → un objeto JSON no entra en un `string`.
```
class MCPArgs {              // MCPMessages.c
    string type;             // world_spawn: clase de entidad
    ref array<float> pos;    // [x,y,z]  (mismo patrón que MCPPlayerState.pos)
    int flags;               // 0/ausente => default ECE_PLACE_ON_SURFACE (ver 0.5)
    int rotation;            // 0/ausente => default RF_DEFAULT
    int seat;                // vehicle_enter: crew index (0 = driver)
    float throttle;          // vehicle_drive probe
    float duration;
};
class MCPCommand { int id; string cmd; ref MCPArgs args; };  // + args
```
`JsonSerializer` mapea `{}`→MCPArgs con defaults; campos ausentes quedan default. Cada handler lee solo lo suyo.

### 0.2 — Whitelist (fail-closed, R6)
`[EXACT→edit]` `mcp_server.py:14`: `WHITELISTED_COMMANDS = {"query_player_state","world_spawn","vehicle_enter","vehicle_drive"}`. (Check 400 en `:115-116` ya existe.)

### 0.3 — Backpressure end-to-end (R22-002)
`[DESIGN]` Side-effects ya no son read-only → cerrar el backpressure local, no solo el per-tick:
- **Bridge dispatch cap:** en `OnPollSuccess` (`MCPBridge.c:193-199`) procesar máx `MAX_DISPATCH_PER_TICK` (=4); el resto a `m_Pending`, drenada en `OnTick`.
- **Capacidad local (nuevo, R22-002):** `MAX_PENDING` (=32). `OnTick`: **drenar `m_Pending` ANTES de considerar `StartPoll`**; **NO iniciar otro `/poll` mientras `m_Pending.Count() > UMBRAL_BAJO`** (=8); si `m_Pending` llega a `MAX_PENDING`, pausar intake. (Invariante: `m_Pending` acotado; el productor no puede hacerlo crecer alternando polls de 64 con drenaje.)
- **Server cap de cola:** `_handle_enqueue` (`mcp_server.py:109`): `len(queue) >= MAX_QUEUE` (=64) → `429 {"error":"queue_full"}`.
- `[P3, no bloquea]` TTL/evict de `results` (`mcp_server.py:23`) — backlog.

### 0.4 — Readiness diferido (pending-jobs) sin romper el camino síncrono
`[DESIGN]` `query_player_state` sigue síncrono. Para spawn/seat/drive:
```
class MCPJob { int id; string kind; ref MCPArgs args; Object subject; int deadline_tick; };  // [DESIGN]
// Dispatch diferido: valida (0.5) -> ejecuta side-effect -> crea MCPJob en m_Jobs, NO postea aún.
// OnTick: por job, IsReady(job)? -> PostResult(ok) y quitar; tick>deadline -> PostResult(timeout) y quitar (liberar subject ref).
```
`IsReady`: spawn → `GetObjectsAtPosition3D` casa `GetType()==type` **o** `subject!=null`; seat → predicado B2 (0.6 / 1b.1); drive(probe) → ver 1b.2.

### 0.5 — Validación fail-closed de args (R22-001, BLOQUEANTE) `[DESIGN]`
**Antes de cualquier side-effect de mundo**, un helper `ValidateSpawnArgs` que falla cerrado (devuelve error de negocio, NO exception, sin mutar mundo):
- `command.args != null` (si null → `error:"bad_args"`).
- `args.type != ""` **y** `GetGame().ConfigIsExisting("CfgVehicles " + args.type)` `[EXACT game.c:611]` (clase inexistente → `error:"unknown_type"`).
- `args.pos != null && args.pos.Count()==3` (corta/ausente → `error:"bad_pos"`); cada coord **finita** (rechazar NaN/inf) y dentro de rango de mundo (p.ej. `0..GetWorldSize`); conversión **guardada** `vector v = Vector(pos[0],pos[1],pos[2])` (NO `ArrayToVec`, que indexa sin guard — `scripts/1_core/proto/enconvert.c`).
- `flags`: `0`/ausente → default `ECE_PLACE_ON_SURFACE`; si no-cero, validar contra **allowlist/mask** de bits ECE permitidos (rechazar fuera de mask → `error:"bad_flags"`). (Resuelve la ambigüedad "ausente vs 0": 0 = default, nunca `ECE_NONE` literal.)
- `rotation`: `0`/ausente → `RF_DEFAULT`.
- `CreateObjectEx` debe devolver objeto **no-null** antes de crear el job (null → `error:"spawn_failed"`).
- **Fixtures negativos (R26):** `type` inexistente, `pos` ausente, `pos` corta, `args` no objeto, `flags` fuera de allowlist → **error sin spawn** (verificar 0 entidades creadas).

---

## 1a — world_spawn (server-only)
`[DESIGN]` handler en `Dispatch` (`else if (command.cmd=="world_spawn")` tras `MCPBridge.c:265`):
1. **`ValidateSpawnArgs(command.args)` (0.5) — si falla, PostResult(error) y return (sin spawn).**
2. `Object o = GetGame().CreateObjectEx(args.type, v, flags, rotation);` `[EXACT game.c:702]`; si null → `error:"spawn_failed"`.
3. `MCPJob{kind:"spawn", subject:o, pos:v}`; readiness `GetObjectsAtPosition3D` `[EXACT game.c:929]`.
4. Result ok → `{type, pos_real: o.GetPosition(), found:true}`.

**Determinismo (opcional):** `SetVelocity(o,"0 0 0")` `[EXACT enphysics.c:111]` + `dBodyActive(o, ActiveState.INACTIVE)` `[EXACT enphysics.c:64]` si desliza.

**Aceptación B1 (R26):** post-spawn, `GetObjectsAtPosition3D(pos,2.0)` contiene Object con `GetType()==args.type`; ok < timeout. Fixture NO derivado de la lectura del bridge (LL-115). + fixtures negativos de 0.5.

## 1b.1 — vehicle_enter (B2, server-side)
`[DESIGN]`:
1. `GetGame().GetPlayers(m_Players)` `[EXACT game.c:947]`; `Human h = Human.Cast(m_Players[0])`.
2. Resolver vehículo (por id de spawn previo o `GetObjectsAtPosition3D`).
3. `int seatAnim = veh.GetSeatAnimationType(0);` `[EXACT transport.c:475]`
4. `HumanCommandVehicle cmd = h.StartCommand_Vehicle(veh, 0, seatAnim);` `[EXACT human.c:1492]`; `cmd.SetVehicleType(veh.GetAnimInstance());` `[EXACT transport.c:465]`.
5. `MCPJob{kind:"seat"}`; readiness = predicado del CONFLICT-2.

**Aceptación B2 (R26):** `GetCommand_Vehicle()!=null && !IsGettingIn() && GetVehicleSeat()==DayZPlayerConstants.VEHICLESEAT_DRIVER` (constante nombrada, NO `==0`) < timeout (poll, NO N ticks fijos).

## 1b.2 — vehicle_drive PROBE (B3, server-side, INSTRUMENTADO test-only)
> Instrumentación de diagnóstico (autorizada por la elección "probe en el 1er test"), NO la tool final.
> Anchor: `// MCP-PROBE B3 server-side — DECISION DATA, no es la tool final`. Revert/condicionar antes de cerrar B3.

`[DESIGN]` fixture COMPLETO (R22-003) + probe:
1. Spawn de clase de coche **pineada** `[verify clase: p.ej. "Hatchback_02"]`. **`veh.OnDebugSpawn();`** `[EXACT hatchback_02.c:387 → SpawnUniversalParts/FillUpCarFluids + 4 ruedas]` para dejarlo mecánicamente operativo (ruedas + partes vitales + fuel/coolant/oil). `[verify OnDebugSpawn callable server-side / no diag-gated]`. Fallback si no: crear ruedas + partes + fluidos con APIs públicas verificadas.
2. **Precondición `vehicle_fixture_ready` (R22-003):** loggear que el coche está listo (4 ruedas presentes, partes vitales OK) **ANTES** de interpretar movimiento. Sin esto, `speedo=0` no es interpretable.
3. Server-side: `veh.EngineStart()` → medir `veh.EngineIsOn()` server-side (1ª señal). `SetHandbrake(0); ShiftTo(CarGear.FIRST); SetThrottle(1.0);`. Sobre N ticks: `GetSpeedometer()` + delta `GetPosition()` + `GetNetworkMoveStrategy()`.

**Salida del probe (DATO, no PASS de producto):** `{vehicle_fixture_ready, engine_on_server, speedo_max, pos_delta, net_strategy}`. Interpretación SOLO si `vehicle_fixture_ready==true`: `engine_on_server==false || speedo_max≈0` con net_strategy=PHYSICS ⇒ client-auth ⇒ B3 al client peer o diferir (decisión post-test con usuario).

---

## Test in-game agrupado (R5) — un solo build
**Rebuild:** `@DayZ_MCP` (PBO `-packonly`). **Escenarios en una sesión:**
1. `world_spawn` (pos conocida) → existencia (B1) + **casos negativos de 0.5** (type malo, pos corta → error sin spawn).
2. `vehicle_enter` → predicado de asiento (B2).
3. `vehicle_drive` PROBE → `vehicle_fixture_ready` primero, luego `{engine_on_server, speedo, pos_delta, net_strategy}`.
4. **Backpressure:** encolar > MAX_QUEUE → 429; ráfaga grande → ≤ MAX_DISPATCH_PER_TICK/tick y `m_Pending` acotado.

**Logs/datos:** RPT + `script_*.log`; verdict Python con los resultados + bloque DATO del probe; coords player y coche.

## Fuera de alcance / diferido
- **Tool final de B3** (post-probe). **Client peer** (solo si el probe lo fuerza). TTL de results (P3). Multi-instancia.

## Criterios de aceptación (R26)
| Pieza | Criterio verificable |
|---|---|
| 0.5 validación | type inexistente / pos ausente / pos corta / args no-objeto / flags fuera de allowlist → error sin spawn (0 entidades) |
| 0.1 args | `world_spawn` con type/pos llega con valores intactos al handler (log del valor parseado) |
| 0.2 whitelist | comando no-whitelisted → 400; tras añadir → aceptado |
| 0.3 backpressure | > MAX_QUEUE → 429; ráfaga → ≤ MAX_DISPATCH_PER_TICK/tick; `m_Pending` no crece sin límite (fixture de saturación) |
| B1 | `GetObjectsAtPosition3D` casa el tipo; ok < timeout |
| B2 | predicado con constante nombrada true < timeout |
| B3 probe | `vehicle_fixture_ready` true; DATO {engine_on_server, speedo, net_strategy} recolectado — decide diseño, NO PASS |

## Riesgos
- `[UNKNOWN]` B3 server-side (lo resuelve el probe, ahora con fixture no contaminable).
- `[ASSUMPTION]` `PlayerBase→Human` cast (compile); `GetSpeedometer` km/h; `OnDebugSpawn` callable server-side `[verify]`.
- `[RESUELTO R22-004]` product-spec sincronizado (B2 constante nombrada, B3 probe/diferido) — changelog 2026-06-07.
- Instrumentación del probe: test-only, anchor + revert declarados.
