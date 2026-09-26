## VEREDICTO

NO es seguro integrarlo: reproduje seis incumplimientos de P1–P4; los seis tienen intercalación ejecutable y pertenecen a familias ya visitadas, no a una familia nueva.

## HALLAZGOS

### MAYOR — H1. La última superposición de actividad todavía puede perder una carrera y devolver un valor que ya es falso

`../ws/tools/dayz_mcp/process_lifecycle.py:988-1022` y `:2589-2605`.

```python
# [EXACT] ../ws/tools/dayz_mcp/process_lifecycle.py:988-1003
def _activity_for_run_id(
    self, run_id: str, clock: float
) -> tuple[str, float | None]:
    key = self._activity_key(run_id)
    with self._activity_lock:
        if key in self._activity_unknown:
            return "unknown", None
        stamp = self._last_activity.get(key)
    if stamp is None:
        return "unknown", None
    if stamp > clock:
        return "unknown", None
    age = clock - stamp
    if age <= _ACTIVITY_STALE_S:
        return "recent", round(age, 3)
    return "stale", round(age, 3)

# [EXACT] ../ws/tools/dayz_mcp/process_lifecycle.py:1008-1022
def _with_live_activity(
    self, box: dict[str, object], clock: float
) -> dict[str, object]:
    runs = box.get("runs")
    if not isinstance(runs, list):
        return box
    for item in runs:
        ...
        state, age = self._activity_for_run_id(run_id, clock)
        item["activity_state"] = state
        item["last_activity_age_s"] = age
    return box

# [EXACT] ../ws/tools/dayz_mcp/process_lifecycle.py:2592-2605
with self._activity_lock:
    cached = self._box_cache
...
if time.monotonic() - cached_at < _BOX_OCCUPANCY_CACHE_S:
    return self._with_live_activity(_copy_box(cached_snapshot), clock)
...
return self._with_live_activity(_copy_box(snapshot), clock)
```

Fallo concreto: bloqueé un lector justo después de que `_activity_for_run_id` leyera `recent`; mientras seguía dentro de `box_occupancy`, hice fallar un `record_command_activity`, que terminó y dejó el run sticky `unknown`. Al liberar el lector, éste devolvió `recent/0.009` aunque la memoria viva ya decía `unknown/null`:

```text
writer_ok=false
truth=['unknown', null]
returned=['recent', 0.009]
```

N9 mueve la lectura al final, pero no protege esa lectura final. P1 exige expresamente que quien pierde la carrera no devuelva la actividad anterior. Corrección sugerida: tomar una instantánea coherente de todos los campos de actividad bajo `_activity_lock` y usar la liberación de ese lock como punto de linealización; no volver a adquirirlo por run ni dejar una ventana entre leer el último run y terminar la lectura.

### MAYOR — H2. Un `unknown` sin binding más viejo puede borrar una actividad acreditada más nueva

`../ws/tools/dayz_mcp/process_lifecycle.py:940-966` frente a la guarda monótona de `:968-985`.

```python
# [EXACT] ../ws/tools/dayz_mcp/process_lifecycle.py:947-966
epoch = time.time() if now is None else float(now)
...
runs = list(self.manifest.list_runs())
...
active = [run for run in runs if run.state in _ACTIVE_STATES]
with self._activity_lock:
    for run in active:
        self._last_activity.pop(self._activity_key(run.run_id), None)
    self._bump_box_revision_locked()

# [EXACT] ../ws/tools/dayz_mcp/process_lifecycle.py:978-985
with self._activity_lock:
    if not ok:
        self._activity_unknown.add(key)
    elif key not in self._activity_unknown:
        current = self._last_activity.get(key)
        if current is None or epoch >= current:
            self._last_activity[key] = epoch
    self._bump_box_revision_locked()
```

Fallo concreto: el hilo U entró en `record_box_command_activity` sin binding, capturó su epoch y quedó bloqueado al listar runs. Después, el hilo B terminó una escritura acreditada posterior y la lectura dio `recent/0.0`. Cuando U reanudó, hizo `pop` sin comparar epochs y el resultado retrocedió a `unknown/null`:

```text
before_release=['recent', 0.0]
after_release=['unknown', null]
```

P3 requiere `unknown` para una observación sin binding, pero P4 requiere que una observación anterior no sustituya a una posterior acreditada. La rama sin binding ignora el `epoch` que ya calculó. Corrección sugerida: aplicar también aquí un high-water mark; sólo puede invalidar un sello cuya época no sea posterior a la observación sin binding.

### MAYOR — H3. El `start_run` rechazado sigue pudiendo dejar frescura si un comando entra durante la ventana `BOUND` previa al commit del manifiesto

`../ws/tools/dayz_mcp/process_lifecycle.py:1530-1563`, `:1235-1265`; `../ws/tools/dayz_mcp/loopback.py:977-994`, `:1535-1581`.

```python
# [EXACT] ../ws/tools/dayz_mcp/process_lifecycle.py:1534-1563
record = self._record_from_snapshot(actual, launch_role)
...
self._confirm_instance(minted, record)

completed = RunRecord.from_payload(dataclasses.asdict(provisional))
completed.processes.append(record)
completed.state = "RUNNING"
try:
    self.manifest.replace(completed)
except Exception:
    self._retire_minted(run_id, launch_role, minted, "launch_failed")
    return self._settle_failed_launch(...)
self._capture_start_activity(completed, launched=record)

# [EXACT] ../ws/tools/dayz_mcp/loopback.py:989-994
binding.pid = pid
binding.creation_time_utc = creation
if isinstance(role, str) and role:
    binding.role = role
binding.state = BINDING_BOUND

# [EXACT] ../ws/tools/dayz_mcp/loopback.py:1565-1581
binding = self._bindings.get(fence_instance) if fence_instance else None
self._seal_command(command_id, fence_instance, binding)
commanded_run_id = _binding_run_id(binding)
...
self._note_run_command_activity(
    owner_client, run_id=commanded_run_id
)
```

Fallo concreto: extendí `run-existing` con un rol servidor. Después de `_confirm_instance`, el binding ya estaba `BOUND` para `run-existing`; antes del `manifest.replace(completed)` encolé `query_all_players`, que devolvió 200 y registró actividad. Forcé a fallar sólo ese replace. `_settle_failed_launch` terminó el PID nuevo y restauró el run heredado de un solo proceso, pero éste quedó `recent`:

```text
binding_before_failure={state:'BOUND', run_id:'run-existing'}
enqueue=[200, {cmd:'query_all_players', id:1, peer:'server'}]
start_result={error:'lifecycle_start_failed', _http_status:503, state:'RUNNING'}
restored_processes=1
activity_state='recent'
```

Mover la basal después del replace cerró el caso secuencial, no esta consecuencia concurrente. El comando se atribuyó al rol del intento rechazado y luego el mismo `run_id` volvió a representar sólo el proceso heredado. Corrección sugerida: no publicar el binding como `BOUND` hasta que el `RUNNING` que contiene ese proceso sea durable, y compensar cualquier actividad de un intento que no llega a ese commit.

### MAYOR — H4. `stop_run` publica `EXITED` antes de retirar el binding: todavía acepta un comando para un run ya muerto

`../ws/tools/dayz_mcp/process_lifecycle.py:1959-1991`; `../ws/tools/dayz_mcp/loopback.py:999-1012`, `:1535-1581`.

```python
# [EXACT] ../ws/tools/dayz_mcp/process_lifecycle.py:1959-1991
run.state = "EXITED"
run.owner_session_id = None
run.owner_lease_id = None
run.processes = []
try:
    self.manifest.replace(run)
except Exception:
    ...
self._invalidate_box_cache()
result = {
    "ok": True,
    "run_id": run_id,
    "state": "EXITED",
    "terminated": terminated,
}
self._retire_run_bindings(run_id, "stopped")

# [EXACT] ../ws/tools/dayz_mcp/loopback.py:1006-1012
def retire_run(self, run_id: str, reason: str) -> None:
    with self._lock:
        keys = [key for key in self._role_index if key[0] == run_id]
        for key in keys:
            instance = self._role_index.pop(key, None)
            if instance is not None:
                self._retire_instance_locked(instance, reason)
```

Fallo concreto: detuve el único PID, dejé que `manifest.replace(EXITED)` terminara y, dentro de `_invalidate_box_cache`, encolé `camera_get` antes de que `stop_run` alcanzara `_retire_run_bindings`. El binding seguía `BOUND`, su `run_id` ya estaba durablemente `EXITED`, el enqueue devolvió 200, dejó un comando en cola y escribió `run_command_activity` para ese run. Inmediatamente después la retirada descartó la cola:

```text
durable_state='EXITED'
binding_state='BOUND'
enqueue=[200, {cmd:'camera_get', id:1, peer:'client'}]
queued_before_retire=1
binding_exists_after=false
queue_exists_after=false
```

No es un binding reciclado ni de otro rol: es el binding correcto cuya vida excede la del run que acredita. El mismo intervalo empieza ya en `STOPPING`, porque la retirada sólo está en el final feliz. Corrección sugerida: al abandonar `RUNNING`, volver el binding no despachable como parte del mismo orden de transición; repetir el contrato en release, repair, reconcile y reap. Un run `UNRECONCILED` sin binding es la degradación fail-closed segura.

### MEDIA — H5. `repair_manifest_recovery` invalida antes de `recover_after_restart`, pero no después de la mutación final

`../ws/tools/dayz_mcp/process_lifecycle.py:2317-2383`.

```python
# [EXACT] ../ws/tools/dayz_mcp/process_lifecycle.py:2320-2328
restored = RunManifestStore(
    self.manifest.paths,
    checkpoint=self.manifest._checkpoint,
)
...
self.manifest = restored
self._invalidate_box_cache()
for run in restored.list_runs():
    ...

# [EXACT] ../ws/tools/dayz_mcp/process_lifecycle.py:2379-2383
try:
    restored.recover_after_restart()
except Exception:
    return self._manifest_repair_failure("manifest_drift")
return {
    "terminal_safe": True,
```

`recover_after_restart` cambia `RUNNING` con dueño a `RUNNING_IDLE` y `STARTING/STOPPING` a `UNRECONCILED` en `../ws/tools/dayz_mcp/process_lifecycle.py:748-780`.

Fallo concreto: durante esa llamada final hice una lectura después de la invalidez de `:2327` pero antes de la recuperación. La lectura cacheó `RUNNING`; `recover_after_restart` dejó durablemente `RUNNING_IDLE`; la reparación devolvió `terminal_safe=true`, revisión 1, y la lectura inmediata siguió publicando `RUNNING`:

```text
between_state='RUNNING'
durable_after='RUNNING_IDLE'
published_after='RUNNING'
revision=1
```

Es una degradación estructural acotada por el TTL de 1,5 s, no corrupción. Corrección sugerida: consumir el resultado de `recover_after_restart` e incrementar la revisión después si `released` o `unreconciled` no están vacíos.

### MEDIA — H6. La cuarentena legacy cambia la caja sin incrementar la revisión

`../ws/tools/dayz_mcp/process_lifecycle.py:640-698` y `:1025-1030`.

```python
# [EXACT] ../ws/tools/dayz_mcp/process_lifecycle.py:685-698
run = self._clone(current)
run.state = "UNRECONCILED"
run.owner_session_id = None
run.owner_lease_id = None
self._runs[run.run_id] = run
changed.append(run.run_id)
...
if changed:
    self._persist_locked()
...
return changed

# [EXACT] ../ws/tools/dayz_mcp/process_lifecycle.py:1025-1030
def _legacy_identity_error(self) -> dict[str, object] | None:
    try:
        self.manifest.quarantine_legacy_active(self.audit)
    except Exception:
        return self._error("legacy_identity_transition_failed", 503)
    return None
```

Fallo concreto: cacheé una caja con `run-existing` en `RUNNING` y con dueño; `status()` ejecutó la cuarentena y devolvió el run durable `UNRECONCILED`/sin dueño. `_box_revision` permaneció 0 y la siguiente `box_occupancy()` devolvió todavía el `RUNNING` cacheado:

```text
before_state='RUNNING'
status_state='UNRECONCILED'
durable_state='UNRECONCILED'
after_state='RUNNING'
revision=0
```

Esta ruta estaba en el BACKLOG de una revisión anterior; el alcance actual P2 dice explícitamente **toda** mutación de `runs`, por lo que ahora sí es hallazgo de producto. Corrección sugerida: conservar el `changed` que ya devuelve `quarantine_legacy_active` e invalidar cuando no esté vacío.

### Comprobaciones solicitadas sin hallazgo adicional

- **Orden de locks:** el único orden anidado encontrado es `_operation_lock → _activity_lock` mediante `_invalidate_box_cache`/`_capture_start_activity` (`process_lifecycle.py:902-932`). `box_occupancy` suelta `_activity_lock` antes de consultar manifiesto/diag (`:2592-2600`), y los escritores de actividad no toman `_operation_lock`. No encontré el orden inverso. H1 es una ventana de consistencia, no un deadlock.
- **Frontera de daemon:** la clave es exactamente `(daemon_generation, run_id)` (`process_lifecycle.py:898-900`); el daemon entrega la misma generación a `ServerState` y `ProcessLifecycle` (`daemon.py:332-357,519-538`), y los bindings no se persisten (`instance_fence.py:1-4`). No encontré mezcla entre generaciones. H3 es reciclado semántico dentro de la misma generación tras rollback, no fuga de otra generación.
- **`unknown` nominal sin binding:** la ruta sin `run_id` no acredita por dueño/cardinalidad y elimina sellos (`process_lifecycle.py:954-966`). No encontré un camino nominal que publique edad sin sello; H2 es exclusivamente orden temporal incorrecto.
- **Actividad no autoritativa:** la actividad sólo rellena las filas (`process_lifecycle.py:2632-2647`); `occupied` sigue siendo `bool(runs or foreign)` (`:2675-2682`), stop decide por manifest/owner/identidad (`:1743-1776`) y reap por `_REAPABLE_STATES` + `_run_all_dead` (`:2405-2467`). No encontré ningún condicional de autoridad sobre `activity_state` o `last_activity_age_s`.

## FAMILIAS

No hay una familia nueva. **Son instancias nuevas de familias viejas**, lo que significa que el problema ya no es un call-site aislado del parche:

- H1, H2, H5 y H6: **coherencia de caché/frescura**; la revisión y el high-water mark se propagaron a los caminos conocidos, pero no forman todavía un único contrato para lectores e invalidadores.
- H3 y H4: **identidad del destino y ciclo de vida del binding**; se conserva el `run_id` exacto, pero el intervalo de validez del binding no coincide con el intervalo durable del run.
- **Frontera de generación:** no encontré una instancia nueva en esta ronda.

## BACKLOG

- El gate y el oráculo quedan fuera del producto, pero no discriminan estos seis casos: `gate/run.sh` dio 25/25 y `gate/suite.sh` dio 319/319 mientras las seis contrapruebas anteriores fallaban. Deben añadirse mutantes/intercalaciones específicas si se reutilizan como cierre de otra ronda.
- No elevé refactors, estilo, tipado, `runtime_state.py`, `README-mcp.md` ni el sobre de `dayz_test_stop`; permanecen fuera del lote por el brief.

## NO VERIFICADO

- No ejecuté DayZ, un daemon HTTP persistente ni procesos reales. Las seis reproducciones fueron offline sobre las clases productivas, usando los fakes existentes y barreras de hilos deterministas.
- No ejecuté la suite global. Sí ejecuté la suite acotada pedida por el lote: 66 + 90 + 8 + 64 + 45 + 46 = 319 tests, todos OK; el oráculo dio 25/25.
- No forcé errores físicos de disco ni fallos reales del guard; los puntos de fallo se inyectaron en `manifest.replace` y en las barreras exactas descritas.
- `../ws` no contiene metadatos Git utilizables. El dictamen corresponde a `process_lifecycle.py` SHA-256 `EAF9C5946C096A1850415C0A47E388F0B4B3C1D2F93A76BD1A03EB721CCB49EE` y `loopback.py` SHA-256 `9BCA62828C6EDE33B77F76F5EFAFDA00C5869689BB4B45BD436AD9240E231CFD`.
- No se actualizó el vault de memoria: el encargo autorizó un único entregable en este directorio y el vault no es una raíz escribible de esta sesión; este dictamen es el registro durable del trabajo.
