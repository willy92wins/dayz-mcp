## HECHO

Ronda 2: `root=null` explícito se rechaza ANTES de encolar en las cuatro tools. El tipo publicado deja de ser `str | None` (schema `anyOf[string,null]`, FastMCP colapsa null y omisión en el mismo `None`) y pasa a `root: str = None` (schema `type:string`, Pydantic corta el null). Las guardas de cuerpo no se tocaron.

- `C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\938664ff-361c-4575-aa56-c7c06c7ca2a8\scratchpad\lote-F\ws\tools\dayz_mcp\server.py:4195` — `ui_tree(..., root: str = None)` con comentario de por qué no `str | None`.
- `...\server.py:4222` — `ui_set_text`, misma anotación.
- `...\server.py:4247` — `ui_click`, misma anotación.
- `...\server.py:4325` — `ui_focus`, misma anotación.
- Guardas intactas: `if root is not None and (not isinstance(root, str) or root == "")` en `server.py:4202`, `:4229`, `:4258`, `:4331`; `if root is not None: args["root"] = root` igual.
- Tests nuevos: `...\tools\tests\test_mcp_tools.py:986-1009` (uno por tool).

## GATE

`bash gate/run.sh`:

```
========================================================================================================
[PASS ] F1-ui_tree el schema registrado declara root
          properties=['limit', 'path', 'root', 'timeout_s']
[PASS ] F1-ui_set_text el schema registrado declara root
          properties=['path', 'root', 'text', 'timeout_s']
[PASS ] F1-ui_click el schema registrado declara root
          properties=['bubble', 'button', 'mode', 'path', 'root', 'timeout_s']
[PASS ] F1-ui_focus el schema registrado declara root
          properties=['path', 'root', 'timeout_s']
[PASS ] F1-ui_click el schema registrado declara mode
          properties=['bubble', 'button', 'mode', 'path', 'root', 'timeout_s']
[PASS ] F1-ui_click el schema registrado declara bubble
          properties=['bubble', 'button', 'mode', 'path', 'root', 'timeout_s']
[PASS ] F2-ui_tree root llega al puente con su valor
          args={'limit': 256, 'path': 'Btn', 'root': 'MiRoot'} err=
[PASS ] F2-ui_set_text root llega al puente con su valor
          args={'path': 'Btn', 'text': 'x', 'root': 'MiRoot'} err=
[PASS ] F2-ui_click root llega al puente con su valor
          args={'path': 'Btn', 'button': 0, 'mode': 'direct', 'bubble': False, 'root': 'MiRoot'} err=
[PASS ] F2-ui_focus root llega al puente con su valor
          args={'path': 'Btn', 'root': 'MiRoot'} err=
[PASS ] F3-ui_tree sin root, root NO viaja
          args={'limit': 256, 'path': 'Btn'} err=
[PASS ] F3-ui_set_text sin root, root NO viaja
          args={'path': 'Btn', 'text': 'x'} err=
[PASS ] F3-ui_click sin root, root NO viaja
          args={'path': 'Btn', 'button': 0, 'mode': 'direct', 'bubble': False} err=
[PASS ] F3-ui_focus sin root, root NO viaja
          args={'path': 'Btn'} err=
[PASS ] F4 ui_click transmite mode y bubble incluso por defecto
          args={'path': 'Btn', 'button': 0, 'mode': 'direct', 'bubble': False} err=
[PASS ] F4b ui_click transmite los valores que le pasan
          args={'path': 'Btn', 'button': 0, 'mode': 'complete', 'bubble': True} err=
[PASS ] F5-mode-desconocido se rechaza ANTES de encolar
          llego_al_puente=False err=ToolError: Error executing tool ui_click: bad_args: mode 'otro' must be one of 'direct' or
[PASS ] F5-mode-no-str se rechaza ANTES de encolar
          llego_al_puente=False err=ToolError: Error executing tool ui_click: 1 validation error for ui_clickArguments
mode
[PASS ] F5-bubble-no-bool se rechaza ANTES de encolar
          llego_al_puente=False err=ToolError: Error executing tool ui_click: 1 validation error for ui_clickArguments
bubble
[PASS ] F5-bubble-entero se rechaza ANTES de encolar
          llego_al_puente=False err=ToolError: Error executing tool ui_click: 1 validation error for ui_clickArguments
bubble
[PASS ] F5-root-vacio se rechaza ANTES de encolar
          llego_al_puente=False err=ToolError: Error executing tool ui_click: bad_args: root '' must be a non-empty string
[PASS ] F5-root-no-str se rechaza ANTES de encolar
          llego_al_puente=False err=ToolError: Error executing tool ui_focus: 1 validation error for ui_focusArguments
root
[PASS ] F5b-ui_tree root=null explicito se rechaza ANTES de encolar
          llego_al_puente=False args=None err=ToolError: Error executing tool ui_tree: 1 validation error for ui_treeArguments
[PASS ] F5b-ui_set_text root=null explicito se rechaza ANTES de encolar
          llego_al_puente=False args=None err=ToolError: Error executing tool ui_set_text: 1 validation error for ui_set_textA
[PASS ] F5b-ui_click root=null explicito se rechaza ANTES de encolar
          llego_al_puente=False args=None err=ToolError: Error executing tool ui_click: 1 validation error for ui_clickArgumen
[PASS ] F5b-ui_focus root=null explicito se rechaza ANTES de encolar
          llego_al_puente=False args=None err=ToolError: Error executing tool ui_focus: 1 validation error for ui_focusArgumen
[PASS ] F6-path-vacio sigue rechazandose
          llego_al_puente=False err=ToolError: Error executing tool ui_click: bad_args: path '' must be a non-empty string
[PASS ] F6-button-fuera-de-rango sigue rechazandose
          llego_al_puente=False err=ToolError: Error executing tool ui_click: bad_args: button 9 must be an int from 0 to 2
========================================================================================================
ORACULO: PASS=28 FAIL=0 UNMET=0 de 28
ORACULO-VERDE
```

`bash gate/suite.sh`:

```
test_mcp_tools : Ran 45 tests in 7.764s  OK
test_loopback : Ran 64 tests in 0.836s  OK
test_ui_enforce_contract : Ran 15 tests in 0.017s  OK
test_validate_command_args_table : Ran 24 tests in 0.001s  OK
test_client_mode : Ran 53 tests in 5.973s  OK
SUITE-ACOTADA OK
```

## CONTROL POSITIVO

Corridos ANTES del cambio de firmas (`cd tools && PYTHONPATH=. <venv> -m unittest` de los cuatro tests nuevos). `Ran 4 tests in 0.324s FAILED (failures=4)`.

- `test_ui_tree_rejects_explicit_null_root_before_enqueue`: `AssertionError: {'limit': 256, 'path': 'Btn'} is not None`
- `test_ui_set_text_rejects_explicit_null_root_before_enqueue`: `AssertionError: {'path': 'Btn', 'text': 'x'} is not None`
- `test_ui_click_rejects_explicit_null_root_before_enqueue`: `AssertionError: {'path': 'Btn', 'button': 0, 'mode': 'direct', 'bubble': False} is not None`
- `test_ui_focus_rejects_explicit_null_root_before_enqueue`: `AssertionError: {'path': 'Btn'} is not None`

Tras `root: str = None`: los cuatro `ok`; `UiPublicSurfaceTest` 12/12 OK.

## LO QUE NO PUDE VERIFICAR

- No corrí la suite completa (2309 tests); el brief lo prohíbe.
- No hay juego, PBO ni red: no vi `ResolveUiRoot` in-game.
- El rechazo de `root=null` lo corta Pydantic (`1 validation error for …Arguments`) antes del cuerpo. No llamé las funciones saltándome FastMCP con `root=None`: la guarda `if root is not None` no dispararía en ese caso, y no está diseñada para ello (cubre `root=""` y llamadas directas con tipo malo).
- No comprobé el sello hash de `gate/`.

## DISPUTAS

Ninguna.
