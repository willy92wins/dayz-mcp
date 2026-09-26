# INFORME — 5 fichas DayZ-MCP (copia aislada)

## RESUMEN

### T1 (fb-20260827-154455-0f12) — `session_transition_conflict` pelado

Raise-site que dispara en el repro: `control_client.py:577-580` (`_require_recovery_idle_status`). El llamante tiene lease activo → `execute_dayz_test_run` → `_require_idle_session` → `ClientRuntime.reconcile_idle_session` → `_reconcile_idle_session_locked` → status remoto `self.state != "none"` → ese raise. Los otros 6 sitios no están en este camino (`_begin_operation:314`, acquire :344/:370, `session_wait:453`, reconcile sin `operation_id:598`, snapshot drift :622).

El texto pelado lo decide `server.py:907-921` (`public_error_code` sin `hint` → `error.code`). No se tocó la máquina de estados ni el lease.

Fix (ronda 2, F1): señal local barata `active_lease_token` leída **antes** de reconciliar (`dayz_test_tool.py:336-349`). Con lease local: hint de soltar lease, verbo correcto (`_HELD_LEASE_RUN` / `_HELD_LEASE_STOP`). Sin lease (ticket/operation/drift): `session_transition_conflict: a session transition is in flight`. `execute_dayz_test_stop` pasa `tool="dayz_test_stop"` (`:672`).

Docstring `dayz_test_run`: `server.py:2668-2669` ("Release any held session lease before calling").

### T2 (fb-20260828-160423-91ed) — `bad_dayz_test_request` sin campo

`build_run_request` tragaba el `ValueError("invalid_dayz_test_request")` del parse en `dayz_test_tool.py:162` (`_fail("bad_dayz_test_request")`). El enum público vive en `dayz_test_tool.py:17-18` + check :125-126; `_artifact_paths:343-350` sigue siendo `{server, all, else=client}` para rutas, no para el rechazo. `offline` se acepta (stop interno).

Fix (ronda 2, F2): `execute_dayz_test_run` rechaza `offline` y cualquier no-público en `:618-620` (`mode not in _PUBLIC_MODES`). `build_run_request` sigue aceptando `offline` para el stop interno (`mode="offline", kill=True`).

Docstring: `server.py:2669` (`mode is server|all|client`).

### T3 (fb-20260827-154519-6b10) — docstring `ui_focus`

Solo doc. `server.py:4028-4031`: se quitó "plain TextWidget" de los no-focusables; queda NoFocus/disabled; un TextWidget plano sí recibe foco (`ok=1`). Sin cambio de lógica.

### T4 (fb-20260827-202324-8ab3 p.2) — `version_state` stale

Mapa: `loopback.py:1038` (bind, no versión); `:1261` pega `version_state` de `_poll_versions` (None inicial = `legacy_blocked`); `:1709-1793` `record_poll` escribe versión. El payload de `bridge_status` lo arma `core._peer_status` (`core.py:50-84`).

Antes: `last_poll_age_s is None` ya ponía `version_detail=never_polled_this_generation` pero `version_state` seguía siendo el veredicto computado (`legacy_blocked` / `legacy`) — el stale "a secas".

Fix acotado (`core.py:68-73`): si no hay poll observado esta generación, `version_state` y `version_detail` = `never_polled_this_generation`.

Ronda 2 (F3): `server.py:604-609` trata `never_polled_this_generation` + `require_version` como no-listo (`game_not_ready`) **antes** de enqueue (misma clase que el `legacy_blocked` + age=None previo). `loopback.py:1382-1390` (`_version_block_fields`) etiqueta 409/423 con `never_polled_this_generation` cuando `_last_poll_at` es None y el validator bloquearía; un poll real sin `ver=` sigue siendo `legacy_blocked` / "poll did not include ver=". Acreditación y `_poll_versions` no se tocaron.

### T5 (fb-20260828-160429-2899) — width/height (diagnóstico)

Ver sección DIAGNOSTICO. Parche propuesto (no aplicado, ronda 2 F4): espera con timeout (10 s) la ventana top-level del PID **cliente** del `run_id` exacto (status lifecycle vía broker), redimensiona solo esa, relee `GetWindowRect`. `mode=all` no toca el server. Si no hay PID o no hay ventana en el timeout, no hace EnumWindows por título. `C:\Users\guill\AppData\Local\Temp\grok_inbox_daemon\patch_launcher_width_height.diff`. Docstring honesta: `server.py:2674-2679`.

## GATES

### 1. Control de import (antes de la suite)

cwd = `C:\Users\guill\AppData\Local\Temp\grok_inbox_daemon\tools`, `PYTHONPATH` = ese `tools`.

```
C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe -c "import dayz_mcp; print(dayz_mcp.__file__)"
```

Salida:

```
C:\Users\guill\AppData\Local\Temp\grok_inbox_daemon\tools\dayz_mcp\__init__.py
```

Dentro del workspace. Se continuó.

### 2. Rojo → verde (T1, T2, T4)

**ROJO** (tests escritos, producción aún sin el fix):

```
FAIL: test_build_run_request_names_invalid_mode_and_expected_values
AssertionError: 'bad_dayz_test_request' != 'bad_dayz_test_request:mode expected server|all|client'

ERROR: test_run_names_held_session_lease_on_transition_conflict
dayz_mcp.control_client.ControlClientError: session_transition_conflict
  File "...\dayz_test_tool.py", line 316, in _require_idle_session
    await runtime.reconcile_idle_session()

FAIL: test_never_polled_this_generation_is_distinct_from_legacy_blocked
AssertionError: 'legacy_blocked' != 'never_polled_this_generation'

FAIL: test_require_version_false_never_polled_is_not_legacy_as_current
AssertionError: 'legacy' != 'never_polled_this_generation'

FAIL: test_polled_without_ver_keeps_real_legacy_detail
AssertionError: 'legacy_blocked' != 'never_polled_this_generation'

FAIL: test_status_endpoint_returns_rich_payload
AssertionError: 'legacy' != 'never_polled_this_generation'

Ran 6 tests in 0.090s
FAILED (failures=5, errors=1)
```

**VERDE** (mismos 6 tests tras el fix):

```
test_build_run_request_names_invalid_mode_and_expected_values ... ok
test_run_names_held_session_lease_on_transition_conflict ... ok
test_never_polled_this_generation_is_distinct_from_legacy_blocked ... ok
test_require_version_false_never_polled_is_not_legacy_as_current ... ok
test_polled_without_ver_keeps_real_legacy_detail ... ok
test_status_endpoint_returns_rich_payload ... ok

Ran 6 tests in 0.083s
OK
```

### 3. Unittest de módulos afectados

```
python -m unittest tests.test_dayz_test_tool tests.test_control_client tests.test_bridge_status_generation tests.test_daemon tests.test_mcp_tools tests.test_box_occupancy tests.test_server_response_truth tests.test_dayz_test_value_error_codes tests.test_bad_args_messages tests.test_loopback -v
```

```
Ran 245 tests in 9.456s
OK
```

También verdes (muestra de consumidores de `version_state` / docstring):

```
test_version_state_legacy_and_legacy_blocked ... ok
test_version_state_mismatch_and_ok ... ok
test_bridge_status_ready_true_with_fresh_versioned_peers ... ok
test_dayz_test_run_description_names_wait_for_box_cap ... ok
Ran 4 tests in 1.279s
OK
```

Suite completa (`unittest discover -s tests -p "test_*.py"`): 1644 tests en 135.535 s. `FAILED (failures=60, errors=78, skipped=32)`. Los fallos muestreados son árbol incompleto de esta copia (faltan `README.md`, `CLAUDE.md`, `AGENTS.md`, `install-mcp.ps1`, `test-contracts\`, addon `MCPBridge.c`, playbooks → `playbook_runner_missing`). No hay FAIL citado contra `never_polled_this_generation`, el mensaje enriquecido de T1, ni el enum de `mode`. No se atribuyen a estos diffs.

### 4. Ronda 2 — rojo → verde (F1, F2, F3)

**ROJO** (tests nuevos, producción aún sin F1–F3):

```
ERROR: test_run_rejects_public_offline_mode_with_expected_enum
ValueError: invalid_native_launcher_bundle
  (offline todavía llegaba a abrir el launcher)

FAIL: test_run_transition_conflict_without_local_lease_stays_neutral
AssertionError: '...release your session lease first - dayz_test_run...'
              != 'session_transition_conflict: a session transition is in flight'

FAIL: test_stop_names_held_session_lease_without_naming_run
AssertionError: '...dayz_test_run manages...' != '...dayz_test_stop manages...'

FAIL: test_never_polled_is_game_not_ready_before_enqueue
AssertionError: 'game_not_ready' not found (el peer alcanzaba enqueue)

FAIL: test_never_polled_409_matches_bridge_status_label
AssertionError: 'legacy_blocked' != 'never_polled_this_generation'

FAIL: test_lease_required_never_polled_does_not_invent_a_pbo
AssertionError: 'legacy_blocked' != 'never_polled_this_generation'

Ran 6 tests in 0.253s
FAILED (failures=5, errors=1)
```

**VERDE** (mismos + regresiones de lease/409):

```
test_run_rejects_public_offline_mode_with_expected_enum ... ok
test_run_transition_conflict_without_local_lease_stays_neutral ... ok
test_stop_names_held_session_lease_without_naming_run ... ok
test_run_names_held_session_lease_on_transition_conflict ... ok
test_never_polled_is_game_not_ready_before_enqueue ... ok
test_never_polled_409_matches_bridge_status_label ... ok
test_lease_required_never_polled_does_not_invent_a_pbo ... ok
test_post_authorize_version_blocked_aborts ... ok
test_lease_required_includes_version_block_fields ... ok

Ran 9 tests in 0.249s
OK
```

Módulos afectados (ronda 2):

```
python -m unittest tests.test_dayz_test_tool tests.test_control_client tests.test_bridge_status_generation tests.test_daemon tests.test_mcp_tools tests.test_box_occupancy tests.test_server_response_truth tests.test_dayz_test_value_error_codes tests.test_bad_args_messages tests.test_loopback tests.test_weak_agent_consumer_ux.WeakAgentReadyTest
```

```
Ran 259 tests in 9.376s
OK
```

## DIAGNOSTICO (T5)

Causa: `app_main.py` **no consume** `width`/`height`. Parsea el request y reenvía los bytes canónicos al worker; el C++ no lanza DayZDiag ni añade flags de resolución.

1. `C:\Users\guill\AppData\Local\Temp\grok_inbox_daemon\_ref\launcher_src\app_main.py:216` — `parse_dayz_test_request`. El payload trae `width`/`height`. `_worker_main` no los lee.
2. Mismo fichero `:238-247` — `execute_dayz_test_worker(parsed.canonical_bytes, ...)`. El launcher Python no traduce tamaño a argv ni a `SetWindowPos`.
3. `launcher.cpp:1308` — `CreateProcessW` del **worker** (Python embebido), no de `DayZDiag_x64.exe`. No hay `-x`/`-y` ni tamaño de ventana en el nativo.
4. El consumo está en el worker empaquetado, no en `app_main.py`: `dayz_test_worker.py:254-256` (rol `client`) y `:268-270` (rol `offline`) meten `-window -x={width} -y={height}` en el argv de DayZDiag. El rol `server` (`:228-238`) **no** lleva `-x`/`-y`.
5. Esos flags no fijan el viewport de render. Medido in-game por el orquestador: pedir 1280×720 arrancó el cliente a 846×461. El perfil del cliente (`-profiles=` + `-name=`) y/o el DPI de Windows pisan `-x`/`-y`. `SetWindowPos` host-side posterior sí funciona; `ui_reload_layout` re-mide sin reboot.

Parche propuesto (no aplicado; el bundle está sellado): `C:\Users\guill\AppData\Local\Temp\grok_inbox_daemon\patch_launcher_width_height.diff` — tras un worker `exit_code==0` en mode `all|client|offline`, consulta lifecycle status del `run_id`, espera ≤10 s la ventana top-level del PID **cliente**, `SetWindowPos` solo esa, confirma con `GetWindowRect`. `mode=all` no redimensiona el server. Si no hay PID o no aparece ventana, no hace nada (no busca por título).

## LO QUE NO PUDE VERIFICAR

- Comportamiento in-game del repro T1 (3/3 lo midió el orquestador). El unit simula `ControlClientError` en `reconcile_idle_session`, no un daemon HTTP vivo con lease.
- Que `ToolError` de FastMCP en el camino `ClientRuntime.reconcile_idle_session` (str exacto `session_transition_conflict`) se enriquece igual: el helper acepta `.code` o `str(error) == "session_transition_conflict"`; no se levantó un `build_app` con lease real.
- El parche del launcher: no se aplicó, no se re-selló el bundle, no se lanzó un cliente 1280×720 para ver si el wait-by-PID + `SetWindowPos` + `GetWindowRect` coinciden con el viewport. El status lifecycle vía broker en el worker post-ack no se ejecutó.
- `GetWindowRect` vs área cliente: el motor puede reportar un viewport distinto del rect de la ventana host (bordes/DPI).
- Suite completa sobre el árbol vivo con docs/addon/playbooks: esta copia aislada no los trae. Los 259 tests de los módulos tocados (ronda 2) sí pasaron aquí.
- `test_instance_fence` no carga: falta `MCPBridge.c` bajo addon/mod en esta copia.
