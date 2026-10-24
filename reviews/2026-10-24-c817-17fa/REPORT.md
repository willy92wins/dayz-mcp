# Informe c817 + 17fa

Worktree `C:\Users\guill\AppData\Local\Temp\dzmcp-c817-17fa`, rama `claude/buzon-c817-17fa-adminlog-inventory-move`, base `origin/main` `f8cf3b43`. Sin push y sin lanzar DayZ.

## c817 — `server_adminlog`

`dayz_test_run(..., server_adminlog: StrictBool = False)` (`tools/dayz_mcp/server.py`, parámetro tras `navmesh_data_server`).

- El campo entra en la petición sellada v1 como opcional, igual que `navmesh_data_server`: un documento viejo que lo omite sigue siendo canónico y equivale a `false`.
- `true` con `mode=server` o `mode=all` añade el argv literal `-adminlog` solo en el proceso servidor, al lado de `-startNavmeshDataServer`. Omitido o `false` no añade el flag.
- `true` con `mode=client` o `mode=offline` es `bad_dayz_test_request:server_adminlog_requires_server` y no encola el lanzamiento. `mode=all` sí se admite. `pack_only` guarda el campo y no arranca proceso.
- Valores que no son bool estricto (`None`, `0`, `1`, `"true"`) son `flag_not_boolean`.
- `logs_since` y `wait_for(log_matches)` pasan por `_current_launch_logs` → `resolve_log_files`. `LOG_SUFFIXES` es `(".rpt", ".log", ".adm")`. El nombre no se fija. Siguen fuera los que empiezan por `crash`. Con `start_epoch`, un `.adm` anterior al lanzamiento no entra.

## 17fa — `inventory_move`

```
inventory_move(
    item_object_id: StrictInt,          # >= 1
    op: StrictStr,                      # to_cargo | to_attachment | to_hands | to_inventory | combine | split_half | split_to_hands
    target_object_id: StrictInt = 0,    # >= 1 si op es to_cargo, to_attachment o combine
    slot: StrictStr = "",               # obligatorio y no vacío si op es to_attachment
    authority: StrictStr = "client",    # client | server
    uid: StrictStr = "",
    timeout_s: StrictFloat = DEFAULT_TOOL_TIMEOUT_S,
) -> dict
```

Comando de servidor, con lease, fuera de la whitelist de cliente. `MCP_BRIDGE_VERSION` sigue en `"12"`. No está en `SERVER_ARG_CONTRACT`.

Respuesta: `accepted` (la llamada se emitió), `confirmed` (solo si la lectura del servidor casa con la operación), `error`, y `inventory_move` con `op`, `authority`, `loc_type`, `parent_object_id`, `parent_type`, `slot`, `quantity`. Un timeout deja `accepted=true`, `confirmed=false`, `error="timeout"` y la última ubicación leída. Id desconocido: `object_not_found`. Fuera de alcance: `not_reachable`. Sin sitio: `no_room`. También `slot_not_found`, `slot_occupied`, `not_an_item`, `inventory_unavailable`, `not_networked`, `no_identity`, `rpc_failed`, `input_busy`, `not_client`, `bad_args`.

`authority=client` (multijugador): ScriptRPC `MCP_RPC_INVENTORY_MOVE = 78541071` hacia el cliente dueño, que llama `PredictiveTakeEntityToTargetCargo`, `PredictiveTakeEntityToTargetAttachmentEx`, `PredictiveTakeEntityToHands`, `PredictiveTakeEntityToInventory(FindInventoryLocationType.ANY, item)`, `CombineItemsClient`, `OnRightClick` o `SplitIntoStackMaxHandsClient`. La confirmación es un job de 5 s y una lectura de `InventoryLocation` en el servidor. `accepted` no pone `confirmed`.

`authority=server`: en el mismo tick, `GameInventory.LocationSyncMoveEntity` (cargo y attachment), `ServerTakeEntityToHands`, `ServerTakeEntityToInventory`, `CombineItems`, `SplitItem` o `SplitIntoStackMaxHands`. Sin comando de jugador. No se usa `HasInventoryJuncture`. No hay `modded class` de `DayZPlayerInventory`, `Magazine` ni `Transport`.

El alcance de cliente es `UAMaxDistances.DEFAULT`, o que la raíz de jerarquía sea el jugador (un ítem ya en su inventario pasa). `hands_take` rechaza cargo antes del alcance; este verbo no.

## Supuestos

- [ASSUMPTION] Con `-adminlog`, DayZDiag escribe el `.ADM` en el directorio `-profiles` del servidor. El descubrimiento lista cualquier `*.adm` de ese perfil que pase el suelo de mtime. No hay un nombre medido.
- [ASSUMPTION] `confirmed=true` solo sale de `MCPWriteInventoryMove` cuando la ubicación leída en el servidor cumple la operación. El RPC del cliente no confirma.

## Ficheros

c817: `tools/dayz_mcp/dayz_test_request.py`, `dayz_test_tool.py`, `dayz_test_worker.py`, `launch_logs.py`, `log_tail.py`, `server.py` (parámetro y texto de logs), `tools/packaged-modules.lock.json`, `tools/tests/test_server_adminlog.py`, `test_dayz_test_request.py`, `test_dayz_test_worker.py`, `test_g5fix_named_profiles.py`, `test_instance_identity_97de.py`, `test_launch_contract_c561_31d2_2837.py`.

17fa: `addon/scripts/4_World/MCP_InventoryMove.c` (nuevo), `addon/scripts/5_Mission/MCPBridge.c`, `MCPMessages.c`, `tools/dayz_mcp/loopback.py`, `bridge_readiness.py`, `result_prune.py`, `server.py` (tool), `tools/tests/fixtures/bridge_capabilities_v1.json`, `test_bridge_server_capabilities.py`, `test_command_validation_coverage.py`, `test_lote2_numeric_boundary.py` (193 filas), `test_ingress_schema_coherence.py`, `test_validate_command_args_table.py`, `test_result_prune.py`, `test_owned_scalar_census.py` (pasó sin cambio: el write de `confirmed` se atribuye por la llamada), `test_player_move.py`, `test_player_trace.py` (el sufijo de `PRUNABLE_FIELDS` incluye `inventory_move`).

Docs: `README.md` (89 tools y el párrafo de logs), `CLAUDE.md`, `dayz-mcp-architecture.md`, `tools/README-mcp.md`, `CHANGELOG.md`, `PROJECT-MAP.md` (`CHANGELOG.md` 73 KB).

`server.py`, `README.md`, `CHANGELOG.md` y `tools/README-mcp.md` mezclan los dos tickets; van en el commit de `inventory_move` porque el parámetro público y el verbo nuevo viven en el mismo `server.py`. El commit c817 lleva el sellado, el argv, el descubrimiento y el lock.

## Tests

Intérprete: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe`. Cwd: `C:\Users\guill\AppData\Local\Temp\dzmcp-c817-17fa\tools`.

```
cmd /c 'cd /d C:\Users\guill\AppData\Local\Temp\dzmcp-c817-17fa\tools && "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -m unittest discover -s tests -t .'
```

Segunda corrida, tras corregir el sufijo de `PRUNABLE_FIELDS` y el tamaño de `CHANGELOG.md` en `PROJECT-MAP.md`:

```
======================================================================
FAIL: test_a_daemon_started_by_another_venv_exits_with_one_line (tests.test_fb_acf3_88c2_8575_4fdf_named_causes.DaemonPythonNotApprovedTest.test_a_daemon_started_by_another_venv_exits_with_one_line)
----------------------------------------------------------------------
AssertionError: 'c:\\users\\guill\\appdata\\local\\temp\\dzmcp-c817-17fa\\tools\\.venv-mcp\\scripts\\python.exe' not found in 'daemon: daemon_python_not_approved: this interpreter is c:\\users\\guill\\appdata\\local\\temp\\tmp6yc6nywc\\clone\\.venv-clone\\scripts\\python.exe, but the daemon runs only under c:\\users\\guill\\onedrive\\documentos\\dayz projects\\dayz_mcp_dev\\tools\\.venv-mcp\\scripts\\python.exe, ...'

----------------------------------------------------------------------
Ran 6361 tests in 801.614s

FAILED (failures=1, skipped=46)
```

Ese fallo no viene del diff. El hijo importa `dayz_mcp` del editable del venv vivo (`DayZ_MCP_dev`), así que el path aprobado es ese venv y no `tools\.venv-mcp` de este worktree. El worktree no tiene venv y el encargo prohíbe instalar paquetes. La primera corrida tuvo además tres fallos de este diff (sufijo de `PRUNABLE_FIELDS` en `test_player_move` y `test_player_trace`, y `CHANGELOG.md` a 73.2 KiB contra un "72 KB"); esos tres quedaron en verde en la segunda corrida (`Ran 3 tests in 0.002s OK`).

## Linter

```
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" C:\Users\guill\DayZ-Modding-Knowledge-Pack\tools\dayz-script-validator\scripts\script_validator.py C:\Users\guill\AppData\Local\Temp\dzmcp-c817-17fa\addon
```

Salida: `status=FAIL`, exit 1, `files_scanned=23` (base 22: el archivo nuevo `MCP_InventoryMove.c`), 4 errors, 7 warnings. Los 4 errors son los de la base, mismos fichero y línea:

- `ES-LOCAL-VAR-REDECLARE` `scripts\5_Mission\MCP_ActionCursor.c` líneas 163 y 234 (`row`)
- `ES-LAYOUT-PATH-PBOPREFIX-MISMATCH` `scripts\5_Mission\MCPDialogController.c` línea 39
- `ES-LAYOUT-FILE-MISSING` `scripts\5_Mission\MCPDialogController.c` línea 40

Warnings: 1 `ES-EMPTY-IFDEF-UNSUPPORTED-PATTERN` en `MCP_ActionCursor.c:65` y 6 `ES-GETTYPE-EXACT-MATCH` (`MCPBridge.c` 2135, 3521, 4214; `MCPClientBridge.c` 3940, 4239, 5245). Ninguno en `MCP_InventoryMove.c`. El recuento de warnings no sube. No comparé las líneas de warning contra `origin/main` fichero a fichero: la inserción en `MCPBridge.c` las mueve.

## DISPUTAS

- El NEG del ticket nombra `mode=client`. También rechazo `mode=offline`: no hay proceso servidor al que pasar `-adminlog`.
- El estado público de la run no lleva el argv. `logs_since` no puede ocultar un `.adm` "porque no pediste -adminlog". Lista el que exista en el perfil con el mismo suelo de mtime que el RPT. Sin el flag, DayZ no crea el fichero, y un `.adm` de una run anterior cae por `start_epoch`.
- Errores tipados de más, no pedidos en el NEG: `slot_not_found`, `slot_occupied`, `not_an_item`. Un destino lleno o un slot ocupado no mueven el ítem.
- `to_inventory` en servidor usa `ServerTakeEntityToInventory` (bool). `to_hands` en servidor es `ServerTakeEntityToHands` (void): el "no cabe" no se ve hasta la lectura del job.

## LO QUE NO PUDE VERIFICAR

Todo lo que necesita el juego. En concreto: que `-adminlog` cree un `.ADM` en `-profiles`; que `wait_for` case una línea AdminLog y una línea vanilla de conexión; que cada `Predictive*` , `CombineItemsClient`, `OnRightClick` y `SplitIntoStackMaxHandsClient` muevan de verdad; que `LocationSyncMoveEntity` mueva entre cajas sin jugador; que `not_reachable` y `no_room` dejen el ítem quieto; que `typename.EnumToString(InventoryLocationType, loc.GetType())` compile; que `FindFreeLocationFor` no devuelva `no_room` cuando el ítem ya está en ese cargo; el PBO y el bridge en una sesión real. El linter no compila Enforce.

## Ronda 2

La disputa de la ronda 1 sobre listar un `.adm` sin `start_epoch` queda cerrada: sin timestamp no entra ningún `.adm`.

1. P2 daemon. `validate_command_args` llama a `_inventory_move_op_rules` (`tools/dayz_mcp/loopback.py:1902` y `:1925`). `to_cargo`, `to_attachment` y `combine` exigen `target_object_id` entero positivo. `to_attachment` exige `slot` no vacío. La tool pública ya lo hacía (`server.py` junto a `inventory_move`). Tests: `tools/tests/test_inventory_move_contract.py` (daemon y `call_tool`, aceptados y rechazados, el bridge no se espera en el rechazo) y filas nuevas en `test_validate_command_args_table.py`.

2. P2 inventario ajeno. `MCPInventoryMoveForeign` (`MCPBridge.c:984`) corre en el dispatch antes del RPC (`:812`), para el ítem y el destino, en los dos authority. El cliente lo repite en `MCPInventoryMoveOwnedByOther` (`MCP_InventoryMove.c:73`) antes de la llamada predictiva (`:119`). Otro `GetHierarchyRootPlayer` es `not_reachable`. Solo Enforce: sin test de ejecución.

3. P1 munición. `MCPItemQuantity` (`MCPBridge.c:1132`) usa `Magazine.GetAmmoCount()` y `GetQuantity()` en el resto. Esa función alimenta el antes/después de combine y split y la cantidad de la respuesta. No hay contraparte Python: el censo de cantidad vive en Enforce. No añadí un doble en Python.

4. P1 `to_inventory` y HANDS. Decisión: no quito HANDS de los flags. `FindInventoryLocationType.ANY` sigue en el servidor (`ServerTakeEntityToInventory`), en el cliente (`PredictiveTakeEntityToInventory`) y en la prevalidación (`MCPBridge.c:1114`). La confirmación (`MCPBridge.c:1355`) acepta `HANDS` cuando el padre es el jugador del job. `GROUND` sigue sin confirmar. Documentado en la descripción de la tool (`server.py`) y en `README.md`.

5. P1 `no_room` de `to_inventory`. El servidor prevalidaba cargo y attachment, no inventario. Ahora `FindFreeLocationFor` con `ANY` va antes del RPC (`MCPBridge.c:1114`) y devuelve `no_room` sin `accepted`. El RPC `MCP_RPC_INVENTORY_MOVE` es solo servidor→cliente (`rpc.Send` hacia la identidad; `OnRPC` solo en cliente). Un rechazo del cliente se imprime y no vuelve correlacionado: no hay canal de vuelta en ese RPC. No abrí uno nuevo.

6. P1 `.adm` de otra run. `_newest_rpt_and_script` (`launch_logs.py:75`) ya no mira `.adm`. `_current_launch_logs` (`:113`) sin `start_epoch` devuelve solo el `.rpt` y el `.log` más nuevos. Con `start_epoch`, el `.adm` entra si su mtime pasa el suelo `start_epoch - 2`, igual que el resto, y `crash*` sigue fuera. `logs_since` usa esa función (`server.py:6076`). Tests en `test_server_adminlog.py`: sin epoch no hay `.adm` aunque sea el más nuevo; con epoch entra el de la run y no el anterior; `crash_*.adm` y `crash_*.log` fuera; el `.rpt` viejo no sustituye al actual.

### Gates ronda 2

Módulos tocados, 11 tests, 0.283 s, OK:

```
cmd /c 'cd /d C:\Users\guill\AppData\Local\Temp\dzmcp-c817-17fa\tools && "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -m unittest tests.test_inventory_move_contract tests.test_server_adminlog tests.test_validate_command_args_table.ValidateCommandArgsTableTest.test_inventory_move tests.test_docs_truth.ProjectMapSizesDocsTest -v'
----------------------------------------------------------------------
Ran 11 tests in 0.283s

OK
```

Suite completa:

```
cmd /c 'cd /d C:\Users\guill\AppData\Local\Temp\dzmcp-c817-17fa\tools && "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -m unittest discover -s tests -t .'
======================================================================
FAIL: test_a_daemon_started_by_another_venv_exits_with_one_line (tests.test_fb_acf3_88c2_8575_4fdf_named_causes.DaemonPythonNotApprovedTest.test_a_daemon_started_by_another_venv_exits_with_one_line)
AssertionError: 'c:\\users\\guill\\appdata\\local\\temp\\dzmcp-c817-17fa\\tools\\.venv-mcp\\scripts\\python.exe' not found in 'daemon: daemon_python_not_approved: ... dayz_mcp_dev\\tools\\.venv-mcp\\scripts\\python.exe ...'
----------------------------------------------------------------------
Ran 6363 tests in 807.044s

FAILED (failures=1, skipped=46)
```

Ese fallo es el único. Es el del intérprete del árbol vivo, no de este diff.

Linter (exit 1, 23 ficheros, 4 errors, 7 warnings). Los 4 errors son los de la base, mismas líneas: `MCP_ActionCursor.c` 163 y 234, `MCPDialogController.c` 39 y 40. Ningún error nuevo. Warnings: `MCP_ActionCursor.c:65` y `ES-GETTYPE-EXACT-MATCH` en `MCPBridge.c` 2189, 3575, 4268 y `MCPClientBridge.c` 3940, 4239, 5245.

### DISPUTAS ronda 2

- El rechazo del cliente no vuelve. El canal existente no lo permite. Queda la prevalidación del servidor.
- `to_inventory` confirma manos del jugador seleccionado porque `ANY` las incluye. No excluí HANDS de los flags.

### LO QUE NO PUDE VERIFICAR ronda 2

En juego: que `GetAmmoCount` confirme un combine o un split de munición; que `FindFreeLocationFor(..., ANY)` rechace un inventario lleno antes del RPC; que el ítem de otro jugador no se mueva; que un `to_inventory` que cae en manos confirme; que un `.adm` real aparezca solo con `start_epoch`.

## Ronda 3

La disputa de la ronda 2 («el canal no permite devolver el rechazo») queda cerrada: el cliente responde con `MCP_RPC_INVENTORY_MOVE_REFUSED` (`78541072`).

### A. `authority=server` rechazaba el inventario propio

`DispatchInventoryMove` resuelve jugador también cuando el ítem o el destino tienen `GetHierarchyRootPlayer()` (`MCPBridge.c:817-826`). Sin raíz de jugador, mundo a mundo sigue con `player` nulo. Si `ResolvePlayer` falla, se devuelve su error y no se mueve nada. `MCPServerInventoryMove` no se tocó. `MCPInventoryMoveReady` en `to_cargo`, `to_attachment` y `combine` no lee `job.actor` (`MCPBridge.c:1365`). El test de códigos lo comprueba en el fuente.

### B. El rechazo del cliente vuelve

El id del job (`command.id`) es el primer campo del RPC (`MCP_InventoryMove.c:38`, lectura en `:238`). Si esa lectura falla, solo se imprime (`:241`) y el servidor acaba en `timeout`. Cualquier rechazo posterior se imprime y se manda `(jobId, reason)` (`MCPRefuseInventoryMove`, `:102-114`).

`MCPBridge` se suscribe a `g_Game.Event_OnRPC` en el constructor (`:109`) y se da de baja en `~MCPBridge` (`:113-117`). `MCPOnInventoryMoveRefused` (`:1560`) ignora el RPC si falla una comprobación y deja el job hasta su plazo. La lista blanca es `MCPInventoryMoveClientReason` (`:1509`): los once códigos del cliente; otro valor pasa a `client_refused`. Compara `sender.GetId()` con la identidad del actor.

El manejador no escribe el resultado. `Event_OnRPC` no es un comando, y el censo de escalares trataría esa escritura como alcanzable desde cualquier verbo. Marca `job.phase`: 1 si la lectura del servidor ya cuadra (gana la confirmación), 2 si hay rechazo (`job.error`). En el listen server, `Send` puede entrar al manejador antes de volver: el job se inserta antes del RPC (`MCPBridge.c:900`) y, si `phase` ya es 2, `DispatchInventoryMove` publica el rechazo y saca el job (`:913`). Si el RPC llega después, lo publica `ProcessJobs` (`:5167`) antes del timeout, una sola vez. `MCPPostInventoryMoveRefused` (`:1498`) pone `ok=false`, `accepted=false`, `confirmed=false`. `MCPWriteInventoryMove` sigue poniendo `accepted=true` en confirmación y timeout.

Documentado en `MCP_InventoryMove.c:137-140`, `README.md:136` y la descripción de `inventory_move` en `tools/dayz_mcp/server.py`.

Tests: `test_a_client_refusal_reaches_the_caller_unchanged` (un `accepted=false` / `error=no_room` del puente llega igual) y `test_every_client_refusal_code_is_on_the_server_whitelist` (los dos `.c`). `PROJECT-MAP.md` pasa el changelog a 74 KB.

### Gates

Módulos tocados más el censo (el primer fallo de suite salía de ahí) y el tamaño del changelog:

```
Ran 38 tests in 6.720s

OK
```

Suite completa, exit 1:

```
======================================================================
FAIL: test_a_daemon_started_by_another_venv_exits_with_one_line (tests.test_fb_acf3_88c2_8575_4fdf_named_causes.DaemonPythonNotApprovedTest.test_a_daemon_started_by_another_venv_exits_with_one_line)
The ticket's repro: a clone's venv named .venv-clone starts the daemon.
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Users\guill\AppData\Local\Temp\dzmcp-c817-17fa\tools\tests\test_fb_acf3_88c2_8575_4fdf_named_causes.py", line 886, in test_a_daemon_started_by_another_venv_exits_with_one_line
    self.assertIn(
    ~~~~~~~~~~~~~^
        os.path.normcase(str(_TOOLS_DIR / ".venv-mcp" / "Scripts" / "python.exe")),
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        line,
        ^^^^^
    )
    ^
AssertionError: 'c:\\users\\guill\\appdata\\local\\temp\\dzmcp-c817-17fa\\tools\\.venv-mcp\\scripts\\python.exe' not found in 'daemon: daemon_python_not_approved: this interpreter is c:\\users\\guill\\appdata\\local\\temp\\tmpiie5bt7a\\clone\\.venv-clone\\scripts\\python.exe, but the daemon runs only under c:\\users\\guill\\onedrive\\documentos\\dayz projects\\dayz_mcp_dev\\tools\\.venv-mcp\\scripts\\python.exe, the path it checks its own identity against. create that venv with the installer in readme "install" (it installs this package into tools\\.venv-mcp) and start the daemon with its python.exe.'

----------------------------------------------------------------------
Ran 6365 tests in 919.117s

FAILED (failures=1, skipped=46)
```

Ese fallo es el único. Es el del intérprete del árbol vivo, no de este diff.

Linter (exit 1). Cuatro errors, los de la base, mismas líneas. Siete warnings: el mismo `ES-EMPTY-IFDEF` y los seis `ES-GETTYPE-EXACT-MATCH`; en `MCPBridge.c` las líneas se desplazaron. Salida literal:

```
{
  "addon_root": "C:\\Users\\guill\\AppData\\Local\\Temp\\dzmcp-c817-17fa\\addon",
  "status": "FAIL",
  "errors": [
    {
      "check": "ES-LOCAL-VAR-REDECLARE",
      "file": "scripts\\5_Mission\\MCP_ActionCursor.c",
      "line": 163,
      "message": "[FAIL] scripts\\5_Mission\\MCP_ActionCursor.c line 163: local variable 'row' is re-declared in the same method. Enforce Script uses function scope for all locals (no block scope), so this is a compile error 'multiple declaration' (pitfalls-advanced.md:320-362). Declare it once and reuse it.",
      "severity": "FAIL",
      "rule_id": "ES-LOCAL-VAR-REDECLARE"
    },
    {
      "check": "ES-LOCAL-VAR-REDECLARE",
      "file": "scripts\\5_Mission\\MCP_ActionCursor.c",
      "line": 234,
      "message": "[FAIL] scripts\\5_Mission\\MCP_ActionCursor.c line 234: local variable 'row' is re-declared in the same method. Enforce Script uses function scope for all locals (no block scope), so this is a compile error 'multiple declaration' (pitfalls-advanced.md:320-362). Declare it once and reuse it.",
      "severity": "FAIL",
      "rule_id": "ES-LOCAL-VAR-REDECLARE"
    },
    {
      "check": "ES-LAYOUT-PATH-PBOPREFIX-MISMATCH",
      "file": "scripts\\5_Mission\\MCPDialogController.c",
      "line": 39,
      "message": "[FAIL] scripts\\5_Mission\\MCPDialogController.c line 39: layout path '$profile:mcp_hot.layout' does not start with $PBOPREFIX$ 'DayZ_MCP'. Mismatch causes crash + ghost menu in UIManager (pitfalls-advanced.md:304-316, SKILL.md rule 34). Update the layout path to begin with the addon's PBOPREFIX or update $PBOPREFIX$ to match.",
      "severity": "FAIL",
      "rule_id": "ES-LAYOUT-PATH-PBOPREFIX-MISMATCH"
    },
    {
      "check": "ES-LAYOUT-FILE-MISSING",
      "file": "scripts\\5_Mission\\MCPDialogController.c",
      "line": 40,
      "message": "[FAIL] scripts\\5_Mission\\MCPDialogController.c line 40: layout 'DayZ_MCP/gui/layouts/hot/mcp_hot.layout' is referenced but the file does not exist under the addon root (resolved 'gui/layouts/hot/mcp_hot.layout'). CreateWidgets on a missing .layout crashes inside the native call (origin: production mod bug ledger). Create the file or fix the path.",
      "severity": "FAIL",
      "rule_id": "ES-LAYOUT-FILE-MISSING"
    }
  ],
  "warnings": [
    {
      "check": "ES-EMPTY-IFDEF-UNSUPPORTED-PATTERN",
      "file": "scripts\\5_Mission\\MCP_ActionCursor.c",
      "line": 65,
      "message": "[WARN] scripts\\5_Mission\\MCP_ActionCursor.c line 65: unsupported preprocessor pattern for ES-EMPTY-IFDEF. Phase 1 supports only '#ifdef <macro>'/'#ifndef <macro>' paired with '#endif'; #if/#elif/#else and complex conditions are not analyzed.",
      "severity": "WARN",
      "rule_id": "ES-EMPTY-IFDEF-UNSUPPORTED-PATTERN"
    },
    {
      "check": "ES-GETTYPE-EXACT-MATCH",
      "file": "scripts\\5_Mission\\MCPBridge.c",
      "line": 2365,
      "message": "[WARN] scripts\\5_Mission\\MCPBridge.c line 2365: `.GetType()` compared with `==`/`!=`. Exact match fails on inherited or modded variants; the server may silently reject actions whose client check uses IsKindOf (pitfalls-advanced.md:257-274, SKILL.md rules 31-32). Use `obj.IsKindOf(\"TypeName\")` for full inheritance-chain checks.",
      "severity": "WARN",
      "rule_id": "ES-GETTYPE-EXACT-MATCH"
    },
    {
      "check": "ES-GETTYPE-EXACT-MATCH",
      "file": "scripts\\5_Mission\\MCPBridge.c",
      "line": 3751,
      "message": "[WARN] scripts\\5_Mission\\MCPBridge.c line 3751: `.GetType()` compared with `==`/`!=`. Exact match fails on inherited or modded variants; the server may silently reject actions whose client check uses IsKindOf (pitfalls-advanced.md:257-274, SKILL.md rules 31-32). Use `obj.IsKindOf(\"TypeName\")` for full inheritance-chain checks.",
      "severity": "WARN",
      "rule_id": "ES-GETTYPE-EXACT-MATCH"
    },
    {
      "check": "ES-GETTYPE-EXACT-MATCH",
      "file": "scripts\\5_Mission\\MCPBridge.c",
      "line": 4444,
      "message": "[WARN] scripts\\5_Mission\\MCPBridge.c line 4444: `.GetType()` compared with `==`/`!=`. Exact match fails on inherited or modded variants; the server may silently reject actions whose client check uses IsKindOf (pitfalls-advanced.md:257-274, SKILL.md rules 31-32). Use `obj.IsKindOf(\"TypeName\")` for full inheritance-chain checks.",
      "severity": "WARN",
      "rule_id": "ES-GETTYPE-EXACT-MATCH"
    },
    {
      "check": "ES-GETTYPE-EXACT-MATCH",
      "file": "scripts\\5_Mission\\MCPClientBridge.c",
      "line": 3940,
      "message": "[WARN] scripts\\5_Mission\\MCPClientBridge.c line 3940: `.GetType()` compared with `==`/`!=`. Exact match fails on inherited or modded variants; the server may silently reject actions whose client check uses IsKindOf (pitfalls-advanced.md:257-274, SKILL.md rules 31-32). Use `obj.IsKindOf(\"TypeName\")` for full inheritance-chain checks.",
      "severity": "WARN",
      "rule_id": "ES-GETTYPE-EXACT-MATCH"
    },
    {
      "check": "ES-GETTYPE-EXACT-MATCH",
      "file": "scripts\\5_Mission\\MCPClientBridge.c",
      "line": 4239,
      "message": "[WARN] scripts\\5_Mission\\MCPClientBridge.c line 4239: `.GetType()` compared with `==`/`!=`. Exact match fails on inherited or modded variants; the server may silently reject actions whose client check uses IsKindOf (pitfalls-advanced.md:257-274, SKILL.md rules 31-32). Use `obj.IsKindOf(\"TypeName\")` for full inheritance-chain checks.",
      "severity": "WARN",
      "rule_id": "ES-GETTYPE-EXACT-MATCH"
    },
    {
      "check": "ES-GETTYPE-EXACT-MATCH",
      "file": "scripts\\5_Mission\\MCPClientBridge.c",
      "line": 5245,
      "message": "[WARN] scripts\\5_Mission\\MCPClientBridge.c line 5245: `.GetType()` compared with `==`/`!=`. Exact match fails on inherited or modded variants; the server may silently reject actions whose client check uses IsKindOf (pitfalls-advanced.md:257-274, SKILL.md rules 31-32). Use `obj.IsKindOf(\"TypeName\")` for full inheritance-chain checks.",
      "severity": "WARN",
      "rule_id": "ES-GETTYPE-EXACT-MATCH"
    }
  ],
  "info": {
    "files_scanned": 23,
    "elapsed_ms": 37959
  }
}
```

### DISPUTAS ronda 3

- Un `Print` por cada RPC que no es el nuestro inundaría el log: `Event_OnRPC` ve todo el tráfico. El manejador vuelve en silencio si `rpc_type` no es `MCP_RPC_INVENTORY_MOVE_REFUSED`. El `Print` queda para cuando el tipo coincide y una comprobación posterior falla.
- El resultado no se publica dentro del manejador. Se marca `job.phase` y lo publica el dispatch (si `Send` volvió en el mismo stack) o `ProcessJobs` (si el RPC llegó después). Sigue siendo una sola publicación, con `accepted=false`, y el job sale de `m_Jobs` antes del timeout.

### LO QUE NO PUDE VERIFICAR ronda 3

En juego, y por tanto sin ejecutar Enforce: el repro de A (ítem en las manos de U, caja B, `authority=server`, `to_cargo` ya no es `not_reachable`); que `DayZGame.OnRPC` entregue el ScriptRPC de vuelta; que el cargador dentro de un arma en el suelo produzca `no_room` y que ese código vuelva con `accepted=false`; que un fallo al leer solo el id siga en `timeout`. El linter no compila Enforce.

