# Cargar el addon solo en el servidor con `-serverMod=`

> Estado: comprobada el 11/11 (`2026-11-11`) en DayZDiag `1.30.164289`
> (experimental en `9810aaf`, PBO de la v1.5, run `27028350`) con
> `dayz_test_run(project="DayZ_MCP", mode="all", server_only_mods=["@DayZ_MCP"])`.

## Qué clases viven en cada lado

El PBO del addon contiene clases de los dos lados:

| Lado | Clase / misión | Qué hace |
|---|---|---|
| Solo servidor | `MissionServer` + `MCPBridge` (`addon/scripts/5_Mission/MissionServer.c`, `MCPBridge.c`) | Bridge del servidor: comandos del servidor, estado del mundo, godmode por misión y demás verbos server-side |
| Solo cliente | `MCPClientBridge` dentro de `MissionGameplay` (`addon/scripts/5_Mission/MissionGameplay.c`, `MCPClientBridge.c`) | Bridge del cliente: verbos que solo existen en el cliente (cámara, UI, input, vista del jugador, …) |

Las dos misiones se enganchan por `modded class`: si el PBO no está cargado en
ese proceso, la clase modded no existe y su bridge nunca arranca.

## Qué pasa si el PBO se carga solo con `-serverMod=`

`-serverMod=` carga el PBO únicamente en el proceso servidor. El cliente no
recibe ese PBO, así que en el cliente:

- no existe `MCPClientBridge` ni el hook de `MissionGameplay` que lo arranca;
- no existen las tools/verbos de cliente (los de `MCPClientBridge`), aunque el
  bridge de servidor responda con normalidad;
- el servidor sí tiene `MCPBridge` y sus verbos de servidor funcionan.

Usa `-serverMod=` solo cuando quieras el bridge de servidor sin tocar el
cliente. Para la sesión completa (servidor + cliente) el PBO debe llegar
también al cliente, con el mecanismo de carga de mods del cliente (`-mod=` o
el que use el launcher), no solo al servidor.

## Síntoma para diagnosticarlo

Los dos bridges escriben con `Print`: `MCPClientBridge.Log`
(`addon/scripts/5_Mission/MCPClientBridge.c`) emite el prefijo `[MCP-CLIENT]` y
`MCPBridge.Log` (`addon/scripts/5_Mission/MCPBridge.c`) emite `[DayZ-MCP]`. En
la medida del 11/11 esas líneas salieron en el script log del perfil
(`script_*.log`), no en el RPT. Hay que distinguir dos resultados en el script
log del cliente:

- **Arranque correcto:**
  `[MCP-CLIENT] client config loaded path=$profile:dayz_mcp.json url=… keylen=… instlen=… poll_hz=5`
  (medida en `reviews\ga88\b-logs\client-script_2026-11-11_19-29-37.log`, run
  `61b9878e` del 11/11 con `@DayZ_MCP` y `@LFQuad` en `-mod=` del cliente). Una
  inicialización correcta emite directamente esa línea, sin pasar por
  `pending`.
- **Inicialización pendiente o fallida:** `[MCP-CLIENT] client init
  pending: <motivo>`. Esa línea solo sale del manejo de fallos
  (`LogInitFailure` en `addon/scripts/5_Mission/MCPClientBridge.c`: RestApi o
  config no disponible, URL/clave ausente, RestContext no disponible, …).
  Indica que el PBO de cliente sí cargó, pero su inicialización no se completó.
  Su ausencia no demuestra que falte el addon: un arranque correcto tampoco la
  emite.

Si el PBO se cargó solo con `-serverMod=`:

- ni el script log ni el RPT del **cliente** contienen ninguna línea
  `[MCP-CLIENT]`, ni la de arranque correcto ni la de inicialización pendiente;
- el script log del **servidor** sí muestra actividad `[DayZ-MCP]` de
  `MCPBridge` (no su RPT).

Esa asimetría (servidor con bridge, cliente sin ninguna línea
`[MCP-CLIENT]`) es un indicio compatible con «PBO solo en el servidor»,
no su causa exclusiva ni una prueba por sí sola: un script log de otra
ejecución o perfil, un cliente que aún no llegó a `MissionGameplay` o un
log incompleto producirían la misma ausencia. Confírmalo con la lista de
PBO/mods efectivamente cargados por el cliente antes de concluirlo.

## Qué tools dejan de funcionar

Necesitan el bridge del cliente las tools cuyo comando está en
`CLIENT_COMMANDS` (`tools/dayz_mcp/loopback.py:139-184`, enrutado al peer del
cliente por `peer_for_command` en `tools/dayz_mcp/loopback.py:517-523`) y las
que en su propio camino de ejecución llegan bajo otro nombre a uno de esos
comandos:

- Cámara y vista: `camera_get`, `camera_path_play`, `camera_path_release`, `camera_set`, `camera_set_focus`, `dev_free_camera_set`, `hud_set`, `player_look_at`, `player_view_set`, `restore_gameplay`
- UI y chat: `chat_read`, `gameplay_state`, `ui_click`, `ui_dialog`, `ui_focus`, `ui_map_set`, `ui_menu_set`, `ui_pause`, `ui_reload_layout`, `ui_set_text`, `ui_tree`
- Input, acciones y jugador: `action_cursor`, `action_hold`, `action_use`, `actor_emote`, `anim_timeline`, `input_describe`, `input_trigger`, `key_press`, `player_move`, `player_respawn`, `player_trace`
- Vehículos y armas: `engine_set`, `vehicle_control`, `vehicle_get_in_client`, `vehicle_release`, `vehicle_telemetry`, `vehicle_trace`, `weapon_aim`, `weapon_fire`, `weapon_raise`, `weapon_sights`
- Bajo otro nombre: `object_delete`, `playbook_run`, `player_teleport`, `scene_clear`, `session_release`

Dentro de las directas de `CLIENT_COMMANDS` (`tools/dayz_mcp/server.py`),
todas fallan enteras sin el bridge del cliente (con foco, `camera_set` manda
`camera_set_focus` en lugar de `camera_set`, en
`tools/dayz_mcp/server.py:9948`, `tools/dayz_mcp/server.py:10020` y
`tools/dayz_mcp/server.py:10033`; `restore_gameplay` manda `restore_gameplay`
seguido de `camera_get` en `tools/dayz_mcp/camera_restore.py:10`; `action_use`
manda `action_use`, `action_use_door`, `action_use_component` o
`action_use_target` según el selector en
`tools/dayz_mcp/server.py:11652-11674`).

En las que llegan bajo otro nombre en su propio camino
(`tools/dayz_mcp/server.py`):
`object_delete` (`tools/dayz_mcp/server.py:7686-7701`) y `scene_clear`
(`tools/dayz_mcp/server.py:7629-7642`) pasan por `_client_seat_refusal`
(`tools/dayz_mcp/server.py:7495-7577`, que comprueba `_client_peer_probeable`
en `tools/dayz_mcp/peer_liveness.py:29-38` para leer `vehicle_telemetry` en el
cliente) y **fallan enteras** al momento con `ok: false`,
`error: "vehicle_seat_unverified"` y `deleted: 0`
(`tools/dayz_mcp/server.py:7533-7543`), sin borrar nada en el servidor;
`player_teleport` (`tools/dayz_mcp/server.py:8129-8170`) comprueba
`_runtime_client_peer_probeable` (`tools/dayz_mcp/bridge_readiness.py:88-93`)
antes de pedir `vehicle_telemetry`, así que sin bridge del cliente **solo se
salta esa comprobación previa** (`occupant_client_seated`) y ejecuta el
teletransporte en el servidor; `playbook_run`
(`tools/dayz_mcp/server.py:12016-12033`, `tools/dayz_mcp/playbook_tool.py`)
**falla entera en los playbooks cuyos pasos llaman a tools de cliente**
(`ask_restart` en `playbooks/ask_restart.toml` usa `ui_dialog`, y
`lease_spawn_prepare_trace` en `playbooks/lease_spawn_prepare_trace.toml` usa
`vehicle_telemetry`), mientras que `place_safely`
(`playbooks/place_safely.toml`) usa solo tools de servidor (`box_is_mine`, en
`playbooks/box_is_mine.toml`, no manda comandos al cliente, pero su paso de
`bridge_status` exige `ready.ready` en `true`); y `session_release`
(`tools/dayz_mcp/server.py:6192-6201`) **libera el lease con éxito en el
servidor y el daemon** (`release` en
`tools/dayz_mcp/session_coordination.py:1478-1507` hacia
`_release_active_locked` en
`tools/dayz_mcp/session_coordination.py:2916-2962`, al que también llegan
`/admin/release` en `tools/dayz_mcp/session_coordination.py:2270` y la
expiración `_expire_due` en `tools/dayz_mcp/session_coordination.py:2864`,
pasando por `cleanup` en `tools/dayz_mcp/daemon.py:541-552` y `cleanup_begin`
en `tools/dayz_mcp/daemon.py:89-97` hasta `cleanup_owner` en
`tools/dayz_mcp/loopback.py:8220-8482`, que retira holds en cola en
`tools/dayz_mcp/loopback.py:8247`). Sin sondeos del cliente, las limpiezas
internas `lease_close_client` (`tools/dayz_mcp/loopback.py:6331-6384` exige
censo anunciado), `action_hold_cancel` (`tools/dayz_mcp/loopback.py:8179-8206`)
y `player_look_at_release` (`tools/dayz_mcp/loopback.py:6195-6208`, solo para
comandos ya entregados) no llegan a encolarse, y `camera_path_release` ya no se
encola por lease: con bridge v13 la trayectoria la para el cierre del cliente
(`addon/scripts/5_Mission/MCPClientBridge.c:10819-10838`), que sin censo tampoco
se publica. Si sin `--require-version` el lease admitió
`vehicle_trace(mode="start")` o un `vehicle_control` no nulo para un cliente
`BOUND` (`vehicle_active` en
`tools/dayz_mcp/session_coordination.py:1679-1686`), lo que pasa depende de si
el fin del lease abre una ronda de cierre, cosa que hace si el lease entregó
alguna mutación o quedan filas debidas (`_open_lease_end_round_locked` en
`tools/dayz_mcp/session_coordination.py:4711-4753`). Con ronda, el cierre
agregado sustituye al `vehicle_release` y `cleanup_owner` no lo encola
(`tools/dayz_mcp/loopback.py:8154-8168` y
`tools/dayz_mcp/loopback.py:8384-8393`); sin censo del cliente, su cierre no se
publica (`close_support_unknown` en `tools/dayz_mcp/loopback.py:6372-6383` y
`tools/dayz_mcp/loopback.py:8240-8253`), así que la fila del cliente queda
debida (`tools/dayz_mcp/session_coordination.py:1854-1859`) y sale en
`unconfirmed` de `lease_end`. Sin ronda, `cleanup_owner` encola
`vehicle_release` interno en `_fire_and_forget_ids`
(`tools/dayz_mcp/loopback.py:3263` y `tools/dayz_mcp/loopback.py:8384-8393`)
sin consumidor: exento de la valla del propietario
(`tools/dayz_mcp/loopback.py:4610-4616` y
`tools/dayz_mcp/loopback.py:4634-4635`), permanece en `_bound_queues` hasta
que un `/poll` de cualquier peer (`record_poll` en
`tools/dayz_mcp/loopback.py:7061-7066` hacia `_expire_stale_commands` en
`tools/dayz_mcp/loopback.py:7483-7504`) lo descarta como `stale_discarded`
pasados `COMMAND_TTL_S` (`30.0` s, `tools/dayz_mcp/loopback.py:459`, sin
guardar resultado fallido en `tools/dayz_mcp/loopback.py:7529-7538`) o hasta
que `_retire_instance_locked` (`tools/dayz_mcp/loopback.py:4558-4587`) retira
el enlace del cliente (`binding_retired`), quedando en cola si no ocurre
ninguno de los dos.

Fuera de esa lista, cualquier llamada que alcance el coordinador del daemon
puede expirar un lease ya vencido mediante `_expire_due` (`authorize` en
`tools/dayz_mcp/session_coordination.py:1525-1534` y `status` en
`tools/dayz_mcp/session_coordination.py:2366-2368`, además de `acquire` en
`tools/dayz_mcp/session_coordination.py:421-431`, `wait` en
`tools/dayz_mcp/session_coordination.py:1101-1122` y `heartbeat` en
`tools/dayz_mcp/session_coordination.py:1420-1424`), ejecutando el mismo
`_release_active_locked` (`tools/dayz_mcp/session_coordination.py:2864-2875` y
`tools/dayz_mcp/session_coordination.py:2916-2962`) y `cleanup_owner`
(`tools/dayz_mcp/daemon.py:541-552`, `tools/dayz_mcp/daemon.py:89-97` y
`tools/dayz_mcp/loopback.py:8220-8482`), de modo que la limpieza de cliente de
un lease vencido puede encolarla cualquiera que sea la siguiente llamada que
llegue al daemon. Sin el bridge del cliente, esa limpieza corre la misma suerte
que la del cierre explícito anterior: las limpiezas internas que exigen censo
anunciado o comandos ya entregados (`tools/dayz_mcp/loopback.py:6195-6208`,
`tools/dayz_mcp/loopback.py:6331-6384` y `tools/dayz_mcp/loopback.py:8179-8206`)
no llegan a encolarse, y si el lease vencido había dejado `vehicle_active`
activo (`tools/dayz_mcp/session_coordination.py:1679-1686` y
`tools/dayz_mcp/session_coordination.py:2356-2363`), el `vehicle_release`
interno solo se encola sin ronda de cierre
(`tools/dayz_mcp/loopback.py:8384-8393`). Con ronda, la fila del cliente queda
debida como en el cierre explícito. Sin ronda, el release queda en
`_bound_queues` y `_fire_and_forget_ids` (`tools/dayz_mcp/loopback.py:3263`)
sin consumidor, exento de la valla del propietario
(`tools/dayz_mcp/loopback.py:4610-4616` y
`tools/dayz_mcp/loopback.py:4634-4635`), hasta que cualquier `/poll`
(`tools/dayz_mcp/loopback.py:7061-7066` y
`tools/dayz_mcp/loopback.py:7483-7504`) lo descarta como `stale_discarded`
pasados `COMMAND_TTL_S` (`30.0` s, `tools/dayz_mcp/loopback.py:459` y
`tools/dayz_mcp/loopback.py:7529-7538`) o hasta que `_retire_instance_locked`
(`tools/dayz_mcp/loopback.py:4558-4587`) retira el enlace (`binding_retired`),
quedando en cola si no ocurre ninguno de los dos.

En la medida del 11/11, `ui_tree` devolvió al momento `ok: false`,
`error: "game_not_ready:reason=binding_not_ready"`, `code: "not_ready"` y
`next_step: "bridge_status"`. En el código actual, `_world_read_not_ready` y
`compute_bridge_ready` (`tools/dayz_mcp/bridge_readiness.py:181-188` y
`tools/dayz_mcp/bridge_readiness.py:237-259`, invocados desde `call_bridge` en
`tools/dayz_mcp/server.py:1493` y `tools/dayz_mcp/server.py:3075-3091` para
`_BRIDGE_WORLD_READ_COMMANDS`, derivado de `READ_ONLY_COMMANDS` en
`tools/dayz_mcp/session_coordination.py:28-73`) devuelven ese mismo sobre salvo
que `next_step` es ahora `{"tool": "bridge_status", "args": {}}`
(`tools/dayz_mcp/bridge_readiness.py:258`, mediante `_next_public_call` en
`tools/dayz_mcp/tool_catalog.py:345-352`), y ese camino cubre únicamente las
seis lecturas directas de cliente que llegan a `call_bridge`: `camera_get`,
`chat_read`, `gameplay_state`, `input_describe`, `ui_tree` y
`vehicle_telemetry` (con cliente `BOUND` sin sondeos `reason` es
`binding_not_ready`, y sin cliente `BOUND` es `client_not_polling`). Antes de
`call_bridge`, `action_cursor` (`tools/dayz_mcp/action_cursor.py:495-497`),
`player_look_at` (`tools/dayz_mcp/look_at_control.py:216-225`, tras validar el
lease), `action_hold` (`tools/dayz_mcp/server.py:11745-11755`) y `action_use`
con `door_index`, `component_index` o `hands`/`self`
(`tools/dayz_mcp/server.py:11662-11670`) comprueban el censo anunciado en
`bridge_status` y fallan al momento con `bridge_capability_missing`,
`hold_not_supported` o `*_not_supported`. Las demás tools mutadoras de
`CLIENT_COMMANDS` pasan por `/enqueue` (`tools/dayz_mcp/loopback.py:5092-5109`,
`tools/dayz_mcp/loopback.py:5587-5595` y
`tools/dayz_mcp/loopback.py:5716-5735`, traducido en el cliente MCP por
`_public_enqueue_error` en `tools/dayz_mcp/bridge_errors.py:810-851` y
`_game_not_ready_reason` en `tools/dayz_mcp/bridge_readiness.py:625-653`):
sin lease, en una generación sin ningún sondeo del cliente fallan al momento
con `lease_required` incluso con `--require-version`, porque
`_version_block_fields` (`tools/dayz_mcp/loopback.py:5231-5239`) publica
`never_polled_this_generation` y `_public_enqueue_error`
(`tools/dayz_mcp/bridge_errors.py:813-826`) solo traduce `lease_required` a
`game_not_ready` cuando `version_state` es `legacy_blocked` o
`version_mismatch` (solo en `Runtime` embebido `ensure_peer_allowed` en
`tools/dayz_mcp/server.py:1452-1462` falla antes con
`game_not_ready:reason=client_not_polling`); con lease y `--require-version`
activo, `/enqueue` rechaza con `version_blocked`
(`tools/dayz_mcp/loopback.py:5587-5595`), traducido al momento a
`game_not_ready:reason=client_not_polling`
(`tools/dayz_mcp/bridge_errors.py:831-833`); y con lease y sin
`--require-version`, sin cliente `BOUND` rechaza antes la valla de enlace en
`_enqueue_fence_target` (`tools/dayz_mcp/loopback.py:4803-4827` y
`tools/dayz_mcp/loopback.py:5719-5724`, p. ej. `legacy_unbound` o
`binding_not_ready`), con cliente `BOUND` sin sondeos los verbos del censo de
capacidades en `_CLIENT_ANNOUNCED_COMMANDS`
(`tools/dayz_mcp/loopback.py:1448-1459` — `actor_emote`, `camera_path_play`,
`camera_path_release`, `camera_set` con foco / `camera_set_focus`, `hud_set`,
`player_view_set` y `ui_pause`, además de `action_hold`) fallan al momento
antes de encolarse en `_client_announce_refusal_locked`
(`tools/dayz_mcp/loopback.py:5301-5339` y
`tools/dayz_mcp/loopback.py:5730-5734`) con `bridge_capability_missing`
(`hold_not_supported` para `action_hold`), si el proceso cliente registrado ya
murió rechaza con `client_process_gone`
(`tools/dayz_mcp/loopback.py:5659-5664`), y solo las mutadoras restantes de
cliente entran en `_bound_queues` sin consumidor: cada llamada admitida termina
en lo que ocurra antes entre el agotamiento de su propio presupuesto de espera
—con `ToolError("timeout waiting for ...")` y abandono `tool_timeout`
(`tools/dayz_mcp/server.py:1526-1528`, `tools/dayz_mcp/server.py:1580`,
`tools/dayz_mcp/server.py:3159-3162`, `tools/dayz_mcp/server.py:3288-3291` y
`tools/dayz_mcp/server.py:5135-5138`)— y el barrido `stale_discarded` pasados
`COMMAND_TTL_S` (`30.0` s, `tools/dayz_mcp/loopback.py:459`) en cualquier
`/poll` de cualquier peer —incluido el del servidor—, donde `record_poll`
(`tools/dayz_mcp/loopback.py:7140-7145`) ejecuta `_expire_stale_commands`
(`tools/dayz_mcp/loopback.py:7576-7597`) y `_mark_discarded`
(`tools/dayz_mcp/loopback.py:7599-7648`), guarda en `_results`
`{"id": ..., "ok": false, "error": "stale_discarded"}`
(`tools/dayz_mcp/loopback.py:7632-7636`), y la llamada que sigue esperando lo
recoge en `wait_for_result` (`tools/dayz_mcp/server.py:1559-1580`) o mediante
`call_bridge` y `_await_result` (`tools/dayz_mcp/server.py:3152-3162` y
`tools/dayz_mcp/server.py:3178-3291`, o `probe_bridge_result` en
`tools/dayz_mcp/server.py:1660-1669`, `tools/dayz_mcp/server.py:3447-3474` y
`tools/dayz_mcp/server.py:5115-5120` para `ui_dialog`) y lanza `_bridge_error`
(`tools/dayz_mcp/bridge_errors.py:734-767`) como `ToolError("stale_discarded")`.
Para la mayoría de las tools de cliente ese presupuesto de espera es su
`timeout_s` (`_timeout(timeout_s)` en `tools/dayz_mcp/tool_args.py:92-101`, sin
recorte dentro de `(0.0, MAX_TIMEOUT_S]` con `MAX_TIMEOUT_S = 300.0` s en
`tools/dayz_mcp/tool_args.py:34`; en `ClientRuntime.call_bridge`, `/enqueue` y
`_await_result` comparten un único `deadline` sobre ese presupuesto en
`tools/dayz_mcp/server.py:3074`, `tools/dayz_mcp/server.py:3109-3115` y
`tools/dayz_mcp/server.py:3153-3155`, mientras que en `Runtime.call_bridge`
`wait_for_result` lo cuenta tras encolar en proceso en
`tools/dayz_mcp/server.py:1497-1522` y `tools/dayz_mcp/server.py:1559-1565`).
Entre las tools de cliente cuyo wrapper fija un presupuesto distinto de un
único `timeout_s`, en las mutadoras que sí entran en `_bound_queues` `ui_dialog`
valida `timeout_s` en `[5.0, 240.0]` s (`tools/dayz_mcp/ui_dialog.py:28-29` y
`tools/dayz_mcp/ui_dialog.py:107-116`) y espera `bridge_wait_budget_s(timeout_s)`
= `timeout_s` + `BRIDGE_SLACK_S`, con `BRIDGE_SLACK_S = 10.0` s
(`tools/dayz_mcp/ui_dialog.py:31` y `tools/dayz_mcp/ui_dialog.py:67-69`, usado
en `tools/dayz_mcp/server.py:5107`, pasado a `enqueue_bridge` en
`tools/dayz_mcp/server.py:5109-5111` y `tools/dayz_mcp/server.py:3302` y como
nuevo `deadline` de `probe_bridge_result` en `tools/dayz_mcp/server.py:5113`,
de modo que si `timeout_s` es menor que `COMMAND_TTL_S` pero `timeout_s` +
`BRIDGE_SLACK_S` lo supera, `stale_discarded` puede llegar después de
`timeout_s`), `input_trigger` con `phase="hold"` espera
`max(_timeout(timeout_s), hold_s + INPUT_TRIGGER_HOLD_SLACK_S)` con
`INPUT_TRIGGER_HOLD_SLACK_S = 5.0` s (`tools/dayz_mcp/tool_args.py:225`,
`tools/dayz_mcp/tool_args.py:307-310` y `tools/dayz_mcp/server.py:10654-10658`),
`player_move` con `phase="hold"` espera
`max(_timeout(timeout_s), hold_s + HOLD_SLACK_S)` con `HOLD_SLACK_S = 5.0` s
(`tools/dayz_mcp/player_move.py:46` y `tools/dayz_mcp/server.py:10781-10785`), y
`restore_gameplay` aplica `_timeout(timeout_s)` por separado a cada una de sus
dos llamadas (`restore_gameplay` en `tools/dayz_mcp/server.py:10467-10468` y
`camera_get` en `tools/dayz_mcp/server.py:10473-10475`, quedando bloqueada en la
primera sin consumidor); fuera de las admitidas sin sondeos del cliente,
también ajustan o reparten la espera `actor_emote` con
`max(_timeout(timeout_s), wait_s + ACTOR_EMOTE_WAIT_SLACK_S)` y
`ACTOR_EMOTE_WAIT_SLACK_S = 5.0` s (`tools/dayz_mcp/loopback.py:1412` y
`tools/dayz_mcp/server.py:10433-10436`), `action_hold` exigiendo `timeout_s >=
hold_timeout_s + HOLD_CALL_MARGIN_S` (`HOLD_CALL_MARGIN_S = 15.0` s en
`tools/dayz_mcp/action_hold.py:22` y `tools/dayz_mcp/action_hold.py:202-206`) y
repartiendo un único `deadline` entre `bridge_status_payload`,
`enqueue_action_hold` y `await_action_hold`
(`tools/dayz_mcp/server.py:11726-11771`), `action_cursor` repartiendo un único
`deadline` entre dos lecturas de `bridge_status_payload` y sus llamadas a
cliente y servidor (`tools/dayz_mcp/action_cursor.py:481-547`), `player_look_at`
repartiendo un único `deadline` sobre `_timeout(timeout_s)` entre dos lecturas
de `bridge_status_payload` y `call_bridge_fenced` mientras recorta el
`timeout_s` enviado al bridge a `min(timeout, MAX_CONTROL_S)` con
`MAX_CONTROL_S = 3.0` s (`tools/dayz_mcp/look_at_control.py:16`,
`tools/dayz_mcp/look_at_control.py:196-198` y
`tools/dayz_mcp/look_at_control.py:204-254`), `action_use` con `door_index`,
`component_index` o `hands`/`self` consultando antes `bridge_status_payload()`
fuera del `_timeout(timeout_s)` de `call_bridge`
(`tools/dayz_mcp/server.py:11662-11674`), y las seis lecturas de cliente en
`ClientRuntime.call_bridge` acotando su sondeo previo de `bridge_status_payload`
a `min(LIVENESS_STATUS_TIMEOUT_S, ...)` con `LIVENESS_STATUS_TIMEOUT_S = 1.0` s
dentro del mismo `deadline` (`tools/dayz_mcp/server.py:701` y
`tools/dayz_mcp/server.py:3074-3091`).

Las tools del servidor, como `query_all_players`, siguen funcionando con
normalidad (`_server_read_ready` en `tools/dayz_mcp/bridge_readiness.py:204-235`
solo exige el peer del servidor).

## Verificado el 11/11

Medido el 11/11 (`2026-11-11`) en DayZDiag `1.30.164289` (experimental en
`9810aaf`, PBO de la v1.5, run `27028350`):

- `dayz_test_run(project="DayZ_MCP", mode="all", server_only_mods=["@DayZ_MCP"])`:
  `succeeded`, `reason: "binding_not_ready"`; servidor arrancado con
  `-serverMod=P:\Mods\@DayZ_MCP` y sin `-mod=`, cliente con `-mod=` vacío, y
  ninguno con `-filePatching`.
- `query_all_players` listó al jugador a los `44,7 s` de volver
  `dayz_test_run`.
- `ui_tree(timeout_s=15)` contestó en `0,41 s` con `ok: false`,
  `error: "game_not_ready:reason=binding_not_ready"`, `code: "not_ready"`,
  `next_step: "bridge_status"`.
- `bridge_status`: servidor `ok` y `BOUND`; cliente
  `never_polled_this_generation`; `ready: false` (`binding_not_ready`).
- Cliente (run `27028350`): `0` líneas `[MCP-CLIENT]` y `0` `[DayZ-MCP]` en el
  script log, el RPT y el log de errores; el RPT solo nombra `DayZ_MCP` en la
  ruta del perfil y en ninguna lista de mods o addons.
- Servidor (run `27028350`): `16` líneas `[DayZ-MCP]`, todas en el script log y
  `0` en el RPT, con arranque
  `[DayZ-MCP] config loaded path=$profile:dayz_mcp.json url=… keylen=… instlen=… poll_hz=5`.
- Con el PBO también en el cliente (run `61b9878e` del 11/11, `-mod=` con
  `@DayZ_MCP` y `@LFQuad`,
  `reviews\ga88\b-logs\client-script_2026-11-11_19-29-37.log`): `162` líneas
  `[MCP-CLIENT]` y `0` `[DayZ-MCP]` en el script log del cliente (`0` y `0` en
  su RPT y en su log de errores), con arranque
  `[MCP-CLIENT] client config loaded path=$profile:dayz_mcp.json url=… keylen=… instlen=… poll_hz=5`.

Como se indica en la sección de diagnóstico, la ausencia de líneas
`[MCP-CLIENT]` debe confirmarse siempre con la lista de PBO/mods efectivamente
cargados por el cliente.
