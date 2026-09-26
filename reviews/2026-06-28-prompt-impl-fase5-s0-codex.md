# Prompt implementación Fase 5 — S0 (`drive_probe_client`) para Codex

> Patrón: implementation-handoff (scope-bounded, post-R22). Plan v2.1 APPROVED.
> Generado 2026-06-28 por Claude tras cargar plan + bridge + verificar APIs (R2).
> El gate S0 in-game lo corre Claude DESPUÉS de que Codex implemente esto.

```
===== PROMPT INICIO =====

Tarea: implementar el spike S0 de la Fase 5 del mod DayZ-MCP: UN comando nuevo
`drive_probe_client` en el peer CLIENTE del bridge Enforce, que conduce el coche owner
y reporta ownership. Esta sesión cubre **únicamente S0** (un cmd cliente + sus DTOs +
la entrada de whitelist + un test de routing Python). El Tramo A (los 5 verbos
`engine_set`/`vehicle_control`/`gear_shift`/`vehicle_telemetry`/`vehicle_release`), el
held-state/deadman/identidad, y `query_get_in_condition` NO se implementan aquí — ni
siquiera parcialmente.

S0 es un spike de DECISIÓN, no la surface final (el plan §3.3 dice que `drive_probe_client`
se reescribe como los 5 verbos cuando S0 valide la hipótesis). Por eso: cmd minimalista,
sin tool MCP, conducido por enqueue raw. No lo conviertas en producto.

## Carga inicial obligatoria

Lee estos archivos antes de tocar nada (rutas absolutas Windows):

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-28-fase5-drivability-autonoma.md
   (plan v2.1 — el contrato. §2 = spec de S0; §11 = citas verificadas; §12 = changelog R22.
   Lo aplicado en el changelog es vinculante.)
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c
   (el `drive_probe` SERVER a ESPEJAR — NO a modificar: constantes de fase :7-18, máquina
   `ProcessDriveProbe*` :1733-1901, ignite :1818, drive :1833, sample :1861. Copia la
   ESTRUCTURA de fases al cliente.)
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPClientBridge.c
   (el peer CLIENTE — DONDE va S0. Dispatch else-if :427-439, patrón de job multi-tick de
   cámara `MCP_ProcessJob`/`ProcessCameraSetJob` :493-542, `MCP_PostJobSuccess` :652-670,
   `SuppressGameplay`/`RestoreGameplay` :960-1006, `IsFiniteFloat` :915, `PostResult` :1030.)
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c
   (DTOs: `MCPArgs` :11-67, `MCPResult` :197-221, `MCPJob` :223-246. Añadirás campos aquí.)
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPJobRunner.c
   (contrato del job runner cliente: `MCP_ProcessJob` true ⇒ éxito/fallo según `job.error`;
   deadline ⇒ timeout. :80-121.)
6. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py
   (whitelist: `SERVER_COMMANDS` :19, `CLIENT_COMMANDS` :29, `peer_for_command` :40. Añadirás
   el cmd a CLIENT_COMMANDS.)

NO releas los reviews R22 round-1/round-2 — el plan v2.1 ya los integra. NO abras las skills
del repo. NO abras `server.py` (no se añade tool MCP en S0).

## Alcance acotado — S0 del plan v2.1 (§2, Gate S0 §7, F5-006/F5-007)

### Paso 1 — DTOs nuevos (MCPMessages.c)

Archivo: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c

- `MCPResult` (tras `net_strategy` :217): añadir
  `bool is_owner;` · `string owner_identity;` · `int net_id_low;` · `int net_id_high;`.
  (Se autoserializan vía `JsonSerializer.WriteToString`, MCPClientBridge.c:1039 — no hace falta
  serialización manual.)
- `MCPJob` (tras `net_strategy` :240): añadir los mismos cuatro campos
  `bool is_owner; string owner_identity; int net_id_low; int net_id_high;`
  para arrastrarlos por las fases.
- `MCPArgs`: **NO añadir campos**. S0 reutiliza `throttle` (:20), `duration` (:21) y `mode`
  (:26, string) para la conmutación de suppression. Verifícalo y no toques el DTO de args.

### Paso 2 — Handler `drive_probe_client` (MCPClientBridge.c)

Archivo: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPClientBridge.c

a) **Constantes** (junto a las `CAMERA_*` :77-89): define fases y timeouts ESPEJANDO el server
   (MCPBridge.c:7-18). Nombres propios del cliente para no colisionar, p.ej.
   `DRIVE_CLIENT_PHASE_PREP=0 / IGNITE=1 / DRIVE=2 / SAMPLE=3 / REPORT=4`,
   `DRIVE_CLIENT_TIMEOUT_S=12.0`, `DRIVE_CLIENT_PREP_TIMEOUT_S=5.0`,
   `DRIVE_CLIENT_DEFAULT_SAMPLE_S=2.0`, `DRIVE_CLIENT_MAX_SAMPLE_S=5.0`.

b) **Dispatch** (en `Dispatch`, añade un `else if` ANTES del `else` final :435):
   `else if (command.cmd == "drive_probe_client") { postNow = DispatchDriveProbeClient(command, result); }`.

c) **`DispatchDriveProbeClient(MCPCommand, MCPResult)`** — ESPEJA `DispatchVehicleDriveProbe`
   (MCPBridge.c:490-559) pero con el job runner del cliente:
   - Validación fail-closed (F5-007, espeja MCPBridge.c:496-518): `throttle` rango 0..1 **y**
     finitud (`IsFiniteFloat`, :915) → `bad_throttle`; `duration` 0..`DRIVE_CLIENT_MAX_SAMPLE_S`
     **y** finitud → `bad_duration`. Fuera de rango/NaN ⇒ `result.error`, `return true` (sin tocar
     el coche). Defaults: throttle=1.0, sample=`DRIVE_CLIENT_DEFAULT_SAMPLE_S` si vienen en 0.
   - Busy-check: `if (m_JobRunner && m_JobRunner.Count() > 0)` ⇒ `error="busy"; return true;`
     (igual que `DispatchCameraSet` :457-462).
   - Crea `MCPJob`: `kind="drive_probe_client"`, `phase=DRIVE_CLIENT_PHASE_PREP`,
     `sample_s_target`, `deadline_s = m_JobRunner.GetElapsedS()+DRIVE_CLIENT_TIMEOUT_S`,
     `prep_deadline_s = m_JobRunner.GetElapsedS()+DRIVE_CLIENT_PREP_TIMEOUT_S`, copia
     `tick_*`; `m_JobRunner.AddJob(job)`; `return false`.

d) **Máquina de fases** en `MCP_ProcessJob` (:493): añade
   `if (job.kind == "drive_probe_client") return ProcessDriveProbeClientJob(job);` y los métodos
   por fase, ESPEJANDO MCPBridge.c:1760-1901 con UNA diferencia de resolución del coche y la
   instrumentación de ownership:

   - **PREP** (espeja `ProcessDriveProbePrep` :1760):
     - Resuelve el coche OWNER (esta es LA diferencia vs server):
       `PlayerBase player = PlayerBase.Cast(GetGame().GetPlayer());` (patrón ya usado :624/:967);
       si null ⇒ `error="not_seated"`. `HumanCommandVehicle vc = player.GetCommand_Vehicle();`
       (human.c:1494, heredado por PlayerBase — playerbase.c:3106) si null ⇒ `error="not_seated"`.
       `CarScript car = CarScript.Cast(vc.GetTransport());` si null ⇒ `error="no_vehicle"`. Guarda
       `job.subject = car`.
     - **Gameplay**: llama `RestoreGameplay()` (:982) SIEMPRE en PREP — la simulación del player
       debe estar ON para conducir (una `camera_set free` previa la deshabilita, :627). DESPUÉS,
       si `job.args && job.args.mode == "suppress"` ⇒ `SuppressGameplay()` (:960; deshabilita
       input/HUD pero NO la simulación). Esto implementa "con y sin suppression" del plan §2.
     - **Fixture readiness** (espeja `IsVehicleFixtureReady` MCPBridge.c:1696-1731 — replícalo
       inline o como helper privado del cliente; son lecturas de `CarScript`: `WheelCountPresent()`
       == `WheelCount()`, `GetFluidFraction(CarFluid.FUEL) > 0`, batería/bujía). Si no ready y
       `!job.fixture_attempted` ⇒ `car.OnDebugSpawn(); job.fixture_attempted = true;`. Si ready ⇒
       `phase=IGNITE; return false;`. Si `m_JobRunner.GetElapsedS() > job.prep_deadline_s` ⇒
       `phase=REPORT; return true;` (reporta lo que haya — los campos de ownership siguen siendo
       diagnóstico). NOTA en comentario: `OnDebugSpawn` client-side puede ser no-op por
       server-authority; el gate condiciona también server-side (es belt-and-suspenders).
     - `return false` mientras espera.
   - **IGNITE** (espeja :1818): `car.EngineStart(); job.engine_on_server = car.EngineIsOn();
     phase=DRIVE; return false;`.
   - **DRIVE** (espeja :1833): `SetHandbrake(0); SetBrake(0); if (GetGear()<CarGear.FIRST)
     ShiftTo(CarGear.FIRST); SetThrottle(throttle);` snapshot `start_pos`, `sample_start_s`,
     resetea `speedo_max/pos_delta`, `phase=SAMPLE; return false;`.
   - **SAMPLE** (espeja :1861): reaplica throttle/handbrake/brake/gear cada tick; trackea
     `speedo_max` y `pos_delta = (car.GetPosition()-start_pos).Length()`; captura
     `net_strategy = EncodeNetworkMoveStrategy(car.GetNetworkMoveStrategy())` — **replica
     `EncodeNetworkMoveStrategy` MCPBridge.c:1913 como helper del cliente** (no existe en el
     cliente). Captura la INSTRUMENTACIÓN DE OWNERSHIP (F5-006):
       `job.is_owner = car.IsOwner();`            // pawn.c:194
       `PlayerIdentity oid = car.GetOwnerIdentity();`  // pawn.c:209
       `job.owner_identity = oid ? oid.GetPlainId() : "";`  // gameplay.c:370, null-guard
       `int lo, hi; car.GetNetworkID(lo, hi); job.net_id_low = lo; job.net_id_high = hi;`  // object.c:815
     Cuando `m_JobRunner.GetElapsedS() - sample_start_s >= sample_s_target` ⇒
     `phase=REPORT; return true;`.

e) **`MCP_PostJobSuccess`** (:652): añade rama `if (job.kind == "drive_probe_client")` que llena
   `MCPResult` con `vehicle_fixture_ready, engine_on_server, speedo_max, pos_delta, net_strategy,
   is_owner, owner_identity, net_id_low, net_id_high` + los `tick_*` y `PostResult`. (El fallo y el
   timeout ya los cubren `MCP_PostJobFailure`/`MCP_PostJobTimeout` genéricos :672/:689 — el job
   en error post-PREP debe setear `job.error`.)

### Paso 3 — Whitelist Python (loopback.py)

Archivo: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py

- Añade `"drive_probe_client"` a `CLIENT_COMMANDS` (:29). Eso basta: `peer_for_command` (:40) lo
  enruta al peer `client` y `WHITELISTED_COMMANDS` (:31) lo incluye. NO lo pongas en
  `SERVER_COMMANDS`. NO añadas tool MCP en `server.py`.
- **NO bumpees `MCP_BRIDGE_VERSION`** (MCPMessages.c:1 = "4"): el cmd es aditivo y compatible;
  bumpear rompería el handshake de versión del daemon/gate en curso.

### Paso 4 — Test de routing (Python, offline-verificable)

El lado Enforce NO es compilable offline (sin el compilador del juego) — su gate es in-game y lo
corre Claude después. Lo único verificable por ti es el routing Python.

Archivo: añade un test al patrón existente (mira `tools/tests/test_loopback.py` y/o
`tools/tests/test_x5_loopback.py` y sigue su estilo `unittest` stdlib). Cubre:
- `peer_for_command("drive_probe_client") == "client"`.
- `enqueue_command("drive_probe_client", {...})` con peer por defecto ⇒ 200 + `peer=="client"`.
- `enqueue_command("drive_probe_client", {...}, peer="server")` ⇒ 400 `bad_peer` (mismatch).
- `"drive_probe_client" in WHITELISTED_COMMANDS`.

Comando esperado (desde C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\):
`.venv-mcp\Scripts\python.exe -m unittest discover tests` → todos verdes (incluido el nuevo).

## Restricciones críticas (vinculantes toda la sesión)

1. **Enforce Script puro** en los .c del bridge; **Python stdlib** en loopback/tests (sin deps
   nuevas). Convenciones DayZ de tu AGENTS.md/skills aplican; en especial R11g (condiciones
   booleanas multi-línea → una sola línea) — el server probe ya las respeta, imítalo.
2. **ESPEJAR, no refactorizar**: copia la estructura de fases del server probe al cliente con la
   mínima adaptación (resolución owner + ownership trio + suppression toggle). NO “mejores” ni
   unifiques código compartido server/cliente en esta sesión.
3. **NO toques el `drive_probe` SERVER** (MCPBridge.c: `DispatchVehicleDriveProbe`,
   `ProcessDriveProbe*`, `PostDriveProbeResult`, `IsVehicleFixtureReady`, `EncodeNetworkMoveStrategy`),
   ni los handlers de cámara, ni los 8 handlers existentes, ni el harness, ni `server.py`.
4. **NO implementes Tramo A ni `query_get_in_condition`** ni held-state/`OnTick`-reapply/deadman/
   identidad (§3.1) ni `vehicle_release`. `drive_probe_client` es un job multi-tick autocontenido
   vía el JobRunner (como `camera_set`), NO un estado retenido. Te tentará añadir el held-state
   porque “conducir es continuo” — resiste: eso es G1/Tramo A, sesión aparte.
5. **NO renombres `engine_on_server`** (F5-005); en el cliente significa "engine on (owner)" — es
   un alias documentado.
6. **NO añadas tool MCP ni bumpees la versión del bridge.** S0 se conduce por enqueue raw.
7. **Cite-then-verify**: cada API Enforce que uses ya está citada arriba con `path:line` contra
   vanilla (`IsOwner` pawn.c:194 · `GetOwnerIdentity` pawn.c:209 · `GetNetworkID` object.c:815 ·
   `GetPlainId` gameplay.c:370 · `Car.*` car.c:196-271 · `GetCommand_Vehicle` human.c:1494). Si
   necesitas una no citada, verifícala en el source vanilla bajo `..\scripts\` antes de usarla y
   anótalo en el handoff.
8. **NO improvises fuera del plan**: si algo del plan no encaja (una firma que no existe, una fase
   que no mapea), NO lo arregles al vuelo — anótalo en el Bloque C con `path:line` del plan, aplica
   la interpretación conservadora y márcalo para revisión.
9. **NO self-review / R21** en esta sesión. Implementa, corre el test Python, y para. El gate
   in-game y la R21 vienen después, por separado.

## Output esperado al cerrar (bloques A/B/C/D)

### Bloque A — Archivos creados/modificados
Paths absolutos + tamaño aprox + 1 línea de qué cambió en cada uno.

### Bloque B — Resultado de la verificación
Output LITERAL de `.venv-mcp\Scripts\python.exe -m unittest discover tests` (no parafrasear) +
exit code. Para el lado Enforce: confirma balance de llaves (vía Read, no bash) y un diff/resumen
de las funciones añadidas; declara explícitamente "compilación + gate = in-game, pendiente Claude".

### Bloque C — Hallazgos durante implementación
Por hallazgo: archivo/sección, qué no encajaba, acción (o "marcado"), sugerencia. Si ninguno:
"Sin hallazgos." Incluye aquí cualquier API que tuviste que verificar tú.

### Bloque D — Handoff para el gate S0 (Claude)
- Estado al cierre (qué quedó listo).
- Recordatorio de la secuencia de gate: `world_spawn CivilianSedan` → `vehicle_enter` →
  (condicionar server-side) → `drive_probe_client` mode="" y mode="suppress" → leer
  `pos_delta`/`is_owner`/`owner_identity`/`net_id_*`.
- Deuda/avisos que dejas (p.ej. si `OnDebugSpawn` client-side resultó dudoso).

### Bloque E — Mejoras de skill propuestas
Por cada gotcha/patrón reusable de esta sesión: qué SKILL.md (o CLAUDE.md/AGENTS.md) lo habría
evitado y el parche APPEND-only concreto (skill + sección + texto), citando el hallazgo que lo
motiva. Si ninguno: "Sin propuestas."

===== PROMPT FIN =====
```

## Notas para Claude (NO van en el prompt a Codex)

**Secuencia del gate S0 in-game** (la corro yo tras Codex; 1 rebuild de `DayZ_MCP.pbo`):
1. `world_spawn` CivilianSedan (tool MCP, server).
2. `vehicle_enter` (tool MCP, server) → sentado driver (`seated==true`).
3. Acondicionar server-side: enqueue raw `vehicle_drive` (peer server, ya whitelisted loopback.py:24,
   sin tool) → llena fuel/batería vía `OnDebugSpawn` autoritativo + baseline server inerte
   (`pos_delta≈0` esperado para coche PHYSICS).
4. enqueue raw `drive_probe_client` (peer client) `mode=""` → owner-side, controles activos.
5. enqueue raw `drive_probe_client` (peer client) `mode="suppress"` → owner-side, input suprimido.
6. Veredicto: `pos_delta>1.0` en (4) o (5) ⇒ **PASS** (hipótesis owner se sostiene → Tramo A).
   `pos_delta≈0` en ambos ⇒ **STOP**; el trío `is_owner`/`owner_identity`/`net_id_*` clasifica:
   `is_owner==false` ⇒ ownership no transferido al cliente MCP; `is_owner==true` ⇒ otra causa
   (plan B = input vía `HumanInputController`, plan §8). Reportar con evidencia, NO iterar a ciegas.

**Conducción raw**: enqueue por `POST /enqueue {cmd,peer,args}` + lectura `GET /await?id=` contra
el daemon `:8765` (key del keyfile); patrón en `gate4a_mcp_client.py` (`synthetic_poll` :347).

**Si S0 PASA** → nota para promocionar la invariante owner-authority a la skill `dayz-vehicles`
(entregable #3), en línea con la lección de promoción cross-proyecto.

---

## Receptor + revisión adversarial (2026-06-28, post-Codex)

**Codex implementó S0 limpio** (exit 0): 4 archivos (MCPClientBridge.c, MCPMessages.c, loopback.py,
test_loopback.py). Boundary verificado por mtimes (server probe `MCPBridge.c` + `server.py` intactos).
Tests Python re-corridos por Claude: **121 OK, exit 0**. Código leído línea a línea = espejo fiel del
server probe con los 3 deltas S0 (owner resolution, ownership trio, suppression toggle).

**Revisión adversarial offline (3 lentes, workflow):** veredicto 3/3 = "minor", 0 P1, sin crash, gate
conclusivo por construcción. Un defecto de código (convergió mirror+enforce) → fixeado vía Codex:
- **Suppress lifecycle**: `mode=="suppress"` llamaba `SuppressGameplay()` sin `RestoreGameplay()` al
  cerrar el job → operador bloqueado tras la probe; + flap Restore/Suppress por-tick en PREP.
  Fix: setup de gameplay one-shot en PREP + `RestoreGameplay()` en success/failure/timeout SOLO para
  `drive_probe_client`. (No corrompía `pos_delta`; era UX-trampa durante el gate.)

### Notas de procedimiento del gate S0 (vinculantes al interpretar — evitan verde/rojo falso)

Derivadas de la lente gate-validity (todas con cita a vanilla). El veredicto NO se lee solo de
`pos_delta`:

1. **owner_identity es ADVISORY en el cliente.** `GetOwnerIdentity()` (pawn.c:209) probablemente
   devuelve null en el peer cliente AUNQUE sea owner (la identity se puebla en el server). NO
   clasificar por `owner_identity==""`. Señales autoritativas = **`is_owner`** (pawn.c:194) +
   **`net_strategy`**.
2. **Anti verde-falso por drift/settle.** `pos_delta` no tiene guarda de velocidad. PASS exige
   **`pos_delta>1.0` Y `speedo_max>0` Y `engine_on_server==true`** (un settle/empuje da
   desplazamiento con speedo≈0).
3. **Orden de celdas.** Correr **`mode=""` PRIMERO** (controles activos). `SetThrottle` es "future
   input" (car.c:201) que bajo PHYSICS el reconcile por-frame puede pisar; `mode="suppress"` zeroea
   ese input → posible rojo-falso. Si `""` mueve y `suppress` no → es la interacción de suppression,
   NO fallo de ownership (confirmar con `is_owner`/`net_strategy`, independientes del input).
4. **Gate por `vehicle_fixture_ready`.** Si `vehicle_fixture_ready==false` ⇒ fallo de ACONDICIONADO
   (no de ownership) — de ahí el paso server-side `vehicle_drive` en la secuencia. No interpretar
   `pos_delta` si fixture no está ready.

### Decision tree del gate (lo aplico al conducir)

```
condicionar server-side (vehicle_drive) -> drive_probe_client mode="" -> [si no mueve] mode="suppress"
  Para cada celda:
    vehicle_fixture_ready==false                    -> INCONCLUSO (acondicionado), no es ownership
    pos_delta>1.0 && speedo_max>0 && engine_on      -> PASS (hipótesis owner se sostiene -> Tramo A)
    pos_delta≈0 && is_owner==true                   -> STOP, causa = otra (no ownership); plan B HID §8
    pos_delta≈0 && is_owner==false                  -> STOP, causa = ownership no transferido al cliente MCP
  Si "" da STOP pero "suppress" da PASS (o viceversa) -> registrar cuál mueve (es el hallazgo de S0).
```

---

## Resultado del Gate S0 in-game (2026-06-28) — **STOP (precondición), hipótesis owner UNTESTED**

Conducido por Claude vía raw `/enqueue`+`/await` a :8765 (daemon broker reusado) + DayZDiag
server+client (filepatching, PBO rebuild `-packonly` con el bridge nuevo, `version_state: ok`
4~1.29.163047). 3 runs con hipótesis distintas (no iteración ciega). Box compartido: la 1ª corrida
fue evictada por la sesión A6_SR2M (regla box-exclusivo); el usuario liberó el box y se repitió.

**Evidencia (CivilianSedan, net_strategy=2=PHYSICS):**

| Paso | Peer | Resultado |
|---|---|---|
| `world_spawn CivilianSedan` | server | ok, found=1 @ (6066.6, 1931.7) |
| `vehicle_enter` | server | **seated=1, seat=driver** |
| `vehicle_drive` (condición+baseline) | server | `vehicle_fixture_ready=1, engine_on=1, speedo_max=9.41, pos_delta=2.29, net_strategy=2` |
| `drive_probe_client` mode="" | client | **error=`not_seated`** (×6 sondas en 3 runs, hasta 15s settle) |
| `drive_probe_client` mode="suppress" | client | **error=`not_seated`** |

**Hallazgo F1 (precondición — STOP):** `vehicle_enter` (server `StartCommand_Vehicle`) sienta al
player driver **server-side** (el server `vehicle_drive` lo confirma: resuelve el coche por
`GetFirstHuman().GetCommand_Vehicle()` y conduce). Pero en el CLIENTE,
`GetGame().GetPlayer().GetCommand_Vehicle()` devuelve **null** → `drive_probe_client` aborta en PREP
con `not_seated`. **El asiento forzado server-side NO propaga el vehicle-command al cliente.** Esto
**valida la duda explícita del plan §2** ("no hay evidencia de que vehicle_enter —server— transfiera
el ownership al cliente MCP"). La hipótesis owner-authority queda **UNTESTED** (bloqueada por la
precondición; el trío is_owner/owner_identity/net_id nunca se captura porque PREP falla antes).

**Hallazgo F2 (premisa del plan en entredicho):** el `drive_probe` **SERVER movió un coche PHYSICS**
(`pos_delta=2.29`, `speedo=9.41`, `net_strategy=2`). El plan §0/§2 asumía el server **inerte** para
coches PHYSICS (por `IsServerOrOwner`). Pero `IsServerOrOwner()` en el server hace
`if (isServer || ...) return isServer;` → **siempre true en el server** (carscript.c:3220-3231), así
que el server SÍ simula. **Caveat (R3):** confound de single-box — el cliente nunca tomó ownership
del coche (no estaba sentado desde su vista), así que el server retuvo autoridad. En un dedicado real
con cliente remoto dueño, el server podría ser inerte. **No verificado en 2 máquinas.**

**Veredicto: STOP.** No construir Tramo A todavía. Dos caminos a adjudicar (next session):
1. **Probar la hipótesis owner de verdad**: el cliente debe sentarse ÉL MISMO
   (`StartCommand_Vehicle` client-side en `MCPClientBridge`, capacidad nueva), no depender del
   asiento server-side. Solo entonces `drive_probe_client` puede resolver el coche y medir owner-side.
2. **Reexaminar F2**: si el server ya conduce coches PHYSICS (confirmar en dedicado 2-máquinas o
   aceptar el single-box como entorno de test), quizá Tramo A (owner-side client) **sobra** y el
   `vehicle_drive` server existente basta para el bucle de drivability → simplifica Fase 5.

**Entregable #3 (promoción owner-authority a `dayz-vehicles`): NO procede** — S0 no dio PASS; la
invariante no está establecida (sigue siendo hipótesis untested).

**Infra dejada lista (reusable):** `tools\run-s0-gate.ps1` (build-aware launcher contra el daemon,
sin time-freeze, deja procesos vivos), driver raw `scratchpad\s0_gate_driver.py`. PBO desplegado con
el bridge nuevo. Daemon :8765 reiniciado con `loopback.py` nuevo (whitelist `drive_probe_client`).

---

## Investigación F2 (2026-06-28) — VEREDICTO: artefacto single-box, plan se sostiene, MANTENER Tramo A

4 analistas de source + 2 jueces adversariales (cite-then-verify R2). **Ambos jueces: `unified-root-holds`.**

**Cadena verificada (path:line vanilla):**
- `Transport extends Pawn` (transport.c:53, bajo `FEATURE_NETWORK_RECONCILIATION`) → `Car` (car.c:98) →
  `CarScript` (carscript.c:170). Por tanto el `IsOwner()` de `IsServerOrOwner()` (carscript.c:3222-3231)
  es el **ownership de red del COCHE** (Pawn). Glosario: Owner = el cliente que controla el pawn
  (pawn.c:5-8); `IsAuthorityOwner()` = "authority AND has no owner" (pawn.c:199-200).
- `IsServerOrOwner()` se consume SOLO en teardown/fluidos (carscript.c:822/850/986), **nunca gatea
  throttle/steer/gear**. El único `SetThrottle` de script (carscript.c:1377) está muerto en producción
  (`#ifdef DIAG_DEVELOPER`, m_eDebugMode). El throttle→física es **proto native** (car.c:202) y lo
  aplica el simulador del cuerpo = el owner.
- Get-in real: `ActionGetInTransport.Start()` (método compartido cliente+server, NO `OnStartServer`)
  llama `m_Player.StartCommand_Vehicle` en el Human del CLIENTE + reserva asiento vía juncture
  (actiongetintransport.c:82-98, 141-161). `vehicle_enter` del MCP lo hace **solo server-side** → el
  cliente nunca crea su `HumanCommandVehicle` → `GetCommand_Vehicle()==null` (F1).
- No existe `SetNetworkOwner`/`SetOwner` en script: la transferencia de ownership es 100% nativa
  (CrewGetIn/StartCommand_Vehicle/Synchronize proto native). Un `StartCommand_Vehicle` server-only no la
  reproduce.

**Conclusión:** F1 y F2 = **una raíz** (el cliente nunca tomó ownership). Coche sin dueño =
authority-owner = simulado por el server → server `SetThrottle` lo mueve (F2, artefacto single-box).
En un dedicado real con cliente que hace el get-in y se vuelve owner, el server probe sería inerte y
manda el owner-side → **Tramo A sigue siendo necesario**.

**Corrección al plan (premisa falsificada en su literal):** §0/§2 decían "server probe INERTE bajo
PHYSICS". Real: "server mueve un coche SIN dueño; se vuelve inerte cuando un cliente lo posee". La
premisa original era teoría (de `actionstartengine.c`, guard sobre EngineStart no SetThrottle) +
medición B3 débil (`fase1-verdict.json` B3: pos_delta=0.136m, "inconclusive"). F2 (2.29m) es la 1ª
medición de movimiento real server-side — explicada por no-owner, no por refutar el modelo.

**Incertidumbre residual (R3):** el paso final ("el simulador nativo corre en el owner; un seat
forzado server-side NO transfiere ownership") vive en C++ nativo no verificable desde script.
Alta-confianza-por-inferencia, no byte-verified.

**Dos confirmaciones disponibles (ninguna ejecutada — la barata necesita código):**
1. **Barata (single-box, decisiva para esa pata):** añadir lectura de ownership del coche
   (`IsOwner()`/`IsAuthorityOwner()`/`GetOwnerIdentity()`) al server probe; predicción del modelo:
   `IsAuthorityOwner()==true` + `GetOwnerIdentity()==null` mientras el server lo mueve. (Pequeño cambio
   en MCPBridge → Codex.)
2. **Decisiva (producción):** test 2-máquinas (dedicado + cliente remoto real que hace get-in →
   se vuelve owner), luego correr el server probe → predicción: inerte (pos_delta < umbral).

**Rediseño de Tramo A (input nuevo):** la precondición no es `vehicle_enter` server-side sino que el
**cliente tome ownership** (get-in client-side / `StartCommand_Vehicle` en el Human del cliente desde
`MCPClientBridge`). Sin eso, `drive_probe_client`/los 5 verbos nunca resuelven el coche.

**Invariante cross-proyecto candidata a `dayz-vehicles`** (verificada source + in-game): *"Un
`StartCommand_Vehicle` server-side sienta al player solo server-side; el cliente NO obtiene
`GetCommand_Vehicle()` ni el ownership del coche. Bajo PHYSICS el throttle lo aplica el simulador del
cuerpo (el owner); un coche sin cliente-dueño lo simula el server (artefacto de single-box/SP). Para
conducción owner-side el cliente debe hacer el get-in él mismo."*
