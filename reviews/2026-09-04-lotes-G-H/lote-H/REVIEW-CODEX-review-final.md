## VEREDICTO

NO: no es seguro integrarlo; H-01 reabre un `fail-open` de resultados tardíos que la base de G ya cerraba, y H-02/H-03 incumplen dos cierres explícitos del contrato.

## HALLAZGOS

### ALTA — H-01: la retirada terminal se persiste antes de retirar el binding

`../ws/tools/dayz_mcp/process_lifecycle.py:1048` centraliza los seis caminos del anillo, pero el orden literal es persistir `EXITED` y sólo después retirar el binding:

```python
# [EXACT] ../ws/tools/dayz_mcp/process_lifecycle.py:1048
def _commit_retirement(
    self, run: RunRecord, event: str, reason: str, decision: str
) -> bool:
    # Persist first; only then retire bindings and publish the diagnostic.
    try:
        self.manifest.replace(run)
    except Exception:
        return False
    self._invalidate_box_cache()
    binding_reason = _BINDING_REASON_BY_EVENT.get(event, reason)
    self._retire_run_bindings(run.run_id, binding_reason)
    self._retire_run_diagnostic(run, event, reason, decision, "EXITED")
    self._forget_run_residues(run.run_id)
    return True
```

En cinco de los seis alimentadores —`begin_release_owner`, `repair_recovery_fault`, `repair_manifest_recovery`, reap y `admin_reconcile`— no hay retirada anterior al helper. Por ejemplo, reap prepara `EXITED` y llama directamente al helper en `../ws/tools/dayz_mcp/process_lifecycle.py:2879`:

```python
# [EXACT] ../ws/tools/dayz_mcp/process_lifecycle.py:2879
owner_session_id = run.owner_session_id
run.owner_session_id = None
run.owner_lease_id = None
run.processes = []
run.state = "EXITED"
if not self._commit_retirement(
    run,
    "run_reaped",
    "all_processes_gone_or_foreign",
    "reaped",
):
```

Mientras `_retire_run_bindings` aún no ha corrido, `store_result` sólo consulta si la instancia ya figura como retirada (`../ws/tools/dayz_mcp/loopback.py:2447`):

```python
# [EXACT] ../ws/tools/dayz_mcp/loopback.py:2447
with self._lock:
    fence = self._command_fence.get(command_id)
    if fence is not None:
        target_instance, target_epoch, _target_pid = fence
        if target_instance in self._retired_instances:
            return 409, {"error": "binding_retired"}
```

Fallo concreto reproducido: encolé y despaché `camera_get`, forcé un reap, detuve el hilo al entrar en `_retire_run_bindings` y envié el resultado. Durante esa ventana el manifiesto ya decía `EXITED`, pero el resultado se almacenó como válido:

```text
{'enqueue_status': 200, 'poll_status': 200, 'delivered_ids': [1],
 'durable_during_window': 'EXITED',
 'store_result': (200, {'ok': True, 'id': 1, 'ok_value': True}),
 'reaped': ['run-existing']}
```

Esto no es una tolerancia: acredita un resultado tardío después de que la autoridad durable declaró terminado el run. Además es una validación debilitada respecto a la base, que retiraba antes de persistir (`../../lote-G/ws-frozen-r8/tools/dayz_mcp/process_lifecycle.py:2566`):

```python
# [EXACT] ../../lote-G/ws-frozen-r8/tools/dayz_mcp/process_lifecycle.py:2566
run.state = "EXITED"
self._retire_run_bindings(run.run_id, "reaped")
try:
    self.manifest.replace(run)
```

Fix sugerido: retirar el binding antes de `manifest.replace` en el helper común; mantener la publicación del diagnóstico después de una persistencia correcta. La retirada previa debe ser idempotente para el camino de stop, que ya llega retirado. Añadir un test de carrera que bloquee entre persistencia y retirada y exija `409 binding_retired` al resultado.

### MEDIA — H-02: la limpieza terminal borra la tumba y permite aterrizar un crédito anterior

El enqueue toma el sello temporal bajo el lock del loopback, pero escribe la actividad después de soltarlo (`../ws/tools/dayz_mcp/loopback.py:1760`):

```python
# [EXACT] ../ws/tools/dayz_mcp/loopback.py:1760
if not internal and isinstance(commanded_run_id, str) and commanded_run_id:
    activity_epoch = time.time()

try:
    if not internal:
        self._note_run_command_activity(
            owner_client, run_id=commanded_run_id, now=activity_epoch
        )
except Exception:
    pass
```

La retirada crea correctamente una tumba, pero `_commit_retirement` llama después a `_forget_run_residues`, que borra tanto el dato como esa misma frontera (`../ws/tools/dayz_mcp/process_lifecycle.py:1212`):

```python
# [EXACT] ../ws/tools/dayz_mcp/process_lifecycle.py:1212
def _forget_run_residues(self, run_id: str) -> None:
    with self._activity_lock:
        doomed = [
            key
            for key in (
                set(self._last_activity)
                | set(self._activity_tombstone)
                | set(self._activity_unknown)
                | set(self._compensating_runs)
            )
            if key[1] == run_id
        ]
        for key in doomed:
            self._last_activity.pop(key, None)
            self._activity_tombstone.pop(key, None)
            self._activity_unknown.discard(key)
            self._compensating_runs.discard(key)
```

Sin tumba, `_seal_activity_locked` vuelve a aceptar el sello viejo (`../ws/tools/dayz_mcp/process_lifecycle.py:1251`):

```python
# [EXACT] ../ws/tools/dayz_mcp/process_lifecycle.py:1251
tombstone = self._activity_tombstone.get(key)
if tombstone is not None and epoch <= tombstone:
    return
current = self._last_activity.get(key)
if current is None or epoch >= current:
    self._last_activity[key] = epoch
```

Fallo concreto reproducido: bloqueé el escritor después de aceptar el comando, completé reap hasta `EXITED` y solté después el escritor. La limpieza estaba vacía antes de soltarlo y el crédito antiguo reapareció:

```text
{'enqueue': (200, {'id': 1, 'peer': 'client', 'cmd': 'camera_get'}),
 'reaped': ['run-existing'], 'durable': 'EXITED',
 'before_release': {'last': None, 'tombstone': None},
 'after_release': {'last': 1788500029.4495974, 'tombstone': None}}
```

El mismo fallo queda `fail-open` en una transición no terminal si crear la tumba lanza una excepción: `ServerState._tombstone_run_activity` la absorbe en `../ws/tools/dayz_mcp/loopback.py:1166`, la transición a `RUNNING_IDLE` continúa y el crédito demorado se publica como reciente. Reproducción:

```text
{'enqueue': (200, {'id': 1, 'peer': 'client', 'cmd': 'camera_get'}),
 'durable': 'RUNNING_IDLE', 'activity_state': 'recent',
 'last_activity_age_s': 0.003}
```

El impacto es corrupción diagnóstica, no una ampliación de autoridad: la actividad no autoriza comandos. Fix sugerido: al terminar, limpiar dato/`unknown`/compensación pero conservar o recrear una frontera terminal posterior a cualquier sello aceptable; una nueva basal posterior puede superar esa frontera. Si la creación de tumba falla durante el cerco, abortar la transición conservando el cerco o publicar `unknown`, nunca continuar como si la frontera existiera. Añadir dos tests deterministas para ambos interleavings.

### MEDIA — H-03: un estado durable mal formado se interpreta como permiso de enqueue

`_durable_run_state` distingue una excepción del getter, pero convierte un objeto durable sin `state: str` —y también un getter ausente— en `None` (`../ws/tools/dayz_mcp/loopback.py:1185`):

```python
# [EXACT] ../ws/tools/dayz_mcp/loopback.py:1185
def _durable_run_state(self, run_id: str | None) -> str | None:
    if not isinstance(run_id, str) or not run_id:
        return None
    lifecycle = self.lifecycle
    if lifecycle is None:
        return None
    manifest = getattr(lifecycle, "manifest", None)
    getter = getattr(manifest, "get", None)
    if not callable(getter):
        return None
    try:
        run = getter(run_id)
    except Exception:
        return _DURABLE_UNREADABLE
    if run is None:
        return "EXITED"
    state = getattr(run, "state", None)
    return state if isinstance(state, str) else None
```

El consumidor trata precisamente ese `None` como autorización (`../ws/tools/dayz_mcp/loopback.py:1231`):

```python
# [EXACT] ../ws/tools/dayz_mcp/loopback.py:1231
state = self._durable_run_state(run_id)
if state == _DURABLE_UNREADABLE:
    return "run_state_unavailable"
if state in {"RUNNING", "STARTING"}:
    return None
if state is None:
    return None
```

Fallo concreto reproducido con un proveedor durable que devuelve un registro ilegible:

```text
{'status': 200,
 'payload': {'id': 1, 'peer': 'client', 'cmd': 'camera_get'}}
```

El poll vuelve a comprobar y no entrega el comando, de modo que el daño observado es admisión positiva/encolado y posible crédito de actividad, no ejecución remota. Sigue contrariando «estado durable ilegible ⇒ rechazo» y es una rama `fail-open`. Fix sugerido: con `lifecycle` presente, getter ausente o registro presente con `state` no textual deben devolver `_DURABLE_UNREADABLE`; reservar el permiso únicamente para `RUNNING`/`STARTING`. Añadir fixtures separados para getter que lanza, getter ausente y registro mal formado.

## FAMILIAS

No aparece una familia nueva.

- H-01 pertenece a **ciclo de vida asimétrico del cerco** y **invariante atada al call-site**. La manifestación concreta nace de la composición G×H: el helper del anillo unificó cinco call-sites y cambió el orden que G mantenía.
- H-02 pertenece a **borrado-sin-tumba** y **sello-vs-dato**. La carrera terminal es una interacción nueva entre la limpieza de residuos de H y el crédito demorado de G, pero la familia ya estaba visitada.
- H-03 pertenece a **identidad/autoridad del destino**. No es debilitamiento introducido por H: la rama `state is None` ya existía en la base; el cierre de H sólo corrigió el caso «getter lanza».

No encontré inversión del orden final de locks. Los caminos mutadores observados siguen `_operation_lock → ServerState._lock → _activity_lock`; las lecturas de status/caja sólo toman `_activity_lock` en secciones separadas, y `_forget_run_residues` suelta `_activity_lock` antes de llamar `unfence_runs`. Tampoco encontré una fila no terminal publicada junto al diagnóstico del mismo run: el filtro de `../ws/tools/dayz_mcp/process_lifecycle.py:1094` lo impide incluso en la ventana de publicación.

## BACKLOG

Ninguno. No traslado a backlog ninguno de los tres hallazgos: todos incumplen propiedades explícitas del lote fusionado. No evalué como defectos de este lote gates, oráculos, ledgers, briefs, `server.py`, `daemon.py`, `runtime_state.py`, rendimiento del caché de sondas, estilo ni tipado.

## NO VERIFICADO

- No se ejecutó una integración con procesos DayZ reales/Windows; los gates y las tres reproducciones usan los dobles controlados del repositorio.
- No se hizo model checking exhaustivo de todos los schedules de hilos. Sí se forzaron de forma determinista las tres ventanas descritas y se obtuvieron los outputs pegados arriba.
- No se ejecutó la suite del árbol base `ws-frozen-r8`; sólo se hizo comparación literal contra él para localizar debilitamientos.
- Hashes verificados antes de revisar: `process_lifecycle.py` = `70179879d1a0af8e0d2adcf625cd9d1e3d46ee8a7e56a5476dd52ed89729c6e0`; `loopback.py` = `d607e62e8f37f8ea8a3d6169a8c5e333f7f158ab1cc2cec353a02ad721d3230e`; `dayz_test_tool.py` = `bca97e26e3a7d9cef96846bee6559eebc938dbbd90e46b3eab6b0f75019f4b5d`.
- `bash ../ws/gate/run.sh` — exit 0; última línea: `ORACULO-VERDE`.
- `bash ../ws/gate/suite.sh` — exit 0; última línea: `SUITE-ACOTADA OK`.
- Desde `../ws/tools`, `PYTHONPATH=. python ../../gate-extra/oracle_lote_g.py` — exit 0; última línea: `ORACULO-VERDE`.
