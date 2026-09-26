# Lane 103 (Claude) — informe: disclosure 80-char vs tests de contrato

Estado: **COMPLETO** (familias B+C de #103 en verde; 3 subtests de telemetry
que el triaje agrupo en C tienen otra causa raiz -> #104, ver Bloque C).

Rama: `feature/claude-103-lane-claude`. Commit del fix: `0394fb6`
`fix(tests): contract tests read the full catalog after lease (issue #103)`.
Produccion (`tools/dayz_mcp/`) **sin tocar**: se uso la via PREFERENTE.

## Enfoque

Nuevo helper `tests/catalog_helpers.list_tools_after_lease(app, runtime)`:
pone un lease token temporal en el runtime, llama `app.list_tools()` (la ruta
real parcheada por `list_tools_progressive`) y restaura el token anterior en
`finally`. Asi los tests de contrato leen el catalogo COMPLETO que ve un
cliente real tras `session_acquire_wait`, sin reimplementar nada ni leer
internals de FastMCP. El catalogo compacto pre-lease sigue fijado por
`test_progressive_disclosure.py` (sin cambios).

### Bloque A - Archivos creados/modificados

- `tools/tests/catalog_helpers.py` (nuevo, 24 lineas): helper `list_tools_after_lease`.
- `tools/tests/test_pleno_lease_and_orphans.py` (+13/-3): import del helper (l.24); 3 sitios `list_tools()` -> helper (l.115-118, 214-217, 277-280).
- `tools/tests/test_session_status_blocked_on.py` (+5/-1): import (l.17); sitio l.117-120.
- `tools/tests/test_wait_for_marker.py` (+5/-1): import (l.19); sitio l.125-128.
- `tools/tests/test_playbook_reload.py` (+9/-1): cabecera estandar `_TOOLS_DIR` en sys.path (l.16-18); import (l.25); sitio `test_wire_requires_exact_explicit_module` l.155-158. Los `list_tools()` de l.113/136 (comparacion schemas antes/despues del reload) se dejan pre-lease a proposito: comparan la misma vista consigo misma.
- `tools/tests/test_session_acquire_wait.py` (+8/-1): cabecera `_TOOLS_DIR` (l.5-14); import (l.22); sitio l.556.
- `tools/tests/test_telemetry_read_modes.py` (+5/-1): import (l.27); sitio l.53-56.
- `_orquestacion/lane-103-claude-informe.md` (este fichero).

### Bloque B - Resultado de los tests / verificacion

Modulos de la familia ANTES (worktree, HEAD `cc02221`, literal):
- `test_pleno_lease_and_orphans.py`: `Ran 12 tests in 1.084s` -> `FAILED (failures=9)`
- `test_telemetry_read_modes.py`: `Ran 7 tests in 0.901s` -> `FAILED (failures=3, errors=1)` (1 error = `KeyError: 'telemetry_read'` [disclosure]; 3 failures = `status_snapshot` [NO disclosure])
- Los otros 4 modulos: la corrida "antes" por modulo no quedo capturada (el bucle de shell fue bloqueado por el sandbox); sus fallos aparecen en la baseline del orquestador y en el triaje del issue (blocked_on 2, wait_for_marker 1, playbook_reload 1, session_acquire_wait 1).

Modulos de la familia DESPUES (literal):
- `test_pleno_lease_and_orphans.py`: `Ran 12 tests in 1.108s` -> `OK`
- `test_session_status_blocked_on.py`: `Ran 4 tests in 0.390s` -> `OK`
- `test_wait_for_marker.py`: `Ran 5 tests in 1.070s` -> `OK`
- `test_playbook_reload.py`: `Ran 22 tests in 4.023s` -> `OK`
- `test_session_acquire_wait.py`: `Ran 20 tests in 2.430s` -> `OK`
- `test_telemetry_read_modes.py`: `Ran 7 tests in 0.767s` -> `FAILED (failures=3)` (solo quedan los 3 subtests `status_snapshot`, causa #104)

`test_progressive_disclosure.py` (invariante 8B):
- DESPUES (literal): `Ran 5 tests in 0.506s` -> `OK`
- ANTES: no hay corrida literal aparte. Es equivalente por construccion: el commit `0394fb6` solo toca los 7 ficheros de `tools/tests/` del Bloque A; `test_progressive_disclosure.py` importa solo `mcp` y `dayz_mcp.server`, y ninguno de los dos cambio (`git diff --name-only HEAD~1 HEAD`).

Suite COMPLETA:
- ANTES (baseline del orquestador, checkout principal): `4174 tests -> 56 failures, 5 errors, 10 skipped`.
- DESPUES (este worktree, literal): `Ran 4173 tests in 367.709s` -> `FAILED (failures=43, errors=2, skipped=58)`.
- Delta atribuible al fix: -12 failures (pleno 9 + blocked_on 2 subtests + wait_for_marker 1) y -3 errors (KeyError en playbook_reload, session_acquire_wait, telemetry_read_modes). Errors: 5 -> 2 cuadra exacto. Failures: 56-12 = 44 esperado frente a 43 observado; esa diferencia de 1, junto con el total de tests (4174 frente a 4173) y los skipped (10 frente a 58), es ruido de entorno worktree frente a checkout principal (ver Bloque C). No pude repetir la baseline dentro del worktree: el sandbox pide aprobacion para `git checkout` de ficheros y para operar fuera del worktree, y el modo es no interactivo.
- Los 45 fallos restantes pertenecen todos a otras familias: A/#102 (test_client_mode, test_daemon_query_all_players, test_mcp_tools, test_session_e2e, test_weak_agent_consumer_ux, test_playbook_runner, test_fn_p0_small_model_loops) y D/#104 (test_fn_f1f5, test_ui_dialog, test_ui_error_diagnostics, test_vehicle_prepare_fixture, test_python_backlog_fixes, test_w3_bug_verdicts, test_a429_overlay, test_telemetry_read_modes x3). Ninguno es de la familia B/C.

### Bloque C - Hallazgos durante implementacion

1. **Via preferente, produccion intacta.** No hizo falta tocar `_compact_initial_catalog`. Todas las sentencias de contrato (`session_heartbeat`, `120`, `logs_since`, `session_status does not renew`, `run_not_owned`) existen en las descripciones completas y aparecen post-lease.
2. **telemetry_read_modes: 3 de los 4 fallos NO son de disclosure.** `TelemetryReadPublicErrorsAreToolErrorsTest.test_bridge_codes_raise_tool_error_not_ok_false_dict` (3 subtests) corre en modo EMBEDDED (sin disclosure) y falla con `'types.SimpleNamespace' object has no attribute 'status_snapshot'`: `_fake_bridge_state` no expone `status_snapshot`. Es la misma raiz que #104 punto 3 (`AttributeError: status_snapshot`); #102 tambien cuenta "test_telemetry_read_modes(4)". No los toque. Se lo dejo a #104.
3. **Hallazgo de entorno (importante para las otras lanes).** El venv `.venv-mcp` resuelve `dayz_mcp` (y `tests`) contra el checkout PRINCIPAL `DayZ_MCP_dev/tools` mediante el hook editable, salvo que el modulo de test inserte antes su propio `tools/` en `sys.path`. Consecuencias: (a) en un worktree, los modulos sin la cabecera `_TOOLS_DIR` (p. ej. `test_playbook_reload`, `test_session_acquire_wait`, antes de este fix) prueban el codigo del checkout principal y no el de la rama; (b) un helper nuevo en `tests/` da `ModuleNotFoundError: No module named 'tests'` en esos modulos. Por eso anadi a esos dos modulos la cabecera estandar que ya usan los demas (cambio aditivo). Probablemente tambien explica la deriva de conteo worktree frente a principal (4173 frente a 4174 tests, 58 frente a 10 skipped).
4. **`test_playbook_reload` l.113/136** siguen usando `list_tools()` pre-lease a proposito: la asercion es "los schemas no cambian tras el reload", y compara la misma vista consigo misma.
5. Quedo un fichero auxiliar sin trackear, `_orquestacion/run103.sh` (el sandbox bloqueo su ejecucion). No se incluye en el commit. Se puede borrar.

### Bloque D - Handoff para la revision (Sol)

- Estado: fix completo en `0394fb6` y push a `origin/feature/claude-103-lane-claude`. Sin PR (segun el brief).
- Que revisar:
  - `tools/tests/catalog_helpers.py`: comprobar que restaurar el token en `finally` es suficiente. `active_lease_token` es propiedad con setter en `ClientRuntime` (`server.py` ~l.1888) y delega en `_control`.
  - Que ningun test de contrato haya dejado de probar algo: ahora leen la vista post-lease, que es la que ve un cliente con lease; la vista pre-lease sigue cubierta por `test_progressive_disclosure.py`.
  - Las cabeceras `_TOOLS_DIR` anadidas a `test_playbook_reload` y `test_session_acquire_wait`.
- Deuda conocida:
  - 3 subtests `status_snapshot` en `test_telemetry_read_modes` -> #104.
  - Baseline "antes" de la suite completa tomada del orquestador (checkout principal), no repetida en el worktree. Conviene que el arbitro re-corra la suite en el checkout principal tras el merge para cerrar el conteo exacto.
  - Solapamiento: #102/#104 pueden tocar `test_telemetry_read_modes.py`. Mi cambio ahi son 2 hunks pequenos (import + un sitio `list_tools`), asi que un conflicto seria trivial.
