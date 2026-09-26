## VEREDICTO

NO: no es seguro integrarlo; hay cuatro fallos observables en P2/fail-closed/procedencia/caché y la abierta (b) incumple la lectura de backups, aunque no encontré ningún camino donde la actividad decida `occupied`, stop o reap.

## HALLAZGOS

### ALTA — H-01: si el callback de audit no expone `current_path`, la basal vuelve a fabricarse desde `creation_time_utc`

`ws/tools/dayz_mcp/process_lifecycle.py:903`, `:913` y `:1048`

```python
# [EXACT] ws/tools/dayz_mcp/process_lifecycle.py:903-911
def _audit_jsonl_path(self) -> Path | None:
    writer = getattr(self.audit, "__self__", None)
    path = getattr(writer, "current_path", None)
    if path is None:
        return None
    try:
        return Path(path)
    except TypeError:
        return None

# [EXACT] ws/tools/dayz_mcp/process_lifecycle.py:913-926
def _capture_start_activity(self, run: RunRecord) -> None:
    stamps = [
        stamp
        for stamp in (
            _utc_epoch(record.creation_time_utc) for record in run.processes
        )
        if stamp is not None
    ]
    if not stamps:
        return
    key = self._activity_key(run.run_id)
    with self._activity_lock:
        if key not in self._activity_unknown and key not in self._last_activity:
            self._last_activity[key] = min(stamps)

# [EXACT] ws/tools/dayz_mcp/process_lifecycle.py:1590-1595
completed = RunRecord.from_payload(dataclasses.asdict(provisional))
completed.processes.append(record)
completed.state = "RUNNING"
self._capture_start_activity(completed)
try:
    self.manifest.replace(completed)

# [EXACT] ws/tools/dayz_mcp/process_lifecycle.py:1048-1065
def _run_activity(self, run: RunRecord, clock: float) -> tuple[str, float | None]:
    key = self._activity_key(run.run_id)
    with self._activity_lock:
        if key in self._activity_unknown:
            return "unknown", None
    status, jsonl_epoch = self._latest_jsonl_activity_epoch(run.run_id)
    stamp: float | None
    if status == "unusable":
        return "unknown", None
    if status == "ok":
        stamp = jsonl_epoch
    elif status == "missing":
        return "unknown", None
    else:
        with self._activity_lock:
            stamp = self._last_activity.get(key)
    if stamp is None:
        return "unknown", None
```

Fallo concreto: `ProcessLifecycle(audit=<callable sin current_path>, daemon_generation="gen-A")`, con un run activo cuyo proceso nació en `T`, ejecuta `_capture_start_activity` en el start; después `box_occupancy(now=T+100)` devuelve `activity_state="recent"` y `last_activity_age_s=100.0` aunque no hay ningún `lifecycle_start_outcome` legible en audit. Contraprueba ejecutada sobre esta copia: `REPRO_NO_AUDIT_PATH None 0 recent 100.0`.

Es producto porque contradice P2 —la basal debe salir del evento acreditado, no del proceso— y el fail-closed ante ausencia. Es además una puerta alternativa del defecto ya corregido: la ruta canónica con `JsonlAuditWriter` usa el audit, pero el contrato genérico `AuditCallback` conserva el comportamiento antiguo. Fix sugerido: eliminar `_capture_start_activity` como fuente de verdad y hacer que `status == "absent"` devuelva `("unknown", None)` igual que `missing`; cualquier optimización en memoria debe derivarse de un evento durable ya leído, no de `creation_time_utc`.

### ALTA — H-02: la caché puede publicar `recent` después de un fallo del escritor que ya hizo el estado sticky `unknown`

`ws/tools/dayz_mcp/process_lifecycle.py:958` y `:2602`

```python
# [EXACT] ws/tools/dayz_mcp/process_lifecycle.py:958-977
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
        self._activity_persist_attempted.add(key)
        if not ok:
            self._activity_unknown.add(key)
            return False
        if key not in self._activity_unknown:
            self._last_activity[key] = epoch
            if self._audit_jsonl_path() is not None:
                self._jsonl_activity_written.add(key)
    return True

# [EXACT] ws/tools/dayz_mcp/process_lifecycle.py:2612-2621
if now is None and self._box_cache is not None:
    cached_at, cached = self._box_cache
    if time.monotonic() - cached_at < _BOX_OCCUPANCY_CACHE_S:
        return _copy_box(cached)
snapshot = self._box_occupancy_uncached(
    time.time() if now is None else float(now)
)
if now is None:
    self._box_cache = (time.monotonic(), snapshot)
return _copy_box(snapshot)
```

El censo completo de asignaciones confirma que las mutaciones de actividad no invalidan la caché:

```text
# [EXACT] rg "_box_cache =" ws/tools/dayz_mcp/process_lifecycle.py
822: self._box_cache: tuple[float, dict[str, object]] | None = None
2473: self._box_cache = None
2620: self._box_cache = (time.monotonic(), snapshot)
```

Fallo concreto: una primera `box_occupancy()` cachea `recent`; inmediatamente `record_command_activity` falla y añade la clave a `_activity_unknown`; una segunda lectura dentro de 1,5 s devuelve todavía `recent/10.0`, mientras la lectura no cacheada devuelve `unknown/null`. Contraprueba ejecutada: `REPRO_CACHE_HIDES_WRITER_FAILURE False recent recent 10.0 unknown None`.

La misma omisión oculta una escritura buena: tras cachear `stale`, un enqueue aceptado escribe actividad durable, pero la lectura inmediata sigue dando `stale/901.0`; la no cacheada da `recent/0.0` (`REPRO_CACHE stale stale recent 901.0 0.0`). Incluso sin escrituras, una edad cacheada puede cruzar el umbral de 900 s sin cambiar de estado.

Es producto porque rompe la memoria sticky fail-closed y la regla exacta `<=900 recent`, `>900 stale` en la superficie real: `loopback.py:237-240` llama `reader()` sin `now`, que es precisamente la rama cacheada. La actividad sigue sin ser autoridad, por lo que el daño es diagnóstico, no un stop/reap indebido. Fix sugerido: invalidar/sincronizar `_box_cache` en toda mutación de actividad (éxito y fallo), o no cachear los campos temporales y recalcular estado/edad sobre la evidencia retenida; la solución debe evitar introducir orden inverso entre `_activity_lock` y `_operation_lock`.

### MEDIA — H-03: cualquier evento llamado `run_command_activity` se acepta como comando real, sin acreditar `reason` ni `decision`

`ws/tools/dayz_mcp/process_lifecycle.py:1011`

```python
# [EXACT] ws/tools/dayz_mcp/process_lifecycle.py:1011-1026
event_name = payload.get("event")
is_command = event_name == "run_command_activity"
is_start = (
    event_name == "lifecycle_start_outcome"
    and payload.get("decision") == "started"
    and payload.get("state") == "RUNNING"
)
if not is_command and not is_start:
    continue
if payload.get("run_id") != run_id:
    continue
event_generation = payload.get("daemon_generation")
if not isinstance(event_generation, str) or not event_generation:
    return "unusable", None
if event_generation != generation:
    continue

# [EXACT] ws/tools/dayz_mcp/process_lifecycle.py:1027-1043
stamp: float | None = None
if is_command:
    epoch = payload.get("activity_epoch")
    if (
        isinstance(epoch, (int, float))
        and not isinstance(epoch, bool)
        and epoch == epoch
    ):
        stamp = float(epoch)
    else:
        stamp = _utc_epoch(payload.get("timestamp_utc"))
else:
    stamp = _utc_epoch(payload.get("timestamp_utc"))
if stamp is None:
    return "unusable", None
if latest is None or stamp > latest:
    latest = stamp
```

Fallo concreto: audit contiene para el mismo `(daemon_generation, run_id)` un evento `run_command_activity` con `reason="activity_observed"`, `decision="recorded"` y `activity_epoch=T+800`. A `T+950`, el start basal ya es stale, pero el producto devuelve `recent/150.0`. Contraprueba ejecutada: `REPRO_PROVENANCE recent 150.0`.

Es producto porque la invariante heredada restringe este evento al enqueue aceptado y se pidió expresamente que el audit distinguiera actividad real de otra procedencia. Hoy el resto muerto de (a) emite exactamente ese `reason`, y el lector lo consagraría como actividad real si reapareciera un call-site. Fix sugerido: para eventos de comando exigir al menos `reason == "enqueue_accepted"` y `decision == "recorded"`; un registro con el nombre reservado pero semántica incompatible debe volver ambiguo ese run (`unknown/null`), no renovar su actividad.

### MEDIA — H-04: `-Infinity` pasa por el parser y produce `stale/Infinity` en vez de fail-closed

`ws/tools/dayz_mcp/process_lifecycle.py:1028` y `:1064`

```python
# [EXACT] ws/tools/dayz_mcp/process_lifecycle.py:1028-1037
if is_command:
    epoch = payload.get("activity_epoch")
    if (
        isinstance(epoch, (int, float))
        and not isinstance(epoch, bool)
        and epoch == epoch
    ):
        stamp = float(epoch)
    else:
        stamp = _utc_epoch(payload.get("timestamp_utc"))

# [EXACT] ws/tools/dayz_mcp/process_lifecycle.py:1064-1071
if stamp is None:
    return "unknown", None
if stamp > clock:
    return "unknown", None
age = clock - stamp
if age <= _ACTIVITY_STALE_S:
    return "recent", round(age, 3)
return "stale", round(age, 3)
```

Fallo concreto: una línea con `"activity_epoch":-Infinity` no es JSON válido, pero `json.loads` de Python la acepta como `float('-inf')`; `epoch == epoch` sólo descarta NaN. La comparación de futuro es falsa, la resta da `inf` y el producto devuelve `activity_state="stale"`, `last_activity_age_s=inf`. Contraprueba ejecutada: `REPRO_NEG_INFINITY True stale inf True`.

Es producto porque se exige fail-closed a `unknown/null` ante JSON inválido y porque esta rama publica una edad que no puede sostener. Fix sugerido: rechazar constantes no JSON en `json.loads` y exigir `math.isfinite(float(epoch))` antes de aceptar `activity_epoch`; aplicar la misma finitud al reloj antes de calcular la edad.

### Comprobaciones solicitadas sin hallazgo

- **La actividad no es autoridad.** El único consumidor de `_run_activity` es la proyección de la fila (`process_lifecycle.py:2652-2663`); `occupied` se decide separadamente con `bool(runs or foreign)` (`:2691-2697`). `start_run` bloquea por estados activos del manifest (`:1381-1442`), `stop_run` por propiedad/estado/identidad (`:1804-1880`) y reap por liveness del proceso (`:2427-2488`). No hay referencia a `activity_state` ni `last_activity_age_s` en esas decisiones.
- **Locks.** No encontré adquisición anidada inversa: `_operation_lock` puede llegar a `_capture_start_activity`, que toma brevemente `_activity_lock`; los escritores de actividad no toman `_operation_lock`, y `_run_activity` suelta `_activity_lock` antes de I/O. No hay deadlock demostrable entre ambos. El defecto concurrente real es H-02: `_box_cache` está fuera de ambos contratos.
- **Generaciones.** `JsonlAuditWriter` sobrescribe todo evento con su generación (`runtime_state.py:320-322`) y el lector descarta generaciones distintas (`process_lifecycle.py:1022-1026`). En reinicio, los runs heredados pasan a `RUNNING_IDLE` (`process_lifecycle.py:748-773`) y, sin actividad durable de la generación nueva, quedan `unknown`; no encontré continuidad falsa por esta vía.
- **P1 canónico.** Con `JsonlAuditWriter.write` directo, una llamada a `box_occupancy` seguida de `occupancy_error_fields` dejó `events.jsonl` byte a byte idéntico: `CONTROL_P1_BYTES_EQUAL True recent 10.0`.

## LAS DOS ABIERTAS

### (a) `_ensure_activity_persisted`: se va

```python
# [EXACT] ws/tools/dayz_mcp/process_lifecycle.py:979-988
def _ensure_activity_persisted(self, run_id: str) -> None:
    key = self._activity_key(run_id)
    with self._activity_lock:
        if key in self._activity_unknown or key in self._activity_persist_attempted:
            return
        epoch = self._last_activity.get(key)
        if epoch is None:
            return
        self._activity_persist_attempted.add(key)
    self._write_command_activity(run_id, epoch, reason="activity_observed")
```

Hoy no es un fallo observable porque el censo del árbol no encuentra call-sites, así que no lo elevo a hallazgo. No debe quedarse: es una entrada alternativa que viola la invariante «`run_command_activity` sólo en enqueue aceptado», conserva literalmente el `reason` del defecto anterior y H-03 demuestra que el lector todavía lo acepta como evidencia real. El cambio mínimo es eliminar la función; después, eliminar sólo el estado que quede realmente huérfano según grep (`_activity_persist_attempted` y, si sigue sin consumidores, `_jsonl_activity_written`).

### (b) lookup completo de un solo fichero: es defecto, no aceptable

```python
# [EXACT] ws/tools/dayz_mcp/process_lifecycle.py:990-1002
def _latest_jsonl_activity_epoch(self, run_id: str) -> tuple[str, float | None]:
    path = self._audit_jsonl_path()
    if path is None:
        return "absent", None
    if not path.is_file():
        return "missing", None
    generation = self._activity_key(run_id)[0]
    latest: float | None = None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "unusable", None
    for line in text.splitlines():

# [EXACT] ws/tools/dayz_mcp/runtime_state.py:26-27
AUDIT_MAX_BYTES = 5 * 1024 * 1024
AUDIT_BACKUPS = 5

# [EXACT] ws/tools/dayz_mcp/runtime_state.py:269-273
paths = [self.current_path]
paths.extend(
    Path(f"{self.current_path}.{index}")
    for index in range(1, AUDIT_BACKUPS + 1)
)
```

La omisión de backups es un defecto funcional de P2: forcé una rotación real, el `lifecycle_start_outcome` acreditado quedó en `events.jsonl.1`, el current contenía un evento válido no relacionado y el resultado fue `REPRO_ROTATION True unknown None`. La evidencia durable existe y pertenece a la generación/run correctos, pero el producto no la lee.

La lectura de 5 MB está acotada en bytes **por fichero y por run**, pero no en trabajo por snapshot: `_box_occupancy_uncached` invoca el parser completo una vez por cada run activo (`process_lifecycle.py:2648-2652`). Por tanto es `O(runs × líneas del audit)` en cada miss de caché; eso incumple la intención «acotado» aunque no falsifique por sí solo la edad de un único run. Debe recorrerse `events.jsonl` más los cinco backups con un presupuesto explícito y construir en una sola pasada el resultado para todos los run IDs activos; no releer el mismo corpus por run.

## BACKLOG

- **`status`/`public_status` pueden escribir y mutar ante un run legacy.** `status` y `public_status` llaman `_legacy_identity_error` (`process_lifecycle.py:2552-2569`), que ejecuta `self.manifest.quarantine_legacy_active(self.audit)` (`:1073-1077`); este método emite `legacy_identity_quarantined` y persiste `UNRECONCILED` (`:640-698`). Contraprueba: `STATUS_SIDE_EFFECT False None UNRECONCILED`. No es hallazgo de P1-P3: no sale del lookup de actividad y es el gate de migración legacy preexistente; se registra aquí porque el encargo pidió censar toda puerta de lectura hacia `self.audit`.
- **El propio escritor reserializa el current completo en cada append.** `runtime_state.py:339-349` hace `read_text()` y `_atomic_write_text(previous + line)`. Es coste general del audit, no comportamiento de P1-P3 ni del lookup revisado.
- **Los tests con `AuditSink` fijan el fallback no durable.** `test_box_occupancy.py:424-506` construye el lifecycle sin `current_path` y espera `recent/stale` desde la hora del proceso. Corregir esa expectativa será necesario al cerrar H-01, pero la cobertura como tal no es producto.

## NO VERIFICADO

- La suite `python -m unittest tests.test_box_occupancy` no llegó a ejecutar casos: la copia carece de la dependencia `mcp` y falló al importar `mcp.server.fastmcp`. Sí pasó `python -m py_compile` sobre los cinco ficheros entregados y sí se ejecutaron directamente las contrapruebas descritas arriba contra las clases reales.
- No se hizo estrés multihilo con rotación simultánea del writer; la revisión de orden de locks fue estática y los fallos H-01–H-04 se reprodujeron de forma determinista.
- No se arrancó un daemon/HTTP real ni DayZ; se revisó y ejecutó el componente Python aislado.
- `ws` no contiene metadatos `.git`, así que no se pudo contrastar un diff/base ni verificar commit o branch. El objeto revisado fue exclusivamente `C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\938664ff-361c-4575-aa56-c7c06c7ca2a8\scratchpad\lote-G\ws`.
