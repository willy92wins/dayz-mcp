<!-- Salida CRUDA de una lane Qwen3.8-Flash-Next (GX10, ronda 4, contexto completo), sin editar. NO es el informe: puede contener errores.
     Verificación mecánica del receptor (EVID literal a ±10 líneas en HEAD 57269d2): == W4b_runtime_core.md findings: 0 {}
     Veredicto del receptor: ver ../INFORME.md §4 y §8. -->



GATE NO CORRIDO: revisión por API sin herramientas

## Resumen
El núcleo del MCP está dominado por un coordinador (`SessionCoordinator`) centralizado que controla la adquisición de leases, colas y auditoría en un único gran bloque de código. 
El diseño asume que un solo dueño puede mutar, pero permite explícitamente a otros clientes ejecutar 12 comandos de lectura (`READ_ONLY_COMMANDS`) sin poseer el lease.
Sin embargo, las lecturas de clientes sin lease colisionan con el diseño de aislamiento de bucles locales (`loopback`), ya que el *fencing* a nivel de estado del juego rechaza cualquier encolado si no hay un propietario o dueño del proceso. 
Para viabilizar el uso de lecturas sin lease (P1), el filtro de aislamiento de `loopback` debe separar las lecturas puras de las verificaciones de dueños de corridas de DayZ.
Para ligar el lease a la sesión viva (P2), se requiere una orquestación entre el supervisor de `stdio`, el *carrier* de identidad, y los *heartbeats* de asincronía del propio cliente de control.

## A. Modelo de estado
| Estado | Módulo / Variable | Fuente de Verdad y Persistencia | Cita (Módulo: Línea) |
|---|---|---|---|
| **Lease** | `SessionCoordinator._active` | Memoria (daemon). Persiste a disco mediante el hook `persist_snapshot`. | `session_coordination.py:249`, `session_coordination.py:3432` |
| **Cola FIFO** | `SessionCoordinator._queue` | Memoria. Persiste a disco en el snapshot. | `session_coordination.py:262`, `session_coordination.py:3343` |
| **Reservas** | `SessionCoordinator._queue_reservations` | Memoria. **No persiste**. | `session_coordination.py:263`, `session_coordination.py:3343` |
| **Box** | `SessionCoordinator._box_queue`, `_box_claiming` | Memoria. **No persiste a disco** a propósito. | `session_coordination.py:265-267`, `session_coordination.py:1925-1927` |
| **Binding Peers** | `ServerState._bindings`, `_role_index` | Memoria. Aislamiento de instancias de juego en ejecución (nunca persisten). | `loopback.py:969-970`, `instance_fence.py:3` |
| **Corrida Adoptada** | `ProcessLifecycle` (externo a este material) | Estado duradero de las corridas de juegos (procesos). | `loopback.py:3243-3379` |
| **Capacidades** | `ServerState._peer_caps` | Memoria. Dictaminado al último `poll` acreditado del bridge. | `loopback.py:978`, `loopback.py:2947-2966` |

## B. Corrección
**Riesgo 1: Inconsistencia visual durante mutaciones ajenas.**
* **Escenario:** El cliente A (con lease) lanza un comando de mutación a la cola (ej. `vehicle_control`). El cliente B, sin lease, ejecuta `camera_get`. El coordinador (`session_coordination.py:1261`) acepta la solicitud de B porque es un comando de solo lectura. En la cola de espera, el cliente bridge recibe ambas cosas y B lee el estado de la cámara mientras A la está manipulando a traición.
* **Cita:** `session_coordination.py:1261-1268` autoriza a B a entrar y no exige comprobar si existe una mutación ajena activa.

**Riesgo 2: Bloqueo de sesiones ante caídas.**
* **Escenario:** A tiene el lease. El proceso local de A (MCP) se cuelga o crashea sin poder lanzar un release. A caduca. En el coordinador, si estaba ligado a un juego activo (attached_at_expiry), se dispara un `expiry_grace`. B intenta adquirirlo y es bloqueado por 90 segundos exactos.
* **Cita:** `session_coordination.py:2219` fija `LEASE_GRACE_S`, `session_coordination.py:2210-2222` arma el bloqueo, y `session_coordination.py:2194-2199` (función `_grace_blocks_stranger_locked`) deniega a otros hasta que pasa el tiempo, aunque el dueño original esté muerto.

**Riesgo 3: Falsa admisión para clientes no reclamados.**
* **Escenario:** El cliente A ejecuta un comando de solo lectura (lease_token=None). El coordinador lo autoriza y el lazo (loopback) llama a `_enqueue_fence_target`. Si no hay un binding `BOUND`, cae a una cola de tipo `legacy`.
* **Cita:** `loopback.py:1493` (`return None, self._legacy_queues[peer], None`) deja pasar la solicitud sin exigir dueños de procesos, eludiendo a propósito el aislamiento de corridas.

## C. Proporcionalidad
* **WAL (Write-Ahead Log):** Garantiza que un fallo a medio escribir el snapshot de transición de lease o cola no corrompa la memoria a la hora de un reinicio del daemon. El coste es altísimo (múltiples callbacks, checks de *fences* y reintentos).
* **Snapshot:** Persistencia en disco de la cola y los dueños para que los procesos que sobreviven a los dueños puedan reconstruir sus colas tras el reinicio del daemon.
* **Tombstones (Tácticas y Operaciones):** Conserva temporalmente las peticiones abortadas (`MAX_OPERATION_TOMBSTONES` en 128, con 120s de vida). Previene que un cliente con una operación cancelada (o a medio *flush*) siga reclamando el control del juego en el futuro cercano.
* **Box FIFO:** Control de "asientos" de la máquina para un uso multi-sesión (limitar que 2 clientes usen los puertos UDP). Al no usar persistencia, su complejidad se reduce a listas puras en memoria, pero puede perder su dueño a los 600s (`BOX_CLAIM_TTL_S`).
* **Audit Gate:** Un candado para asegurar que las auditorías a disco no escriban a la vez que un lease está a punto de ser revocado en memoria (previene *ghost sessions* en logs).
* **Fault Store:** Un bloqueante a muerte si la auditoría a disco de `session_coordination` falla (ej. disco corrupto). Si `self._audit_fault` se dispara, toda la API de sesión muere a 503 salvo el `audit-repair`.

## D. Viabilidad de P1 (Lecturas visibles y ejecutables sin lease)
Hoy un comando de solo lectura pasa de largo de la exigencia del `lease_token` en `session_coordination.authorize` (línea 1221). Sin embargo, al intentar llegar a la cola de comandos, es bloqueado por `loopback.py`.
* **Obstáculo:** La función `_enqueue_fence_target` en `loopback.py` filtra la ruta a la cola para los *read-only*.
* **Ruta a tocar:** `loopback.py:1475-1493`. Para una lectura sin lease (o con un cliente desconocido), si detecta un juego activo (`BOUND`), evalúa su estado de dueño a través de `_enqueue_run_rejection` (líneas 1386-1414). Si la corrida está en `RUNNING_IDLE` (sin dueño), rechaza la lectura con `run_not_owned`.
* **Arreglo:** En la línea 1476, para las rutas de `READ_ONLY_COMMANDS` que no usen `lease_token`, se debe saltar la comprobación de dueños si el estado es `RUNNING` o `STARTING`.
* **Riesgo:** Permitir que la IA haga un escaneo del mapa o del entorno (mediante `surface_query`) a mitad de una mutación de otro dueño.

## E. Viabilidad de P2 (Lease atado a la sesión viva)
Actualmente el heartbeat es asincrónico, a cargo del cliente y a intervalos de 45s (definido en `lease_supervisor.py:12`).
* **Implementación:** El *lease* tiene que estar vivo siempre que el cliente del agente esté con el stdio abierto.
* **En control_client.py:** En los métodos como `session_acquire` (línea 491), tras la confirmación del lease, debe lanzar un *loop* de latido que dependa de la supervivencia del socket stdio con el Host (Claude/Codex).
* **En mcp_supervisor.py:** Si el host no puede ejecutar una mutación y se va, debe abortar a su worker hijo (línea 346-363), lo que mataría a su heartbeat al instante.
* **Inactividad / Idle:** El tope de inactividad (timeout) tiene que ser 120s (TTL), y no debe solaparse con un reinicio. Al soltarlo o caducar (y no haber más dueños), `session_coordination` debería emitir un `tools/list_changed` (mediante el supervisor de MCP).
* **Prueba:** Simular a un *worker* que muere abruptamente (matar su árbol de proceso). El supervisor de host debe disparar un `heartbeat` o *release* de forma asincrónica para desalojar a los clientes en cola.

## F. Descomposición
**Función `acquire` (`session_coordination.py:284` a 747 - 463 líneas):**
1. **Validación y purga:** `_purge_operation_tombstones_locked`, `_expire_due()`, y verificación de dueños (`_repair_fence`, `_audit_fault`).
2. **Reclamación de reservas:** Si un cliente ya posee un lease, retorna 200 de forma idempotente (línea 364).
3. **Cola de admisión:** Llamada a `_queue_reservations` y a `acquire_audit_failed` a mitad del ciclo (línea 666).
4. **Provisión de Grant:** `_new_lease_locked` y la firma a disco `_write_initial_grant_audits_locked` (línea 474).

**Función `_claim_head_from_wait_locked` (`session_coordination.py:2665` a 2927 - 262 líneas):**
1. **Admisión de la espera:** Llama a `_arm_wal_locked` a disco para asegurar el estado de la espera en la FIFO (línea 2743).
2. **Preparación:** Llama a la auditoría (`session_grant_prepared`) y a las re-validaciones a memoria.
3. **Concesión y Fallos:** Llamadas a `_queue.pop(0)` y a los intentos de compensación (`_compensate_provisional_grant_locked`) si la auditoría final de `session_granted` a disco falla.

## G. Propuestas
1. **Segregar los *read-only* del bloqueo de dueños de corrida.** (S / Bajo / Unit Test)
   * En `loopback.py:_enqueue_fence_target`, eximir a las peticiones que carezcan de `lease_token` del chequeo de estado de dueño de corrida si están catalogadas en solo lectura.
2. **Rebajar el tiempo de *expiry grace*.** (S / Bajo / Test de latencia de adquisición)
   * Pasar a 30s el `LEASE_GRACE_S` (`session_coordination.py:16`) si el dueño muere sin poder lanzar un heartbeat.
3. **Auditoría a ciegas de *read-only* a la baja.** (M / Medio / Llamadas a `authorize` sin auditoría a disco)
   * El flujo de `authorize` sin dueños (`session_coordination.py:1261`) bloquea al auditor del lazo local a escribir a disco sin control de dueño, lo que puede inflar el tamaño de auditoría sin necesidad de un *lease*.
4. **Rehacer el ciclo asincrónico de heartbeat y *lease*.** (M / Medio / Test de asincronía de `lease_supervisor`)
   * Ligar a la vida del stdio la supervivencia del lease en los *workers* para prevenir los 120s de bloqueo tras la caída de procesos huérfanos.

## H. Valoración
* **Corrección: 8/10** (Asegura a muerte a los dueños de escritura, pero es laxo con los *read-only* a nivel lógico de `loopback`).
* **Proporcionalidad: 4/10** (Demasiada maquinaria de WAL y de *fences* de estado a memoria y a disco para un caso de uso local, 1 máquina y 1 partida).
* **Legibilidad: 2/10** (`acquire` de 463 líneas y 20+ variables de instancia a memoria y a disco en la misma línea de tiempo).
* **Testabilidad: 6/10** (Buena inyección de dependencias a tiempo y funciones, pero la lógica a medio *lock* y *audit_gate* es difícil de emular sin falsos bloqueos).
* **Preparación para P1/P2: 5/10** (Tiene la base para P2 con *worker* asincrónicos, y el P1 está latente al no aplicar el aislamiento de  `loopback` a las 12 lecturas).

## Hallazgos
F01 | B | tools/dayz_mcp/session_coordination.py:1262 | seguridad | Las 12 lecturas seguras no verifican si otro dueño tiene el control (lease) para permitir una escritura en paralelo, arriesgando lecturas a mitad de mutación de otro cliente. | EVID: client=client, | FIX: Validar si existe una escritura de mutación activa antes de autorizar a ciegas a los clientes sin lease.
F02 | D | tools/dayz_mcp/loopback.py:1476 | diseño | Los clientes de solo lectura sin lease son a ciegas bloqueados si la corrida de DayZ no tiene dueño o está inactiva. | EVID: rejection = self._enqueue_run_rejection( | FIX: Ignorar la comprobación del dueño de la corrida en `loopback.py` si no se usa un lease_token y es de solo lectura.
F03 | D | tools/dayz_mcp/session_coordination.py:2194 | comportamiento | Un cliente que crashea bloquea a los demás durante 90 segundos a través de la gracia de caducidad. | EVID: return client != grace.former | FIX: Reducir a 30 segundos el LEASE_GRACE_S o permitir reclamar a los forasteros si el heartbeat no es vivo.

## LO QUE NO PUDE VERIFICAR
* El diseño de la función `server_reload` de `build_app` o cómo se asocia a los eventos `tools/list_changed` a disco desde el supervisor de MCP hacia Claude.
* La lógica a disco de `ProcessLifecycle` y cómo interactúa a la hora de adoptar a los procesos (`adopt_run`).
* Los detalles de los 4090 tests del repo y su cobertura asincrónica del *supervisor de lease*.

## ¿Qué puede estar mal en la premisa de este encargo?
* El código prohíbe a propósito a los *read-only* interactuar con una partida de 1 dueño si este está a mitad de una escritura, para evitar *visual glitches* en DayZ que a ciegas confundirían a la IA (p. ej. leer el árbol de UI de un inventario a medio cerrar).
* El bloqueo a los 90 segundos por `expiry_grace` está pensado a propósito para cuando un usuario desconecta a Claude o Codex y lo vuelve a conectar 30s después, evitando que pierda el control a favor de otro agente.