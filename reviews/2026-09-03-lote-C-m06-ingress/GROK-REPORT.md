# M06-UI-INGRESS — ingress UI abierto al contrato

Los cuatro verbos UI aceptan los campos del contrato (`root` opcional en todos; `mode` y `bubble` solo en `ui_click`). El fail-closed de claves no declaradas no se tocó. Oráculo `UI_INGRESS_SCHEMA_OK` (exit 0). Unittest de los dos ficheros del módulo: `OK` (87 tests).

## Qué cambió y por qué

En `tools/dayz_mcp/loopback.py` declaré los campos en las variantes existentes de `_COMMAND_ARG_SCHEMAS`. No relajé `_matches_schema_variant`: una clave no nombrada sigue siendo `bad_args`.

- `ui_tree` (`tools/dayz_mcp/loopback.py:636-645`): `root` opcional, validado con `_is_non_empty_string`. `path` y `limit` se quedan como estaban.
- `ui_set_text` (`tools/dayz_mcp/loopback.py:646-656`): `root` opcional, misma validación. `path` y `text` siguen siendo required.
- `ui_click` (`tools/dayz_mcp/loopback.py:657-669`): `mode`, `bubble` y `root` opcionales. `mode` = `_one_of("direct", "complete")`. `bubble` = `_is_strict_bool` (ni `"true"` ni `1`). `root` = `_is_non_empty_string`. `button` 0–2 no cambia.
- `ui_focus` (`tools/dayz_mcp/loopback.py:700-709`): `root` opcional, `_is_non_empty_string`. `path` sigue required.

`mode` y `bubble` no se añadieron a `ui_tree`, `ui_set_text` ni `ui_focus`, así que un leak de esos campos sigue siendo `bad_args`.

Tests: expectativas escritas a mano, no leídas de `_COMMAND_ARG_SCHEMAS`.

- `tools/tests/test_validate_command_args_table.py:352-364` (`ui_tree`), `:365-395` (`ui_set_text`), `:396-448` (`ui_click`), `:469-483` (`ui_focus`): aceptación de `root`/`mode`/`bubble` y rechazo de valores ilegales, claves extra y leaks entre verbos.
- `tools/tests/test_loopback.py:1674-1742` (`UiIngressSchemaTest`): 18 payloads que el contrato acepta y 20 que debe rechazar, independientes de la tabla.

## Salida del oráculo

Comando:

```
"C:/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev/tools/.venv-mcp/Scripts/python.exe" gate/oracle_ui_schema.py
```

Salida literal (exit 0):

```
casos: 18 aceptar / 15 rechazar / 0 incumplidos
UI_INGRESS_SCHEMA_OK
```

## Salida de los tests

Comando (cwd `tools/`):

```
"C:/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev/tools/.venv-mcp/Scripts/python.exe" -m unittest tests.test_loopback tests.test_validate_command_args_table
```

Salida literal (exit 0):

```
.......................................................................................
----------------------------------------------------------------------
Ran 87 tests in 0.877s

OK
```

(El recuento pasa de 85 a 87 porque `UiIngressSchemaTest` añade dos métodos. Los casos extra de la tabla viven dentro de los `test_ui_*` que ya existían.)

## LO QUE NO PUDE VERIFICAR

- No corrí el resto de `tools/tests/` (p. ej. `test_command_validation_coverage`, `test_ui_enforce_contract`, `test_bad_args_messages`). El encargo mide solo estos dos ficheros.
- No hay juego ni MCP en esta sesión: no comprobé que Enforce resuelva `root`/`mode`/`bubble` en runtime. Leí `addon/scripts/5_Mission/MCPMessages.c:63,70-71` (los campos existen en el struct) y no ejecuté el puente.
- No rehasheé ni toqué `gate/`. No verifiqué el hash sellado; el oráculo lo corrí en modo lectura.
- No comprobé `ui_reload_layout` con `root`/`bubble` (no es uno de los cuatro verbos del contrato). El rechazo de claves extra en ese verbo sigue cubierto por el caso `extra_key` que ya estaba.
