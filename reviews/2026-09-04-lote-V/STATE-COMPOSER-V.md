## HECHO

### V1 — `bridge_status` open reason set
- Added `_bridge_status_description()` (`tools/dayz_mcp/server.py:2772-2781`) that reads `READY_REASONS | set(_FENCE_BLOCK_READY.values())` at call time, sorts, joins with `|`, and embeds the contract phrase (`open set`, `shape`, `whitelist`).
- `bridge_status` tool now uses `description=_bridge_status_description()` at registration (`server.py:4141`).
- Tests: `tools/tests/test_lote_v_products.py` (`BridgeStatusDescriptionTest`).

### V2 — `entities_query` `no_player_connected`
- `_annotate_entities_reliability` adds `reason: "no_player_connected"` when `players_result.ok` is truthy and `players` is empty (`server.py:1919-1920`).
- `entities_query` description names `no_player_connected` (`server.py:3769-3771`).
- Tests: `tools/tests/test_wave_fixes_20260824.py` (`test_no_players_is_remote_with_null_distance`), `tools/tests/test_lote_v_products.py` (`EntitiesQueryWireTest`).

### V3 — `wait_for` timeout rejection (no clamp)
- Replaced silent clamp with upfront `ToolError("bad_args: timeout_s must be <= 600")` before lock/probes (`server.py:2337-2340`).
- `wait_for` description documents `timeout_s <= 600 (bad_args above; never clamped)` (`server.py:4501`).
- `tools/README-mcp.md:137` updated to rejection wording.
- Tests: `tools/tests/test_wait_for.py` (`test_rejects_timeout_above_600_before_lock`, `test_accepts_exactly_600`), `tools/tests/test_lote_v_products.py` (`WaitForWireTest`).

### V4 — `wait_for` retries `server_poll_stale`
- Players branch catches exact `game_not_ready:reason=server_poll_stale`, increments `not_ready_probes`, sets `last_error`, continues loop (`server.py:2433-2436`).
- `_wait_for_response` always emits `not_ready_probes`; `last_error` only when retries occurred (`server.py:2274-2289`, `:2484-2498`).
- `wait_for` description documents startup retry behavior (`server.py:4502-4505`).
- Tests: `tools/tests/test_wait_for.py` (`test_waits_through_server_poll_stale`, `test_server_poll_stale_timeout_reports_last_error`, `test_other_game_not_ready_aborts_first_probe`).

### V5 — `action_use` class-name contract
- Description extended with Enforce class name / `GetType()` / not visible prompt text (`server.py:4455-4460`).
- Tests: `tools/tests/test_lote_v_products.py` (`ActionUseDescriptionTest`).

## GATES

No se pudo ejecutar. Todos los intentos de `Shell` (incl. `bash gate/run.sh`, `gate/suite.sh`, y `python ../gate/oracle.py` directo) fueron rechazados por hooks de Cursor antes de arrancar el comando:

```
Hook blocked with message: --: eval: line 1: syntax error near unexpected token `&'
--: eval: line 1: `$OutputEncoding = [System.Text.Encoding]::UTF8; Get-Content ... | & { $input | powershell ... launch-ledger.ps1 ... }'
```

(mismo patrón para `prime-agent-skills-gate.ps1` y `gpu-lease-gate.ps1`).

Salida literal de la última corrida: **ninguna** — el proceso no llegó a iniciarse.

## LO QUE NO PUDE VERIFICAR

- Salida literal de `bash gate/run.sh` (G1 / oráculo).
- Salida literal de `bash gate/suite.sh` (G2 / suite acotada).
- Ejecución de la suite `unittest` en el intérprete venv especificado.
- Comportamiento e2e `V4-e2e-never-polled-server` contra runtime real con loopback (depende del oráculo).

## DISPUTAS

Ninguna.
