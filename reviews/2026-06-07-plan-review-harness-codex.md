### Resumen ejecutivo
- Veredicto: approve with minor changes.
- El contrato productor-consumidor principal del bridge esta alineado: `MCPArgs`, `MCPResult`, campos B1/B2/B3, ticks, whitelist, `MAX_QUEUE`, `MAX_PENDING` y el launch base de `run-poc.ps1` existen donde el plan dice.
- No veo un FAIL que obligue a replantear el harness, pero hay 4 WARN que conviene cerrar antes de implementar: dos son de cobertura/semantica del cliente y dos evitan regresion/falso FAIL.
- B3 queda correctamente fuera de `overall_pass`; el riesgo principal esta en que S4 y los negativos de B1 se implementen con el cliente actual sin endurecer el manejo de HTTP esperado.

### Matriz de hallazgos
| ID | Seccion plan | Severidad | Resumen | Resolucion sugerida |
|---|---|---|---|---|
| HR22-001 | sec.S1-neg / sec.Contrato errores | WARN mayor | Los negativos B1 no cubren todo lo que exige el plan padre: `bad_flags` esta marcado opcional y `args no-objeto` se descarta aunque es alcanzable como 400 HTTP del server. | Hacer `bad_flags` obligatorio y agregar un negativo HTTP-layer `args:null` / no-dict esperado como 400 `bad_args`, sin esperar `MCPResult`; o cambiar explicitamente el plan padre. |
| HR22-002 | sec.S4 Backpressure | WARN mayor | El 429 esperado puede salir como excepcion si se implementa con `Client.request_json()` / `enqueue()` actuales. | Definir un enqueue S4-safe que trate 429 como dato esperado (`status/error`) y preserve ids OK previos; usar excepcion solo para HTTP inesperado. |
| HR22-003 | sec.Pieza 1 `mcp_client.py` | WARN mayor | La frase "pasa a subcomando poc (o default)" deja ambigua la retrocompatibilidad del CLI certificado A1-A5. | Fijar en el plan que la invocacion sin subcomando actual sigue funcionando; `phase1` es opt-in y `poc` puede ser alias, no requisito. |
| HR22-004 | sec.S1 fallback de coche | WARN mayor | Si falla `Hatchback_02` y entra un fallback, la aceptacion `type==CAR_CLASS` puede dar falso FAIL si `CAR_CLASS` sigue siendo el valor primario. | Nombrar `selected_car_class` / `attempted_class` y comparar `result.type` contra ese valor; guardar ese valor en el verdict. |

### Hallazgos detallados

#### HR22-001 - Negativos B1 incompletos respecto al plan padre
Seccion plan: sec.S1-neg, lineas 82-87; sec.Contrato errores, linea 37.

Cita del plan: S1-neg prueba `unknown_type`, `bad_pos` por pos corta, `bad_pos` por pos ausente y deja `bad_flags` como opcional. Tambien dice que `bad_args` no se testea como negativo porque no seria reproducible por el harness.

Problema: el plan padre exige `args no-objeto` y `flags fuera de allowlist` como criterios de validacion de 0.5 (`DayZ_MCP_dev/plans/2026-06-07-fase1-control.md:126`). El bridge valida `bad_flags` antes de `CreateObjectEx` (`DayZ_MCP/scripts/5_Mission/MCPBridge.c:581-587`) y solo acepta `ECE_PLACE_ON_SURFACE` (`DayZ_MCP/scripts/5_Mission/MCPBridge.c:654-661`), asi que el caso `flags:999` debe ser obligatorio, no opcional. Para `bad_args`: es correcto que no llega al bridge como `MCPResult` si falta `args`, porque el server usa default `{}` (`DayZ_MCP_dev/tools/mcp_server.py:120`); pero si el cliente manda `args:null` o no-dict, el server HTTP devuelve 400 `bad_args` (`DayZ_MCP_dev/tools/mcp_server.py:120-123`). Eso si es reproducible por el harness a nivel HTTP y cubre el criterio del plan padre sin mutar mundo.

Propuesta: convertir `bad_flags` en caso obligatorio de B1-neg y agregar un caso HTTP-layer `world_spawn` con `args:null` esperado como `status=400,error="bad_args"`, sin `/await` posterior. Si se decide no cubrirlo, el plan padre debe quitar o mover ese criterio.

#### HR22-002 - El 429 esperado de S4 necesita camino de cliente que no explote por excepcion
Seccion plan: sec.S4 Backpressure, lineas 100-105; sec.Pieza 1, lineas 55-56.

Cita del plan: S4a debe encolar una rafaga hasta recibir un 429 y contarlo como aceptacion; Pieza 1 propone reusar `Client` y anadir `enqueue_cmd`.

Problema: el cliente actual tiene `request_json()` con `urllib.request.urlopen()` sin catch (`DayZ_MCP_dev/tools/mcp_client.py:46-49`), y `enqueue()` lo usa directamente (`DayZ_MCP_dev/tools/mcp_client.py:85-87`). El 429 que S4 espera se emite en `_handle_enqueue` (`DayZ_MCP_dev/tools/mcp_server.py:125-128`), pero con el patron actual aparece como `urllib.error.HTTPError` y puede tumbar el run o producir top-level error en vez de dato de S4. `raw_status()` si captura `HTTPError` (`DayZ_MCP_dev/tools/mcp_client.py:76-83`), pero solo devuelve status y no modela ids OK / error body para el verdict.

Propuesta: especificar en el plan un helper S4-safe, por ejemplo `enqueue_cmd_status(cmd,args)->{status,id,error}`, que use `request_json` para 200 y capture `HTTPError` para 429. El verdict debe registrar `queue_full_429_seen`, `ok_before_429` y continuar esperando los ids aceptados.

#### HR22-003 - Ambiguedad de subparsers puede romper A1-A5
Seccion plan: sec.Pieza 1, linea 54.

Cita del plan: el modo POC actual "pasa a subcomando `poc` (o se mantiene como default por retrocompat)".

Problema: el CLI actual no tiene subcommands; parsea `--port`, `--keyfile`, `--spawn`, `--output`, `--timeout` directamente (`DayZ_MCP_dev/tools/mcp_client.py:277-284`). `run-poc.ps1` certificado lo invoca sin subcomando (`DayZ_MCP_dev/tools/run-poc.ps1:673-676`). Si la implementacion elige la rama "pasa a subcomando `poc`" sin conservar el default, rompe A1-A5 sin tocar `run-poc.ps1`, que el propio plan declara fuera de alcance.

Propuesta: cambiar la decision a: "la ruta sin subcomando conserva exactamente el POC actual; `phase1` se agrega como subcomando opt-in; `poc` puede ser alias opcional". La validacion minima es que la invocacion actual de `run-poc.ps1` siga parseando.

#### HR22-004 - Fallback de clase de coche puede generar falso FAIL
Seccion plan: sec.S1 world_spawn, lineas 78-80.

Cita del plan: `CAR_CLASS = "Hatchback_02"`; si falla, probar fallbacks hasta un `ok`; aceptacion B1: `ok && found==true && type==CAR_CLASS`.

Problema: el bridge devuelve `result.type = job.args.type` para B1 (`DayZ_MCP/scripts/5_Mission/MCPBridge.c:1116-1120`). Si el intento que finalmente spawnea es, por ejemplo, `Sedan_02`, la aceptacion `type==CAR_CLASS` solo es correcta si `CAR_CLASS` se actualiza al intento seleccionado. El plan no lo dice. Las clases de la lista existen y son spawnables `scope=2`: `Hatchback_02` (`DZ/vehicles/wheeled/config.cpp:9463-9465`), `Sedan_02` (`DZ/vehicles/wheeled/config.cpp:13929-13931`), `Offroad_02` (`DZ/vehicles/wheeled/config.cpp:20831-20833`), `CivilianSedan` (`DZ/vehicles/wheeled/config.cpp:5098-5100`) y `OffroadHatchback` (`DZ/vehicles/wheeled/config.cpp:1242-1244`). El problema no es la lista; es el nombre de la variable/criterio.

Propuesta: definir `selected_car_class` desde el intento que dio `ok`, guardar ese valor en `B1_world_spawn.car_class`, y aceptar `result.type == selected_car_class`.

### Cobertura
- Escenarios R5 cubiertos: B1 positivo, B2, B3-probe como DATO fuera de gate, backpressure S4a/S4b/S4c.
- Escenarios R5 parcialmente cubiertos: B1 negativos. `unknown_type` y `bad_pos` estan cubiertos; `bad_flags` debe ser obligatorio y `args no-objeto` debe cubrirse como negativo HTTP 400 o eliminarse del criterio padre.
- Citas del contrato verificadas: `MCPArgs` existe con `type,pos,flags,rotation,seat,throttle,duration` (`DayZ_MCP/scripts/5_Mission/MCPMessages.c:8-22`); `MCPResult` contiene los campos citados (`DayZ_MCP/scripts/5_Mission/MCPMessages.c:52-71`); `query_player_state` postea `ok/state` (`DayZ_MCP/scripts/5_Mission/MCPBridge.c:329-341`) y construye `name,pos` (`DayZ_MCP/scripts/5_Mission/MCPBridge.c:1199-1231`); B1 postea `type,found,pos_real` (`DayZ_MCP/scripts/5_Mission/MCPBridge.c:1116-1130`); B2 postea `seated,seat="driver"` (`DayZ_MCP/scripts/5_Mission/MCPBridge.c:1133-1144`); B3 postea `vehicle_fixture_ready,engine_on_server,speedo_max,pos_delta,net_strategy` (`DayZ_MCP/scripts/5_Mission/MCPBridge.c:1146-1161`); `net_strategy` codifica NONE/LATEST/PHYSICS/desconocido como 0/1/2/-1 (`DayZ_MCP/scripts/5_Mission/MCPBridge.c:1082-1100`).
- Server verificado: whitelist (`DayZ_MCP_dev/tools/mcp_server.py:14`), reject no-whitelist (`DayZ_MCP_dev/tools/mcp_server.py:115-118`), `MAX_QUEUE=64` (`DayZ_MCP_dev/tools/mcp_server.py:15`), 429 `queue_full` (`DayZ_MCP_dev/tools/mcp_server.py:125-128`), `args` default `{}` + no-dict 400 `bad_args` (`DayZ_MCP_dev/tools/mcp_server.py:120-123`), `set_poll_delay` rango 0..5000 (`DayZ_MCP_dev/tools/mcp_server.py:201-215`).
- Backpressure verificado: bridge drena `m_Pending` antes de poll (`DayZ_MCP/scripts/5_Mission/MCPBridge.c:78-89`), dispatch cap 4 por tick en batch y pending (`DayZ_MCP/scripts/5_Mission/MCPBridge.c:224-237`, `DayZ_MCP/scripts/5_Mission/MCPBridge.c:240-255`), `MAX_PENDING` produce `bridge_queue_full` (`DayZ_MCP/scripts/5_Mission/MCPBridge.c:269-272`).
- Launch fase 0 verificado: `run-poc.ps1` copia mission completa (`DayZ_MCP_dev/tools/run-poc.ps1:514-525`), escribe `init.c` con spawn fijo y marker `[MCP-POC]` (`DayZ_MCP_dev/tools/run-poc.ps1:527-559`), build PBO `-packonly` y marker check (`DayZ_MCP_dev/tools/run-poc.ps1:171-213`, `DayZ_MCP_dev/tools/run-poc.ps1:593-597`), lanza server+client DayZDiag (`DayZ_MCP_dev/tools/run-poc.ps1:608-627`).

### Proximo paso
Claude aplica los 4 WARN en el plan del harness y lo reenvia. Si esos cambios quedan incorporados, la implementacion puede empezar sin tocar el bridge ni `run-poc.ps1`.