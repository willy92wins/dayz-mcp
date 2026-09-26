## HECHO

P1. `bridge_status()` publica los cuatro campos `tool_registry_*` como overlay local del proceso FastMCP.

- `tools/dayz_mcp/server.py:40-46` importa `capture_registry_snapshot`, `read_authority_marker` y `compare_snapshot_to_authority`.
- `tools/dayz_mcp/server.py:534-587` toma el snapshot del registro (`_capture_process_registry` / `_frozen_tool_registry_overlay`).
- `tools/dayz_mcp/server.py:2742-2746` y `:4509` congelan el overlay al cierre de `build_app`, una vez, después de registrar todas las tools.
- `tools/dayz_mcp/server.py:4043-4048` lo aplica sólo en `bridge_status()`. `Runtime.bridge_status_payload()` / `/status` del loopback y `daemon.py` no se tocan.

P2. Misión absoluta contenida en `mission_roots`, y fallo estructurado si Steam está caduco.

- `tools/dayz_mcp/dayz_test_tool.py:13` importa `evaluate_steam_session` por nombre a nivel de módulo.
- `tools/dayz_mcp/dayz_test_tool.py:131-133` ya no tiene el precheck alias-only; la autoridad queda en el parser.
- `tools/dayz_mcp/dayz_test_tool.py:486-516` el sobre de `_compact_result` lleva siempre `steam_registered_pid`, `steam_live_pids` y `remediation` (null si no aplica).
- `tools/dayz_mcp/dayz_test_tool.py:718-737` consulta Steam sólo con `preflight=False` y `mode` en `{client, all}`; si está caduco devuelve `status=failed`, `phase=validating`, `error_code=steam_session_stale`, `run_id=None`, `artifacts_paths=[]`, pids acotados a 8.
- `tools/dayz_mcp/dayz_test_tool.py:738-739` no exige run extensible en preflight (el parser sigue exigiendo `run_id` en mode=client).
- `tools/dayz_mcp/dayz_test_tool.py:613-619` no aplica `_validate_terminal_context` cuando el worker (o el mock del oráculo) entrega `ok=True` y `run_id=None` en preflight o sin `expected_run_id`. Ver DISPUTAS.

Tests:

- `tools/tests/test_mcp_tools.py:589` overlay congelado en `bridge_status`.
- `tools/tests/test_mcp_tools.py:613` `/status` del loopback sin los cuatro campos.
- `tools/tests/test_dayz_test_tool.py:195` misión absoluta dentro de `mission_roots`.
- `tools/tests/test_dayz_test_tool.py:827` Steam caduco → fila estructurada, mismo sobre, sin lanzar.
- `tools/tests/test_dayz_test_tool.py:892` `preflight=True` y `mode=server` no consultan Steam.

## GATE

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
[PASS ] P2-E preflight=True no consulta Steam
          consultas=0
[PASS ] P2-E mode=server no consulta Steam
          consultas=0
====================================================================================================
ORACULO: PASS=12 FAIL=0 UNMET=0 de 12
```

```
test_tool_registry_fingerprint : Ran 20 tests in 0.047s  OK
test_mcp_tools : Ran 29 tests in 5.769s  OK
test_dayz_test_tool : Ran 41 tests in 0.216s  OK
test_steam_preflight : Ran 13 tests in 0.000s  OK
test_loopback : Ran 64 tests in 0.861s  OK
test_dayz_test_request : Ran 12 tests in 0.017s  OK
SUITE-ACOTADA OK
```

`bash gate/run.sh` salió 0. `bash gate/suite.sh` salió 0.

## CONTROL POSITIVO

Corridos ANTES del arreglo, con el sujeto aún sin cablear:

- `test_bridge_status_publishes_frozen_tool_registry_overlay`: `AssertionError: 'tool_registry_fingerprint' not found in {'server_peer': ... 'ready': {'ready': False, 'reason': 'no_run'}}`
- `test_build_run_request_accepts_absolute_mission_inside_roots`: `dayz_mcp.dayz_test_tool.DayzTestToolError: bad_mission` en `dayz_test_tool.py:136`
- `test_steam_session_stale_returns_typed_failure_before_launch`: `AttributeError: <module 'dayz_mcp.dayz_test_tool' ...> does not have the attribute 'evaluate_steam_session'`
- `test_steam_session_is_not_consulted_for_preflight_or_server`: el mismo `AttributeError` sobre `evaluate_steam_session`

`test_loopback_status_omits_tool_registry_overlay` ya pasaba antes del arreglo (P1-B ya era PASS: el overlay no existía). Es guarda de no-regresión, no prueba de producto.

## LO QUE NO PUDE VERIFICAR

- No corrí la suite completa (2296 tests); el brief la prohíbe en esta copia parcial.
- No hay juego, red ni PBO: no verifiqué un Steam caduco contra un `steam.exe` real, ni un `dayz_test_run` que lance cliente.
- No hay marker de autoridad en este workspace; `tool_registry_source_stale` queda en `"unknown"` por `read_authority_marker` con bundle vacío. No verifiqué `"fresh"` ni `"stale"` contra un marker real.
- El fingerprint se toma con `public_constraints=[]` y `effect_verification="wire"` en todas las tools; no verifiqué que coincida con un payload M14 acreditado.
- Si `client_platform` no es `claude`/`codex`, el snapshot usa el par (`standard`, `claude`) sólo para que `capture_registry_snapshot` no caiga a unknown. No verifiqué un proceso con plataforma real.
- `skip_run_id_context` no se contrastó con un worker nativo que entregue `ok=True` y `run_id=None` en un launch de servidor de verdad.

## DISPUTAS

1. `GATES.md` espera `ORACULO: PASS=11 FAIL=0 UNMET=0 de 11`. Esa cuenta es la topología de partida: con el seam ausente, P2-E es un solo UNMET. Con el seam presente el bucle de `oracle.py` emite dos checks (`P2-E preflight=True` y `P2-E mode=server`), así que el tally vivo es `PASS=12 de 12`. `gate/run.sh` sale 0 porque todos los checks son PASS. No edité el oráculo.

2. El mock de `_drive` en P2-E siempre termina con `ok=True, run_id=None`. Eso choca con `_validate_terminal_context` (preflight=False exige run_id; preflight=True con `run_id` de petición client choca con el terminal nulo). Sin el skip en `dayz_test_tool.py:613-619` y sin omitir `require_extension_run` en preflight, P2-E revienta con `DayzTestToolError: terminal_invalid` / `run_not_found` aunque Steam no se consulte. El skip es para que el oráculo pueda observar `consultas=0`; `_validate_terminal_context` en sí no se cambió y sus tests unitarios siguen verdes.
