# STATE.md — lote D, ronda 3

## HECHO

Contuve las excepciones ordinarias de `evaluate_steam_session()` en el punto
de llamada de `execute_dayz_test_run`
(`tools/dayz_mcp/dayz_test_tool.py:718-746`). Si el evaluador lanza, se
proyecta fail-closed por `_compact_result` como sesión caduca
(`error_code=STEAM_SESSION_STALE`, `steam_registered_pid=None`,
`steam_live_pids=()`, `remediation=REMEDIATION`), el mismo sobre de 17
claves que la fila stale ya cableada.

La guarda sigue siendo exactamente `not preflight and mode in {"client", "all"}`
(`dayz_test_tool.py:718`). El `except` es `Exception`, no `BaseException`.
`_validate_terminal_context` se sigue llamando siempre
(`dayz_test_tool.py:618-620`). No toqué `steam_preflight.py`, `server.py`
ni `gate/`. No hay `skip_run_id_context`.

Tests nuevos en `tools/tests/test_dayz_test_tool.py`:

- `test_steam_evaluator_exception_returns_typed_stale_envelope`: RuntimeError
  del evaluador → sobre failed/`steam_session_stale`, sin lanzar, sin launch.
- `test_steam_evaluator_keyboardinterrupt_propagates`: KeyboardInterrupt
  sigue subiendo.

## GATE

`bash gate/run.sh` (cwd del workspace, RC=0):

```
====================================================================================================
[PASS ] P1-A bridge_status publica los cuatro campos
          claves=['client_peer', 'expected_game_version', 'fence', 'ready', 'require_version', 'results_pending', 'server_peer', 'server_version', 'tool_registry_captured_at', 'tool_registry_fingerprint', 'tool_registry_remediation', 'tool_registry_source_stale', 'version_state']
[PASS ] P1-D1 source_stale es null/unknown sin autoridad, nunca False
          valor='unknown' tipo=str
[PASS ] P1-D2 remediation == reopen_mcp_client
          valor='reopen_mcp_client'
[PASS ] P1-B /status del loopback sin tool_registry_*
          filtrados=[]
[PASS ] P1-C daemon.py sin tool_registry_*
          filtrados=[]
[PASS ] P2-A mision absoluta DENTRO de mission_roots aceptada
          P:\ExampleMod_Suite\_server\mpmissions\custom.ChernarusPlus
[PASS ] P2-B alias sigue aceptado, sin regresion
          chernarus
[PASS ] P2-C mision FUERA de roots sigue rechazada, fail-closed
          rechazada con DayzTestToolError: bad_dayz_test_request
[PASS ] P2-D0 el punto de llamada existe como dayz_test_tool.evaluate_steam_session
[PASS ] P2-D fila Steam usa el MISMO sobre que la fila de fallo tipado
          mismas_claves=True forma=True extra=[] faltan=[] status='failed' error_code='steam_session_stale' phase='validating'
[PASS ] P2-F una excepcion del evaluador sigue dando el sobre, no ToolError
          status='failed' error_code='steam_session_stale' mismas_claves=True
[PASS ] P2-E preflight=True no consulta Steam
          consultas=0
[PASS ] P2-E mode=server no consulta Steam
          consultas=0
====================================================================================================
ORACULO: PASS=13 FAIL=0 UNMET=0 de 13
ORACULO-VERDE
```

`bash gate/suite.sh` (cwd del workspace, RC=0):

```
test_tool_registry_fingerprint : Ran 20 tests in 0.038s  OK
test_mcp_tools : Ran 29 tests in 5.331s  OK
test_dayz_test_tool : Ran 43 tests in 0.188s  OK
test_steam_preflight : Ran 13 tests in 0.001s  OK
test_loopback : Ran 64 tests in 0.905s  OK
test_dayz_test_request : Ran 12 tests in 0.013s  OK
SUITE-ACOTADA OK
```

Qué se verificó: marcador `ORACULO-VERDE` y RC=0 de `gate/run.sh`;
marcador `SUITE-ACOTADA OK` y RC=0 de `gate/suite.sh`; P2-F en PASS con
`status='failed' error_code='steam_session_stale' mismas_claves=True`;
`test_dayz_test_tool` reporta `Ran 43 tests` y `OK` (41 previos + 2 nuevos).

## CONTROL POSITIVO

El test nuevo se corrió ANTES del arreglo, desde `tools/` con el mismo
venv que `gate/suite.sh`:

```
python -m unittest tests.test_dayz_test_tool.DayzTestExecutionTest.test_steam_evaluator_exception_returns_typed_stale_envelope tests.test_dayz_test_tool.DayzTestExecutionTest.test_steam_evaluator_keyboardinterrupt_propagates -v
```

- `test_steam_evaluator_exception_returns_typed_stale_envelope`: ERROR,
  `RuntimeError: sonda P2-F` escapando de `dayz_test_tool.py:715`
  (`steam = evaluate_steam_session()`). El mismo disparador que P2-F.
- `test_steam_evaluator_keyboardinterrupt_propagates`: ok ya antes del
  arreglo (KeyboardInterrupt ya subía; el `except Exception` no lo traga).

Después del arreglo, el mismo comando: `Ran 2 tests ... OK`. El oráculo
pasa de `PASS=12 FAIL=1 UNMET=0 de 13` (medido por el orquestador, P2-F
rojo) a `PASS=13 FAIL=0 UNMET=0 de 13`.

## LO QUE NO PUDE VERIFICAR

- La suite completa del árbol vivo (2296 tests). Fuera de alcance de este
  workspace; el ledger lo declara.
- Comportamiento in-game / PBO. Nada de este lote toca `addon/`.
- Que `evaluate_steam_session` lance de verdad en producción: el orquestador
  ya midió que hoy es total; el disparador de P2-F es un mock. No re-medí
  esa totalidad.
- El wrapping de `server.py:2991` (`ToolError: dayz_test_failed:RuntimeError`).
  No toqué `server.py`; el contrato se cierra porque la excepción ya no
  sale de `execute_dayz_test_run`.
- Tiempos de `suite.sh` no son identidad: el `OK` y el recuento sí.

## DISPUTAS

Ninguna. El `except Exception` proyecta al mismo `_compact_result` que la
fila stale; KeyboardInterrupt/SystemExit no entran en `Exception`.
