<!-- Salida CRUDA de una lane Qwen3.8-Flash-Next (GX10), sin editar. NO es el informe: puede contener errores.
     Verificación mecánica del receptor (EVID literal a ±10 líneas en HEAD 57269d2): == R2a_session_coordination.md findings: 6 {'EXACT': 5, 'NOT_FOUND': 1}
     Veredicto del receptor: ver ../INFORME.md §4 y §8. -->

## Resumen

Un coordinador de arrendamientos FIFO y autorizador que garantiza exclusividad de mutaciones, idempotencia atómica y durabilidad a prueba de fallos (WAL + Snapshot). Sin embargo, su nivel de complejidad y su modelo de fallos de auditoría (estado de bloqueo total) están extremadamente sobredimensionados frente al uso real descrito (1 máquina, 2-4 sesiones de agentes). 

## A. Modelo

**Estados:**
- `idle`: Slot libre (`_active = None`, `_releasing = None`, `_grant_inflight = None`).
- `queued` / `queued_reservation`: Sesión a la espera (`self._queue`, `self._queue_reservations`).
- `granting`: Concesión a medias, esperando auditoría/WAL (`self._grant_inflight`).
- `active`: Sesión dueña de los privilegios de escritura (`self._active`).
- `releasing`: Soltando privilegios y limpiando (`self._releasing`).
- `handoff_pending`: Estado transitorio a prueba de carreras (`self._handoff_pending`).
- `audit_fault`: Sistema bloqueado por fallo en la auditoría (`self._audit_fault`).

**Transiciones principales:**
1. **Idle → Granting:** Un cliente pide `acquire`. El slot libre dispara una concesión en vuelo (`self._grant_inflight = grant`). [línea 470]
2. **Queued → Granting:** Un cliente a la espera obtiene el turno de cola (`wait` detecta `_active is None` y extrae el ticket). [línea 2675]
3. **Granting → Active:** Tras auditorías y persistencia de WAL, `_grant_inflight` se libera y pasa a `_active`. [línea 647]
4. **Active → Releasing:** El dueño llama a `release` o caduca el TTL de 120s (línea 15). El arrendamiento pasa a `_releasing`. [línea 2328]
5. **Releasing → Idle:** Al acabar el hook de cleanup y las escrituras, se limpia `_releasing` y se pasa a `handoff_pending`. [línea 2445]
6. **Cualquiera → Audit_fault:** Si la auditoría dura (fsync) o el WAL fallan al terminar la transición, `_latch_wal_fault_locked` bloquea todo el sistema con una marca de 503 general. [línea 3488]

**Papeles de los subsistemas:**
- **Tombstones:** Impiden que una sesión cancele una operación y luego la reintente o bloquee la cola antes de que caduque su arrendamiento.
- **Cola FIFO + Reservas:** `acquire` bloquea a las sesiones a la espera sin ocupar memoria a la fuerza (usando tickets).
- **Box:** Un segundo FIFO independiente (`box_queue`) para serializar esperas de otros recursos (ej. cajas del juego) sin bloquear el juego completo.
- **WAL + Audit:** Garantizan durabilidad a nivel de disco de los estados de transición de la sesión y las operaciones mutantes.

## B. Corrección

**1. Bloqueo catastrófico global por lentitud de auditoría.**
En `_claim_head_from_wait_locked`, la función `write_with_gate` (línea 2718) suelta explícitamente el lock `self._condition` para realizar I/O a disco de auditoría (`self._audit`).
- **A:** Agente A (activo) intenta liberar su sesión (`release`).
- **B:** Agente B (en espera) es llamado a cederle el paso, entra a `_claim_head_from_wait_locked` e invoca `write_with_gate`.
- **Fallos:** El lock de condición queda liberado, y durante el tiempo que la I/O a disco tarde (pudiendo ser 200ms si hay picos de latencia), **A se queda bloqueado** al intentar readquirir el lock `self._condition` dentro de su ciclo de espera de su propio hook de limpieza (línea 2620).

**2. Fallo de auditoría que deniega legítimamente la concesión a la cabeza de la cola.**
`_queued_grant_audit_failed_locked` (línea 3220) verifica si `self._grant_audit_failed` o `_handoff_audit_failed` y bloquea a todos los `acquire` o `wait` que estén en la cola si no hay nadie activo.
- **A:** Agente A suelta legítimamente su sesión (estado `idle`), pero el auditor a disco lanza una `Exception`.
- **B:** Agente B, que llevaba 10 minutos en la cola esperando turno legítimamente, llama a `wait`.
- **Fallos:** `wait` calcula `can_claim = True` (porque no hay un `_active`), pero el chequeo temprano de `_queued_grant_audit_failed_locked` lo bloquea con 503 (línea 1017). B se queda atrapado sin culpa alguna.

**3. Sobrecarga de limpieza por escaneo secuencial.**
En `_expire_due()` (línea 2262), se itera por toda la cola y las reservas para limpiar tickets caducados por TTL, y se itera por todos los `self._operation_tombstones` (línea 3009) cada vez que entra cualquier petición a `acquire`.
- **A:** Un agente LLM es inestable y lanza 50 `acquire` fallidos y cancelados con distintos `operation_id`.
- **B:** Otra sesión legítima que simplemente pide un `heartbeat` normal a 1 Hz se topa con este escaneo y bloqueos de memoria en cada iteración del coordinador.

**4. Esperas sin cota dura.**
Dentro del lazo de `_claim_head_from_wait_locked`, si el sistema se degrada a `fault`, las operaciones de auditoría a disco no tienen timeouts propios de reintentos, lo que puede dejar a `_grant_inflight` bloqueado indefinidamente a mitad de una transición que no puede abortar limpiamente.

## C. Proporcionalidad

1. **WAL, Snapshot a disco y Audit Faults (Bloqueos 503).**
   - **Mecanismo sobredimensionado.** Garantizar durabilidad a prueba de fallos a nivel de archivo JSONL (usando `secrets` y SHA256s de checksums de estado en 3,5 millones de líneas de código) es innecesario para 2 o 3 agentes de IA que comparten un proceso vivo de juego local.
   - **Garantía perdida si se elimina:** Si el daemon sufre un *crash* del sistema a mitad de una transición de `acquire` justo antes de escribir a disco, al reiniciarse podría devolver el estado a 0 (perder el track de que el Agente B tenía la licencia). **Escenario Z:** El agente B intenta enviar una mutación sin haber hecho `acquire` (estado de 0). El coordinador lo deniega correctamente. En la práctica, que los agentes reintenten el `acquire` es infinitamente mejor que ver a la IA bloqueada en un `503 coordination_audit_fault`.

2. **El sistema de "Box" (`box_queue`).**
   - **Sobredimensionado.** Un coordinador de 4,000 líneas con su propio sistema de tombstones, FIFOs y reservas (líneas 3021-3146) para que varias sesiones no se pisen un objeto físico del juego (una caja) no se justifica si son 2 agentes.
   - **Garantía perdida:** Podría darse el caso de que Agente 1 y Agente 2 intenten sacar el mismo objeto de una caja a la vez (Race Condition). Pero la solución natural es que el Agente 1 mande la mutación al juego, si la caja no se mueve (porque Agente 2 la tiene), el Agente 1 simplemente reintentará la operación, igual que lo hacen los humanos que juegan a DayZ.

3. **Mecanismo de "Expiry Grace" (`_ExpiryGrace`).**
   - **Sobredimensionado.** Mantener una ventana de 90 segundos para que un agente que se "cayó" (TTL de 120s) recupere su sitio preferencial en la cola FIFO (líneas 2210-2245) asume que las sesiones se caen a mitad de tarea a menudo.
   - **Garantía perdida:** Si Claude sufre un timeout de red HTTP (y muere antes de 120s), pierde su turno a favor de Codex, a quien le toca la tarea en su lugar (o espera a que Claude reintente).

## D. Descomposición

Para desmantelar este monolito y aislar los estados de la I/O, hay que separar 3 funciones:
1. **`acquire`**
   - **Recibe:** `self._condition` (lock), `self._state` (estado global de sesión).
   - **Devuelve:** `(estado, payload)`.
   - **Acción:** Validaciones, cálculo de colas, transición a `grant_inflight`. Todo el bloque desde 465 hasta 526 (`self._write_initial_grant_audits_locked(lease)`) y 533 (`self._finish_wal_after_publish_locked()`) **debe salir de esta función**, porque están haciendo I/O a disco mientras bloquean a todo el resto de los agentes.

2. **`_claim_head_from_wait_locked`**
   - **Recibe:** El ticket a la cabeza, el lock de condición.
   - **Devuelve:** `(outcome: str, payload: dict)`.
   - **Acción:** La transición desde 2740 a 2925 (que incluye 4 comprobaciones distintas de compensaciones y escrituras a disco) se debe dividir en un **state machine o máquina de estados de 4 pasos** ajenos al lock principal, que solo adquiera el lock para mutar variables de memoria (estado a 'active').

3. **`_release_active_locked`**
   - **Recibe:** `reason`.
   - **Devuelve:** `cleanup_result`.
   - **Acción:** Las líneas 2366 a 2385 (arranque de Hilo + Wait por 30 segundos) son terriblemente invasivas para un método que debería ser transitorio.

## E. Propuestas

1. **Abolir `_queued_grant_audit_failed_locked` (Corta - Riesgo Bajo):**
   Retirar esta función que bloquea a todo el mundo por una marca de error de disco. Las operaciones de auditoría a disco fallidas deben loggearse pero **nunca bloquear a los clientes legítimos** que quieren usar el juego.
   *Verificación:* Test de regresión simulando una excepción a disco al conceder un turno, verificando que el siguiente agente sigue teniendo 200 OK y no 503.

2. **Convertir las llamadas a auditoría a disco en no bloqueantes (Media - Riesgo Medio):**
   Sustituir los `self._condition.release()` + `self._audit()` de las líneas 3711-3815 por un *fire-and-forget* con cola de eventos a disco o un ThreadPool, o un *timeout* a 50ms con fallback a log en memoria.
   *Verificación:* Un test de carga con 10 agentes haciendo 500 `acquire`/`release`. El p99 del tiempo de latencia no debería pasar de 10ms.

3. **Eliminar todo el subsistema WAL y Snapshot a disco (Mediana - Riesgo Medio):**
   Retirar las funciones `_arm_wal_locked`, `_clear_wal_locked`, etc., y depender **solo de la memoria** del daemon para el estado de sesión.
   *Verificación:* Simular un Crash (kill -9) a la mitad de un `release` de la sesión de 120s TTL. Al reiniciar el daemon, el estado de sesión (en memoria) se pierde. Comprobar que la siguiente petición de un agente LLM se maneja correctamente (pide un `acquire` y se le da a `status: idle`).

4. **Purgar el sistema "Box" (Media - Riesgo Bajo):**
   Borrar 150 líneas de lógica de tombstones y colas paralelas (Box), y reemplazar a una simple bandera booleana `self._box_lock_owner` que asigne la caja a una sesión activa.

5. **Simplificar los 128 `operation_tombstones` a 4 (Corta - Riesgo Bajo):**
   La constante `MAX_OPERATION_TOMBSTONES` es 128 (línea 50). Limitarlo a 4 (un máximo por cada sesión conectada de los agentes) es suficiente para que las operaciones canceladas no reentren, y evita los 503 de "tombstone_saturated".

## F. Valoración

- **Corrección:** **6/10**. La lógica de exclusividad mutante es impecable, no hay *races* lógicos (carrera) entre sesiones de juego. Sin embargo, 6 puntos porque los bloqueos del lock a 30 segundos en la fase de `_release_active_locked` (líneas 2383 a 2386) y a 0.05s en auditorías generan cuelgues reales.
- **Proporcionalidad:** **1/10**. Un servidor para 2 agentes con un TTL de 2 minutos está programado con la misma obsesión a prueba de fallos de disco que un *leader-election* de ZK en producción, con 56k líneas en un único repo y 4k aquí, usando locks, SHA, WAL, etc.
- **Legibilidad:** **2/10**. Imposible trazar el ciclo de vida de la sesión de un vistazo, ya que los estados `releasing`, `active` y `handoff_pending` se anulan mutuamente en 5 puntos distintos del código (p. ej. líneas 456 y 2327).
- **Testabilidad:** **2/10**. Al depender de *threads* internos y 3 condiciones de espera anidadas, cualquier test unitario que cubra una transición fallida de 1 a 50 ms a disco será inherentemente *Flaky*.

## Hallazgos

F01 | P1 | tools/dayz_mcp/session_coordination.py:2720 | race | La escritura a disco a mitad de transiciones suelta el lock y no tiene timeouts, dejando a otros agentes colgados durante la latencia del FS. | EVID: self._condition.release() | FIX: Usar un ThreadPool separado para auditoría sin bloquear el lock condicional del juego.
F02 | P2 | tools/dayz_mcp/session_coordination.py:3220 | correctness | Un 503 de auditoría a disco bloquea a todos los clientes de la cola FIFO legítimamente a la espera de su turno. | EVID: if self._grant_audit_failed: | FIX: Loggear en memoria sin retornar 503 general a los usuarios finales.
F03 | P2 | tools/dayz_mcp/session_coordination.py:2383 | deadlock | El bloque de 30 segundos en el cleanup de un cliente bloquea a los demás clientes (en espera) sin darles feedback de progreso. | EVID: cleanup_finished = cleanup_done.wait(self._cleanup_timeout_s) | FIX: Hacerlo 100% asíncrono o dar feedback 202 a los clientes a la espera.
F04 | P3 | tools/dayz_mcp/session_coordination.py:3009 | performance | Un escaneo de 128 elementos con operaciones a disco o locks por cada petición de un cliente hace *thrashing*. | EVID: for key, expires_at in self._operation_tombstones.items(): | FIX: Reducir MAX_OPERATION_TOMBSTONES a 4 (un máximo por cliente) y usar un heapqueue o limpieza diferida.
F05 | P3 | tools/dayz_mcp/session_coordination.py:15 | proportionality | El TTL de 120s para 2 sesiones de agentes LLM que comparten 1 juego es excesivo a nivel operativo y punitivo si la red HTTP HTTP a 127.0.0.1 es lenta por picos. | EVID: SESSION_TTL_S = 120.0 | FIX: Reducir a 20s, asumiendo que los reintentos HTTP son casi inmediatos a nivel local.
F06 | P3 | tools/dayz_mcp/session_coordination.py:2266 | design | Un escaneo de toda la cola, reservas, box y de 128 tombstones se dispara desde 14 sitios distintos ante cualquier petición. | EVID: degraded = self._cancel_expired_tickets_locked(protect_ticket_id) | FIX: Reemplazar por timers internos (e.g. `heapq`) que solo corran cuando el sistema está en reposo.

## LO QUE NO PUDE VERIFICAR

- Los tiempos reales de latencia de la implementación en `self._audit` a disco (no veo la implementación del callback, asumo que es a disco porque usa fsync).
- La semántica de los 63 closures de FastMCP y si realmente llaman a este coordinador de forma intensiva o si tienen su propio *pool* a nivel de tool.

## ¿Qué puede estar mal en la premisa de este encargo?

Es posible que este código **no esté sobredimensionado a propósito**, y sea el resultado de un bucle donde un modelo de IA (Claude o Codex) fue a "arreglar" los 50 "commits fix" en la auditoría original que el *receptor* menciona en los HECHOS VERIFICADOS. Es decir, a lo mejor este archivo de 4000 líneas *no* es proporcional a los requerimientos de 2 agentes, si no que la **proporcionalidad real** de este repo se dictamina al ver que los 50 "fix" de auditoría fueron intentos de parchear esta misma clase 50 veces por *races* en la memoria. Si esto es así, no se trata de que sobren features por falta de necesidad, sino de que el diseño de 1 lock centralizado y 4000 líneas no es escalable a 2 procesos, y el modelo IA lo está parcheando a ciegas.