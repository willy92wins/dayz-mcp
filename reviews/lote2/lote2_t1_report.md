# T1 — borde FastMCP numeric

## Resultado

Implementado únicamente en `tools/dayz_mcp/server.py`. Se cambiaron 115 anotaciones de argumentos tool de `int`/`float` a `StrictInt`/`StrictFloat`, incluyendo `list[float]` y opcionales. `lease_acquire` hereda la firma de `session_acquire_wait`. No se tocaron conversiones internas, límites ni defaults.

El rechazo sucede en Pydantic/FastMCP antes del handler. Se conservan enteros JSON en campos float, cero, decimales y `None`/omisión opcionales. También se rechazan cadenas numéricas y floats en argumentos StrictInt: es endurecimiento deliberado de tipo, no sólo un guard de bool. La política queda documentada al principio de `build_app`.

## Evidencia ejecutada

Test nuevo: `tools/tests/test_lote2_numeric_boundary.py`.

- Línea 17: clasificador recursivo de anotaciones numéricas, incluyendo Annotated, unión opcional y listas.
- Línea 30: censo de funciones del registro vivo (no lista manual).
- Línea 66: spy sólo en la función final; conserva el modelo y wrappers FastMCP. Todas las sondas entran por `app.call_tool`.
- Línea 78: piso exacto de 119 parámetros numéricos; incluye alias, opcionales, vectores y `exec_enforce` habilitable.
- Línea 87: 310 sondas de rechazo: False y True en cada escalar; cada una de las tres posiciones de cada lista numérica. Requiere error con el nombre del parámetro y ausencia de llamada al handler.
- Línea 105: números reales válidos cruzan el mismo modelo. Estas pruebas de tipo no sustituyen los límites del handler.
- Línea 119: opcionales explícitamente null y omitidos llegan al handler como None.
- Línea 136: handlers reales de object_anim, world_weather_set, vehicle_control y world_spawn conservan cero/enteros; sólo se simula call_bridge.

Comando rojo, antes de cambiar server.py:

```bash
PYTHONPATH='tools;tools/.venv-mcp/Lib/site-packages' PYTHONDONTWRITEBYTECODE=1 /mnt/c/Python314/python.exe -m unittest discover -s tools/tests -p test_lote2_numeric_boundary.py > tools/tests/lote2_t1_red.log 2>&1
```

Resultado: `Ran 5 tests`, `FAILED (failures=304)`. Todos los fallos finales eran `AssertionError: ToolError not raised`, no errores de infraestructura. Los seis casos ya estrictos pasaron. Una primera iteración del fixture produjo además nueve errores por construir `object_inspect.want` con números; se corrigió el generador de listas y se volvió a ejecutar ANTES del arreglo. El rojo conservado es el limpio, sin esos errores.

Comando verde, después del arreglo (mismo comando cambiando destino a `tools/tests/lote2_t1_green.log`): `Ran 5 tests`, `OK`. Repetido después del comentario y del piso exacto de censo: OK.

Todos los archivos escritos se releyeron y se comprobó ausencia de NUL. `server.py` se reanalizó con AST. No git, red, servicios ni bundle. No se inició lifespan. Las sondas no llaman al bridge ni al ejecutor lifecycle real.

**Coordinación con padre:** el test existente `test_wire_coercion_census.py` todavía fija varias coerciones defectuosas como comportamiento esperado. Debe actualizarlo el padre; no lo modifiqué por el alcance asignado. No ejecuté suite completa.

## Censo completo

Registro vivo con `enable_exec_enforce=True`: 61 tools y 217 argumentos del caller. Numéricos: 119 argumentos en 46 tools, compuestos por 68 float (incluye unión budget), 33 int y 18 listas float. Las 4 tools knowledge no tienen argumentos numéricos. Sin flag exec: se excluye sólo `exec_enforce` y su `timeout_s` numérico. No quedan anotaciones bare int/float en las firmas tools de server.py.

La tabla enumera también todos los argumentos adicionales, para no confundir «familia numeric» con sólo los campos defectuosos iniciales. `scene_raycast.from_pos` se publica como `from`. Context no es argumento del caller. Las líneas corresponden a declaraciones finales; `lease_acquire` reutiliza `session_acquire_wait`.

| Tool / línea | Argumentos numéricos finales | Argumentos adicionales |
|---|---|---|
| `action_use` (server.py:4966) | pos: list[StrictFloat] &#124; None; radius: StrictFloat; timeout_s: StrictFloat | action: str; classname: str |
| `bridge_status` (server.py:4642) | — | — |
| `camera_get` (server.py:4443) | timeout_s: StrictFloat | cam_mode: str |
| `camera_set` (server.py:4393) | cam_pos: list[StrictFloat] &#124; None; cam_orientation: list[StrictFloat] &#124; None; look_at: list[StrictFloat] &#124; None; cam_matrix: list[StrictFloat] &#124; None; fov: StrictFloat; settle_ticks: StrictInt; timeout_s: StrictFloat | cam_mode: str |
| `capture_screenshot` (server.py:4539) | max_tokens: StrictInt; frames: StrictInt; quality: StrictInt | scale: str; process_name: str; fmt: str; crop: str; crop_space: str; save_fullres: bool; save_dir: str |
| `dayz_knowledge_find` (knowledge.py:630) | — | query: str |
| `dayz_knowledge_prepare` (knowledge.py:659) | — | — |
| `dayz_knowledge_show` (knowledge.py:640) | — | name: str |
| `dayz_knowledge_status` (knowledge.py:650) | — | — |
| `dayz_test_run` (server.py:3416) | port: StrictInt; width: StrictInt; height: StrictInt; server_wait_s: StrictInt; wait_for_box_s: StrictFloat; client_start_budget_s: StrictFloat &#124; StrictInt &#124; None | project: str; mode: str; mission: str; build: bool; clean: bool; pack_only: bool; preflight: bool; run_id: str &#124; None; extra_mods: list[str] &#124; None; base_mods: list[str] &#124; None; server_mods: list[str] &#124; None; no_base_mods: bool; no_file_patching: bool; player_name: str; auto_remediate_steam: StrictBool |
| `dayz_test_stop` (server.py:3568) | — | run_id: str |
| `engine_set` (server.py:4669) | timeout_s: StrictFloat | mode: str |
| `entities_query` (server.py:4229) | pos: list[StrictFloat]; radius: StrictFloat; limit: StrictInt; timeout_s: StrictFloat | — |
| `exec_enforce` (server.py:4636) | timeout_s: StrictFloat | expr: str; main_fn: str |
| `infected_drive` (server.py:4099) | pos: list[StrictFloat]; heading: StrictFloat &#124; None; speed: StrictFloat &#124; None; timeout_s: StrictFloat | type: str; mode: str &#124; None |
| `inventory_give` (server.py:4149) | timeout_s: StrictFloat | classname: str; dest: str; uid: str |
| `key_press` (server.py:4496) | dik: StrictInt; timeout_s: StrictFloat | — |
| `lease_acquire` (server.py:3288) | max_wait_s: StrictFloat &#124; None | purpose: str |
| `list_projects` (server.py:5062) | — | — |
| `logs_since` (server.py:3631) | max_lines: StrictInt | marker: str &#124; dict[str, Any] &#124; None; run_id: str &#124; None |
| `notify_players` (server.py:3769) | show_time: StrictFloat; timeout_s: StrictFloat | title: str; detail: str; icon: str; uid: str |
| `object_anim` (server.py:4069) | pos: list[StrictFloat] &#124; None; phase: StrictFloat &#124; None; object_id: StrictInt; timeout_s: StrictFloat | source: str; type: str |
| `object_delete` (server.py:3747) | object_id: StrictInt; timeout_s: StrictFloat | — |
| `object_inspect` (server.py:4180) | pos: list[StrictFloat] &#124; None; object_id: StrictInt; timeout_s: StrictFloat | want: list[str]; type: str |
| `pipeline_feedback` (server.py:5079) | — | kind: Literal['bug', 'request', 'finding', 'tool_contribution']; title: str; body: str; project: str |
| `pipeline_inbox` (server.py:5108) | limit: StrictInt | kind: str; include_resolved: bool |
| `pipeline_resolve` (server.py:5141) | — | feedback_id: str; resolution: str; evidence_ref: str &#124; None |
| `playbook_run` (server.py:5169) | — | name: str; params: dict[str, Any] &#124; None |
| `player_respawn` (server.py:4513) | timeout_s: StrictFloat | — |
| `player_teleport` (server.py:4035) | pos: list[StrictFloat]; timeout_s: StrictFloat | uid: str; skip_clearance_check: bool |
| `query_all_players` (server.py:3602) | timeout_s: StrictFloat | — |
| `query_get_in_condition` (server.py:3894) | pos: list[StrictFloat]; component: StrictInt; timeout_s: StrictFloat | — |
| `query_player_state` (server.py:3597) | timeout_s: StrictFloat | — |
| `restore_gameplay` (server.py:4459) | timeout_s: StrictFloat | — |
| `scene_raycast` (server.py:3824) | from_pos: list[StrictFloat]; to: list[StrictFloat]; radius: StrictFloat; timeout_s: StrictFloat | method: str; ignore: str; intersect: str |
| `session_acquire` (server.py:3261) | — | purpose: str |
| `session_acquire_wait` (server.py:3288) | max_wait_s: StrictFloat &#124; None | purpose: str |
| `session_cancel` (server.py:3325) | — | ticket: str |
| `session_heartbeat` (server.py:3335) | — | lease_token: str |
| `session_release` (server.py:3343) | — | lease_token: str |
| `session_status` (server.py:3361) | — | — |
| `session_wait` (server.py:3271) | timeout_s: StrictFloat | ticket: str |
| `surface_query` (server.py:3938) | x: StrictFloat; z: StrictFloat; timeout_s: StrictFloat | — |
| `telemetry_read` (server.py:3866) | pos: list[StrictFloat] &#124; None; radius: StrictFloat; max_lines: StrictInt; timeout_s: StrictFloat | mode: str; type: str; path: str |
| `ui_click` (server.py:4839) | button: StrictInt; timeout_s: StrictFloat | path: str; root: str; mode: Literal['direct', 'complete']; bubble: StrictBool |
| `ui_dialog` (server.py:4943) | timeout_s: StrictFloat | kind: Literal['acknowledge', 'confirm', 'form']; title: str; message: str; fields: list[dict[str, Any]] &#124; None |
| `ui_focus` (server.py:4918) | timeout_s: StrictFloat | path: str; root: str |
| `ui_reload_layout` (server.py:4879) | limit: StrictInt; timeout_s: StrictFloat | path: str; mode: Literal['reload', 'close'] |
| `ui_set_text` (server.py:4809) | timeout_s: StrictFloat | path: str; text: str; root: str |
| `ui_tree` (server.py:4782) | limit: StrictInt; timeout_s: StrictFloat | path: str; root: str |
| `vehicle_control` (server.py:4698) | throttle: StrictFloat; steer: StrictFloat; brake: StrictFloat; handbrake: StrictFloat; hold_ttl_s: StrictFloat; timeout_s: StrictFloat | — |
| `vehicle_enter` (server.py:3813) | pos: list[StrictFloat]; timeout_s: StrictFloat | — |
| `vehicle_get_in_client` (server.py:4655) | pos: list[StrictFloat]; timeout_s: StrictFloat | — |
| `vehicle_prepare_fixture` (server.py:3911) | pos: list[StrictFloat]; radius: StrictFloat; timeout_s: StrictFloat | type: str |
| `vehicle_release` (server.py:4771) | timeout_s: StrictFloat | — |
| `vehicle_telemetry` (server.py:4726) | timeout_s: StrictFloat | — |
| `vehicle_trace` (server.py:4733) | cursor: StrictInt; limit: StrictInt; sample_hz: StrictInt; max_samples: StrictInt; timeout_s: StrictFloat | mode: str; trace_id: str |
| `wait_for` (server.py:5022) | value: StrictInt; timeout_s: StrictFloat; poll_interval_s: StrictFloat; lookback_lines: StrictInt | condition: Literal['players_at_least', 'players_at_most', 'log_matches']; pattern: str; lookback_from: Literal['lines', 'launch']; marker: str &#124; dict[str, Any] &#124; None |
| `world_spawn` (server.py:3727) | pos: list[StrictFloat]; flags: StrictInt; rotation: StrictInt; timeout_s: StrictFloat | type: str |
| `world_time_set` (server.py:4265) | year: StrictInt; month: StrictInt; day: StrictInt; hour: StrictInt; minute: StrictInt; time_multiplier: StrictFloat &#124; None; timeout_s: StrictFloat | — |
| `world_weather_set` (server.py:4338) | overcast: StrictFloat &#124; None; rain: StrictFloat &#124; None; fog: StrictFloat &#124; None; time: StrictFloat; min_duration: StrictFloat; timeout_s: StrictFloat | — |
