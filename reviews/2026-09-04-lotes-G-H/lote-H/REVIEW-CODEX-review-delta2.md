## VEREDICTO

NO es seguro integrarlo todavía: P22′ y P20′ quedan cerrados, pero P19′ se reabre al reiniciar el daemon antes del siguiente reap; `adopt_run` devuelve éxito sobre un run sin binding y el primer comando mutante falla.

## HALLAZGOS

### MEDIA — P19′ depende de una marca volátil: se pierde al reiniciar justo cuando hace falta

Ubicación: `../ws/tools/dayz_mcp/loopback.py:906`, `../ws/tools/dayz_mcp/loopback.py:1051-1065`, `../ws/tools/dayz_mcp/process_lifecycle.py:1023-1033`, `../ws/tools/dayz_mcp/process_lifecycle.py:2502-2509`.

La retirada se recuerda sólo en un `set` del `ServerState`. Un objeto nuevo —la forma de esa memoria tras reiniciar— empieza vacío:

`[EXACT]` `../ws/tools/dayz_mcp/loopback.py:903-907`

```python
self._command_fence: dict[int, tuple[str, int, int]] = {}
self._test_identity_override: TestIdentityOverride | None = None
self._retired_instances: OrderedDict[str, None] = OrderedDict()
self._retired_run_ids: set[str] = set()
self._retired_roles: set[str] = set()
```

La persistencia terminal fallida deja la marca en el objeto viejo, porque se retira antes de intentar `replace`:

`[EXACT]` `../ws/tools/dayz_mcp/process_lifecycle.py:1065-1077`

```python
def _commit_retirement(
    self, run: RunRecord, event: str, reason: str, decision: str
) -> bool:
    # Binding retirement precedes the durable transition; the diagnostic
    # follows a successful persist. stop_run may already have retired.
    binding_reason = _BINDING_REASON_BY_EVENT.get(event, reason)
    self._retire_run_bindings(run.run_id, binding_reason)
    try:
        self.manifest.replace(run)
    except Exception:
        return False
    self._best_effort_post_persist_retirement(run, event, reason, decision)
    return True
```

Pero el guard de adopción interpreta como «no retirado» tanto la ausencia del servicio como una respuesta falsa —incluido el `set` vacío de una instancia nueva—:

`[EXACT]` `../ws/tools/dayz_mcp/process_lifecycle.py:1023-1033`

```python
def _bindings_retired_pending_reap(self, run_id: str) -> bool:
    bindings = self.bindings
    if bindings is None:
        return False
    checker = getattr(bindings, "run_bindings_retired", None)
    if not callable(checker):
        return False
    try:
        return bool(checker(run_id))
    except Exception:
        return False
```

Por ello se salta el rechazo de P19′ y se vuelve a persistir el run como `RUNNING`:

`[EXACT]` `../ws/tools/dayz_mcp/process_lifecycle.py:2502-2509,2533-2547`

```python
if run.state != "RUNNING_IDLE" or run.owner_session_id is not None:
    return self._reject_reserved(authority, command, "run_not_adoptable")
if self._bindings_retired_pending_reap(run_id):
    result = self._reject_reserved(
        authority, command, "binding_retired_pending_reap"
    )
    result["hint"] = _BINDING_RETIRED_PENDING_REAP_HINT
    return result

# ...

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

Fallo concreto reproducido: usé el fixture exacto de persistencia terminal fallida de `../ws/tools/tests/test_process_lifecycle.py:3536-3563`, sustituí después `ServerState` por uno recién construido y llamé a `adopt_run` antes del reap siguiente.

`[EXACT — salida de la sonda ejecutada]`

```text
{'first_reap': [],
 'marker_before_adopt': False,
 'adopted': {'ok': True, 'run_id': 'run-existing', 'state': 'RUNNING'},
 'state_after_adopt': 'RUNNING',
 'mutating_enqueue': [409, {'error': 'legacy_unbound', ...}],
 'second_reap': ['run-existing'],
 'final_state': 'EXITED'}
```

Consecuencia: no hay crash ni corrupción observada; hay una **degradación de recuperación** y una respuesta falsa de éxito. El siguiente reap converge, pero entre el reinicio y ese reap el cliente recibe un run supuestamente adoptado que no puede mutar. La ventana es alcanzable porque el servidor HTTP arranca y acredita estado antes de instalar el reaper (`../ws/tools/dayz_mcp/daemon.py:914-928,961-962`); `daemon.py` se usó sólo para probar alcanzabilidad, no se revisa ni se propone cambiarlo.

Además, la marca tiene la vida útil inversa a la necesaria: `retire_run` la añade (`loopback.py:1062-1065`) y sólo `prepare`/el helper de test la quitan (`loopback.py:1012,1094`). Tras un reap exitoso sigue presente —la sonda devolvió `True`— aunque el estado `EXITED` ya impide adoptar; con run IDs nuevos, el conjunto crece durante toda la vida del daemon. Al reiniciar sí se vacía, precisamente cuando aún era necesaria si el `replace` terminal había fallado.

Fix sugerido `[DESIGN]`: sin cambiar el formato persistente, hacer que `adopt_run` exija de forma fail-closed una acreditación actual de que el run conserva al menos un binding adoptable. El release normal conserva ese binding y mantiene N20; un `ServerState` recién creado no lo acredita y dirige al reap. Limpiar la marca histórica tras una transición terminal persistida evita el residuo por run. No reconstruir bindings a ciegas.

### Comprobaciones sin hallazgo

- **P22′:** cerrado. Todo binding `BOUND` pasa por `_enqueue_run_rejection`; estado `None` produce `run_state_unavailable` (`loopback.py:1313-1321,1335-1342`). El poll consulta la misma condición y vacía la entrega (`loopback.py:2140-2153`). `exec_enforce` comparte esa frontera. `store_result` sólo consume IDs ya conocidos y la valla de instancia (`loopback.py:2493-2538`): con `lifecycle is None` desde el inicio, ninguna rama normal puede crear un despacho BOUND que luego acredite; la aceptación de un resultado previamente despachado si el puntero se retirase dinámicamente no crea despacho ni crédito y no encontré ningún camino de producción que retire ese puntero. La cola legacy sin binding sigue entregando lecturas en embedded (`loopback.py:1349-1353,2157-2169`; test `test_loopback.py:1804-1812`). Las mutaciones legacy siguen rechazadas.
- **P20′:** cerrado. El escritor calcula una sola clave `(daemon_generation, run_id)` (`process_lifecycle.py:1175-1177`) y consulta la tumba de esa misma clave (`process_lifecycle.py:1324-1340`). `_seal_terminal` elimina residuos y compensación de todas las generaciones y eleva una frontera común (`process_lifecycle.py:1263-1295`). Un fallo con `epoch <= tombstone` se descarta; uno posterior añade sticky `unknown`, que es el comportamiento fail-closed pedido. `_compensating_runs` no borra esa frontera.
- **P19′ sin reinicio:** cerrado. La persistencia fallida deja `RUNNING_IDLE`, la marca bloquea adopción y el reap siguiente llega a `EXITED`. Un `stop_run` en ese intervalo devuelve `{'error': 'run_not_adopted', '_http_status': 409}` sin mutar y el reap posterior converge. La adopción legítima tras release normal sigue verde en N20.
- **Locks:** el delta añade `operation_lock -> ServerState._lock` al consultar la marca. No encontré camino inverso `ServerState._lock -> operation_lock`. `_seal_terminal` suelta `_activity_lock` antes de entrar en bindings (`process_lifecycle.py:1268-1295` frente a `:1296-1302`); no introduce inversión nueva.
- **Regresión/diff:** hashes finales coinciden con los tres declarados. El delta medido es `process_lifecycle.py +26/-1`, `loopback.py +43/-3`; `dayz_test_tool.py` conserva su hash. No desapareció ninguna validación respecto a `../ws-frozen-r5` fuera del cambio deliberado de `None` a rechazo.

### Gates ejecutados

- `bash gate/run.sh` — exit 0; última línea: `ORACULO-VERDE`
- Desde `../ws/tools`, `PYTHONPATH=. python ../../gate-extra/oracle_lote_g.py` — exit 0; última línea: `ORACULO-VERDE`
- `bash gate/suite.sh` — exit 0; última línea: `SUITE-ACOTADA OK`

## FAMILIAS

Ninguna familia nueva. El hallazgo es una continuación de la familia visitada P19′/H-01: coherencia entre retirada del binding, persistencia terminal y adopción. N40 cubre la misma instancia en memoria, pero no la pérdida de esa memoria al reiniciar.

## BACKLOG

Ninguno. El crecimiento de `_retired_run_ids` pertenece al delta y queda incluido en el hallazgo P19′; no se difiere como deuda ajena.

## NO VERIFICADO

- No arranqué un daemon HTTP real ni forcé el reinicio del proceso Windows en la ventana: la reproducción reconstruyó `ServerState` en proceso, contra el mismo manifiesto después del fallo inyectado. La alcanzabilidad del orden de arranque se verificó por lectura de `daemon.py:914-928,961-962`.
- No ejecuté la suite completa del repositorio ni una sesión DayZ real; ejecuté los tres gates solicitados, incluida la suite acotada de 11 módulos.
- No inyecté una excepción dentro de `run_bindings_retired`; la rama `except -> False` fue revisada estáticamente. El método de producción actual sólo valida el string y consulta el `set` bajo lock (`loopback.py:1106-1110`).
