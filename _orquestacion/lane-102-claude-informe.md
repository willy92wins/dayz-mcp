# Informe lane 102 (Claude) — issue #102: GamePeer sin caps=/ach= -> capabilities_unknown

Rama: `feature/claude-102-lane-claude` · commit del fix: `fc0a406` · estado: **COMPLETO** (1 residual documentado, ver C.1)

### Bloque A - Archivos creados/modificados

Solo tests/fixtures. **Cero ficheros de produccion** (`tools/dayz_mcp/**`, `addon/**` intactos).

- `tools/tests/fence_helpers.py` +171-199 — nuevos helpers:
  - `poll_census_query(peer)`: `caps=` (sorted `server._BRIDGE_COMMAND_TOOLS[peer]`) y, solo para `server`, `ach=server.EXPECTED_SERVER_ARG_CONTRACT_HASH` (= `3c77a99c95fd05a4`, fijado por `test_arg_contract_hash.py:34`). Replica MCPBridge.c:256-257 (caps+ach) y MCPClientBridge.c:491 (solo caps).
  - `announced_capabilities(peer)`: el bloque `capabilities` que publica `loopback._capabilities_view_locked` tras ese poll, para fakes que construyen `status_snapshot` a mano.
- `tools/tests/test_client_mode.py` 24 (import), 96 (`GamePeer._run`: `query.update(poll_census_query(self.peer))`).
- `tools/tests/test_session_e2e.py` 25-31 (imports, incl. `_VALID_PEER_VERSION`), 131-132 (`GamePeer._poll_once`: `ver=` + census), 275-276 (mensaje de assert con payload, solo diagnostico), 418-419 (`test_acquire_adopts_...` arranca tambien el peer server: `camera_get` es world read y exige ambos peers vivos).
- `tools/tests/test_mcp_tools.py` 18-24 (shim `sys.path` como sus hermanos), 28-33 (import), 121 (`FakePeer.run`: census en cada poll).
- `tools/tests/test_daemon_query_all_players.py` 15 (import), 46 (`_ResultState.status_snapshot`: `capabilities` por peer).
- `tools/tests/test_weak_agent_consumer_ux.py` 20 (import), 209-213 y 225-229 (`server_peer.capabilities`).
- `tools/tests/test_fn_p0_small_model_loops.py` 210-220 (`server_peer.capabilities` inline; el modulo no importa `tests.*` y lo dejo asi).
- `playbooks/fixtures/run_really_started/pass.json` 96-102 y `playbooks/fixtures/box_is_mine/pass.json` 136-142 — `server_peer.capabilities` `{state: match, ach esperado=anunciado}` en la respuesta `bridge_status` con `ready:true`. Ambos playbooks son **DRAFT** (sin SHA congelado; ningun test hashea `playbooks/fixtures`).
- `_orquestacion/lane-102-claude-informe.md` — este informe.

### Bloque B - Resultado de los tests / verificacion

Repro previo al fix (esperado):
```
AssertionError: bridge did not become ready: {'ready': False, 'reason': 'capabilities_unknown', ...}
Ran 1 test in 1.590s
FAILED (failures=1)
```

Modulos de la familia tras el fix (`discover -s tools/tests -p <modulo>`):

| modulo | resultado |
|---|---|
| test_client_mode.py | `Ran 53 tests in 7.737s` **OK** |
| test_daemon_query_all_players.py | `Ran 5 tests in 0.272s` **OK** |
| test_weak_agent_consumer_ux.py | `Ran 37 tests in 0.662s` **OK** |
| test_fn_p0_small_model_loops.py | `Ran 19 tests in 1.206s` **OK** |
| test_playbook_runner.py (via `test_playbook*.py`) | verde; unico error = `test_playbook_reload` ImportError standalone (familia #103, ver C) |
| test_session_e2e.py | `Ran 14 tests` FAILED (failures=1) -> residual C.1 |
| test_mcp_tools.py | `Ran 57 tests in 11.981s` FAILED (failures=1) -> `reattach_matrix`, familia #103 (truncado 80 chars) |
| test_pleno_lease_and_orphans.py | 9 subfallos, todos familia #103 (descripcion truncada), **no** caps |
| test_telemetry_read_modes.py | 3F+1E, familia #103 (catalogo compacto / `telemetry_read` ausente de list_tools) |

Suite COMPLETA (`... -m unittest discover -s tools/tests -p "test_*.py"`):

- **Antes (baseline del orquestador en main):** 4174 tests -> 56 failures, 5 errors, 10 skipped.
- **Despues (esta rama, worktree):**
```
Ran 4173 tests in 326.786s
FAILED (failures=29, errors=5, skipped=58)
```
  Fallos: 56 -> **29** (-27). Errores: 5 -> 5 (ninguno de familia A).
  Los 58 skipped (vs 10) y el 4173 vs 4174 son **del worktree, no del fix**: todas las razones de skip son artefactos no versionados ausentes aqui (bundle nativo `dayz-test-v1` sin construir, `PROJECT-MAP.md`/`SKILL.md`/`CLAUDE.md` "public clone", `tools/.venv-mcp` no esta dentro del worktree -> `test_interpreter_guard` skip, `fase3-evidence-subject.png` ausente, symlink sin privilegio, `h9_native_probe.py` ausente -> ModuleSkipped cuenta 1 en lugar de N).

Reparto de los 29F/5E restantes:
- #103 (B+C, 16F+3E): pleno(9), session_status_blocked_on(2), wait_for_marker(1), mcp_tools reattach_matrix(1), telemetry_read_modes(3F+1E), playbook_reload(1E), session_acquire_wait(1E).
- #104 (D, 12F+2E): fn_f1f5(4), ui_dialog(1), ui_error_diagnostics(3), vehicle_prepare_fixture(2), python_backlog_fixes(2), w3_bug_verdicts(1E), a429_overlay(1E).
- Familia A residual (1F): `test_session_e2e.test_release_cancels_only_owner_queued_commands` (C.1).

### Bloque C - Hallazgos durante implementacion

1. **Residual (no es caps; no lo arregle):** `test_session_e2e.test_release_cancels_only_owner_queued_commands` lanza un `query_player_state` ajeno **sin ningun peer vivo** y espera `ToolError(run_not_owned)` del daemon. Desde `e72ff5b` (17-sep, fail-fast de world reads, `server.py:2286-2296` + `_world_read_not_ready`) esa lectura devuelve el sobre `game_not_ready:reason=binding_not_ready` en el cliente y nunca llega al daemon. Arrancar un peer para hacerla "ready" drena la cola, y el test afirma luego `server_peer.command_names() == []`: conflicto de diseno del test con el fail-fast, no con el gate 0878. Opciones para quien lo coja: usar un comando no world-read para la peticion ajena, o reescribir la asercion al sobre `not_ready`. Lo dejo para decision de revision.
2. **Triaje del issue parcialmente incorrecto:** tras arreglar caps, `test_pleno_lease_and_orphans` (9) y `test_telemetry_read_modes` (4) siguen fallando por causas de **#103** (el propio #103 los lista), no de #102. Tambien 2 de los 3 fallos de `test_session_e2e` no eran solo caps: despues de caps salieron `version_mismatch` (el GamePeer e2e no mandaba `ver=`, el bridge real si) y `binding_not_ready` (world read con solo el peer client). Arreglados en fixture (ver A).
3. **`test_a429_overlay.test_p3_n3_ready_true_with_historical_counters` (archivado en #104 item 7) SI lo rompio #94**, pero no por caps: `9af5cbf` anadio el kwarg `registered_tools=` a `Runtime.bridge_status_payload`, y el stub `with_rejects` del test no lo acepta (`TypeError: ... unexpected keyword argument 'registered_tools'`). Fix de test trivial (aceptar `**kwargs`). No lo toco para no pisar la lane #104; se lo senalo.
4. **Imports standalone:** `test_mcp_tools.py`, `test_arg_contract_hash.py` y `test_playbook_reload.py` fallan con `ModuleNotFoundError: No module named 'tests'` si se ejecutan solos con `-p <modulo>` (no tienen el shim `sys.path` de sus hermanos); en la suite completa funcionan porque otro modulo ya inserto `tools/`. Anadi el shim solo a `test_mcp_tools.py` (familia A, lo necesitaba para verificar). Los otros dos quedan como estan.
5. `caps=` de la fixture se deriva de `server._BRIDGE_COMMAND_TOOLS` (tabla escrita a mano desde los `.c`), no del literal Enforce: produce `state=match` y deja a `test_arg_contract_hash` como el guardian de la paridad Enforce<->Python. Decision conservadora; parsear los `.c` desde una fixture HTTP me parecio fragil.
6. Entorno: no pude crear un worktree A/B en `9af5cbf~1` (requiere aprobacion en este modo). El diagnostico de C.1/C.3 es por lectura de codigo + `git log -S` + el diff de `9af5cbf`, no por A/B ejecutado.

### Bloque D - Handoff para la revision (Sol)

- **Estado:** familia A en verde salvo C.1 (1 test, causa ajena al gate caps/ach). Suite completa 56F/5E -> 29F/5E; los 28 restantes (sin contar C.1) son de #103/#104 segun el reparto del Bloque B.
- **Que revisar:**
  1. `fence_helpers.poll_census_query` / `announced_capabilities`: que el shape coincide con el wire real (MCPBridge.c:256-257, MCPClientBridge.c:491) y con `loopback._capabilities_view_locked` (loopback.py:2976-3004).
  2. Que ningun cambio relaja el gate: no se toco `compute_bridge_ready` ni `_compare_bridge_capabilities`; `test_arg_contract_hash.py` sigue verde en suite completa (sus casos absent/wrong ach siguen fallando cerrado).
  3. Los dos `pass.json` de playbooks (DRAFT): bloque `capabilities` `match` anadido al `server_peer` de la respuesta `bridge_status`.
  4. `test_session_e2e`: el `ver=` en GamePeer y el peer server extra en `test_acquire_adopts_delivers_then_fifo_wait_adopts`.
- **Deuda conocida:** C.1 (decidir diseno del test), C.3 (para lane #104), C.4 (shims standalone en 2 modulos).
- Sin push force, sin PR, sin tocar `main` ni otras ramas.
