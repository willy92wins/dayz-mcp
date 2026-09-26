## HECHO

F-04 — `tools/dayz_mcp/process_lifecycle.py:2595-2628` (`adopt_run`) y
`tools/dayz_mcp/loopback.py:3138-3152` + `:3216-3235` (`_adopt_on_grant`).

El `except Exception:` posterior a `_authorize` ya no silencia el compensador.
El error sigue siendo `adopt_failed` / 503; el manifiesto no se toca (el `get`
reventó antes de mutar). Tres salidas del cierre:

- Si `abort_reservation` **devuelve** una tupla de degradaciones (p. ej.
  `("audit_failed",)`), se recogen con `degraded.extend(...)` y se escriben en
  `result["cleanup_degraded"]` (`:2604-2612`, `:2626-2627`) — el mismo patrón
  que `stop_run` en `:2280-2296`. `_adopt_on_grant` copia esa lista a
  `adopted_run["cleanup_degraded"]` sólo cuando no está vacía (`:3138-3152`,
  rama `ok is not True` en `:3216-3224`).
- Si `abort_reservation` **lanza**, se intenta el cierre alternativo
  `reject_reservation(authority[0], authority[1], authority[2], "adopt_failed")`
  (`:2614-2623`). Ese pop deja `pending_authorizations == 0`. Si el
  `AuthorizationDecision` trae `cleanup_degraded`, también se recogen.
- Si `reject_reservation` **también lanza**, se declara
  `"reservation_abort_failed"` en `cleanup_degraded` (`:2624-2625`). La
  respuesta no afirma un cierre que no ocurrió: el error es `adopt_failed` y
  la degradación dice que el abort/reject no cerraron. El lease ya concedido
  sigue usable (release 200).

El `except` exterior de `_adopt_on_grant` (`:3226-3235`) aplica el mismo
`_copy_cleanup` si `adopt_run` ya había devuelto degradaciones y algo lanza
después.

F-05 — `tools/dayz_mcp/loopback.py:3155-3182`. Se materializa `list(list_runs())`
y se valida **cada** fila (`state`, `owner_session_id`, `run_id` presentes;
`state` y `run_id` `str`) **antes** de filtrar idle. Una fila que no cumple
(p. ej. `object()`) lleva la enumeración entera a
`{ok:false, run_id:null, error:"run_state_unavailable"}`. `null` queda sólo
para una lista válida sin candidato RUNNING_IDLE sin dueño.

## ASERCIONES

| test | antes (`../../ws-frozen-r2/tools/`) | después | F-item |
|---|---|---|---|
| `test_process_lifecycle.ProcessLifecycleTest.test_adopt_run_abort_degradations_reach_cleanup_degraded` (`:1733`) | FAIL: `'audit_failed' not found in []` | PASS | F-04 |
| `test_process_lifecycle.ProcessLifecycleTest.test_adopt_run_abort_raise_closes_via_reject_reservation` (`:1765`) | FAIL: `pending_authorizations` con 1 reserva | PASS (pendientes 0 vía `reject_reservation`) | F-04 |
| `test_process_lifecycle.ProcessLifecycleTest.test_adopt_run_abort_and_reject_raise_declares_reservation_abort_failed` (`:1798`) | FAIL: `'reservation_abort_failed' not found in []` | PASS | F-04 |
| `test_daemon.DaemonEndpointTest.test_acquire_survives_adopt_run_when_abort_reservation_raises` (`:689`) | FAIL: pending=1 y `cleanup_degraded=[]` | PASS (pendientes 0 o `reservation_abort_failed`; release 200) | F-04 |
| `test_daemon.DaemonEndpointTest.test_acquire_malformed_list_runs_row_declares_run_state_unavailable` (`:741`) | FAIL: `adopted_run is None` | PASS: `{ok:false, run_id:null, error:run_state_unavailable}` | F-05 |
| `gate/oracle_tests.py` JB11 | FAIL (2º rojo del oráculo r3) | PASS (pendientes 0 en [1]; `audit_failed` en [2]) | F-04 |
| `gate/oracle_tests.py` JB12 | FAIL (2º rojo del oráculo r3) | PASS | F-05 |

Los 16 checks anteriores y los tests F-01/F-02 ya verdes
(`test_adopt_run_dependency_failure_after_authorize_closes_reservation`,
`test_acquire_survives_adopt_run_manifest_get_failure`,
`test_acquire_list_runs_failure_declares_run_state_unavailable`) no cambiaron
de aserción y siguen PASS.

## GATE

```
PRODUCTO-CONGELADO OK (61 ficheros sellados + 2 del write-set)
```

```
========================================================================================================
[PASS ] JA1-LIMPIEZA-INTERNA-LLEGA-AL-PEER tras session_release con vehiculo activo el peer client recibe vehicle_release
          peer client vio ['vehicle_control', 'vehicle_release'] tras el release (cleanup={'terminal_safe': True, 'runs_released': ['test-run'], 'cancelled': 0, 'vehicle_release_enqueued': 1}) -- la limpieza vehicle_release que encola el release del dueno es una orden interna del daemon: el cerco P6 protege de sesiones sin dueno, no de la limpieza del propio daemon; drenarla deja el vehiculo del juego bajo control fantasma (fb
[PASS ] JA2-EL-CERCO-SIGUE-PARA-LO-NORMAL una lectura sobre el run cercado sigue siendo run_not_owned (control positivo: pasa con el cerco parcheado)
          lectura sobre RUNNING_IDLE cercado: 409 {'error': 'run_not_owned', 'hint': 'This run has no owner (RUNNING_IDLE). Adopt the existing run before dispatching.'} (esperado 409 run_not_owned); control positivo con el cerco parcheado: 200 {'id': 1} (esperado 200) -- si el control no pasa, el check no esta observando el cerco
[PASS ] JA3-DRENA-LO-NORMAL-Y-ENTREGA-LO-INTERNO una mutacion encolada antes del release se descarta run_not_owned y vehicle_release se entrega
          peer vio ['vehicle_control', 'vehicle_release'] (esperado [vehicle_control, vehicle_release]); await #2: 200 {'status': 'done', 'result': {'id': 2, 'ok': False, 'error': 'owner_release'}} (esperado done/ok False/owner_release|run_not_owned) -- el cerco drena lo que NO es limpieza del daemon y entrega lo que si lo es
[PASS ] JA4-LA-LIMPIEZA-ENTREGADA-NO-ACREDITA-ACTIVIDAD tras entregar vehicle_release el run sigue RUNNING_IDLE y su actividad no es recent
          antes del release: {'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 1.979, 'owner_session': 'session-A-01', 'state': 'RUNNING', 'activity_state': 'recent', 'last_activity_age_s': 0.02, 'daemon_generation_at_launch': None, 'daemon_generation_current': '', 'generation_changed': None}; despues de entregar la limpieza: {'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 2.367, 'owner_
[PASS ] JA5-INTERNAL-NO-SE-ACEPTA-POR-HTTP un /enqueue con internal:true sobre el run cercado sigue siendo run_not_owned; internal=True tiene un unico llamador (cleanup_owner)
          internal=True en dayz_mcp: 1 en loopback.py dentro de ['cleanup_owner'], otros ficheros ninguno; /enqueue con internal:true sobre el run cercado: 409 {'error': 'run_not_owned', 'hint': 'This run has no owner (RUNNING_IDLE). Adopt the existing run before dispatching.'} (esperado 409 run_not_owned) -- la exencion es del daemon, no de quien lo pida
[PASS ] JB1-ADQUIRIR-ADOPTA /session/acquire sobre la caja con un run RUNNING_IDLE sin dueno lo adopta y lo declara en adopted_run
          acquire -> adopted_run={'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': True}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 2.866, 'owner_session': 'session-A-01', 'state': 'RUNNING', 'activity_state': 'unknown', 'last_activity_age_s': None, 'daemon_generation_at_launch': None, 'daemon_generation_current': '', 'generation_changed': None} -- sin esto ningun run lle
[PASS ] JB2-DESPACHO-TRAS-ADQUIRIR con el run adoptado por el acquire una lectura y una mutacion se entregan al peer
          lectura: 200 {'id': 1}; mutacion: 200 {'id': 2}; peer vio ['camera_get', 'vehicle_control'] -- el oraculo de las fichas: tras lanzar y adquirir, el puente despacha y run_not_owned no sube
[PASS ] JB3-LA-CONCESION-EN-COLA-ADOPTA el siguiente de la FIFO recibe el lease por /session/wait y adopta el run que el anterior dejo RUNNING_IDLE
          wait de B -> 200 status='active' adopted_run={'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': True}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 3.435, 'owner_session': 'session-B-fe', 'state': 'RUNNING', 'activity_state': 'unknown', 'last_activity_age_s': None, 'daemon_generation_at_launch': None, 'daemon_generation_current': '', 'generation_changed': None} -- l
[PASS ] JB4-SIN-RUN-ADOPTABLE-CONCEDE-SIN-ADOPTAR con la caja vacia el acquire concede el lease y declara adopted_run null
          acquire sin run -> 200 {'status': 'active', 'adopted_run': None} (esperado active + adopted_run: null) -- el lease sigue siendo de la caja; la clave presente y nula dice 'no habia nada que adoptar', distinto de 'no lo intente'
[PASS ] JB5-PROCESOS-MUERTOS-CONCEDE-SIN-ADOPTAR un run RUNNING_IDLE cuyos procesos ya no existen no se adopta, el lease se concede igual y adopted_run trae el motivo
          acquire con pid muerto 4000000 -> 200 status='active' adopted_run={'ok': False, 'run_id': 'test-run', 'error': 'run_processes_gone', 'hint': 'This run has no live owned process. Reap or recover the run (reap_dead_run / reap_dead_runs / admin reconcile) so the durable EXITED state converges; adopt_run does not restore a dead run.'}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 1519440.0
[PASS ] JB6-ADOPT-EXPLICITO-IDEMPOTENTE /lifecycle/adopt por el mismo dueno (misma sesion, mismo lease) devuelve ok/RUNNING/dispatchable en vez de run_not_adoptable
          acquire adopted_run={'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': True}; adopt #1 -> 200 {'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': True}; adopt #2 -> 200 {'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': True}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 3.995, 'owner_session': 'session-A-01', 'state': 'RUNNING
[PASS ] JB7-UN-AJENO-NO-ADOPTA otra sesion sin lease no puede adoptar el run que el titular posee
          adopt de B sin lease -> 423 {'error': 'lease_required'}; adopt de B con el token de A -> 403 {'error': 'lease_invalid'}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 4.204, 'owner_session': 'session-A-01', 'state': 'RUNNING', 'activity_state': 'unknown', 'last_activity_age_s': None, 'daemon_generation_at_launch': None, 'daemon_generation_current': '', 'generation_changed': None} -- la 
[PASS ] JB8-EL-RELEASE-DEVUELVE-EL-RUN tras adoptar por el acquire, session_release lista el run en runs_released y lo deja RUNNING_IDLE
          release -> 200 cleanup={'terminal_safe': True, 'runs_released': ['test-run'], 'cancelled': 0, 'vehicle_release_enqueued': 0}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 4.433, 'owner_session': None, 'state': 'RUNNING_IDLE', 'activity_state': 'unknown', 'last_activity_age_s': None, 'daemon_generation_at_launch': None, 'daemon_generation_current': '', 'generation_changed': None} -- run
[PASS ] JB9-MANIFIESTO-ILEGIBLE-NO-ES-NULL si list_runs falla, el acquire concede el lease y adopted_run declara run_state_unavailable (null es solo 'enumeracion valida sin candidato')
          acquire con manifiesto ilegible -> 200 status='active' adopted_run={'ok': False, 'run_id': None, 'error': 'run_state_unavailable'} (esperado ok False, error run_state_unavailable, run_id None) -- F-02: 'no pude mirar' y 'no habia nada' no pueden ser la misma respuesta
[PASS ] JB10-ADOPT-QUE-REVIENTA-NO-CORTA-LA-CONCESION si adopt_run lanza tras abrir la reserva, la respuesta llega entera (200 + adopted_run ok:false), no queda reserva huerfana y el lease sigue usable
          acquire con adopt_run reventando -> 200 {'status': 'active', 'adopted_run': {'ok': False, 'run_id': 'test-run', 'error': 'adopt_failed'}} transporte=None; reservas pendientes en el lease activo=0 (esperado 0); release -> 200; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 4.796, 'owner_session': None, 'state': 'RUNNING_IDLE', 'activity_state': 'unknown', 'last_activity_age_s': None, 'dae
[PASS ] JC1-EL-NOMBRE-DEL-TEST-DICE-LO-QUE-AFIRMA el test de la limpieza tras publicar idle ya no se llama *_is_gone_* y su nombre dice que sobrevive
          nombre viejo presente=False; nombre nuevo=def test_internal_cleanup_enqueued_before_release_survives_when_idle_published( -- F-03: un mantenedor que elija tests por nombre leeria lo contrario de la asercion
[PASS ] JB11-EL-FALLO-DEL-COMPENSADOR-SE-DECLARA si adopt_run revienta y abort_reservation tambien, la reserva se cierra por otra via o la degradacion viaja en adopted_run.cleanup_degraded; y las degradaciones que devuelve abort_reservation se propagan
          [1] get+abort revientan: acquire 200 transporte=None adopted_run={'ok': False, 'run_id': 'test-run', 'error': 'adopt_failed'} pendientes=0 release=200 (esperado 200 + ok False + (pendientes 0 o reservation_abort_failed declarado) + release 200); [2] abort degradado: acquire 200 adopted_run={'ok': False, 'run_id': 'test-run', 'error': 'adopt_failed', 'cleanup_degraded': ['audit_failed']} (esperado audit_failed en clea
[PASS ] JB12-FILA-MALFORMADA-NO-ES-NULL una enumeracion con filas sin esquema (state/owner_session_id/run_id) se declara run_state_unavailable, no 'sin candidato'
          acquire con list_runs -> [object()]: 200 status='active' adopted_run={'ok': False, 'run_id': None, 'error': 'run_state_unavailable'} (esperado ok False, run_state_unavailable, run_id None) -- una fila que no cumple el esquema no es 'ningun candidato': es una lectura que no se puede juzgar
========================================================================================================
ORACULO-TESTS: PASS=18 FAIL=0 UNMET=0 de 18
ORACULO-TESTS-VERDE
```

```
Ran 2558 tests in 190.911s
rojos = permitidos (2)
SUITE-COMPLETA OK
```

## CONTROL POSITIVO

Los 5 tests nuevos contra el producto congelado de ronda 2
(`ws-frozen-r2/tools/dayz_mcp/{process_lifecycle,loopback}.py` preimportado en
`sys.modules`; tests del write-set actual). `dayz_mcp` resuelto a frozen:

```
dayz_mcp from ...\ws-frozen-r2\tools\dayz_mcp\__init__.py
process_lifecycle from ...\ws-frozen-r2\tools\dayz_mcp\process_lifecycle.py
loopback from ...\ws-frozen-r2\tools\dayz_mcp\loopback.py
test_adopt_run_abort_degradations_reach_cleanup_degraded ... FAIL
test_adopt_run_abort_raise_closes_via_reject_reservation ... FAIL
test_adopt_run_abort_and_reject_raise_declares_reservation_abort_failed ... FAIL
test_acquire_survives_adopt_run_when_abort_reservation_raises ... FAIL
test_acquire_malformed_list_runs_row_declares_run_state_unavailable ... FAIL

FAIL: test_adopt_run_abort_degradations_reach_cleanup_degraded
AssertionError: 'audit_failed' not found in []

FAIL: test_adopt_run_abort_raise_closes_via_reject_reservation
AssertionError: Lists differ: [_PendingAuthorization(...)] != []

FAIL: test_adopt_run_abort_and_reject_raise_declares_reservation_abort_failed
AssertionError: 'reservation_abort_failed' not found in []

FAIL: test_acquire_survives_adopt_run_when_abort_reservation_raises
AssertionError: False is not true : pending=[_PendingAuthorization(...)] cleanup_degraded=[]

FAIL: test_acquire_malformed_list_runs_row_declares_run_state_unavailable
AssertionError: None != {'ok': False, 'run_id': None, 'error': 'run_state_unavailable'}

Ran 5 tests in 0.204s
FAILED (failures=5)
```

Los mismos 5 contra el árbol actual (`PYTHONPATH=.` desde `tools/`):

```
test_adopt_run_abort_degradations_reach_cleanup_degraded ... ok
test_adopt_run_abort_raise_closes_via_reject_reservation ... ok
test_adopt_run_abort_and_reject_raise_declares_reservation_abort_failed ... ok
test_acquire_survives_adopt_run_when_abort_reservation_raises ... ok
test_acquire_malformed_list_runs_row_declares_run_state_unavailable ... ok

Ran 5 tests in 0.294s
OK
```

## LO QUE NO PUDE VERIFICAR

- No corrí DayZDiag ni el puente in-game; todo es unittest + oráculo HTTP de
  fixture.
- No ejercité el heartbeat del coordinador para reproducir el «pending 1 que
  el heartbeat no limpia» del repro de Codex. Verifiqué el estado inmediatamente
  después de `adopt_run` / `/session/acquire` (pendientes 0 por
  `reject_reservation`, o `reservation_abort_failed` declarado).
- No hay aserción que fuerce la rama del `except` exterior de `_adopt_on_grant`
  *después* de un `adopt_run` que ya devolvió `cleanup_degraded`. El copiado
  está en `:3226-3235`; no lo disparé.
- No hay test de la sub-rama «`abort_reservation` lanza y `reject_reservation`
  tiene éxito pero trae `cleanup_degraded` (p. ej. `audit_failed`)». El
  `extend` del atributo está; no lo aserté.
- No forcé los intermitentes conocidos
  (`test_wait_for_marker...lookback...`, `test_bug046...one_generation`).
  La suite completa pasó a la primera: exactamente los 2 rojos de
  `gate/allowed_red.txt`.
- `/lifecycle/adopt` HTTP no se re-probó aparte; F-04 se cubre en `adopt_run`
  unitario y en `/session/acquire` (`_adopt_on_grant`).

## DISPUTAS

Ninguna. JB11 acepta (pendientes 0 **o** `reservation_abort_failed`); el
camino ordinario de abort-lanza cierra por `reject_reservation` (pendientes 0)
y el camino doble-falla declara la degradación. JB12 coincide con el contrato
de fila sin esquema ≠ `null`.
