# Plan delta — Fase 5 Tramo A REDISEÑO (precondición ownership client-side)

> **Estado**: DRAFT nocturno 2026-06-28 (autónomo). Sucede a `plans/2026-06-28-fase5-drivability-autonoma.md`
> (v2.1 §13). Disparado por el gate S0 (F1) + investigación adversarial (workflow `wj2n4zm97`, 3 analistas +
> 2 jueces, **ambos jueces hypothesis-refuted SOBRE EL MECANISMO**). NO re-litiga el plan v2.1; corrige LA
> PRECONDICIÓN de Tramo A y la implementa por una vía distinta a la propuesta en §13.
> **Implementa el SPIKE**: Codex (handoff scope-bounded). **Receptor + gate in-game**: Claude.
> **El Tramo A completo (5 verbos) queda como `[DESIGN]` — NO se construye sin aprobación (cambio grande).**

---

## 1. Qué cambió respecto a §13 del plan v2.1 (verdicto verificado)

§13 propuso: "el cliente debe tomar ownership él mismo (get-in client-side / `StartCommand_Vehicle` en el
Human del cliente desde `MCPClientBridge`, **posiblemente con juncture** `AddInventoryJunctureEx`/`SetVehicle`)".
La investigación adversarial refina y CORRIGE dos puntos de esa frase:

### 1.1 El path "DEVELOPER debug get-in" (SendGetInVehicle) está MUERTO en este build — NO usar
La vía vanilla de get-in por sync-juncture (`DayZPlayerSyncJunctures.SendGetInVehicle`) y todo su receptor
cliente viven bajo `#ifdef DEVELOPER`. **`DEVELOPER` (a secas) NO está definido en este DayZDiag** (solo
`DIAG_DEVELOPER`). Verificado leyendo los logs de compilación reales del gate S0 (ambos peers, todos los
módulos, 2 corridas):
- SERVER `…,DIAG,DIAG_DEVELOPER,NO_GUI,NO_GUI_INGAME,…,FEATURE_NETWORK_RECONCILIATION,…`
  (`_s0/run_20260628_024647/server_profiles/script_2026-06-28_02-46-50.log:4-10`)
- CLIENT `…,DIAG,DIAG_DEVELOPER,…,FEATURE_NETWORK_RECONCILIATION,…`
  (`_s0/run_20260628_024647/client_profiles/script_2026-06-28_02-46-58.log:4-9`)

**Consecuencia dura**: un PBO que llame `DayZPlayerSyncJunctures.SendGetInVehicle` (símbolo solo existente
bajo `#ifdef DEVELOPER`, `dayzplayersyncjunctures.c:33-58`) **FALLA LA COMPILACIÓN del mod entero → tira
TODAS las tools** (no es un no-op silencioso). PROHIBIDO emitirlo. Corroborado por GATE4B-LIM (ExecuteEnforceScript
Developer-only devuelve false en server diag).

### 1.2 El juncture de inventario NO es load-bearing para el ownership
`AddInventoryJunctureEx` + `InventoryLocation.SetVehicle` (`actiongetintransport.c:141-161 AddActionJuncture`)
= **reserva de asiento** (lock para que otro crew no lo tome). El ownership NO viene de ahí. El get-in debug
vanilla lo OMITE por completo. Para un único conductor sobre un coche vacío, no es necesario.

### 1.3 El ownership SÍ se transfiere — por una vía que NO necesita DEVELOPER `[EXACT]`
`FEATURE_NETWORK_RECONCILIATION` SÍ está definido (ambos peers, ver logs). Bajo ese macro:
```c
// playerbase.c:4266-4282
override void OnVehicleSeatDriverEnter()
{
    m_IsVehicleSeatDriver = true;
    if (m_Hud) m_Hud.ShowVehicleInfo();
#ifdef FEATURE_NETWORK_RECONCILIATION
    PlayerIdentity identity = GetIdentity();
    if (identity)
    {
        Pawn pawn = Pawn.Cast(GetParent());   // GetParent() == el COCHE al estar sentado
        identity.Possess(pawn);               // la identity controladora posee el coche
    }
#endif
}
```
`Possess` proto native (`gameplay.c:377-380`); `Transport extends Pawn` (`transport.c:53`); `IsOwner/
IsAuthorityOwner` (`pawn.c:194-209`). NO hay guard `IsServer` ni `DEVELOPER`. Simétrico: `OnVehicleSeatDriverLeft`
(`:4284-4300`) re-posee al player. Corrobora: muerte del conductor re-posee al player "so other players can use
the vehicle" (`dayzplayerimplement.c:557-570,710-722`).

**Por qué F1 ocurrió**: el `vehicle_enter` server fuerza `StartCommand_Vehicle` sobre la COPIA SERVER del player
(`MCPBridge.c:464`), pero el CLIENTE es dueño de su propio player pawn → un comando forzado server-side no se
convierte en el comando del cliente → `GetGame().GetPlayer().GetCommand_Vehicle()` null client-side (F1). El
comando de vehículo debe DRIVERLO el owner del player = el CLIENTE.

---

## 2. Mecanismo del rediseño — el CLIENTE sienta su propio player `[EXACT firmas]`

El CLIENTE llama, sobre SU player, `StartCommand_Vehicle` (native sin guard):
`GetGame().GetPlayer().StartCommand_Vehicle(car, 0, seatAnim)` (`human.c:1492`,
`seatAnim = car.GetSeatAnimationType(0)`). El cliente conduce su propio comando (owner del player pawn) →
get-in completa → `OnVehicleSeatDriverEnter` → `Possess(car)` → `car.IsOwner()==true` client-side → la
simulación owner-side de PHYSICS corre en el cliente → `SetThrottle` owner-side mueve el coche.

**Resolución del coche en el cliente** `[EXACT]`: el coche está spawneado (server) y replicado al cliente.
Resolverlo por posición con `GetGame().GetObjectsAtPosition3D(pos, radius, objs, cargos)` + `Transport.Cast`
(API general, válida client-side; espejo de `FindTransportNear` `MCPBridge.c:1512-1532`).

**Estado "sentado"** `[EXACT]`: `hcv.GetTransport()==car && !hcv.IsGettingIn() &&
hcv.GetVehicleSeat()==DayZPlayerConstants.VEHICLESEAT_DRIVER` (`HumanCommandVehicle` `human.c:689-706`;
espejo de `IsSeatReady` `MCPBridge.c:1677-1693`). Hay LATENCIA: `StartCommand_Vehicle` arranca el get-in;
`OnVehicleSeatDriverEnter` dispara al COMPLETAR. Por tanto **poll hasta sentado** dentro de `prep_deadline`.

**Incógnita que SOLO el gate resuelve (native, no script-verificable)**: ¿el server SANCIONA un
`StartCommand_Vehicle` client-directo sin el RPC de acción/juncture, o la reconciliación de red lo
force-corrige (saca al cliente)? Mitigación = secuencia del gate §5 (client-only primero; luego
server `vehicle_enter` + client-self-seat = ambas copias sentadas, réplica fiel del `Start()` dual).

---

## 3. SPIKE (esto SÍ se construye esta noche — FASE D Codex, scope-bounded)

Objetivo: probar la North Star — el cliente toma ownership in-game (`IsOwner()==true` client-side) y
`drive_probe_client` mide `pos_delta` owner-side. Mínimo, gate-focused, **sin loopback.py** (verbos ya
whitelisted) → **sin reinicio de daemon**.

### 3.1 Parte 1 — auto-seat client-side en `drive_probe_client` (MCPClientBridge.c) `[DESIGN, APIs EXACT]`
Modificar `ProcessDriveProbeClientPrep` (`MCPClientBridge.c:646-704`):
- Añadir helper `FindTransportNearClient(vector pos)` espejando `MCPBridge.c:1512-1532`
  (`GetGame().GetObjectsAtPosition3D` + `Transport.Cast`; arrays locales/miembro + const radius).
- En PREP, ANTES de exigir `GetCommand_Vehicle()`:
  1. `PlayerBase player = PlayerBase.Cast(GetGame().GetPlayer());` si null ⇒ `error="no_player"`.
  2. `HumanCommandVehicle hcv = player.GetCommand_Vehicle();`
  3. Si `hcv` null Y `!job.seat_attempted`: leer `pos` de `job.args.pos` (reusa `MCPArgs.pos`); si vacío ⇒
     `error="no_pos"`. `Transport car = FindTransportNearClient(pos);` si null ⇒ `error="no_vehicle"`.
     `int seatAnim = car.GetSeatAnimationType(0); player.StartCommand_Vehicle(car, 0, seatAnim);`
     `job.seat_attempted = true; return false;` (esperar).
  4. Si `hcv` no null pero `hcv.IsGettingIn()` ⇒ `return false` (sigue entrando).
  5. Si `hcv` no null Y `!IsGettingIn()` Y `GetVehicleSeat()==VEHICLESEAT_DRIVER` ⇒ SENTADO:
     `CarScript car = CarScript.Cast(hcv.GetTransport())`, `job.subject=car`,
     `CaptureDriveProbeClientOwnership(job, car)` (ya existe `:801-826`), gameplay setup one-shot (ya existe),
     fixture readiness (ya existe), `phase=IGNITE`.
  6. Si `m_JobRunner.GetElapsedS() > job.prep_deadline_s` ⇒ `error="not_seated"` (reporta el trío igualmente).
- `MCPJob` campo nuevo `bool seat_attempted;` (MCPMessages.c).
- El resto de fases (IGNITE/DRIVE/SAMPLE/REPORT) **sin cambios**; ya capturan ownership + miden pos_delta.

### 3.2 Parte 2 — lectura de ownership en el SERVER probe (FASE B) (MCPBridge.c) `[DESIGN, APIs EXACT]`
Confirma F2 (artefacto single-box) por lectura directa: mientras el server mueve el coche sin dueño-cliente,
predicción `is_owner==false`, `is_authority_owner==true`, `owner_identity==""`.
- Añadir helper `CaptureDriveProbeOwnership(MCPJob job, CarScript car)` espejando el del cliente
  (`MCPClientBridge.c:801-826`): `job.is_owner=car.IsOwner();` `job.is_authority_owner=car.IsAuthorityOwner();`
  `PlayerIdentity oid=car.GetOwnerIdentity(); job.owner_identity = oid ? oid.GetPlainId() : "";`
  `int lo,hi; car.GetNetworkID(lo,hi); job.net_id_low=lo; job.net_id_high=hi;`
  (`pawn.c:194-209`, `object.c:815`, `gameplay.c:370`).
- Llamarlo en `ProcessDriveProbeDrive` (snapshot) y `ProcessDriveProbeSample` (`MCPBridge.c:1861-1901`).
- Surfacear en `PostDriveProbeResult` (`MCPBridge.c:1977-1992`): `is_owner/is_authority_owner/owner_identity/
  net_id_low/net_id_high`.

### 3.3 DTO (MCPMessages.c) `[DESIGN]`
- `MCPResult` (tras `net_id_high` `:221`): `bool is_authority_owner;`.
- `MCPJob` (tras `net_id_high` `:248`): `bool is_authority_owner;` + `bool seat_attempted;`.
- `is_owner/owner_identity/net_id_low/high` YA existen (`:218-221`, `:245-248`). NO bumpear `MCP_BRIDGE_VERSION`.
  Capturar `is_authority_owner` también en el client probe (simetría; cliente owner ⇒ false).

### 3.4 Whitelist / tools — SIN cambios
`drive_probe_client` (CLIENT) y `vehicle_drive`/`vehicle_enter` (SERVER) YA whitelisted (`loopback.py:19-29`).
NO tool MCP nueva. NO reinicio de daemon. Conducción raw por `/enqueue`+`/await`.

### 3.5 Test Python (offline)
Routing ya cubierto (`test_loopback.py:127-138`). El cambio del spike es Enforce (gate in-game). Si Codex
añade un campo a un DTO, no afecta routing. Re-correr `python -m unittest discover tests` (debe seguir verde).

### 3.6 Fuera del spike (NO construir)
Los 5 verbos de control, held-state/deadman/identidad, `query_get_in_condition`, tool MCP nueva. Eso es §4.

---

## 4. Tramo A COMPLETO redibujado `[DESIGN]` — requiere aprobación, NO se construye esta noche

Solo si el SPIKE da PASS (cliente toma ownership + mueve owner-side). Diferencias vs el §3 del plan v2.1:
- **Precondición nueva**: un verbo `vehicle_get_in_client` (CLIENT) que sienta al cliente vía
  `StartCommand_Vehicle` client-side (lo del spike, promovido a verbo propio) + `vehicle_get_out_client`.
  La resolución del coche por `pos`. Los 5 verbos de control (`engine_set`/`vehicle_control`/`gear_shift`/
  `vehicle_telemetry`/`vehicle_release`) resuelven el coche owner por `GetGame().GetPlayer().GetCommand_Vehicle().GetTransport()`
  (ya non-null tras el get-in client-side) y se gatean a `IsOwner()`.
- Held-state/deadman/identidad/fail-closed: igual que v2.1 §3.1-3.2 (sin cambios).
- `query_get_in_condition` (server): sin cambios (v2.1 §4).
- Cambio grande multi-archivo (MCPClientBridge + MCPMessages + loopback + server.py + tests) → **plan +
  aprobación antes de implementar** (regla G1/R3). Queda como handoff para sesión con usuario.

---

## 5. Gate del SPIKE in-game (FASE E) — secuencia + árbol de decisión

1 rebuild de `DayZ_MCP.pbo` (`-packonly`) + deploy. Daemon NO se reinicia (loopback intacto). `run-s0-gate.ps1`.

**Celda 1 (hipótesis limpia, client-only):** `world_spawn CivilianSedan` (server) → `vehicle_drive` (server,
acondiciona fuel/batería + lee ownership server, predice is_authority_owner=true) → `drive_probe_client`
(client, `pos`=spawn pos, mode="") → auto-seat client-side → mide.
**Celda 2 (réplica dual si Celda 1 no da ownership):** `world_spawn` → `vehicle_enter` (server, sienta copia
server) → `drive_probe_client` (client, `pos`, mode="") → ambas copias sentadas → mide.
**Celda 3:** repetir la que mueva con `mode="suppress"`.

**PASS = `pos_delta>1.0` Y `speedo_max>0` Y `engine_on_server==true`** (triple criterio; owner_identity es
ADVISORY en cliente). Árbol por celda:
```
drive_probe_client error=not_seated              -> el client NO se sentó: ownership untestable; revisar latencia/force-correct
vehicle_fixture_ready==false                     -> fallo de acondicionado, NO ownership (correr vehicle_drive antes)
pos_delta>1.0 && speedo_max>0 && engine_on       -> PASS (cliente owner + mueve -> Tramo A se construye)
pos_delta≈0 && is_owner==true (client)           -> STOP, owner-side throttle no mueve -> plan B HumanInputController (§8 v2.1)
pos_delta≈0 && is_owner==false (client)          -> STOP, ownership NO se transfirió al cliente: capturar trío + server probe trío
Celda1 STOP pero Celda2 PASS                      -> hallazgo: el get-in necesita la copia server (registrar)
```
Teardown por PID (`_s0-run.json`). EXCLUSIVIDAD del box primero (si hay DayZ ajeno → solo offline, PARA).

---

## 6. Riesgos

| Riesgo | Prob | Mitigación |
|---|---|---|
| Server force-corrige el seat client-directo (sin RPC de acción) | media | Celda 2 (server `vehicle_enter` también) = réplica dual; árbol distingue |
| `GetGame().GetPlayer().GetIdentity()` null en cliente ⇒ Possess no corre client-side | media | Possess es server-authoritative; el server (su OnVehicleSeatDriverEnter) asigna y sincroniza; Celda 2 garantiza la copia server. Gate lo revela por `is_owner` |
| Latencia get-in ⇒ race not_seated | baja | PREP poll hasta sentado dentro de `prep_deadline` (5s) |
| `is_authority_owner` campo nuevo rompe serialización | baja | Aditivo, autoserializado por JsonSerializer; sin bump de versión |
| Box ocupado por otra sesión en FASE E | media | Re-check inmediato antes de lanzar; NUNCA evictar |

---

## 7. Citas verificadas (R2.1 — Read directo propio salvo nota)

**Compile defines (logs reales, ambos peers):** `_s0/run_20260628_024647/server_profiles/script_2026-06-28_02-46-50.log:4-10`
· `…/client_profiles/script_2026-06-28_02-46-58.log:4-9` · (2ª corrida `run_20260628_023736` concuerda).
**Vanilla (`scripts/`):** `playerbase.c:4266-4282` (OnVehicleSeatDriverEnter→Possess(car)) · `:4284-4300`
(Left→Possess(player)) · `pawn.c:5-8` glosario, `:194-209` IsOwner/IsAuthority/IsAuthorityOwner/GetOwnerIdentity ·
`transport.c:53` Transport extends Pawn · `gameplay.c:377-380` Possess proto native · `human.c:1492`
StartCommand_Vehicle, `:689-706` HumanCommandVehicle (GetVehicleSeat:696, IsGettingIn:705) ·
`dayzplayersyncjunctures.c:33-58` (#ifdef DEVELOPER — MUERTO) · `plugindeveloper.c:622-640` (#ifdef DEVELOPER) ·
`dayzplayerimplement.c:557-570,710-722` (death re-possess) · `staminahandler.c:507` (IsAuthorityOwner naming).
**Bridge (proyecto):** `MCPClientBridge.c:646-704` (PREP a modificar), `:801-826` (CaptureDriveProbeClientOwnership) ·
`MCPBridge.c:429-488` (DispatchVehicleEnter, F1), `:464` (server StartCommand_Vehicle), `:1512-1532`
(FindTransportNear), `:1677-1693` (IsSeatReady), `:1861-1901` (ProcessDriveProbeSample), `:1977-1992`
(PostDriveProbeResult) · `MCPMessages.c:11-67` MCPArgs (`pos`:14, `mode`:26), `:197-225` MCPResult
(ownership :218-221), `:227-254` MCPJob · `loopback.py:19-31` whitelist.
**Workflow `wj2n4zm97`:** 3 analistas + 2 jueces (ambos hypothesis-refuted-on-mechanism). A3 falló (schema cap);
sus preguntas resueltas por A1+jueces+lecturas propias. Output: `tasks/wj2n4zm97.output`.

---

## 8. RESULTADO DEL GATE IN-GAME (2026-06-28 noche) — **rediseño VALIDADO (ownership) + STOP en driving (plan B HID)**

Conducido por Claude vía raw `/enqueue`+`/await` a :8765 (daemon broker, key OK, version 4 ok) + DayZDiag
server+client (`run-s0-gate.ps1`, PBO reconstruida `-packonly` con el spike+fixes, ambos peers `vstate=ok`).
Box exclusivo (sin DayZ ajeno). Spike implementado por Codex (receptor limpio: 3 archivos, 121 tests OK,
0 scope creep, sin `#ifdef DEVELOPER`) + 3 fixes de revisión adversarial (sim_restore, seat_failed, SetVehicleType).

### Evidencia (CivilianSedan, net_id=23, net_strategy=2=PHYSICS)

| Paso | Peer | Resultado clave |
|---|---|---|
| `world_spawn CivilianSedan` | server | ok, found=1 @ (6063.1, 1931.7) |
| `drive_probe_client` (self-seat) mode="" | client | **is_owner=1**, is_authority_owner=0, owner_identity="765…937", fixture_ready=1, engine_on=1, **pos_delta=0.126, speedo_max=0.0** |
| `drive_probe_client` mode="suppress" | client | **is_owner=1**, igual, **pos_delta=0.115, speedo_max=0.0** |
| `vehicle_drive` (sin enter) | server | `not_seated` (el seat client-side NO sincroniza el command al player-copy del server — espejo de F1) |
| `vehicle_enter` + `vehicle_drive` | server | seated=1; **is_owner=0, is_authority_owner=0, owner_identity="765…937", net_id=23**, pos_delta=0.022, speedo=0.37 |

### Hallazgo G1 (PRECONDICIÓN RESUELTA — rediseño validado in-game + confirmado por ambos lados)
El **cliente toma ownership REAL de red** del coche llamando `GetGame().GetPlayer().StartCommand_Vehicle(car,0,seat)`
client-side: `car.IsOwner()==true` client-side **Y** el servidor lo confirma (`IsAuthorityOwner()==false` =
"authority pero CON dueño" + `GetOwnerIdentity()==la identity del cliente`). El servidor NO puede mover el
coche (no es el dueño). **F1/F2 (el cliente nunca tomó ownership) están RESUELTOS.** El mecanismo de FASE A
(client-side StartCommand_Vehicle → `OnVehicleSeatDriverEnter`→`Possess(car)` bajo FEATURE_NETWORK_RECONCILIATION)
funciona. La invariante owner-authority queda **ESTABLECIDA** (verificada source + in-game + server-confirmed).

### Hallazgo G2 (NUEVO BLOCKER, distinto de ownership — STOP, plan B §8)
Con el cliente como dueño legítimo, **`Car.SetThrottle()` owner-side NO mueve el coche** (`pos_delta≈0`,
`speedo_max==0`, con engine_on y gear=FIRST y freno/handbrake sueltos; idéntico en mode=""` y `"suppress"`).
El servidor tampoco lo mueve (no es dueño). Causa (empírica + hipótesis citada): el `Car.SetThrottle` scripted
es "future input" (`car.c:201-202`) que bajo la reconciliación PHYSICS del owner NO se consume para conducir;
la conducción real va por el **HumanInputController / controller del coche**, no por el setter scripted.
**Esto es el plan B §8 (input vía HumanInputController) — requiere investigación nueva + código (sesión aparte).**

### Veredicto
**STOP** por la rama del árbol `pos_delta≈0 && is_owner==true → plan B HID`. PERO: a diferencia del gate S0
(que paró en la PRECONDICIÓN), este gate **superó la precondición** (ownership client-side conseguido y
confirmado por el servidor) y aisló el blocker real al ACTUADOR de throttle. El rediseño de la precondición
es un **éxito**; Tramo A necesita repensar el actuador de control (HID en vez de `Car.SetThrottle`).

### Implicación para Tramo A (§4)
La precondición `vehicle_get_in_client` (seat client-side) **está probada**. Los 5 verbos de control NO pueden
usar `Car.SetThrottle/SetSteering/SetBrake` scripted (no conducen el owner-sim PHYSICS) → deben alimentar el
input por el HumanInputController. **Antes de construir Tramo A: investigar el actuador HID (plan B §8).**

**Evidencia bruta:** `scratchpad\_gate_clientonly.json` + `diag_server_ownership.py` (output en el handoff de sesión).
**Promoción de invariante a `dayz-vehicles`:** AHORA procede para la mitad de ownership (establecida); ver handoff.

---

## 9. PLAN B RESUELTO (2026-06-28 mediodía) — **drivability owner-side CONSEGUIDA, PASS in-game**

El usuario eligió "Solo B (modded CarScript)" para el actuador. Investigación de source (Claude, R2):
- El input de conducción es 100% NATIVO. Los ÚNICOS callers de `Car.SetThrottle/SetSteering` en TODO el
  vanilla son `actionpushcar.c` (brake=0) y el autopiloto debug `carscript.c:1377` (dentro de `OnInput`,
  bajo `#ifdef DIAG_DEVELOPER`). No hay API de inyección de input de vehículo: `HumanInputController`
  (`human.c:17-269`) no tiene override de throttle (`IsOtherController` :195 confirma que el vehículo usa
  otro controlador), y `UAInput`/`UAInterface` (`uainput.c`) solo LEEN/configuran, no setean valores.
- **CAUSA RAÍZ del blocker §8**: `CarScript.OnInput(dt)` (`carscript.c:1303`) corre cada frame en el
  OWNER; `super.OnInput(dt)` lee el input del DRIVER local (teclas=0) y fija el future-throttle a 0 →
  PISA cualquier `Car.SetThrottle` puesto desde el mission/`OnUpdate`/job. (Por eso F2 SÍ movía un coche
  SIN driver: sin driver, super.OnInput no escribe input → el SetThrottle del server sobrevive.)
- **FIX (espeja el autopiloto vanilla)**: aplicar el throttle DENTRO de `CarScript.OnInput` TRAS `super`.

### Implementación (3 archivos, en la PBO)  `[EXACT]`
- `scripts/4_World/MCP_CarScript.c` (NUEVO): `class MCPCarDrive` (holder estático: s_Active/s_Car/
  s_Throttle/s_Steer/s_Brake/s_Handbrake/s_DeadlineS + `Set`/`Clear`) + `modded class CarScript` con
  `override void OnInput(float dt)` que tras `super.OnInput(dt)` y guardas (s_Active, s_Car==this, deadman
  `GetGame().GetTickTime()>s_DeadlineS`) espeja el actuador del autopiloto (`carscript.c:1311-1381`:
  gestión de RPM con early-return si `EngineGetRPM()<EngineGetRPMIdle()`, marcha manual `ShiftTo(FIRST)`/
  `ShiftUp`, `SetThrottle/SetSteering/SetBrake/SetHandbrake` + `SetBrakesActivateWithoutDriver(false)`).
  (+ logging debug `[MCP-DRIVE]` — QUITAR/gatear antes de producción.)
- `config.cpp`: `dependencies[]={"World","Mission"}` + `class worldScriptModule { files[]={"DayZ_MCP/scripts/4_World"}; }`.
- `MCPClientBridge.c`: `drive_probe_client` fases DRIVE/SAMPLE → `MCPCarDrive.Set(car, throttle, 0,0,0, GetGame().GetTickTime()+DRIVE_CLIENT_DEADMAN_S)`
  (en vez de `Car.SetThrottle` directo); `Clear()` en las 3 salidas del job.

### Gate in-game — PASS (CivilianSedan, owner-cliente)
| Spawn | pos_delta | speedo_max | is_owner | Veredicto |
|---|---|---|---|---|
| en el spawn (6063,1931) — OBSTÁCULO delante | 0.15–0.25 | 0.29–0.89 | 1 | bloqueado por colisión (el usuario lo vio) |
| **offset +40m inland (despejado)** | **29.18 m** | **38.87 km/h** | **1** | **PASS** (los 3 criterios) |

Log `[MCP-DRIVE]` del run despejado: `peer=client`, `thr_set=1`, `gearCur` 2→3→5 (cambio auto),
`rpm` 1049→1467→1973→2489→2994→3429→3774 (motor revoluciona), `spd` 1.7→7.8→…→38.7 km/h, `w0/w1=true`.
→ El motor revoluciona y el coche acelera de verdad; los números bajos del primer spawn eran 100% el obstáculo.

### Veredicto: PLAN B CERRADO. Fase 5 drivability owner-side end-to-end (get-in client + drive owner) FUNCIONA.
NEXT = empaquetar como los 5 verbos de Tramo A sobre `MCPCarDrive` + quitar el debug Print + `query_get_in_condition`
(Tramo B). Cambio grande → plan + aprobación (R3). Evidencia: `tools/_planB-PASS-verdict.json`,
`tools/tramoA_gate_driver.py --dz 40`, log `_s0/run_20260628_120319/client_profiles/script_*.log`.

---

## 10. TRAMO A — verbos componibles CONSTRUIDOS + gate PASS (2026-06-28, TANDA A)

Codex implementó (handoff scope-bounded) los 5 verbos en el peer CLIENTE de `MCPClientBridge.c` + DTOs +
whitelist `loopback.py` + tests (122/122 OK). Receptor limpio (scope por mtime, brace, R8). 1 fix correctivo
(get-in no condicionaba el coche → motor no arrancaba → `vehicle_get_in_client` ahora hace `OnDebugSpawn`
tras sentarse, como el drive_probe PREP).

| Verbo (peer cliente) | Tipo | Implementación |
|---|---|---|
| `vehicle_get_in_client` | job | auto-seat por `pos` (`StartCommand_Vehicle`) + `OnDebugSpawn` (conditioning) + reporta `seated`/`is_owner`/`vehicle_fixture_ready` |
| `engine_set` | sync | `EngineStart`/`EngineStop` sobre `ResolveOwnedCar()` |
| `vehicle_control` | sync | fail-closed (throttle 0..1, steer -1..1, brake 0..1, handbrake 0/1, + NaN) → `MCPCarDrive.Set(...)` con `hold_ttl_s` (deadman). El "sostenido" lo da el holder+OnInput |
| `vehicle_telemetry` | sync | speedo/gear/engine/pos/net_strategy/ownership-trio |
| `vehicle_release` | sync | `MCPCarDrive.Clear()` |

`ResolveOwnedCar()` = `GetGame().GetPlayer().GetCommand_Vehicle().GetTransport()` casteado a CarScript.
DTOs: `MCPArgs += steer/brake/handbrake/hold_ttl_s`; `MCPResult += gear`. `drive_probe_client` intacto (sigue
de gate). NO se bumpeó la versión. **El daemon se reinició** (loopback.py cambió: +5 verbos en CLIENT_COMMANDS).

### Gate in-game (verbos componibles) — PASS
Secuencia (raw enqueue, `tramoA_verbs_gate.py --dz 40`): `world_spawn` → `vehicle_get_in_client`(pos)
[seated=1, fixture_ready=1, is_owner=1] → `engine_set start` [engine_on=1] → `vehicle_telemetry` [pos0] →
`vehicle_control{throttle:1, hold_ttl_s:12}` (UNA orden) → **5 s sin re-llamar** (el coche conduce SOSTENIDO
vía el held-state + OnInput) → `vehicle_telemetry` [**pos_delta=36.2 m, speedo=40.06 km/h, gear=5 (cambio auto)**,
is_owner=1] → `vehicle_release`. **PASS** (movió >1 m con una sola orden, motor ON, owner). Prueba el motor de
estado retenido + deadman (F5-003). Verdict: `tools/_tramoA_verbs_verdict.json`.

### TANDA B HECHA (2026-06-28) + pendiente menor
**HECHO**: (1) 5 tools `@app.tool` en `server.py` (peer cliente, `runtime.tool_lock`, fail-closed en
`vehicle_control`; espejan `camera_set`/`camera_get`) — verificado offline (test de registro, suite 123/123 OK)
+ gate in-game de los verbos SIN regresión (coche condujo 35.5 m a 40 km/h); (2) **`Print("[MCP-DRIVE]")`
quitado** de `MCP_CarScript.c` (bridge recompila+carga+conduce, peers OK). Codex limpio, scope-bounded.
**Pendiente MENOR (no bloqueante)**: (3) `gear_shift` (opcional, la caja auto cambia sola) + auto-clear de
`vehicle_control` por identidad de coche (F5-004, hoy solo deadman); (4) gate MCP-tool end-to-end (las 5 tools
verificadas offline+revisión; un drive vía las tools reales requiere cliente MCP fresco tipo `gate4a_mcp_client.py`);
(5) `query_get_in_condition` (Tramo B) **HECHO** — server (lectura pura), evalúa los 7 gates de
`ActionGetInTransport` + reporta `first_block`; DTOs `MCPGetInCondition`/`MCPSeatCondition` + whitelist
`SERVER_COMMANDS` + tool MCP (peer server); suite 124/124. **Gate in-game PASS**: componente-pasajero
`available=1, first_block=""`, componente-conductor `crew_index=0, unreachable` (gate 7), no-crew `componentNN`,
sin componente `partial=1` con 4 asientos (gates 5/6 por seat). Nit no bloqueante: `MCPArgs.component` en raw-omit
defaultea a 0 (→`componentNN`) en vez de -1; MOOT porque la tool siempre manda `component` (default `-1`). Driver
`tools/tramoB_getin_gate.py` (spawn SIN offset: gate 7 exige el coche junto al jugador), verdict
`tools/_tramoB-getin-verdict.json`. (6) **escalera `dayz-mcp-verify` (§6 plan v2.1) — único pendiente real de Fase 5**
(orquestación rip→conducible; mapea cada fallo de rung a un fix de la taxonomía SUB_BRZ).
