## RESULTADO
T1: HECHA — código, pruebas nuevas rojo/verde y suite de cierre OK.
T2: HECHA — código, pruebas nuevas rojo/verde y suite de cierre OK.
T3: NO TOCADA — no se modificó Enforce ni se afirma restauración completa.

## POR TAREA
### T1 (a9ae)
- `tools/dayz_mcp/server.py:3187`: política de tipos estrictos al registrar tools. Se cambiaron 115 anotaciones fuente a StrictInt/StrictFloat, incluidas listas y opcionales. `lease_acquire` hereda otra firma: 116 parámetros públicos endurecidos.
- Casos iniciales: `server.py:3435` wait_for_box_s, `server.py:4073` phase, `server.py:4271` time_multiplier.
- NUEVO `tools/tests/test_lote2_numeric_boundary.py:87`: matriz mediante app.call_tool; 310 sondas bool, sin alcanzar el handler. Otros tests conservan números válidos, opcionales y uso de handlers reales con bridge simulado.
- ROJO visto ANTES de editar server.py: `Ran 5 tests`, `FAILED (failures=304)`, mensaje `AssertionError: ToolError not raised`. Los seis subcasos ya estrictos pasaban. Log: `reviews/lote2/lote2_t1_red.log`.
- VERDE después: `Ran 5 tests`, `OK`. Log: `reviews/lote2/lote2_t1_green.log`.
- `tools/tests/test_wire_coercion_census.py`: retiradas excepciones numéricas reparadas del allowlist explícito, sin vaciar el censo ni desactivar su comparación. Sus antiguas pruebas exigían las coerciones defectuosas. Nuevas expectativas también se vieron rojas sobre el código previo: `reviews/lote2/lote2_census_red.log`.

### T2 (a2c0)
- `tools/dayz_mcp/steam_preflight.py:60`: nuevo steam_remediation_reason en el resultado de remediación, no en la evaluación normal.
- `steam_preflight.py:307`: sondeo con presupuesto monotónico; sleeps limitados al tiempo restante. No acepta un éxito observado después del plazo.
- `steam_preflight.py:342`: un motivo de fracaso fuerza steam_session_stale. Abortos de shutdown/relaunch no se promueven a éxito porque otra lectura resulte sana.
- `steam_preflight.py:401`: conserva el resultado observado dentro del sondeo. Se elimina la lectura posterior que podía convertir un timeout en éxito.
- `tools/dayz_mcp/dayz_test_tool.py:1412`: publica steam_remediated=false y steam_remediation_reason cuando no lo consigue; no lanza el cliente.
- NUEVO `tools/tests/test_lote2_t2_steam.py`: cinco tests con reloj/proveedor/launcher simulados. Comprueba espera de ActiveUser != 0 y PID vivo cuya imagen es steam.exe, timeout, éxito tardío, shutdown fallido y envelope sin lanzamiento.
- ROJO ANTES del arreglo: `Ran 5 tests`, `FAILED (failures=4)`. Mensajes: `AssertionError: None != 'steam_session_stale'` (éxito tardío y shutdown fallido) y `AssertionError: None != 'active_process_timeout'` (motivo ausente). Log: `reviews/lote2/lote2_t2_red.txt`.
- VERDE después: `Ran 5 tests`, `OK`; 17 tests existentes de steam_preflight también OK. Logs: `reviews/lote2/lote2_t2_green.txt`, `reviews/lote2/lote2_t2_existing.txt`.
- El test de espera de la condición compuesta YA pasaba antes: esa comprobación no es nueva.

### T3 (4f83)
No implementada. La rama Enforce sigue asignando result.ok=true. La comprobación Python de cámara ya existente no acredita controles/HUD/simulación.

## CENSO T1
Registro vivo con enable_exec_enforce=True: **61 tools, 217 parámetros del caller; 119 numéricos en 46 tools**: 68 float, 33 int, 18 listas float.
- Antes: 101 escalares numéricos; 3 ya estrictos (dayz_test_run.client_start_budget_s, key_press.dik, ui_click.button). Por tanto, **98 escalares vulnerables: los 3 del encargo y 95 parámetros MÁS**.
- Además, 18 listas admitían bool dentro del vector: familia ampliada de **116 parámetros vulnerables**, o **113 MÁS** que los 3 iniciales.
- Después: los 119 parámetros numéricos rechazan bool en el borde. Quedan **0 escalares/listas numéricos tipados de esta familia sin endurecer**. Las 4 tools knowledge no tienen parámetros numéricos.
- El alias lease_acquire cuenta como superficie pública separada; comparte declaración con session_acquire_wait. scene_raycast.from_pos se publica como `from`. Sin enable_exec_enforce se resta su timeout_s y una tool.
- Tabla completa de numéricos; los tres ya estrictos indicados arriba no son cambios nuevos. Las listas cuentan como un parámetro, no como tres coordenadas.

| Tool / línea | Parámetros numéricos finales |
|---|---|
| `action_use` (server.py:4966) | pos: list[StrictFloat] &#124; None; radius: StrictFloat; timeout_s: StrictFloat |
| `camera_get` (server.py:4443) | timeout_s: StrictFloat |
| `camera_set` (server.py:4393) | cam_pos: list[StrictFloat] &#124; None; cam_orientation: list[StrictFloat] &#124; None; look_at: list[StrictFloat] &#124; None; cam_matrix: list[StrictFloat] &#124; None; fov: StrictFloat; settle_ticks: StrictInt; timeout_s: StrictFloat |
| `capture_screenshot` (server.py:4539) | max_tokens: StrictInt; frames: StrictInt; quality: StrictInt |
| `dayz_test_run` (server.py:3416) | port: StrictInt; width: StrictInt; height: StrictInt; server_wait_s: StrictInt; wait_for_box_s: StrictFloat; client_start_budget_s: StrictFloat &#124; StrictInt &#124; None |
| `engine_set` (server.py:4669) | timeout_s: StrictFloat |
| `entities_query` (server.py:4229) | pos: list[StrictFloat]; radius: StrictFloat; limit: StrictInt; timeout_s: StrictFloat |
| `exec_enforce` (server.py:4636) | timeout_s: StrictFloat |
| `infected_drive` (server.py:4099) | pos: list[StrictFloat]; heading: StrictFloat &#124; None; speed: StrictFloat &#124; None; timeout_s: StrictFloat |
| `inventory_give` (server.py:4149) | timeout_s: StrictFloat |
| `key_press` (server.py:4496) | dik: StrictInt; timeout_s: StrictFloat |
| `lease_acquire` (server.py:3288) | max_wait_s: StrictFloat &#124; None |
| `logs_since` (server.py:3631) | max_lines: StrictInt |
| `notify_players` (server.py:3769) | show_time: StrictFloat; timeout_s: StrictFloat |
| `object_anim` (server.py:4069) | pos: list[StrictFloat] &#124; None; phase: StrictFloat &#124; None; object_id: StrictInt; timeout_s: StrictFloat |
| `object_delete` (server.py:3747) | object_id: StrictInt; timeout_s: StrictFloat |
| `object_inspect` (server.py:4180) | pos: list[StrictFloat] &#124; None; object_id: StrictInt; timeout_s: StrictFloat |
| `pipeline_inbox` (server.py:5108) | limit: StrictInt |
| `player_respawn` (server.py:4513) | timeout_s: StrictFloat |
| `player_teleport` (server.py:4035) | pos: list[StrictFloat]; timeout_s: StrictFloat |
| `query_all_players` (server.py:3602) | timeout_s: StrictFloat |
| `query_get_in_condition` (server.py:3894) | pos: list[StrictFloat]; component: StrictInt; timeout_s: StrictFloat |
| `query_player_state` (server.py:3597) | timeout_s: StrictFloat |
| `restore_gameplay` (server.py:4459) | timeout_s: StrictFloat |
| `scene_raycast` (server.py:3824) | from_pos: list[StrictFloat]; to: list[StrictFloat]; radius: StrictFloat; timeout_s: StrictFloat |
| `session_acquire_wait` (server.py:3288) | max_wait_s: StrictFloat &#124; None |
| `session_wait` (server.py:3271) | timeout_s: StrictFloat |
| `surface_query` (server.py:3938) | x: StrictFloat; z: StrictFloat; timeout_s: StrictFloat |
| `telemetry_read` (server.py:3866) | pos: list[StrictFloat] &#124; None; radius: StrictFloat; max_lines: StrictInt; timeout_s: StrictFloat |
| `ui_click` (server.py:4839) | button: StrictInt; timeout_s: StrictFloat |
| `ui_dialog` (server.py:4943) | timeout_s: StrictFloat |
| `ui_focus` (server.py:4918) | timeout_s: StrictFloat |
| `ui_reload_layout` (server.py:4879) | limit: StrictInt; timeout_s: StrictFloat |
| `ui_set_text` (server.py:4809) | timeout_s: StrictFloat |
| `ui_tree` (server.py:4782) | limit: StrictInt; timeout_s: StrictFloat |
| `vehicle_control` (server.py:4698) | throttle: StrictFloat; steer: StrictFloat; brake: StrictFloat; handbrake: StrictFloat; hold_ttl_s: StrictFloat; timeout_s: StrictFloat |
| `vehicle_enter` (server.py:3813) | pos: list[StrictFloat]; timeout_s: StrictFloat |
| `vehicle_get_in_client` (server.py:4655) | pos: list[StrictFloat]; timeout_s: StrictFloat |
| `vehicle_prepare_fixture` (server.py:3911) | pos: list[StrictFloat]; radius: StrictFloat; timeout_s: StrictFloat |
| `vehicle_release` (server.py:4771) | timeout_s: StrictFloat |
| `vehicle_telemetry` (server.py:4726) | timeout_s: StrictFloat |
| `vehicle_trace` (server.py:4733) | cursor: StrictInt; limit: StrictInt; sample_hz: StrictInt; max_samples: StrictInt; timeout_s: StrictFloat |
| `wait_for` (server.py:5022) | value: StrictInt; timeout_s: StrictFloat; poll_interval_s: StrictFloat; lookback_lines: StrictInt |
| `world_spawn` (server.py:3727) | pos: list[StrictFloat]; flags: StrictInt; rotation: StrictInt; timeout_s: StrictFloat |
| `world_time_set` (server.py:4265) | year: StrictInt; month: StrictInt; day: StrictInt; hour: StrictInt; minute: StrictInt; time_multiplier: StrictFloat &#124; None; timeout_s: StrictFloat |
| `world_weather_set` (server.py:4338) | overcast: StrictFloat &#124; None; rain: StrictFloat &#124; None; fog: StrictFloat &#124; None; time: StrictFloat; min_duration: StrictFloat; timeout_s: StrictFloat |

## SUITE
Primer pase con `/mnt/c/Python314/python.exe -u -m unittest discover -s tools/tests -t .`:
`Ran 3079 tests in 287.281s`
`FAILED (failures=8, skipped=12)`
Log: `reviews/lote2/lote2_suite.log`.
Incluyó intérprete no aprobado (guard y fixtures de daemon), censo con allowance heredado lease_acquire (ya corregido), y un inspect.getsource de playbook mientras server.py cambiaba. No se declara este pase como cierre estable.
Segundo pase con intérprete aprobado: `Ran 3080 tests in 319.015s`, `FAILED (errors=1, skipped=10)`.
Único error: importación de fixtures en el nuevo test T2 (`No module named 'test_steam_preflight'`). Corregida a `tools.tests`; no se modificó código de producción tras ese pase. Log: `reviews/lote2/lote2_suite_final.log`.
Comprobación focal posterior: `tools/.venv-mcp/Scripts/python.exe -u -m unittest tools.tests.test_lote2_t2_steam tools.tests.test_lote2_numeric_boundary tools.tests.test_wire_coercion_census`: `Ran 37 tests in 1.762s`, `OK`. Log: `reviews/lote2/lote2_focused_final.log`.
**CIERRE DEFINITIVO** con código estable y el intérprete aprobado:
`tools/.venv-mcp/Scripts/python.exe -u -m unittest discover -s tools/tests -t .`
```text
Ran 3084 tests in 304.246s
OK (skipped=11)
```
Código de salida: **0**. Log: `reviews/lote2/lote2_suite_closure.log`; salida: `reviews/lote2/lote2_suite_closure.exit`.
No apareció como fallo el test inestable indicado por el encargo. Este pase no reportó app_module_drift; no se reselló el bundle ni se afirma haberlo acreditado para lanzamiento real.

## DECISIONES
- Se priorizaron T1 y T2; T3 queda explícitamente abierta.
- StrictFloat conserva enteros JSON y cero. StrictInt rechaza floats, incluso 1.0. Ambos rechazan cadenas numéricas: endurecimiento deliberado que puede exigir corregir callers que enviaban strings.
- No se cambiaron rangos/defaults: el cero numérico explícito sigue siendo válido donde lo era. El arreglo no prohíbe congelar deliberadamente la simulación con 0; evita que false lo haga por coerción.
- T2 conserva presupuestos existentes de 15 s para shutdown y 20 s para ActiveProcess. La enumeración de PIDs sigue siendo diagnóstica; la acreditación usa process_exists e imagen del PID registrado. No se exige estabilidad posterior ni se promete arranque de DayZ.
- No se usó git, no se reselló bundle ni se tocaron launchers. Los tests de fixtures de la suite son los del proyecto; no se arrancaron servicios manualmente.
- Escrituras propias releídas y verificadas sin NUL. Informes detallados: reviews/lote2/lote2_t1_report.md y lote2_t2_report.md.

## LO QUE NO PUDE VERIFICAR
- No se probó Steam, DayZ ni DayZDiag reales ni se compiló Enforce. T3 sigue abierta.
- No se reproduce ni se corrige el fallo de cliente con clave Steam sana (0155/3290/6665). La condición registral es necesaria según el contrato, no una prueba de disponibilidad real para DayZ.
- Un proveedor/WinAPI bloqueado puede exceder el presupuesto: el reloj acota sondeo/sleeps, no interrumpe una llamada bloqueada.
- El censo cubre firmas numéricas tipadas, no números ocultos en dict[str, Any] ni coerciones de permisos bool; quedan fuera de esta familia.
- **¿Qué puede estar mal en la premisa de este encargo?** T2 ya tenía sondeo compuesto en este árbol; no se demostró que faltara esa espera en la reproducción histórica. Se reprodujeron caminos concretos que ignoraban el resultado del timeout o declaraban éxito tras abortar la remediación. Una clave sana no demuestra que Steam sirva al cliente. En T3, que una restauración no cambie nada tampoco sería por sí solo un fallo si la poscondición ya estuviera satisfecha; aquí no se comprobó esa poscondición.
