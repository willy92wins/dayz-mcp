# Plan Fase 5 — Drivability autónoma + diagnóstico get-in (MCP)

> **Estado**: v2.1 — **R22 round-1 + round-2 cerradas 2026-06-28; APPROVE WITH MINOR. La
> implementación de S0 puede empezar.** Round-1: NEEDS-WORK, 3 P1 + 6 P2 (F5-001..009), los 9
> aplicados. Round-2: approve with minor (8 closed + 1 partial), 3 doc-fixes R22bF5-001..003
> aplicados, 0 P1 abierto. Reviews: `reviews/2026-06-28-plan-review-fase5-codex.md` +
> `reviews/2026-06-28-plan-review-fase5-round2-codex.md`.
> F5-001 adjudicado con el usuario (AskUserQuestion): grupo **G** añadido al `product-spec.md`
> ANTES de S0 (gate DPF cerrado). El changelog de resoluciones está en §12.
> **Objetivo de negocio**: cerrar el bucle de test de **coches conducibles** vía MCP para que
> el agente itere un coche (rip Forza → conducible) **de principio a fin sin humano en el juego**.
> Lifecycle desatendido (host arranca/mantiene DayZDiag) y alcance (partir de un rip existente)
> adjudicados por el usuario (2026-06-28).
> **Principio de diseño (usuario)**: el MCP expone **verbos granulares y componibles**; la
> orquestación (escalera + máquina de estados + diagnose→fix) vive en la skill `dayz-mcp-verify`.
> **Implementa**: Codex (handoff scope-bounded post-R22). **Receptor + gates in-game**: Claude.

---

## 0. Contexto — qué ya existe (no se reescribe)

El bridge Enforce ya tiene el patrón modular pedido: dispatch else-if en
[`MCPBridge.c:338-388`](../../DayZ_MCP/scripts/5_Mission/MCPBridge.c) (server) y
[`MCPClientBridge.c:427-439`](../../DayZ_MCP/scripts/5_Mission/MCPClientBridge.c) (cliente).
Añadir un verbo = un `else if` + handler + entrada en la whitelist del loopback + tool MCP.

**El actuador de conducir está construido al ~80%, pero como PROBE de decisión, no como tool:**

| Pieza ya construida | Ubicación |
|---|---|
| `vehicle_enter` → seat (StartCommand_Vehicle, crew=0=driver), gate `VEHICLESEAT_DRIVER` | `MCPBridge.c:429-488`, `:1688` |
| `vehicle_drive` → job `drive_probe` PREP→IGNITE→DRIVE→SAMPLE→REPORT | `MCPBridge.c:490-559`, `:1733-1900` |
| Ignite `EngineStart()` + `EngineIsOn()` | `MCPBridge.c:1827-1828` |
| Drive `ShiftTo(CarGear.FIRST)` + `SetThrottle()` + suelta freno/handbrake | `MCPBridge.c:1844-1850` |
| Sample `GetSpeedometer()` máx + Δposición + `GetNetworkMoveStrategy()` | `MCPBridge.c:1884-1892` |
| Pre-acondicionado del coche (ruedas/fuel/batería/bujía + `OnDebugSpawn`) | `MCPBridge.c:1696-1731`, `:1795` |
| DTOs reutilizables: `MCPArgs.throttle/duration/seat`; `MCPResult.engine_on_server/speedo_max/pos_delta/net_strategy/seated` | `MCPMessages.c:19-21`, `:210-217` |

**El propio código se declara probe, no tool**: `PostDriveProbeResult` lleva el comentario
`// MCP-PROBE B3 server-side -- DECISION DATA, no es la tool final`
([`MCPBridge.c:1979`](../../DayZ_MCP/scripts/5_Mission/MCPBridge.c)). Por eso `vehicle_drive`
**no se expone como tool MCP** (decisión G-5 del plan Fase 4, `plans/2026-06-10-fase4-mcp.md:43`).

**La razón raíz, citada (no inferida):** `CarScript.IsServerOrOwner()`
([`carscript.c:3220-3231`](../../scripts/4_world/entities/vehicles/carscript.c)):

```c
// Cars that use the old networking only perform this part of the simulation on the server and not the clients
bool IsServerOrOwner()
{
    bool isServer = g_Game.IsServer();
    if (isServer || GetNetworkMoveStrategy() != NetworkMoveStrategy.PHYSICS)
        return isServer;
    return IsOwner();   // <-- networking nuevo (PHYSICS): la sim corre en el OWNER (cliente)
}
```

Con `NetworkMoveStrategy.PHYSICS` (networking moderno) el throttle solo tiene efecto en el
**owner = el cliente que conduce**. El `drive_probe` corre en el **server** → inerte para esos
coches. El probe ya captura `net_strategy` (`MCPBridge.c:1856/1892`, enum en
[`pawn.c:138`](../../scripts/3_game/entities/pawn.c), getter `:218`).

---

## 1. Objetivo y trazado a product-spec — CERRADO (F5-001)

Fase 5 traza al grupo **G (G0-G2)** del `product-spec.md`, **añadido y adjudicado el 2026-06-28
ANTES de implementar** (F5-001 resuelto). G0=spike S0; G1=verbos de control owner; G2=diagnóstico
get-in. La conducción que B3 difirió la retoma G. La escalera/orquestador NO son parte del MCP
(van a la skill, §6). El gate DPF queda cerrado: el plan ya no propone el criterio "al cierre".

---

## 2. Hipótesis central + Spike S0 (de-risk, GATE DURO antes de comprometer surface)

**Hipótesis**: portar la lógica del `drive_probe` al **peer cliente** (donde el player es el owner
de la física, `GetGame().GetPlayer()`) hace que el coche **se mueva** con coches
`NetworkMoveStrategy.PHYSICS`, a diferencia del server. **Es hipótesis, no certeza** (R22 A/B): no
hay evidencia en el bridge de que `vehicle_enter` —que corre en el SERVER— transfiera el ownership
al cliente MCP. S0 lo prueba conductualmente Y diagnósticamente.

**S0 — spike mínimo (no es surface final):** añadir UN cmd `drive_probe_client` al dispatch de
[`MCPClientBridge.c:427`](../../DayZ_MCP/scripts/5_Mission/MCPClientBridge.c), reusando la máquina
de fases de `MCPBridge.c` (copiar IGNITE→DRIVE→SAMPLE), resolviendo el coche por
`GetGame().GetPlayer().GetCommand_Vehicle().GetTransport()`. Secuencia: `vehicle_enter` (server,
sienta) → `drive_probe_client` (cliente, conduce) → leer resultado.

**Instrumentación de ownership en S0 (F5-006):** además de `pos_delta` y `net_strategy`, el probe
reporta **`IsOwner()`** ([`pawn.c:194`](../../scripts/3_game/entities/pawn.c)),
**`GetOwnerIdentity()`** ([`pawn.c:209`](../../scripts/3_game/entities/pawn.c)) y el **net id**
(`GetNetworkID(out low, out high)`, [`object.c:815`](../../scripts/3_game/entities/object.c)) del
coche desde el peer cliente. Esto convierte un fallo en diagnóstico: `pos_delta≈0` **con**
`IsOwner()==false` = el ownership no se transfirió al cliente MCP (causa identificada); `pos_delta≈0`
**con** `IsOwner()==true` = otra causa.

**Gate S0 (in-game, coche PHYSICS):**
- `pos_delta > 1.0 m` tras 2 s de throttle=1.0 (CivilianSedan) → **PASS**, seguir a Tramo A.
- `pos_delta ≈ 0` → **STOP**. NO construir Tramo A. El trío `IsOwner/GetOwnerIdentity/net id` dice
  por qué; replantear con ese dato (plan B = input vía `HumanInputController`, §8). Escalar.

> **Por qué S0 primero (R8/R5):** todo el Tramo A asume que owner-side `SetThrottle` mueve el
> coche. Esa es la **única incógnita no verificable offline**. Validarla aislada antes de construir
> 4 primitivas encima.

**Riesgo a vigilar en S0**: `MCPClientBridge` ya hace `SuppressGameplay()` →
`PlayerControlDisable(INPUT_EXCLUDE_ALL)` ([`MCPClientBridge.c:960-980`](../../DayZ_MCP/scripts/5_Mission/MCPClientBridge.c))
para la cámara. Para conducir puede que NO se deba suprimir todo el input (o que el HID pise el
`SetThrottle`). S0 prueba con y sin suppression y registra cuál mueve el coche.

---

## 3. Tramo A — primitivas de control en el peer cliente (modular) `[DESIGN]`

Solo si S0 = PASS. Cada verbo es un `else if` en el dispatch de `MCPClientBridge.c`, resolviendo
el coche owner por `GetGame().GetPlayer().GetCommand_Vehicle().GetTransport()` casteado a
`CarScript`. APIs `Car.*` verificadas `[EXACT]` en [`car.c`](../../scripts/3_game/vehicles/car.c):

| Verbo MCP | Args (MCPArgs) | Enforce `[EXACT]` | Result |
|---|---|---|---|
| `engine_set` | `mode` ("start"\|"stop") | `EngineStart()` `car.c:244` / `EngineStop()` `:247` + `EngineIsOn()` | `engine_on_server` (alias, §3.2) |
| `vehicle_control` | `throttle` 0..1, `steer` -1..1, `brake` 0..1, `handbrake` 0/1, `hold_ttl_s?` | `SetThrottle()` `car.c:202`, `SetSteering()` `:196`, `SetBrake()` `:214`, `SetHandbrake()` | estado leído + `hold_ttl_s` restante |
| `gear_shift` | `mode` ("to"\|"up"), `gear` (int) | `ShiftTo()` `car.c:271`, `ShiftUp()` `:268`, `GetGear()` `:259`, `GetGearCount()` `:265` | `gear` actual (campo nuevo, §3.2) |
| `vehicle_telemetry` | — | `GetSpeedometer()`, `GetGear()`, `EngineIsOn()`, `GetPosition()`, `GetNetworkMoveStrategy()` | `speedo_max`/`pos_delta`/`engine_on_server`/`net_strategy`/`gear` |
| `vehicle_release` (F5-003) | — | pone throttle/brake/steer a 0, desarma el held-state | `ok` |

### 3.1 Motor de control sostenido (held state) + identidad + deadman (F5-003, F5-004)

El MCP es request/response; conducir es continuo. El `drive_probe` reaplica `SetThrottle` **cada
tick de SAMPLE** (`MCPBridge.c:1871`), señal de que el control no persiste solo. Por tanto
`vehicle_control` fija un **estado de control retenido** en `MCPClientBridge` que `OnTick`
([`MCPClientBridge.c:158`](../../DayZ_MCP/scripts/5_Mission/MCPClientBridge.c)) reaplica cada frame.
`[DESIGN]`:
- Campos nuevos en `MCPClientBridge`: `m_DriveActive` (bool), `m_DriveNetIdLow/High` (int — **la
  IDENTIDAD del coche fijada al activar**, F5-004), `m_Throttle/m_Steer/m_Brake/m_Handbrake`
  (held), `m_HoldDeadlineS` (float — **TTL/deadman**, F5-003).
- En `OnTick`, si `m_DriveActive`: re-resolver el coche owner y **comparar su net id contra
  `m_DriveNetId*`**. Si NO coincide (el player cambió de vehículo) → **auto-clear** (no reaplicar a
  otro coche). Si el player no está en vehículo → auto-clear.
- **Deadman (F5-003)**: si `m_ElapsedS > m_HoldDeadlineS` → auto-release (throttle/brake a 0,
  desarma). `vehicle_control` **refresca** el deadline (`m_ElapsedS + HOLD_TTL_S`, default p.ej.
  3 s). Razón dura: con el broker, **el daemon sobrevive a su sesión (LL-156)** → una sesión MCP
  muerta a media conducción dejaría el coche acelerando solo. El deadman lo impide; `vehicle_release`
  es la liberación explícita.
- `vehicle_control` actualiza los held, fija net id si arma, refresca deadline; devuelve inmediato.
  La telemetría se lee con `vehicle_telemetry` (verbo aparte, modularidad).

### 3.2 Cambios de DTO `[DESIGN]` — rename CERRADO (F5-005)
- `MCPArgs`: añadir `steer`, `brake`, `handbrake`, `gear`, `hold_ttl_s`. Reutilizar `throttle`
  (`MCPMessages.c:20`). Los que distingan "no tocar" → `MCP_ARG_FLOAT_UNSET` (`:2`).
- `MCPResult`: añadir `gear` (int), `is_owner` (bool), `owner_identity` (string), `net_id_low/high`
  (int) para S0/telemetría. Reutilizar `engine_on_server/speedo_max/pos_delta/net_strategy`.
- **Rename `engine_on_server`→`engine_on`: NO en F5 (decisión cerrada).** Se mantiene el nombre
  heredado; en peer cliente significa "engine on (owner)" — se **documenta como alias** en el DTO y
  en el README. Un rename tocaría el server bridge y todos los call-sites (R7) → va a un **plan
  separado**, fuera de F5.

### 3.3 Decisión: 5 verbos modulares vs reusar `drive_probe`
Verbos separados (`engine_set`/`vehicle_control`/`gear_shift`/`vehicle_telemetry`/`vehicle_release`)
por el principio del usuario. El `drive_probe` monolítico server **se mantiene intacto** (DECISION
DATA, R7/G-4). El spike S0 (`drive_probe_client`) se **reescribe** como estos verbos una vez S0
valida la hipótesis (no se deja un camino muerto).

---

## 4. Tramo B — `query_get_in_condition` (server, diagnóstico) `[DESIGN, gates EXACT]`

**El verbo de mayor ROI.** Lectura **pura server-side** (sin owner-authority) que evalúa los 7
gates de `ActionGetInTransport.ActionCondition`
([`actiongetintransport.c:26-80`](../../scripts/4_world/classes/useractionscomponent/actions/interact/actiongetintransport.c))
y reporta **cuál falla**:

| # | Gate | API `[EXACT]` | Bug que detecta |
|---|---|---|---|
| 1 | target es Transport | cast | — |
| 2 | item en manos no Heavy | `IsHeavyBehaviour()` | — |
| 3 | player no ya en vehículo | `GetCommand_Vehicle()` | — |
| 4 | `CrewPositionIndex(componentIdx) >= 0` | `transport.c:116` | **componentNN ausente** (asientos isla) |
| 5 | asiento libre (`CrewMember(idx)` null) | `transport.c:124` | seat ocupado |
| 6 | `CrewCanGetThrough(idx)` && `IsAreaAtDoorFree(idx)` | `transport.c:493` / `:679` | **`class X: CarScript` pelado hereda false** |
| 7 | algún `CanReachSeatFromDoors(sel,...)` | `transport.c:511` + `GetActionComponentNameList` `object.c:198` | door/selection mal |

### 4.1 Contrato — `component` OBLIGATORIO para PASS (F5-002)
Vanilla decide el `crew_index` desde el **componente del cursor** (`int componentIndex =
target.GetComponentIndex(); int crew_index = trans.CrewPositionIndex(componentIndex)`,
[`actiongetintransport.c:50-51`](../../scripts/4_world/classes/useractionscomponent/actions/interact/actiongetintransport.c)).
Sin componente NO se puede reproducir el gate 4 fielmente → un `available==true` sin componente
sería un **verde-falso** (justo lo que la herramienta debe evitar). Por tanto:
- **Args**: `pos` (find transport near, reusa `FindTransportNear`) + `component` (int — índice de
  componente de un `scene_raycast` previo contra el coche).
- **Con `component`**: evalúa el camino EXACTO (gate 4 con ese componentIdx) → veredicto
  `available` + `first_block` **autoritativo** (puede ser PASS).
- **Sin `component`**: itera `crew_index` 0..`CrewSize()` (`transport.c:112`) y reporta los gates
  independientes de componente (5,6,7) por asiento → **diagnóstico PARCIAL, marcado
  `partial=true`, NUNCA un PASS**. Útil para acotar, no para certificar.
- **Result** (DTO nuevo `MCPGetInCondition`): `{available, partial, crew_size, component_crew_index,
  per_seat:[{crew_index, crew_can_get_through, area_free, occupied, reachable}], first_block}`.

### 4.2 Por qué NO simular el input/radial real
La fidelidad máxima (inyectar input → radial en UI) es frágil headless y no aporta sobre evaluar la
condición: el radial **es** esta condición. `vehicle_enter` (forzado, ya existe) cubre "sentarse y
proceder"; `query_get_in_condition` cubre "¿saldría el radial y por qué no?".

---

## 5. Seguridad / whitelist / concurrencia (hereda Fase 4)

- Cada cmd nuevo → **whitelist del loopback** (`dayz_mcp/loopback.py` / shim `mcp_server.py`);
  Codex cita `path:line` del punto de whitelist durante impl (convención Fase 4 §2).
- Cada tool nueva → `@mcp.tool` en `dayz_mcp/server.py` + mutex global E4 (los verbos de control
  mutan estado → toman el lock; `vehicle_telemetry`/`query_get_in_condition` read-only).
- **Fail-closed (R6) — rango Y NaN/Inf (F5-007)**: validar ANTES de aplicar `throttle` 0..1,
  `steer` -1..1, `brake` 0..1, `handbrake` 0/1, `gear` 0..`GetGearCount()`, **y finitud** (reusar
  `IsFiniteFloat`, [`MCPClientBridge.c:915`](../../DayZ_MCP/scripts/5_Mission/MCPClientBridge.c)).
  Fuera de rango o NaN/Inf → `ToolError`, sin tocar el coche. Cierra el patrón de BUG-011
  (radius/Inf no fail-closed). Espejo de la validación de `DispatchVehicleDriveProbe`
  (`MCPBridge.c:496-518`).
- **Peer correcto**: control + telemetría = peer **cliente** (owner); `query_get_in_condition` =
  peer **server**. La tool marca el peer como `camera_*` (client) vs `world_spawn` (server).
- **Broker D-14 (F5-003/F)**: los verbos de control son compatibles con el modo `--client` (igual
  que `camera_*`), PERO el held-state indefinido NO lo es sin el deadman/TTL del §3.1 — el deadman
  es lo que hace el control seguro bajo un daemon que sobrevive a la sesión (LL-156).

---

## 6. Orquestación — escalera de aceptación (skill `dayz-mcp-verify`, NO es MCP)

Fuera del entregable MCP, pero es el consumidor (contrato hacia adelante, R8-extendido). La skill
recorre una escalera ordenada; cada rung lee ground-truth in-game; cada fallo mapea a un fix
conocido de la taxonomía SUB_BRZ:

| Rung | Verbo(s) | PASS | Fallo → fix |
|---|---|---|---|
| R1 spawnea | `world_spawn` | entity existe | componentNN / action selection |
| R2 render sólido+orientado | `scene_raycast` + `camera_set`+`capture_screenshot` | raycast sólido en N puntos + visual N ángulos | winding por-pieza / orient / escala |
| **R2.5 restore (F5-009)** | (restaurar gameplay) | controles/sim restaurados; **freecam prohibida en esta escalera** | — |
| R3 get-in disponible | `query_get_in_condition` (**con `component` de un raycast**) | `available==true` | el `first_block` reportado |
| R4 sentado | `vehicle_enter` | `seated==true`, seat=driver | seat anim / crew |
| R5 conduce | `engine_set`+`vehicle_control`+`vehicle_telemetry`+`vehicle_release` | `pos_delta>umbral` con throttle | wheel sim (FireGeo) / drivetrain |
| R6 ruedas/sentido | `vehicle_control(steer)` + visual | gira al lado correcto | model.cfg `angle` |

**Orden cámara→conducir (F5-009):** `camera_set` suprime controles
([`MCPClientBridge.c:562`](../../DayZ_MCP/scripts/5_Mission/MCPClientBridge.c)) y la freecam
deshabilita la simulación del player ([`:627`](../../DayZ_MCP/scripts/5_Mission/MCPClientBridge.c)).
La escalera DEBE restaurar gameplay (rung R2.5) antes de get-in/drive, y **no usar freecam** entre
R2 y R5, o el coche no responderá.

**Barandillas anti-verde-falso (innegociables)**: (1) si un rung falla **fuera de la taxonomía
conocida** → la skill PARA y escala, no rebuildea a ciegas; (2) el gate es ground-truth in-game,
nunca proxy offline; (3) presupuesto de iteraciones por ciclo + journal por ciclo
(screenshots+telemetría+veredicto) para que cada verde sea inspeccionable.

---

## 7. Gates de aceptación

### Gate S0 (spike, 1 rebuild) — F5-006
`drive_probe_client` sobre CivilianSedan: `pos_delta>1.0` tras 2 s throttle **+ reporta
`IsOwner()`/`GetOwnerIdentity()`/net id** → la hipótesis owner se sostiene. Si FAIL → STOP; el trío
de ownership clasifica la causa (ownership no transferido vs otra). No se construye nada más.

### Gate A (control modular, mismo o siguiente rebuild)
1. `engine_set start` → `engine_on_server==true` (campo wire; `engine_on` es solo alias de lectura, §3.2); `stop` → false.
2. `vehicle_control{throttle:1}` sostenido → el coche acelera (Δpos crece tick a tick), se mantiene sin re-llamar (held §3.1).
3. `vehicle_control{steer:-1}` → el coche gira.
4. `gear_shift{mode:"to",gear:2}` → `vehicle_telemetry.gear==2`.
5. `vehicle_control{brake:1}` → desacelera a ~0.
6. **`vehicle_release` (F5-003)** → el held-state se desarma, throttle a 0.
7. **Deadman (F5-003)** → tras `hold_ttl_s` sin refrescar `vehicle_control`, el coche auto-suelta (Δpos deja de crecer) sin más llamadas.
8. **Identidad (F5-004)** → con control activo, forzar cambio de coche (o salir) → el held NO se aplica al coche nuevo (auto-clear).
9. **Negativos fail-closed (F5-007)** → `throttle:2`, `steer:5`, `gear:99`, **y `throttle:NaN`/`steer:Inf`** → `ToolError`, coche intacto.

### Gate B (query, mismo rebuild) — F5-002 / F5-008
1. Coche vanilla bien formado, **con `component` válido** → `available==true`, `per_seat[driver].crew_can_get_through==true`.
2. **Fixture de regresión SUB_BRZ (especificado, F5-008)**: coche con **componentNN presente +
   asiento mapeado** (gates 4-5 pasan) pero `class X: CarScript` pelado sin override de
   `CrewCanGetThrough` → `available==false`, `first_block=="crew_can_get_through"`. (Sin componentNN
   válido el blocker sería gate 4, no 6 — el fixture debe garantizar que el componente mapea.)
3. Coche con asientos **sin componentNN** → `available==false`, `first_block=="componentNN"`
   (`component_crew_index<0`).
4. **Sin `component` (F5-002)** → result `partial==true`, NUNCA un `available==true` (PASS).

### Gate de regresión
`run-fase2.ps1` + `run-fase3.ps1` GATE=PASS post-rebuild (el `drive_probe` server y los 8 handlers
existentes NO se tocan — R7).

---

## 8. Riesgos y mitigaciones

| Riesgo | Prob. | Mitigación |
|---|---|---|
| Owner-side `SetThrottle` tampoco mueve el coche (cliente MCP no es owner real) | media | **S0 lo aísla + instrumenta `IsOwner`** (F5-006); si falla, plan B = input vía `HumanInputController` (investigación nueva, no se asume) |
| `SuppressGameplay`/freecam pisan o bloquean el control (cámara antes de conducir) | media | rung R2.5 restore + freecam prohibida (F5-009); held-state reaplica tras el HID |
| Held throttle huérfano si la sesión muere (daemon sobrevive, LL-156) | **media** | **deadman/TTL + `vehicle_release`** (F5-003); `vehicle_control` refresca el deadline |
| Held-state reaplica a coche equivocado tras cambiar de vehículo | baja | **net id fijado al activar + auto-clear** si cambia (F5-004) |
| `query_get_in_condition` da verde-falso sin componente | media | **`component` obligatorio para PASS**; sin él, `partial` (F5-002) |
| Rename `engine_on_server` rompe el server bridge | baja | **no rename en F5**, alias documentado (F5-005); rename = plan separado |
| BUG-009 (autoconexión cliente flaky) intermitencia de gates | media | mitigación heredada `-WaitInGameSeconds 300` + retry (Fase 4 §9) |

---

## 9. Reparto y orden

1. **R22 ✓ HECHA 2026-06-28**: Codex NEEDS-WORK (3 P1 + 6 P2); F5-001..009 aplicados en este v2
   (§12). F5-001 adjudicado (grupo G en product-spec, cerrado antes de S0).
2. **Codex implementa S0** (handoff scope-bounded: 1 cmd cliente `drive_probe_client` + reporte
   ownership; PROHIBIDO tocar el `drive_probe` server / camera / harness).
3. **Receptor Claude + Gate S0** (1 rebuild). **Decision point**: PASS→seguir, FAIL→STOP+escalar (con el dato de ownership).
4. **Codex implementa Tramo A+B** (4 verbos control + `vehicle_release` + held/deadman/identidad +
   `query_get_in_condition` + DTOs + whitelist + tools; PROHIBIDO tocar handlers existentes).
5. **Receptor + 1 rebuild + Gates A/B**.
6. **Skill `dayz-mcp-verify`**: cablear la escalera §6 (sesión aparte).
7. Cierre: Changelog product-spec (hecho 2026-06-28), HANDOFF, handoff 30_Sessions.

---

## 10. Criterio product-spec — añadido (F5-001)

Grupo **G (G0-G2)** ya está en `product-spec.md` (§"G — Drivability real & diagnóstico de acceso")
y en su Changelog (2026-06-28). G0=spike/ownership, G1=verbos control owner (sostenido+deadman+
fail-closed), G2=`query_get_in_condition` (`component` obligatorio para PASS). Aceptación = gates
S0/A/B de este plan.

---

## 11. Citas verificadas (R22 las re-chequea)

**Bridge (proyecto):** `DayZ_MCP/scripts/5_Mission/`
- Dispatch server else-if: `MCPBridge.c:338-388` · `DispatchVehicleEnter`: `:429-488` (StartCommand_Vehicle crew=0 `:462-464`) · `DispatchVehicleDriveProbe`: `:490-559`
- drive_probe: `ProcessDriveProbe` `:1733` · Ignite `EngineStart` `:1827` · Drive `ShiftTo`/`SetThrottle` `:1844-1850` · Sample `GetSpeedometer`/`pos_delta` `:1884-1891` · `EncodeNetworkMoveStrategy` `:1913` · comentario "DECISION DATA, no es la tool final" `:1979`
- `IsVehicleFixtureReady` `:1696-1731` (`OnDebugSpawn` `:1795`) · `IsSeatReady` VEHICLESEAT_DRIVER `:1688`
- Dispatch cliente else-if (solo camera): `MCPClientBridge.c:427-439` · `OnTick` `:158` · `DrainPending` `:380` · `SuppressGameplay` `:960-980` · freecam `DisableSimulation` `:627` · `IsFiniteFloat` `:915` · `GetGame().GetPlayer()` `:624`/`:967`
- DTOs: `MCPMessages.c` — `MCPArgs.throttle/duration/seat` `:19-21` · `MCPResult.engine_on_server/speedo_max/pos_delta/net_strategy` `:214-217`, `seated`/`seat` `:210-211` · `MCPJob` `:223-246` · `MCP_BRIDGE_VERSION` `:1`

**Vanilla (read-only ref):** `scripts/`
- `Car.*`: `car.c` — `SetSteering` `:196` · `SetThrottle` `:202` · `SetBrake` `:214` · `EngineStart` `:244` · `EngineStop` `:247` · `GetGear` `:259` · `GetGearCount` `:265` · `ShiftUp` `:268` · `ShiftTo` `:271`
- Owner-authority: `carscript.c:3220-3231` (`IsServerOrOwner`) · `NetworkMoveStrategy` enum `pawn.c:138`, getter `:218` · **`IsOwner` `pawn.c:194` · `GetOwnerIdentity` `pawn.c:209` · `GetNetworkID` `object.c:815`** (F5-006)
- Get-in: `actiongetintransport.c:26-80` (`ActionCondition`), `:50-51` (componentIndex→CrewPositionIndex), `:91` (`StartCommand_Vehicle`)
- Transport crew API: `transport.c` — `CrewSize` `:112` · `CrewPositionIndex` `:116` · `CrewMember` `:124` · `GetSeatAnimationType` `:475` · `CrewCanGetThrough` `:493` · `CanReachSeatFromDoors` `:511` · `IsAreaAtDoorFree` `:679` · `GetActionComponentNameList` `object.c:198`

---

## 12. Changelog R22 (2026-06-28) — resoluciones F5-001..009

| ID | Sev | Resolución en v2 |
|---|---|---|
| F5-001 | P1 | DPF cerrado ANTES de S0: grupo **G** añadido a product-spec (adjudicado con usuario). §1, §10. |
| F5-002 | P1 | `query_get_in_condition`: `component` OBLIGATORIO para PASS; sin él → `partial`, nunca PASS. §4.1, Gate B.4. |
| F5-003 | P1 | `vehicle_release` añadido a la surface (§3 tabla) + **deadman/TTL** que auto-suelta (§3.1) + Gate A.6/A.7. Motivado por daemon-sobrevive-sesión (LL-156). |
| F5-004 | P2 | Identidad del coche (net id) fijada al activar control + auto-clear si cambia. §3.1, Gate A.8. |
| F5-005 | P2 | Rename `engine_on_server` CERRADO: no en F5, alias documentado; rename = plan separado. §3.2. |
| F5-006 | P2 | S0 reporta `IsOwner()`/`GetOwnerIdentity()`/net id (APIs confirmadas pawn.c:194/209, object.c:815). §2, Gate S0. |
| F5-007 | P2 | Fail-closed NaN/Inf + rango para throttle/steer/brake/handbrake (reusa `IsFiniteFloat`). §5, Gate A.9. |
| F5-008 | P2 | Fixture SUB_BRZ especificado: componentNN presente + asiento mapeado, entonces `CrewCanGetThrough=false`. Gate B.2. |
| F5-009 | P2 | Escalera: rung R2.5 restore-gameplay antes de get-in/drive + freecam prohibida. §6. |

**Round-2 (approve with minor, 2026-06-28):** Codex verificó **8 closed + 1 partial** (F5-005, por
la inconsistencia R22bF5-001). 3 regresiones doc-only aplicadas: **R22bF5-001** (Gate A.1 →
`engine_on_server`, no `engine_on`), **R22bF5-002** (§3.3 "5 verbos"), **R22bF5-003** (`hold_ttl_s?`
en la columna Args de `vehicle_control`). **0 P1 abierto → S0 puede empezar.** Citas F5-006
(`IsOwner`/`GetOwnerIdentity`/`GetNetworkID`) confirmadas por Codex contra vanilla. Review:
`reviews/2026-06-28-plan-review-fase5-round2-codex.md`.

---

## 13. Corrección de premisa post-S0 (applied 2026-06-28) — NO reescribe §0/§2, las matiza

El gate S0 in-game + investigación de source (4 analistas + 2 jueces, `unified-root-holds`) corrigen
la premisa de §0/§2:

- **Premisa original (§0/§2):** "el `drive_probe` server es **inerte** para coches PHYSICS (el throttle
  solo afecta al owner=cliente)". Era teoría (de `actionstartengine.c`, guard sobre EngineStart no
  SetThrottle) + una medición B3 débil (`fase1-verdict.json`: pos_delta=0.136m, "inconclusive").
- **Corregido (verificado):** el server `SetThrottle` **SÍ mueve** un coche PHYSICS **sin dueño-cliente**
  (S0 F2: pos_delta=2.29, speedo=9.41). `IsServerOrOwner()` (carscript.c:3222-3231) **solo gatea
  teardown** (carscript.c:822/850/986), no el throttle; throttle→física es nativa y la aplica el
  **owner**; un coche sin owner = `IsAuthorityOwner` → lo simula el server. Redacción correcta: *"el
  server mueve un coche SIN dueño; se vuelve inerte cuando un cliente lo posee."*
- **S0 F1 (precondición que invalida el approach de Tramo A tal cual):** `vehicle_enter` (server
  `StartCommand_Vehicle`) NO transfiere ownership ni vehicle-command al cliente → `drive_probe_client`
  da `not_seated`. **El plan se SOSTIENE y Tramo A sigue siendo necesario**, pero su **precondición
  cambia**: el cliente debe **tomar ownership él mismo** (get-in client-side / `StartCommand_Vehicle` en
  el Human del cliente desde `MCPClientBridge`, posiblemente con juncture `AddInventoryJunctureEx`/
  `SetVehicle` — `actiongetintransport.c:82-98,141-161`), no el seat forzado server-side de §2.
- **Pendiente de Tramo A (rediseño):** sesión nocturna
  `reviews/2026-06-28-prompt-next-session-tramoA-redesign-nocturna.md`. Detalle del gate + investigación:
  `reviews/2026-06-28-prompt-impl-fase5-s0-codex.md`.
