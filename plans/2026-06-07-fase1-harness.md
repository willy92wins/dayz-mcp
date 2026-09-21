# Plan — Harness de test in-game fase 1 (R5, run agrupado)

> Traza a `plans/2026-06-07-fase1-control.md` §"Test in-game agrupado (R5)" y a
> `product-spec.md` grupo B (B1/B2/B3). Describe **la infraestructura de test** (no el bridge, ya R21-clean + X.5).
> Etiquetas: `[EXACT]` = verificado host-direct contra el código real (path:line) · `[DESIGN]` = estructura nueva.
> Scope confirmado con usuario 2026-06-07: **plan-review separado** (R22) + **run COMPLETO** (B1+negativos+B2+B3-probe+backpressure en un build/launch).

## Changelog del plan
- **v2 (2026-06-07, R22-harness aplicada — veredicto Codex "approve with minor changes", 0 FAIL/4 WARN, todos verificados host-direct por Claude):**
  - HR22-001 — negativos B1 completados: `bad_flags` ahora **obligatorio**; añadido negativo **HTTP-layer** `args` no-dict → 400 `bad_args` (cubre "args no-objeto" del plan padre que el bridge nunca ve).
  - HR22-002 — `enqueue_cmd_status` **S4-safe**: captura el 429/400 como HTTPError (con `request_json`/`enqueue` actuales explotaría).
  - HR22-003 — CLI retrocompat: invocación **sin subcomando = POC A1-A5 intacto**; `phase1` opt-in (`--mode`, default poc); no tocar run-poc.ps1.
  - HR22-004 — `selected_car_class`: comparar `result.type` contra el intento que dio `ok`, no contra el primario; guardar en el verdict.
  - `[verify]` de scope **cerrado**: clases de la fallback list son `scope=2` (Hatchback_02 verificado host-direct config.cpp:9463-9465).
- v1: plan inicial (contrato verificado + scope completo R5).

## Objetivo

Un solo run in-game (R5) que: (1) verifica B1/B2 como PASS, (2) recolecta el **DATO** del probe B3
que decide su autoría (server/client/diferir), (3) verifica la deuda heredada de backpressure
(P2-3/P2-4) en el mismo build. El gate `overall_pass` cubre **B1, negativos, B2 y backpressure**;
**B3 NO entra en el gate** — es DATO de decisión, no criterio de producto (LL-116; product-spec B3=⏳probe).

## Contrato productor↔consumidor (verificado host-direct, R2)

### Args de entrada (`MCPArgs`, `[EXACT]` DayZ_MCP/scripts/5_Mission/MCPMessages.c:8-22)
- `world_spawn`: `{type:string, pos:[x,y,z], flags?:int, rotation?:int}`.
- `vehicle_enter`: `{pos:[x,y,z]}` — la pos del **vehículo** (crew hardcodeado a 0, `[EXACT]` MCPBridge.c:433).
- `vehicle_drive`: `{throttle?:float 0..1, duration?:float 1..300 ticks}` — **sin pos**; usa el coche donde el player está sentado (`GetCommand_Vehicle().GetTransport()`, `[EXACT]` MCPBridge.c:504-507).

### Resultados (`MCPResult`, `[EXACT]` MCPMessages.c:52-71 + handlers)
| Cmd | Éxito (campos no-default) | path:line |
|---|---|---|
| `query_player_state` | `{ok:true, state:{name,pos[3]}}` | MCPBridge.c:329-341 |
| `world_spawn` (B1) | `{ok:true, type, found:true, pos_real[3]}` | PostJobSuccess MCPBridge.c:1116-1130 |
| `vehicle_enter` (B2) | `{ok:true, seated:true, seat:"driver"}` | PostSeatSuccess MCPBridge.c:1133-1144 |
| `vehicle_drive` (B3) | `{ok:true, vehicle_fixture_ready, engine_on_server, speedo_max, pos_delta, net_strategy}` | PostDriveProbeResult MCPBridge.c:1146-1161 |

Todos traen `{id, tick_poll_sent, tick_poll_callback, tick_dispatch}`. `net_strategy`: `0`=NONE, `1`=LATEST, `2`=PHYSICS, `-1`=desconocido (`[EXACT]` EncodeNetworkMoveStrategy MCPBridge.c:1082-1100).

### Errores de negocio (`ok:false, error:"..."`) y de HTTP alcanzables
- `world_spawn` (bridge, `ok:false`): `unknown_type` (type vacío/no en CfgVehicles, `[EXACT]` MCPBridge.c:551-563), `bad_pos` (pos ausente/≠3/no-finita/fuera de mundo, :565-579), `bad_flags` (flags no-cero fuera de allowlist, :581-587 + IsAllowedSpawnFlags :654-661 solo `ECE_PLACE_ON_SURFACE`), `spawn_failed` (CreateObjectEx null, :378-382).
- `vehicle_enter`: `bad_pos`, `no_players`, `no_vehicle`, `busy`, `seat_failed`, `timeout`.
- `vehicle_drive`: `bad_throttle`, `bad_duration`, `busy`, `not_seated`, `no_vehicle`, `bad_probe_phase`, `timeout`.
- **`bad_args` tiene dos caras** (HR22-001): (a) `command.args==null` AL BRIDGE **no es alcanzable** — `mcp_server.py:120` inyecta default `{}`; (b) un POST `/enqueue` con `args` **no-dict** lo rechaza el **SERVER con 400 `bad_args`** (`mcp_server.py:120-123`), antes del bridge. La cara (b) SÍ es testeable a nivel HTTP y cubre el "args no-objeto" del plan padre (S1-neg paso 9).

### Server (ya listo, NO tocar salvo necesidad declarada)
- Whitelist `{query_player_state, world_spawn, vehicle_enter, vehicle_drive}` `[EXACT]` mcp_server.py:14; no-whitelist→400 (:116-118).
- `MAX_QUEUE=64` → 429 `{"error":"queue_full"}` `[EXACT]` mcp_server.py:15,125-128.
- `args` no-dict → 400 `bad_args` (:120-123). `set_poll_delay` rango 0..5000ms (:201-215).

## Constantes del bridge que el harness explota (`[EXACT]` MCPBridge.c:3-10)
`MAX_DISPATCH_PER_TICK=4` · `MAX_PENDING=32` · `PENDING_POLL_THRESHOLD=8` · `JOB_TIMEOUT_TICKS=300` · `DRIVE_PROBE_TIMEOUT_TICKS=900` (~15s @60fps) · `DRIVE_PROBE_PREP_TIMEOUT_TICKS=300`.

---

## Arquitectura del harness `[DESIGN]`

Dos piezas nuevas + cero cambios al bridge probado:

### Pieza 1 — `tools/mcp_client.py` extendido con modo `phase1`
- **Retrocompat del CLI certificado (HR22-003, casi-FAIL): la invocación SIN subcomando conserva EXACTAMENTE el comportamiento POC actual.** `run-poc.ps1:674` invoca `mcp_client.py --port --keyfile --spawn --output --timeout` (posicional, sin subcomando) y DEBE seguir parseando sin cambios. Implementación segura: `--mode {poc,phase1}` con **default `poc`** (o subparsers con default→poc), `phase1` opt-in. **No se altera la lógica A1-A5 ni run-poc.ps1** (fase 0 certificada; R3). Validación mínima: la línea de invocación actual de run-poc.ps1 sigue funcionando.
- Reusa `Client` (request_json/raw_status/await_result/set_poll_delay) `[EXACT]` mcp_client.py:28-111.
- **Nuevos métodos** (el `enqueue` actual solo manda `args:{}`, :85-87):
  - `enqueue_cmd(cmd, args:dict)->int` — camino feliz (200 → id).
  - `enqueue_cmd_status(cmd, args)->{status,id,error}` **S4-safe (HR22-002)**: usa `request_json` para 200 y **captura `urllib.error.HTTPError`** (el 429 de mcp_server.py:125-128 y el 400 del paso 9 llegan como HTTPError; con `request_json`/`enqueue` actuales explotarían — :46-49). Reserva la excepción solo para HTTP inesperado; preserva los ids OK previos para el verdict.
- Escribe `tools/fase1-verdict.json` (archivo **nuevo**, NO pisa `poc-verdict.json` certificado).

### Pieza 2 — `tools/run-fase1.ps1` (clon enfocado del launch de fase 0)
- **Reusa el patrón de `run-poc.ps1`**: build PBO `-packonly` con marker `[MCP-POC]`, Python server, DayZDiag server+client, mission chernarus **completa** copiada + `init.c` con spawn fijo del player. `[EXACT]` run-poc.ps1:514-559,593-597,608-627.
- **NO incluye el bloque A5** (stop/restart del server) — irrelevante para fase 1.
- **NO usa `run-step0.ps1`** (su marker `[MCP-STEP0]` está stale: el bridge emite `[MCP-POC]`, MCPBridge.c:1343; y su mission es bare sin spawn). El launch a reusar es el de `run-poc.ps1`.
- **Reuso (recomendado): COPIAR** las funciones de launch dentro de `run-fase1.ps1` (Read-SharedText, New-*Token, Find-NewestFile, Resolve-AddonBuilderPath, Resolve-ServerMissionPath, Ensure-DayZWorkDrive, Invoke-DayZMcpPboBuild, writers de init.c/cfg/config). Duplicación aceptable para test-infra; **no factorizar a un common dot-source** porque tocaría `run-poc.ps1` probado (R3). *(Codex puede proponer factorizar si lo ve neto; decisión a adjudicar.)*
- Emite `GATE=PASS/FAIL` + vuelca `fase1-verdict.json`, RPT, script*.log y los markers `[MCP-POC]`.
- Param `-DayZPort 2402` por defecto (heads-up entorno: LFQuad puede relanzar en 2302).

---

## Secuencia del cliente `phase1` `[DESIGN]` (orden forzado por dependencias del bridge)

> B2 necesita el coche ya spawneado (FindTransportNear); B3 necesita al player ya sentado.
> ⟹ secuencial con `await_result` entre cada paso. NO ráfaga (salvo el bloque S4).

**S0 — ancla de escena (robusto al drift ~9 m del handoff fase 0):**
1. `query_player_state` → `state.pos` = `[px,py,pz]`. Si falla → abortar (sin player no hay escena).
2. Pos de spawn del coche = `[px + 5.0, py, pz]` (5 m al este; lejos para no telefragear, recuperable por radio de búsqueda al usar `pos_real`). **El coche se ancla al player actual, no a una constante** — neutraliza el drift.

**S1 — B1 world_spawn (PASS):**
3. `world_spawn {type:selected_car_class, pos:[px+5,py,pz]}`. Lista de intentos en orden: `["Hatchback_02","Sedan_02","Offroad_02","CivilianSedan","OffroadHatchback"]` (`[EXACT]` todas `extends CarScript`; **todas `scope=2`** en `DZ/vehicles/wheeled/config.cpp` — Hatchback_02 verificado host-direct :9463-9465). `selected_car_class` = el primer intento que devuelve `ok`; si `error∈{unknown_type,spawn_failed}` pasar al siguiente.
4. **Aceptación B1 (HR22-004):** `ok && found==true && result.type==selected_car_class` (comparar contra el intento seleccionado, NO el primario; el bridge devuelve `result.type=job.args.type`, MCPBridge.c:1116-1120). Guardar `car_pos_real = result.pos_real` (pos tras ECE_PLACE_ON_SURFACE; **usar esta, no la pedida**, para el enter). El verdict guarda `B1_world_spawn.car_class = selected_car_class`.

**S1-neg — casos negativos (PASS = error sin spawn):**
5. `world_spawn {type:"__NOPE__", pos:[px+5,py,pz]}` → `ok==false && error=="unknown_type"`.
6. `world_spawn {type:selected_car_class, pos:[1,2]}` (pos corta) → `ok==false && error=="bad_pos"`.
7. `world_spawn {type:selected_car_class}` (pos ausente) → `ok==false && error=="bad_pos"`.
8. **(HR22-001, obligatorio)** `world_spawn {type:selected_car_class, pos:[px+5,py,pz], flags:999}` → `ok==false && error=="bad_flags"` (bridge valida flags antes de CreateObjectEx, MCPBridge.c:581-587; solo `ECE_PLACE_ON_SURFACE`, :654-661).
9. **(HR22-001, negativo HTTP-layer)** POST `/enqueue {cmd:"world_spawn", args:<no-dict, p.ej. la cadena "x">}` vía `enqueue_cmd_status` → esperar **status HTTP 400, error=="bad_args"** (rechazado en el server, mcp_server.py:120-123; NO llega al bridge → SIN `/await`). Cubre el "args no-objeto" del plan padre (fase1-control.md:126).
- Verificación "0 entidades creadas": indirecta vía `ok==false`/400 + ausencia de `pos_real` (el cliente no enumera mundo).

**S2 — B2 vehicle_enter (PASS):**
10. `vehicle_enter {pos: car_pos_real}`. Timeout amplio (seat anim diferida). Aceptación B2: `ok && seated==true && seat=="driver"`.

**S3 — B3 vehicle_drive PROBE (recolecta DATO, NO PASS/FAIL):**
11. `vehicle_drive {throttle:1.0, duration:60}`. Timeout cliente **≥40 s** (prep+ignite+drive+60 ticks; DRIVE_PROBE_TIMEOUT_TICKS=900).
12. Recolectar `{vehicle_fixture_ready, engine_on_server, speedo_max, pos_delta, net_strategy}`.
13. **Interpretación (LL-116):** SOLO si `vehicle_fixture_ready==true`. `interpretation`:
    - `fixture_ready==false` → `"inconclusive: fixture not ready; do NOT read speedo"`.
    - `fixture_ready==true && net_strategy==2(PHYSICS) && (engine_on_server==false || speedo_max≈0)` → `"client-authoritative: B3 server-side no mueve; decidir client-peer/diferir"`.
    - `fixture_ready==true && (speedo_max>1 || pos_delta>0.5)` → `"server-authoritative: el coche se movió server-side"`.

**S4 — Backpressure (PASS, deuda P2-3/P2-4):** usa `enqueue_cmd_status` (HR22-002) en todos los sub-casos.
- **S4a 429 + S4c m_Pending acotado (combinados):**
  1. `set_poll_delay(5000)`; encolar 1 cebo `query_player_state` para que el siguiente `/poll` consuma el delay → el bridge entra en un poll de 5 s (`m_PollInFlight=true`, no drena).
  2. Esperar ~0.5 s; ráfaga de `enqueue_cmd_status("query_player_state",{})` hasta recibir **status 429**. Contar `ok_before_429`.
  3. Aceptación S4a: `queue_full_429_seen==true` cerca de `MAX_QUEUE=64`.
  4. Tras el sleep, el bridge recibe la cola (≤64), dispatcha 4/tick y mete ≤32 en `m_Pending`; el resto (>32 pendientes) recibe `error=="bridge_queue_full"` (`[EXACT]` MCPBridge.c:269-272). Aceptación S4c: ≥1 `bridge_queue_full` **sin crash** y el resto resuelve.
- **S4b dispatch cap ≤4/tick (verificable desde el DATO):** ráfaga de ~12 `query_player_state` (síncronos → `tick_dispatch` = tick real de proceso). Await todos. Agrupar por `tick_dispatch`; aceptación: ningún tick con >`MAX_DISPATCH_PER_TICK`(4).
- Heads-up timing: S4a depende de que la ventana del poll de 5 s solape la ráfaga; si el bridge poleó justo antes del cebo, reintentar la ventana 1-2 veces. (Robustez a endurecer en implementación.)

---

## `fase1-verdict.json` `[DESIGN]`
```
{
  "overall_pass": <B1 ∧ B1_neg ∧ B2 ∧ S4>,        // B3 NO entra (LL-116)
  "tests": {
    "B1_world_spawn":   {pass, car_class:selected_car_class, pos_requested, pos_real, found, error},
    "B1_negatives":     {pass, cases:[{name, expected, got, pass}]},   // incl. bad_flags + args-no-dict(400)
    "B2_vehicle_enter": {pass, seated, seat, error},
    "B3_drive_probe":   {probe_collected, vehicle_fixture_ready, engine_on_server,
                         speedo_max, pos_delta, net_strategy, interpretation},  // sin pass/fail
    "S4_backpressure":  {pass, queue_full_429_seen, ok_before_429,
                         bridge_queue_full_count, dispatch_cap_max_per_tick, dispatch_cap_ok}
  },
  "b3_decision_data": { ... copia destacada del DATO para análisis host-direct de Claude ... }
}
```
- `GATE=PASS` del `.ps1` = `clientExit==0 && overall_pass==true`. B3 se reporta aparte y NO afecta el gate.

## Criterios de aceptación (R26)
| Pieza | Criterio verificable | Cuenta en gate |
|---|---|---|
| B1 | `ok && found && result.type==selected_car_class`; `pos_real` presente | sí |
| B1-neg | unknown_type / bad_pos×2 / **bad_flags** (`ok==false` error exacto) + **args-no-dict → HTTP 400 bad_args** (HR22-001) | sí |
| B2 | `ok && seated && seat=="driver"` | sí |
| B3 | `probe_collected && (fixture_ready? interpretación válida : inconclusive)` | **NO** (DATO) |
| S4a | `queue_full_429_seen` cerca de MAX_QUEUE | sí |
| S4b | `max dispatches por tick_dispatch ≤ 4` | sí |
| S4c | ≥1 `bridge_queue_full` sin crash; resto resuelve | sí |

## Riesgos / `[verify]`
- `[verify in-game]` `OnDebugSpawn()` callable server-side (MCPBridge.c:963). Si no surte efecto → `vehicle_fixture_ready=false` → B3 **inconclusive** (no rompe el gate; deja B3 sin decidir → re-probar con fixture manual).
- `[RESUELTO HR22-004]` clases spawneables: `Hatchback_02 scope=2` verificado host-direct (`DZ/vehicles/wheeled/config.cpp:9463-9465`); fallback list scope=2 confirmada por Codex (Sedan_02:13929, Offroad_02:20831, CivilianSedan:5098, OffroadHatchback:1242).
- `[ASSUMPTION]` S4a timing (ventana de 5 s solapa la ráfaga) → reintentos + endurecer en implementación.
- `[verify]` clon de funciones de launch fiel a `run-poc.ps1` (sin drift de rutas/markers).

## Fuera de alcance
- Tool final de B3 (post-probe). Client-peer (solo si el DATO lo fuerza). TTL de results (P3). Tocar el bridge o la lógica A1-A5.
