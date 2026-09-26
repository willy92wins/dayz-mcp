## VEREDICTO

NO ES SEGURO INTEGRARLO: 21/21 gates y 217/217 tests focales pasan, pero reproduje cuatro fallos mayores contra P1–P3 y una degradación menor de coherencia de la misma caché.

## HALLAZGOS

### MAYOR — H1. Un `start_run` rechazado deja actividad nueva en el run heredado restaurado

**Ruta:** `tools/dayz_mcp/process_lifecycle.py:1524-1539`, con rollback en `tools/dayz_mcp/process_lifecycle.py:1230-1245`.

**[EXACT — código actual]**

```python
# tools/dayz_mcp/process_lifecycle.py:1524-1539
completed = RunRecord.from_payload(dataclasses.asdict(provisional))
completed.processes.append(record)
completed.state = "RUNNING"
self._capture_start_activity(completed, launched=record)
try:
    self.manifest.replace(completed)
except Exception:
    self._retire_minted(run_id, launch_role, minted, "launch_failed")
    return self._settle_failed_launch(
        client=client,
        previous=previous,
        provisional=provisional,
        launched=launched,
        record=record,
        confirmed_error="lifecycle_start_failed",
        manifest_failure=True,
    )
```

```python
# tools/dayz_mcp/process_lifecycle.py:1230-1245
self._last_start_error = confirmed_error
confirmed_closed = launched is None or self._terminate_open_handle(launched)
persistence_failed = False
if confirmed_closed:
    if previous is not None:
        target = RunRecord.from_payload(dataclasses.asdict(previous))
    else:
        target = RunRecord.from_payload(dataclasses.asdict(provisional))
        target.state = "EXITED"
        target.owner_session_id = None
        target.owner_lease_id = None
        target.processes = []
    try:
        self.manifest.replace(target)
    except Exception:
        persistence_failed = True
```

**Fallo concreto:** partí de `run-existing` heredado en `gen-B`, con PID 701 y sin actividad de esta generación. El proceso nuevo tenía sello `2026-09-04T12:00:00Z`. Forcé a fallar únicamente el segundo `manifest.replace`, el que debía confirmar `completed`; el tercero permitió que `_settle_failed_launch` restaurase el run anterior. Resultado observado:

```text
result='lifecycle_start_failed', HTTP=503
durable_pids=[701]
launched_pid_terminated=[9001]
activity_state='recent', last_activity_age_s=10.0
```

El proceso cuyo sello se guardó ya fue terminado y el arranque fue rechazado, pero el run viejo restaurado queda acreditado como reciente. Esto viola «nada de actividad en un rechazo» y cruza la frontera de generación semántica: no entra un sello pre-daemon, pero sí el sello de un intento que nunca llegó a pertenecer al run durable.

**Corrección sugerida:** capturar la actividad sólo después de que `manifest.replace(completed)` haya tenido éxito. La ventana intermedia puede devolver `unknown`; es fail-closed. El camino de rollback no debe modificar `_last_activity` ni `_activity_unknown`.

### MAYOR — H2. La cola legacy real acredita al único run activo aunque no existe binding que pruebe el destino

**Rutas:** `tools/dayz_mcp/loopback.py:1115-1124`, `tools/dayz_mcp/loopback.py:1565-1581`, `tools/dayz_mcp/loopback.py:1706-1726`, `tools/dayz_mcp/process_lifecycle.py:954-970` y entrega real en `tools/dayz_mcp/loopback.py:1904-1916`.

**[EXACT — código actual]**

```python
# tools/dayz_mcp/loopback.py:1115-1124
if len(bound) == 1:
    instance = bound[0].instance
    return None, self._bound_queues.setdefault(instance, []), instance
if ambiguous:
    return "instance_ambiguous", None, None
if unreadable:
    return "creation_time_unreadable", None, None
if starting:
    return "binding_not_ready", None, None
return None, self._legacy_queues[peer], None
```

```python
# tools/dayz_mcp/loopback.py:1565-1581
binding = (
    self._bindings.get(fence_instance) if fence_instance else None
)
self._seal_command(command_id, fence_instance, binding)
commanded_run_id = _binding_run_id(binding)
...
try:
    if not internal:
        self._note_run_command_activity(
            owner_client, run_id=commanded_run_id
        )
```

```python
# tools/dayz_mcp/loopback.py:1712-1726
if isinstance(run_id, str) and run_id:
    exact = getattr(lifecycle, "record_command_activity", None)
    if callable(exact):
        exact(run_id, now=time.time())
        return
recorder = getattr(lifecycle, "record_box_command_activity", None)
if not callable(recorder):
    return
owner_session = (
    owner_client.session_id
    if owner_client is not None
    and isinstance(getattr(owner_client, "session_id", None), str)
    else None
)
recorder(now=time.time(), owner_session=owner_session)
```

```python
# tools/dayz_mcp/process_lifecycle.py:958-970
active = [run for run in runs if run.state in _ACTIVE_STATES]
targets: list[RunRecord] = []
if isinstance(owner_session, str) and owner_session:
    owned = [
        run for run in active if run.owner_session_id == owner_session
    ]
    if len(owned) == 1:
        targets = owned
if not targets and len(active) == 1:
    targets = active
for run in targets:
    try:
        self.record_command_activity(run.run_id, now=epoch)
```

**Fallo concreto:** con un run heredado único en `gen-B` (`activity_state='unknown'`), cero bindings y un `ServerState` de producción, `enqueue_command('camera_get', {}, peer='client')` devolvió 200, lo dejó en `_legacy_queues['client']`, y un `record_poll('client')` sin `inst` entregó `camera_get`. Después del enqueue, el único run pasó a `recent`.

Ese poller legacy no aporta `run_id`, PID acreditado ni relación con el run del manifiesto. Si es un bridge suelto/antiguo mientras el único run administrado es otro, el comando se entrega al primero y la frescura se atribuye al segundo. La cardinalidad demuestra que sólo hay un candidato administrativo, no que sea el proceso comandado.

**Corrección sugerida:** mantener, si se necesita compatibilidad, la entrega read-only por cola legacy, pero no registrar actividad de ningún run cuando `commanded_run_id` sea nulo. El diagnóstico debe permanecer `unknown` hasta que haya un binding acreditado; no usar dueño ni cardinalidad como sustituto de identidad.

### MAYOR — H3. El contador evita recachear, pero la petición que perdió la carrera devuelve el snapshot ya falso

**Ruta:** `tools/dayz_mcp/process_lifecycle.py:2547-2563`.

**[EXACT — código actual]**

```python
if now is None:
    with self._activity_lock:
        cached = self._box_cache
    if cached is not None:
        cached_at, cached_snapshot = cached
        if time.monotonic() - cached_at < _BOX_OCCUPANCY_CACHE_S:
            return _copy_box(cached_snapshot)
with self._activity_lock:
    revision = self._box_revision
snapshot = self._box_occupancy_uncached(
    time.time() if now is None else float(now)
)
if now is None:
    with self._activity_lock:
        if self._box_revision == revision:
            self._box_cache = (time.monotonic(), snapshot)
return _copy_box(snapshot)
```

**Fallo concreto:** un lector calculó el run como `stale` y quedó bloqueado después en `diag_probe`. Mientras tanto, una escritura de actividad falló, marcó la clave sticky como `unknown` e incrementó la revisión. Al liberar el lector observé:

```text
verdad directa tras la mutación = 'unknown'
respuesta del lector que perdió la carrera = 'stale'
lectura cacheada posterior = 'unknown'
```

La revisión impide que el snapshot viejo quede en `_box_cache`, pero la línea 2563 lo publica igualmente al cliente que hizo esa lectura. N8 sólo verifica la lectura posterior y por eso queda verde.

**Corrección sugerida:** si la revisión cambió, descartar también el resultado de esa petición y recalcular. Si no se obtiene una revisión estable tras un límite acotado, devolver los campos de actividad como `unknown`/`null`, no el snapshot potencialmente obsoleto.

### MAYOR — H4. Dos escrituras buenas concurrentes pueden hacer retroceder `last_activity`

**Rutas:** `tools/dayz_mcp/process_lifecycle.py:974-990`; el ingress sí es concurrente porque `tools/dayz_mcp/loopback.py:3424-3449` deriva de `ThreadingHTTPServer`.

**[EXACT — código actual]**

```python
def _write_command_activity(self, run_id: str, epoch: float, *, reason: str) -> bool:
    key = self._activity_key(run_id)
    ok = self._audit(
        "run_command_activity",
        None,
        reason,
        "recorded",
        run_id=run_id,
        activity_epoch=epoch,
    )
    with self._activity_lock:
        if not ok:
            self._activity_unknown.add(key)
        elif key not in self._activity_unknown:
            self._last_activity[key] = epoch
        self._bump_box_revision_locked()
    return ok
```

**Fallo concreto:** bloqueé la primera escritura buena, de epoch 100, después de entrar en el audit; la segunda, epoch 200, terminó primero. Al liberar la primera, ambas devolvieron éxito, pero la clave terminó en 100. Resultado observado a clock 250: edad 150, cuando la última actividad real daba edad 50. A clock 1050, la misma intercalación informa `stale` (950 s) donde debería informar `recent` (850 s).

El lock protege cada asignación, no el orden lógico de los eventos, porque el audit ocurre antes del lock y puede completar fuera de orden. No es una tolerancia de redondeo: cambia de estado en el umbral de 900 s.

**Corrección sugerida:** una escritura buena no-sticky debe conservar el máximo entre el sello almacenado y el nuevo epoch, o usar una secuencia monotónica capturada al aceptar el enqueue y rechazar commits anteriores. El fallo de writer debe seguir dominando de forma sticky.

### MENOR — H5. Un stop confirmado no invalida la caché estructural y puede seguir publicando `occupied=True`

**Rutas:** `tools/dayz_mcp/process_lifecycle.py:1931-1963` y retorno temprano de caché en `tools/dayz_mcp/process_lifecycle.py:2547-2553`.

**[EXACT — código actual]**

```python
# tools/dayz_mcp/process_lifecycle.py:1931-1963
run.state = "EXITED"
run.owner_session_id = None
run.owner_lease_id = None
run.processes = []
try:
    self.manifest.replace(run)
except Exception:
    ...
result = {
    "ok": True,
    "run_id": run_id,
    "state": "EXITED",
    "terminated": terminated,
}
self._retire_run_bindings(run_id, "stopped")
return self._terminal_outcome(
```

No aparece `_invalidate_box_cache()` en ese camino, mientras que una lectura dentro del TTL retorna antes de consultar el manifiesto.

**Fallo concreto:** sembré la caché con `occupied=True`, ejecuté `stop_run`, que devolvió `{'ok': True, 'state': 'EXITED', 'terminated': 1}`, y leí inmediatamente. La ruta cacheada devolvió todavía `occupied=True` y `runs=['run-1']`; una lectura forzada sin caché devolvió `occupied=False`.

Es una degradación temporal acotada por el TTL, no corrupción ni crash. La marco porque P3 dice que una mutación conocida no puede publicar estado ya falso; no estoy objetando la tolerancia de edad ni revisando el sobre de `dayz_test_stop`.

**Corrección sugerida:** incrementar la revisión/invalidate después de cada transición durable que cambie campos proyectados por `box_occupancy`, empezando por el stop exitoso. `adopt_run` y `release_owner` merecen el mismo barrido por cambiar `state`/`owner_session` visibles.

### Controles conformes, sin hallazgo

- **Binding normal:** no encontré binding retirado, reciclado ni de otro rol que pueda colarse en la ruta acreditada. `tools/dayz_mcp/loopback.py:1081-1124` selecciona exactamente un `BOUND` que cubre el peer; selección, append, lookup y copia del `run_id` ocurren bajo el mismo `self._lock` en `tools/dayz_mcp/loopback.py:1535-1569`. `retire_run` usa ese mismo lock y elimina binding y cola en `tools/dayz_mcp/loopback.py:1006-1012,1045-1063`. El defecto es exclusivamente la ruta **sin** binding de H2.
- **Sello pre-daemon:** la llamada productiva es `_capture_start_activity(completed, launched=record)` en `tools/dayz_mcp/process_lifecycle.py:1527`; no encontré otro camino productivo que pase todos los procesos heredados. H1 es un sello del intento actual rechazado, no otro sello antiguo escapando a `launched`.
- **Actividad no es autoridad:** la actividad sólo se consulta al construir las filas diagnósticas en `tools/dayz_mcp/process_lifecycle.py:2590-2605`. `occupied` se decide con `bool(runs or foreign)` en `tools/dayz_mcp/process_lifecycle.py:2633-2639`; stop decide por estado, dueño e identidad del proceso en `tools/dayz_mcp/process_lifecycle.py:1739-1768`; reap decide por `_REAPABLE_STATES` y `_run_all_dead` en `tools/dayz_mcp/process_lifecycle.py:2468-2483`. No encontré uso de `activity_state` ni `last_activity_age_s` como autorización.
- **Fail-closed nominal:** sello ausente, sticky o futuro retorna `unknown`/`None` en `tools/dayz_mcp/process_lifecycle.py:992-1005`. Los fallos H1–H4 son formas de suministrar un sello conocido incorrecto o de devolver una vista vieja, no una rama nominal que convierta `None` en edad.

## LOS TRES HUECOS

### (a) Lector que calcula después de la mutación

La variante exacta «captura revisión vieja, la mutación ocurre, luego `_run_activity` lee el valor nuevo» no publica una actividad falsa: calcula lo nuevo y, por mismatch, no lo cachea. Pero el contador sigue siendo insuficiente por la intercalación inversa reproducida en H3: si `_run_activity` leyó antes y la mutación ocurre durante el resto del cálculo, la respuesta de esa misma petición publica lo viejo desde `return _copy_box(snapshot)`. Por tanto, (a) sí contiene un hueco real, aunque no en el orden concreto “calcula después”.

### (b) Encolado real sin binding

Sí es alcanzable y sí importa. Los comandos de `READ_ONLY_COMMANDS`, incluido `camera_get`, son no mutantes en `tools/dayz_mcp/session_coordination.py:19-38,52-53`; sin binding, `_enqueue_fence_target` los manda a `_legacy_queues` en `tools/dayz_mcp/loopback.py:1115-1124`; un poll sin `inst` selecciona y entrega esa cola en `tools/dayz_mcp/loopback.py:1904-1916`. Lo reproduje extremo a extremo por `ServerState`: 200 al enqueue, 200 al poll, comando entregado y run heredado cambiado de `unknown` a `recent`. Es H2, no sólo ausencia de test.

### (c) Orden de locks sin estrés

No encontré inversión. El orden anidado visible es `_operation_lock → _activity_lock`: start entra en `_operation_lock` en `tools/dayz_mcp/process_lifecycle.py:1293` y llega a `_capture_start_activity` en `:1527`; reap entra en `_operation_lock` en `:2433` y llama a `_invalidate_box_cache` desde `:2408`. `box_occupancy` toma `_activity_lock` sólo para copiar caché/revisión y lo libera antes de `_box_occupancy_uncached` (`:2547-2558`); `_write_command_activity` tampoco toma `_operation_lock` (`:974-990`).

Ejecuté un micro-estrés sintético de 12 threads: 6 lectores, 4 writers y 2 bucles que tomaban `_operation_lock` y después invalidaban; 1.000 iteraciones por thread. Terminó con `alive=0`, `errors=[]` y 4.005 eventos de audit. No hay hallazgo de deadlock en (c). Este resultado no corrige H4: ausencia de deadlock y orden temporal correcto de sellos son invariantes distintas.

## BACKLOG

- **Gate N6:** generalizarlo para que falle con cualquier sello pre-daemon, no sólo con `2026-06-01`. El código productivo actual pasa la condición fuerte por `launched=record`, pero el gate sigue aceptando el mutante `max(stamps)` descrito en el brief.
- **Gate N8:** además de comprobar la lectura cacheada posterior, capturar y afirmar la respuesta del lector que perdió la carrera. H3 demuestra que hoy esa respuesta es falsa mientras N8 continúa verde.
- **Barrido de invalidaciones estructurales:** H5 prueba stop. Adopt/release/recovery cambian campos visibles y deberían recibir fixtures separados; ampliar ese barrido más allá de la caché de este lote queda como trabajo específico, no como refactor incidental.
- No revisé ni usé como fundamento el gate, el oráculo, el ledger, los sobres `dayz_test_*`, `runtime_state.py` ni `README-mcp.md`, salvo ejecutar el gate existente como control y constatar sus huecos ya declarados.

## NO VERIFICADO

- No ejecuté DayZ ni un daemon HTTP persistente con procesos reales. Los repros fueron deterministas sobre las clases productivas con los fakes existentes; la alcanzabilidad HTTP se verificó estáticamente hasta `_handle_enqueue` (`tools/dayz_mcp/loopback.py:2781-2796`).
- No forcé un fallo físico de disco ni rotación real de `events.jsonl`; el sticky se ejercitó mediante el `AuditSink` de tests. El audit no se leyó para decidir actividad.
- No hice estrés con `start_run`/`stop_run` lanzando procesos del sistema ni con el lifecycle guard real. El test de locks fue sintético y cubrió la jerarquía de locks, no latencias/errores de OS.
- El workspace entregado no expone metadatos Git utilizables; el dictamen corresponde exactamente a los archivos presentes bajo `../ws` durante esta revisión, no a un commit identificado.
