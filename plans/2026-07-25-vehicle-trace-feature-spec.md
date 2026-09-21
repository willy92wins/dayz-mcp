# Feature Spec: `vehicle_trace` atómico owner-client

**Mod / PBO**: `DayZ_MCP.pbo`  
**Date**: 2026-07-25  
**Status**: READY — R22/R26 cerrado tras autorización explícita  
**Plan**: `plans/2026-07-25-vehicle-trace-atomic-instrumentation.md`

**Enmienda autorizada 2026-07-26**: el gate live requiere acreditar
`DayZ_MCP` como proyecto propio en la policy sellada `dayz-test-v1`; queda
prohibido reutilizar la identidad de otro mod.

**Enmienda autorizada 2026-07-26 — wire/rate live**: Enforce serializa los
bools del DTO como `0|1`; el boundary host los convierte de forma exhaustiva y
fail-closed al bool JSON del schema. El curso live canónico solicita 20 Hz tras
medir 22,467774 Hz reales; no cambia el contrato genérico SC-005 ni sus
negativos de 30 Hz.

## Context / Why

La telemetría puntual actual mezcla observaciones separadas y no puede decidir contactos sub-0,25 s ni readback de control aplicado. La feature entrega un único stream owner-client, monotónico y fail-closed para habilitar la viabilidad de H1–H6 sin tocar `MERCEDES_AMGLF`.

## Acceptance Scenarios

1. **Given** un cliente sentado como conductor/owner de un `CivilianSedan` preparado y una lease activa, **When** se ejecuta `vehicle_trace(mode="start")`, se aplican controles con `vehicle_control`, se detiene y se leen todos los chunks, **Then** cada muestra pertenece al mismo coche y reloj, `is_owner=true`, sequence es continuo, la frecuencia efectiva es al menos 20 Hz y los controles aplicados proceden de getters reales. `is_authority_owner` se conserva como diagnóstico y no es un segundo requisito de ownership.
   - **Repro in-game**: arrancar un run aprobado con `CivilianSedan`; adquirir lease; entrar como conductor; start a 20 Hz; aplicar throttle y steer; stop; read de 64 en 64; verificar schema, ownership, movimiento y readback.

2. **Given** el mismo control con el trace activo, **When** el cuerpo del `CivilianSedan` produce al menos un `OnContact` no-wheel, **Then** el stream conserva count, zone, impulso, posición local, normal y penetración de la evidencia owner-client.
   - **Repro in-game**: contacto controlado del chasis con obstáculo/terreno; esperar al menos un intervalo; stop/read; exigir `body_contact_count>0`. Si el callback no aparece en owner client, gate `RED`.

3. **Given** un trace activo, **When** se libera o expira la lease, se llama `vehicle_release` o se apaga el bridge, **Then** no queda control ni trace activo o legible con un id stale.
   - **Repro in-game**: start; liberar la lease sin stop explícito; confirmar cleanup interno `vehicle_release`; un read con el id anterior falla cerrado.

4. **Given** fixtures offline con overflow, gap, regresión de reloj, frecuencia insuficiente, campo ausente o steer solicitado distinto del aplicado, **When** se ejecuta el verificador, **Then** devuelve `STOP`/exit 2 y nunca sintetiza un PASS parcial.
   - **Repro in-game**: no aplica; fixtures deterministas offline antes del primer build.

5. **Given** un trace completo y metadata lifecycle real, **When** el bundler host genera el artefacto, **Then** calcula —no acepta declarados— SHA-256 de trace, schema, course, PBO, RPT y script log, conserva `run_id` y las identidades completas de proceso y produce bytes deterministas.
   - **Repro in-game**: tras el control live, exportar chunks y snapshot lifecycle; ejecutar bundler dos veces; SHA-256 de ambos outputs idéntico.

6. **Given** el launcher sellado sin una entrada `DayZ_MCP`, **When** se solicita
   PACKONLY/live para este proyecto, **Then** el control previo demuestra
   `bad_project`; tras añadir únicamente sus raíces exactas y reconstruir el
   bundle reproducible, la misma petición se acepta y un proyecto no listado
   continúa fallando cerrado.
   - **Repro host**: test RED contra el builder previo; test GREEN de
     `build_run_request(project="DayZ_MCP", build=true, pack_only=true)` más
     control negativo `DayZ_MCP_OutsidePolicy`; tres builds reproducibles,
     verificación del bundle/loader registrado y reemplazo CAS del registro con
     receipt/rollback.

## Success Criteria

- **SC-001**: una única tool `vehicle_trace` expone exactamente `start|status|stop|read|clear`; `start` genera un `trace_id` UUID4 hex de 32 caracteres y todos los demás modos exigen ese id exacto.
- **SC-002**: `sample_hz` admite enteros 20–60; `max_samples` admite 2–8192; todos los DTO de muestra se instancian fuera de los hooks al hacer start, el buffer es append-only y overflow detiene el trace como incompleto. `Reserve()` por sí solo no cuenta como preasignación de objetos.
- **SC-003**: `read` admite cursor 0..count y chunks 1..64; nunca sobrescribe ni reordena muestras; `next_cursor` y `eof` son deterministas.
- **SC-004**: cada muestra v1 contiene los campos obligatorios de reloj, pose/dirección/velocidad, control solicitado+aplicado, cuatro contactos+velocidades angulares, motor/marcha/ruedas, `IsOwner()`/`IsAuthorityOwner()`/net id, wheel loss y evidencia `OnContact`; solo `IsOwner()` es requisito para iniciar y continuar el trace owner-client.
- **SC-005**: sequence empieza en 0 y es contiguo; reloj estrictamente creciente; `sample_dt_s` concuerda con la diferencia de reloj a ±0,001 s; máximo gap ≤ `1,5/sample_hz`; frecuencia efectiva ≥ `max(20, 0,9*sample_hz)` Hz.
- **SC-006**: para muestras con `control_active=true`, error absoluto solicitado↔aplicado por canal ≤0,001; un canal ausente o divergente es `STOP`.
- **SC-007**: no existe `new` dentro de `CarScript.OnInput` ni `CarScript.OnContact`; `OnContact` no retiene el objeto `Contact`, copia únicamente primitivas y el muestreo ocurre después del único punto de salida del intento de aplicar control.
- **SC-008**: spinout = ángulo horizontal direction↔velocity ≥45° durante ≥0,25 s a ≥30 km/h; rollover = `|roll|≥60°` durante ≥0,50 s; grounding = al menos un evento `OnContact` no-wheel durante la ventana H5. Las tres derivaciones se recalculan offline.
- **SC-009**: un pulso de `WheelHasContact=false` de 0,10 s se reconstruye con error ≤ un intervalo de muestra.
- **SC-010**: release/expiry/shutdown invalida el trace y limpia control; `vehicle_trace` no entra en `READ_ONLY_COMMANDS`.
- **SC-011**: bridge/Python suben juntos de v5 a v6; una mezcla v5/v6 queda `version_blocked`.
- **SC-012**: PBO PACKONLY compila; el curso live canónico solicita 20 Hz y el control sobre `CivilianSedan` produce ≥2 s, ≥20 Hz efectivos, ownership estable y al menos un contacto corporal owner-client; cero error de compilación/script atribuible a `DayZ_MCP`.
- **SC-013**: el artefacto incluye hashes calculados de trace/schema/course/PBO/RPT/script-log, `run_id`, identidades `pid+creation_time+executable_sha256+command_line_sha256+role+identity_scheme`, checks y `artifact_sha256` determinista.
- **SC-014**: `dayz-test-v1` contiene exactamente una entrada `DayZ_MCP` con
  `dev_root=P:\DayZ_MCP_dev`, `default_source=P:\DayZ_MCP`, cero base mods,
  mission root canónico y `mod_roots=[P:\Mods]`; `worker-runtime.json` refleja
  la misma identidad. El control previo falla por `bad_project`, el control
  posterior acepta PACKONLY, un proyecto no listado sigue rechazado, los tres
  fingerprints reproducibles coinciden y el registro queda reemplazado por CAS
  con rollback verificable.

## Scope — Out of scope

- No modificar PBO, config, modelo, scripts, handling ni staging de `MERCEDES_AMGLF`.
- No ejecutar S2, tuning H1–H6, VPP, HID/input crudo, telemetría server-side ni persistencia del trace.
- No buffer circular, streaming push, compresión, base de datos, rediseño del broker ni refactor adyacente.
- No declarar comportamiento de engine por tests estáticos: build y control live siguen siendo gates obligatorios.
- No añadir, ensanchar ni relajar la policy de ningún otro proyecto; la
  enmienda se limita a la identidad exacta `DayZ_MCP`.

## Assumptions

- **ASSUMED-LIVE-GATE**: `CarScript.OnContact` entrega eventos útiles en la VM owner-client. Se difiere porque solo el engine vivo puede probarlo; ausencia = `RED` y rollback, no relajación del criterio.
- **ASSUMED-LIVE-GATE**: `JsonSerializer` serializa un chunk de 64 muestras v1 dentro del transporte existente. Se prueba en build+live; fallo = `RED`.
- **RESOLVED**: el trace es mutante/lease-gated por default y start debe marcar `vehicle_active`; ver `DayZ_MCP_dev/tools/dayz_mcp/session_coordination.py:18-37`, `:1290-1293`.
- **RESOLVED**: vertical nativa es `y`; spinout usa el plano `x/z` y `GetDirection()` real, no una reconstrucción de Euler.
- **RESOLVED**: el conductor se verifica con `HumanCommandVehicle.GetVehicleSeat()` y `DayZPlayerConstants.VEHICLESEAT_DRIVER`; ver `P:/scripts/3_game/human.c:696`, `P:/scripts/3_game/dayzplayer.c:674` y el precedente compilable `DayZ_MCP/scripts/5_Mission/MCPClientBridge.c:1003-1007`.
- **RESOLVED**: `Pawn.IsOwner()` significa simulado por el owner; `IsAuthorityOwner()` significa simulado por authority sin owner y por tanto es diagnóstico, no condición acumulativa; ver `P:/scripts/3_game/entities/pawn.c:193-200`.
- **RESOLVED**: cada `read` es un comando MCP nuevo con id propio y un único resultado; no reutiliza ni sobrescribe el id de un chunk anterior. El slot por comando se materializa en `DayZ_MCP_dev/tools/dayz_mcp/loopback.py:730-764`.
- **RESOLVED**: start/status/stop/read son llamadas acotadas e independientes; ninguna retiene el operation pin durante la duración del trace. La lease se mantiene por el heartbeat/TTL existente.
- **RESOLVED**: la selección pública exige una coincidencia única y falla
  `bad_project` si no existe; ver
  `DayZ_MCP_dev/tools/dayz_mcp/dayz_test_tool.py:65-72`.
- **RESOLVED**: request policy y runtime se generan desde
  `DayZ_MCP_dev/tools/build_native_launcher.py:399-562` y `:563-663`; el registro
  se reemplaza y revierte mediante las rutas CAS verificadas en
  `DayZ_MCP_dev/tools/dayz_mcp/launcher_registry_update.py:249-256` y
  `:412-417`.

## Forward Contract (R8-extended)

| Consumer | Symbol it reads | Kind | Verify status |
|---|---|---|---|
| `MCP_CarScript.c` | `CarScript.OnInput(float dt)` | override | `DayZ_MCP/scripts/4_World/MCP_CarScript.c:603-664`; hook implementado; base cargada y compilada |
| `MCP_CarScript.c` | `Transport.OnContact(string,vector,IEntity,Contact)` | override | base `P:/scripts/3_game/vehicles/transport.c:252`; hook implementado en `DayZ_MCP/scripts/4_World/MCP_CarScript.c:666-670` |
| capturador | getters de control/rueda | API | `P:/scripts/3_game/vehicles/car.c:193-217`, `:290-297`, `:349-352` |
| capturador | pose/direction/net/ownership/clock | API | `P:/scripts/3_game/entities/object.c:293-320`, `:815`; `pawn.c:194-209`; `P:/scripts/3_game/global/game.c:913` |
| capturador | `GetVelocity(IEntity)` | API global | `P:/scripts/1_core/proto/enphysics.c:104` |
| capturador | campos `Contact` | DTO engine | `P:/scripts/1_core/physics/contact.c:9-49` |
| bridge client | `HumanCommandVehicle.GetVehicleSeat()` + `VEHICLESEAT_DRIVER` | API/constante | `P:/scripts/3_game/human.c:696`; `P:/scripts/3_game/dayzplayer.c:674`; uso del trace `DayZ_MCP/scripts/5_Mission/MCPClientBridge.c:826-834` |
| bridge client | `MCPVehicleTrace`, `MCPVehicleTraceSample`, `MCPVehicleTraceRead` | clases nuevas | `[EXACT] DayZ_MCP/scripts/4_World/MCP_CarScript.c:29,87,115; dispatch en DayZ_MCP/scripts/5_Mission/MCPClientBridge.c:489-491,793-929` |
| host/daemon | comando `vehicle_trace` | comando nuevo | `[EXACT] tool tipada y normalización wire fail-closed en DayZ_MCP_dev/tools/dayz_mcp/server.py:1389-1420 y vehicle_trace.py:204-233` |
| bundler | schema `dayz-mcp-vehicle-trace-v1` | contrato nuevo | `[EXACT] DayZ_MCP_dev/tools/schemas/vehicle-trace-v1.json:3,28; validator/bundler en vehicle_trace.py:282-680,1072-1128` |
| launcher sellado | `_build_request_policy()` + `_build_worker_runtime()` | generadores existentes | `DayZ_MCP_dev/tools/build_native_launcher.py:399-663`; entrada `DayZ_MCP` añadida por TDD |
| selección pública | `_selected_policy()` | contrato fail-closed existente | `DayZ_MCP_dev/tools/dayz_mcp/dayz_test_tool.py:65-72` |
| registro launcher | `install_dayz_test_v1()` + `rollback_last_registry_transition()` | CAS/rollback existentes | `DayZ_MCP_dev/tools/dayz_mcp/launcher_registry_update.py:249-256`, `:412-417` |

## Verification plan

| Criterion | Verification | Where |
|---|---|---|
| SC-001–SC-006, SC-008–SC-011, SC-013 | pytest/unittest sobre fixtures positivas, mutaciones negativas y fake peer real | offline |
| SC-007 | test estructural sobre funciones + revisión Enforce + compilación DayZDiag | offline/build |
| SC-010 | tests `SessionCoordinator` y cleanup loopback; postflight `session_status` | offline/live |
| SC-011 | tests de handshake existentes + bridge status post-deploy | offline/live |
| SC-012 y escenarios 1–3 | un run aprobado `CivilianSedan`, logs y trace crudo | in-game |
| escenario 4 | demostrar RED exacto antes de implementar y GREEN después | offline TDD |
| escenario 5 | bundler dos veces y comparación byte/SHA | offline post-live |
| SC-014 y escenario 6 | RED `bad_project`; GREEN positivo/negativo; tres builds; bundle/registry/receipt/rollback | offline antes de PACKONLY |

## Open questions / NEEDS CLARIFICATION

- Ninguna decisión de producto abierta. Los dos unknowns de engine están convertidos en gates live fail-closed.
