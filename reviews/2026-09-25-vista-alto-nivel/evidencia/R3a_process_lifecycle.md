<!-- Salida CRUDA de una lane Qwen3.8-Flash-Next (GX10), sin editar. NO es el informe: puede contener errores.
     Verificación mecánica del receptor (EVID literal a ±10 líneas en HEAD 57269d2): == R3a_process_lifecycle.md findings: 10 {'EXACT': 10}
     Veredicto del receptor: ver ../INFORME.md §4 y §8. -->



## Resumen (máximo 8 líneas)
- La máquina visible usa 6 estados: `STARTING`, `RUNNING`, `RUNNING_IDLE`, `STOPPING`, `EXITED` y `UNRECONCILED`.
- La autoridad visible es `session_id`, `lease_id` y `reservation_id`, pero viaja como tupla anónima.
- La persistencia es `runs.json` v1 con escrituras atómicas y poda de `EXITED` al cargar.
- Las transiciones están custodiadas por `_operation_lock`, reservas de lease, auditoría, cuarentena y probes de SO.
- La idempotencia por `launch_operation_id` + sha evita duplicar un spawn en reintentos.
- La reconciliación/reap/admin cubre kill forzado, daemon reiniciado, procesos muertos y manifest drift.
- Windows domina lanzamiento, cierre de ventanas, imágenes de proceso y source pin.
- Simplificar con tipos nombrados, funciones con responsabilidad única y capacidades abstraídas; no con borrar garantías.

## A. Máquina de estados

| Estado / origen | Destino | Qué se persiste | Quién lo provoca | Líneas |
|---|---:|---|---|---:|
| ABSENT | STARTING | `RunRecord` nuevo, estado `STARTING`, owner/lease, operación/sha si viene de launch nuevo | Cliente autenticado con lease a través de `start_run` | 2571, 2594-2599 |
| ABSENT | STARTING | Igual que row anterior, para run nueva | Cliente con lease | 2561-2599 |
| RUNNING | STARTING | Copia de run existente con estado `STARTING` durante extensión | Cliente con lease | 2421-2435, 2571-2599 |
| STARTING | RUNNING | Añade `ProcessRecord`, estado `RUNNING`, processes no vacío | Cliente con lease tras spawn exitoso | 2771-2813 |
| RUNNING | RUNNING | Sin cambio de estado: retry idempotente devolvido OK | Cliente con lease, misma operación/sha/owner/lease | 2440-2474 |
| STARTING | EXITED | Run sin procesos, owner/lease a null, estado terminal | Cliente en intento fallido donde el handle de arranque está cerrado y no había run previa | 2237-2243 |
| STARTING | UNRECONCILED | Estado no reconciliado; si hay handle no confirmado, queda pendiente | Cliente en intento fallido con handle abierto | 2255-2261 |
| RUNNING | UNRECONCILED | Owner/lease a null, estado no reconciliado | Cliente con `stop_run` si un proceso registrado no se puede clasificar | 3187-3192 |
| RUNNING | STOPPING | Estado intermedio antes de terminar procesos owned | Cliente con `stop_run` | 3259-3261 |
| STOPPING | EXITED | Processes vacío, owner/lease null, estado terminal | Cliente con `stop_run` si termina con éxito | 3357-3361 |
| STOPPING | UNRECONCILED | Owner/lease null, supervivientes vivos, estado no reconciliado | Cliente con `stop_run` si falla terminate o entra cuarentena a mitad | 3279-3325 |
| RUNNING_IDLE | RUNNING | Owner/lease asignados, estado `RUNNING` | Cliente con `adopt_run` | 3605-3609, 3648-3652 |
| UNRECONCILED | RUNNING | Owner/lease asignados, estado `RUNNING` | Cliente con `adopt_run` si run huérfana y hay procesos owned | 3605-3609, 3648-3652 |
| RUNNING | RUNNING_IDLE | Owner/lease null, estado idle | Liberación de owner por sesión/lease | 2203-2212, 1119-1124 |
| RUNNING | RUNNING_IDLE | Owner/lease null para todos los RUNNING con owner | Daemon tras reinicio | 1134-1147, 3712-3725 |
| STARTING / STOPPING | UNRECONCILED | Owner/lease null, estado no reconciliado | Daemon en `recover_after_restart` | 1172-1176 |
| activos legacy | UNRECONCILED | Owner/lease null para procesos `legacy-wmi-v1` activos | Carga/puerta de identidad legada | 1085-1098 |
| RUNNING / RUNNING_IDLE / UNRECONCILED | EXITED | Owner/lease null, processes vacío | Reap de run donde todos los procesos son gone/foreign | 45, 4187-4191, 4112-4123 |
| UNRECONCILED / STARTING / STOPPING / RUNNING_IDLE huérfana | RUNNING_IDLE o EXITED | Owner/lease null; supervivientes o vacío terminal | `admin_reconcile` confirmado por admin | 4678-4683, 4740-4746 |
| EXITED | podado | `EXITED` se elimina del manifiesto al cargar, con backup antes de podar | Carga de `RunManifestStore` | 926-945 |
| RUNNING | sin cambio de estado | `launch_acknowledged` pasa a `True` | Cliente con `ack_run` | 2874-2903 |

HECHO visible: la persistencia dura es un JSON v1 cargado y persistido por `RunManifestStore`, con `_persist_locked()` y `atomic_write_bytes` en `tools/dayz_mcp/process_lifecycle.py:844`, `:957`, `:977`.
HECHO visible: el estado se valida en `RunRecord.validate()`; por ejemplo, `RUNNING` exige owner y procesos en `tools/dayz_mcp/process_lifecycle.py:796`.
INFERENCIA [INFERENCIA]: el flujo completo de producción puede incluir más pasos fuera de este fichero, pero el material muestra que este módulo contiene las transiciones durables principales.

## B. Proporcionalidad

| Mecanismo | Garantía principal | Recorte posible | Garantía perdida | Escenario que lo demuestra |
|---|---|---|---|---|
| `launch_operation_id` + `launch_request_sha256` + estado `RUNNING` en `tools/dayz_mcp/process_lifecycle.py:2440-2474` | Un retry de la misma petición no relanza dos corridas | Guardar solo idempotencia por run id, sin sha | Una repetición con el mismo operation id pero otros `argv` podría devolver la run previa como si fuera válida | Agente reintenta un spawn tras timeout y cambia accidentalmente puerto/mod |
| `authority` + `_reservation_active` + `_commit_reserved` + `_finish_committed` en `tools/dayz_mcp/process_lifecycle.py:1722-1778` | Exclusividad y cierre exacto de operación bajo lease | Quitar reservas para una sola sesión | Dos sesiones o un proxy MCP podrían intercalar start/stop sobre el mismo juego | Uso declarado con varias sesiones de agente y proxy a un juego |
| `_quarantined()` fail-closed en `tools/dayz_mcp/process_lifecycle.py:1708-1720` | No actúa cuando el estado retail es desconocido o hay procesos conocidos | Quitar la cuarentena | Lanzar, parar o adoptar a ciegas sobre estado de juego no verificado | Sonda de retail falla o no está disponible |
| Auditoría antes de transición en `tools/dayz_mcp/process_lifecycle.py:2549`, `:3238`, `:3637`, `:4710` | Trazabilidad antes de spawn/kill/admin actions | Hacer auditoría asincrónica o no bloqueante | Un kill o spawn puede ocurrir sin prueba duradera | Incidente donde el agente necesita saber quién y cuándo terminó un juego |
| Reconciliación/reap/admin en `tools/dayz_mcp/process_lifecycle.py:1157-1189`, `:4076-4123`, `:4641-4754` | Converger tras crash, kill forzado, socket huérfano o manifest corrupto | Quitar reap/admin y exigir reset manual | Un DayZ muerto o una transición interrumpida bloquea la caja de forma recurrente | Proceso del juego muere y deja run activa o processes registrados |
| `close_run` con ventanas visibles en `tools/dayz_mcp/process_lifecycle.py:3412-3519` | Cierre amigable de GUI Windows si el juego deja ventanas | Convertirlo en capability opcional | Menos ergonomía para cliente GUI, pero el ciclo de proceso sigue por guard | Servidor dedicado sin GUI o CI sin escritorio |

HECHO verificado por el receptor: `_start_run_reserved` mide 472 líneas y `stop_run` 272. Esto es proporcional a riesgo alto, pero a cambio de legibilidad y mantenimiento.
HECHO verificado por el receptor: el uso real es Windows, un juego y pocas sesiones.
INFERENCIA [INFERENCIA]: la maquinaria de lease, auditoría y reconciliación parece sobredimensionada para un usuario, pero no es claramente innecesaria si varias sesiones comparten un juego a través de MCP.
Quitar una comprobación concreta rompe una garantía concreta:
- Quitar la comparación de sha rompe la garantía de identidad de petición exacta en un retry ambiguo.
- Quitar la reserva rompe la exclusión entre sesiones que comparten la caja de juego.
- Quitar cuarentena rompe fail-closed ante estado OS desconocido.
- Quitar auditoría no impide necesariamente el spawn/kill, pero rompe trazabilidad y elimina un bloqueo operativo si el writer falla.
- Quitar reap deja runs muertas activas y bloquea el box.

## C. Descomposición

Sustituto de la tupla `authority`:
```python
@dataclass(frozen=True)
class LifecycleAuthority:
    session_id: str
    lease_id: str
    reservation_id: str
```
HECHO visible: la autoridad se construye a partir de `decision.owner_session_id`, `decision.lease_id` y `decision.reservation_id` en `tools/dayz_mcp/process_lifecycle.py:1738-1742`.
HECHO verificado por el receptor: se accede por posición 40 veces en este fichero.

Para `_start_run_reserved`, propuesta con nombres:

1. `admit_start_request(client, authority, request) -> StartAdmission | dict[str, object]`
   - Recibe cliente, autoridad y request original.
   - Valida parseo, executable canonical, run existente o nueva, box bloqueado, diag/port y cuarentena.
   - Devuelve `StartAdmission(new_run_id, existing, provisional, registered_pids, requested_port)` o un error dict.

2. `prepare_steam_if_client(parsed, client, authority, steam) -> Preparation | str`
   - Recibe petición admitida, cliente, autoridad y opcional Steam preparation previo.
   - Decide si es cliente GUI, reclama/gatea Steam y devuelve preparation o código de error.
   - No debe contener spawn ni persistencia de run.

3. `persist_provisional_start(existing, provisional) -> bool`
   - Recibe run existente y provisional con estado `STARTING`.
   - Añade o reemplaza manifest y devuelve si la persistencia fue OK.
   - Centraliza el efecto visible en `tools/dayz_mcp/process_lifecycle.py:2596-2599`.

4. `supersede_role_if_needed(client, provisional, role, parsed) -> tuple[int, ...] | str`
   - Solo aplica a extensión de role ya existente.
   - Recibe run provisional, role y witness de reemplazo.
   - Devuelve pids retirados o error.
   - Debe devolver datos, no mezclar estado del caller.

5. `launch_and_identify(parsed_argv, cwd, role) -> tuple[object, ProcessRecord] | str`
   - Recibe argv canónica, cwd y role.
   - Lanza launcher, pide snapshot al guard, valida identity v2.
   - Devuelve handle y `ProcessRecord`, o error.

6. `persist_start_success(client, provisional, record) -> dict[str, object]`
   - Recibe provisional, nuevo process record y cliente.
   - Construye estado `RUNNING`, reemplaza manifest, captura actividad y emite resultado terminal.
   - Termina de cerrar el ciclo iniciado en `tools/dayz_mcp/process_lifecycle.py:2809-2844`.

Para `stop_run`:

1. `resolve_stop_target(client, authority, run_id) -> RunRecord | dict[str, object]`
   - Recibe cliente, autoridad y run id.
   - Valida run existente, propietario, lease y estado esperado, con excepción del caso `never_started` visible.
   - Devuelve run o error.

2. `classify_stop_processes(client, run) -> StopClassification`
   - Recibe run y cliente.
   - Clasifica owned/gone/foreign/unknown con `guard`.
   - Devuelve pids owned/gone/foreign y unknown reason.

3. `audit_stop_plan(client, owned, gone, foreign) -> bool | dict[str, object]`
   - Recibe clasificación.
   - Emite auditoría antes de terminar.
   - Si falla, devuelve rejection o error, no muta estado.

4. `terminate_stop_processes(client, run, owned) -> StopOutcome`
   - Recibe owned a terminar.
   - Ejecuta `guard.terminate`, controla cuarentena a mitad.
   - Devuelve `StopOutcome(terminated, survivors, reason, degraded)`.

5. `finalize_stop_run(client, run, outcome) -> dict[str, object]`
   - Recibe run y resultado de terminación.
   - Decide si persistir `EXITED` o `UNRECONCILED`.
   - Emite terminal outcome con el estado realmente persistido.

HECHO visible: ya existe una semilla de este patrón en `_commit_retirement` y `_terminal_outcome`, en `tools/dayz_mcp/process_lifecycle.py:1360-1372` y `:2097-2114`.

## D. Portabilidad

HECHO visible: el launcher depende de Windows para estilos de ventana en `tools/dayz_mcp/process_lifecycle.py:1448-1454`.
HECHO visible: la advertencia de source pin solo se ejecuta si `os.name == "nt"` en `tools/dayz_mcp/process_lifecycle.py:1267-1272`.
HECHO visible: `close_run` depende de top-level windows y `WM_CLOSE` en `tools/dayz_mcp/process_lifecycle.py:3487-3500`.
HECHO visible: las imágenes DayZ están hardcodeadas como `.exe` en `tools/dayz_mcp/process_lifecycle.py:53-55`, y el executable canónico usa `DayZDiag_x64.exe`, `DayZ_BE.exe` y `DayZ_x64.exe` en `:1789-1800`.
HECHO visible: `_public_profiles_label` normaliza hacia path Windows con backslash en `tools/dayz_mcp/process_lifecycle.py:120`.

Para lanzar un servidor DayZ dedicado Linux o remoto, faltarían estas abstracciones:

1. `ProcessLaunchBackend`
   - `spawn(argv, cwd, window_style) -> ProcessHandle`.
   - Windows mantiene `CREATE_NO_WINDOW` / `STARTUPINFO`.
   - Linux o remoto devuelve un handle de proceso o una referencia a proceso remoto.

2. `ProcessIdentityBackend`
   - `snapshot(pid) -> ProcessIdentitySnapshot`.
   - Debe poder reportar `process_not_found`, identity v2 y terminación confirmada sin depender de API Windows concreta visible aquí.

3. `NetworkProbeBackend`
   - `udp_holders() -> list[PortHolder]`.
   - Debe leer la tabla UDP host para los puertos DayZ y los holders usados en `tools/dayz_mcp/process_lifecycle.py:4271-4316`.

4. `DayZBinaryPolicy`
   - En lugar de nombres `.exe` hardcodeados, permitir nombres de binarios Linux o remotos.
   - Debe conservar el criterio: solo DayZ images u ocupación del puerto pedido bloquean el lanzamiento.

5. `WindowCloseBackend`
   - Optional no-op para servidor sin GUI.
   - Solo Windows GUI lo implementa.

6. `SteamPreparationGate`
   - Debe ser no-op o capability opcional para servidor Linux/remoto sin cliente Steam Windows.

Para CI sin juego, hacen falta dobles de estas capacidades:

- `launcher`: doble que devuelve un handle fake con `pid` válido.
- `guard`: snapshot y terminate controlados por escenario.
- `retail_probe`: necesario si se quiere que `_quarantined()` no sea fail-closed; un doble debería devolver `known=True` y `processes=[]`.
- `diag_probe`: si falta, `_diag_snapshot()` devuelve desconocido en `tools/dayz_mcp/process_lifecycle.py:4247-4257`.
- `port_probe`: puede devolver escaneo vacío conocido para pruebas donde no importa puerto; si no hay probes, no debe fallar por eso.
- `manifest`: `RunManifestStore` con paths temporales y checkpoint en memoria.
- `coordinator`: doble que autorize/reserve/commit/finish.
- `audit`: callback controlado que puede devolver éxito o fallo deliberado para probar fail-closed.

HECHO visible: `_quarantined()` devuelve `True` si `retail_probe is None` en `tools/dayz_mcp/process_lifecycle.py:1709`.
INFERENCIA [INFERENCIA]: para CI limpia, el diseño necesita un modo de plataforma con sondas fakes o una política explícita de “sin SO real”, porque hoy varios flujos de arranque/paro requieren probes conocidos.

## E. Propuestas

| Prioridad | Propuesta | Coste | Riesgo | Cómo verificar que no se pierda garantía |
|---:|---|---:|---|---|
| 1 | Sustituir `tuple[str, str, str]` por `LifecycleAuthority` | S | Bajo | Buscar todos los accesos a `authority[...]`, sustituir por atributos; añadir type-check o test que construya autoridad desde un `AuthorizationDecision` |
| 2 | Extraer transiciones a helpers nombrados: `mark_starting`, `mark_running`, `mark_idle`, `mark_stopping`, `mark_unreconciled`, `mark_exited` | S/M | Medio | Snapshot de estados antes/después para cada transición; invariantes: EXITED sin owner/processes, RUNNING con owner/processes, RUNNING_IDLE sin owner |
| 3 | Partir `_start_run_reserved` en pipeline con 6 funciones nombradas y probar cada paso aislado | M | Medio alto | Golden tests por etapa: admisión, Steam, provisional, replace, spawn, commit. Comparar manifest antes/después para cada fallo |
| 4 | Partir `stop_run` en resolver, clasificar, auditar, terminar, finalizar | S/M | Medio alto | Matriz de estados: not found, no adopted, unknown, partial kill, full kill, quarantine mid-stop |
| 5 | Introducir `ProcessLaunchBackend`, `ProcessIdentityBackend`, `NetworkProbeBackend` y `WindowCloseBackend` | L | Alto | Suite de dobles para arranque/paro sin SO; si algún día se porta a Linux, mismo contrato con adapter nuevo |
| 6 | Reducir re-checks redundantes a helpers explícitos, sin eliminar fail-closed | M | Medio | Cada helper que conserva semántica anterior debe tener test de excepción: `_guard_quarantined`, `_audit_before_transition`, `_ensure_reservation` |

No propongo borrar todavía:
- idempotencia por operation id + sha;
- reservas de lease;
- auditoría como bloqueo duro de transiciones sensibles;
- reconciliación/reap de runs muertas;
- clasificación owned/gone/foreign/unknown antes de terminar.

## F. Valoración

| Dimensión | Nota | Motivo |
|---|---:|---|
| Corrección | 6/10 | Hay mucho fail-closed y control de identidad, pero no ejecuté el código y la cantidad de efectos laterales hace difícil certificar el orden exacto de persistencia, kill, audit y release. |
| Proporcionalidad | 4/10 | Para una máquina y pocas sesiones, hay mecanismos muy caros; aun así, si hay proxy multi-sesión a un mismo juego, la exclusividad y la reconciliación no son decorativas. |
| Legibilidad | 3/10 | `_start_run_reserved` 472 líneas y `stop_run` 272 líneas son demasiado anchas; la autoridad posicional y las transiciones escritas a mano dispersan la semántica. |
| Testabilidad | 4/10 | Hay injection de `guard`, probes, launcher, coordinator y manifest; pero locks, probes de SO, Steam y cierre de ventanas hacen difícil una suite rápida sin dobles de plataforma. |
| Portabilidad | 2/10 | Dependencias visibles de Windows en flags de proceso, source pin, imágenes `.exe`, normalización de path y `WM_CLOSE`. La lógica de estado es abstraíble, pero la plataforma no lo está del todo. |

## Hallazgos
F01 | P2 | tools/dayz_mcp/process_lifecycle.py:2375 | legibilidad | La función `_start_run_reserved` es demasiado ancha y mezcla admisión, Steam, persistencia, spawn, reemplazo y compensación. | EVID: def _start_run_reserved( | FIX: descomponer en admit, prepare_steam, persist_provisional, replace, launch, commit
F02 | P2 | tools/dayz_mcp/process_lifecycle.py:3139 | legibilidad | `stop_run` concentra autorización, clasificación de procesos, auditoría, terminación, compensación y persistencia terminal. | EVID: def stop_run(self, client: ClientIdentity | FIX: extraer resolve/classify/audit/terminate/finalize
F03 | P2 | tools/dayz_mcp/process_lifecycle.py:1731 | tipado | La autoridad del lease se modela como tupla sin nombre de tres strings. | EVID: -> tuple[str, str, str] | None: | FIX: dataclass `LifecycleAuthority(session_id, lease_id, reservation_id)`
F04 | P2 | tools/dayz_mcp/process_lifecycle.py:1752 | legibilidad | Los accesos posicionales a la autoridad reducen seguridad semántica y dificultan refactors. | EVID: authority[0], authority[1], authority[2], reason, status | FIX: sustituir por `authority.session_id`, `authority.lease_id`, `authority.reservation_id`
F05 | P3 | tools/dayz_mcp/process_lifecycle.py:2316 | corrección | La idempotencia compara operation id, owner, lease, sha y estado RUNNING antes de devolver un launch reutilizado. | EVID: existing.launch_operation_id == parsed.get("launch_operation_id") | FIX: extraer ese check a función con nombre y no eliminarlo
F06 | P2 | tools/dayz_mcp/process_lifecycle.py:2549 | disponibilidad | La auditoría es bloqueante: si el audit writer falla, el start se rechaza con 503. | EVID: if not self._audit("lifecycle_start", client, "lease_valid", "allowed" | FIX: documentar y centralizar la política de auditoría fail-closed
F07 | P2 | tools/dayz_mcp/process_lifecycle.py:1717 | testabilidad | `_quarantined()` es fail-closed si la sonda falta, no se conoce o hay procesos, lo que acopla flujos a probes de SO. | EVID: result.get("known") is not True | FIX: crear plataforma con dobles deterministas o política explícita para CI
F08 | P2 | tools/dayz_mcp/process_lifecycle.py:1448 | portabilidad | El launcher usa comportamiento específico de Windows para ocultar consola. | EVID: if os.name == "nt" and window_style == "hidden": | FIX: mover a `WindowsLaunchBackend`
F09 | P2 | tools/dayz_mcp/process_lifecycle.py:3487 | portabilidad | `close_run` depende de top-level windows y cierre por mensajes de GUI Windows. | EVID: windows = window_close.list_visible_top_level_windows( | FIX: hacer `WindowCloseBackend` opcional y no-op sin GUI
F10 | P1 | tools/dayz_mcp/process_lifecycle.py:4737 | mantenibilidad | Algunas transiciones a estados huérfanos se escriben a mano en vez de ir por un helper de transición duradero. | EVID: run.owner_session_id = None | FIX: centralizar en `mark_idle` / `mark_unreconciled` / `mark_exited`

## LO QUE NO PUDE VERIFICAR
- No puedo verificar ejecución real, concurrencia con sesiones simultáneas, timeouts, ni que no haya races fuera de este fichero.
- No veo la implementación completa de `SessionCoordinator`, `guard`, `retail_probe`, `diag_probe`, `port_probe`, `window_close`, `native_process_snapshot` ni `native_bundle`.
- No puedo confirmar si `admin_reconcile` tiene una barrera TTY real más allá del comentario visible en `tools/dayz_mcp/process_lifecycle.py:349-352`.
- No puedo verificar si los tests actuales cubren todas las transiciones de la tabla; habría que ejecutar la suite y medir cobertura de estados y fallos de auditoría/kill.
- No puedo verificar portabilidad a Linux porque no están en el material los adaptadores nativos Linux.
- No puedo verificar si existen más escrituras a `runs.json` desde otros módulos; este fichero muestra una vía, pero no prueba unicidad de escritura global.
- No puedo contar re-checks, líneas exactas de otras funciones ni porcentaje de cobertura.

## ¿Qué puede estar mal en la premisa de este encargo?
- Se pide evaluar portabilidad a Linux/remoto, pero el contexto verificado dice que solo Windows es soportado y desarrollado; puede ser un requisito hipotético y no una obligación actual.
- Se habla de “reservar, lanzar, adoptar, parar, reconciliar y cuarentena”, pero el material también expone `close_run`, `ack_run`, release de owner, repair de manifest y recovery faults: el ciclo real es más amplio.
- La premisa de sobredimensionamiento puede ser engañosa: una sola máquina con un juego puede necesitar exclusión fuerte si varias sesiones MCP comparten ese juego a través de un worker/proxy.
- La corrección no puede valorarse solo leyendo 4.773 líneas; aquí falta ejecución de tests, pruebas de race, y dobles de OS para saber si el diseño realmente converge.

GATE NO CORRIDO: revisión por API sin herramientas