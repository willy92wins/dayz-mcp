# Feature Spec: Concurrencia multi-agente sobre una caja DayZ

**Mod / PBO**: `DayZ_MCP` (Python host + bridge Enforce; el reparto de recursos es host-only)
**Date**: 2026-07-26
**Status**: Draft
**Plan**: deriva del diagnóstico de esta sesión; extiende `product-spec.md` grupo H
(coordinación de sesiones) y H11 (`dayz_test_run`/`dayz_test_stop`).

## Context / Why

Hoy el FIFO protege el **lease** (mutaciones, TTL 120 s) pero el recurso realmente escaso —
el **run** (la instancia de DayZ)— no tiene cola: tiene rechazo. Un agente espera su turno de
lease, lo obtiene, y entonces choca contra `active_run_exists` porque otro agente tiene un run
vivo. Bloqueó mediciones reales dos veces en dos días (SUB_BRZ contra LFPowerGrid A1 el
2026-07-25; LFHeli contra A2 E1 el 2026-07-26) y la única salida documentada es que la sesión
propietaria pare su run a mano.

La observación que abarata la solución: **compartir el juego ya funciona**. El dispatch de
comandos se autoriza por el lease, no por la propiedad del run
(`tools/dayz_mcp/loopback.py:336-364`). Lo que no está soportado es compartir el **lanzamiento**.

## Modelo de recursos (el cambio conceptual)

| Recurso | Hoy | Tras esta feature |
|---|---|---|
| `lease` (mutaciones) | FIFO, TTL 120 s, cap 64 | **sin cambios** |
| `run` (la instancia DayZ) | 1 global, rechazo `active_run_exists` | 1 global **con clase y cola**: `SHARED` (N adheridos, stack unión) o `EXCLUSIVE` (1 dueño, stack sellado) |
| `work_zone` (espacio del mapa) | no existe | asignada por adhesión, disjunta entre agentes |

Un run `SHARED` no lo posee una sesión: lo posee el daemon. Un `EXCLUSIVE` conserva
exactamente la semántica de hoy. Clases incompatibles **se encolan**, no se rechazan.

## Acceptance Scenarios *(mandatory)*

1. **Given** no hay run activo y el agente A pide `dayz_test_run(project="SUB_BRZ",
   exclusivity="shared", required_mods=["@SUB_BRZ"])`,
   **When** el run arranca,
   **Then** el run queda `RUNNING_IDLE` con `run_class="SHARED"`,
   `effective_mods ⊇ {"@SUB_BRZ"}` y A figura como participante con una `work_zone` asignada.
   - **Repro in-game**: lanzar desde una sesión; `session_status` + `bridge_status` muestran
     el run compartido y un participante; el cliente DayZ carga y responde a `query_player_state`.

2. **Given** el run `SHARED` del escenario 1 está vivo y el agente B (otra sesión) pide
   `dayz_run_join(project="SUB_BRZ", required_mods=["@SUB_BRZ"])`,
   **When** B se adhiere,
   **Then** **no se relanza nada**, B recibe `joined` con una `work_zone` **disjunta** de la de A,
   y B puede mutar (spawn en su zona) en cuanto obtiene el lease.
   - **Repro in-game**: dos sesiones reales; A y B spawnean un objeto cada una; ambos objetos
     existen simultáneamente y en zonas distintas; ningún proceso DayZ se reinició.

3. **Given** el run `SHARED` lleva `@SUB_BRZ` y el agente C pide adherirse con
   `required_mods=["@LFHeli"]` (no cargado),
   **When** C llama `dayz_run_join`,
   **Then** C recibe `relaunch_required` con la unión propuesta y el presupuesto de arena
   estimado; **no** se relanza sin que C llame explícitamente `dayz_run_drain`.
   - **Repro in-game**: comprobar que el run sigue vivo y que A/B no fueron interrumpidos.

4. **Given** C ejecuta `dayz_run_drain(required_mods=["@LFHeli"])` con A y B adheridos,
   **When** el drain corre,
   **Then** A y B reciben la señal de drain en su siguiente llamada, el run se detiene **una sola
   vez** tras salir los adheridos (o expirar su plazo), y se relanza con la unión
   `{@SUB_BRZ, @LFHeli}`; A, B y C pueden re-adherirse sin relanzar otra vez.
   - **Repro in-game**: 3 sesiones; contar reinicios de DayZ (debe ser exactamente 1).

5. **Given** un run `SHARED` vivo y el agente D pide `exclusivity="exclusive"` (medición
   LFPowerGrid, stack sellado),
   **When** D llama `dayz_test_run`,
   **Then** D **espera en cola** con progreso `queued(position=N)` en vez de recibir
   `active_run_exists`; cuando el `SHARED` queda sin adheridos, D obtiene la caja en exclusiva
   y ningún mod ajeno entra en su stack.
   - **Repro in-game**: el stack efectivo de D se verifica en el log de arranque del servidor;
     debe coincidir byte a byte con el sellado de su policy.

6. **Given** el agente B se adhirió y spawneó objetos en su zona,
   **When** B llama `dayz_run_leave` (o su sesión desaparece y expira),
   **Then** los objetos de B se eliminan, su zona vuelve al pool, y el run sigue vivo para A.
   - **Repro in-game**: `scene_raycast` en la zona de B no encuentra los objetos; el run no se
     reinició y A conserva los suyos.

7. **Given** la unión propuesta excede el presupuesto de script arena,
   **When** se pide el drain,
   **Then** falla **cerrado antes de tocar el run vivo**, con el desglose del presupuesto;
   nunca se relanza a un estado que no compila/carga.
   - **Repro in-game**: no aplica (gate offline); el run existente queda intacto.

### Escenarios de crash-recovery e intervención admin (R8 / DZ-R9)

Obligatorios porque la feature toca `runs.json` (formato persistente), colas async y el
lifecycle de procesos. Un hueco aquí es un bug, no una omisión de documentación.

8. **Given** un drain en curso (run parado, aún no relanzado),
   **When** el daemon muere en esa ventana,
   **Then** al arrancar la generación siguiente no queda ningún run "medio relanzado":
   el estado observable es `EXITED` o `UNRECONCILED` explícito, nunca un `SHARED` con
   participantes que no existen. Ningún proceso DayZ ajeno se mata para llegar a ese estado.
   - **Repro**: matar el daemon entre el stop y el start del drain; leer `session_status` +
     `doctor` en la generación nueva.

9. **Given** un run `SHARED` con 2 participantes,
   **When** el daemon se reinicia (nueva `daemon_generation`),
   **Then** las adhesiones quedan invalidadas igual que hoy quedan leases y tickets
   (invariante H2/H5), el run sigue siendo adoptable por `run_id` exacto, y un agente que
   creía estar adherido recibe un error estable — no un `joined` fantasma.
   - **Repro**: reiniciar daemon con el juego vivo; reintentar una mutación con la adhesión vieja.

10. **Given** un `runs.json` escrito por el binario nuevo (con `run_class`/`effective_mods`/
    `participants`),
    **When** se hace rollback al binario anterior,
    **Then** el binario viejo lee el registro sin error (claves extra ignoradas,
    `process_lifecycle.py:112-133`) y lo trata como el run exclusivo de siempre; no se corrompe
    ni se borra el fichero.
    - **Repro**: gate offline con fixture de `runs.json` nuevo + código viejo.

11. **Given** un participante cuya sesión desapareció sin `dayz_run_leave`,
    **When** vence su plazo,
    **Then** su zona se libera y sus objetos se limpian **sin** tocar el run ni los demás
    participantes; si la limpieza falla, queda `cleanup_degraded` visible y fail-closed, nunca
    un borrado silencioso ni un kill de proceso.
    - **Repro**: cerrar una sesión en duro con objetos spawneados; observar liberación por TTL.

12. **Given** un estado ambiguo (run vivo sin participantes reconciliables, o drain latcheado),
    **When** el admin interviene,
    **Then** la vía es la existente y auditada — `doctor` para diagnóstico read-only,
    `lifecycle_cli reap` sólo si el run está all-dead, `admin_cli` con TTY para el force-clear —
    y **ninguna** tool MCP ordinaria puede resolverlo por atajo.
    - **Repro**: fixture de cada estado ambiguo; verificar que la tool ordinaria falla cerrado.

**Escalado DZ-R9**: antes de declarar release-safe, invocar `rigorous-data-audit`. El cambio
cruza componentes (manifest persistente + cola + lifecycle de procesos + comandos admin), que es
exactamente el disparador de la regla.

## Success Criteria *(mandatory)*

- **SC-001**: con un run `SHARED` compatible vivo, `dayz_run_join` responde `joined`
  **sin reiniciar ningún proceso DayZ** (PIDs de server/client idénticos antes y después).
- **SC-002**: `active_run_exists` deja de ser un resultado alcanzable por `dayz_test_run` en la
  superficie pública; una petición incompatible o bien se encola (`queued(position)`) o bien
  termina con un código explícito (`relaunch_required`, `arena_budget_exceeded`).
- **SC-003**: una petición rechazada **antes de admisión** no devuelve `run_id`, no marca
  `cleanup_degraded` y no reintenta: hoy devuelve un `run_id` fantasma no registrado
  (`stop` sobre él → `run_not_found`), con `cleanup_degraded=true` y 3 intentos ciegos.
- **SC-004**: dos zonas de trabajo asignadas simultáneamente no se solapan; un `world_spawn`
  fuera de la zona propia se rechaza fail-closed.
- **SC-005**: tras `dayz_run_leave` o expiración de un participante, 0 objetos suyos permanecen
  y el run sigue en `RUNNING_IDLE` mientras quede ≥1 participante.
- **SC-006**: un drain concurrente pedido por 2 agentes a la vez produce **exactamente 1**
  reinicio de DayZ (no 2).
- **SC-007**: 0 líneas `Error` en `script_*.log` atribuibles al stack unión durante un run
  compartido de ≥10 min con 2 agentes activos.
- **SC-008**: el run `EXCLUSIVE` conserva su stack sellado byte a byte — ningún mod procedente
  de la unión aparece en su línea de arranque.
- **SC-008b**: toda medición **sensible a la cadencia o al presupuesto de tick** se ejecuta
  obligatoriamente en `EXCLUSIVE` y el artefacto registra la exclusividad de la caja. Caso real
  que lo motiva: el gate live de `vehicle_trace` quedó RED por `effective_hz=19.96890272042031`
  frente a un umbral de 20 Hz — un déficit del 0,16 % que carga de script ajeno en el mismo tick
  puede producir o enmascarar. Un run `SHARED` no puede acreditar ese criterio.
- **SC-009**: la suite focal existente sigue verde y no aparece ningún test ID nuevo en rojo
  respecto al baseline congelado antes del primer diff.
- **SC-010**: un crash del daemon en cualquier punto del drain deja un estado observable de la
  lista cerrada {`EXITED`, `UNRECONCILED`, run intacto}; 0 casos de `SHARED` con participantes
  inexistentes y 0 procesos ajenos terminados.
- **SC-011**: un `runs.json` con los campos nuevos es leído sin error por el binario anterior y
  tratado como run exclusivo (rollback demostrado en fixture, no argumentado).
- **SC-012**: la limpieza de un participante caído no modifica el run ni a los demás
  participantes; un fallo de limpieza produce `cleanup_degraded` visible, nunca un kill.

## Scope — Out of scope *(mandatory)*

- **Multi-instancia de DayZ** (2+ juegos en puertos distintos). El bridge sondea un único
  `:8765` y `product-spec.md` §Fuera de alcance ya lo declara fuera. Sigue siendo **un juego**.
- **Cola durable de comandos / reanudar el razonamiento de un agente tras caída**: rechazado en
  BUG-046 §2.1 por convertir el daemon en un segundo orquestador. La espera sigue siendo
  request-bound.
- **Cambiar el TTL de 120 s del lease ni la semántica de grants**: BUG-046 está `CORE GREEN`;
  esta feature no toca `session_coordination.py`.
- **Aislamiento de estado global del mundo** (tiempo, clima, freecam): siguen siendo globales;
  se cubren por convención de protocolo (quien los toca los restaura), no por código.
- **Arreglar el gate mixto 2 Claude + 2 Codex de H9**: sigue abierto y es independiente.

## Assumptions

- **ASSUMED**: el stack unión de los mods realmente en uso **cabe** en el script arena del
  servidor. **No resuelto — y hay evidencia en contra**: la medición de LFPowerGrid documenta la
  arena `4_World` al 97 % de 32 MiB *sin* LFPG cargado. Por eso la unión NO es incondicional:
  SC-007 + escenario 7 exigen un guard de presupuesto que falle cerrado antes de relanzar.
  El presupuesto concreto se mide en la Fase 0 del plan, no se asume.
- **ASSUMED**: un participante muerto se detecta por el mismo mecanismo de expiración de
  identidad que ya usa el lease (TTL + identidad de sesión). Se reutiliza; no se inventa uno nuevo.
- **ASSUMED**: `RunRecord.from_payload` tolera claves desconocidas → un binario viejo puede leer
  un `runs.json` nuevo. **Verificado**: `process_lifecycle.py:112-133` lee por `value.get(...)`
  y no rechaza claves extra. Esto habilita el cambio de schema aditivo con rollback.
- **ASSUMED**: las zonas de trabajo pueden derivarse de coordenadas fijas por mapa. Los mapas
  soportados hoy por la superficie pública son `chernarus`, `livonia`, `sakhal`
  (`dayz_test_request.py:283-285`). Un mapa por path arbitrario no recibe zonas: cae a
  `EXCLUSIVE`.

## Forward Contract (R8-extended)

Símbolos que el implementador y las fases siguientes leen. Todos verificados salvo marca.

| Consumer | Symbol it reads | Kind | Verify status |
|---|---|---|---|
| lifecycle start | `active_run_exists` (rechazo a sustituir por cola) | error string | `process_lifecycle.py:874` (+ `:832`, `:871`) |
| manifest | `RunRecord` (run_id, owner_session_id, owner_lease_id, state, label, mod, profiles, mission, processes, launch_*) | dataclass | `process_lifecycle.py:97-110` |
| manifest | `RunRecord.from_payload` tolerante a claves extra | método | `process_lifecycle.py:112-133` |
| manifest | `RUN_STATES` / `_ACTIVE_STATES` (`RUNNING_IDLE` = estado adherible) | frozenset | `process_lifecycle.py:23-26` |
| worker | `WORKER_ERROR_CODES` (cerrado; **no** contiene `active_run_exists`) | frozenset | `dayz_test_worker.py:24-38` |
| worker | `new_run_id` generado antes del start (origen del run_id fantasma) | var | `dayz_test_worker.py:373-378` |
| worker | 3 reintentos ciegos de `invoke_start` | control flow | `dayz_test_worker.py:393-402` |
| worker | `_stop_best_effort` sobre run no registrado → `cleanup_degraded` | control flow | `dayz_test_worker.py:431-439` |
| tool | espera FIFO de lease `max_wait_s=None` + `queue_progress_cb` | llamada | `dayz_test_tool.py:426-434` |
| tool | `_public_mod_list` / `_valid_public_mod` (alias de 1 segmento) | validador | `dayz_test_tool.py:75-93` |
| tool | `_selected_policy` → `bad_project` | validador | `dayz_test_tool.py:65-72` |
| request | `extra_mods` / `base_mods` / `server_mods` / `mission` / `port` | campos | `dayz_test_request.py:263-295` |
| request | `default_base_mods` de la policy sellada | campo | `dayz_test_request.py:264` |
| coordination | `SESSION_TTL_S=120`, `WAIT_MAX_S=30`, `MAX_SESSION_QUEUE=64` | constantes | `session_coordination.py:15-17` |
| coordination | `READ_ONLY_COMMANDS` (lecturas sin lease) | frozenset | `session_coordination.py:18-27` |
| loopback | dispatch autorizado por `coordination.authorize(...)`; el ownership manejado es lease/reservation y **no hay ninguna referencia a run** en el camino | control flow | `loopback.py:326-333`, `:356-402` |
| runbook | protocolo de sesión compartida que los agentes leen | doc | `ObsidianVault/AI/20_Runbooks/dayz-mcp-agent-session-protocol.md` |
| product-spec | grupo H (H1-H12) + H11 superficie `dayz_test_run` | spec | `product-spec.md` §H |

**Símbolos nuevos que esta feature crea** (los consumen el runbook, las skills
`dayz-mcp-verify`/`dayz-test-ingame` y los agentes):

| Símbolo | Kind | Estado |
|---|---|---|
| `dayz_run_join(project, required_mods, purpose)` | tool MCP | `[DESIGN]` |
| `dayz_run_leave()` | tool MCP | `[DESIGN]` |
| `dayz_run_drain(reason, required_mods)` | tool MCP | `[DESIGN]` |
| `dayz_test_run(..., exclusivity)` | parámetro nuevo | `[DESIGN]` |
| `RunRecord.run_class` / `.effective_mods` / `.participants` | campos aditivos | `[DESIGN]` |
| `arena_budget_exceeded` / `relaunch_required` / `drain_in_progress` | error codes | `[DESIGN]` |
| `work_zone` (centro + radio por participante) | DTO | `[DESIGN]` |

## Verification plan

| Criterio | Verificación | Dónde |
|---|---|---|
| SC-001 | comparar PID/creation-time de server+client antes/después del join | in-game (batch R5) |
| SC-002 | fixtures de la tool: incompatible → `queued`/código explícito; grep de que `active_run_exists` no cruza la frontera pública | offline |
| SC-003 | test TDD: rechazo pre-admisión → sin `run_id`, sin `cleanup_degraded`, 1 solo intento | offline |
| SC-004 | test de asignación de zonas (solapamiento) + rechazo de `world_spawn` fuera de zona | offline + in-game |
| SC-005 | `scene_raycast` sobre la zona liberada; `session_status` con el run vivo | in-game (batch R5) |
| SC-006 | gate multiproceso: 2 drains concurrentes → contar arranques del ejecutable | offline (fake) + in-game |
| SC-007 | scan de `script_*.log` post-sesión | offline (post-test) |
| SC-008 | diff de la línea de arranque del server contra el sellado de la policy | offline |
| SC-009 | suite focal + manifest de IDs contra baseline congelado | offline |
| SC-010 | inyección de crash en cada punto del drain (matriz, patrón BUG-046) | offline |
| SC-011 | fixture `runs.json` nuevo leído por el binario anterior | offline |
| SC-012 | fixture de participante caído; verificar run y peers intactos | offline + in-game |
| Escenario 7 | fixture con unión sintética que excede presupuesto → falla sin tocar el run | offline |
| Escenarios 8-12 | matriz de crash/rollback/admin, read-only sobre estados ambiguos | offline |

## Resultado del checklist de spec (Step 2)

15/16. **CHK004 con desviación documentada**: el escenario 7 (presupuesto de arena) no lleva
repro in-game porque es un gate offline por diseño — falla *antes* de tocar el run vivo, así que
un repro in-game sería contradictorio con el propio criterio. Los escenarios 8-12 se verifican
por matriz de inyección de fallo, no por repro manual.

Dos fallos encontrados y corregidos durante el checklist, no antes:
- **CHK011/CHK012**: `loopback.py:336-364` estaba citado desde un grep sin abrir el archivo —
  justo la cita que sostiene "compartir el juego ya funciona" (R2.1: un snippet no es un hecho).
  Verificado y re-anclado a `:326-333`/`:356-402`.
- **CHK016**: faltaban por completo los escenarios de crash-recovery e intervención admin pese a
  que la feature toca formato persistente y colas async. Añadidos como escenarios 8-12 +
  SC-010..012, con escalado explícito a `rigorous-data-audit`.

## Riesgos que el plan debe cerrar (no son gaps del spec)

1. **Presupuesto de arena** — el riesgo #1. Un stack unión puede no cargar. Guard obligatorio.
2. **Contaminación cruzada de mods** — un error de script del mod de B puede tumbar la sesión de
   A. Mitigación mínima: el drain declara qué entra, y SC-007 lo mide.
3. **Regresión de cleanup** — relajar `_stop_best_effort` puede **filtrar runs** (procesos DayZ
   vivos sin registro). El discriminador debe ser un conjunto cerrado de rechazos de
   pre-admisión; ante cualquier duda, conservar el comportamiento conservador de hoy.
4. **Baseline global no verde** — el HANDOFF reporta `discover 1227: 11 failures, 31 errors`.
   El plan debe congelar y fingerprintar el baseline antes del primer diff (igual que BUG-046).

## Preguntas CERRADAS — ratificadas por el usuario el 2026-07-28

Las tres las había adjudicado Codex en la Fase 0 y quedaron sin ratificar hasta hoy. Evidencia que
sostuvo cada decisión: `reports/2026-07-28-q1q3-evidencia-para-ratificar.md`. Registro formal en
`decisions/decision-log.md` D-32.

- **Q1 — CERRADA**: el run `SHARED` muere tras **120 s sin participantes**. Se reutiliza
  `SESSION_TTL_S = 120.0` (`session_coordination.py:15`), que ya gobierna leases (`:464`, `:1229`,
  `:2498`) y tickets (`:1071`, `:1986`, `:3387`); no se introduce constante temporal nueva. Si el
  coste de espera de un `EXCLUSIVE` resulta caro, la palanca es Q2, no bajar este TTL — bajarlo
  afectaría también a leases y tickets.
- **Q2 — CERRADA**: un `EXCLUSIVE` en cola puede desalojar a un `SHARED` **sólo con 0
  participantes**; con ≥1 espera. **Condición vinculante**: la comprobación de "0 participantes" y
  el desalojo deben **evaluarse y consumirse atómicamente**. Sin esa atomicidad existe una ventana
  en la que entra un adherido entre el check y el desalojo, y se mataría un run que acaba de ganar
  participante.
- **Q3 — CERRADA**: tabla congelada con **cota superior**, fail-closed ante `UNKNOWN_*`.
  **Condición vinculante**: el guard debe exigir un **hash de contenido por mod** y fallar cerrado
  si no coincide con el registrado. Justificación medida: la unión de los 8 proyectos reales
  consume **9,13 % del cap** en cota superior (mediana por mod 0,18 %), así que el margen del ±30 %
  es irrelevante para este uso; el riesgo real es que la tabla envejezca en silencio, porque la
  Fase 0 no calculó hashes de PBO y el propio artefacto se declara "foto fechada, no autoridad
  perpetua".
