## VEREDICTO

**SEGURO PARA INTEGRAR este delta: 0 hallazgos bloqueantes o accionables.** P19'', P19''-b y P19''-c cumplen el contrato revisado sin regresiones observadas en P1-P22' ni en H1-H5.

Última línea literal de los tres gates ejecutados sobre `../ws`:

```text
bash gate/run.sh
ORACULO-VERDE

PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python ../../gate-extra/oracle_lote_g.py
ORACULO-VERDE

bash gate/suite.sh
SUITE-ACOTADA OK
```

## HALLAZGOS

Ninguno.

### P19'' — la adopción exige evidencia durable y una muestra actual del proceso

El predicado usa el registro persistido y consulta el guard en cada adopción; no lee una marca del `ServerState` ni una foto cacheada:

`../ws/tools/dayz_mcp/process_lifecycle.py:2171`

```python
def _identity_matches(record: ProcessRecord, actual: object) -> bool:
    if not isinstance(actual, dict) or actual.get("identity_complete") is not True:
        return False
    return (
        actual.get("identity_scheme") == record.identity_scheme
        and actual.get("pid") == record.pid
        and actual.get("creation_time_utc") == record.creation_time_utc
        and str(actual.get("executable_sha256", "")).casefold() == record.executable_sha256.casefold()
        and str(actual.get("command_line_sha256", "")).casefold() == record.command_line_sha256.casefold()
    )
```

`../ws/tools/dayz_mcp/process_lifecycle.py:2189`

```python
try:
    actual = self.guard.snapshot(record.pid)
except Exception:
    return "unknown", "guard_unavailable"
...
if self._identity_matches(record, actual):
    return "owned", ""
if actual.get("identity_complete") is True:
    return "foreign", "process_identity_mismatch"
return "unknown", "process_identity_mismatch"
```

El guard productivo crea un `psutil.Process(pid)` y toma una muestra nueva; no conserva snapshots entre llamadas (`../ws/tools/dayz_mcp/native_process_guard.py:150`):

```python
def snapshot(self, pid: int) -> dict[str, object]:
    if not _valid_pid(pid):
        return _identity_failure(pid, "identity_unavailable", 3)
    try:
        process = self._process(pid)
        return self._snapshot_process(process, pid)
    except Exception as error:
        if _is_psutil_error(error, "NoSuchProcess"):
            return _identity_failure(pid, "process_not_found", 4)
        return _identity_failure(pid, "identity_unavailable", 3)
```

La partición completa se evalúa bajo `_operation_lock`; `unknown` conserva prioridad fail-closed y la ausencia total de `owned` rechaza tanto `gone`, como `foreign`, como su mezcla (`../ws/tools/dayz_mcp/process_lifecycle.py:2498`, `:2515`):

```python
with self._operation_lock:
    ...
    buckets, unknown_reason = self._partition_registered_processes(run.processes)
    if buckets["unknown"]:
        reason = unknown_reason or "process_identity_mismatch"
        return self._reject_reserved(
            authority,
            command,
            reason,
            503 if reason == "guard_unavailable" else 409,
        )
    if not buckets["owned"]:
        result = self._reject_reserved(
            authority, command, "run_processes_gone"
        )
        result["hint"] = _RUN_PROCESSES_GONE_HINT
        return result
```

Sonda adicional no perteneciente a los gates: un run con un PID `gone` y otro `foreign`, sin `owned`, devolvió `run_processes_gone`, HTTP 409, dejó el estado durable en `RUNNING_IDLE` y no intentó terminar el PID extranjero.

Una reutilización de PID sólo clasifica `owned` si también coinciden exactamente `creation_time_utc`, esquema, hash del ejecutable y hash de argv. Una colisión simultánea de los cinco campos sería indistinguible bajo la identidad contractual existente; no encontré un estado cacheado adicional que permita acreditar falsamente un proceso.

Los dos caminos productivos convergen en el mismo método: HTTP reenvía directamente a `adopt_run` (`../ws/tools/dayz_mcp/loopback.py:3219`) y el worker privado usado por `dayz_test_tool` invoca el comando lifecycle `adopt` (`../ws/tools/dayz_mcp/dayz_test_worker.py:524`, `:592`).

### P19''-c — `dispatchable` observa el `ServerState` presente con el orden de locks requerido

La adopción persiste `RUNNING`, invalida la cache, retira el cerco y sólo entonces consulta la despachabilidad, todavía dentro de `_operation_lock` (`../ws/tools/dayz_mcp/process_lifecycle.py:2542`):

```python
run.owner_session_id = client.session_id
run.owner_lease_id = authority[1]
run.state = "RUNNING"
try:
    self.manifest.replace(run)
except Exception:
    self._finish_committed(authority, command_id)
    return self._error("manifest_failed", 503)
self._invalidate_box_cache()
self._unfence_runs([run_id])
self._finish_committed(authority, command_id)
dispatchable = self._adopt_dispatchable(run_id)
```

El segundo lock se toma dentro del único checker (`../ws/tools/dayz_mcp/loopback.py:1102`):

```python
def run_has_bound_binding(self, run_id: str) -> bool:
    """True iff a present binding for this run is BOUND. STARTING does not dispatch."""

    if not isinstance(run_id, str) or not run_id:
        return False
    with self._lock:
        return any(
            _binding_run_id(binding) == run_id and binding.state == BINDING_BOUND
            for binding in self._bindings.values()
        )
```

Por tanto, el orden observado es `_operation_lock -> ServerState._lock`. No encontré el inverso: las llamadas de `ServerState` hacia lifecycle bajo `_lock` llegan a tumbas de actividad o al manifiesto y no toman `_operation_lock` (`../ws/tools/dayz_mcp/loopback.py:1213`, `:1236`).

En el estado que se responde, un `True` no convive con los tres bloqueos preguntados: el cerco se retira en `process_lifecycle.py:2551`, el manifiesto ya acredita `RUNNING` en `:2546`, y el checker sólo existe sobre el `ServerState` conectado. El enqueue vuelve a comprobar bajo el mismo `ServerState._lock` que haya un binding BOUND y que el estado durable sea `RUNNING|STARTING` (`../ws/tools/dayz_mcp/loopback.py:1314`, `:1767`); el poll revalida otra vez antes de entregar (`../ws/tools/dayz_mcp/loopback.py:2193`, `:2215`). Las denegaciones ortogonales posteriores —lease caducado, cuarentena, versión o cola llena— no contradicen el significado acotado de esta bandera: presencia de algún binding BOUND para el run.

`False` tampoco habilita despacho en los casos del contrato: sin binding no hay candidato y `STARTING` devuelve `binding_not_ready` (`../ws/tools/dayz_mcp/loopback.py:1327`); tras reinicio no existe binding en el `ServerState` nuevo. El daemon productivo cablea siempre el mismo `state` como `bindings` (`../ws/tools/dayz_mcp/daemon.py:530`), por lo que no hay dos superficies divergentes.

La clave nueva es aditiva. El handler HTTP sólo extrae `_http_status` y serializa el resto (`../ws/tools/dayz_mcp/loopback.py:3236`); el CLI imprime el dict completo sin esquema cerrado (`../ws/tools/dayz_mcp/lifecycle_cli.py:138`, `:169`); y el worker valida únicamente `ok`, `run_id` y `state` (`../ws/tools/dayz_mcp/dayz_test_worker.py:333`). `dayz_test_tool.py` delega la petición al launcher/worker y no compara la respuesta de adopt contra un dict exacto (`../ws/tools/dayz_mcp/dayz_test_tool.py:615`). `server.py` consume el resultado final de `dayz_test_tool`, no el dict interno de adopt (`../ws/tools/dayz_mcp/server.py:3048`).

### P19''-b — retirada completa de la marca volátil

El grep exacto sobre ambos archivos del producto devolvió cero coincidencias para:

```text
_retired_run_ids
run_bindings_retired
_bindings_retired_pending_reap
binding_retired_pending_reap
_BINDING_RETIRED_PENDING_REAP_HINT
```

La retirada terminal elimina los bindings presentes bajo `ServerState._lock` (`../ws/tools/dayz_mcp/loopback.py:1049`) y la adopción posterior no consulta historia volátil. Si el persist terminal falla después de retirar bindings, un proceso aún `owned` permite adoptar pero declara `dispatchable: False`; si ninguno queda `owned`, rechaza `run_processes_gone`. Un reap exitoso deja `EXITED`, no `RUNNING_IDLE` (`../ws/tools/dayz_mcp/process_lifecycle.py:2952`). No encontré un camino de reap/reinicio que fabrique `dispatchable: True` después de retirar el único binding.

## FAMILIAS

Familia visitada: **P19 / adopción de un run durable pero no utilizable**. Esta tercera delta cambia la autoridad desde memoria histórica a evidencia actual y cierra las variantes `gone`, `foreign`, mezcla `gone+foreign`, reinicio con proceso muerto y reinicio con proceso vivo sin binding. No apareció una familia nueva.

## BACKLOG

Sin backlog accionable de este lote.

Matiz no bloqueante: cuando `ProcessLifecycle` se construye artificialmente con `bindings is None`, `dispatchable` es correctamente `False`, pero el único hint atribuye la causa a un reinicio (`../ws/tools/dayz_mcp/process_lifecycle.py:286`, `:1030`, `:2560`). Ese texto es exacto para el daemon productivo —que siempre inyecta `ServerState`— y coincide con el contrato pedido; sería causalmente impreciso sólo para consumidores alternativos del constructor que hoy no forman parte del producto.

No actualicé la memoria Obsidian: el encargo limita la escritura al dictamen en este directorio y no produjo un hallazgo durable nuevo que registrar.

## NO VERIFICADO

- No arranqué un daemon HTTP real ni el launcher nativo. El camino HTTP se verificó por lectura y por `test_lifecycle_http` dentro de `gate/suite.sh`; el camino `dayz_test_tool -> worker -> lifecycle CLI` se verificó por lectura y por las suites acotadas.
- No forcé en Windows una reutilización real de PID con coincidencia de creación a microsegundo, ejecutable y argv. Verifiqué el mecanismo y los cinco campos, no la imposibilidad física absoluta de una colisión completa.
- No ejecuté la suite global ni DayZ in-game; el brief autoriza y exige únicamente los tres gates acotados.
- El orden de locks se verificó por el grafo de llamadas y las pruebas existentes, no mediante una campaña prolongada de estrés/deadlock.
- No inyecté `MemoryError` ni una excepción artificial dentro de `ServerState.unfence_runs`/`run_has_bound_binding`; sus cuerpos no hacen I/O y la revisión cubrió sus caminos ordinarios y los fallos explícitamente modelados.
- Como en cualquier snapshot de procesos, un proceso puede morir inmediatamente después de que el guard lo clasifique `owned`. No existe una exclusión mutua con el kernel; el reaper es el mecanismo de convergencia. No afirmo que la muestra convierta la vida futura del proceso en una garantía.
