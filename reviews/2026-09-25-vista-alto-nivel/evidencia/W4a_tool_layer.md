<!-- Salida CRUDA de una lane Qwen3.8-Flash-Next (GX10, ronda 4, contexto completo), sin editar. NO es el informe: puede contener errores.
     Verificación mecánica del receptor (EVID literal a ±10 líneas en HEAD 57269d2): == W4a_tool_layer.md findings: 15 {'FAR': 13, 'EXACT': 2}
     Veredicto del receptor: ver ../INFORME.md §4 y §8. -->



GATE NO CORRIDO: revisión por API sin herramientas.

## Resumen

`build_app` es una factoría de 63 closures con 4 mutadores de esquema ajenos a la firma (7244-7248) y 6 formas de respuesta coexistentes. La partición viable pasa por 15 módulos temáticos que reciben un contexto explícito en lugar de closures, un registro central que reemplaza a `app._tool_manager` y a `app._mcp_server.list_tools()`, y dos de los 4 parcheadores son declarables mientras que los otros dos no porque la autoridad del enum es dinámica y el public name `from` es reservada. La migración a sobre único se hace de forma aditiva en un wrapper común. Todo el plan es verifiable byte a byte sobre el catálogo exportado antes/después.

## A. Responsabilidades

Rangos verificados en `tools/dayz_mcp/server.py`, todos fuera de 4655-7271 (`build_app`):

- **Contrato de constantes de superficie y enums de superficie**: 95-325. `UiClickMode`, `InventoryAttachDest`, `TelemetryReadMode`, `_CLOSED_SCHEMA_TOOLS` (100-108), `DEFAULT_TOOL_TIMEOUT_S`, `MAX_TIMEOUT_S`, `LEASE_REQUIRED_RECIPE`/`LEASE_EXPIRED_RECIPE`/`LEASE_INVALID_RECIPE`/`TAKEOVER_REQUIRED`/`RETAIL_QUARANTINE_RECIPE` (134-150), `ECE_*` y `WORLD_SPAWN_FLAGS_LINE` (164-185), `READY_REASONS` y `_READY_NEXT_TOOLS` (199-237), `WAIT_FOR_LOOKBACK_MAX`/`WAIT_FOR_LOOKBACK_FROM` (238-245), `_REMOTE_ERROR_CODES`/`_STALE_TICKET_ERRORS`/`_STALE_LEASE_ERRORS`/`_PUBLISHED_NOT_READY_CODES`/`_WAIT_FOR_RETRYABLE_NOT_READY` (251-325).
- **Seguridad de errores hacia el wire (no host paths)**: 328-541. `_carriable_hint`, `_DAYZ_TEST_VALUE_ERROR_CODES`, `_log_opaque_failure`, `_wire_safe_error`, `_is_safe_error_token`, `_opaque_dayz_test_failure`, `_typed_dayz_test_value_errors`, `_CONTROL_CLIENT_ERROR_CODES`, `_published_not_ready_code`, `_remote_error_code`.
- **Readiness (`bridge_status`)**: 544-622 + 1024-1095. `_FENCE_BLOCK_READY`, `_finite_poll_age`, `compute_bridge_ready`, `_front_key`, `_ready_next_tool`, `_with_ready`, `_game_not_ready_reason`, `_target_peer_down`.
- **Progressive disclosure**: 625-689. `_BRIDGE_WORLD_READ_COMMANDS`, `_LEASE_REVEAL_PREFIXES`, `_INITIAL_CATALOG_NAMES`, `_INITIAL_DESCRIPTION_LIMIT`, `_runtime_holds_lease`, `_progressive_disclosure_active`, `_is_lease_revealed_tool`, `_compact_initial_catalog`.
- **Recipe / `next_step`**: 692-824 + `agent_loop.py`. `_visible_public_tools`, `_next_public_call`, `_world_read_not_ready`, `_with_bridge_success_hints`, `_with_ok_next_step`.
- **Censo de capacidades vs tools registradas**: 838-982. `_BRIDGE_COMMAND_TOOLS`, `_compare_bridge_capabilities`, `_with_capability_comparison`, `_client_peer_announces_command`.
- **Fingerprint de registro y overlay de frescura**: 984-1021 + 4003-4465. `_registry_tool_records`, `_capture_process_registry`, `_frozen_tool_registry_overlay`, `_annotate_caller_tool_registry`, `_observe_caller_tool_registry_stale`, `_TOOLS_REMEDIATION_*`, `_DAEMON_REMEDIATION_WHEN`, `_MUTATION_REJECTS_META`, `_tool_registry_remediation_for`, `_daemon_source_remediation`, `_annotate_mcp_fence`.
- **Traducción de `ToolError` hacia el wire**: 1098-1294. `_UI_ECHO_VERBS`, `_bridge_error_detail`, `_bridge_error`, `_retail_quarantine_recipe`, `_public_enqueue_error`.
- **Config y runtimes**: 1297-2379. `_image_format_from_mime`, `ServerConfig`, `Runtime`, `_CallBudgetExpired`, `ClientRuntime`, `required_keyfile`.
- **Validadores compartidos de argumentos**: 2382-2567. `_bad_args`, `is_allowed_spawn_flags`, `_require_vec3`, `_require_float_list`, `_timeout`, `_finite_float`, `_optional_finite_float`, `_require_range`, `_is_int_clock_part`, `_add_applied_days`, `_overflow_clock_parts`, `_clock_was_normalized`, `_normalize_applied_clock`, `VEHICLE_CONTROL_MAX_TTL_S`.
- **Cuatro parcheadores de esquema + descripciones de run_parameters**: 2570-2770.
- **Helpers de entidades/objetos/logs/wait_for**: 2773-3296 + 3299-3648 + 3651-3718. `_player_count`, `_sibling_profile_dirs`, `_run_start_epoch`, `_current_launch_logs`, `_coerce_logs_since_marker`, `_offset_before_last_lines`, `_marker_rewound`, `_log_markers_*`, `_new_log_lines`, `_scan_log_for_pattern`, `_record_scan`, `_scanned_report`, `_wait_for_response`, `_entity_wait_*`, `_structured_not_ready_message`, `execute_wait_for`, `_peer_liveness_suffix`, `execute_ui_dialog`.
- **Box FIFO / session / dayz_test_run helpers**: 3721-4652. `_parse_wait_for_box_s`, `_box_from_status`, `_box_*`, `_BOX_OFFER_*`, `_offer_*`, `_box_wait_cannot_help`, `_box_queue_offer`, `_port_conflict_fields`, `_apply_takeover_required`, `_enrich_active_run_result`, `_failed_active_run_result`, `_heartbeat_box_claim`, `_release_box_wait_ticket`, `execute_wait_for_box`, `box_available_for`, `_ownerless_idle_*`, `_lease_renewal_contract`, `_client_dump_*`, `_attach_revalidated_runs_retired_recently`, `_session_status_blocked_on`, `_bridge_status_description`.
- **CLI y ciclo de vida del proceso**: 7274-7410. `parse_args`, `_release_and_exit`, `run_supervisor`, `run`.

[INFERENCIA] Los 6 bloques de 95-325, 328-541, 625-689, 692-824, 2570-2770 y 4003-4652 son candidatos a `dayz_mcp.tools.contracts.*` porque hoy son el vocabulario de la superficie pública y no el mecanismo de registro.

## B. Partición

Módulos destino propuestos (nombres nuevos, no verificados como existentes), todos bajo `tools/dayz_mcp/tools/`:

- `contracts/constants.py`: 95-325, 1098-1294, 2382-2567 (validadores) y 2570-2770.
- `contracts/envelope.py`: nuevo sobre único.
- `registry.py`: nuevo.
- `readiness.py`: 544-622 + 1024-1095.
- `disclosure.py`: 625-689 + 4802-4806 + 7257-7270 (`list_tools_progressive`).
- `recipes.py`: 692-824 + re-export de `agent_loop`.
- `wire_errors.py`: 328-541 + 1215-1294.
- `fingerprint.py`: 984-1021 + 4003-4465.
- `capabilities.py`: 838-982.
- `box_fifo.py`: 3721-4028 + 4274-4652 + 3721-4271.
- `wait_log.py`: 2773-3014 + 3017-3296 + 3299-3648 + 3651-3718.
- Ruts de herramientas, una por dominio con las 63 tools del catálogo:
  - `session.py`: session_acquire, session_wait, session_acquire_wait, lease_acquire, session_cancel, session_heartbeat, session_release, session_status.
  - `lifecycle.py`: dayz_test_run, dayz_test_stop, dayz_test_close, list_projects.
  - `status.py`: bridge_status.
  - `pipeline.py`: pipeline_feedback, pipeline_inbox, pipeline_resolve.
  - `knowledge.py`: 4 tools dayz_knowledge_* (registradas vía `register_knowledge_tools` 4701).
  - `playbook.py`: playbook_run, playbook_reload.
  - `world_server.py`: query_player_state, query_all_players, world_spawn, object_delete, notify_players, scene_raycast, telemetry_read, query_get_in_condition, vehicle_prepare_fixture, surface_query, player_teleport, object_anim, infected_drive, inventory_give, inventory_attach, object_inspect, entities_query, world_time_set, world_weather_set.
  - `vehicle_client.py`: vehicle_enter, vehicle_get_in_client, engine_set, vehicle_control, vehicle_telemetry, vehicle_trace, vehicle_release.
  - `camera_client.py`: camera_set, camera_get, restore_gameplay.
  - `client_input.py`: key_press, player_respawn, action_use.
  - `client_ui.py`: ui_tree, ui_set_text, ui_click, ui_reload_layout, ui_focus, ui_dialog.
  - `capture.py`: capture_screenshot.
  - `exec.py`: exec_enforce (condicional por `config.enable_exec_enforce`, 5554-5560).
- `cli.py`: 7274-7410.

Interfaz de registro propuesta (nombre y firma concretos, a verificar):

```python
# tools/dayz_mcp/tools/registry.py
@dataclass(frozen=True)
class ToolContext:
    runtime: Any
    config: ServerConfig
    log_marker_state: dict[str, str]
    observe_sources: Callable[[], dict[str, Any]]
    ctx: "AppHandle"

ToolFactory = Callable[[ToolContext], Callable[..., Any]]

def register_all(app: "AppHandle", ctx: ToolContext) -> None: ...
```

Cada módulo exporta una función de fábrica por tool que devuelve la coroutine a registrar. Las closures de hoy se sustituyen por inyección explícita a través de `ToolContext`:
- `runtime` → `ctx.runtime` (Runtime/ClientRuntime, 1336/1556).
- `config` → `ctx.config` (`ServerConfig`, 1306-1333).
- `log_marker_state` → hoy es un dict mutado por `logs_since` (5293) y leído por el mismo handler (5336-5339, 5396); se mueve a `ctx.log_marker_state` creado por el registro y accedido por referencia.
- `observe_server_sources` → se rellena al final del registro (7256) porque necesita el `app` construido. Se expone como callback tardío (`observe_sources` reemplazable) o como `functools.partial` ligado tras el `install_result_freshness`.

Dirección de dependencias permitida:
- `server.py` (facade) → `tools/cli.py`, `tools/registry.py`.
- `tools/registry.py` → 14 módulos de tools + `contracts/*`.
- Un módulo de tools → NUNCA otro módulo de tools; solo a `contracts/*`, `core.py`, `result_prune.py`, `agent_loop.py`, `playbook_tool.py`.
- `contracts/*` → sin dependencias a `tools/*`.

[INFERENCIA] `exec_enforce` y las 4 tools de `register_knowledge_tools` (4701) deben respetar el mismo registro; hoy no pasa por `@app.tool`.

## C. Esquemas

1. **`_patch_public_argument_alias`** (2570-2588, aplicada a `scene_raycast` en 7248):
   - Renombra la propiedad del JSON Schema de `from_pos` a `from` (2574-2576), renombra también dentro de `required` (2578-2579), y envuelve `call_fn_with_arg_validation` para reescribir el argumento entrante de `from` a `from_pos` antes de validar (2582-2588).
   - Declarativamente no se puede con `Annotated[..., Field(alias=...)]` porque FastMCP usa el alias de Pydantic como nombre público y deja el handler con el alias (`from`) sin poder nombrar el parámetro así (reservada). Alternativa viable sin parchear: cambiar el handler a `from_`/`from_vector` y documentar que el public name es `from`; esto rompe el contrato, no sirve para migración silenciosa.
   - [VERIFICAR-API] si `mcp==1.27.2` tiene un hook público de nombre público de argumento distinto del nombre de Python.
2. **`_patch_closed_tool_schema`** (2640-2662, aplicada a 7 herramientas en 100-108 + 7246-7247):
   - Fija `additionalProperties=False` (2644), siembra `required=[]` si falta (2647), y envuelve la validación para rechazar argumentos desconocidos con un mensaje custom que acota a 5 nombres (2624-2637) más `; missing:`.
   - La parte declarable es `additionalProperties=False`: con un modelo `BaseModel` de argumentos y `model_config = ConfigDict(extra="forbid")`, FastMCP publicaría ese flag. El mensaje custom de 2624-2637 y el top-5 no son declarables; requieren el wrapper.
   - [INFERENCIA] El resto del repo no modela args con BaseModel (son parámetros sueltos), por lo que esto exigiría reescribir las 7 signatures a un modelo Pydantic por herramienta.
3. **`_patch_mode_enum_from_authority`** (2665-2689, aplicada a `dayz_test_run` 7244):
   - Lee `dayz_test_modes.public_mode_names()` al construir (2680) y en cada llamada (2684); publica `enum` en la propiedad `mode` y valida.
   - NO declarable con `Literal[...]` porque el docstring 2666-2672 dice explícitamente que debe respetar a substituted record set en tiempo de ejecución. `Literal` se congela en build time.
4. **`_describe_run_parameters`** (2741-2770, aplicada a `dayz_test_run` 7245):
   - Inyecta 6 descripciones (`mode`, `run_id`, `extra_mods`, `auto_remediate_steam`, `client_start_budget_s`, `takeover`) en `tool.parameters["properties"][field]["description"]`, y lanza `RuntimeError` si la propiedad falta (2768-2769).
   - 100% declarable: `Annotated[str, Field(description=RUN_ID_MATRIX_MODE_DESCRIPTION)]` o `Field(default=..., description=...)`. Es el parcheador más barato de eliminar.

HECHOS: 222 de 228 parámetros no tienen `description`; los 6 que sí la tienen en este parcheador son el núcleo de la anomalía, no la causa general.

## D. Sobre único

Formas a unificar (HECHOS verificados):
1. `ToolError` con código pelado (4742 `raise ToolError("bad_purpose")`).
2. dict con `ok`/`error` + `next_step` inyectado (`_with_ok_next_step` 805-824).
3. `next_step=` incrustado en string (`with_next_step`, `agent_loop.py` 66-74).
4. dict `ready`/`reason` sin `ok` (`compute_bridge_ready` 571-622).
5. `next_step` con `args: {}` siempre vacío (`_next_public_call` 700-707).
6. Recetas en string (`LEASE_REQUIRED_RECIPE` 134-150).

Plan aditivo sin quitar nada:

- **Paso 0**: definir `tools/contracts/envelope.py` con `ToolEnvelope` que añade a cualquier retorno dict las llaves `ok: bool`, `error: str | None`, `code: str | None`, `next_step: NextStep | None` con `NextStep = {"tool": str, "args": dict[str, Any], "hint": str | None}`. Conserva los campos ya presentes (no los renombra, no los elimina).
- **Paso 1**: un wrapper único en `tools/registry.py` —`wrap_tool(handler: Callable) -> Callable`— que aplica a todo registro:
  - Si el handler devuelve dict, pasa por `normalise_envelope(result, command=...)` que solo *añade* llaves: `ok`, `error`, `next_step` (si aún no están), `warnings: list[str]` (si aún no está), `not_verified: list[str]` (si aún no está).
  - Si lanza `ToolError`, el mismo wrapper convierte el string a `(code, hint)` partiendo por `; ` (contrato ya documentado en 1231 y 1221-1223), y los expone como atributos `error.code`, `error.hint` (el patrón ya se usa con `error.object_id` en 1234).
- **Paso 2**: los 6 sitios heterogéneos se reescriben a la forma `raise ToolErrorFromCode(code, hint=..., next_step=...)` y al retorno dict ya normalizado. El wrapper mantiene la compatibilidad durante todo el proceso.
- **Paso 3**: `_ready_next_tool` / `_next_public_call` / `_with_ok_next_step` dejan de mutar y pasan a poblar `next_step` dentro del envoltorio común.
- **Paso 4**: `agent_loop.with_next_step` se queda como helper destring-only hasta que los consumidores toleren el objeto; en paralelo, el wrapper emite la forma string Y la forma objeto, de modo que cualquier cliente siga leyendo el string actual y el nuevo campo sea opcional.

Tratamiento de `ToolError` hoy: FastMCP serializa solo `str(exc)` (2583-2585, 5155-5162). Por eso el wrapper NO puede devolver un dict en lugar de lanzar `ToolError`; mantiene el `raise` y añade atributos para que un futuro `@mcp_error_serializer` (hook propio de este repo, no de FastMCP) los consuma si algún día hay hook público en MCP [VERIFICAR-API].

Familias de tests a tocar (descripción, no nombres):
- Tests que afirman el string exacto de `ToolError` (`bad_purpose`, `bad_ticket`, `bad_lease_token`, `bad_flags`, `bad_marker`, `bad_year`, `bad_month`, `bad_day`, `bad_hour`, `bad_minute`, `bad_time_multiplier`, `bad_type`, `bad_radius`, `bad_mode`, `bad_args: entity.*`, `bad_timeout`, `bad_overcast`, `bad_rain`, `bad_fog`, `no_weather_fields`, `bad_throttle`, `bad_steer`, `bad_brake`, `bad_handbrake`, `bad_hold_ttl_s`, `bad_wait_timeout`, `bad_dayz_test_request`, `run_not_owned`, `run_exists`, `run_not_found`, `bridge_mod_missing`, etc.) — deben seguir pasando byte a byte porque el string de cabeza no cambia.
- Tests que asertan el objeto dict devuelto por herramienta con llaves exactas (los 5 casos 2/4/5 de arriba) — pasan a comparar con el sobre envoltorio, pero las llaves actuales deben seguir presentes.
- Tests de `_compact_initial_catalog` sobre `description` truncada a 80 chars con carácter final `…` (675-689).
- Tests de `_patch_closed_tool_schema` sobre el mensaje `bad_args: unexpected arguments: ... (accepted: ...) [; missing: ...]` (2633-2637).
- Tests de `_patch_mode_enum_from_authority` con `dayz_test_modes.public_mode_names()` sustituido y dos llamadas seguidas a la misma app (2683-2687).
- Tests de progressive disclosure sobre catálogo inicial, truncado y `list_changed` (625-689 + 4802-4806).
- Tests de `tool_pack.apply_tool_pack` (46-54) sobre filtro `local8b` tras build.
- Tests de `_front_key` (1024-1032) y `_with_ready` (1043-1064) sobre el orden de llaves en el payload.
- Tests de `_annotate_entities_reliability` / `_normalize_entities_cargo` (2884-2939).
- Tests de `_bridge_status_description()` (4619-4652) que compara el set `READY_REASONS | _FENCE_BLOCK_READY.values()` contra el catálogo exportado.

## E. Internos de FastMCP

HECHOS: `app._tool_manager` usado en 986, 2571, 2641, 2674, 2755, 7249 y 7251; `app._mcp_server.list_tools()(...)` en 7270.

- 986 (`_registry_tool_records`): itera `app._tool_manager.list_tools()` para extraer `name`, `description` y `parameters` y producir el snapshot que alimenta el fingerprint (1007-1013). Alternativa pública plausible: `await app.list_tools()` (asíncrono) devuelve objetos Tool con `inputSchema` no `parameters` [VERIFICAR-API]; requeriría adaptar los keys de 990-995.
- 2571, 2641, 2674, 2755: cuatro `get_tool(name)` para mutar `tool.parameters` y `tool.fn_metadata.call_fn_with_arg_validation`. No hay alternativa pública conocida para mutar un tool ya registrado; FastMCP expone `app.tool(...)`/`add_tool(...)` para REGISTRO, no para post-mutación.
- 7249: `tool_pack_mod.apply_tool_pack(app._tool_manager, config.tool_pack)` (46-54) llama a `list_tools()` y a `remove_tool(name)`. Alternativa pública: en FastMCP no conozco un `remove_tool` público; con la propuesta B el registro central podría simplemente no registrar las fuera del pack, y el tool_pack pasar a ser filtro del registry propio [VERIFICAR-API si FastMCP 1.27.2 ofrece `remove_tool` en la cara pública].
- 7251: `app._tool_manager.list_tools()` para el `_registered_tool_names` cacheado (4710, 7253-7254). Alternativa pública: `await app.list_tools()` (asíncrono).
- 7270: re-bind del handler MCP de `tools/list` porque FastMCP fijó el original en construcción (7265-7268). Alternativa pública: pasar el decorator de tools/list en la construcción, si FastMCP 1.27.2 lo permite; si no, es imposible [VERIFICAR-API]. El comentario 7265-7268 afirma que reemplazar el atributo Python no basta, así que probablemente no exista hook limpio.

Conclusión: en este repo, 6 de los 8 usos internos son necesarios por mutación post-registro; al hacer declarables 2 de los 4 parcheadores y mover el catálogo/descarga a la propuesta B, 4 de esos 6 se eliminan.

## F. Orden y riesgos

Cada paso autoverificable, catálogo de salida idéntico byte a byte antes/después (comparando el resultado de `app.list_tools()` serializado con el mismo `canonical_json_bytes` ya disponible en 211-214):

1. Extraer a `_contracts` 95-325 + 2382-2567 + 2570-2770 con `from ._contracts import ...` en `server.py`. Riesgo: 0, es re-export puro.
2. Extraer 544-622 + 1024-1095 a `readiness.py`; 328-541 + 1215-1294 a `wire_errors.py`; 625-689 + 4802-4806 + 7257-7270 a `disclosure.py`; 692-824 a `recipes.py`; 838-982 a `capabilities.py`; 984-1021 + 4003-4465 a `fingerprint.py`. Riesgo: 0, re-exports + 1 `functools.partial` para `install_result_freshness`.
3. Añadir `tools/registry.py` con `ToolContext` y un registro por fábrica, SIN mover handlers. Verificar que el catálogo sigue siendo idéntico.
4. Migrar UN módulo por dominio a fábricas. Riesgo: closures capturadas; mitigar con test de identidad de referencia a `runtime.tool_lock` antes/después.
5. Declarar 6 descripciones de `_describe_run_parameters` con `Field(description=...)` en `lifecycle.py`. Eliminar 2741-2770 y 7245.
6. Reescribir las 7 firmas de `_CLOSED_SCHEMA_TOOLS` a un `BaseModel(model_config=ConfigDict(extra="forbid"))` por herramienta; dejar el wrapper del top-5 en el mismo módulo. Riesgo: 7 cambios a la vez; mitigar 1 herramienta por commit.
7. Introducir `ToolEnvelope` + wrapper `wrap_tool` sin quitar 6 formas. Verificar: las 4 familias de tests de la sección D siguen en verde.
8. Reescribir 5 de los 6 sitios heterogéneos al sobre (no el 6º, `agent_loop.with_next_step`, que se deja como re-export).
9. Eliminar 4 de los 6 usos internos de `_tool_manager` que la partición deja huérfanos (986, 2755, 7249, 7251), manteniendo 7270 con `# VERIFICAR-API` hasta confirmar si existe hook público en `mcp==1.27.2`.
10. Borrar los 2 parcheadores declarables y mover 7244 a una aserción de enum de 1 línea.

## G. Valoración

- **Estructura: 3/10**. 63 tools y 4 parcheadores aplicados a mano dentro de `build_app` (7244-7248). La factoría no permite importar una sola herramienta para probarla sin construir la app entera (ver `effective_schema.py` 34-37 que sí lo hace).
- **Extensibilidad: 2/10**. Añadir una herramienta obliga a editar 2 rangos: el propio de la función y `build_app`, más `agent_loop.PUBLIC_NEXT_TOOLS` 12-32 y el `_INITIAL_CATALOG_NAMES` 632-652 si entra en el set compacto; la coherencia es manual.
- **Consistencia del contrato: 4/10**. Seis formas coexistentes (4742 código pelado, 805-824 next_step inyectado, 66-74 de `agent_loop` string embebido, 571-622 ready sin ok, 700-707 `args: {}`, 134-150 recetas string). La parte positiva es que los 4 parcheadores y 7244-7248 documentan por qué son necesarios en 2583-2588 y 2683-2687.
- **Acoplamiento a FastMCP: 3/10**. `app._tool_manager` y `app._mcp_server` en 8 sitios y 3 intenciones distintas (leer, mutar, re-bind de dispatcher). `tool.fn_metadata.call_fn_with_arg_validation` mutado con `object.__setattr__` (2588, 2662, 2689) es el acoplamiento más frágil de todos: escribes sobre el mecanismo de validación interno de la librería fijada a 1.27.2.

## Hallazgos

F01 | P0 | tools/dayz_mcp/server.py:4655 | estructura | `build_app` construye 63 closures y 4 parches ad-hoc, acoplando registro, closures y esquema post-registro en una función única | EVID: def build_app(config: ServerConfig) -> tuple[FastMCP, Any]: | FIX: extraer a `tools/registry.py` con `register_all(app, ToolContext(...))`
F02 | P0 | tools/dayz_mcp/server.py:2588 | acoplamiento FastMCP | El parcheador 1 muta `tool.fn_metadata.call_fn_with_arg_validation` con `object.__setattr__` | EVID: object.__setattr__(tool.fn_metadata, "call_fn_with_arg_validation", patched) | FIX: si `mcp` no publica hook, centralizar este patrón en un único decorator `with_argument_aliases`
F03 | P0 | tools/dayz_mcp/server.py:2680 | contrato | El enum de `mode` se lee del authority en build time y en runtime, por eso no es declarable con `Literal` | EVID: prop["enum"] = list(dayz_test_modes.public_mode_names()) | FIX: mover a un decorador propio `dynamic_enum(field="mode", source=dayz_test_modes.public_mode_names)`
F04 | P1 | tools/dayz_mcp/server.py:2770 | contrato | `_describe_run_parameters` es declarable 100% con `Field(description=...)` y no aporta valor como parche | EVID: prop["description"] = text | FIX: migrar a 6 `Annotated[..., Field(description=...)]` en la firma de `dayz_test_run`
F05 | P1 | tools/dayz_mcp/server.py:7270 | acoplamiento FastMCP | Re-bind del dispatcher `tools/list` interno para progressive disclosure | EVID: app._mcp_server.list_tools()(list_tools_progressive) | FIX: buscar hook público; si no existe, envolver `app.list_tools` y el dispatcher en un wrapper propio documentado
F06 | P1 | tools/dayz_mcp/server.py:7249 | acoplamiento FastMCP | `tool_pack` muta el registro por fuera con `app._tool_manager.remove_tool` | EVID: tool_pack_mod.apply_tool_pack(app._tool_manager, config.tool_pack) | FIX: filtrar dentro de `register_all` con el pack, no mutar FastMCP
F07 | P1 | tools/dayz_mcp/server.py:1234 | consistencia contrato | Hay un canal ad-hoc para atributos estructurados en ToolError (`error.object_id`) | EVID: error.object_id = object_id | FIX: definir `ToolErrorPayload` con `code/hint/args/object_id` como campos, usarlo en 1226-1235 y 2189/2329
F08 | P1 | tools/dayz_mcp/server.py:4742 | consistencia contrato | 215 `raise ToolError` con string plano; el código es el entero string | EVID: raise ToolError("bad_purpose") | FIX: un `raise code("bad_purpose")` con wrapper que pueble el sobre
F09 | P1 | tools/dayz_mcp/server.py:704 | consistencia contrato | `_next_public_call` emite siempre `args: {}` vacío, indistinguible de "sin args intencionales" | EVID: return {"tool": tool, "args": {}} | FIX: distinguir `None` de `{}` en `NextStep.args`
F10 | P2 | tools/dayz_mcp/agent_loop.py:71 | consistencia contrato | Recetas incrustan `next_step=<tool>` como sufijo string en lugar de un campo | EVID: token = f"next_step={name}" | FIX: migrar a campo del sobre; mantener la forma string como retrocompatibilidad
F11 | P2 | tools/dayz_mcp/server.py:1355 | consistencia contrato | `compute_bridge_ready` retorna dict con `ready/reason` sin `ok`, rompiendo la forma 2 | EVID: return {"ready": False, "reason": "no_run"} | FIX: al pasar al envelope común, añadir `ok: False` y `code: "not_ready"`
F12 | P2 | tools/dayz_mcp/server.py:685 | contrato | El truncado a 80 chars puede cortar a mitad de una frase pública | EVID: description[:_INITIAL_DESCRIPTION_LIMIT].rstrip() + "…" | FIX: truncar en límite de frase, no de carácter
F13 | P2 | tools/dayz_mcp/server.py:2644 | acoplamiento FastMCP | `_patch_closed_tool_schema` publica `additionalProperties=False` fuera de la firma | EVID: tool.parameters["additionalProperties"] = False | FIX: migrar a `BaseModel(model_config=ConfigDict(extra="forbid"))`
F14 | P2 | tools/dayz_mcp/effective_schema.py:35 | estructura | La auditoría de contrato construye toda la app para leer el schema final | EVID: app, _runtime = build_app(ServerConfig(log_sink=lambda _message: None)) | FIX: tras la partición, leer el catálogo desde el registry sin construir FastMCP
F15 | P3 | tools/dayz_mcp/server.py:7089 | consistencia contrato | 222 de 228 parámetros carecen de `description` en el JSON Schema (HECHO verificado), dejando a 8B callers con el string de la descripción global de la tool | EVID: def _bridge_status_description() -> str: | FIX: migrar 6 descripciones por commit a `Field(description=...)` con checklist por módulo de tools

## LO QUE NO PUDE VERIFICAR

- Que FastMCP 1.27.2 ofrezca un hook público para: `get_tool` post-registro, `remove_tool` en la cara pública, nombre de argumento público distinto del nombre de Python (F02/F06), y un decorator para reemplazar el dispatcher `tools/list` sin tocar `_mcp_server` (F05). [VERIFICAR-API]
- Que exista un serializer de `ToolError` que acepte dict o modelo estructurado en MCP 1.27.2 (D/Paso 1 y 4).
- Que `register_knowledge_tools` (4701) registre por el mismo canal `@app.tool`; solo veo la llamada, no el cuerpo del módulo `knowledge`.
- Los 4093 métodos de test a tocar, porque el material no incluye los ficheros de test.
- Si `app.add_tool(session_acquire_wait, name="lease_acquire", ...)` (4812-4816) es cara pública y si permite re-name + re-description sin duplicar la closure.

## ¿Qué puede estar mal en la premisa de este encargo?

- Un repo con 4.093 tests y 262 ficheros de test sobre 81 módulos probablemente no admite 15 commits "byte a byte idénticos" si el wrapper cambia el orden de llaves de los dict retornados (`_front_key` 1024-1032 muestra que el ORDEN es parte del contrato). El plan debería fijar el orden explícito, no solo las llaves.
- La auditoría de 2026-09-07 ya propuso consolidar y el repo fue en dirección contraria (64→81 módulos, 185→262 tests). Puede que la causa raíz no sea de estructura sino de que las 63 tools son 63 consumidores humanos distintos de 63 errores concretos (fichas fb-*/ae65/59d9); la partición sin disciplina de owner puede duplicar los 4 parcheadores a 4×N sitios.
- `_patch_mode_enum_from_authority` existe por un motivo funcional documentado (2666-2672): sustituir el authority sin reconstruir `build_app`. Cualquier plan "puramente declarativo" que rompa eso convierte un bug reportable en uno silencioso.