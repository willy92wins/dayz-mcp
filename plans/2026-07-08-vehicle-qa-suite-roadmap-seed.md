# DayZ Vehicle QA Suite - roadmap seed para Claude

Fecha: 2026-07-08
Autor: Codex
Estado: seed / handoff, no plan aprobado de implementacion
Proyecto destino: `DayZ_MCP_dev`

## Decisiones ya confirmadas

- El objetivo no es solo arreglar falsos positivos: queremos mas herramientas y mejores procedimientos para probar vehiculos DayZ.
- El toolkit debe ser reusable para cualquier vehiculo DayZ, no especifico de `SUB_BRZ`.
- `SUB_BRZ` sera el primer perfil real de prueba.
- El modo de operacion deseado es banco mixto:
  - gates objetivos hands-off cuando sea posible;
  - fase visual asistida cuando la evidencia visual requiera juicio humano;
  - nunca declarar PASS visual si la captura no demuestra el criterio.
- Ubicacion principal confirmada: `DayZ_MCP_dev`.
- Los mods individuales deben aportar perfiles/configuracion, no copiar el harness entero.
- Alcance deseado: QA suite completa, estructurada por fases.

## Problema que dispara el proyecto

El test in-game del ciclo 1 de `SUB_BRZ` mostro que el tooling actual puede generar evidencia parcial pero no siempre separa bien:

- estado server-side vs estado client-side;
- objeto creado vs objeto visible/interactuable para el cliente;
- `CrewGetIn` server-side vs get-in real del cliente;
- movimiento real por input/control vs desplazamiento por pendiente, caida, correccion de fisicas o posicion contaminada;
- captura valida de DayZ vs captura de otra ventana u overlay que tapa la escena.

El resultado fue util, pero el procedimiento tuvo demasiados puntos manuales y falsos positivos potenciales.

## Evidencia concreta del test SUB_BRZ 2026-07-08

Artefactos relevantes:

- `SUB_BRZ_dev/_ladder/codex_cycle1_2026-07-08_16-14-59/verdict.json`
- Server RPT: `SUB_BRZ_dev/_server/profiles/DayZDiag_x64_2026-07-08_16-02-46.RPT`
- Client RPT: `SUB_BRZ_dev/_client/profiles/DayZDiag_x64_2026-07-08_16-08-38.RPT`
- Server script: `SUB_BRZ_dev/_server/profiles/script_2026-07-08_16-02-49.log`
- Client script: `SUB_BRZ_dev/_client/profiles/script_2026-07-08_16-08-41.log`

Observaciones:

- RPT/server+client sin warning objetivo `Proxy with name 'CivSedanWheel_*' was not found in FireGeometry`.
- script.log sin `Virtual Machine Exception ... Could not find zone Reflector_*`.
- `WheelCountPresent()==4` quedo probado por smoke server-side:
  - `SCRIPT : [MISSION-DBG] KIT LF wheelsPresent=4 fuel=1`
  - `SCRIPT : [MISSION-DBG] ... wc=1111 wp=4`
- `drive_ladder.py` probo spawn real via MCP:
  - `R1.PASS=true`
- Driver get-in real del cliente paso:
  - `R4.PASS=true`
  - `seated=1`
  - `is_owner=1`
  - `fixture_ready=1`
- Puerta/asiento pasajero fallo:
  - `component=1`
  - `crew_index=1`
  - `available=false`
  - `first_block="unreachable"`
- Drive quedo ambiguo:
  - `pos_delta=62.79429281589315`
  - `speedo_max=-0.3821844160556793`
  - `engine_on_server=true`
  - `is_owner=true`
  - `verdict="needs_clear_ground_retest"`
- Captura visual no cerro cabina 1PP:
  - ventana DayZ capturada, pero overlay de mods/conexion tapaba la escena.

Conclusion del caso SUB_BRZ: el tooling debe tratar esos resultados como evidencia estructurada, no como PASS global.

## Principios de diseno

1. Fail-closed por criterio
   - Cada criterio tiene que declarar `PASS`, `FAIL`, `AMBIGUOUS` o `NO_EVIDENCE`.
   - `PASS` solo se permite si la evidencia corresponde exactamente al contrato del criterio.

2. Separar autoridad y perspectiva
   - Server-side spawn no prueba visibilidad cliente.
   - Server-side get-in no prueba client ownership.
   - Movimiento de posicion no prueba conduccion si no hay input/control/owner/terreno controlados.

3. Evidencia antes que impresion
   - Todo gate debe producir JSON machine-readable.
   - Las capturas son evidencia solo si el sistema valida que son de la ventana DayZ y que no estan bloqueadas por overlay cuando el criterio lo requiere.

4. Reusable por perfiles
   - El harness vive en `DayZ_MCP_dev`.
   - Cada vehiculo define perfil: classname, categoria, seats esperados, puertas esperadas, controles, tolerancias, escenarios visuales y known-noise.

5. Vanilla controls
   - Cada suite debe poder comparar contra un vehiculo vanilla de control, por ejemplo `CivilianSedan`, para distinguir bug de modelo vs bug de harness/terreno.

6. Procedimiento reproducible
   - Wipe de persistence antes de cada run.
   - Hash del PBO probado.
   - Paths exactos de RPT/script.
   - Cierre de procesos al final.

## Arquitectura propuesta

### 1. Vehicle profiles

Carpeta sugerida:

`DayZ_MCP_dev/tools/vehicle_qa/profiles/`

Ejemplo conceptual:

```json
{
  "id": "SUB_BRZ",
  "classname": "SUB_BRZ",
  "control_classname": "CivilianSedan",
  "expected": {
    "wheel_count": 4,
    "crew_size": 2,
    "seats": [
      { "name": "driver", "crew_index": 0, "required": true },
      { "name": "codriver", "crew_index": 1, "required": true }
    ],
    "doors": [
      { "name": "driver", "crew_index": 0, "required": true },
      { "name": "codriver", "crew_index": 1, "required": true }
    ]
  },
  "known_noise": {
    "rpt_ignore": [
      "Can't load @SUB_BRZ/Anims/cfg/skeletons.anim.xml"
    ],
    "visual_ignore": [
      "transparent tail lights",
      "transparent small side windows"
    ]
  },
  "spawn": {
    "preferred_sites": ["chernarus_airstrip_flat_01"],
    "rotations": [0, 90, 180, 270]
  }
}
```

### 2. Scenario runner

Comando conceptual:

```powershell
python -m vehicle_qa run --profile SUB_BRZ --suite full --out <run_dir>
```

Responsabilidades:

- preparar run dir;
- registrar hash del PBO;
- limpiar persistence;
- arrancar daemon MCP;
- lanzar server/client;
- esperar `bridge_status server=ok client=ok`;
- ejecutar escenarios;
- recolectar logs/capturas;
- cerrar procesos;
- producir `run.json`, `report.md` y `evidence/`.

### 3. Clean launch/bootstrap

Debe reemplazar el bootstrap manual visto en SUB_BRZ.

Funciones:

- resolver paths DayZ/DayZServer/workshop;
- instalar/escribir `dayz_mcp.json` en server profile, client profile y mission;
- arrancar daemon con quoting robusto para paths con espacios;
- probar `/status` y `/enqueue`;
- diagnosticar procesos `--client` stale vs daemon real;
- lanzar server;
- esperar bind UDP con margen realista;
- lanzar client;
- verificar poll server/client.

### 4. Scenario DSL

Cada test debe ser un escenario pequeno y composable.

Escenarios base:

- `spawn_clean`
- `fixture_status`
- `server_telemetry`
- `client_visible_raycast`
- `get_in_driver`
- `get_in_codriver`
- `drive_straight_clear_ground`
- `turn_left_right`
- `brake_stop`
- `engine_start_stop`
- `camera_1pp_cabin`
- `camera_3pp_exterior`
- `damage_reflectors`
- `lights_on_off`
- `material_visibility`
- `log_scan`

Cada escenario devuelve:

```json
{
  "id": "get_in_driver",
  "status": "PASS",
  "evidence": [],
  "metrics": {},
  "diagnostics": [],
  "blocked_by": []
}
```

### 5. Reporting

Outputs por run:

- `run.json`: resultado completo machine-readable.
- `report.md`: resumen humano.
- `grep.json`: conteos de errores target.
- `processes.json`: procesos lanzados/cerrados.
- `pbo_hashes.json`: artefactos probados.
- `captures/`: PNG/JPEG/WebP.
- `logs/`: copias o referencias exactas a RPT/script.

Estados permitidos:

- `PASS`
- `FAIL`
- `AMBIGUOUS`
- `NO_EVIDENCE`
- `HARNESS_FAIL`
- `SKIPPED_BY_PROFILE`

Regla: un suite global solo puede ser `PASS` si todos los criterios `required=true` son `PASS`.

## Herramientas nuevas necesarias

### MCP commands / bridge verbs

1. `vehicle_fixture_status`
   - Input: pos/object_id/classname.
   - Output:
     - `wheel_count_present`
     - `wheel_count_expected`
     - `wheel_contact_count`
     - fuel
     - battery
     - sparkplug
     - radiator
     - engine_belt
     - `fixture_ready`

2. `vehicle_get_in_client`
   - Extender con `seat` o `crew_index`.
   - Hoy sirve para driver; falta codriver/passenger real.

3. `vehicle_get_out_client`
   - Necesario para probar ambos asientos sin reiniciar toda la sesion.

4. `player_reposition`
   - Teleport del cliente cerca de una puerta/asiento.
   - Debe ser seguro y explicito en logs.

5. `vehicle_spawn_at_site`
   - Spawn en sitio validado por QA.
   - Debe incluir pre-raycast de suelo, pendiente y obstaculos.

6. `ground_probe`
   - Devuelve:
     - slope
     - surface type
     - object blockers
     - clearance forward/back/left/right

7. `vehicle_drive_segment`
   - Drive controlado con ventana de muestreo.
   - Output:
     - pos0/pos1
     - pos_delta
     - signed_speedo
     - abs_speedo_max
     - rpm
     - gear
     - engine_on
     - is_owner
     - control_applied_ticks
     - ground_slope
     - collision/blocker flags

8. `camera_capture_assert`
   - Host-side capture con validaciones minimas:
     - proceso correcto;
     - ventana DayZ en foreground o captura directa por handle;
     - overlay/menu detectado si es posible;
     - archivo guardado.

9. `vehicle_damage_zone_probe`
   - Consulta zonas esperadas/config.
   - Prueba damage apply/readback para zonas como reflectors sin depender de crash/exception.

10. `vehicle_lights_probe`
    - Estado lights on/off.
    - Capturas nocturnas controladas.
    - Opcional: analisis simple de luminancia en regiones.

11. `material_visibility_probe`
    - Capturas multiangulo con checklist humano.
    - Puede incluir analisis basico de alfa/zonas negras/transparentes si el frame es confiable.

### Host-side helpers

1. `qa_launch.ps1`
   - Wrapper unico de launch/bootstrap.

2. `qa_collect_logs.ps1`
   - Copia/normaliza RPT/script/logs relevantes.

3. `qa_report.py`
   - Genera markdown y JSON final.

4. `qa_window_capture.py`
   - Captura ventana DayZ por handle, no screenshot global.

5. `qa_process_guard.ps1`
   - Cierre seguro de solo procesos lanzados por el run.

6. `qa_storage_wipe.ps1`
   - Wipe auditable con backup y path safety.

## Procedimientos de prueba propuestos

### Procedimiento base por vehiculo

1. Resolver perfil.
2. Registrar PBO probado y hash.
3. Wipe persistence de la mission activa.
4. Launch server/client con `@DayZ_MCP` y mod bajo prueba.
5. Esperar bridge server/client ok.
6. Spawn control vanilla.
7. Ejecutar smoke vanilla para validar harness/terreno.
8. Spawn vehiculo bajo prueba.
9. Fixture status.
10. Visibilidad cliente/raycast.
11. Get-in driver.
12. Get-out.
13. Get-in codriver/passenger.
14. Drive recto en terreno claro.
15. Turn left/right.
16. Brake/stop.
17. Engine stop/start.
18. Capturas 1PP/3PP/cabina/exterior.
19. Luces/damage/material probes segun suite.
20. Grep RPT/script.
21. Cerrar procesos.
22. Generar reporte.

### Procedimiento clear-ground drive

Para evitar falsos FAIL de drivetrain:

1. Elegir sitio plano de perfil.
2. Raycast suelo y clearance.
3. Spawn control vanilla en el mismo sitio.
4. Driver get-in control vanilla.
5. Drive control vanilla.
6. Si control vanilla falla, marcar `HARNESS_FAIL`.
7. Spawn vehiculo bajo prueba.
8. Repetir driver get-in y drive.
9. Si hay `pos_delta` pero speedo raro, repetir con otra rotacion.
10. Si falla en 2-3 sitios limpios mientras vanilla pasa, entonces diagnosticar como fallo del vehiculo.

### Procedimiento visual

La fase visual debe ser asistida pero estructurada:

1. Poner hora/clima controlados.
2. Posicionar camara en presets.
3. Capturar:
   - exterior front/left/right/rear/top-ish;
   - 1PP cabin;
   - 3PP cabin/exterior;
   - lights off/on;
   - damage zones si aplica.
4. Validar que la captura sea DayZ y no overlay.
5. Si overlay/menu tapa la escena: `NO_EVIDENCE`.
6. Humano/agente marca checklist:
   - escala/orientacion;
   - ruedas visibles y girando si aplica;
   - cabina visible 1PP;
   - texturas/lights/glass;
   - clipping grave;
   - LOD/proxy obvio.

## Roadmap propuesto

### Fase 0 - Especificacion y contrato

Objetivo:

Definir el contrato del Vehicle QA Suite antes de implementar.

Entregables:

- product-spec extension o doc nuevo `vehicle-qa-suite-spec.md`;
- schema de `vehicle_profile.json`;
- schema de `run.json`;
- catalogo de estados y reglas PASS/FAIL;
- lista de escenarios v1;
- fixtures positivos/negativos para reporte.

Gate:

- Review de plan por Codex/Claude.
- Ningun criterio sin definicion de evidencia.

### Fase 1 - Bootstrap robusto y reporte

Objetivo:

Hacer que el harness lance/limpie/cierre de forma repetible.

Entregables:

- `qa_launch.ps1`
- `qa_storage_wipe.ps1`
- `qa_process_guard.ps1`
- `qa_report.py`
- run dir estandar
- `pbo_hashes.json`
- `grep.json`

Gate:

- Run contra `CivilianSedan` sin mod bajo prueba.
- Procesos cerrados al final.
- Reporte generado aunque un paso falle.

### Fase 2 - Core vehicle scenarios

Objetivo:

Cubrir spawn, fixture, get-in driver, drive recto y logs.

Entregables:

- scenario runner v1;
- perfil `CivilianSedan`;
- perfil `SUB_BRZ`;
- `vehicle_fixture_status`;
- `drive_straight_clear_ground`;
- control vanilla antes del vehiculo bajo prueba.

Gate:

- `CivilianSedan` PASS.
- `SUB_BRZ` produce verdict honesto sin falsos PASS.

### Fase 3 - Seats/doors/interaccion

Objetivo:

Probar puertas/asientos de forma explicita.

Entregables:

- `vehicle_get_in_client(seat|crew_index)`;
- `vehicle_get_out_client`;
- `player_reposition`;
- escenario `get_in_each_required_seat`;
- diagnostico por `query_get_in_condition`.

Gate:

- Driver y codriver se reportan separadamente.
- Un codriver `unreachable` sale como FAIL del asiento correcto, no como fallo generico.

### Fase 4 - Visual/capture suite

Objetivo:

Estandarizar capturas y checklist visual asistido.

Entregables:

- captura por handle/proceso DayZ;
- overlay detection basica;
- camera presets;
- visual report markdown;
- `NO_EVIDENCE` cuando la captura no sirve.

Gate:

- Capturas DayZ reales, no desktop accidental.
- 1PP cabin solo PASS si la captura muestra cabina.

### Fase 5 - Lights/materials/damage

Objetivo:

Cubrir visual y damage zones sin depender de RPT indirecto.

Entregables:

- `vehicle_damage_zone_probe`;
- reflector/damage smoke;
- lights on/off scenario;
- material/glass/lights checklist;
- comparison contra vanilla cuando aplique.

Gate:

- VM exception de damage zones se detecta por log y por probe.
- Lights/material findings quedan separados de drivability.

### Fase 6 - Regression y dashboard

Objetivo:

Convertir runs en historial comparativo.

Entregables:

- `runs/index.jsonl`;
- comparacion run-to-run;
- resumen por vehiculo;
- tabla de tendencias;
- filtros por version/PBO hash/game version.

Gate:

- Dos runs del mismo vehiculo comparables.
- Deteccion de regresion por criterio.

### Fase 7 - Multi-vehiculo / batch

Objetivo:

Ejecutar suite sobre varios perfiles.

Entregables:

- `vehicle_qa batch --profiles SUB_BRZ,CivilianSedan,...`
- aislamiento de persistence por run;
- scheduling de escenarios;
- report global.

Gate:

- Batch no contamina vehiculos entre si.
- Fallo de un perfil no bloquea la recoleccion de evidencia de los demas.

## Criterios de calidad del tooling

- Un fallo del harness se reporta como `HARNESS_FAIL`, no como fallo del mod.
- Un resultado ambiguo se reporta como `AMBIGUOUS`, con retest automatico si existe procedimiento.
- Cada PASS debe tener al menos una evidencia objetiva.
- Cada FAIL debe incluir diagnostico minimo y ruta de artefacto.
- Cada run debe cerrar sus procesos.
- Cada run debe registrar hash del PBO probado.
- Cada run debe preservar logs/capturas suficientes para re-auditar.

## Backlog inicial de issues concretos

1. `drive_ladder.py` marca `pos_delta>1` pero `speedo_max` negativo como FAIL ambiguo. Hay que decidir si usar signed speed o abs speed, y bajo que condiciones.
2. Falta clear-ground retest automatico.
3. Falta passenger/codriver get-in real client-side.
4. Falta get-out client-side.
5. Falta fixture status explicito con `wheel_count_present`.
6. Falta captura robusta de ventana DayZ sin overlay.
7. Falta separacion formal entre server-side evidence y client-side evidence.
8. Falta report generator que produzca A/B/C/D o formato suite.
9. Falta profile schema.
10. Falta run manifest con procesos lanzados y cierre.

## Prompt sugerido para Claude

Usar este bloque como entrada para estructurar roadmap/proyecto:

```text
Queremos convertir DayZ_MCP_dev en un Vehicle QA Suite reusable para vehiculos DayZ.
No es solo arreglar falsos positivos: queremos herramientas nuevas y procedimientos mejores.

Decisiones cerradas:
- reusable para cualquier vehiculo;
- SUB_BRZ sera primer perfil real;
- banco mixto: hands-off para gates objetivos, visual asistido sin falsos PASS;
- ubicacion principal: DayZ_MCP_dev;
- alcance final: QA suite completa, implementada por fases.

Toma el seed en plans/2026-07-08-vehicle-qa-suite-roadmap-seed.md y conviertelo en:
1. roadmap formal por fases;
2. product-spec o extension DPF;
3. Fase 0 de research/contrato;
4. primer plan implementable sin tocar mods de vehiculos;
5. criterios R26 verificables por fase.

No implementar todavia. Primero revisar el scope, dependencias reales de DayZ_MCP, verbos existentes, gaps de MCPBridge/MCPClientBridge y proponer plan.
```

