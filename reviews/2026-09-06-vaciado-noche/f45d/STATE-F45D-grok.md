# STATE.md — lote f45d (enqueue refusal reason)

Encargo: el cliente MCP deja de aplastar `run_not_owned` / `run_state_unavailable` / `enqueue_cancelled` a `remote_error` y, si el hint es acotado, lo pega al código. No se tocó política de valla, ni `execute_wait_for`, ni recetas.

T-CENSO: el recorrido AST de la ruta `/enqueue` no encontró ningún código extra fuera de la lista blanca además de esos tres. Se añadieron solo `run_not_owned`, `run_state_unavailable` y `enqueue_cancelled`.

## Qué cambió

### `tools/dayz_mcp/server.py`

- `:197-201` — comentario de una línea y tres entradas nuevas en `_REMOTE_ERROR_CODES`.
- `:204` — `_ENQUEUE_HINT_MAX_CHARS = 240`.
- `:207-220` — helper `_carriable_hint`.
- `:757-760` — docstring de `_public_enqueue_error` ampliado con la regla del hint.
- `:786-789` — el final de `_public_enqueue_error` deja de ser `return code`; si `code != "remote_error"` y hay hint acarreable, `return f"{code}: {hint}"`. Las tres ramas con receta (`retail_quarantine`, `lease_required`, `version_blocked`) no se tocaron.
- `:1556` (`call_bridge`) y `:1647` (`enqueue_bridge`) — la limpieza de lease local pasa a `if _remote_error_code(payload) in _STALE_LEASE_ERRORS and lease_token is not None:`.
- `:4886` — frase nueva en la descripción de `wait_for`.

`execute_wait_for`, `_remote_error_code`, `_game_not_ready_reason`, `_target_peer_down` y `_bridge_error` intactos.

### `tools/tests/test_wait_for.py`

- `:20` — `from dayz_mcp import loopback, server`.
- `:170-178` — `WaitForTest.test_an_ownership_refusal_aborts_the_first_probe_with_its_hint` (T5).

### `tools/tests/test_enqueue_refusal_reaches_the_caller.py` (nuevo)

T1 `EnqueueRefusalCodeTest`, T2 `EnqueueCodeCensusTest`, T3/T4 `ClientModeIdleRunRefusalTest`.

Limpieza de lease con hint: el test existente `tests.test_client_mode.ClientModeTest.test_lease_errors_clear_only_matching_token_for_session_and_enqueue` (`tools/tests/test_client_mode.py:443-456`) ya pinea que `call_bridge` con `lease_expired`/`lease_invalid` desnudos deja `active_lease_token is None`. T3 añade el mismo camino con `hint: "x"`.

## FRASES REESCRITAS

### `tools/dayz_mcp/server.py:197` (comentario nuevo)

- viejo: (no existía)
- nuevo: `# run-fence refusals emitted by loopback._enqueue_run_rejection and loopback._enqueue_command.`

### `tools/dayz_mcp/server.py:207-208` (docstring nuevo)

- viejo: (no existía)
- nuevo: `"""Accredited-daemon prose travels only beside a whitelist code, bounded; it never replaces the code."""`

### `tools/dayz_mcp/server.py:757-760` (docstring)

- viejo: `"""Map a remote enqueue payload to the caller-facing ToolError string."""`
- nuevo:

```
    """Map a remote enqueue payload to the caller-facing ToolError string.

    A known code with a valid hint travels as "<code>: <hint>"; a known code without a hint stays bare; an unknown code stays the bare token remote_error even when a hint is present.
    """
```

### `tools/dayz_mcp/server.py:4885-4886` (descripción de `wait_for`)

- viejo: `"other not-ready reason aborts on the first probe. "`
- nuevo: `"other not-ready reason aborts on the first probe. " "A probe refused with run_not_owned (the run has no owner) aborts on the first probe with the daemon's hint: adopt the run with session_acquire_wait first. "`

## Censo T2 (ordenado)

25 códigos. Todos contenidos en `_REMOTE_ERROR_CODES` tras el cambio. El AST sí encontró `run_not_owned`, `run_state_unavailable` y `enqueue_cancelled` (el test nombra al que falte si el recorrido se rompe). No hubo códigos extra que añadir.

```
audit_failed
bad_args
bad_operation_timeout
bad_peer
binding_not_ready
binding_retired
creation_time_unreadable
enqueue_cancelled
exec_not_allowed
instance_ambiguous
instance_malformed
instance_peer_collision
instance_role_mismatch
instance_unattributed
instance_unknown
invalid_identity
lease_invalid
legacy_unbound
not_whitelisted
queue_full
retail_quarantine
run_not_owned
run_state_unavailable
unbound_after_restart
version_blocked
```

## Ejecuciones

Interprete: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe`
Desde `<workspace>\tools` con `PYTHONPATH=.`.

### Rojo primero (producto sin el arreglo; tests ya escritos)

`PYTHONPATH=. <interprete> -m unittest tests.test_enqueue_refusal_reaches_the_caller tests.test_wait_for`

```
.FFFFF.F.FF........................
======================================================================
FAIL: test_call_bridge_surfaces_the_idle_run_refusal_with_its_hint (tests.test_enqueue_refusal_reaches_the_caller.ClientModeIdleRunRefusalTest.test_call_bridge_surfaces_the_idle_run_refusal_with_its_hint)
AssertionError: 'remote_error' != 'run_not_owned: This run has no owner (RUNN[49 chars]ing.'
======================================================================
FAIL: test_enqueue_bridge_surfaces_the_same_text (tests.test_enqueue_refusal_reaches_the_caller.ClientModeIdleRunRefusalTest.test_enqueue_bridge_surfaces_the_same_text)
AssertionError: 'remote_error' != 'run_not_owned: This run has no owner (RUNN[49 chars]ing.'
======================================================================
FAIL: test_the_wait_for_description_names_the_ownership_refusal (tests.test_enqueue_refusal_reaches_the_caller.ClientModeIdleRunRefusalTest.test_the_wait_for_description_names_the_ownership_refusal)
AssertionError: 'run_not_owned' not found in "Block until a condition holds. ..."
======================================================================
FAIL: test_every_code_the_enqueue_route_can_emit_is_whitelisted (tests.test_enqueue_refusal_reaches_the_caller.EnqueueCodeCensusTest.test_every_code_the_enqueue_route_can_emit_is_whitelisted)
AssertionError: ['enqueue_cancelled', 'run_not_owned', 'run_state_unavailable'] is not false : enqueue codes not in _REMOTE_ERROR_CODES: enqueue_cancelled, run_not_owned, run_state_unavailable
======================================================================
FAIL: test_a_fence_hint_travels_with_its_code (tests.test_enqueue_refusal_reaches_the_caller.EnqueueRefusalCodeTest.test_a_fence_hint_travels_with_its_code)
AssertionError: 'legacy_unbound' != 'legacy_unbound: Peer poll lacks a valid inst=. ...'
======================================================================
FAIL: test_an_oversized_or_malformed_hint_is_dropped (tests.test_enqueue_refusal_reaches_the_caller.EnqueueRefusalCodeTest.test_an_oversized_or_malformed_hint_is_dropped)
AssertionError: 'remote_error' != 'run_not_owned'
======================================================================
FAIL: test_every_run_fence_code_is_whitelisted (tests.test_enqueue_refusal_reaches_the_caller.EnqueueRefusalCodeTest.test_every_run_fence_code_is_whitelisted)
AssertionError: 'remote_error' != 'run_not_owned'
======================================================================
FAIL: test_the_idle_run_refusal_keeps_its_code_and_hint (tests.test_enqueue_refusal_reaches_the_caller.EnqueueRefusalCodeTest.test_the_idle_run_refusal_keeps_its_code_and_hint)
AssertionError: 'remote_error' != 'run_not_owned: This run has no owner (RUNN[49 chars]ing.'
----------------------------------------------------------------------
Ran 35 tests in 9.609s

FAILED (failures=8)
```

Desglose de partida:

- Módulo nuevo: 12 tests, 8 FAIL / 4 OK.
- T1 ya verdes hoy (como pedía el brief): `test_a_whitelisted_code_without_hint_stays_bare`, `test_an_unknown_code_drops_its_hint`.
- T1 también verde hoy (las recetas no miran el hint): `test_the_recipes_keep_their_own_text`.
- T3 ya verde hoy: `test_a_hinted_stale_lease_still_clears_the_local_lease` (el hint aún no viaja, el código queda desnudo `lease_invalid` y entra en `_STALE_LEASE_ERRORS`).
- T1/T2/T3 (los dos primeros)/T4 rojos, como pedía el brief.
- `tests.test_wait_for`: 23 tests, todos OK. T5 (`test_an_ownership_refusal_aborts_the_first_probe_with_its_hint`) verde de partida: el `else: raise` de `execute_wait_for` relanza el `ToolError` intacto. No es un rojo.

### Verde final (tras el arreglo)

`PYTHONPATH=. <interprete> -m unittest tests.test_enqueue_refusal_reaches_the_caller`

```
............
----------------------------------------------------------------------
Ran 12 tests in 0.141s

OK
```

`PYTHONPATH=. <interprete> -m unittest tests.test_wait_for`

```
...Executing <Task finished name='Task-17' coro=<WaitForBug086EvidenceTest.test_the_public_tool_default_also_sees_the_earlier_response() ...> took 0.114 seconds
....................
----------------------------------------------------------------------
Ran 23 tests in 9.497s

OK
```

Recuento: módulo nuevo 12→12 (8 FAIL → 12 OK). `tests.test_wait_for` 23→23 OK (T5 verde en ambos extremos).

### Regresión

| módulo | salida |
|---|---|
| `tests.test_client_mode` | `Ran 53 tests in 7.398s` / `OK` (el CLI del daemon imprimió `usage: python.exe -m unittest ... error: the following arguments are required: --keyfile` en un subtest; no es fallo) |
| `tests.test_weak_agent_consumer_ux` | `Ran 23 tests in 0.627s` / `OK` |
| `tests.test_server_response_truth` | `Ran 15 tests in 0.885s` / `OK` |
| `tests.test_bad_args_messages` | `Ran 7 tests in 0.410s` / `OK` |
| `tests.test_wait_for_launch_and_contract` | `Ran 21 tests in 4.369s` / `OK` |
| `tests.test_wait_for_requires_a_live_run` | `Ran 3 tests in 0.035s` / `OK` |
| `tests.test_box_occupancy` | `Ran 81 tests in 1.102s` / `OK` |
| `tests.test_loopback` | `Ran 74 tests in 0.909s` / `OK` |
| `tests.test_mcp_tools` | `Ran 46 tests in 7.672s` / `OK` |
| `tests.test_session_e2e` | `Ran 14 tests in 2.975s` / `OK` |

Ningún método con «acceso denegado» por fixture Win32.

## Mutantes

`server.py` se restauró byte a byte tras cada mutante. Hash estable del arreglo:

- antes / después de cada restore: `5641ec1485f7f0133c7ad02c9fc3df2444819182e956d809ad2a8307c98754c7`

### (a) quitar `"run_not_owned"` de `_REMOTE_ERROR_CODES`

- hash mutante: `6fa056b05563afe67edc8a51ea27d2486f0e7f85f13776f2c64d9136917e8b8c`
- `tests.test_enqueue_refusal_reaches_the_caller`: `Ran 12 tests in 0.139s` / `FAILED (failures=6)`
- rojos: T2 `test_every_code_the_enqueue_route_can_emit_is_whitelisted` (falta `run_not_owned`); `test_the_idle_run_refusal_keeps_its_code_and_hint`; también `test_every_run_fence_code_is_whitelisted`, `test_an_oversized_or_malformed_hint_is_dropped`, y T3 `test_call_bridge_surfaces_the_idle_run_refusal_with_its_hint` / `test_enqueue_bridge_surfaces_the_same_text`.
- restore: `5641ec1485f7f0133c7ad02c9fc3df2444819182e956d809ad2a8307c98754c7`

### (b) volver a `return code` sin hint al final de `_public_enqueue_error`

- hash mutante: `0a94aec5027dd998afb7f9f85d12fece2844b9517365c26f19f903541cc6b321`
- `tests.test_enqueue_refusal_reaches_the_caller`: `Ran 12 tests in 0.150s` / `FAILED (failures=5)`
- rojos T1/T3 de hint: `test_the_idle_run_refusal_keeps_its_code_and_hint`, `test_a_fence_hint_travels_with_its_code`, `test_an_oversized_or_malformed_hint_is_dropped` (el de 240 caracteres), `test_call_bridge_surfaces_the_idle_run_refusal_with_its_hint`, `test_enqueue_bridge_surfaces_the_same_text`.
- restore: `5641ec1485f7f0133c7ad02c9fc3df2444819182e956d809ad2a8307c98754c7`

### (c) quitar `code != "remote_error"` (el hint viaja también con código desconocido)

- hash mutante: `c909aa9b9af0f9b9aef29bad374761ccbbf6077053903a63e2989aea800bffeb`
- `tests.test_enqueue_refusal_reaches_the_caller`: `Ran 12 tests in 0.143s` / `FAILED (failures=2)` — las dos subpruebas de `test_an_unknown_code_drops_its_hint` con hint (`arbitrary_remote_text` y error no-str) devolvieron `remote_error: sensitive-test-value`.
- control negativo `tests.test_client_mode.ClientModeTest.test_session_http_error_never_echoes_unredacted_payload`: `Ran 1 test in 0.022s` / `OK` (no cambió: esos payloads no llevan `hint`).
- restore: `5641ec1485f7f0133c7ad02c9fc3df2444819182e956d809ad2a8307c98754c7`

## sha256 final del write-set

(python `hashlib.sha256`; ficheros en LF, 0 CRLF)

- `tools/dayz_mcp/server.py` `5641ec1485f7f0133c7ad02c9fc3df2444819182e956d809ad2a8307c98754c7`
- `tools/tests/test_wait_for.py` `571dba41ef55b15b7fd497e1068f0b2fa367da563523f557d7dfdb5526e8504f`
- `tools/tests/test_enqueue_refusal_reaches_the_caller.py` `6143cefab178a716a56a70772e0cd2317259d64c02f6e5d6eb8f07cd35d83cae`

## LO QUE NO PUDE VERIFICAR

- No corrí la suite completa de `tools/tests` (el brief lo prohíbe: levanta daemons y se cuelga con sesiones vivas).
- No lancé juego, daemon ni launcher; el caso medido 2026-09-06 21:07 (`wait_for` → `query_all_players` → 409 `run_not_owned` con hint) no se reprodujo contra un proceso vivo, solo contra `_public_enqueue_error`, `call_bridge`/`enqueue_bridge` con `_call` fijado, y `execute_wait_for` con `_FakeRuntime`.
- No leí ni escribí fuera del workspace. No pude contrastar el arreglo con el worktree git original (este workspace no es un repositorio).
- En esta máquina no apareció el «acceso denegado» Win32 de un revisor anterior; si ocurre en otro sandbox, aquí no se vio.
