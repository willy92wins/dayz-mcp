<!-- Salida CRUDA de una lane Qwen3.8-Flash-Next (GX10), sin editar. NO es el informe: puede contener errores.
     Verificación mecánica del receptor (EVID literal a ±10 líneas en HEAD 57269d2): == L2_runtime_core.md findings: 5 {'EXACT': 5}
     Veredicto del receptor: ver ../INFORME.md §4 y §8. -->

## Resumen (máximo 8 líneas)

El núcleo es correcto para 1–3 agentes en una máquina, pero su complejidad es desproporcionada al problema: un solo lock (`_condition`) controla un estado compartido por 6 hilos con I/O externo entremezclada; funciones de 200–464 líneas reimplementan 3 veces el mismo flujo de concesión; WAL, tombstones, box, grace y fault-store añaden mecanismos que no resuelven ninguna amenaza real en 127.0.0.1 con un usuario y dos clientes LLM. La concurrencia es razonable pero frágil (revisión de estado 3–5 veces por rama, 4 semáforos que degradan a 503, `acquire` con dos bloqueos a 50 ms). La escalabilidad es nula: un puerto único, un lock global y 80 dependencias de Windows impiden 5+ agentes, multi-instancia o Linux sin reescribir la arquitectura. La propuesta va en 6 pasos, sin tocar el protocolo de red.

---

## A. Modelo de componentes

**Tabla de procesos y estado:**

| Proceso | Fichero principal | Estado que posee | Protocolo |
|---|---|---|---|
| Proceso anfitrión (Claude/Codex) | (externo) | Sesión MCP del usuario | stdio a Supervisor |
| Supervisor MCP | `mcp_supervisor.py` | Generación de worker en ejecución (`_current`), handshake de inicialización verbatim (`_initialize_line`, `_initialized_line`), ids admitidos (`inflight`) por generación | stdio a anfitrión |
| Worker `--client` (N generaciones) | `control_client.py` + `server.py` (fuera) | Estado de sesión local (`state`: NEW/QUEUED/ACTIVE/RELEASING/CLOSED), token de arrendamiento y ticket de cola | HTTP a daemon (loopback) + archivo de portador (handoff) |
| Portador de arrendamiento (archivo) | `session_handoff.py` | Identidad + token de arrendamiento + id de arrendamiento + generación + hora de escritura | Archivo privado 0600, consume-once, path por env var |
| Hilo de latido de arrendamiento | `lease_supervisor.py` | Próximo vencimiento (`next_due`), señal de fallo (`_failure`), parada (`_stop`) | `asyncio.Event` + 45 s/120 s TTL |
| Daemon HTTP | `daemon.py` + `loopback.py` (fuera) | `ServerState` (cola de comandos, resultados, sesiones), `SessionCoordinator` (estado de arrendamiento/FIFO), manifiesto de ejecución, marca WAL, instantánea de coordinación, registro de enlazado, estado de vecinos, reloj de inactividad | 127.0.0.1:8765 HTTP; disco para estado duradero |
| Juego (DayZDiag / DayZServer) | Externo (mod Enforce) | Estado del juego | HTTP a 127.0.0.1:8765; token `instance` |
| Supervisor de procesos nativos | `native_process_guard.py` (fuera), `orphan_guard.py` (esqueleto) | Observaciones de procesos de Windows, tabla de puertos, identificadores de espera de padre | API nativa de Windows |

**Diagrama de texto:**

```
Anfitrión (Claude/Codex)
    │  stdio (JSON-RPC 2.0)
    ▼
Supervisor (mcp_supervisor.py)
    │  stdio  ─►  Worker gen-1 (control_client.py)
    │           Worker gen-2 (post-reciclado)
    │
    ├── archivo de portador (session_handoff.py) ── gen-N+1
    │
    ▼
Daemon HTTP (daemon.py + loopback.py + SessionCoordinator)
    │  HTTP 127.0.0.1:8765 (clave en query string)
    ▼
Juego (DayZDiag / DayZServer_x64)
```

**Fuente de verdad por estado:**

| Estado | Fuente de verdad |
|---|---|
| Arrendamiento | `SessionCoordinator` en el daemon, serializado por `_condition`. La marca WAL y la instantánea duradera registran la transición para recuperación. |
| Cola FIFO (`_queue`, `_queue_reservations`) | `SessionCoordinator` en memoria; el TTL `SESSION_TTL_S` (120 s) purga tickets abandonados. No se persiste. |
| Cola de box (`_box_queue`) | `SessionCoordinator` en memoria; TTL de arrendamiento 600 s, TTL normal 120 s. No se persiste. |
| Ejecución (DayZ) | `RunManifestStore` (disco) + sondeo del juego. `ProcessLifecycle` reconcilia con `reap_dead_runs` cada 30 s. |
| Autoridad del anfitrión (MCP) | `Supervisor._current` y `inflight` en memoria; el supervisor es la única autoridad sobre qué generación responde. |
| Portador de sesión | Archivo 0600, consume-once; la autoridad final la revalida el daemon en cada authorize. |

---

## B. Corrección y concurrencia

**1. Revisión repetida del estado tras cada I/O externo.** En `acquire`, entre `self._write_initial_grant_audits_locked(lease)` (que suelta el cond) y el final de la función, se comprueba 3 veces que el arrendamiento sigue siendo el activo:

`session_coordination.py:594-601`:
```python
                    if (
                        self._grant_inflight is not grant
                        or self._active is not lease
                        or self._releasing is not None
                        or self._audit_fault is not None
                        or self._lifecycle_recovery_fault is not None
                        or self._repair_fence
                        or self._wal_marker is not None
                    ):
```
Cada I/O externo (`audit`, `_fault_arm`, `_fault_transition`, `_persist_snapshot`, `_fault_clear`) suelta el lock (`session_coordination.py:3802`) y deja una ventana donde otro hilo puede cambiar el estado y obliga a compensar. La función completa de 464 líneas es esencialmente un autómata de compensación manual con 5 checkpoints.

**2. Dos bloqueos de 50 ms dentro del mismo lock de condición en `acquire`.**

- `session_coordination.py:345-362` — cuando hay una reserva pendiente de auditoría:
```python
             if reservation is not None:
                 deadline = time.monotonic() + RELEASE_AUDIT_TIMEOUT_S
                 while reservation in self._queue_reservations:
                     remaining = deadline - time.monotonic()
                     if remaining <= 0.0:
                         return 503, self._payload_with_degradation(
                             {"error": "audit_failed"}, degraded
                         )
                     self._condition.wait(remaining)
```
- `session_coordination.py:423-437` — re-adquisición preferencial tras expirar el arrendamiento:
```python
                grant_deadline = time.monotonic() + (
                    self._cleanup_timeout_s + RELEASE_AUDIT_TIMEOUT_S
                )
                while self._preferential_reacquire_locked(client) and (
                    ...
                ):
                    remaining = grant_deadline - time.monotonic()
                    if remaining <= 0.0:
                        break
                    self._condition.wait(remaining)
```
Ambos bloquean el lock completo a 50 ms por reserva en espera. Con 5–10 clientes concurrentes, `acquire` es un cuello de botella: el 503 a 50 ms y el 503 a 30 s se convierten en timeouts para peticiones ajenas.

**3. 4 semáforos de workers como punto de fallo silencioso.**

- `_cleanup_worker_slots` (BoundedSemaphore 4, `session_coordination.py:272-274`)
- `_release_audit_worker_slots` (BoundedSemaphore 1, `session_coordination.py:277-279`)

`session_coordination.py:2365-2367` (liberación del arrendamiento):
```python
         cleanup_slot = self._cleanup_worker_slots.acquire(blocking=False)
         if not cleanup_slot:
             self._cleanup_worker_saturated += 1
             degraded.append("cleanup_worker_saturated")
```
`session_coordination.py:2555-2559` (auditoría de liberación):
```python
         if not self._release_audit_worker_slots.acquire(blocking=False):
             # Slot exhausted: no worker starts, so callers must apply the
             # not_started side effects (audit_failed + _handoff_audit_failed).
             # A tuple never matches those string checks.
             return "not_started"
```
Y en el propio hilo de auditoría, `self._audit_gate.acquire(blocking=False)` (`session_coordination.py:2731`):
```python
             gate_acquired = self._audit_gate.acquire(blocking=False)
             gate_failed = False
             ...
         if not gate_acquired:
             clear_grant()
             return ("audit_failed" if gate_failed else "busy"), None
```
Un `release()` concurrente con un auditoría lenta devuelve "audit_failed" al cliente aunque la operación sea válida. Es un fallo por contención de semáforo, no de coherencia lógica.

**4. El hilo de auditoría de liberación persiste a `self._condition` a 5 ms por defecto.**

`session_coordination.py:2645-2646`:
```python
         if wait_s == 0.0:
             wait_s = min(0.005, self._cleanup_timeout_s)
         start_error = False
         self._condition.release()
         try:
             try:
                 worker.start()
             except Exception:
                 start_error = True
             else:
                 done.wait(wait_s)
         finally:
             self._condition.acquire()
```
Si `RELEASE_AUDIT_TIMEOUT_S` (0,05 s) se agota por una auditoría lenta, el worker arranca y termina escribiendo dentro de `_condition` (`session_coordination.py:2621-2623`):
```python
                         if result.get("ok") is True:
                             wal_outcome = self._finish_wal_after_publish_locked()
                             if wal_outcome == "ok":
                                 self._clear_handoff_pending_locked()
                                 if not self._persist_snapshot_locked():
```
Mientras el hilo principal ya ha devuelto 202 y sigue procesando peticiones. Correcto pero difícil de razonar.

**5. Dos dueños durante un microsegundo en la reclamación de cabecera del FIFO.** En `_claim_head_from_wait_locked` (`session_coordination.py:2828-2832`), el ticket se saca de la cola y se asigna a `_active` en dos operaciones separadas, ambas bajo el lock. El estado intermedio no es observable, pero es un punto de confusión para mantener la invariante:
```python
             self._queue.pop(0)
             granted_at = self._time_fn()
             lease.granted_at = granted_at
             lease.expires_at = granted_at + SESSION_TTL_S
             self._active = lease
```

**6. La marca WAL y la revisión de coordinación se capturan con `id()` de objetos vivos.** En `_wal_finish_fence_locked`:
```python
         return (
             "grant",
             self._revision,
             id(self._active),
             id(self._releasing),
             id(self._grant_inflight),
             tuple(id(ticket) for ticket in self._queue),
             tuple(id(ticket) for ticket in self._queue_reservations),
```
Depender de `id()` de objetos vivos para comprobar coherencia tras soltar el lock funciona pero es frágil y casi indetectable cuando falla.

**7. Recuperación de fallos de auditoría y de ciclo de vida se restauran a 50 ms.** `RELEASE_AUDIT_TIMEOUT_S = 0.05` (`session_coordination.py:49`) es tan bajo que en práctica nunca espera a una auditoría que ya está en marcha. En la práctica, la auditoría se lanza a 50 ms y el 503 se devuelve antes de que termine, o se degrada a un 202 con flag `cleanup_degraded`.

---

## C. Complejidad proporcional

El problema real: **2 clientes LLM, 1 juego, 1 daemon, 1 máquina local**. El diseño de `session_coordination.py` implementa 5 mecanismos de coherencia superpuestos:

| Mecanismo | Líneas aproximadas | Garantía real aportada |
|---|---|---|
| Cola FIFO con 2 listas (`_queue` + `_queue_reservations`) | ~350 | Justifica 202 vs 503 |
| Marca WAL (arm → transition → finish → clear) | ~400 | Recuperación de 4 fases (audit_failed, snapshot_failed, etc.) |
| Tombstones de operación + tombstones de box | ~200 | Distingue operación cancelada de operación que publica tarde |
| Cola de box separada con su propio claim | ~150 | Permite a un agente "reservar el box" |
| Re-adquisición preferencial + gracia de expiración + `attached_run_probe` | ~100 | Un agente con juego vivo puede readquirir 1 vez preferente |
| Detección de fallos de ciclo de vida + tienda de fallos | ~200 | Un run zombie tras caída de daemon |

Ninguno de estos resuelve una amenaza real en 127.0.0.1 con 1 usuario y 2 clientes LLM.

**Qué se podría eliminar o fusionar:**

| Recorte | Qué se pierde | Riesgo de perderlo |
|---|---|---|
| **Fusionar `_queue` + `_queue_reservations` en una sola lista FIFO** con un flag `pending` en cada ticket | La distinción atómica entre 503 (`coordination_changed` durante auditoría) y 503 (`audit_failed` por reserva agotada) | Bajo: la diferencia solo importa a los tests de autoridad, no a los usuarios |
| **Eliminar la cola de box completamente** (`_box_queue`, `_box_tombstones`, `_box_claiming`, `_purge_box_locked`, 7 métodos de ~100 líneas) | Ninguna, si no hay 50 agentes compitiendo por 1 juego | **Ninguno** a 3 agentes |
| **Simplificar la marca WAL a un solo flag binario** "recovery needed" | Distinguir `audit_failed` de `compensation_failed` de `terminal_transition_failed` | Bajo: en 127.0.0.1, si la auditoría falla, se puede reintentar o devolver 503 sin distinguir la causa |
| **Eliminar re-adquisición preferencial + gracia + `attached_run_probe`** (4 funciones, ~100 líneas) | Un agente cuyo TTL expiró puede recuperar 1 vez antes de que otro agente ocupe la cola | Bajo: a 2-3 sesiones el TTL de 120 s es más largo que cualquier pausa razonable de un LLM |
| **Un solo tipo de tombstone** (un solo diccionario para operaciones y box) | Distinguir una operación de una cola de box en un solo dict | Ninguno a 3 agentes |
| **Eliminar el 4º lock de gate (`_audit_gate`)** | Distinguir 503 (`audit_failed`) de 503 (`session_granting`) en la auditoría de concesión | Bajo: a 2 clientes, el caso es casi imposible de disparar |

**Conclusión:** la proporción de complejidad no se justifica por las garantías que entrega a 2 clientes y 1 juego.

---

## D. Escalabilidad

**1) 5–10 agentes simultáneos.**
`acquire` bloquea el lock a 50 ms por reserva (`session_coordination.py:345-362`) y a 30 s por re-adquisición preferente (`session_coordination.py:423-437`). Con 8 clientes, cada operación de `acquire` bloquea a los otros 7. `_release_audit_worker_slots = 1` (línea 277) significa que **un solo `release()` a la vez puede escribir auditoría**, y cualquier otro `release()` o `wait()` recibe 503 o 202-degradado.
Para 5–10:
- Un solo lock con 4 semáforos de 4/1/4/1 es un cuello de botella de primer orden.
- El re-visor de estado tras 5 I/Os en 464 líneas hace que cada transición sea 3–5× más lenta que si se usara un modelo simple de "intenta, si falla reintenta".
- **Solución:** pasar a un lock por sesión, o a un modelo actor/event-loop con una sola operación por transición.

**2) Varias instancias de DayZ (dos servidores, dos mods).**
`daemon.py` usa un solo `int(port)` (`daemon.py:1416`) con un solo bind de 127.0.0.1 (línea 828–830). El `ServerState` es un singleton sin campo de instancia. `instance_fence.py` gestiona **instancias dentro de un mismo daemon** (cada run de DayZDiag con su `inst=` UUID). No hay dos puertos ni dos coordinadores.
Para 2 DayZ en 2 moddirs, necesitas **dos daemons** en 2 puertos, cada uno con su `SessionCoordinator`. El `control_client` ya es multi-puerto (tiene `port` en `policy`), pero `daemon.py` no es multi-puerto y `loopback.py` no está preparado para dos `ServerState` en el mismo proceso.

**3) DayZ remoto o Linux.**
- **Remote:** `orphan_guard.py` usa `IsProcessInJob`, `CreateToolhelp32Snapshot` (líneas 80–85), `netstat` (`_listener_pid_from_netstat_output`, 345), y `session_handoff.py` asume que ambos procesos comparten un sistema de ficheros local (`tempfile.mkdtemp` 0700). Todo esto es Windows-only y local.
- **Linux:** `orphan_guard.py` no tiene implementación POSIX para `snapshot_udp_port_holders` o `IsProcessInJob`. `pinned_keyfile.py` (esqueleto) usa Win32 (`FILE_FLAG_OPEN_REPARSE_POINT`, 18–26). `spawn_detached` (daemon.py:1718-1728) tiene una rama POSIX, pero no está probada a fondo (el repo dice "Solo Windows").

**4) Un segundo desarrollador humano.**
El `ClientIdentity` compara `pid`, `ppid`, `started_at_utc`, `session_id`, y `_identity_collision_locked` (líneas 2951-2966) **rechaza** una coincidencia parcial de `session_id` con otro `pid`:
```python
         return any(
             existing.session_id == client.session_id and existing != client
             for existing in identities
         )
```
Si dos desarrolladores comparten la misma máquina Windows y ambos usan la misma clave, el coordinador trata los 2 procesos como una sola identidad si heredan el `session_id` de un padre común (Claude/Codex). En la práctica, para 2 usuarios, **necesitas 2 instancias completas**: 2 daemon en 2 puertos, 2 claves, 2 run manifests, 2 rutas de registro de instancia.

---

## E. Estructura

### 1. `acquire` (session_coordination.py:284, 464 líneas)

Tiene 12 salidas (`return`) con 7 condiciones diferentes de 503/409/429 y 5 bloques de compensación idénticos a nivel estructural.

**Descomposición propuesta:**

| Pieza | Recibe | Devuelve |
|---|---|---|
| `_validate_acquire(client, purpose, operation_id)` | ClientIdentity, purpose, operation_id | (400, "bad_operation_id") o None si válida |
| `_check_permanent_faults()` | — | (status, payload) si `_repair_fence`, `_audit_fault` o `grant_audit_failed` están activos; None si ok |
| `_resolve_identity_and_operation(client, operation_id, degraded)` | ClientIdentity, operation_id, degraded | (status, payload) si colisión, operación existente, tombstone, saturación o conflicto; None si ok |
| `_resolve_inflight_grant(client)` | ClientIdentity | (202, ticket_payload) o (409, "session_granting") si hay una concesión en vuelo para este cliente; None si no |
| `_try_preferential_reacquire(client)` | ClientIdentity | (409, "session_releasing") si sigue soltando; None si la re-adquisición completó o no aplica |
| `_claim_idle_slot(client, purpose, operation_id)` | ClientIdentity, purpose, operation_id | (200, payload) si el slot estaba libre y la concesión fue exitosa; None si no aplicaba |
| `_enqueue_ticket(client, purpose, operation_id, degraded)` | ClientIdentity, purpose, operation_id, degraded | (202, ticket_payload) o (429, "queue_full") si el slot estaba ocupado |

Cada pieza mantiene la semántica exacta de las 464 líneas; el body principal es un `for` sobre estas 6 funciones, con un solo bloque de compensación central.

### 2. `_claim_head_from_wait_locked` (session_coordination.py:2665, 263 líneas)

Tiene 5 checkpoints de coherencia, 3 llamadas a `write_with_gate`, 3 a `_latch_wal_fault_locked` y 5 a `_compensate_provisional_grant_locked`. El 90% de las 263 líneas son 3 copias casi idénticas del patrón "state_changed → compensate → return 409".

**Descomposición propuesta:**

| Pieza | Recibe | Devuelve |
|---|---|---|
| `_prepare_claim(ticket)` | _Ticket | `_GrantInFlight` con estado armed y `self._grant_inflight` set; None si el gate de auditoría está ocupado |
| `_audit_claim_prepared(grant, ticket, lease)` | _GrantInFlight, _Ticket, _Lease | True si `write_with_gate` escribió, False si no |
| `_audit_claim_committed(grant, ticket, lease)` | _GrantInFlight, _Ticket, _Lease | True si escribió el "session_granted"; False + compensación si falló |
| `_verify_claim_invariant(grant, lease)` | _GrantInFlight, _Lease | True si 7 condiciones de coherencia se mantienen; False si `_compensate_provisional_grant_locked` la compensó con éxito; 503 si no se pudo compensar |
| `_finish_claim(ticket, lease, grant)` | _Ticket, _Lease, _GrantInFlight | (200, active_payload) si todo OK; 409 o 503 si la coherencia cambió |

### 3. `_activate_server_coordination` (daemon.py:362, 255 líneas)

Construye 13 dependencias encadenadas (coordination_fault_store, lifecycle_recovery_store, manifest, lifecycle, guard) con 6 ramas de recuperación (corrupt manifest, lifecycle_fault, quarantine_legacy, restart_recovery).

**Descomposición propuesta:**

| Pieza | Recibe | Devuelve |
|---|---|---|
| `_startup_stores(paths, deadline, bounded_io)` | RuntimePaths, deadline | `(coordination_store, fault_store, lifecycle_recovery_store)` |
| `_startup_manifest(paths, lifecycle_recovery_store, deadline, bounded_io)` | paths, store, deadline | `RunManifestStore` |
| `_build_session_coordinator(state, audit_writer, stores, deadline)` | ServerState, JsonlAuditWriter, stores | `SessionCoordinator` + callbacks de limpieza y sonda |
| `_build_lifecycle(coordinator, manifest, state, deadline)` | coordinator, manifest, state | `ProcessLifecycle` |
| `_recover_unowned_runs(lifecycle, manifest, deadline)` | lifecycle, manifest | lista de run_ids recuperados, o arm de fallo |

---

## F. Propuestas priorizadas

| # | Propuesta | Coste | Riesgo | Cómo verificar |
|---|---|---|---|---|
| **F1** | **Fusionar `_queue` + `_queue_reservations` en una sola lista FIFO** con un flag `_pending_audit` en cada `_Ticket`. Eliminar 4 métodos (`_queued_grant_audit_failed_locked`, `_queue_capacity_locked`, 2 ramas de while). | M | Bajo: la distinción 503/503 no la usa ningún cliente externo | Test de autoridad que simule auditoría lenta durante 200 ms y verifique que un 2º acquire devuelve 202 con posición 1 |
| **F2** | **Eliminar la cola de box** (`_box_queue`, `_box_tombstones`, `_box_claiming`, `_box_claiming_ticket`, `_box_claimed_at`, 7 métodos). El concepto "box" no tiene caso a 3 sesiones. | M | Ninguna a 3 clientes; alto si en producción hay 50 agentes compitiendo por 1 juego | Test de regresión de `box_wait_touch`: la llamada a este método devuelve `box_ticket=None` y no bloquea |
| **F3** | **Reducir `_claim_head_from_wait_locked` a 100 líneas** extrayendo 4 helper: 1) preparar, 2) auditar prepared, 3) verificar invariantes, 4) compensar. | S | Bajo: refactor a funciones puras con los mismos argumentos | Comparar 6 tests de autoridad concurrente antes/después (los 6 que ejercitan el camino wait→claim→commit) |
| **F4** | **Extraer los 4 puntos de compensación idénticos de `acquire` a `_revoke_grant(grant, reason, requeue_ticket)`**. Cada rama es hoy un bloque de 20 líneas idéntico salvo el `reason` y el `operation_id`. | S | Bajo: es una función nueva con 3 args; 0 semántica nueva | Test de la ruta "coordination_changed" que verifique que el arrendamiento se revoca, la licencia se invalida y el ticket se re-cola correctamente |
| **F5** | **Duplicar el lock de condición en 2**: `_lock_coord` (lease, cola, tombstones, grace) y `_lock_audit` (marca WAL, `_audit_gate`, `_revision`, `_invalid_tokens`). `acquire` coje `_lock_coord` primero, `_lock_audit` en la fase de auditoría, y nunca libera `_lock_coord` para no soltar el lock principal a 50 ms. | M | Medio: la coherencia entre dos locks es más difícil de demostrar que con uno. Riesgo a 5+ clientes: los 2 locks a 50 ms | Test de autoridad que simule una auditoría de 100 ms y verifique que otro acquire no se bloquea a 50 ms por esa reserva |
| **F6** | **Convertir `_release_audit_worker_slots` de 1 a 3 slots** (paralelizar auditorías de liberación). `session_coordination.py:277`. Un `release` a 3 ms a la vez. | S | Bajo: el 503 a 3 ms es raro con 2 clientes | Test de 3 `release()` concurrentes y verificar que 3 devuelven 200 sin 503 |
| **F7** | **Añadir 1 campo `daemon_port: int` a `ClientIdentity` y a `ControlIdentity`.** El coordinador, en el lock de identidad, compara `daemon_port` y rechaza si dos clientes de puertos distintos intentan entrar en el mismo coordinador (que a 2 juegos son 2 puertos). Esto separa 2 instancias **dentro del mismo proceso de supervisión** (dos workers `--client` a 2 puertos, 1 supervisor). Prepara 2-daemon-sin-cambiar-protocolo. | L (estructural, toca 8 ficheros) | Bajo: solo 1 campo nuevo + 1 comparación extra | Test de 2 clientes a 2 puertos simulando 2 coordinadores; verificar que la identidad del puerto A es invisible al puerto B |
| **F8** | **Documentar la semántica de `POLL_INTERVAL_S = 0.05` (server.py:120) y `HEARTBEAT_INTERVAL_S = 45.0` (lease_supervisor.py:12) en un fichero `timing_contract.md` al lado de `daemon.py`.** 2 contratos de tiempo no documentados en el código. | S | Ninguno | Revisión manual del doc; no hay verificación automática |

---

## G. Valoración

| Subsección | Nota | Motivos concretos |
|---|---|---|
| **Coordinación y arrendamiento** | **6/10** | Correcto en 2 clientes con 1 juego. Frágil a 5. La complejidad no se compensa con las garantías. `acquire` 464 líneas, 3 checkpoints, 4 bloqueos a 50 ms dentro del mismo lock de condición (`session_coordination.py:345-362`, 423-437). |
| **Daemon** | **7/10** | La secuencia de arranque es coherente (descubrimiento → elección → migración de identidad → bind → recuperación de ciclo de vida). Bien pensado: no re-matar un daemon sano (`_bind_with_reclaim`, daemon.py:788-870). Falta: 3 timeouts de 40 s/30 s/5 s con 5 ramas de error, y `_ensure_identity_migration` (daemon.py:187) depende de 3 ficheros de Windows. |
| **Cliente y supervisor** | **7/10** | `control_client.py` es correcto con 5 estados, 2 locks (`_state_lock` threading, `_transition_lock` asyncio) y 5 códigos de error de recuperación (session_transition_conflict, daemon_unavailable, daemon_response_ambiguous). El supervisor no lee el token (session_handoff.py, 25-27) y eso es correcto. Falta: 0 tests de `session_acquire_wait` con 2 clientes que entran a la cola a la vez. |
| **Robustez ante fallos** | **7/10** | La marca WAL, la recuperación de auditoría, la recuperación de ciclo de vida y la sonda de ejecución conectada (`attached_run_probe`) cubren los 5 modos de fallo posibles. La re-adquisición preferencial y la gracia (líneas 2164-2260) son correctas. La falta: 4 semáforos con 4/1/4/1 que a 3 clientes liberaciones simultáneas devuelven 503/202-degradado, no 200 (session_coordination.py:2365-2367, 2555-2559). |
| **Legibilidad y mantenibilidad** | **4/10** | `acquire` 464 líneas con 12 returns, 5 ramas de compensación idénticas, 2 bloqueos a 50 ms, 6 variables de control (`degraded`, `collision`, `reservation`, `existing`, `inflight`, `active`) que se mezclan a lo largo de toda la función. 3 funciones con 3+ bucles anidados de 6+ niveles. `daemon.py:_activate_server_coordination` tiene 6 niveles de anidamiento a 255 líneas. El código funciona y los comentarios son honestos, pero 500 líneas de función sin un diagrama de estados visible hacen que cualquier cambio requiera 4h de revisión de coherencia. |

---

## Hallazgos

F01 | P1 | tools/dayz_mcp/session_coordination.py:345-362 | concurrencia | `acquire` bloquea 50 ms por reserva pendiente de auditoría dentro del lock de condición, con 5 clientes es un cuello de botella serializante | EVID: `deadline = time.monotonic() + RELEASE_AUDIT_TIMEOUT_S` | FIX: Pasar a 1 sola cola con flag pending o mover a lock de auditoría independiente

F02 | P1 | tools/dayz_mcp/session_coordination.py:2555-2559 | concurrencia | `_release_audit_worker_slots` (1) serializa auditorías de liberación; un 2º `release()` a 50 ms sin slot devuelve "not_started" y degrada a 202/503 | EVID: `if not self._release_audit_worker_slots.acquire(blocking=False):` | FIX: Aumentar a 3 slots o usar cola asíncrona

F03 | P1 | tools/dayz_mcp/session_coordination.py:2731-2740 | concurrencia | `_claim_head_from_wait_locked` devuelve 503 audit_failed si `_audit_gate` está bloqueado por otra auditoría, a 50 ms | EVID: `gate_acquired = self._audit_gate.acquire(blocking=False)` | FIX: Esperar brevemente o 202+reintentar

F04 | P1 | tools/dayz_mcp/session_coordination.py:2645-2657 | concurrencia | Worker de auditoría de liberación se espera a 5 ms por defecto, pero el worker escribe a `self._condition` a 50 ms tras devolver 202 al cliente | EVID: `if wait_s == 0.0: wait_s = min(0.005, self._cleanup_timeout_s)` | FIX: Esperar a 50 ms o 200 ms (RELEASE_AUDIT_TIMEOUT_S)

F05 | P1 | tools/dayz_mcp/session_coordination.py:594-622 | complejidad | 5 checkpoints de coherencia a 40 líneas en `acquire` son 5 copias de la misma lógica de compensación | EVID: `self._grant_inflight is not grant` | FIX: Extraer `_verify_grant_state(grant, lease, ticket) -> bool`

F06 | P2 | tools/dayz_mcp/session_coordin