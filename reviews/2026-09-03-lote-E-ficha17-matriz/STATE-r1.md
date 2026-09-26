## HECHO

P1/P2/P3 de `dayz_test_run` sobre el write-set. No se tocó `gate/`, `dayz_test_worker.py`, `dayz_test_request.py`, `addon/` ni `tests/test_client_mode.py`.

- `tools/dayz_mcp/dayz_test_tool.py:178-187` — la fachada ya no colapsa las tres causas del parser en `bad_dayz_test_request`. Propaga `invalid_run_id` y `bad_dayz_test_request:{client_requires_run_id|server_all_forbid_run_id}`. El mismo código sale por `build_run_request` y por la tool pública (`execute_dayz_test_run` → `ToolError(code)`).
- `tools/dayz_mcp/dayz_test_tool.py:540-545` — `_validate_terminal_context` repara la correlación: si hay `expected_run_id`, el terminal debe devolver exactamente ese UUID (preflight client reattach). Si no hay, se conserva el XOR original (`preflight` ⇔ `run_id is None`). Un launch real sin `run_id` sigue en `terminal_invalid`.
- `tools/dayz_mcp/server.py:193-195` — `_DAYZ_TEST_VALUE_ERROR_CODES` registra los tres tokens del parser.
- `tools/dayz_mcp/server.py:2886-2888` — descripción **registrada** de `dayz_test_run`: secuencia `server -> run_id -> client(run_id)`, prohibición simétrica, y que preflight no relaja la matriz. Palabras `reattach` y `preflight` visibles en `app.list_tools()`.
- `tools/tests/test_dayz_test_tool.py:219` — fachada nombra las tres causas × preflight y la precedencia UUID.
- `tools/tests/test_dayz_test_tool.py:538-547` — reattach preflight+UUID aceptado; preflight que pierde el UUID sigue rechazado.
- `tools/tests/test_dayz_test_tool.py:1104` — `execute_dayz_test_run(preflight=True, mode=client, run_id=…)` conserva el UUID y no consulta Steam.
- `tools/tests/test_mcp_tools.py:724` — los tres tokens están en el mapa público.
- `tools/tests/test_mcp_tools.py:737` — las tres causas llegan nombradas por la tool pública, ambos valores de preflight.
- `tools/tests/test_mcp_tools.py:800` — reattach por FastMCP: `status=succeeded` y el UUID sobrevive.
- `tools/tests/test_mcp_tools.py:869` — descripción registrada menciona `reattach` y `preflight`.

## GATE

```
========================================================================================================
[PASS ] E0-SELLO tests/test_client_mode.py sin editar
          sha256=e11c5bbcb82ca626
[PASS ] E1-UUID preflight=False nombra la causa en fachada y tool
          esperaba 'invalid_run_id' | fachada='invalid_run_id' | publica='Error executing tool dayz_test_run: invalid_run_id'
[PASS ] E1-UUID preflight=True  nombra la causa en fachada y tool
          esperaba 'invalid_run_id' | fachada='invalid_run_id' | publica='Error executing tool dayz_test_run: invalid_run_id'
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
          fachada='invalid_run_id'
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
test_dayz_test_tool : Ran 45 tests in 0.205s  OK
test_dayz_test_request : Ran 12 tests in 0.022s  OK
test_dayz_test_worker : Ran 24 tests in 0.034s  OK
test_mcp_tools : Ran 33 tests in 5.708s  OK
test_client_mode : Ran 53 tests in 6.225s  OK
test_request_path_authority : Ran 13 tests in 0.425s  OK (skipped=1)
SUITE-ACOTADA OK
```

## CONTROL POSITIVO

Corridos **antes** del arreglo, mismo intérprete, `cd tools && PYTHONPATH=. … -m unittest <casos nuevos>`:

- `test_build_run_request_names_run_id_matrix_causes` — `AssertionError: 'invalid_run_id' not found in 'bad_dayz_test_request'` (igual con `client_requires_run_id` y `server_all_forbid_run_id`).
- `test_success_run_id_matches_operation_context` (caso reattach añadido) — `DayzTestToolError: terminal_invalid` en `_validate_terminal_context` (`dayz_test_tool.py:533` pre-arreglo).
- `test_preflight_client_reattach_preserves_requested_run_id` — `DayzTestToolError: terminal_invalid` al volver el worker con el UUID pedido.
- `test_dayz_test_run_id_matrix_tokens_are_mapped` — `AssertionError: 'invalid_run_id' not found in {'invalid_dayz_test_path_authority': 'bad_mod_authority', ...}` (y los otros dos tokens).
- `test_dayz_test_run_names_run_id_matrix_causes_on_the_wire` — `AssertionError: 'invalid_run_id' not found in 'Error executing tool dayz_test_run: bad_dayz_test_request'`.
- `test_dayz_test_run_preflight_client_reattach_keeps_run_id` — `ToolError: Error executing tool dayz_test_run: terminal_invalid`.
- `test_dayz_test_run_description_documents_reattach_matrix` — `AssertionError: 'reattach' not found in 'queue and run an approved dayz test project; … mode is server|all|client. wait_for_box_s>0 waits …'`.

Tras el arreglo, los 7 casos: `Ran 7 tests in 0.295s OK`.

## LO QUE NO PUDE VERIFICAR

- Suite completa del repo (~2303 tests): prohibida en este lote; la acotada del gate sí.
- Cliente MCP real / FastMCP remoto: la descripción se leyó con `app.list_tools()` (el mismo camino que el oráculo), no con un cliente externo.
- Path ValueError que bypasea la fachada (parse dentro del launcher): el mapa ahora tiene los tres tokens, pero no fabriqué un leak real por `native_launcher_transaction`.
- Hash-sello de `gate/`: no lo recalculé; el brief dice que lo comprueba el receptor.
- In-game, red, PBO, Steam de verdad: fuera de alcance.
- Que un cliente humano lea `invalid_run_id` y lo busque como `bad_run_id` (ver DISPUTAS).

## DISPUTAS

1. **Código del UUID malformado.** El brief pedía que el cliente viera `bad_run_id`. El oráculo importa `dayz_test_request._INVALID_RUN_ID` (`invalid_run_id`) y exige esa cadena en fachada y tool (E1-UUID, E1-PREC). Emitir `bad_run_id` pone esos checks en rojo (`'invalid_run_id' not found in 'bad_run_id'`). Dejé `invalid_run_id`, que ya es el token del parser y pasa E1. `_exact_run` sigue usando `bad_run_id` para UUID malformado en stop/extensión; son dos códigos públicos para la misma clase de error.

2. **Valores del mapa con dos puntos.** El brief pedía `bad_dayz_test_request:client_requires_run_id` / `…:server_all_forbid_run_id` también en `_DAYZ_TEST_VALUE_ERROR_CODES`. `test_dayz_test_value_error_tokens_are_all_mapped` exige `code.isidentifier()` para cada valor del mapa; un `:` lo rompe. El mapa lleva identity mappings (identificadores). La fachada/tool sí emiten el prefijo con `:`, y el oráculo lo acepta porque busca el token del parser como substring. Un ValueError que se escape de la fachada llegaría al cliente como `client_requires_run_id` (sin prefijo) vía el mapa o el fallback `_is_safe_error_token`.
