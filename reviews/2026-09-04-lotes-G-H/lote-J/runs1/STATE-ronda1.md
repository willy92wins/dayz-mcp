## HECHO

P-J1. Predicado `ServerState._command_exempt_from_fence` (`tools/dayz_mcp/loopback.py:1154-1157`) ≡ `command_id in self._fire_and_forget_ids`. Cuatro sitios: `_drain_run_locked` (`:1159-1184`) conserva los exentos y descarta el resto con `run_not_owned`; el poll BOUND (`:2173-2184`) drena y deja la cola restante (exentos + TTL); los dos re-chequeos del poll (`:2222-2229` y `:2249-2258`) entregan solo exentos en vez de `queue=[]` / `commands=[]`; `_enqueue_run_rejection` (`:1287-1315`) con `internal=True` no rechaza por cerco si el estado durable es RUNNING/STARTING (sí `run_state_unavailable` sin estado legible, sí `binding_retired` fuera de esos estados). `internal=True` sigue teniendo un único llamador de producto: `cleanup_owner` (`:2737`). `/enqueue` HTTP no pasa `internal` (`_handle_enqueue`).

P-J2. Helper `Handler._adopt_on_grant` (`loopback.py:3113-3184`), llamado desde `_handle_session` tras `acquire`/`wait` 200 y fuera del lock del coordinador (`:3265-3266`). Recorre `manifest.list_runs()`: cero RUNNING_IDLE sin dueño → `"adopted_run": null`; más de uno → `multiple_idle_runs`; uno → `lifecycle.adopt_run` y declara el payload. Sin `adopt_run`/`list_runs` reales no toca la respuesta (fixtures solo-coordinador). El lease se concede igual si la adopción falla.

`adopt_run` (`process_lifecycle.py:2487`) es idempotente para el mismo dueño: si el run está RUNNING con `owner_session_id == client.session_id` y `owner_lease_id ==` lease del llamante (`:2508-2535`), hace commit de no-op (`_audit` + `_commit_reserved` + `_finish_committed`, sin `manifest.replace` ni unfence) y devuelve el mismo payload de éxito (`ok`/`run_id`/`RUNNING`/`dispatchable`). Un ajeno sigue en `run_not_adoptable` / `lease_required` / `lease_invalid`.

## ASERCIONES

| test | antes | después | P-item |
|---|---|---|---|
| `tests.test_session_e2e.SessionE2ETest.test_vehicle_release_cleanup_is_enqueued_exactly_once` | peer `["vehicle_control"]` tras el release (y otra vez a 0.1s) | `wait_until` peer `["vehicle_control", "vehicle_release"]` | P-J1 |
| `tests.test_process_lifecycle.ProcessLifecycleTest.test_internal_cleanup_enqueued_before_release_is_gone_when_idle_published` | cola `[]` y poll `commands=[]` con el idle ya publicado; enqueue `internal=True` sin F&F | se apunta el id en `_fire_and_forget_ids`; cola y poll entregan `vehicle_release` | P-J1 |
| `tests.test_loopback.FenceExemptionTest.test_drain_keeps_fire_and_forget_and_discards_the_rest` | no existía | drenaje conserva F&F, descarta el resto `run_not_owned` | P-J1 |
| `tests.test_loopback.FenceExemptionTest.test_held_poll_delivers_only_the_exempt_command` | no existía | poll retenido entrega solo `vehicle_release` | P-J1 |
| `tests.test_loopback.FenceExemptionTest.test_internal_enqueue_on_fenced_run_is_not_run_not_owned` | no existía | `internal=True` sobre run cercado RUNNING no es `run_not_owned`; sin estado durable → `run_state_unavailable` | P-J1 |
| `tests.test_process_lifecycle.ProcessLifecycleTest.test_exempt_delivery_does_not_credit_activity` | no existía | entregar el exento deja RUNNING_IDLE y `activity_state != recent` | P-J1 |
| `tests.test_process_lifecycle.ProcessLifecycleTest.test_adopt_is_idempotent_for_the_same_owner` | no existía | segundo adopt mismo dueño: payload idéntico, estado intacto, audit `lifecycle_adopt`/`allowed` extra | P-J2 |
| `tests.test_process_lifecycle.ProcessLifecycleTest.test_adopt_from_another_session_stays_rejected` | no existía | B sin lease / con token de A no adopta; el titular sigue | P-J2 |
| `tests.test_daemon.DaemonEndpointTest.test_acquire_adopts_the_ownerless_idle_run` | no existía | acquire → `adopted_run` ok/RUNNING/dispatchable y dueño en el manifiesto | P-J2 |
| `tests.test_daemon.DaemonEndpointTest.test_acquire_without_adoptable_run_declares_null` | no existía | caja sin idle → `adopted_run` presente y `null` | P-J2 |
| `tests.test_daemon.DaemonEndpointTest.test_acquire_declares_rejected_adopt_when_processes_are_gone` | no existía | lease active + `adopted_run.ok false` + error + `run_id`; run sigue idle | P-J2 |
| `tests.test_daemon.DaemonEndpointTest.test_acquire_declares_multiple_idle_runs` | no existía | `multiple_idle_runs`, lease concedido, nadie adopta | P-J2 |
| `tests.test_daemon.DaemonEndpointTest.test_wait_on_grant_adopts_the_idle_run` | no existía | FIFO wait concede y `adopted_run.ok` | P-J2 |
| `tests.test_session_e2e.SessionE2ETest.test_acquire_adopts_delivers_then_fifo_wait_adopts` | no existía | acquire → lectura+mutación al peer → `runs_released` → wait de B adopta | P-J2 |
| Fixtures `DaemonHttpServer(adopt_fixture=False)` + acquire (`test_client_mode` / `test_daemon.test_credential_retry…`) | el run bound quedaba RUNNING_IDLE | el acquire HTTP lo adopta; las aserciones existentes no miraban ese estado y `_adopt_run` explícito ahora es no-op | P-J2 (sin cambio de aserción) |

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
          antes del release: {'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 2.923, 'owner_session': 'session-A-01', 'state': 'RUNNING', 'activity_state': 'recent', 'last_activity_age_s': 0.013, 'daemon_generation_at_launch': None, 'daemon_generation_current': '', 'generation_changed': None}; despues de entregar la limpieza: {'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 3.326, 'owner
[PASS ] JA5-INTERNAL-NO-SE-ACEPTA-POR-HTTP un /enqueue con internal:true sobre el run cercado sigue siendo run_not_owned; internal=True tiene un unico llamador (cleanup_owner)
          internal=True en dayz_mcp: 1 en loopback.py dentro de ['cleanup_owner'], otros ficheros ninguno; /enqueue con internal:true sobre el run cercado: 409 {'error': 'run_not_owned', 'hint': 'This run has no owner (RUNNING_IDLE). Adopt the existing run before dispatching.'} (esperado 409 run_not_owned) -- la exencion es del daemon, no de quien lo pida
[PASS ] JB1-ADQUIRIR-ADOPTA /session/acquire sobre la caja con un run RUNNING_IDLE sin dueno lo adopta y lo declara en adopted_run
          acquire -> adopted_run={'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': True}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 3.846, 'owner_session': 'session-A-01', 'state': 'RUNNING', 'activity_state': 'unknown', 'last_activity_age_s': None, 'daemon_generation_at_launch': None, 'daemon_generation_current': '', 'generation_changed': None} -- sin esto ningun run lle
[PASS ] JB2-DESPACHO-TRAS-ADQUIRIR con el run adoptado por el acquire una lectura y una mutacion se entregan al peer
          lectura: 200 {'id': 1}; mutacion: 200 {'id': 2}; peer vio ['camera_get', 'vehicle_control'] -- el oraculo de las fichas: tras lanzar y adquirir, el puente despacha y run_not_owned no sube
[PASS ] JB3-LA-CONCESION-EN-COLA-ADOPTA el siguiente de la FIFO recibe el lease por /session/wait y adopta el run que el anterior dejo RUNNING_IDLE
          wait de B -> 200 status='active' adopted_run={'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': True}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 4.499, 'owner_session': 'session-B-fe', 'state': 'RUNNING', 'activity_state': 'unknown', 'last_activity_age_s': None, 'daemon_generation_at_launch': None, 'daemon_generation_current': '', 'generation_changed': None} -- l
[PASS ] JB4-SIN-RUN-ADOPTABLE-CONCEDE-SIN-ADOPTAR con la caja vacia el acquire concede el lease y declara adopted_run null
          acquire sin run -> 200 {'status': 'active', 'adopted_run': None} (esperado active + adopted_run: null) -- el lease sigue siendo de la caja; la clave presente y nula dice 'no habia nada que adoptar', distinto de 'no lo intente'
[PASS ] JB5-PROCESOS-MUERTOS-CONCEDE-SIN-ADOPTAR un run RUNNING_IDLE cuyos procesos ya no existen no se adopta, el lease se concede igual y adopted_run trae el motivo
          acquire con pid muerto 4000000 -> 200 status='active' adopted_run={'ok': False, 'run_id': 'test-run', 'error': 'run_processes_gone', 'hint': 'This run has no live owned process. Reap or recover the run (reap_dead_run / reap_dead_runs / admin reconcile) so the durable EXITED state converges; adopt_run does not restore a dead run.'}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 1514755.9
[PASS ] JB6-ADOPT-EXPLICITO-IDEMPOTENTE /lifecycle/adopt por el mismo dueno (misma sesion, mismo lease) devuelve ok/RUNNING/dispatchable en vez de run_not_adoptable
          acquire adopted_run={'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': True}; adopt #1 -> 200 {'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': True}; adopt #2 -> 200 {'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': True}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 5.119, 'owner_session': 'session-A-01', 'state': 'RUNNING
[PASS ] JB7-UN-AJENO-NO-ADOPTA otra sesion sin lease no puede adoptar el run que el titular posee
          adopt de B sin lease -> 423 {'error': 'lease_required'}; adopt de B con el token de A -> 403 {'error': 'lease_invalid'}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 5.358, 'owner_session': 'session-A-01', 'state': 'RUNNING', 'activity_state': 'unknown', 'last_activity_age_s': None, 'daemon_generation_at_launch': None, 'daemon_generation_current': '', 'generation_changed': None} -- la 
[PASS ] JB8-EL-RELEASE-DEVUELVE-EL-RUN tras adoptar por el acquire, session_release lista el run en runs_released y lo deja RUNNING_IDLE
          release -> 200 cleanup={'terminal_safe': True, 'runs_released': ['test-run'], 'cancelled': 0, 'vehicle_release_enqueued': 0}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 5.656, 'owner_session': None, 'state': 'RUNNING_IDLE', 'activity_state': 'unknown', 'last_activity_age_s': None, 'daemon_generation_at_launch': None, 'daemon_generation_current': '', 'generation_changed': None} -- run
========================================================================================================
ORACULO-TESTS: PASS=13 FAIL=0 UNMET=0 de 13
ORACULO-TESTS-VERDE
```

```
Ran 2550 tests in 208.325s
rojos = permitidos (2)
SUITE-COMPLETA OK
```

## CONTROL POSITIVO

Copia de `../../ws-frozen-r0/tools/dayz_mcp/loopback.py` y `process_lifecycle.py` sobre un árbol temporal de `tools/` actuales. Mismos dos tests:

Antes (producto frozen r0):

```
test_vehicle_release_cleanup_is_enqueued_exactly_once (tests.test_session_e2e.SessionE2ETest.test_vehicle_release_cleanup_is_enqueued_exactly_once) ... FAIL
test_acquire_adopts_the_ownerless_idle_run (tests.test_daemon.DaemonEndpointTest.test_acquire_adopts_the_ownerless_idle_run) ... FAIL

======================================================================
FAIL: test_vehicle_release_cleanup_is_enqueued_exactly_once (tests.test_session_e2e.SessionE2ETest.test_vehicle_release_cleanup_is_enqueued_exactly_once)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Python314\Lib\asyncio\runners.py", line 127, in run
    return self._loop.run_until_complete(task)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^
  File "C:\Python314\Lib\asyncio\base_events.py", line 719, in run_until_complete
    return future.result()
           ~~~~~~~~~~~~~^^
  File "C:\Users\guill\AppData\Local\Temp\tmp.UTlRoMs8Yc\tests\test_session_e2e.py", line 403, in test_vehicle_release_cleanup_is_enqueued_exactly_once
    await self.wait_until(
    ...<2 lines>...
    )
  File "C:\Users\guill\AppData\Local\Temp\tmp.UTlRoMs8Yc\tests\test_session_e2e.py", line 244, in wait_until
    self.fail("condition_not_reached")
    ~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: condition_not_reached

======================================================================
FAIL: test_acquire_adopts_the_ownerless_idle_run (tests.test_daemon.DaemonEndpointTest.test_acquire_adopts_the_ownerless_idle_run)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Users\guill\AppData\Local\Temp\tmp.UTlRoMs8Yc\tests\test_daemon.py", line 474, in test_acquire_adopts_the_ownerless_idle_run
    self.assertIsInstance(adopted, dict, acquired)
    ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: None is not an instance of <class 'dict'> : {'status': 'active', 'lease_token': '_o-6_gUWqjGOXWK9eOg2m40-3UmZ4M7kqXNz4YfdFoM', 'lease_id': '85086f15f485475caf310964187db453', 'expires_in_s': 119.97022210000432}

----------------------------------------------------------------------
Ran 2 tests in 2.478s

FAILED (failures=2)
```

Después (producto de este lote):

```
test_vehicle_release_cleanup_is_enqueued_exactly_once (tests.test_session_e2e.SessionE2ETest.test_vehicle_release_cleanup_is_enqueued_exactly_once) ... ok
test_acquire_adopts_the_ownerless_idle_run (tests.test_daemon.DaemonEndpointTest.test_acquire_adopts_the_ownerless_idle_run) ... ok

----------------------------------------------------------------------
Ran 2 tests in 0.449s

OK
```

## LO QUE NO PUDE VERIFICAR

- DayZDiag in-game (peers del oráculo/e2e son HTTP fakes; no hay cliente/servidor de juego en esta sesión).
- Los dos rojos permitidos de la suite (`test_full_source_hash_is_frozen`, `test_removing_only_marker_lines_restores_frozen_source_hash`): no los ejecuté en solitario; el gate solo comprueba que siguen siendo exactamente esos dos.
- Los intermitentes nombrados en el brief (`test_lookback_can_match_a_line_written_before_the_action`, `test_run_daemon_candidate_wave_and_cross_port_publish_one_generation`): esta corrida de suite no los trajo como rojo extra, así que no hubo segunda pasada.
- Un enqueue `internal=True` *nuevo* sobre un run ya RUNNING_IDLE (después del fence): el API Python sigue siendo `run_not_owned` (ver DISPUTAS). No medí un cleanup_owner que perdiera la carrera y encolara *después* de `begin_release_owner`.
- `session_status` del protocolo DayZ-MCP compartido: este lote no tocó procesos DayZ ni el lease de la caja real.

## DISPUTAS

El brief pide que `_enqueue_run_rejection(internal=True)` no rechace RUNNING_IDLE. El test sellado fuera del write-set `tests.test_box_occupancy.RunCommandActivityTest.test_internal_command_on_idle_run_is_run_not_owned` afirma lo contrario (API Python, idle ya publicado → `run_not_owned`). No se puede cambiar ese test. En producción `cleanup_begin` (`daemon.py:85-89`) encola *antes* de `begin_release_owner`; la exención que desbloquea JA1/JA3 es drenaje+poll de ids ya en `_fire_and_forget_ids`. Por eso `internal=True` encola sobre un run cercado RUNNING/STARTING y sobre estado ilegible (`run_state_unavailable`), pero un idle durable nuevo sigue `run_not_owned` en el enqueue. El oráculo JA1–JA5 está verde con este recorte.
