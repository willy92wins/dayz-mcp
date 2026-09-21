# Plan `[EXACT]` — `vehicle_trace` atómico owner-client

**Fecha:** 2026-07-25  
**Estado:** READY — R22/R26 cerrados tras investigación dual y aprobación explícita del usuario  
**Origen:** `MERCEDES_AMGLF_dev/plans/2026-07-24-s1-handling-instrumentation-subplan.md`  
**DPF:** `DayZ_MCP_dev/product-spec.md` G3  
**Feature spec:** `plans/2026-07-25-vehicle-trace-feature-spec.md`  
**Scope:** `DayZ_MCP` compilable + `DayZ_MCP_dev` tooling/tests/docs y la
entrada exacta `DayZ_MCP` en la policy/runtime sellada `dayz-test-v1`; cero
producto Mercedes.

**Enmienda autorizada 2026-07-26:** el launcher vigente rechazó el proyecto con
`bad_project`. Se autoriza construir primero la entrada dedicada, reconstruir el
bundle reproducible y reemplazar su registro por CAS; no se autoriza hospedar la
prueba bajo la identidad de otro mod.

**Enmienda autorizada 2026-07-26 — wire/rate live:** el control real
`CivilianSedan` produjo 64 muestras en 2,804016 s (22,467774 Hz), 152 contactos
corporales y cero overflow, pero confirmó que Enforce serializa los bools del
trace como enteros `0|1`. El host debe canonicalizar únicamente esos campos
conocidos a JSON bool y fallar cerrado ante cualquier otro valor. El curso live
canónico solicita 20 Hz, que es el mínimo contractual de SC-012 y del rango
SC-002; el validador genérico conserva sin cambios
`max_gap<=1,5/sample_hz` y `effective_hz>=max(20,0,9*sample_hz)`, incluido
el fixture offline de 30 Hz.

## 1. Objetivo y gate terminal

Añadir una tool lease-gated `vehicle_trace` que capture en el owner client un stream atómico de vehículo, lo lea por cursor sin pérdida silenciosa y genere un artefacto host determinista. El gate final es:

- `GREEN`: DPF/spec/checklist, RED→GREEN, suite offline, PACKONLY, live `CivilianSedan`, `OnContact` owner-client, artifact y cleanup completos.
- `RED`: cualquier hash/fixture falso-verde, compile/config inválido, overflow/gap/clock/readback, callback `OnContact` ausente, build/deploy incompleto, lifecycle no limpio o rollback no verificable.

Este plan termina al cerrar la instrumentación. No repite field viability de Mercedes, no inicia S2 y no ejecuta tuning.

## 2. Data map cliente/servidor/host

| Dato | CLIENT | SERVER | HOST/DAEMON | Mecanismo |
|---|---:|---:|---:|---|
| pose, direction, velocity, control aplicado, ruedas, engine/gear | sí, owner | no | recibe | captura `CarScript.OnInput` → JSON result |
| control solicitado/deadman | sí | no | origina | `MCPCarDrive` existente |
| `OnContact` raw/no-wheel | sí, gate live | no requerido | recibe | acumulador escalar → siguiente muestra |
| trace id/cursor/capacity/sequence | sí | no | origina/valida | UUID4 host + estado append-only client |
| lease/cleanup | no | no | sí | `SessionCoordinator` + `vehicle_release` interno |
| run/process identity | no | no | sí | lifecycle H11 snapshot |
| PBO/course/schema/RPT/script-log hashes | no | no | sí | bundler host abre y hashea bytes |

No se añaden SyncVars, RPCs ni persistencia.

## 3. Contrato público exacto

### 3.1 Tool

`vehicle_trace(mode, trace_id="", cursor=0, limit=64, sample_hz=20, max_samples=4096, timeout_s=15.0)`

- `mode`: exactamente `start|status|stop|read|clear`.
- `start`: exige lease, `HumanCommandVehicle.GetVehicleSeat()==DayZPlayerConstants.VEHICLESEAT_DRIVER` e `IsOwner()==true`; rechaza trace previo no limpiado; Python genera UUID4 `.hex`; `sample_hz` entero 20..60; `max_samples` entero 2..8192. `IsAuthorityOwner()` se registra como diagnóstico y no se exige, porque describe authority sin owner.
- `status|stop|read|clear`: exigen `trace_id` exacto de 32 hex lowercase.
- `read`: cursor 0..count y limit 1..64; devuelve `next_cursor`; `eof=true` solo si inactivo y `next_cursor==count`. Cada chunk viaja en una llamada MCP nueva y por tanto usa un command id nuevo; no se reutiliza un slot `/result`.
- `stop`: idempotente para el mismo trace ya detenido; fuerza una última muestra solo si hay contacto pendiente y el reloj puede avanzar.
- `clear`: solo sobre trace inactivo; invalida id y libera buffer. Start activo o clear activo fallan cerrados.
- El comando queda en `CLIENT_COMMANDS` y fuera de `READ_ONLY_COMMANDS`; comandos desconocidos siguen mutating-default.

### 3.2 Estado y fallos

- Todos los DTO se instancian al start fuera de los hooks; `Reserve()` aislado no basta. Buffer append-only; sequence 0..count-1.
- No se reserva, instancia ni inserta ningún objeto en `OnInput`/`OnContact`; `OnContact` nunca conserva la referencia `Contact`, solo copia primitivas.
- `overflow`, reloj no monotónico, coche/owner cambiado o contacto pendiente imposible de flush → inactivo, `complete=false`, `stop_reason` exacto.
- `vehicle_release`, release/expiry de lease y `MCPClientBridge.Shutdown` abortan y limpian trace+control.
- `vehicle_trace start` marca `lease.vehicle_active=true` en las dos rutas de commit. Puede quedar true hasta `vehicle_release`; cleanup extra inocuo se prefiere a un trace huérfano.
- Start/status/stop/read son operaciones request-bound cortas. El trace no retiene el operation pin entre llamadas; la lease se mantiene con el heartbeat/TTL existente.

## 4. Schema v1

### 4.1 Metadata por trace/read

`schema`, `mode`, `trace_id`, `active`, `complete`, `overflow`, `stop_reason`, `sample_hz`, `capacity`, `count`, `start_monotonic_s`, `owner_identity`, `car_type`, `net_id_low`, `net_id_high`, `cursor`, `next_cursor`, `eof`, `samples`.

El boundary host convierte sólo `0|1` de `active`, `complete`, `overflow`,
`eof` y de los campos bool de muestra al tipo JSON booleano exigido por el
schema. Valores ausentes o distintos de bool/`0|1` son `STOP`; no hay coerción
truthy genérica.

### 4.2 Campos obligatorios por muestra

| Grupo | Campos |
|---|---|
| reloj | `sequence`, `monotonic_s`, `sample_dt_s`, `forced` |
| pose | `position_x/y/z`, `velocity_x/y/z`, `direction_x/y/z`, `yaw_deg`, `pitch_deg`, `roll_deg` |
| control | `control_active`; `throttle/steer/brake/handbrake_requested`; los cuatro `_applied` |
| ruedas | `wheel_contact_0..3`, `wheel_angular_velocity_0..3`, `wheel_count`, `wheels_present` |
| coche | `engine_on`, `gear` |
| ownership | `is_owner`, `is_authority_owner`, `net_id_low`, `net_id_high` |
| eventos | `wheel_loss_event`, `wheel_loss_count`, `contact_count`, `body_contact_count`, `body_contact_zone`, `body_contact_impulse`, `body_contact_local_x/y/z`, `body_contact_normal_x/y/z`, `body_contact_penetration_depth` |

`y` es vertical nativa. Sin crear un string nuevo en el hook, `body_contact` excluye `zoneName` si `IndexOf("wheel")`, `IndexOf("Wheel")` o `IndexOf("WHEEL")` es ≥0; cualquier otro nombre se clasifica como body y se conserva raw. El evento elegido es el de mayor impulso dentro del intervalo.

## 5. Algoritmos offline normativos

- Reloj agregado (SP-063): ademas del check por fila, `|span - sum(sample_dt_s[1:])| <= max(1%*span, 0.1 s)`; todos los comparadores temporales usan `EPS=1e-6 s`.
- Reloj: sequence contiguo desde 0; timestamps estrictos; primer dt 0; después `|dt-(t[i]-t[i-1])|≤0,001 s`.
- Cadencia: `max_gap≤1,5/sample_hz`; effective Hz `(n-1)/(last-first)≥max(20,0,9*sample_hz)`.
- Readback: si `control_active`, error abs solicitado↔aplicado por canal ≤0,001.
- Wheel pulse: duración = suma de `sample_dt_s` de muestras contiguas con el contacto false; error de frontera aceptable ≤max gap.
- Wheel loss: `wheels_present` decrece o `wheel_loss_event/count` no concuerda con ese delta → STOP.
- Spinout: speed horizontal ≥30 km/h y ángulo horizontal direction↔velocity ≥45° sostenido ≥0,25 s.
- Rollover: `abs(roll_deg)≥60°` sostenido ≥0,50 s.
- Grounding H5: cualquier `body_contact_count>0` dentro de la ventana.
- Toda derivación se recalcula desde raw. Si `declared_derived` existe y difiere, check FAIL; nunca se confía en ella.

## 6. Artifact contract

El schema de artefacto es `dayz-mcp-vehicle-trace-artifact-v1`.

- Inputs obligatorios: trace JSON completo, schema, course, PBO PACKONLY desplegado, RPT, script log y lifecycle JSON del run exacto.
- Lifecycle exige `run_id` no vacío y al menos server+client con `pid`, `creation_time_utc`, `executable_sha256`, `command_line_sha256`, `role`, `identity_scheme`.
- El bundler abre los seis archivos y calcula SHA-256; no admite hashes supplied como autoridad.
- Output ordenado/canónico con `checks[{id,status,measured,expected,evidence}]`, derivaciones recalculadas y hashes.
- `artifact_sha256` = SHA-256 del JSON canónico del cuerpo sin ese campo; dos ejecuciones sobre los mismos bytes deben producir output byte-idéntico.
- Exit 0 PASS; 1 FAIL reproducible; 2 STOP/incompleto; 4 input/JSON/tipo ilegible. Campo de muestra ausente = STOP/2; archivo ilegible = 4.

## 7. Archivos previstos y orden de dependencia

1. DPF/spec/plan:
   - `DayZ_MCP_dev/product-spec.md`
   - `DayZ_MCP_dev/plans/2026-07-25-vehicle-trace-{feature-spec,spec-checklist,atomic-instrumentation}.md`
2. Schema/fixtures/tests:
   - `DayZ_MCP_dev/tools/schemas/vehicle-trace-v1.json`
   - `DayZ_MCP_dev/tools/fixtures/vehicle-trace-civilian-sedan-control-v1.json`
   - `DayZ_MCP_dev/tools/tests/fixtures/vehicle_trace/{positive_20hz,negative_mutations}.json`
   - `DayZ_MCP_dev/tools/tests/test_vehicle_trace.py`
   - `DayZ_MCP_dev/tools/tests/test_vehicle_trace_contract.py`
   - updates mínimos a `test_loopback.py` y `test_session_coordination.py`
3. Host:
   - `DayZ_MCP_dev/tools/dayz_mcp/vehicle_trace.py`
   - `DayZ_MCP_dev/tools/vehicle_trace_artifact.py`
   - `DayZ_MCP_dev/tools/dayz_mcp/server.py`
   - `DayZ_MCP_dev/tools/dayz_mcp/loopback.py`
   - `DayZ_MCP_dev/tools/dayz_mcp/session_coordination.py`
   - `DayZ_MCP_dev/tools/dayz_mcp/core.py`
4. Compilable:
   - `DayZ_MCP/scripts/4_World/MCP_CarScript.c`
   - `DayZ_MCP/scripts/5_Mission/MCPMessages.c`
   - `DayZ_MCP/scripts/5_Mission/MCPClientBridge.c`
5. Prerrequisito de lifecycle sellado:
   - `DayZ_MCP_dev/tools/build_native_launcher.py`
   - `DayZ_MCP_dev/tools/tests/test_build_native_launcher_policy.py`
   - `DayZ_MCP_dev/tools/tests/test_native_launcher_bundle.py`
   - `DayZ_MCP_dev/tools/tests/test_native_bundle.py`
   - pines derivados del PE en
     `DayZ_MCP_dev/tools/tests/{test_secure_launcher.py,test_task9_launcher_migration.py}`
   - artefactos derivados `native-launchers/dayz-test-v1/{request-policy.json,worker-runtime.json,closure-manifest.json,reproducibility.json,dayz-test-launcher.exe}`
   - `approved-launchers.json` y receipt transaccional generado por el updater

No se toca `MCPBridge.c`, config, modelo, otro mod ni skill/runbook.

## 8. TDD y viability gates

### Task 0 — Baseline y rollback

- Registrar SHA-256/bytes de cada target antes de tocarlo.
- Guardar copia host-direct en staging nuevo `C:\tmp`.
- Confirmar no git repo; rollback = manifest + copias exactas.

### Task 1 — RED antes de implementación

- Añadir schema, fixture positiva 20 Hz y tabla de mutaciones negativas.
- Tests deben fallar por feature ausente, no por import/typo:
  - positivo y pulso 0,10 s;
  - falta de cada campo;
  - gap, clock regression, low Hz;
  - steer mismatch;
  - overflow;
  - tamper de spinout/rollover/grounding;
  - UUID/modos/cursor/limit;
  - whitelist/lease/cleanup;
  - source contract/bridge v6.
- Ejecutar subset y conservar output RED exacto.

### Task 2 — Host mínimo GREEN

- Implementar validator/derivaciones/bundler/CLI.
- Registrar tool tipada y whitelist.
- Marcar trace start como actividad lease en ambas rutas.
- Actualizar tests existentes solo por el nuevo contrato, sin refactor.
- Ejecutar subset y suite host completa.

### Task 3 — Enforce mínimo GREEN estructural

- Añadir DTO/estado preasignado en 4_World.
- Reestructurar `OnInput` solo para preservar la lógica existente y capturar al final; auditar cada antiguo early return.
- Añadir `OnContact` con `super.OnContact(...)` y acumulación escalar.
- Añadir args/result/dispatch y cleanup en 5_Mission.
- Bump v5→v6 en `MCPMessages.c` y `core.py`.
- Ejecutar source-contract tests; mini-audit por archivo.

### Task 4 — Analyze gate + full offline

- `dayz-feature-spec` Step 3 read-only sobre DPF↔spec↔plan↔tests↔código↔HANDOFF.
- Cero CRITICAL/HIGH abiertos.
- Ejecutar suite focal y suite completa. Si un fallo preexistente no está causado por el diff, demostrarlo con baseline; no relajar tests.

### Task 4.1 — Correcciones de revisión independiente `[EXACT — enmienda autorizada]`

- Preservar el resto fraccional del acumulador Enforce; fixture determinista
  `dt=0,025`, request 30 Hz y resultado efectivo ≥27 Hz.
- El course canónico exige `CivilianSedan`, 20 Hz, duración mínima 2 s y las
  cuatro observaciones cerradas; el bundler lo valida semánticamente y no solo
  lo hashea. Cada control lleva exactamente `at_s,throttle,steer,brake,handbrake`
  con valores finitos y dentro de rango; el schedule empieza en 0, es
  estrictamente creciente, cubre la duración mínima sin exceder el trace y cada
  transición debe observarse en muestras `control_active` con requested/applied
  iguales al valor programado a ±0,001 dentro de ±1,5 intervalos de muestreo.
  El `course_id` queda ligado al schedule canónico exacto
  `(0,0.5,0,0,0)→(1,0.2,0.25,0,0)→(2,0,0,1,0)` en orden
  `at_s,throttle,steer,brake,handbrake`; `handbrake` sólo admite 0 o 1.
- Todo tipo inválido de muestra termina `STOP`/exit 2 sin excepción Python.
- `vehicle_release` conserva la obligación de cleanup hasta un resultado
  exitoso; descarte, timeout o resultado fallido mantienen fail-closed.
- Cada check de artefacto incluye `id,status,measured,expected,evidence`; schema
  y fixtures incluyen la metadata read completa y los tres tamper derivados.
- Demostrar RED sobre los seis defectos, GREEN focal y recompilar PACKONLY antes
  de reconsiderar el gate live.

### Task 4.5 — Policy y launcher sellado `[EXACT — enmienda autorizada]`

- En staging nuevo fuera de OneDrive, añadir primero un test conductual que
  falle por `bad_project`; conservar además el negativo de un proyecto no
  listado.
- Añadir solo `DayZ_MCP` a `_build_request_policy()` y
  `_build_worker_runtime()` con raíces exactas; no editar JSON derivados.
- Copiar los `.py` a OneDrive host-direct y verificar SHA-256.
- Construir fuera de OneDrive con `--offline --verify-reproducible`; exigir
  fingerprints `clean-1=clean-2=offline=final`, manifests canónicos y suites
  de seguridad/bundle GREEN.
- Respaldar bundle/registro, reemplazar el bundle canónico verificado y ejecutar
  la transición documentada
  `rollback-last` → hash CAS → `install-dayz-test-v1`; verificar receipt y
  apertura del launcher. Cualquier mismatch o rollback no demostrable = RED.

### Task 5 — PACKONLY y deploy controlado

- Leer/aplicar `dayz-pbo-build` y `dayz-test-ingame`.
- Construir PACKONLY desde el árbol compilable; inventariar PBO; escanear compile/config errors.
- Copiar/deploy solo `DayZ_MCP.pbo` verificado; conservar SHA anterior para rollback.
- Reiniciar lifecycle solo por tools H11 y run ids exactos. No matar PID.

### Task 6 — Control live `CivilianSedan`

- Resolver primero cualquier run vivo ajeno mediante lifecycle exacto; nunca PID.
- Antes del launch `server|all`, materializar
  `P:\DayZ_MCP_dev\_server\serverDZ.cfg` desde el fixture DayZ_MCP S0
  verificado. El worker consume esa ruta exacta en
  `tools/dayz_mcp/dayz_test_worker.py:205-218`; ausencia demostrada por
  `readiness_failed` el 2026-07-26. Es configuración de staging, no producto.
- Run aprobado server+client, lease justo a tiempo.
- Preparar/entrar `CivilianSedan`, start 20 Hz/4096, control ≥2 s, stop/read completo.
- Repetir con contacto corporal; ausencia de `OnContact` owner-client = RED.
- Release, artifact bundle dos veces, hashes idénticos.

### Task 7 — Revisión, rollback y cierre

- Revisión Codex propia + Codex fresh independiente; no coordinar hallazgos antes del veredicto.
- Corregir solo hallazgos del scope con nuevo RED→GREEN.
- Informe con conclusión arriba, comandos/resultados, hashes, archivos, rollback, riesgos y autorización/denegación de retomar S1.
- Memoria durable, `HANDOFF.md`, handoff en `AI/30_Sessions`, mejora de skill solo como propuesta.
- Postflight `session_status`/`bridge_status`: owner null, queue empty, pending 0, run control EXITED, procesos del run vacíos.

## 9. Mini-audit/API citations obligatorias

Antes de escribir cada llamada Enforce, reabrir:

- control/ruedas: `P:/scripts/3_game/vehicles/car.c:193-217`, `:290-297`, `:349-352`;
- `OnContact`: `P:/scripts/3_game/vehicles/transport.c:252`; cuerpo vanilla `carscript.c:1453-1480`;
- `Contact`: `P:/scripts/1_core/physics/contact.c:9-49`;
- objeto/owner/reloj: `object.c:293-320`, `:815`; `pawn.c:194-209`; `game.c:913`; `enphysics.c:104`.
- conductor: `P:/scripts/3_game/human.c:696`; `P:/scripts/3_game/dayzplayer.c:674`; precedente `DayZ_MCP/scripts/5_Mission/MCPClientBridge.c:1003-1007`.
- slot de resultados pull por command id: `DayZ_MCP_dev/tools/dayz_mcp/loopback.py:730-764`.
- selección fail-closed del proyecto:
  `DayZ_MCP_dev/tools/dayz_mcp/dayz_test_tool.py:65-72`;
- generadores sellados:
  `DayZ_MCP_dev/tools/build_native_launcher.py:399-663`;
- transición CAS y rollback:
  `DayZ_MCP_dev/tools/dayz_mcp/launcher_registry_update.py:249-256`,
  `:412-417`; secuencia operativa existente en
  `DayZ_MCP_dev/plans/2026-07-22-dayz-test-mcp-tools-plan.md:230`.

Toda desviación material nueva del plan se reporta con `path:line` y detiene implementación.

## 10. Rollback

- Restaurar cada archivo desde el manifest/copia staging y verificar SHA-256 exacto.
- Restaurar/deploy del PBO DayZ_MCP anterior pinneado y confirmar handshake v5.
- Para el launcher: `rollback-last` restaura el registro predecessor; restaurar
  además el bundle canónico desde la copia byte-exacta y verificar sus hashes.
- Detener solo el run exacto mediante `dayz_test_stop`.
- No cambia ningún byte de `MERCEDES_AMGLF`.
