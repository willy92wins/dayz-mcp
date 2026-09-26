# Revisión lote C — fb-20260829-221423-b2c4

Rama observada: `work/inbox-20260830-modules` (`adc1c22`).

## VEREDICTO

**BLOQUEANTES=1.** El diagnóstico de `ui_click` sí cruza el wire en el caso cubierto, pero el diff no puede commitearse tal cual: decide si hay diagnóstico escalar por presencia de claves, mientras el resultado real es una clase plana cuyos escalares no asignados viajan como `""`/`0`/`false`. En consecuencia, errores no-UI que deben conservar el código pelado pueden convertirse en `timeout; handler='' user_id=0 clicked=False`.

## C-1 — suites exigidas

Ejecutado desde `tools/`, con `PYTHONPATH=.` y el intérprete obligatorio:

```text
C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe -m unittest tests.test_ui_error_diagnostics -v
Ran 4 tests in 0.188s
OK
FOCAL_EXIT=0
```

Los cuatro casos fueron `ok`, incluido `test_not_handled_reaches_the_client_with_its_diagnostics`.

Después ejecuté, en una sola invocación de `unittest`, los nueve módulos pedidos:

```text
python -m unittest tests.test_d09_d10_spawn_timeout_object_id \
  tests.test_boundary_values_are_pinned \
  tests.test_client_runtime_control_composition \
  tests.test_wait_for tests.test_mcp_tools \
  tests.test_weak_agent_consumer_ux tests.test_docs_truth \
  tests.test_lote_v_products tests.test_tool_registry_fingerprint
Ran 168 tests in 18.289s
OK (skipped=3)
PINNED_EXIT=0
```

Conclusión: las suites solicitadas están verdes. Esto no absuelve el bloqueante: los fixtures que pinean `timeout` omiten los escalares planos. El nuevo negativo hace lo mismo en `tools/tests/test_ui_error_diagnostics.py:58-66`; el pin preexistente también construye `{"ok": 0, "error": "timeout", "object_id": 4242}` en `tools/tests/test_d09_d10_spawn_timeout_object_id.py:50-60`.

## C-2 — consumidores de mensajes

Hice una búsqueda textual recursiva de estos códigos y de comparaciones `==`, `startswith`, `assertEqual`, `assertRegex` y regex en Python: `not_handled`, `no_handler`, `widget_not_found`, `ambiguous_widget`, `widget_ambiguous`, `text_not_writable`, `focus_not_taken`, `layout_not_found`, `layout_load_failed`, `mode_not_implemented`. También busqué `not_handled` en `playbooks/` y `tools/README-mcp.md`.

Salida literal recortada:

```text
== comparadores/asserts con codigos UI (Python) ==
tools\tests\test_ui_error_diagnostics.py:38: self.assertTrue(message.startswith("not_handled; "), message)
tools\tests\test_ui_error_diagnostics.py:65: str(server._bridge_error({"ok": 0, "error": "widget_not_found", "ui_request": {}})),
tools\tests\test_ui_enforce_contract.py:134: if body.count('"no_handler"') != 1 or body.count('"not_handled"') != 1:
== not_handled en playbooks/README ==
NO_MATCH
```

Los matches de `test_ui_enforce_contract.py` y `test_ui_click_scriptview.py` comparan texto del source Enforce, no `str(ToolError)`. No encontré un consumidor adicional que exija `message == "not_handled"` ni prefijos/regex equivalentes. El ejecutor de playbooks conserva el texto sin interpretarlo (`tools/dayz_mcp/playbook_tool.py:219-227`).

La igualdad sensible de `wait_for`, `message == "game_not_ready:reason=server_poll_stale"`, está en `tools/dayz_mcp/server.py:2503-2513`; su suite pasó y ese resultado no pertenece a los verbos UI. Conclusión: no aparece otro consumidor bloqueante por igualdad. El bloqueo encontrado está antes, en la construcción del propio mensaje.

Dos directorios `tools/native-launchers/dayz-test-v1*` devolvieron `Acceso denegado` durante `rg`; se consignan en «Lo que no pude verificar».

## C-3 — mutantes y restauración

### Mutante A: `_bridge_error_detail` devuelve siempre `""`

Se insertó temporalmente `return ""`, se ejecutó el módulo focal y se restauró a mano con `apply_patch`.

```text
test_empty_handler_is_reported_as_no_handler_ran ... FAIL
test_results_without_diagnostics_keep_the_bare_code ... ok
test_ui_fields_ride_in_the_message_after_the_code ... FAIL
test_not_handled_reaches_the_client_with_its_diagnostics ... FAIL
Ran 4 tests ... FAILED (failures=3)
EXIT_CODE=1
```

El unitario y el wire se pusieron rojos como se exigía.

### Mutante B: detalle antes del código

Se cambió temporalmente la construcción a `detail; code`, se ejecutó el módulo y se restauró a mano.

```text
test_ui_fields_ride_in_the_message_after_the_code ... FAIL
AssertionError: False is not true : handler='LFPG_SorterView_TEST' user_id=506 clicked=False; requested_path='BtnCloseX' matched_path='LFPG_Sorter/BtnCloseX'; not_handled
Ran 4 tests ... FAILED (failures=1)
EXIT_CODE=1
```

El `startswith("not_handled; ")` se puso rojo. El test de wire siguió verde porque solo comprueba inclusión de fragmentos; el mutante quedó detectado por el unitario de orden.

### Estado restaurado

Verificación final:

```text
TARGET_STATUS
 M tools/dayz_mcp/server.py
?? tools/tests/test_ui_error_diagnostics.py

TRACKED_DIFF_STAT
 tools/dayz_mcp/server.py | 46 ++++++++++++++++++++++++++++++++++++++++++----
 1 file changed, 42 insertions(+), 4 deletions(-)

UNTRACKED_TEST_STAT
 NUL => tools/tests/test_ui_error_diagnostics.py | 99 +++++++++++++++++++++++++
 1 file changed, 99 insertions(+)

server.py SHA256 = FDF8858C2E81F009CF781004DFA364F4576D14A38B0F0D557FA5E3DFDCD0D48B
test_ui_error_diagnostics.py SHA256 = D68849BA5F4B29749C423C5A2DE8C5759295B99E0AD7C95B899C214FE602C805
TARGET_PATCH_REVERSE_CHECK=0
DIFF_CHECK_EXIT=0
```

La comprobación reversa se hizo limitando `DIFF-C.patch` a los dos paths del lote. El árbol completo ya tenía 126 entradas de estado ajenas, por lo que no es posible afirmar que el `git diff --stat` global contenga solo estos dos archivos. Además, el propio `DIFF-C.patch` contiene un tercer bloque, `tools/tests/test_lote_b_products.py` (`DIFF-C.patch:173`), pese a que el brief define dos archivos; no lo apliqué ni lo revisé como parte de C.

## C-4 — wire en memoria

Ejecuté una sesión MCP cliente/servidor en memoria mediante `create_connected_server_and_client_session`, parcheando exclusivamente `runtime.state`; no inicié daemon, DayZ ni red.

```text
UI_SET_TEXT=(True, "Error executing tool ui_set_text: text_not_writable; requested_path='Cmd' requested_text='dump'")
WORLD_SPAWN=(True, 'Error executing tool world_spawn: timeout')
WIRE_PROBE=PASS
EXIT_CODE=0
```

Conclusión limitada: con exactamente los diccionarios sintéticos pedidos, ambos resultados cruzan el wire con el texto esperado y `object_id` no se concatena al mensaje.

Pero repetí `world_spawn` con la forma plana que el repositorio declara para el wire: los mismos campos más `handler=""`, `user_id=0`, `clicked=false` y `ui_request={}`. Repro literal:

```text
test_world_spawn_wire_scalar_defaults_keeps_pinned_bare_message ... FAIL
AssertionError: "Error executing tool world_spawn: timeout; handler='' user_id=0 clicked=False"
!= 'Error executing tool world_spawn: timeout'
Ran 1 test in 0.193s
FAILED (failures=1)
ACTUAL_WIRE_TEXT="Error executing tool world_spawn: timeout; handler='' user_id=0 clicked=False"
EXIT_CODE=1
```

La clase Enforce es una sola `MCPResult` plana y declara esos escalares en `addon/scripts/5_Mission/MCPMessages.c:423-479`; el bridge serializa el objeto completo en `addon/scripts/5_Mission/MCPClientBridge.c:3975-3998`. El contrato Python existente documenta que los escalares no asignados son indistinguibles de valores reales y no pueden podarse (`tools/dayz_mcp/result_prune.py:1-16,29-46,69-85`). Además, ambas rutas lanzan `_bridge_error` antes del podado: loopback en `tools/dayz_mcp/server.py:912-922` y ClientRuntime en `tools/dayz_mcp/server.py:1524-1553`.

## C-5 — superficie de fuga

Lectura verificada de `tools/dayz_mcp/server.py:647-674`: los tres escalares son `handler`, `user_id` y `clicked`; el eco se recorre con `ui_request.items()` sin lista blanca. El bridge actual define el eco como `requested_path`, `requested_root`, `requested_text` y `matched_path` (`addon/scripts/5_Mission/MCPMessages.c:415-420`) y llena las rutas desde la petición/jerarquía de widgets (`addon/scripts/5_Mission/MCPClientBridge.c:2188-2218`). No vi un campo que lea automáticamente rutas absolutas del host o tokens.

Sí hay una superficie razonable de fuga no bloqueante: `requested_text` reproduce literalmente el texto que el llamante intentó escribir, y `ui_set_text` admite cualquier `str` sin límite visible (`tools/dayz_mcp/server.py:4405-4428`). Si el llamante introduce un token o dato sensible, el error MCP lo replica; `repr` escapa caracteres, pero no redacta ni limita. También, recorrer todas las claves del eco haría visible cualquier campo sensible que se añada en el futuro.

## BLOQUEANTES

### B-01 — La presencia de escalares planos enriquece errores no-UI y rompe mensajes pineados

**Severidad concreta:** regresión de contrato/mensaje; no es crash ni corrupción. Afecta a consumidores que comparan el texto exacto y contradice el requisito explícito de conservar `timeout`, `bad_pos` y `bridge_error` pelados.

**Mecanismo:** `_bridge_error_detail` incluye cada escalar si la clave existe (`tools/dayz_mcp/server.py:647-670`). En el wire, los campos pertenecen a la clase plana `MCPResult` (`addon/scripts/5_Mission/MCPMessages.c:423-479`), y los escalares falsy no se podan por diseño (`tools/dayz_mcp/result_prune.py:9-16`). Encima, `_bridge_error` se ejecuta antes del podado (`tools/dayz_mcp/server.py:912-922,1524-1553`). Los negativos nuevos omiten las claves y por eso no representan esta forma (`tools/tests/test_ui_error_diagnostics.py:58-66`).

**Repro [EXACT]:** desde `tools/`, establecer `PYTHONPATH=.` y pasar este programa por stdin al intérprete obligatorio:

```python
import unittest
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch
from mcp.shared.memory import create_connected_server_and_client_session
from dayz_mcp.server import ServerConfig, build_app

class WireShapeRegression(unittest.IsolatedAsyncioTestCase):
    async def test_world_spawn_wire_scalar_defaults_keeps_bare_message(self):
        app, runtime = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        wire_result = {
            "ok": 0,
            "error": "timeout",
            "object_id": 7,
            "handler": "",
            "user_id": 0,
            "clicked": False,
            "ui_request": {},
        }
        fake_state = SimpleNamespace(
            enqueue_command=lambda *a, **k: (200, {"id": 42}),
            take_result=lambda command_id, remove=False: dict(wire_result),
            abandon_command=lambda *a, **k: None,
        )
        with patch.object(runtime, "ensure_peer_allowed", return_value=None), patch.object(
            type(runtime), "state", new_callable=PropertyMock, return_value=fake_state
        ):
            async with create_connected_server_and_client_session(app._mcp_server) as session:
                result = await session.call_tool(
                    "world_spawn",
                    {"type": "SurvivorM_Mirek", "pos": [7500.0, 0.0, 7500.0]},
                )
        text = " ".join(getattr(block, "text", "") for block in result.content)
        self.assertEqual(text, "Error executing tool world_spawn: timeout")

unittest.main(verbosity=2)
```

Resultado observado:

```text
FAIL
actual:   Error executing tool world_spawn: timeout; handler='' user_id=0 clicked=False
esperado: Error executing tool world_spawn: timeout
```

**Fix sugerido:** dejar de usar mera presencia de clave como discriminador de esos tres escalares. Acotar `handler`/`user_id`/`clicked` al comando `ui_click` (pasando `cmd` a la construcción del error) o, como mínimo, a sus errores terminales `no_handler`/`not_handled`; conservar el eco no vacío para los demás verbos UI. Añadir un negativo con todos los escalares falsy de la clase plana y otro equivalente por ClientRuntime antes de aceptar el cambio.

## BACKLOG

- Aplicar una lista blanca explícita a las claves de `ui_request`; ahora `tools/dayz_mcp/server.py:667-673` publica cualquier ampliación futura del objeto.
- Decidir y documentar una política para `requested_text`: omitirlo, truncarlo y/o redactarlo en errores. Hoy puede duplicar texto sensible aportado por el llamante y generar mensajes sin cota práctica.
- Endurecer el test de wire del orden: `tools/tests/test_ui_error_diagnostics.py:90-95` solo comprueba inclusión; el mutante `detail; code` lo superó y fue detectado únicamente por el unitario.
- Limpiar el artefacto de entrega: `DIFF-C.patch` incluye el bloque ajeno de lote B en `DIFF-C.patch:173`.

## LO QUE NO PUDE VERIFICAR

- No ejecuté DayZ, el daemon ni red, por frontera expresa. Por tanto no capturé bytes nuevos del `JsonSerializer` nativo; para la forma del resultado usé el contrato ya codificado en `result_prune.py`, la declaración real de `MCPResult` y una sesión MCP en memoria con esa forma.
- No pude leer dos directorios bajo `tools/native-launchers/dayz-test-v1*`: `rg` devolvió `Acceso denegado`. No aparecieron como consumidores Python/Markdown accesibles, pero no puedo afirmar que su contenido opaco carezca de la cadena.
- No ejecutué toda la suite del repositorio; ejecutué exactamente el módulo nuevo y los nueve módulos de regresión solicitados. Tres pruebas de la segunda invocación quedaron `skipped` por sus condiciones de entorno.
- No pude certificar que el worktree global contenga solo los dos cambios del lote: ya tenía 126 entradas de estado. Sí certifiqué hashes, `diff --check`, estado y aplicabilidad reversa del parche para los dos paths objetivo después de restaurar los mutantes.
