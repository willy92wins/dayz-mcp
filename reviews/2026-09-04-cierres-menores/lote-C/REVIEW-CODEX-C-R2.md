# Revisión lote C — ronda 2 — fb-20260829-221423-b2c4

Rama observada: `work/inbox-20260830-modules`.

HEAD observado: `adc1c22f0b890f2132439541d6943ffed430c7d8`.

Intérprete usado en todas las ejecuciones Python:
`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe`.

Directorio de trabajo Python: `DayZ_MCP_dev\tools`, con `PYTHONPATH=.` y
`PYTHONDONTWRITEBYTECODE=1`.

## VEREDICTO

**BLOQUEANTES=1.** El B-01 de la ronda 1 está corregido en comportamiento: su repro literal sale
0 y el texto de errores no-UI permanece pelado en `Runtime` y en las dos rutas de resultado de
`ClientRuntime`. La lista blanca también excluye `requested_text` y claves futuras.

El bloqueante nuevo es de gate/cobertura, no un defecto funcional reproducido en los bytes
restaurados: el mutante obligatorio R-03(a) sobrevive. Quitar únicamente el gate
`cmd not in _UI_ECHO_VERBS` deja verde el negativo plano de `world_spawn`, porque el guard
independiente `cmd == "ui_click"` sigue excluyendo los escalares y el fixture lleva
`ui_request={}`. Por tanto, el lote no satisface el criterio de aceptación explícito de que ese
mutante quede rojo.

## R-01 — B-01: errores no-UI con forma plana

Repetí el repro [EXACT] de la ronda 1 mediante una sesión MCP cliente/servidor en memoria, sin
daemon ni red. Salida literal recortada:

```text
test_world_spawn_wire_scalar_defaults_keeps_bare_message ... ok
Ran 1 test in 0.198s
OK
ACTUAL_WIRE_TEXT='Error executing tool world_spawn: timeout'
EXACT_REPRO_EXIT=0
```

También ejecuté llamadas directas con la forma plana para otros dos verbos no-UI y para
`cmd=None`:

```text
WORLD_SPAWN_DIRECT='timeout'
WORLD_SPAWN_NONE='timeout'
PLAYER_TELEPORT_DIRECT='bad_pos'
OBJECT_INSPECT_DIRECT='bridge_error'
```

Construí barato las dos rutas de `ClientRuntime` sin ejecutar su constructor ni abrir red: una
instancia con `object.__new__`, `_call` sintético y payload `status=done` ejercitó
`_await_result` y `probe_bridge_result`. Salida:

```text
CLIENT_AWAIT='timeout'
CLIENT_PROBE='timeout'
DIRECT_AND_CLIENT_PROBES=PASS
DIRECT_PROBES_EXIT=0
```

El mecanismo leído coincide con el resultado: los cuatro `raise` pasan `cmd` en
`tools/dayz_mcp/server.py:933`, `:963`, `:1564` y `:1636`; `_bridge_error` entrega ese verbo a
`_bridge_error_detail` en `tools/dayz_mcp/server.py:689-698`. Los escalares planos existen en
`addon/scripts/5_Mission/MCPMessages.c:423-479`.

**Conclusión R-01:** PASS. El B-01 original queda cerrado por repro ejecutado, no solo por lectura.

## R-02 — lista blanca y exclusión de `requested_text`

Ejecuté un `ok:0` de `ui_click` con `requested_text="dump secret-token"`,
`future_key="x"`, `requested_path="A"` y los tres escalares. Exigí igualdad exacta y ausencia
de los valores excluidos:

```text
UI_CLICK_ALLOWLIST="not_handled; handler='H' user_id=506 clicked=False; requested_path='A'"
DIRECT_AND_CLIENT_PROBES=PASS
DIRECT_PROBES_EXIT=0
```

`secret-token`, `requested_text`, `future_key` y su valor `x` no aparecieron. La lista blanca
real es `requested_path/requested_root/matched_path` en
`tools/dayz_mcp/server.py:647-649,679-685`; los valores `None` y `""` se omiten en `:682`.

El focal también comprobó `ui_set_text` con texto sensible y sin escalares de click:

```text
test_other_ui_verbs_get_the_echo_but_not_the_click_scalars ... ok
```

**Conclusión R-02:** PASS funcional. La clave futura se comprobó con una sonda ejecutada, pero no
está pineada por un test del fichero entregado; queda en BACKLOG.

## R-03 — mutantes y restauración manual

SHA-256 antes de mutar:

```text
server.py|CA4E442FAB43F5D38318661123370F62A5061BC5DC623C3460BC7975515FC01B
test_ui_error_diagnostics.py|30D59B64192A53A2807A61026248131244046288273BE9EC38970F571B110D0F
test_lote_b_products.py|C452B5B96AF59228B02B38966AA35F8F5D3B5AA4FD93671D1E15CD51BED7CDC3
```

### Mutante (a): quitar el gate por verbo

Eliminé temporalmente, mediante parche manual, `tools/dayz_mcp/server.py:670-671` y ejecuté el
negativo indicado:

```text
test_flat_scalars_do_not_enrich_non_ui_verbs ... ok
Ran 1 test in 0.000s
OK
MUTANT_A_EXIT=0
```

**Resultado:** VERDE; el mutante sobrevivió. La causa está en la propia composición del caso:
`tools/tests/test_ui_error_diagnostics.py:68-76` usa escalares planos y `ui_request={}`, mientras
el segundo guard de producción en `tools/dayz_mcp/server.py:673` sigue evitando los escalares
para `world_spawn`. Esto constituye el bloqueante B-02 descrito abajo.

Restauré las dos líneas a mano. SHA inmediatamente después:

```text
AFTER_MUTANT_A_RESTORE_SHA256=CA4E442FAB43F5D38318661123370F62A5061BC5DC623C3460BC7975515FC01B
```

### Mutante (b): escalares para todo verbo UI

Cambié temporalmente `if cmd == "ui_click"` por `if True` y ejecuté el test de
`ui_set_text`:

```text
test_other_ui_verbs_get_the_echo_but_not_the_click_scalars ... FAIL
AssertionError: "text_not_writable; handler='' user_id=0 clicked=False; requested_path='Cmd'"
!= "text_not_writable; requested_path='Cmd'"
Ran 1 test in 0.000s
FAILED (failures=1)
MUTANT_B_EXIT=1
```

**Resultado:** ROJO esperado. Restauración manual aplicada.

### Mutante (c): `detail; code`

Invertí temporalmente la composición de `ToolError` en `tools/dayz_mcp/server.py:698` y ejecuté
el test exacto de `ui_click`:

```text
test_ui_click_fields_ride_in_the_message_after_the_code ... FAIL
actual:   handler='LFPG_SorterView_TEST' user_id=506 clicked=False; requested_path='BtnCloseX' matched_path='LFPG_Sorter/BtnCloseX'; not_handled
esperado: not_handled; handler='LFPG_SorterView_TEST' user_id=506 clicked=False; requested_path='BtnCloseX' matched_path='LFPG_Sorter/BtnCloseX'
Ran 1 test in 0.001s
FAILED (failures=1)
MUTANT_C_EXIT=1
```

**Resultado:** ROJO esperado. Restauración manual aplicada.

### Estado restaurado

No usé `git checkout`, `git restore` ni reset. Tras los tres mutantes:

```text
RESTORED_SHA256=CA4E442FAB43F5D38318661123370F62A5061BC5DC623C3460BC7975515FC01B
tools/dayz_mcp/server.py | 68 +++++++++++++++++++++++++++++++++++++++++-------
1 file changed, 59 insertions(+), 9 deletions(-)
DIFF_CHECK_EXIT=0
```

Hashes finales de los tres ficheros, iguales a los impresos antes de mutar:

```text
server.py|CA4E442FAB43F5D38318661123370F62A5061BC5DC623C3460BC7975515FC01B
test_ui_error_diagnostics.py|30D59B64192A53A2807A61026248131244046288273BE9EC38970F571B110D0F
test_lote_b_products.py|C452B5B96AF59228B02B38966AA35F8F5D3B5AA4FD93671D1E15CD51BED7CDC3
```

El `--stat` real no es el aproximado `+52/-4` del brief: tanto el árbol vivo como
`git apply --numstat DIFF-C2.patch` dan exactamente `59/9` para `server.py`; no es deriva de los
mutantes. `git apply --reverse --check DIFF-C2.patch` terminó 0, por lo que los tres ficheros vivos
corresponden al parche entregado.

## R-04 — regresión

Primero ejecuté literalmente los dos módulos focales:

```text
python -m unittest tests.test_ui_error_diagnostics tests.test_lote_b_products -v
Ran 8 tests in 0.646s
OK
FOCAL_EXIT=0
```

Después ejecuté en una sola invocación los doce módulos pedidos:

```text
python -m unittest tests.test_d09_d10_spawn_timeout_object_id \
  tests.test_boundary_values_are_pinned \
  tests.test_client_runtime_control_composition \
  tests.test_wait_for tests.test_mcp_tools \
  tests.test_weak_agent_consumer_ux tests.test_docs_truth \
  tests.test_lote_v_products tests.test_tool_registry_fingerprint \
  tests.test_ui_enforce_contract tests.test_bad_args_messages \
  tests.test_ui_click_scriptview -v
Ran 196 tests in 18.741s
OK (skipped=3)
REGRESSION_EXIT=0
```

Los tres skips fueron condiciones de entorno declaradas: dos de sparse checkout del addon y una
por ausencia del árbol vanilla `P:\scripts`. Ningún módulo solicitado quedó rojo.

La frase de descripción de `ui_click` **no es falsa contra el código**. Está publicada en
`tools/dayz_mcp/server.py:4445-4449`; la composición efectiva es `code; detail` en `:696-698`.
En el bridge, `InvokeUiClick` devuelve el nombre y el bool, después se asignan
`user_id/handler/clicked` y solo entonces se distingue `no_handler` de `not_handled` en
`addon/scripts/5_Mission/MCPClientBridge.c:1463-1482`. El wire de `list_tools` también la pinea:

```text
test_description_sentences_reach_a_real_client_session ... ok
```

**Conclusión R-04:** PASS para los 204 tests solicitados (8 focales + 196 de regresión), con tres
skips explícitos. El PASS de regresión no compensa el mutante obligatorio superviviente.

## BLOQUEANTES

### B-02 — R-03(a) es un mutante superviviente con el negativo entregado

**Severidad concreta:** defecto de gate/cobertura de regresión; no es crash, excepción ni
corrupción. Bloquea este lote porque R-03 exige que el negativo plano de `world_spawn` se ponga
rojo al quitar el gate por verbo, y la ejecución real queda verde.

**Mecanismo:** el mutante elimina `tools/dayz_mcp/server.py:670-671`, pero no elimina el guard
independiente `cmd == "ui_click"` de `:673`. El fixture de
`tools/tests/test_ui_error_diagnostics.py:68-76` lleva `ui_request={}`, de modo que ninguna rama
restante puede producir detalle. El test observa exactamente el mismo texto con y sin el gate.

Repro [EXACT]:

```diff
[EXACT]
--- a/tools/dayz_mcp/server.py
+++ b/tools/dayz_mcp/server.py
@@
-    if cmd not in _UI_ECHO_VERBS:
-        return ""
     parts: list[str] = []
```

```powershell
[EXACT]
$env:PYTHONPATH='.'
$env:PYTHONDONTWRITEBYTECODE='1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' `
  -m unittest `
  tests.test_ui_error_diagnostics.BridgeErrorDiagnosticsTest.test_flat_scalars_do_not_enrich_non_ui_verbs `
  -v
```

Resultado observado: `Ran 1 test ... OK`, exit 0; el contrato de mutación exigía rojo/exit distinto
de 0.

**Fix sugerido [DESIGN]:** no deformar producción para que dos guards redundantes parezcan uno.
Cambiar el caso del mutante (a) para que el resultado no-UI lleve un eco no vacío de clave
permitida, por ejemplo `ui_request={"requested_path":"A"}`, y exigir todavía el código pelado.
Ese caso hace que retirar el gate exterior sea observable y mantiene independiente el mutante (b),
que prueba el guard de escalares de click. Si el texto literal de R-03(a) exige conservar
`ui_request={}`, entonces el criterio es incompatible con la implementación de dos gates separadas
y debe corregirse en el ledger antes de repetir la ronda.

## BACKLOG

- Pinear en `tools/tests/test_ui_error_diagnostics.py` la exclusión de una clave futura
  (`future_key`) con valor no vacío. La sonda manual R-02 pasa, pero el test entregado solo pinea
  `requested_text`; una futura sustitución de la lista blanca por iteración libre no quedaría
  cubierta por ese caso.
- Añadir una regresión automatizada de las dos rutas de `ClientRuntime` para la forma plana. En esta
  ronda `_await_result` y `probe_bridge_result` pasaron mediante sonda directa, y los call sites
  están corregidos en `tools/dayz_mcp/server.py:1564,1636`, pero los siete tests focales de error
  recorren el `Runtime` embebido, no el modo cliente.

## LO QUE NO PUDE VERIFICAR

- No ejecuté DayZ, daemon, procesos de lifecycle ni red, por frontera expresa. Por tanto no capturé
  bytes nuevos del `JsonSerializer` nativo ni validé el comportamiento contra un proceso de juego.
- La sonda de `ClientRuntime` ejercitó sus dos métodos de recepción con `_call` sintético; no fue una
  sesión FastMCP completa en modo `--client` contra daemon.
- No ejecuté toda la suite del repositorio: ejecuté exactamente los 14 módulos solicitados. Tres
  tests quedaron skipped por sus condiciones de entorno, detalladas en R-04.
- No actualicé memoria del vault ni un handoff adicional: no cambió ninguna API/decisión de proyecto
  y el único hallazgo durable de esta ronda queda contenido, con repro y arreglo sugerido, en este
  dictamen solicitado. Tampoco modifiqué el árbol vivo salvo los tres mutantes restaurados a mano.
