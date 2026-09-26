## VEREDICTO

**NO es seguro integrarlo todavía:** hay 1 hallazgo ALTA y 2 MEDIA, todos reproducidos contra los hashes pedidos. Los tres gates están verdes, pero no cubren estas ramas.

## HALLAZGOS

### ALTA — P22 sigue fail-open cuando falta `lifecycle`: enqueue, `exec_enforce` y poll despachan sin estado durable

**Ubicación:** `../ws/tools/dayz_mcp/loopback.py:1193`, `:1215`, `:1229`, `:1805`, `:1840`, `:2155` y `:2177`.

[EXACT — producto]

```python
def _durable_run_state(self, run_id: str | None) -> str | None:
    if not isinstance(run_id, str) or not run_id:
        return None
    lifecycle = self.lifecycle
    if lifecycle is None:
        return None
```

```python
def _poll_should_hold_commands(self, run_id: str | None) -> bool:
    if self._run_is_fenced(run_id):
        return True
    if self.lifecycle is None:
        return False
    return not self._run_is_dispatchable(run_id)
```

```python
def _enqueue_run_rejection(
    self, run_id: str | None, *, mutation: bool, internal: bool
) -> str | None:
    _ = (mutation, internal)
    if not isinstance(run_id, str) or not run_id:
        return None
    if self._run_is_fenced(run_id):
        return "run_not_owned"
    if self.lifecycle is None:
        return None
```

**Fallo concreto:** un peer `BOUND` confirmado en un `ServerState` sin `lifecycle` puede encolar y recibir comandos aunque no exista ninguna prueba durable de que su run está `RUNNING`/`STARTING`. `_enqueue_exec` consulta el mismo `_enqueue_fence_target` antes y después de su auditoría (`loopback.py:1805-1815` y `:1840-1850`), de modo que `exec_enforce` hereda el permiso. El poll también lo permite explícitamente y vuelve a permitirlo en sus dos revalidaciones (`:2155-2159`, `:2177-2184`). Esto es una omisión de autoridad, no una tolerancia.

[EXACT — repro ejecutado]

```text
AssertionError: P22: lifecycle=None autorizo enqueue: 200 {'id': 1, 'peer': 'client', 'cmd': 'camera_get'}
```

En otra ejecución del mismo fixture público `prepare` → `confirm`, el poll devolvió:

```text
poll (200, {'commands': [{'id': 1, 'cmd': 'camera_get', 'args': {}}], 'delay_ms': 0, 'bind': 'BOUND'})
```

Los demás casos pedidos sí quedan cerrados cuando `lifecycle` existe: excepción del getter produce `_DURABLE_UNREADABLE` (`loopback.py:1203-1206`); getter ausente, registro ausente o `state` malformado acaban en rechazo (`:1199-1210`, `:1239-1246`).

**Fix sugerido [DESIGN]:** tratar `lifecycle is None` como estado durable no disponible: enqueue/`exec_enforce` deben devolver `503 run_state_unavailable` y poll debe retener/vaciar comandos. Mantener la cola legacy no mutante sin `run_id` como compatibilidad separada.

### MEDIA — P20: un escritor viejo cuyo audit falla atraviesa la frontera terminal y vuelve a crear `unknown`

**Ubicación:** `../ws/tools/dayz_mcp/process_lifecycle.py:1246-1278` y `:1294-1323`.

[EXACT — producto]

```python
for key in keys:
    self._last_activity.pop(key, None)
    self._activity_unknown.discard(key)
    self._compensating_runs.discard(key)
    self._raise_activity_tombstone_locked(key, frontier)
```

```python
with self._activity_lock:
    if not ok:
        self._activity_unknown.add(key)
    elif key not in self._activity_unknown:
        self._seal_activity_locked(key, epoch)
    self._bump_box_revision_locked()
```

El filtro de la tumba sólo vive en `_seal_activity_locked`:

```python
tombstone = self._activity_tombstone.get(key)
if tombstone is not None and epoch <= tombstone:
    return
```

**Fallo concreto:** el writer muestrea su `epoch` antes de retirar, queda bloqueado en el audit, `_seal_terminal` limpia residuos y eleva la tumba, y después el audit devuelve fallo. La rama `not ok` añade `unknown` sin comparar `epoch` con la tumba. Un `run_id` reutilizado en la misma generación hereda ese `unknown`; incluso un crédito nuevo exitoso no lo limpia porque `unknown` es sticky (`process_lifecycle.py:1320`). El resultado observado al reutilizar fue:

```text
{'started': True, 'fresh_credit_returned': True, 'activity_state': 'unknown', 'unknown_sticky': True}
```

[EXACT — repro concurrente ejecutado]

```text
AssertionError: P20: el escritor muestreado antes de retirar reintrodujo unknown tras la tumba terminal
```

La parte positiva de P20 sí está bien: `frontier` toma el máximo de `at`, todos los stamps y todas las tumbas del `run_id` (`process_lifecycle.py:1251-1277`), por lo que un crédito **exitoso** muestreado bajo el lock del loopback justo antes de retirar no reaparece; una basal/crédito realmente nuevo, con `epoch > tombstone`, puede aterrizar (`:1300-1305`). El fallo es exclusivamente la rama de audit fallido.

**Fix sugerido [DESIGN]:** bajo `_activity_lock`, aplicar primero la frontera temporal también a la rama `not ok`; un intento con `epoch <= tombstone` debe descartarse sin recrear `unknown`. Un fallo posterior a la frontera sí debe conservar el comportamiento fail-closed sticky.

### MEDIA — P19: tras fallar la persistencia, `adopt_run` confirma `RUNNING` pero no reconstruye el binding retirado

**Ubicación:** `../ws/tools/dayz_mcp/process_lifecycle.py:1048-1060`, `:2462-2522`; `../ws/tools/dayz_mcp/loopback.py:1023-1037`, `:1288-1291`.

[EXACT — producto]

```python
binding_reason = _BINDING_REASON_BY_EVENT.get(event, reason)
self._retire_run_bindings(run.run_id, binding_reason)
try:
    self.manifest.replace(run)
except Exception:
    return False
self._best_effort_post_persist_retirement(run, event, reason, decision)
```

`RunManifestStore.replace` restaura el registro previo si falla el disco (`process_lifecycle.py:755-767`), pero la retirada del binding ya no se revierte. Después, `adopt_run` sólo cambia el manifiesto:

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
return {"ok": True, "run_id": run_id, "state": "RUNNING"}
```

No reminta ni reinstala el binding que `retire_run` quitó del índice y marcó como retirado (`loopback.py:1027-1036`).

**Fallo concreto:** forcé `OSError("disk full")` en la persistencia terminal del reaper. El manifiesto quedó `RUNNING_IDLE`, el binding quedó retirado; restauré el store y llamé a `adopt_run`. Resultado:

```text
AssertionError: P19: adopcion confirma RUNNING pero el anillo sigue retirado: adopt={'ok': True, 'run_id': 'run-existing', 'state': 'RUNNING'}, enqueue=409 {'error': 'binding_retired', 'hint': 'Target instance was retired (stop, reap, or role replace). Relaunch that role via dayz_test_run so start_run mints a new instance. Resending this command will keep failing until the new instance is BOUND.'}
```

No queda irrecuperable: si el registro restaurado está en uno de `_REAPABLE_STATES` (`process_lifecycle.py:36-39`), el siguiente reap vuelve a converger; `STOPPING` necesita recovery/admin/restart. Pero **no es adoptable de forma correcta**: la adopción da éxito falso y deja el run mudo hasta esa reparación.

**Fix sugerido [DESIGN]:** impedir la adopción de un run cuyo binding terminal fue retirado, devolviendo un error que dirija al reap/recovery, o definir una reconstrucción acreditada del binding antes de responder `ok`. No restaurar a ciegas el binding previo después de un fallo de persistencia: ya se drenó y se retiró por seguridad.

### Comprobaciones solicitadas sin hallazgo adicional

- **H-01, orden en los seis caminos:** el orden común sí quedó corregido: retirar `:1054`, persistir `:1056`, publicar post-persist `:1059`. Los seis callers son stop `:2419`, cleanup de release `:2640`, recovery repair `:2767`, manifest recovery `:2856`, reaper `:2937` y admin reconcile `:3390`. El hallazgo anterior es la consecuencia de recuperación tras un fallo, no una recaída del orden.
- **Idempotencia de stop:** no rompe nada observado. La segunda retirada encuentra cero entradas en `_role_index`, vuelve a descartar el fence y sólo eleva monotónicamente la tumba (`loopback.py:1023-1037`; `process_lifecycle.py:1287-1292`).
- **Anillo/sobre:** el diagnóstico sigue entrando únicamente después de `manifest.replace` correcto (`process_lifecycle.py:1055-1059`, `:1069-1072`). Invalidate, diagnóstico y sello están aislados individualmente; un fallo post-persist registra degradación best-effort y el fallo del sello intenta bajar el fence (`:1062-1091`).
- **P21:** no encontré una ruta donde una llamada válida a `tombstone_run_activity` lance y la transición continúe. `fail_closed=True` relanza (`loopback.py:1180-1185`) y `_transition_to_idle` desfencea y relanza sin ejecutar `persist` (`process_lifecycle.py:1647-1658`). El caso de `lifecycle` ausente no lanza: es el fail-open P22 ya reportado.
- **Locks:** el delta mantiene `ProcessLifecycle._operation_lock` → `ServerState._lock` → `ProcessLifecycle._activity_lock`; `_seal_terminal` suelta `_activity_lock` antes de llamar a `unfence_runs` (`process_lifecycle.py:1251-1285`). No encontré adquisición inversa nueva ni E/S dentro de `_activity_lock` en el sello. La espera de `/status` ya aceptada no se ha agravado por este delta.
- **Validaciones respecto a `ws-frozen-r4`:** no desapareció ninguna validación en los hunks. Para `lifecycle` presente, el cambio endurece `None`/malformado a `run_state_unavailable`; la excepción `lifecycle is None` ya existía y quedó sin endurecer.
- **P1-P18 / H restante:** no hallé otra regresión delta. `dayz_test_tool.py` no difiere y su SHA-256 coincide con el declarado.

### Gates ejecutados

[EXACT — última línea de cada ejecución]

```text
bash ../ws/gate/run.sh
ORACULO-VERDE

(desde ../ws/tools) PYTHONPATH=. python ../../gate-extra/oracle_lote_g.py
ORACULO-VERDE

bash ../ws/gate/suite.sh
SUITE-ACOTADA OK
```

Los tres procesos terminaron con exit code `0`. La suite ejecutó 466 tests; los repros dirigidos anteriores terminaron con exit code `1` por sus assertions de seguridad, como se muestra.

## FAMILIAS

- **Nuevas: 0.**
- **Visitadas: 3.** P22 continúa la familia H-03 (autoridad durable/`None`); P20 continúa H-02 (tumba terminal contra writers tardíos); la adopción tras fallo de persistencia continúa H-01 (retirada antes de persistir y recuperación del estado intermedio). Son ramas nuevas dentro de familias ya abiertas, no una cuarta familia.

## BACKLOG

No abro backlog adicional. La cola legacy no mutante sin binding (`loopback.py:1313`) es tolerancia de compatibilidad ya existente y no la cuento como defecto; la mutación legacy sigue rechazándose en `:1273-1294`. `server.py`, `daemon.py`, la espera de `/status` y el despliegue real están fuera de este lote.

## NO VERIFICADO

- No ejecuté DayZ ni un daemon real; la revisión es de código, gates y repros deterministas de `ServerState`/`ProcessLifecycle`.
- No provoqué un fallo físico real del filesystem. El camino de persistencia fallida se verificó inyectando `OSError("disk full")` sobre `RunManifestStore.replace`; el rollback real está leído en `process_lifecycle.py:755-767`.
- No hice stress/fuzz prolongado de concurrencia ni medición de latencia. Sí forcé de forma determinista la intercalación writer-before-retire / writer-after-seal con eventos de `threading`.
- No revisé archivos declarados fuera del producto salvo las líneas mínimas necesarias para ejecutar los gates. Identidad del producto verificada: `process_lifecycle.py` = `c140bd23a5eb94544a572dbec66047d05312d9af28a8538425151d4cc8d80889`; `loopback.py` = `d015c1a5d7eb421ec257512f224020ed34fbd75c6a999e0e8bce9f16033eacae`; `dayz_test_tool.py` = `bca97e26e3a7d9cef96846bee6559eebc938dbbd90e46b3eab6b0f75019f4b5d`.
