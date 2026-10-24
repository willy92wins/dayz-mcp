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
