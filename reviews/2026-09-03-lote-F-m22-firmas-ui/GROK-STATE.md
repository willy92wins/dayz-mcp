## HECHO

Cableado de la superficie pública de UI en `tools/dayz_mcp/server.py`. Las cuatro tools aceptan `root` opcional (ausente no viaja; presente y no vacío sí). `ui_click` declara y transmite siempre `mode` y `bubble`.

- `tools/dayz_mcp/server.py:22` — import de `StrictBool` para que `bubble=1` no se coaccione a `True`.
- `tools/dayz_mcp/server.py:4189-4207` — `ui_tree(..., root: str | None = None)`: valida cadena no vacía si viene; solo entonces entra en `args`.
- `tools/dayz_mcp/server.py:4213-4229` — `ui_set_text(..., root: str | None = None)`, misma regla de presencia.
- `tools/dayz_mcp/server.py:4235-4262` — `ui_click(..., root=None, mode="direct", bubble: StrictBool = False)`: `mode` ∈ {`direct`,`complete`}, `bubble` bool estricto, `mode`/`bubble` siempre en `args`, `root` solo si viene.
- `tools/dayz_mcp/server.py:4311-4326` — `ui_focus(..., root: str | None = None)`, misma regla de presencia.
- Guardas previas intactas: `path` no vacío (`:4243-4244`, `:4316-4317`) y `button` entero 0..2 (`:4245-4246`).
- Tests: `tools/tests/test_mcp_tools.py:891-995` (`UiPublicSurfaceTest`).

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
[PASS ] F6-path-vacio sigue rechazandose
          llego_al_puente=False err=ToolError: Error executing tool ui_click: bad_args: path '' must be a non-empty string
[PASS ] F6-button-fuera-de-rango sigue rechazandose
          llego_al_puente=False err=ToolError: Error executing tool ui_click: bad_args: button 9 must be an int from 0 to 2
========================================================================================================
ORACULO: PASS=24 FAIL=0 UNMET=0 de 24
ORACULO-VERDE
```

`bash gate/suite.sh`:

```
test_mcp_tools : Ran 41 tests in 7.468s  OK
test_loopback : Ran 64 tests in 0.898s  OK
test_ui_enforce_contract : Ran 15 tests in 0.008s  OK
test_validate_command_args_table : Ran 24 tests in 0.001s  OK
test_client_mode : Ran 53 tests in 5.994s  OK
SUITE-ACOTADA OK
```

## CONTROL POSITIVO

Corridos ANTES del cambio (`cd tools && PYTHONPATH=. <venv> -m unittest tests.test_mcp_tools.UiPublicSurfaceTest -v`). `Ran 8 tests in 1.667s FAILED (failures=19)`.

- `test_registered_schema_declares_root_on_all_four_ui_tools` [ui_tree]: `AssertionError: 'root' not found in {'path': {'default': '', 'title': 'Path', 'type': 'string'}, 'limit': {'default': 256, 'title': 'Limit', 'type': 'integer'}, 'timeout_s': {'default': 15.0, 'title': 'Timeout S', 'type': 'number'}}`
- mismo test [ui_click]: `AssertionError: 'root' not found in {'path': {'title': 'Path', 'type': 'string'}, 'button': {'default': 0, 'title': 'Button', 'type': 'integer'}, 'timeout_s': {'default': 15.0, 'title': 'Timeout S', 'type': 'number'}}`
- `test_registered_schema_declares_mode_and_bubble_on_ui_click`: FAIL (schema de `ui_click` sin `mode` ni `bubble`; mismas properties que la línea anterior).
- `test_root_reaches_the_bridge_when_the_caller_sends_it` [ui_tree/ui_set_text/ui_click/ui_focus]: `AssertionError: None != 'MiRoot'`
- `test_ui_click_always_sends_default_mode_and_bubble`: `AssertionError: None != 'direct'`
- `test_ui_click_forwards_complete_mode_and_true_bubble`: `AssertionError: None != 'complete'`
- `test_fail_closed_rejects_before_enqueue` (`mode='otro'`): `AssertionError: {'path': 'Btn', 'button': 0} is not None`
- mismos subtests `mode=3`, `bubble='true'`, `bubble=1`, `root=''`, `root=7`: FastMCP descartaba el campo y encolaba `{path, button}`.

Dos tests nuevos ya eran verdes ANTES (preservación, no control positivo del cableado):

- `test_root_does_not_travel_when_omitted` — `ok` (F3 ya cumplía).
- `test_existing_path_and_button_guards_still_reject` — `ok` (F6 ya cumplía).

Tras el cambio: `UiPublicSurfaceTest` 8/8 OK; `tests.test_mcp_tools` `Ran 41 tests in 7.512s OK`.

## LO QUE NO PUDE VERIFICAR

- No corrí la suite completa (2309 tests); el brief lo prohíbe.
- No hay juego, PBO ni red: no vi `ResolveUiRoot` ni el rechazo `mode_not_implemented` del bridge con `mode="complete"`.
- Por `app.call_tool`, los type-mismatch `mode=3`, `bubble="true"`, `bubble=1` y `root=7` los corta FastMCP/Pydantic (`1 validation error for …Arguments`) antes de llegar a los `isinstance` del cuerpo. El oráculo los da PASS porque no encolan. No ejecuté las funciones saltándome FastMCP para ver esos `isinstance` disparar solos.
- No comprobé el sello hash de `gate/` (el brief dice que lo comprueba quien recibe).

## DISPUTAS

Ninguna.
