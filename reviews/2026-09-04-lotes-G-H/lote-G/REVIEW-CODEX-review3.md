## VEREDICTO

**NO es seguro integrarlo todavía:** el audit ya no es fuente de verdad y la actividad no decide `occupied`, stop ni reap, pero hay cuatro fallos reproducidos dentro del contrato (P3, aislamiento por generación y dos caminos de caché); el oráculo 18/18 y la suite acotada 302/302 permanecen verdes porque no ejercitan esos estados.

## HALLAZGOS

### MAYOR — H-01: el enqueue conoce el `run_id` comandado, lo descarta y puede acreditar otro run

`tools/dayz_mcp/loopback.py:1531-1570` obtiene el binding exacto que recibió el comando, pero al registrar actividad sólo conserva la identidad del cliente:

```python
 1531:             fence_error_code, queue, fence_instance = self._enqueue_fence_target(
 1532:                 peer, cmd
 1533:             )
...
 1557:             binding = (
 1558:                 self._bindings.get(fence_instance) if fence_instance else None
 1559:             )
 1560:             self._seal_command(command_id, fence_instance, binding)
...
 1568:         try:
 1569:             if not internal:
 1570:                 self._note_run_command_activity(owner_client)
```

No es una inferencia desde el dueño: el binding productivo ya guarda el `run_id` en `tools/dayz_mcp/loopback.py:954-965`:

```python
  954:         with self._lock:
  955:             self._station_epoch += 1
  956:             self._bindings[minted] = Binding(
  957:                 instance=minted,
  958:                 run_id=run_id,
  959:                 role=role,
  960:                 epoch=self._station_epoch,
  961:                 pid=None,
  962:                 creation_time_utc=None,
  963:                 state=BINDING_STARTING,
  964:             )
  965:             self._role_index[(run_id, role)] = minted
```

La rama `exec_enforce` repite la pérdida en `tools/dayz_mcp/loopback.py:1634-1686`. Después, `tools/dayz_mcp/loopback.py:1691-1702` pasa sólo `owner_session`:

```python
 1691:     def _note_run_command_activity(self, owner_client: ClientIdentity | None) -> None:
 1692:         lifecycle = self.lifecycle
 1693:         recorder = getattr(lifecycle, "record_box_command_activity", None)
 1694:         if not callable(recorder):
 1695:             return
 1696:         owner_session = (
 1697:             owner_client.session_id
 1698:             if owner_client is not None
 1699:             and isinstance(getattr(owner_client, "session_id", None), str)
 1700:             else None
 1701:         )
 1702:         recorder(now=time.time(), owner_session=owner_session)
```

`tools/dayz_mcp/process_lifecycle.py:935-947` reconstruye entonces el destino por dueño o por cardinalidad, no por el binding:

```python
  935:         active = [run for run in runs if run.state in _ACTIVE_STATES]
  936:         targets: list[RunRecord] = []
  937:         if isinstance(owner_session, str) and owner_session:
  938:             owned = [
  939:                 run for run in active if run.owner_session_id == owner_session
  940:             ]
  941:             if len(owned) == 1:
  942:                 targets = owned
  943:         if not targets and len(active) == 1:
  944:             targets = active
  945:         for run in targets:
  946:             try:
  947:                 self.record_command_activity(run.run_id, now=epoch)
```

Fallo concreto reproducido sobre las clases reales: manifiesto con `run-1` propiedad de A y `run-2` en `RUNNING_IDLE`; único binding `server` apuntando a `run-2`; A encola un `world_time_set` aceptado. Resultado:

```text
status=200
binding_run=run-2
activity_events=[('run-1', 'enqueue_accepted')]
```

El comando queda sellado y encolado para `run-2`, pero el único evento y la frescura se atribuyen a `run-1`. Con varios activos sin dueño coincidente, el mismo defecto deja cero eventos. Esto viola P2 y P3; es corrupción diagnóstica, no autoridad de lifecycle.

Fix sugerido: capturar `binding.run_id` bajo `ServerState._lock` y pasarlo explícitamente a `record_command_activity(run_id)`, en ambas ramas. El fallback por dueño/cardinalidad sólo debería existir para una cola legacy que realmente carezca de binding; nunca debe sustituir una identidad exacta ya disponible.

### MAYOR — H-02: extender un run heredado acredita en la generación nueva un timestamp de la generación anterior

El reinicio conserva el run y lo deja sin dueño (`tools/dayz_mcp/process_lifecycle.py:748-767`):

```python
  748:     def recover_after_restart(self) -> dict[str, list[str]]:
...
  756:             for run_id, current in list(self._runs.items()):
  757:                 run = self._clone(current)
  758:                 if run.state == "RUNNING" and run.owner_session_id is not None:
  759:                     run.owner_session_id = None
  760:                     run.owner_lease_id = None
  761:                     run.state = "RUNNING_IDLE"
  762:                     released.append(run_id)
```

Al extenderlo después de adoptarlo, `start_run` añade el proceso nuevo a todos los heredados y llama a la captura (`tools/dayz_mcp/process_lifecycle.py:1501-1506`):

```python
 1501:                 completed = RunRecord.from_payload(dataclasses.asdict(provisional))
 1502:                 completed.processes.append(record)
 1503:                 completed.state = "RUNNING"
 1504:                 self._capture_start_activity(completed)
 1505:                 try:
 1506:                     self.manifest.replace(completed)
```

La captura elige el mínimo de **todos** los procesos (`tools/dayz_mcp/process_lifecycle.py:906-919`), aunque algunos precedan al daemon actual:

```python
  906:     def _capture_start_activity(self, run: RunRecord) -> None:
  907:         stamps = [
  908:             stamp
  909:             for stamp in (
  910:                 _utc_epoch(record.creation_time_utc) for record in run.processes
  911:             )
  912:             if stamp is not None
  913:         ]
...
  916:         key = self._activity_key(run.run_id)
  917:         with self._activity_lock:
  918:             if key not in self._activity_unknown and key not in self._last_activity:
  919:                 self._last_activity[key] = min(stamps)
```

Fallo concreto reproducido: generación A crea `run-1` el 15-jul; generación B lo recupera como `RUNNING_IDLE`, lo adopta y el 4-sep añade el rol servidor. El mapa de B estaba vacío. Tras el `start_run` aceptado:

```text
key=('gen-B', 'run-1')
stored_stamp=1784073641.0   # proceso heredado de A
new_process_stamp=1788480000.0
box a new_process+10s => activity_state='stale', last_activity_age_s=4406369.0
```

La generación B afirma una basal anterior a su propia existencia. Por tanto, la credencial «este daemon arrancó este run en esta generación» no está implementada para la extensión de un run heredado.

Fix sugerido: hacer que la captura reciba el `record` recién acreditado y use sólo su `creation_time_utc`. Si la credencial debe significar estrictamente “creó el run completo”, ejecutar la basal sólo cuando `existing is None` y mantener un run heredado en `unknown` hasta el primer enqueue externo aceptado.

### MEDIA — H-03: la basal de `start_run` muta actividad sin invalidar una caché ya poblada

`tools/dayz_mcp/process_lifecycle.py:916-919` escribe `_last_activity` sin llamar a `_invalidate_box_cache`:

```python
  916:         key = self._activity_key(run.run_id)
  917:         with self._activity_lock:
  918:             if key not in self._activity_unknown and key not in self._last_activity:
  919:                 self._last_activity[key] = min(stamps)
```

La lectura pública reutiliza durante 1,5 s cualquier snapshot anterior (`tools/dayz_mcp/process_lifecycle.py:2513-2531`; TTL en `:37`):

```python
 2513:     def box_occupancy(self, *, now: float | None = None) -> dict[str, object]:
...
 2523:         if now is None and self._box_cache is not None:
 2524:             cached_at, cached = self._box_cache
 2525:             if time.monotonic() - cached_at < _BOX_OCCUPANCY_CACHE_S:
 2526:                 return _copy_box(cached)
...
 2530:         if now is None:
 2531:             self._box_cache = (time.monotonic(), snapshot)
```

Fallo concreto reproducido: `box_occupancy()` siembra `occupied=False, runs=[]`; después `start_run` devuelve `ok=True`; la lectura inmediata sin `now` sigue devolviendo `occupied=False, runs=[]`, mientras una lectura directa devuelve `occupied=True` y `activity_state='recent'`. No autoriza un segundo start —`start_run` consulta el manifiesto—, pero la respuesta de actividad/ocupación publicada es falsa durante el TTL.

Fix sugerido: invalidar la caché después de soltar `_activity_lock` cuando `_capture_start_activity` haya insertado realmente la basal. Esto no sustituye el arreglo de H-04.

### MEDIA — H-04: una lectura concurrente puede republicar después de la invalidación el estado pre-fallo

`tools/dayz_mcp/process_lifecycle.py:951-966` actualiza el sticky y luego pone la caché a `None`:

```python
  951:     def _write_command_activity(self, run_id: str, epoch: float, *, reason: str) -> bool:
...
  961:         with self._activity_lock:
  962:             if not ok:
  963:                 self._activity_unknown.add(key)
  964:             elif key not in self._activity_unknown:
  965:                 self._last_activity[key] = epoch
  966:         self._invalidate_box_cache()
```

Pero `box_occupancy` calcula sin lock/revisión y publica incondicionalmente al final (`tools/dayz_mcp/process_lifecycle.py:2523-2531`, bloque citado en H-03). Secuencia reproducida con barreras:

1. T1 lee el sello viejo en `_run_activity` y se bloquea en `_diag_snapshot`.
2. T2 recibe fallo del escritor, añade el key a `_activity_unknown` e invalida.
3. T1 termina y asigna a `_box_cache` el snapshot viejo, **después** de la invalidación.
4. La siguiente lectura pública consume esa caché.

Resultado medido:

```text
writer_ok=False
racing_read='stale'
cached_after='stale', last_activity_age_s=4401514.016
direct_truth='unknown', last_activity_age_s=None
```

El fallo de escritor no queda fail-closed en la respuesta pública hasta que caduca la caché. No observé un deadlock; observé una carrera de publicación.

Fix sugerido: mantener una revisión de actividad/caché. La mutación incrementa la revisión bajo `_activity_lock`; el lector captura esa revisión y sólo publica su snapshot si no cambió. Un lock que cubra todo `_box_occupancy_uncached` también cerraría la carrera, pero mantendría bloqueado durante sondas de procesos y argv.

## LAS DOS ABIERTAS

**(a) Es un hueco real, no un caso imposible.** `start_run` evita crear voluntariamente otro run activo, pero `RunManifestStore._load/add` acepta varios registros activos: `tools/dayz_mcp/process_lifecycle.py:544-563,603-614` valida cada registro y unicidad de `run_id`, no cardinalidad activa. El propio conjunto de tests construye ese estado. Además, el binding ya contiene el `run_id` exacto; no hay razón para convertir un estado degradado en pérdida o falsificación de actividad. H-01 muestra las dos consecuencias: varios sin dueño ⇒ cero registro; dueño distinto del binding ⇒ registro en el run equivocado.

**(b) No hay ciclo ABBA en el código actual, pero la caché no es segura.** Las únicas adquisiciones de `_activity_lock` están en `tools/dayz_mcp/process_lifecycle.py:917,961,971`; ninguna toma después `_operation_lock`. `start_run` sí sigue `_operation_lock → _activity_lock` (`:1270` y `:1504`), así que evitar el orden inverso es correcto. El problema no es deadlock: `_box_cache` queda fuera de ambos locks y permite H-03/H-04.

## SOBRE H-01

**Me convence para un run fresco; no como afirmación universal.** En un alta nueva, el sello procede del `ProcessRecord` recién construido desde una snapshot con `identity_complete=True` y esquema `psutil-argv-v2` (`tools/dayz_mcp/process_lifecycle.py:1624-1642`), y no del audit. Ahí la basal en memoria sí tiene procedencia suficiente.

No me convence la frase «el daemon arrancó este run en esta generación» mientras exista H-02: al extender un run heredado, el daemon nuevo copia a su key de generación el timestamp mínimo de un proceso que arrancó el daemon anterior. Reformulada como «el daemon recuerda un sello que él mismo obtuvo durante `start_run`» es literalmente cierta, pero demasiado débil: no impide afirmar continuidad entre generaciones.

La eliminación del lector sí disuelve el H-01 anterior relativo a procedencia del JSONL: búsquedas sobre todo `tools/` dieron cero ocurrencias de `_latest_jsonl_activity_epoch`, `_ensure_activity_persisted`, `_jsonl_activity_written`, `_activity_persist_attempted`, `_audit_jsonl_path` y `activity_observed`. El único camino de lectura de actividad es ahora `_run_activity` sobre los dos contenedores en memoria.

También confirmé la no-autoridad: `occupied` se calcula sólo como `bool(runs or foreign)` en `tools/dayz_mcp/process_lifecycle.py:2602-2608`; stop decide por run/owner/lease/state/identidad en `:1713-1737`; reap por estado y `_run_all_dead` en `:2442-2456`. No hay ningún condicional sobre `activity_state` o `last_activity_age_s` fuera de su proyección diagnóstica.

## BACKLOG

- El helper `tools/tests/fence_helpers.py:10` usa por defecto `run_id="test-run"`; `RunCommandActivityTest._bind_state` lo llama sin pasar el `run_id` real (`tools/tests/test_box_occupancy.py:670-674`). Así, los tests verdes de enqueue ya comandaban un binding distinto de `run-1` y luego aceptaban la atribución por cardinalidad. Es un defecto del gate/cobertura, expresamente fuera del producto de este lote.
- La caché tampoco se invalida en varias transiciones generales de lifecycle (adopt/release/stop). Sólo clasifiqué como hallazgo lo que afecta directamente a la nueva basal o al sticky de actividad; la coherencia global de `box_occupancy` merece un lote separado.
- `runtime_state.py` conserva el escritor/rotación conocidos y queda fuera de alcance según el briefing; no lo convierto en hallazgo de esta ronda.

## NO VERIFICADO

- No ejecuté la suite completa de 2.321 tests; estaba prohibida para este lote. Ejecuté el oráculo actual (18/18 PASS) y las seis suites acotadas (302/302 OK).
- No lancé daemon host, DayZ ni prueba in-game. Las cuatro reproducciones son offline y usan las clases reales más los fakes ya existentes del repo.
- No repetí la saturación Windows de `os.replace` con lectores externos; el lector de actividad ya no existe y `runtime_state.py` está fuera de alcance.
- No pude revisar un diff contra la ronda anterior: `..\ws` no contiene metadatos Git. Revisé los bytes actuales; SHA-256: `process_lifecycle.py=4C9E60F68108DFD71D7CCF2205D7D8CF420A18B7DBF3439AE66188705F4D8EEA`, `loopback.py=5EF2E51C83CF05B8B119DDB45482ADB3B7AB7B8F99D08328B228449763DEE971`.
- No audité consumidores fuera del árbol entregado en `..\ws`; dentro de ese árbol hice búsqueda completa de símbolos y condicionales de actividad.
