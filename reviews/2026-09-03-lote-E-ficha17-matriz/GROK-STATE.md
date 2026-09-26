## HECHO

Ronda 2: un UUID malformado llega al cliente como `bad_run_id` (el mismo código que `_exact_run` en stop/extensión). No se tocó `gate/`, `dayz_test_worker.py`, `dayz_test_request.py`, `addon/` ni `tests/test_client_mode.py`. Identity mappings y prefijo de client/server: intactos (disputa 2).

- `tools/dayz_mcp/dayz_test_tool.py:180-181` — la fachada traduce el token interno del parser (`_INVALID_RUN_ID` = `invalid_run_id`) a `_fail("bad_run_id")`, igual que `_exact_run` en `:208`. `mode=server` + UUID malformado sigue ganando a `server_all_forbid_run_id` porque el parser valida el UUID antes de la combinación mode/run_id.
- `tools/dayz_mcp/server.py:193` — el mapa renombra `"invalid_run_id"` → `"bad_run_id"` para el ValueError que se escape de la fachada (`native_launcher_transaction.py:108` bajo `_typed_dayz_test_value_errors`). Un solo código público.
- `tools/tests/test_dayz_test_tool.py:225` y `:262` — la fachada fija `bad_run_id` en ambos `preflight` y en la precedencia contra mode/run_id.
- `tools/tests/test_mcp_tools.py:728` y `:759` — el mapa apunta a `bad_run_id`; la tool pública lo nombra en el wire.

## GATE

```
========================================================================================================
[PASS ] E0-SELLO tests/test_client_mode.py sin editar
          sha256=e11c5bbcb82ca626
[PASS ] E1-UUID preflight=False nombra la causa en fachada y tool
          esperaba 'bad_run_id' | fachada='bad_run_id' | publica='Error executing tool dayz_test_run: bad_run_id'
[PASS ] E1-UUID preflight=True  nombra la causa en fachada y tool
          esperaba 'bad_run_id' | fachada='bad_run_id' | publica='Error executing tool dayz_test_run: bad_run_id'
[PASS ] E1-CLIENT preflight=False nombra la causa en fachada y tool
          esperaba 'client_requires_run_id' | fachada='bad_dayz_test_request:client_requires_run_id' | publica='Error executing tool dayz_test_run: bad_dayz_test_request:client_requi'
[PASS ] E1-CLIENT preflight=True  nombra la causa en fachada y tool
          esperaba 'client_requires_run_id' | fachada='bad_dayz_test_request:client_requires_run_id' | publica='Error executing tool dayz_test_run: bad_dayz_test_request:client_requi'
[PASS ] E1-SERVER preflight=False nombra la causa en fachada y tool
          esperaba 'server_all_forbid_run_id' | fachada='bad_dayz_test_request:server_all_forbid_run_id' | publica='Error executing tool dayz_test_run: bad_dayz_test_request:server_all_f'
[PASS ] E1-SERVER preflight=True  nombra la causa en fachada y tool
          esperaba 'server_all_forbid_run_id' | fachada='bad_dayz_test_request:server_all_forbid_run_id' | publica='Error executing tool dayz_test_run: bad_dayz_test_request:server_all_f'
[PASS ] E1-ALL preflight=False nombra la causa en fachada y tool
          esperaba 'server_all_forbid_run_id' | fachada='bad_dayz_test_request:server_all_forbid_run_id' | publica='Error executing tool dayz_test_run: bad_dayz_test_request:server_all_f'
[PASS ] E1-ALL preflight=True  nombra la causa en fachada y tool
          esperaba 'server_all_forbid_run_id' | fachada='bad_dayz_test_request:server_all_forbid_run_id' | publica='Error executing tool dayz_test_run: bad_dayz_test_request:server_all_f'
[PASS ] E1-PREC el UUID malformado vence a la combinacion mode/run_id
          fachada='bad_run_id'
[PASS ] E2-REATTACH preflight+client+UUID no acaba en terminal_invalid
          publica='<ok>{"status": "succeeded", "project": "ExampleMod", "mode": "client", "run_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "phase": "completed", "elapse'
[PASS ] E2-UUID el UUID de la peticion sobrevive en la respuesta
          publica='<ok>{"status": "succeeded", "project": "ExampleMod", "mode": "client", "run_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "phase": "completed", "elapse'
[PASS ] E2-SERVER preflight+server+null no acaba en terminal_invalid
          publica='<ok>{"status": "succeeded", "project": "ExampleMod", "mode": "server", "run_id": null, "phase": "completed", "elapsed_s": 0.0, "artifacts_paths": ["P:'
[PASS ] E3-DRYRUN preflight=client no consulta Steam
          consultas=0
[PASS ] E3-DRYRUN preflight=server no consulta Steam
          consultas=0
[PASS ] E3-DRYRUN preflight=all no consulta Steam
          consultas=0
[PASS ] E3-CONTROL una fila productiva client SI consulta Steam
          consultas=1 -- si es 0, E3 se cumple por vacuidad
[PASS ] E2-CONTROL un terminal incoherente SIGUE rechazandose por la tool publica
          publica='Error executing tool dayz_test_run: terminal_invalid' -- si sale <ok>, la correlacion se borro en vez de corregirse
[PASS ] E4 la descripcion REGISTRADA menciona la secuencia reattach
          presente=True | len(desc)=803
[PASS ] E4 la descripcion REGISTRADA menciona que preflight no relaja la matriz
          presente=True | len(desc)=803
========================================================================================================
ORACULO: PASS=20 FAIL=0 UNMET=0 de 20
ORACULO-VERDE
```

```
test_dayz_test_tool : Ran 45 tests in 0.186s  OK
test_dayz_test_request : Ran 12 tests in 0.017s  OK
test_dayz_test_worker : Ran 24 tests in 0.033s  OK
test_mcp_tools : Ran 33 tests in 5.480s  OK
test_client_mode : Ran 53 tests in 6.061s  OK
test_request_path_authority : Ran 13 tests in 0.360s  OK (skipped=1)
SUITE-ACOTADA OK
```

## CONTROL POSITIVO

Tests actualizados a `bad_run_id` y corridos **antes** del cambio de producción, mismo intérprete (`cd tools && PYTHONPATH=. … -m unittest`):

- `test_build_run_request_names_run_id_matrix_causes` — `AssertionError: 'bad_run_id' not found in 'invalid_run_id'` (preflight=False y True); precedencia `AssertionError: 'invalid_run_id' != 'bad_run_id'`.
- `test_dayz_test_run_id_matrix_tokens_are_mapped` — `AssertionError: 'invalid_run_id' != 'bad_run_id'`.
- `test_dayz_test_run_names_run_id_matrix_causes_on_the_wire` — `AssertionError: 'bad_run_id' not found in 'Error executing tool dayz_test_run: invalid_run_id'` (ambos preflight).

Tras el arreglo: `Ran 3 tests in 0.113s OK`. Luego `gate/run.sh` → ORACULO-VERDE y `gate/suite.sh` → SUITE-ACOTADA OK.

## LO QUE NO PUDE VERIFICAR

- Suite completa del repo (~2303 tests): prohibida en este lote; la acotada del gate sí.
- Path ValueError que bypasea la fachada (`native_launcher_transaction.py:108`): el mapa ahora renombra a `bad_run_id`, pero no fabriqué un leak real por esa vía. El camino medido es fachada + `app.call_tool("dayz_test_run")`.
- Cliente MCP real / FastMCP remoto: el wire se observó con `app.call_tool` (el mismo camino que el oráculo), no con un cliente externo.
- Hash-sello de `gate/`: no lo recalculé; el brief dice que lo comprueba el receptor.
- In-game, red, PBO, Steam de verdad: fuera de alcance.

## DISPUTAS

Ninguna abierta.

1. **Cerrada a favor del oráculo corregido.** El código público del UUID malformado es `bad_run_id` (ya enviado en `_exact_run`). El parser sigue lanzando `_INVALID_RUN_ID` internamente; no se tocó `dayz_test_request.py`. Fachada y mapa traducen.

2. **Cerrada a nuestro favor, sin cambios.** Identity mappings de `client_requires_run_id` / `server_all_forbid_run_id` y el prefijo `bad_dayz_test_request:` de la fachada se quedan. `_is_safe_error_token` e `isidentifier()` no se tocaron.
