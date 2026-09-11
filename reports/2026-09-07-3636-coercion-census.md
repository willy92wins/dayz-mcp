# Censo de coercion en el cable (ficha 3636)

Fecha: 2026-09-07 (ronda 2).
Arbol: copia de trabajo de esta lane (no el repo vivo).
Fuente: registro FastMCP tras `build_app(ServerConfig(..., enable_exec_enforce=True))`.
Hay **una** bandera de registro condicional en `build_app`: `enable_exec_enforce`
(`server.py:4578-4584`). El censo la enciende; no hace falta ejecutar la tool.
Las anotaciones se leen de `tool.fn` con
`typing.get_type_hints(..., include_extras=True)` para no perder `Strict*`.

Criterio de **coercible** (ronda 2): una rama convierte si el cable puede
entregar un bool/int/str convertido. Una **union es coercible si CUALQUIER
rama lo es**; una alternativa no coercible no neutraliza a otra.
`Strict(False)` no es estricto: se lee el valor de la metadata, no el nombre
de la clase. `Literal` numerico e `IntEnum` convierten `false`; un `Literal`
solo de strings y un enum de strings rechazan. Forma desconocida: coercible
hasta que el cable demuestre lo contrario. Una anotacion es pista. Lo que
sigue como "peligroso" se midio con `app.call_tool(...)`.

## Totales

- Tools registradas: **61** (60 del registro por defecto + `exec_enforce`)
- Parametros de llamante (sin `Context` inyectado): **217**
- Coercibles: **182**
- No coercibles: **35**
- Tools sin parametros: `bridge_status`, `dayz_knowledge_prepare`, `dayz_knowledge_status`, `list_projects`, `session_status`

Delta respecto a ronda 1 (60 / 214 / 177 / 37), al arreglar el clasificador:

- **2 parametros pasaron de "seguro" a "coercible"** en la superficie por
  defecto: `logs_since.marker` y `wait_for.marker`
  (`str | dict[str, Any] | None`). El `all(...)` de ronda 1 daba por segura
  la union entera por la rama dict. `false` sigue rechazado en el cable
  (pydantic 2 no convierte bool a str); el trinquete los inventaria para
  que una rama numerica futura no pueda esconderse. Eso es F1 en el registro
  vivo.
- **F2 no movio ningun parametro vivo**: no hay `Strict(False)` en la
  superficie. El clasificador de ronda 1 los habria ocultado.
- **F4 anadio 3 parametros que el censo no veia**: `exec_enforce.expr`,
  `exec_enforce.main_fn`, `exec_enforce.timeout_s`. No estaban clasificados
  como seguros; estaban fuera.

Suelo del trinquete: **61 tools / 217 parametros**. Perder la superficie
habilitable hace rojo el piso.

## Lo que el trinquete NO vigila

- Cambio de forma en una clave ya allowlisted: el trinquete es `(tool, param)`,
  no el texto de la anotacion. Meter una rama `float` en `wait_for.marker`
  no crea una clave nueva.
- Coercion de elementos *dentro* de `list[...]` / `dict[...]`.
- El preparse MCP de la cadena `"null"` a `None` antes de pydantic
  (`func_metadata.py`), distinto de `false -> 0.0`.
- Un cliente MCP externo por stdio/JSON-RPC (solo `call_tool` in-process).
- Efectos in-game del ranking. No ejecuta `exec_enforce`.

## `player_teleport.skip_clearance_check` -- primer sospechoso, COMPROBADO

Anotacion: `bool = False` (`tools/dayz_mcp/server.py:3983`).
Tras pydantic hay un `isinstance(..., bool)` (`server.py:3988-3991`) y
`if not skip_clearance_check:` corre el probe (`server.py:3996`).

Esa guarda vive DETRAS de la puerta. Medido por `call_tool`:

| entrada | llega al handler | efecto sobre el probe (columna cubierta) |
| --- | --- | --- |
| `false` (bool JSON) | `False` | corre `surface_query` + `scene_raycast` |
| `0` | `False` | igual |
| `1` | `True` | **salta el probe**; solo `player_teleport` |
| `"true"` | `True` | **salta el probe** |
| `"5"` | rechazo pydantic (`bool_parsing`) | no llama |

`false` en si no apaga la guarda (sigue siendo False). El dano es el simetrico
del defecto original: **un `1` o `"true"` autoriza saltarse un chequeo de
seguridad**. El `isinstance(bool)` nunca ve el `1`.

Si el llamante manda `false` y espera "no", aqui llega `False` y la guarda
sigue. El patron `false -> 0.0` no aplica a un `bool`; aplica a los `float`
abajo.

## Control positivo del defecto original

`client_start_budget_s` ya no es coercible: `StrictFloat | StrictInt | None`
(`server.py:3382`). `call_tool` con `false` / `"5"` / `True` sale `ToolError`
en pydantic. `0` (int) sigue pasando: el rango `[0, 3600]` (`server.py:2858`)
todavia admite un presupuesto cero. Eso es producto, no coercion.

La forma `float | None` **sigue viva** en la superficie. El censo la caza.
Ejemplos: `world_time_set.time_multiplier`, `object_anim.phase`.

## Pruebas de ejecucion (peligrosos)

Criterio: que pasa si el llamante manda `false` / `"5"` / `0`.

### `world_time_set.time_multiplier` (`float | None`, `server.py:4216`)

| entrada | llega | al bridge |
| --- | --- | --- |
| `false` | `0.0` | `time_multiplier: 0.0` |
| `"5"` | `5.0` | (no hace falta para el ranking) |
| `0` | `0.0` | igual |

`0.0` pasa el rango (`server.py:4243-4245`: solo rechaza `< 0` o `> 64`,
salvo `-1`). Congela la sim (el mismo efecto que `SetTimeMultiplier(0)`).
Este **si** es `false -> 0.0` apagando una magnitud que no deberia
interpretarse como "no".

### `dayz_test_run.wait_for_box_s` (`float`, `server.py:3380`)

| entrada | llega al wrapper |
| --- | --- |
| `false` | `0.0` |
| `"5"` | `5.0` |
| `0` | `0.0` |

`_parse_wait_for_box_s` rechaza `bool` (`server.py:2825`), pero pydantic
ya convirtio. `wait_s > 0.0` (`server.py:3404`) no espera. `false` apaga
la espera; es el default, menos grave que el presupuesto, misma forma.

### `object_anim.phase` (`float | None`)

`false` / `0` -> `0.0`; `"5"` -> `5.0`. Un `false` **escribe** fase 0.

### Vecino que debe seguir fallando cerrado: `timeout_s`

`query_player_state` con `timeout_s=false` o `0` -> `bad_timeout`
(`server.py:1777-1778`). Misma anotacion `float`, distinto desenlace:
el bound `> 0` no deja pasar el cero. Eso no autoriza nada.

### `Strict*` (control negativo)

`key_press.dik=false` y `client_start_budget_s=false` mueren en pydantic.
`dik=0` (int de verdad) llega como `0`.

## Ranking: los que hay que arreglar de verdad

Criterio: no "es coercible", sino **si `false`/`1`/`"true"` autoriza o apaga
una guarda**. Un `str` de texto libre no esta aqui. Un `timeout_s` que
rechaza el cero tampoco.

1. **`player_teleport.skip_clearance_check`** -> `StrictBool`.
   `1` y `"true"` saltan el probe de columna cubierta. Primera de la lista
   y comprobada.
2. **`world_time_set.time_multiplier`** -> `StrictFloat | StrictInt | None`,
   y replantear si `0.0` debe ser legal. `false` llega como `0.0` y congela
   la sim.
3. **`dayz_test_run.wait_for_box_s`** -> `StrictFloat` (el parse de bool
   ya existe y nunca se dispara).
4. **`object_anim.phase`** e **`infected_drive.heading`/`speed`** ->
   `StrictFloat | None`. `false` escribe 0.0 al mundo.
5. **`dayz_test_run.build` / `clean` / `pack_only` / `preflight` /
   `no_base_mods` / `no_file_patching`** -> `StrictBool`. `1` y `"true"`
   autorizan mutaciones de lanzamiento. `auto_remediate_steam` ya es
   `StrictBool` cuarenta lineas mas alla.
6. **`vehicle_control.throttle`/`steer`/`brake`/`handbrake`** ->
   `StrictFloat`. `true` llega como `1.0` (actuacion a tope).
7. **`world_weather_set.overcast`/`rain`/`fog`** -> `StrictFloat | None`.
   `false` **setea** el fenomeno a 0.0.
8. **`world_spawn.flags`/`rotation`** -> `StrictInt`. `true` llega como `1`
   (flag RF_*, no un angulo).
9. **`capture_screenshot.save_fullres`** -> `StrictBool`. `1` escribe un
   frame nativo a disco.

No subir a este ranking: `timeout_s` (fail-closed), `str` pelados (pydantic 2.13
rechaza bool/int; `"5"` es texto), conteos `int` cuyo cero es un bound legal.

El trinquete **no arregla** esta lista: la deja inventariada en
`COERCIBLE_ALLOWLIST` con comentario `defect:`. Un parametro coercible
*nuevo* sin entrada hace rojo `tests.test_wire_coercion_census`.

## Tabla del censo

Registro con `enable_exec_enforce=True`, 61 tools, 217 parametros + 5 tools vacias.

| tool | parametro | anotacion | coercible |
| --- | --- | --- | --- |
| `action_use` | `action` | `str` | si |
| `action_use` | `classname` | `str` | si |
| `action_use` | `pos` | `list[float] | None` | no |
| `action_use` | `radius` | `float` | si |
| `action_use` | `timeout_s` | `float` | si |
| `bridge_status` | - | - | no |
| `camera_get` | `cam_mode` | `str` | si |
| `camera_get` | `timeout_s` | `float` | si |
| `camera_set` | `cam_mode` | `str` | si |
| `camera_set` | `cam_pos` | `list[float] | None` | no |
| `camera_set` | `cam_orientation` | `list[float] | None` | no |
| `camera_set` | `look_at` | `list[float] | None` | no |
| `camera_set` | `cam_matrix` | `list[float] | None` | no |
| `camera_set` | `fov` | `float` | si |
| `camera_set` | `settle_ticks` | `int` | si |
| `camera_set` | `timeout_s` | `float` | si |
| `capture_screenshot` | `scale` | `str` | si |
| `capture_screenshot` | `max_tokens` | `int` | si |
| `capture_screenshot` | `frames` | `int` | si |
| `capture_screenshot` | `process_name` | `str` | si |
| `capture_screenshot` | `fmt` | `str` | si |
| `capture_screenshot` | `quality` | `int` | si |
| `capture_screenshot` | `crop` | `str` | si |
| `capture_screenshot` | `crop_space` | `str` | si |
| `capture_screenshot` | `save_fullres` | `bool` | si |
| `capture_screenshot` | `save_dir` | `str` | si |
| `dayz_knowledge_find` | `query` | `str` | si |
| `dayz_knowledge_prepare` | - | - | no |
| `dayz_knowledge_show` | `name` | `str` | si |
| `dayz_knowledge_status` | - | - | no |
| `dayz_test_run` | `project` | `str` | si |
| `dayz_test_run` | `mode` | `str` | si |
| `dayz_test_run` | `mission` | `str` | si |
| `dayz_test_run` | `build` | `bool` | si |
| `dayz_test_run` | `clean` | `bool` | si |
| `dayz_test_run` | `pack_only` | `bool` | si |
| `dayz_test_run` | `preflight` | `bool` | si |
| `dayz_test_run` | `run_id` | `str | None` | si |
| `dayz_test_run` | `extra_mods` | `list[str] | None` | no |
| `dayz_test_run` | `base_mods` | `list[str] | None` | no |
| `dayz_test_run` | `server_mods` | `list[str] | None` | no |
| `dayz_test_run` | `no_base_mods` | `bool` | si |
| `dayz_test_run` | `no_file_patching` | `bool` | si |
| `dayz_test_run` | `port` | `int` | si |
| `dayz_test_run` | `width` | `int` | si |
| `dayz_test_run` | `height` | `int` | si |
| `dayz_test_run` | `player_name` | `str` | si |
| `dayz_test_run` | `server_wait_s` | `int` | si |
| `dayz_test_run` | `wait_for_box_s` | `float` | si |
| `dayz_test_run` | `auto_remediate_steam` | `StrictBool` | no |
| `dayz_test_run` | `client_start_budget_s` | `StrictFloat | StrictInt | None` | no |
| `dayz_test_stop` | `run_id` | `str` | si |
| `engine_set` | `mode` | `str` | si |
| `engine_set` | `timeout_s` | `float` | si |
| `entities_query` | `pos` | `list[float]` | no |
| `entities_query` | `radius` | `float` | si |
| `entities_query` | `limit` | `int` | si |
| `entities_query` | `timeout_s` | `float` | si |
| `exec_enforce` | `expr` | `str` | si |
| `exec_enforce` | `main_fn` | `str` | si |
| `exec_enforce` | `timeout_s` | `float` | si |
| `infected_drive` | `type` | `str` | si |
| `infected_drive` | `pos` | `list[float]` | no |
| `infected_drive` | `heading` | `float | None` | si |
| `infected_drive` | `speed` | `float | None` | si |
| `infected_drive` | `mode` | `str | None` | si |
| `infected_drive` | `timeout_s` | `float` | si |
| `inventory_give` | `classname` | `str` | si |
| `inventory_give` | `dest` | `str` | si |
| `inventory_give` | `uid` | `str` | si |
| `inventory_give` | `timeout_s` | `float` | si |
| `key_press` | `dik` | `StrictInt` | no |
| `key_press` | `timeout_s` | `float` | si |
| `lease_acquire` | `purpose` | `str` | si |
| `lease_acquire` | `max_wait_s` | `float | None` | si |
| `list_projects` | - | - | no |
| `logs_since` | `marker` | `str | dict[str, Any] | None` | si |
| `logs_since` | `max_lines` | `int` | si |
| `logs_since` | `run_id` | `str | None` | si |
| `notify_players` | `show_time` | `float` | si |
| `notify_players` | `title` | `str` | si |
| `notify_players` | `detail` | `str` | si |
| `notify_players` | `icon` | `str` | si |
| `notify_players` | `uid` | `str` | si |
| `notify_players` | `timeout_s` | `float` | si |
| `object_anim` | `source` | `str` | si |
| `object_anim` | `type` | `str` | si |
| `object_anim` | `pos` | `list[float] | None` | no |
| `object_anim` | `phase` | `float | None` | si |
| `object_anim` | `object_id` | `int` | si |
| `object_anim` | `timeout_s` | `float` | si |
| `object_delete` | `object_id` | `int` | si |
| `object_delete` | `timeout_s` | `float` | si |
| `object_inspect` | `want` | `list[str]` | no |
| `object_inspect` | `type` | `str` | si |
| `object_inspect` | `pos` | `list[float] | None` | no |
| `object_inspect` | `object_id` | `int` | si |
| `object_inspect` | `timeout_s` | `float` | si |
| `pipeline_feedback` | `kind` | `Literal['bug', 'request', 'finding', 'tool_contribution']` | no |
| `pipeline_feedback` | `title` | `str` | si |
| `pipeline_feedback` | `body` | `str` | si |
| `pipeline_feedback` | `project` | `str` | si |
| `pipeline_inbox` | `limit` | `int` | si |
| `pipeline_inbox` | `kind` | `str` | si |
| `pipeline_inbox` | `include_resolved` | `bool` | si |
| `pipeline_resolve` | `feedback_id` | `str` | si |
| `pipeline_resolve` | `resolution` | `str` | si |
| `pipeline_resolve` | `evidence_ref` | `str | None` | si |
| `playbook_run` | `name` | `str` | si |
| `playbook_run` | `params` | `dict[str, Any] | None` | no |
| `player_respawn` | `timeout_s` | `float` | si |
| `player_teleport` | `pos` | `list[float]` | no |
| `player_teleport` | `uid` | `str` | si |
| `player_teleport` | `skip_clearance_check` | `bool` | si |
| `player_teleport` | `timeout_s` | `float` | si |
| `query_all_players` | `timeout_s` | `float` | si |
| `query_get_in_condition` | `pos` | `list[float]` | no |
| `query_get_in_condition` | `component` | `int` | si |
| `query_get_in_condition` | `timeout_s` | `float` | si |
| `query_player_state` | `timeout_s` | `float` | si |
| `restore_gameplay` | `timeout_s` | `float` | si |
| `scene_raycast` | `from_pos` | `list[float]` | no |
| `scene_raycast` | `to` | `list[float]` | no |
| `scene_raycast` | `method` | `str` | si |
| `scene_raycast` | `ignore` | `str` | si |
| `scene_raycast` | `radius` | `float` | si |
| `scene_raycast` | `intersect` | `str` | si |
| `scene_raycast` | `timeout_s` | `float` | si |
| `session_acquire` | `purpose` | `str` | si |
| `session_acquire_wait` | `purpose` | `str` | si |
| `session_acquire_wait` | `max_wait_s` | `float | None` | si |
| `session_cancel` | `ticket` | `str` | si |
| `session_heartbeat` | `lease_token` | `str` | si |
| `session_release` | `lease_token` | `str` | si |
| `session_status` | - | - | no |
| `session_wait` | `ticket` | `str` | si |
| `session_wait` | `timeout_s` | `float` | si |
| `surface_query` | `x` | `float` | si |
| `surface_query` | `z` | `float` | si |
| `surface_query` | `timeout_s` | `float` | si |
| `telemetry_read` | `mode` | `str` | si |
| `telemetry_read` | `type` | `str` | si |
| `telemetry_read` | `pos` | `list[float] | None` | no |
| `telemetry_read` | `radius` | `float` | si |
| `telemetry_read` | `path` | `str` | si |
| `telemetry_read` | `max_lines` | `int` | si |
| `telemetry_read` | `timeout_s` | `float` | si |
| `ui_click` | `path` | `str` | si |
| `ui_click` | `button` | `StrictInt` | no |
| `ui_click` | `root` | `str` | si |
| `ui_click` | `mode` | `Literal['direct', 'complete']` | no |
| `ui_click` | `bubble` | `StrictBool` | no |
| `ui_click` | `timeout_s` | `float` | si |
| `ui_dialog` | `kind` | `Literal['acknowledge', 'confirm', 'form']` | no |
| `ui_dialog` | `title` | `str` | si |
| `ui_dialog` | `message` | `str` | si |
| `ui_dialog` | `fields` | `list[dict[str, Any]] | None` | no |
| `ui_dialog` | `timeout_s` | `float` | si |
| `ui_focus` | `path` | `str` | si |
| `ui_focus` | `root` | `str` | si |
| `ui_focus` | `timeout_s` | `float` | si |
| `ui_reload_layout` | `path` | `str` | si |
| `ui_reload_layout` | `mode` | `Literal['reload', 'close']` | no |
| `ui_reload_layout` | `limit` | `int` | si |
| `ui_reload_layout` | `timeout_s` | `float` | si |
| `ui_set_text` | `path` | `str` | si |
| `ui_set_text` | `text` | `str` | si |
| `ui_set_text` | `root` | `str` | si |
| `ui_set_text` | `timeout_s` | `float` | si |
| `ui_tree` | `path` | `str` | si |
| `ui_tree` | `limit` | `int` | si |
| `ui_tree` | `root` | `str` | si |
| `ui_tree` | `timeout_s` | `float` | si |
| `vehicle_control` | `throttle` | `float` | si |
| `vehicle_control` | `steer` | `float` | si |
| `vehicle_control` | `brake` | `float` | si |
| `vehicle_control` | `handbrake` | `float` | si |
| `vehicle_control` | `hold_ttl_s` | `float` | si |
| `vehicle_control` | `timeout_s` | `float` | si |
| `vehicle_enter` | `pos` | `list[float]` | no |
| `vehicle_enter` | `timeout_s` | `float` | si |
| `vehicle_get_in_client` | `pos` | `list[float]` | no |
| `vehicle_get_in_client` | `timeout_s` | `float` | si |
| `vehicle_prepare_fixture` | `type` | `str` | si |
| `vehicle_prepare_fixture` | `pos` | `list[float]` | no |
| `vehicle_prepare_fixture` | `radius` | `float` | si |
| `vehicle_prepare_fixture` | `timeout_s` | `float` | si |
| `vehicle_release` | `timeout_s` | `float` | si |
| `vehicle_telemetry` | `timeout_s` | `float` | si |
| `vehicle_trace` | `mode` | `str` | si |
| `vehicle_trace` | `trace_id` | `str` | si |
| `vehicle_trace` | `cursor` | `int` | si |
| `vehicle_trace` | `limit` | `int` | si |
| `vehicle_trace` | `sample_hz` | `int` | si |
| `vehicle_trace` | `max_samples` | `int` | si |
| `vehicle_trace` | `timeout_s` | `float` | si |
| `wait_for` | `condition` | `Literal['players_at_least', 'players_at_most', 'log_matches']` | no |
| `wait_for` | `value` | `int` | si |
| `wait_for` | `pattern` | `str` | si |
| `wait_for` | `timeout_s` | `float` | si |
| `wait_for` | `poll_interval_s` | `float` | si |
| `wait_for` | `lookback_lines` | `int` | si |
| `wait_for` | `lookback_from` | `Literal['lines', 'launch']` | no |
| `wait_for` | `marker` | `str | dict[str, Any] | None` | si |
| `world_spawn` | `type` | `str` | si |
| `world_spawn` | `pos` | `list[float]` | no |
| `world_spawn` | `flags` | `int` | si |
| `world_spawn` | `rotation` | `int` | si |
| `world_spawn` | `timeout_s` | `float` | si |
| `world_time_set` | `year` | `int` | si |
| `world_time_set` | `month` | `int` | si |
| `world_time_set` | `day` | `int` | si |
| `world_time_set` | `hour` | `int` | si |
| `world_time_set` | `minute` | `int` | si |
| `world_time_set` | `time_multiplier` | `float | None` | si |
| `world_time_set` | `timeout_s` | `float` | si |
| `world_weather_set` | `overcast` | `float | None` | si |
| `world_weather_set` | `rain` | `float | None` | si |
| `world_weather_set` | `fog` | `float | None` | si |
| `world_weather_set` | `time` | `float` | si |
| `world_weather_set` | `min_duration` | `float` | si |
| `world_weather_set` | `timeout_s` | `float` | si |

## Notas

- `scene_raycast.from` es el nombre publico; la anotacion vive en `from_pos`.
- `lease_acquire` es alias de `session_acquire_wait` (mismos parametros).
- El suelo del test: 61 tools y 217 parametros. Perder `exec_enforce` lo rompe.
- `logs_since.marker` y `wait_for.marker` son coercibles por la rama `str`, no
  porque `false` convierta (pydantic 2 lo rechaza).
