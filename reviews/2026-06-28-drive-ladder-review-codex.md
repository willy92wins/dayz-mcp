# R21 adversarial - drive_ladder.py

## Bloque A - Resumen ejecutivo

**VEREDICTO: UNSOUND.**

El orquestador no esta listo para estrenar SUB_BRZ in-game como cadena R1->R6. En su forma actual puede dar PASS falso en R3, R4 y R5, y en un re-run puede medir/controlar un coche ya ocupado de una pasada anterior mientras el verdict se atribuye al SUB_BRZ recien spawneado.

Scope revisado: `C:\Users\guill\.claude\skills\dayz-mcp-verify\references\drive_ladder.py` contra las secciones `DRIVABILITY` y `ESCALERA DE ACEPTACION` de `C:\Users\guill\.claude\skills\dayz-mcp-verify\SKILL.md`, comparado con los gate drivers `tramoA_gate_driver.py`, `tramoA_verbs_gate.py`, `tramoB_getin_gate.py`, y con campos wire verificados en `MCPBridge.c`, `MCPClientBridge.c`, `MCPMessages.c` solo donde el hallazgo lo requeria.

## Bloque B - Matriz de hallazgos

| ID | file:line | Severidad | Resumen | Fix sugerido |
|---|---:|---|---|---|
| DL-001 | `drive_ladder.py:202,205` | FAIL/P1 | R3 da PASS con cualquier asiento disponible; el run Tramo B ya mostro conductor `crew_index=0` unreachable y pasajero available. | Gatear R3 sobre `component_crew_index==0 && available`, conservar `first_block` del conductor, y corregir la prosa de la skill/runbook para decir conductor, no "un componente". |
| DL-002 | `drive_ladder.py:219-224` | FAIL/P1 | R4 pasa con `seated` solamente, aunque el contrato exige `seated + is_owner + vehicle_fixture_ready` y el bridge devuelve esos campos. | R4 debe fallar si `ok` no es true, `_timeout`, `seated` false, `seat!="driver"`, `is_owner` no true o `vehicle_fixture_ready` no true. |
| DL-003 | `drive_ladder.py:161-218,271` | FAIL/P1 | En re-run, si el player ya esta sentado, `vehicle_get_in_client(pos)` acepta el coche actual y R5 mide el coche viejo, no el SUB_BRZ nuevo. | Preflight fail-closed si ya esta en vehiculo, o validar identidad/pos/net_id del coche sentado contra el spawn antes de R5; un `get_out` seria cambio de scope/runbook. |
| DL-004 | `drive_ladder.py:228-239` | FAIL/P1 | R5 puede PASS con movimiento por gravedad/inercia porque ignora `engine_on_server`, `engine_set.ok` e `is_owner`. | Incluir `engine_on_server==true`, `is_owner==true`, `engine_set.ok`, `vehicle_control.ok` y telemetria valida en el criterio R5. |
| DL-005 | `drive_ladder.py:240-248` | FAIL/P1 | La desambiguacion obstaculo vs drivetrain es un heuristic one-shot y puede mapear al fix equivocado. | Implementar el retest obligatorio en suelo despejado o emitir `blocked_candidate` sin fix de drivetrain hasta tener el retest. |
| DL-006 | `drive_ladder.py:228-260` | WARN/P2 | `_timeout`/`ok:false` no se chequean en `engine_set`, `vehicle_control`, `vehicle_telemetry` ni R6; downstream puede leer `None` o errores como si fueran medidas. | Centralizar `require_ok(cmd,res)` por verbo y registrar `error/_timeout` como rung inconcluso/fail-closed. |
| DL-007 | `drive_ladder.py:177-185,277-278` | WARN/P2 | R2 puede contribuir a `overall_PASS` sin visual, y `solid>=6/12` no implementa "rayos donde deben pegar + visual N angulos". | Separar `objective_PASS` de `acceptance_PASS`, exigir paths de PNG/visual externo para cerrar R2/R6, y documentar/justificar el muestreo. |
| DL-008 | `drive_ladder.py:147,263-267` | WARN/P2 | R6 etiqueta left/right con un signo de cross-product y default `steer_left_sign=-1` declarados no verificados. | Reportar `signed_cross` numerico e invertir/etiquetar solo tras calibracion con referencia conocida. |
| DL-009 | `drive_ladder.py:145-146,230-232` | WARN/P2 | `drive_s` no se valida contra `hold_ttl`; con overrides, el deadman puede expirar durante la ventana medida. | Validar `hold_ttl > drive_s + margen` o rearmar control durante la medicion. |
| DL-010 | `drive_ladder.py:167` | NIT/P3 | R1 usa `spawn.get("found", 1)`: no es falso PASS hoy, pero es un default permisivo contra wire roto/regresion. | Usar `spawn.get("found") is True` o `found` ausente => fail-closed. |
| DL-011 | `drive_ladder.py:32,144,279` | NIT/P3 | Keyfile Windows hardcoded y `--journal` default a cwd hacen que `verdict.json` caiga en lugar ambiguo. | Requerir `--journal` explicito o default estable por vehiculo/run; keyfile por env/config del repo. |

## Bloque C - Hallazgos detallados

### DL-001 - R3 PASS falso cuando solo el pasajero esta disponible

Modo de fallo: **PASS falso**.

`drive_ladder.py` guarda cada asiento como `{"component","available","first_block"}` y pierde `component_crew_index` (`C:\Users\guill\.claude\skills\dayz-mcp-verify\references\drive_ladder.py:202`). Luego calcula `r3_pass = partial_ok and any(s["available"] for s in crew_seats)` (`...drive_ladder.py:205`). Eso certifica "algun asiento", no el conductor.

El wire real si distingue asiento: `MCPBridge.c` calcula `crewIndex = trans.CrewPositionIndex(component)` y lo postea como `gi.component_crew_index` (`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c:625-629`); solo pone `gi.available=true` despues de occupied/through/area/reachable para ese `crewIndex` (`...MCPBridge.c:671-690`). El DTO tambien expone `component_crew_index` y `per_seat` (`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c:213-220`).

El caso no es teorico: `tools\_tramoB-getin-verdict.json` muestra `component_crew_index=0`, `available=0`, `first_block="unreachable"` para el conductor (`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\_tramoB-getin-verdict.json:95-99`) y `component_crew_index=1`, `available=1` para pasajero (`..._tramoB-getin-verdict.json:101-106`); aun asi el verdict del gate queda `any_available=true`, `PASS=true` (`..._tramoB-getin-verdict.json:144-163`). El plan de Tramo B documenta ese mismo resultado como "componente-pasajero available=1" y "componente-conductor crew_index=0, unreachable" (`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-28-fase5-tramoA-redesign-delta.md:350-355`).

Fix sugerido: cambiar el criterio a conductor especifico (`component_crew_index==0 && available`) y registrar el blocker del conductor. La prosa de la skill tambien necesita fix: la tabla dice "un component mapea a asiento ... available=1" (`C:\Users\guill\.claude\skills\dayz-mcp-verify\SKILL.md:368`), pero el producto que se quiere estrenar es "humano entra al asiento de conductor".

### DL-002 - R4 PASS falso por no exigir owner ni fixture

Modo de fallo: **PASS falso / FAIL falso downstream**.

R4 hace `seated = bool(gi.get("seated")) and not gi.get("_timeout")` (`C:\Users\guill\.claude\skills\dayz-mcp-verify\references\drive_ladder.py:219`) y marca PASS con eso, aunque solo registra `is_owner` y `fixture_ready` como datos (`...drive_ladder.py:220-225`). El contrato de la skill dice que R4 PASS es `seated=1`, `is_owner=1`, `vehicle_fixture_ready=1` (`C:\Users\guill\.claude\skills\dayz-mcp-verify\SKILL.md:369`).

El bridge devuelve exactamente esos campos: el get-in client valida driver seat (`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPClientBridge.c:986-990`), espera fixture ready (`...MCPClientBridge.c:1012-1017`) o reporta `vehicle_fixture_ready=false` al vencer prep (`...MCPClientBridge.c:1020-1025`), y postea `seated`, `seat="driver"`, `vehicle_fixture_ready`, `is_owner` (`...MCPClientBridge.c:1470-1474`).

Efecto: un coche sentado pero no owner o no acondicionado entra a R5 y el orquestador puede diagnosticar drivetrain, obstaculo o incluso PASS sobre una precondicion rota. Fix sugerido: R4 debe ser fail-closed sobre `ok`, `_timeout`, `seated`, `seat`, `is_owner` y `vehicle_fixture_ready`.

### DL-003 - Re-run puede medir/controlar el coche equivocado

Modo de fallo: **PASS falso al estrenar / rompe re-run**.

El orquestador no saca al jugador del coche antes de empezar: `wait_ready` arranca la corrida (`C:\Users\guill\.claude\skills\dayz-mcp-verify\references\drive_ladder.py:161`), spawnea un coche nuevo en R1 (`...drive_ladder.py:163-171`) y llama `vehicle_get_in_client(pos=car)` en R4 (`...drive_ladder.py:217-224`). Al final solo llama `vehicle_release` (`...drive_ladder.py:271`), que no es get-out.

El bridge confirma el riesgo: si el cliente ya tiene `player.GetCommand_Vehicle()`, `ProcessVehicleGetInClientPrep` no usa `job.args.pos` ni `FindTransportNearClient`; esos pasos solo ocurren cuando no hay `vehicleCommand` (`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPClientBridge.c:936-955`). Despues acepta el transporte actual como `job.subject` (`...MCPClientBridge.c:992-999`). `vehicle_release` solo hace `MCPCarDrive.Clear()` y `ok=true` (`...MCPClientBridge.c:772-776`).

Efecto: si una pasada previa dejo al player en un CivilianSedan o en otro SUB_BRZ, la siguiente puede spawnear un SUB_BRZ nuevo, pero R4/R5 controlan el vehiculo viejo y el `verdict.json` atribuye el resultado al nuevo `car_pos`. Fix sugerido: preflight fail-closed si ya esta en vehiculo, o validar identidad/pos/net_id del coche sentado contra el spawn antes de pasar R4.

### DL-004 - R5 PASS falso si el coche se mueve sin motor/control valido

Modo de fallo: **PASS falso**.

R5 ignora el resultado de `engine_set` (`C:\Users\guill\.claude\skills\dayz-mcp-verify\references\drive_ladder.py:228`), toma telemetria (`...drive_ladder.py:229-236`) y declara PASS solo con `pos_delta > 1.0` y `speedo > 0.0` (`...drive_ladder.py:237-239`). Eso permite que una pendiente, inercia residual o contacto fisico produzca movimiento/speedometer y cierre R5 como "conduce" aunque el motor no haya arrancado o el control no sea owner-side.

El bridge expone la senal que falta: `engine_set` devuelve `engine_on_server = car.EngineIsOn()` (`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPClientBridge.c:626-657`), y `vehicle_telemetry` devuelve `speedo_max`, `gear`, `engine_on_server`, `pos_real`, `is_owner` (`...MCPClientBridge.c:734-750`). El plan de Tramo A fijaba el triple criterio `pos_delta>1.0`, `speedo_max>0`, `engine_on_server==true` (`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-28-fase5-tramoA-redesign-delta.md:175-180`), y la skill lista `engine_on_server` como campo de `vehicle_telemetry` (`C:\Users\guill\.claude\skills\dayz-mcp-verify\SKILL.md:373-378`).

Fix sugerido: R5 PASS debe incluir `engine_on_server==true`, `is_owner==true`, `engine_set.ok`, `vehicle_control.ok`, y telemetria no-timeout.

### DL-005 - R5 mapea obstaculo/drivetrain con un heuristic que puede dar fix equivocado

Modo de fallo: **FAIL falso / fix equivocado**.

Cuando R5 no pasa, el orquestador usa `blocked = speedo > 0.0 or (gear > 1)` (`C:\Users\guill\.claude\skills\dayz-mcp-verify\references\drive_ladder.py:240-245`); si no, mapea directo a wheel sim/drivetrain (`...drive_ladder.py:246-248`). No ejecuta el retest en claro.

La skill dice que la desambiguacion de `pos_delta~=0` es obligatoria: con speedo/gear/RPM se sospecha obstaculo y "ground-truth = el re-test en suelo despejado" (`C:\Users\guill\.claude\skills\dayz-mcp-verify\SKILL.md:351-359`). El propio plan registro el caso real: en el spawn con obstaculo hubo `pos_delta` 0.15-0.25 y `speedo_max` 0.29-0.89, mientras el offset +40m dio 29.18m y 38.87 km/h (`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-28-fase5-tramoA-redesign-delta.md:296-300`).

Efecto: un drivetrain roto con speedo/gear transitorio puede quedar como "obstaculo"; un obstaculo desde frame 0 con speedo 0 puede quedar como drivetrain/wheel sim. Fix sugerido: sin retest despejado, no emitir fix final de drivetrain ni confirmar obstaculo; emitir estado `needs_clear_ground_retest`.

### DL-006 - Timeouts y `ok:false` se consumen como datos

Modo de fallo: **rompe al estrenar / FAIL falso**.

`Daemon.await_result` devuelve `{"_timeout": True}` al vencer (`C:\Users\guill\.claude\skills\dayz-mcp-verify\references\drive_ladder.py:80-87`). En R5, `engine_set`, `vehicle_control`, `vehicle_telemetry` se llaman sin comprobar `_timeout`, `ok` ni `error` (`...drive_ladder.py:228-240`). R6 repite el patron en tres telemetrias y dos controles (`...drive_ladder.py:254-260`).

El bridge devuelve errores de negocio limpios: `engine_set` puede devolver `ok=false,error="not_seated"` (`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPClientBridge.c:629-634`), y `vehicle_control`/`vehicle_telemetry` tambien (`...MCPClientBridge.c:714-740`). El orquestador no distingue "comando no corrio" de "medida valida con cero movimiento".

Fix sugerido: envolver cada verbo con check fail-closed y registrar el rung como inconcluso/error de harness, no como drivetrain/model fix.

### DL-007 - R2 puede cerrar overall sin evidencia visual de aceptacion

Modo de fallo: **PASS falso de aceptacion si se usa el script standalone**.

R2 objetivo suma 12 rayos y marca PASS con `solid >= 6` (`C:\Users\guill\.claude\skills\dayz-mcp-verify\references\drive_ladder.py:177-184`). Luego agrega `R2_visual` con `PASS=None` y una nota para el agente (`...drive_ladder.py:184-185`). `write_verdict` excluye todos los rungs con `PASS is None` de `overall_PASS` (`...drive_ladder.py:275-278`).

Pero el contrato de la escalera define R2 como raycast + `camera_set` + `capture_screenshot`, con PASS por rayos solidos y visual sin agujeros/orient/escala (`C:\Users\guill\.claude\skills\dayz-mcp-verify\SKILL.md:363-366`), y exige journal con `verdict.json` + PNGs R2/R6 para que cada verde sea inspeccionable (`...SKILL.md:404-406`).

Fix sugerido: renombrar el resultado del script a `objective_PASS` o exigir que el wrapper/agente adjunte rutas PNG y cierre `acceptance_PASS`. Si `solid>=6/12` queda, documentar por que ese umbral no permite medio coche con colision parcial.

### DL-008 - R6 puede mentir sobre izquierda/derecha

Modo de fallo: **INFO falso / diagnostico visual enganoso**.

El CLI declara `--steer-left-sign` default `-1.0` como "DayZ convention unverified" (`C:\Users\guill\.claude\skills\dayz-mcp-verify\references\drive_ladder.py:147-148`). Luego calcula `cross = hx * dz - hz * dx` y etiqueta `cross > 0` como `left` (`...drive_ladder.py:263-267`). La skill dice que R6 PASS es curvar al lado comandado y que invertirlo mapea a fix de `model.cfg wheel angle sign` / naming (`C:\Users\guill\.claude\skills\dayz-mcp-verify\SKILL.md:371,424`).

Como R6 es INFO, no rompe `overall_PASS`, pero puede mandar a mirar el fix equivocado si el signo o la convencion de steer estan invertidos. Fix sugerido: reportar el cross numerico, el comando aplicado y "uncalibrated"; solo etiquetar left/right tras comparar con un vehiculo vanilla de referencia.

### DL-009 - `drive_s` puede exceder el deadman sin guard

Modo de fallo: **FAIL falso con overrides**.

El script acepta `--drive-s` y `--hold-ttl` independientes (`C:\Users\guill\.claude\skills\dayz-mcp-verify\references\drive_ladder.py:145-146`), manda un unico `vehicle_control(...hold_ttl_s=args.hold_ttl)` (`...drive_ladder.py:230`) y duerme `drive_s` sin rearmar (`...drive_ladder.py:231-232`). El contrato de `vehicle_control` dice que el control sostenido dura hasta `vehicle_release` o deadman `hold_ttl_s` (`C:\Users\guill\.claude\skills\dayz-mcp-verify\SKILL.md:281-284`).

Con defaults 5s vs 12s no muerde. Con `--drive-s` mayor o `--hold-ttl` menor, el coche deja de recibir control durante la ventana medida y R5 puede falsear drivetrain. Fix sugerido: validar margen o rearmar control durante mediciones largas.

### DL-010 - `found` default permisivo en R1

Modo de fallo: **higiene fail-open**.

R1 evalua `not (spawn.get("ok", 0) and (spawn.get("found", 1)))` (`C:\Users\guill\.claude\skills\dayz-mcp-verify\references\drive_ladder.py:167`). En el bridge actual, `world_spawn` falla con `ok=false,error` si no valida/no spawnea (`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c:400-415`) y el path de exito postea `found=true` (`...MCPBridge.c:2131-2135`); el DTO tiene `found` (`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c:228-242`).

No lo marco como P1 porque `found` no esta inventado y hoy esta presente. Aun asi el default deberia ser fail-closed: si el wire cambia y omite `found`, R1 no debe pasar por omision.

### DL-011 - Portabilidad y journal ambiguo

Modo de fallo: **higiene / trazabilidad**.

`DEF_KEYFILE` esta hardcoded a una ruta Windows personal (`C:\Users\guill\.claude\skills\dayz-mcp-verify\references\drive_ladder.py:32`). `--journal` defaulta a string vacio (`...drive_ladder.py:144`) y `write_verdict` lo convierte a `Path.cwd()` (`...drive_ladder.py:279`).

Efecto: dos corridas desde cwd distinto escriben `verdict.json` en lugares no obvios, y el script no es portable fuera de esta maquina. Fix sugerido: `--journal` requerido o default estable bajo `<TargetMod>_dev\_ladder\run_<timestamp>`, y keyfile por env/config.

## Bloque D - Lo que NO pude verificar + proximo paso

- No ejecute la cadena R1->R6 in-game; esta es revision offline/adversarial como pediste.
- No audite la taxonomia SUB_BRZ ni el bridge Enforce completo. Solo consulte lineas necesarias para result-fields y contratos.
- No pude calibrar el signo real de R6 izquierda/derecha contra un vehiculo vanilla en movimiento; lo dejo como WARN por ser INFO.
- No probe empiricamente el umbral `solid>=6/12` con un modelo roto; el hallazgo es de soundness del contrato, no de reproduccion in-game.
- Verificado sin hallazgo bloqueante: `Daemon.await_result` conserva `remove=1`; `extract_pos` mantiene orden `pos_real`/`pos`/`state.pos`; `wait_ready` mantiene `ok` default true como los gates; `gear`, `found`, `from` y `to` existen en los DTO/campos wire.

Proximo paso: corregir DL-001..DL-005 antes de gastar un ciclo SUB_BRZ. Minimo: fixtures offline con payloads sinteticos de R3 pasajero-only, R4 seated-no-owner/no-fixture, R5 engine-off-moving, R5 blocked-retest-required y re-run already-in-vehicle; luego `py_compile`; despues recien estrenar R1->R6 in-game.

Memoria durable: no actualice Obsidian/memoria porque la tarea pidio un artefacto de review e inbox; no hubo fix aplicado ni decision nueva consolidada fuera de este informe.
