## HECHO

**F-01** — `adopt_run` (`tools/dayz_mcp/process_lifecycle.py:2487`) abre la reserva en `_authorize` (`:2492-2494`) y, si algo lanza después y **antes del commit**, aborta y declara error. El `try` cubre el cuerpo post-authorize (`:2498-2594`); `committed` se pone a True sólo tras `_commit_reserved` (`:2527`, `:2573`). Salida inesperada pre-commit: `coordinator.abort_reservation(..., "adopt_failed")` (`:2601-2605`) y `{"error": "adopt_failed", "_http_status": 503}` (`:2608`) sin `manifest.replace`. `_adopt_on_grant` (`tools/dayz_mcp/loopback.py:3113`, llamado en `:3279-3280` **después** de conceder el lease) envuelve la adopción entera (`:3136-3197`): si `adopt_run` u otra pieza lanza, responde `{ok:false, run_id, error:"adopt_failed"}` (`:3191-3197`) y **nunca propaga**. El lease ya es del llamante; la respuesta llega entera.

Cierre de la reserva: **abort**, no rechazo. `abort_reservation` (`session_coordination.py:1373-1411`, `decision="authorization_aborted"`) es el contrato del coordinador para una operación que **no pudo completarse**; `reject_reservation` (`:1413-1448`, `decision="authorization_rejected"`) es para desenlaces **declarados** del comando (`run_not_found`, `run_not_adoptable`, …). El mismo patrón ya lo usa `stop_run` cuando `manifest.replace` lanza (`process_lifecycle.py:2280`). Un `OSError` de `manifest.get` no es un rechazo de negocio: no hay motivo que el llamante deba tratar como decisión sobre el run.

**F-02** — `list_runs` ilegible ya no es `adopted_run: null`. El `except` de la enumeración (`loopback.py:3144-3150`) declara `{ok:false, run_id:null, error:_DURABLE_UNREADABLE}` (`_DURABLE_UNREADABLE = "run_state_unavailable"`, `:103`). `null` queda sólo para enumeración válida sin candidato (`:3151-3153`).

**F-03** — renombrado `test_internal_cleanup_enqueued_before_release_is_gone_when_idle_published` → `test_internal_cleanup_enqueued_before_release_survives_when_idle_published` (`tools/tests/test_process_lifecycle.py:2594`). Lógica intacta.

## ASERCIONES

| test | antes | después | F-item |
|---|---|---|---|
| `tests.test_daemon.DaemonEndpointTest.test_acquire_list_runs_failure_declares_run_state_unavailable` (`test_daemon.py:613`) | no existía; contra r1: FAIL (`adopted_run is None`) | PASS: acquire 200 active + `{ok:false, run_id:null, error:run_state_unavailable}` | F-02 |
| `tests.test_daemon.DaemonEndpointTest.test_acquire_survives_adopt_run_manifest_get_failure` (`test_daemon.py:639`) | no existía; contra r1: ERROR (`RemoteDisconnected`) | PASS: acquire 200 active + `adopted_run.ok false` + error no vacío; `coordination._active.pending_authorizations == []`; release 200; acquire de otra sesión 200 active; run sigue RUNNING_IDLE sin dueño | F-01 |
| `tests.test_process_lifecycle.ProcessLifecycleTest.test_adopt_run_dependency_failure_after_authorize_closes_reservation` (`test_process_lifecycle.py:1703`) | no existía; contra r1: FAIL (`adopt_run` propaga `OSError`) | PASS: `error=adopt_failed`, `_http_status=503`; reserva cerrada; manifiesto intacto (RUNNING_IDLE, sin dueño) | F-01 |
| `tests.test_process_lifecycle.ProcessLifecycleTest.test_internal_cleanup_enqueued_before_release_is_gone_when_idle_published` | el nombre existía y mentía (afirmaba que la limpieza se entrega) | **renombrado** a `test_internal_cleanup_enqueued_before_release_survives_when_idle_published` (`:2594`); aserciones idénticas | F-03 |
| oráculo JB9 | FAIL (`adopted_run None`) | PASS | F-02 |
| oráculo JB10 | FAIL (respuesta cortada / reserva huérfana) | PASS (`adopted_run.error=adopt_failed`, pending=0, release 200, run RUNNING_IDLE sin dueño) | F-01 |
| oráculo JC1 | FAIL (nombre `*_is_gone_*` presente) | PASS (`*_survives_when_idle_published`) | F-03 |

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
          antes del release: {'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 2.274, 'owner_session': 'session-A-01', 'state': 'RUNNING', 'activity_state': 'recent', 'last_activity_age_s': 0.022, 'daemon_generation_at_launch': None, 'daemon_generation_current': '', 'generation_changed': None}; despues de entregar la limpieza: {'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 2.658, 'owner
[PASS ] JA5-INTERNAL-NO-SE-ACEPTA-POR-HTTP un /enqueue con internal:true sobre el run cercado sigue siendo run_not_owned; internal=True tiene un unico llamador (cleanup_owner)
          internal=True en dayz_mcp: 1 en loopback.py dentro de ['cleanup_owner'], otros ficheros ninguno; /enqueue con internal:true sobre el run cercado: 409 {'error': 'run_not_owned', 'hint': 'This run has no owner (RUNNING_IDLE). Adopt the existing run before dispatching.'} (esperado 409 run_not_owned) -- la exencion es del daemon, no de quien lo pida
[PASS ] JB1-ADQUIRIR-ADOPTA /session/acquire sobre la caja con un run RUNNING_IDLE sin dueno lo adopta y lo declara en adopted_run
          acquire -> adopted_run={'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': True}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 3.153, 'owner_session': 'session-A-01', 'state': 'RUNNING', 'activity_state': 'unknown', 'last_activity_age_s': None, 'daemon_generation_at_launch': None, 'daemon_generation_current': '', 'generation_changed': None} -- sin esto ningun run lle
[PASS ] JB2-DESPACHO-TRAS-ADQUIRIR con el run adoptado por el acquire una lectura y una mutacion se entregan al peer
          lectura: 200 {'id': 1}; mutacion: 200 {'id': 2}; peer vio ['camera_get', 'vehicle_control'] -- el oraculo de las fichas: tras lanzar y adquirir, el puente despacha y run_not_owned no sube
[PASS ] JB3-LA-CONCESION-EN-COLA-ADOPTA el siguiente de la FIFO recibe el lease por /session/wait y adopta el run que el anterior dejo RUNNING_IDLE
          wait de B -> 200 status='active' adopted_run={'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': True}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 3.795, 'owner_session': 'session-B-fe', 'state': 'RUNNING', 'activity_state': 'unknown', 'last_activity_age_s': None, 'daemon_generation_at_launch': None, 'daemon_generation_current': '', 'generation_changed': None} -- l
[PASS ] JB4-SIN-RUN-ADOPTABLE-CONCEDE-SIN-ADOPTAR con la caja vacia el acquire concede el lease y declara adopted_run null
          acquire sin run -> 200 {'status': 'active', 'adopted_run': None} (esperado active + adopted_run: null) -- el lease sigue siendo de la caja; la clave presente y nula dice 'no habia nada que adoptar', distinto de 'no lo intente'
[PASS ] JB5-PROCESOS-MUERTOS-CONCEDE-SIN-ADOPTAR un run RUNNING_IDLE cuyos procesos ya no existen no se adopta, el lease se concede igual y adopted_run trae el motivo
          acquire con pid muerto 4000000 -> 200 status='active' adopted_run={'ok': False, 'run_id': 'test-run', 'error': 'run_processes_gone', 'hint': 'This run has no live owned process. Reap or recover the run (reap_dead_run / reap_dead_runs / admin reconcile) so the durable EXITED state converges; adopt_run does not restore a dead run.'}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 1517254.9
[PASS ] JB6-ADOPT-EXPLICITO-IDEMPOTENTE /lifecycle/adopt por el mismo dueno (misma sesion, mismo lease) devuelve ok/RUNNING/dispatchable en vez de run_not_adoptable
          acquire adopted_run={'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': True}; adopt #1 -> 200 {'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': True}; adopt #2 -> 200 {'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': True}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 4.377, 'owner_session': 'session-A-01', 'state': 'RUNNING
[PASS ] JB7-UN-AJENO-NO-ADOPTA otra sesion sin lease no puede adoptar el run que el titular posee
          adopt de B sin lease -> 423 {'error': 'lease_required'}; adopt de B con el token de A -> 403 {'error': 'lease_invalid'}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 4.618, 'owner_session': 'session-A-01', 'state': 'RUNNING', 'activity_state': 'unknown', 'last_activity_age_s': None, 'daemon_generation_at_launch': None, 'daemon_generation_current': '', 'generation_changed': None} -- la 
[PASS ] JB8-EL-RELEASE-DEVUELVE-EL-RUN tras adoptar por el acquire, session_release lista el run en runs_released y lo deja RUNNING_IDLE
          release -> 200 cleanup={'terminal_safe': True, 'runs_released': ['test-run'], 'cancelled': 0, 'vehicle_release_enqueued': 0}; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 4.918, 'owner_session': None, 'state': 'RUNNING_IDLE', 'activity_state': 'unknown', 'last_activity_age_s': None, 'daemon_generation_at_launch': None, 'daemon_generation_current': '', 'generation_changed': None} -- run
[PASS ] JB9-MANIFIESTO-ILEGIBLE-NO-ES-NULL si list_runs falla, el acquire concede el lease y adopted_run declara run_state_unavailable (null es solo 'enumeracion valida sin candidato')
          acquire con manifiesto ilegible -> 200 status='active' adopted_run={'ok': False, 'run_id': None, 'error': 'run_state_unavailable'} (esperado ok False, error run_state_unavailable, run_id None) -- F-02: 'no pude mirar' y 'no habia nada' no pueden ser la misma respuesta
[PASS ] JB10-ADOPT-QUE-REVIENTA-NO-CORTA-LA-CONCESION si adopt_run lanza tras abrir la reserva, la respuesta llega entera (200 + adopted_run ok:false), no queda reserva huerfana y el lease sigue usable
          acquire con adopt_run reventando -> 200 {'status': 'active', 'adopted_run': {'ok': False, 'run_id': 'test-run', 'error': 'adopt_failed'}} transporte=None; reservas pendientes en el lease activo=0 (esperado 0); release -> 200; box run={'run_id': 'test-run', 'mod': '', 'label': 'fence-fixture', 'age_s': 5.301, 'owner_session': None, 'state': 'RUNNING_IDLE', 'activity_state': 'unknown', 'last_activity_age_s': None, 'dae
[PASS ] JC1-EL-NOMBRE-DEL-TEST-DICE-LO-QUE-AFIRMA el test de la limpieza tras publicar idle ya no se llama *_is_gone_* y su nombre dice que sobrevive
          nombre viejo presente=False; nombre nuevo=def test_internal_cleanup_enqueued_before_release_survives_when_idle_published( -- F-03: un mantenedor que elija tests por nombre leeria lo contrario de la asercion
========================================================================================================
ORACULO-TESTS: PASS=16 FAIL=0 UNMET=0 de 16
ORACULO-TESTS-VERDE
```

```
Ran 2553 tests in 198.978s
rojos = permitidos (2)
SUITE-COMPLETA OK
```

## CONTROL POSITIVO

Copia de `../../ws-frozen-r1/tools/` a `/tmp/tmp.fAVKGbJcoS` (mktemp); overlay de `tests/test_daemon.py` y `tests/test_process_lifecycle.py` actuales; mismo intérprete, `PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1`, desde ese temporal.

**Rojo antes** (producto r1 + tests nuevos):

```
TMP=/tmp/tmp.fAVKGbJcoS
test_acquire_list_runs_failure_declares_run_state_unavailable (tests.test_daemon.DaemonEndpointTest.test_acquire_list_runs_failure_declares_run_state_unavailable) ... FAIL
test_acquire_survives_adopt_run_manifest_get_failure (tests.test_daemon.DaemonEndpointTest.test_acquire_survives_adopt_run_manifest_get_failure) ... ----------------------------------------
Exception occurred during processing of request from ('127.0.0.1', 61965)
Traceback (most recent call last):
  File "C:\Python314\Lib\socketserver.py", line 697, in process_request_thread
    self.finish_request(request, client_address)
    ~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Python314\Lib\socketserver.py", line 362, in finish_request
    self.RequestHandlerClass(request, client_address, self)
    ~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Python314\Lib\socketserver.py", line 766, in __init__
    self.handle()
    ~~~~~~~~~~~^^
  File "C:\Python314\Lib\http\server.py", line 496, in handle
    self.handle_one_request()
    ~~~~~~~~~~~~~~~~~~~~~~~^^
  File "C:\Python314\Lib\http\server.py", line 484, in handle_one_request
    method()
    ~~~~~~^^
  File "C:\Users\guill\AppData\Local\Temp\tmp.fAVKGbJcoS\dayz_mcp\loopback.py", line 2997, in do_POST
    self._handle_session(session_action)
    ~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^
  File "C:\Users\guill\AppData\Local\Temp\tmp.fAVKGbJcoS\dayz_mcp\loopback.py", line 3266, in _handle_session
    payload = self._adopt_on_grant(client, payload)
  File "C:\Users\guill\AppData\Local\Temp\tmp.fAVKGbJcoS\dayz_mcp\loopback.py", line 3155, in _adopt_on_grant
    result = lifecycle.adopt_run(client, token, run_id)
  File "C:\Users\guill\AppData\Local\Temp\tmp.fAVKGbJcoS\dayz_mcp\process_lifecycle.py", line 2505, in adopt_run
    run = self.manifest.get(run_id)
  File "C:\Users\guill\AppData\Local\Temp\tmp.fAVKGbJcoS\tests\test_daemon.py", line 645, in broken_get
    raise OSError("manifest read failed inside adopt_run")
OSError: manifest read failed inside adopt_run
----------------------------------------
ERROR
test_adopt_run_dependency_failure_after_authorize_closes_reservation (tests.test_process_lifecycle.ProcessLifecycleTest.test_adopt_run_dependency_failure_after_authorize_closes_reservation) ... FAIL

======================================================================
ERROR: test_acquire_survives_adopt_run_manifest_get_failure (tests.test_daemon.DaemonEndpointTest.test_acquire_survives_adopt_run_manifest_get_failure)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Users\guill\AppData\Local\Temp\tmp.fAVKGbJcoS\tests\test_daemon.py", line 649, in test_acquire_survives_adopt_run_manifest_get_failure
    status, acquired = _http(
                       ~~~~~^
        srv.base,
        ^^^^^^^^^
    ...<3 lines>...
        {"identity": IDENTITY, "purpose": "drive"},
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "C:\Users\guill\AppData\Local\Temp\tmp.fAVKGbJcoS\tests\test_daemon.py", line 83, in _http
    with urllib.request.urlopen(req, timeout=timeout) as response:
         ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Python314\Lib\urllib\request.py", line 187, in urlopen
    return opener.open(url, data, timeout)
           ~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^
  File "C:\Python314\Lib\urllib\request.py", line 487, in open
    response = self._open(req, data)
  File "C:\Python314\Lib\urllib\request.py", line 504, in _open
    result = self._call_chain(self.handle_open, protocol, protocol +
                              '_open', req)
  File "C:\Python314\Lib\urllib\request.py", line 464, in _call_chain
    result = func(*args)
  File "C:\Python314\Lib\urllib\request.py", line 1350, in http_open
    return self.do_open(http.client.HTTPConnection, req)
           ~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Python314\Lib\urllib\request.py", line 1325, in do_open
    r = h.getresponse()
  File "C:\Python314\Lib\http\client.py", line 1450, in getresponse
    response.begin()
    ~~~~~~~~~~~~~~^^
  File "C:\Python314\Lib\http\client.py", line 336, in begin
    version, status, reason = self._read_status()
                              ~~~~~~~~~~~~~~~~~^^
  File "C:\Python314\Lib\http\client.py", line 305, in _read_status
    raise RemoteDisconnected("Remote end closed connection without"
                             " response")
http.client.RemoteDisconnected: Remote end closed connection without response

======================================================================
FAIL: test_acquire_list_runs_failure_declares_run_state_unavailable (tests.test_daemon.DaemonEndpointTest.test_acquire_list_runs_failure_declares_run_state_unavailable)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Users\guill\AppData\Local\Temp\tmp.fAVKGbJcoS\tests\test_daemon.py", line 634, in test_acquire_list_runs_failure_declares_run_state_unavailable
    self.assertEqual(
    ~~~~~~~~~~~~~~~~^
        acquired.get("adopted_run"),
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        {"ok": False, "run_id": None, "error": "run_state_unavailable"},
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
AssertionError: None != {'ok': False, 'run_id': None, 'error': 'run_state_unavailable'}

======================================================================
FAIL: test_adopt_run_dependency_failure_after_authorize_closes_reservation (tests.test_process_lifecycle.ProcessLifecycleTest.test_adopt_run_dependency_failure_after_authorize_closes_reservation)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Users\guill\AppData\Local\Temp\tmp.fAVKGbJcoS\tests\test_process_lifecycle.py", line 1715, in test_adopt_run_dependency_failure_after_authorize_closes_reservation
    result = self.lifecycle.adopt_run(
        IDENTITY_A, self.token_a, "run-existing"
    )
  File "C:\Users\guill\AppData\Local\Temp\tmp.fAVKGbJcoS\dayz_mcp\process_lifecycle.py", line 2505, in adopt_run
    run = self.manifest.get(run_id)
  File "C:\Users\guill\AppData\Local\Temp\tmp.fAVKGbJcoS\tests\test_process_lifecycle.py", line 1711, in _boom
    raise OSError("manifest read failed")
OSError: manifest read failed

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "C:\Users\guill\AppData\Local\Temp\tmp.fAVKGbJcoS\tests\test_process_lifecycle.py", line 1719, in test_adopt_run_dependency_failure_after_authorize_closes_reservation
    self.fail(f"adopt_run propagated {type(exc).__name__}: {exc}")
    ~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: adopt_run propagated OSError: manifest read failed

----------------------------------------------------------------------
Ran 3 tests in 0.247s

FAILED (failures=2, errors=1)
```

**Verde después** (este workspace):

```
test_acquire_list_runs_failure_declares_run_state_unavailable (tests.test_daemon.DaemonEndpointTest.test_acquire_list_runs_failure_declares_run_state_unavailable) ... ok
test_acquire_survives_adopt_run_manifest_get_failure (tests.test_daemon.DaemonEndpointTest.test_acquire_survives_adopt_run_manifest_get_failure) ... ok
test_adopt_run_dependency_failure_after_authorize_closes_reservation (tests.test_process_lifecycle.ProcessLifecycleTest.test_adopt_run_dependency_failure_after_authorize_closes_reservation) ... ok

----------------------------------------------------------------------
Ran 3 tests in 0.323s

OK
```

## LO QUE NO PUDE VERIFICAR

- `pending_authorizations` no viaja en el payload HTTP de `/session/acquire` ni de `/session/status`. Lo leí por el atributo interno `coordination._active.pending_authorizations` (la misma vía que JB10) y lo corroboré por la vía pública: `release` → 200 y un `/session/acquire` posterior de otra sesión concede 200 active. No hay campo público que cuente reservas pendientes.
- No inyecté el fallo de `manifest.get` sobre el camino `/session/wait` (sólo `/session/acquire`). El wrapper es el mismo `_adopt_on_grant` que wait llama en `:3279-3280`.
- No inspeccioné el audit `session_rejected` / `decision=authorization_aborted` en el camino HTTP; el unit de lifecycle sólo afirma que la lista pendiente queda vacía y que el manifiesto no cambió.
- No corrí esto contra DayZDiag en vivo.

## DISPUTAS
