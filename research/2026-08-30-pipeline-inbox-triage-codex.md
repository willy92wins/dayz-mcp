---
date: 2026-08-30
researcher: codex
project: DayZ_MCP
topic: pipeline-inbox-triage
git_commit: 9a582560e3ce51239480715c1ab7096414f56b78
branch: master
status: draft
tags: [pipeline-inbox, gates-ledger, triage, dayz-mcp]
---

# Research — Triaje completo del buzón DayZ MCP

## Pregunta de research

¿Cuáles de las 35 entradas abiertas describen defectos todavía presentes en los bytes
actuales, cuáles son correcciones o duplicados, y qué conjunto mínimo de cambios y puertas
independientes permite cerrarlas sin introducir refactor incidental ni falsos PASS?

## Estado de entrada

- `pipeline_inbox(include_resolved=false, limit=100)` devolvió 35 entradas abiertas,
  343 entradas totales y 0 líneas malformadas el 2026-08-30.
- El árbol compartido está muy modificado y contiene trabajo ajeno sobre varias superficies
  afectadas. Ninguna línea existente se atribuye a esta sesión hasta contrastarla contra
  `git diff` y volver a hashear el fichero justo antes de escribir.
- No existía un research consolidado del día sobre este tema; por R24 este documento precede
  al primer plan y a cualquier cambio de código.

## Hechos verificados

### Transporte de errores y UI

- El runtime convierte cualquier resultado del bridge con `ok` falso en `ToolError` antes de
  devolver el payload; por eso `handler`, `user_id` y otros diagnósticos quedan fuera de la
  respuesta estructurada: `tools/dayz_mcp/server.py:631`,
  `tools/dayz_mcp/server.py:669`.
- Las cuatro tools UI públicas solo envían `path`, `text` o `button` y no ofrecen un ámbito
  de búsqueda: `tools/dayz_mcp/server.py:3973`, `tools/dayz_mcp/server.py:3992`,
  `tools/dayz_mcp/server.py:4009`, `tools/dayz_mcp/server.py:4069`.
- `ResolveUiRoot` busca primero con `WorkspaceWidget.FindAnyWidget` y, si falla, recorre todo
  el workspace; no cuenta coincidencias ni acota por ancestro:
  `addon/scripts/5_Mission/MCPClientBridge.c:1964`.
- `InvokeUiClick` usa coordenadas `(0,0)` y termina en el primer handler encontrado, tanto si
  consume como si rechaza el evento: `addon/scripts/5_Mission/MCPClientBridge.c:2144`.
- `DispatchUiClick` sí conserva `user_id`, nombre del handler y la distinción
  `no_handler`/`not_handled`; la pérdida ocurre después, en Python:
  `addon/scripts/5_Mission/MCPClientBridge.c:1363`.
- `MCPResult` no tiene eco de `path`, `root` ni `text`; sus campos UI actuales son `ui`,
  `clicked`, `handler` y `user_id`: `addon/scripts/5_Mission/MCPMessages.c:412`.

### Observabilidad del mundo y vehículos

- Cada fila de `entities_query` contiene solo `type`, `classname`, `pos` y `distance`:
  `addon/scripts/5_Mission/MCPMessages.c:354`.
- El servidor construye esas filas sin consultar inventario/cargo:
  `addon/scripts/5_Mission/MCPBridge.c:1373`.
- LFPowerGrid decide que una entidad es un contenedor enlazable mediante
  `candidate.GetInventory().GetCargo()` y usa un radio de 3 m:
  `P:/LFPowerGrid/scripts/4_World/LFPG_Sorter.c:18`,
  `P:/LFPowerGrid/scripts/4_World/LFPG_Sorter.c:407`,
  `P:/LFPowerGrid/scripts/4_World/LFPG_Sorter.c:513`.
- `DispatchVehicleTelemetry` resuelve el coche con `ResolveOwnedCar`, igual que las otras
  operaciones owner-client, pero no asigna `found`, `seated`, `seat`, `type` ni `classname`;
  los ceros observados son defaults del DTO, no evidencia de que el resolvedor fallara:
  `addon/scripts/5_Mission/MCPClientBridge.c:1082`,
  `addon/scripts/5_Mission/MCPClientBridge.c:2332`.
- Si `entities_query` no encuentra un jugador cercano, Python devuelve
  `nearest_player_m=null` y `reliability=remote_unverified`, sin causa explícita:
  `tools/dayz_mcp/server.py:1640`.

### Lifecycle, petición pública y espera

- El dueño actual del contrato distingue modos públicos `server|all|client` de la extensión
  interna `offline`, pero los literales y textos siguen duplicados:
  `tools/dayz_mcp/dayz_test_tool.py:17`, `tools/dayz_mcp/dayz_test_tool.py:18`,
  `tools/dayz_mcp/dayz_test_request.py:280`.
- La capa pública rechaza cualquier misión que no sea un alias, aunque el validador sellado
  inferior admite rutas dentro de `mission_roots`:
  `tools/dayz_mcp/dayz_test_tool.py:134`,
  `tools/dayz_mcp/dayz_test_request.py:282`.
- `mode=client` exige `run_id`, pero el rechazo de esa precondición se colapsa hoy a la familia
  genérica `bad_dayz_test_request`: `tools/dayz_mcp/dayz_test_request.py:343`.
- `client_alive` significa únicamente que el PID existe; no demuestra avance del cliente ni
  ausencia de un modal bloqueante: `tools/dayz_mcp/dayz_test_tool.py:423`.
- El default vivo de `wait_for.lookback_lines` es 200 tanto en la implementación como en la
  tool pública; las fichas que alertaban de un cambio a 0 describían un candidato anterior,
  no el árbol actual: `tools/dayz_mcp/server.py:2043`,
  `tools/dayz_mcp/server.py:4155`.
- Hay un reaper de runs cuyos procesos propios han desaparecido, pero deliberadamente no
  mata un run con servidor vivo aunque el cliente esté muerto; la petición válida es
  diagnosticar inactividad/ocupación, no preemptar el mundo del dueño:
  `tools/dayz_mcp/process_lifecycle.py:2290`,
  `tools/dayz_mcp/process_lifecycle.py:2304`.

### Herramientas auxiliares y evidencia

- El índice Knowledge Pack no se regenera desde las tools; si falta, ambas consultas exigen
  ejecutar un comando externo: `tools/dayz_mcp/knowledge.py:25`,
  `tools/dayz_mcp/knowledge.py:394`.
- El instalador ya conoce y despliega el pack salvo opción explícita de omisión, así que la
  extracción local es prior art y no requiere red ni rutas libres:
  `tools/install_mcp.py:752`, `tools/dayz_mcp/knowledge_pack.py:55`.
- `pipeline_resolve` limita `resolution` a 2000 caracteres y el lector no calcula edad ni
  conserva una referencia de evidencia separada:
  `tools/dayz_mcp/inbox.py:79`, `tools/dayz_mcp/inbox.py:89`,
  `tools/dayz_mcp/inbox.py:104`.
- El crop normalizado se calcula contra el ancho/alto de la imagen exterior completa; el
  backend valida `clientStats`, pero `grab_stable_frame` no conserva el rectángulo cliente
  necesario para transformar coordenadas: `tools/mcp_capture.py:138`,
  `tools/mcp_capture.py:376`.
- Tanto el wrapper vivo de LFQuad2 como la plantilla canónica de `dayz-test-ingame` aceptan
  el código 0 de AddonBuilder y luego solo comprueban que el PBO ya exista:
  `P:/LFQuad2_dev/tools/dayz-test.ps1:477`,
  `P:/LFQuad2_dev/tools/dayz-test.ps1:480`,
  `C:/Users/guill/.agents/skills/dayz-test-ingame/templates/dayz-test.ps1:534`,
  `C:/Users/guill/.agents/skills/dayz-test-ingame/templates/dayz-test.ps1:537`.
- La versión de LFPowerGrid ya coincide en los dos dueños actuales (`1.2.4`); la entrada de
  versión está desplazada y no autoriza un cambio artificial:
  `P:/LFPowerGrid/config.cpp:208`,
  `P:/LFPowerGrid/scripts/3_Game/LFPG_Defines.c:527`.

## Triaje individual de las 35 entradas

Cada fila seguirá siendo una puerta independiente aunque varias compartan implementación.
`CAMBIO` significa que existe trabajo material; `EVIDENCIA` significa que el cierre correcto
es demostrar el estado actual y añadir, cuando aporte valor, una puerta de no-regresión.

| # | Feedback ID | Disposición de Fase 0 | Paquete candidato |
|---:|---|---|---|
| 1 | `fb-20260830-112522-1082` | EVIDENCIA: corrección válida; revisar y probar el código ya presente | P01 input headless/registro |
| 2 | `fb-20260830-112438-40e4` | CAMBIO: añadir predicado real `has_cargo` | P02 observabilidad entidades |
| 3 | `fb-20260830-112422-2762` | CAMBIO: eco de petición UI | P03 contrato UI |
| 4 | `fb-20260830-011217-668f` | CAMBIO: build atómico/fresco en wrapper y plantilla | P04 PBO fresco |
| 5 | `fb-20260830-010517-9d46` | CAMBIO: un único dueño de modos públicos/internos | P05 esquema efectivo |
| 6 | `fb-20260830-002237-0de3` | CAMBIO: rellenar campos semánticos de telemetría | P02 observabilidad vehículos |
| 7 | `fb-20260829-230535-f4f2` | CAMBIO: scope explícito y ambigüedad fail-closed | P03 contrato UI |
| 8 | `fb-20260829-221423-b2c4` | CAMBIO: fallos UI vuelven como payload estructurado | P03 contrato UI |
| 9 | `fb-20260829-194823-ffc7` | CAMBIO: incluir validadores en el contrato auditable | P05 esquema efectivo |
| 10 | `fb-20260829-194752-d366` | CAMBIO: promover herramienta y gate v5 recalibrado | P05 esquema efectivo |
| 11 | `fb-20260829-184952-20be` | CAMBIO: secuencia, coordenadas y bubbling controlables | P03 contrato UI |
| 12 | `fb-20260829-184906-21f5` | EVIDENCIA: corrección semántica; documentación/regresión | P03 contrato UI |
| 13 | `fb-20260829-135727-782b` | CAMBIO: bootstrap Knowledge autocontenido y sellado | P06 Knowledge Pack |
| 14 | `fb-20260829-135408-cc2d` | EVIDENCIA: rechazo correcto; mejorar diagnóstico común | P07 lifecycle UX |
| 15 | `fb-20260829-133459-a396` | CAMBIO: Steam PID real, readiness y misión sellada | P07 lifecycle UX |
| 16 | `fb-20260829-115147-4407` | CAMBIO: aislamiento reversible por fingerprint de modset | P08 persistencia de misión |
| 17 | `fb-20260829-111016-344d` | EVIDENCIA: ya 1.2.4/1.2.4; gate de paridad | P09 versión LFPG |
| 18 | `fb-20260829-104630-141e` | CAMBIO: misma promoción de esquema efectivo | P05 esquema efectivo |
| 19 | `fb-20260829-104625-7c88` | CAMBIO: describir reattach y errores precisos | P07 lifecycle UX |
| 20 | `fb-20260829-104608-4d66` | CAMBIO acotado: señales/edad; sin preemption automática | P07 lifecycle UX |
| 21 | `fb-20260829-104543-47c9` | CAMBIO: prioridad satisfecha por P03 | P03 contrato UI |
| 22 | `fb-20260829-103347-243b` | EVIDENCIA: y=0 probado; aclarar efecto vs `ok` | P10 instrucciones |
| 23 | `fb-20260829-032121-fc6e` | EVIDENCIA: corrección válida; misma aclaración | P10 instrucciones |
| 24 | `fb-20260829-030056-d73b` | EVIDENCIA: default vivo 200 y gate existente | P10 instrucciones |
| 25 | `fb-20260829-025754-f201` | EVIDENCIA: no reducir tools por conteo | P05 esquema efectivo |
| 26 | `fb-20260829-025502-251d` | EVIDENCIA: candidato regresivo no está en árbol vivo | P10 instrucciones |
| 27 | `fb-20260829-025012-103f` | CAMBIO: señal de registro congelado | P11 fingerprint de tools |
| 28 | `fb-20260829-024848-c7ca` | CAMBIO: edad + referencia durable, manteniendo records acotados | P12 buzón |
| 29 | `fb-20260829-024827-9b7b` | CAMBIO: fingerprint/staleness de la sesión MCP | P11 fingerprint de tools |
| 30 | `fb-20260829-024747-55dd` | EVIDENCIA: H1 retirado; H2 cubierto por P10 | P10 instrucciones |
| 31 | `fb-20260829-023649-8f8c` | CAMBIO parcial + evidencia de fixes ya vivos | P07 lifecycle UX |
| 32 | `fb-20260829-022838-7743` | CAMBIO UI + EVIDENCIA input ya expuesto | P01/P03 |
| 33 | `fb-20260828-224835-268a` | CAMBIO: crop en client area con legado explícito | P13 captura visual |
| 34 | `fb-20260828-212912-f6ac` | EVIDENCIA: diagnóstico supersedido; defecto real es scope | P03 contrato UI |
| 35 | `fb-20260828-211445-3bb4` | EVIDENCIA + gate: implementación engine-native ya presente | P01 input headless/registro |

## Prior art en el repo

- La separación pública/interna de modos ya existe; el arreglo debe derivar de ella en vez de
  publicar `offline`: `tools/dayz_mcp/dayz_test_tool.py:17`.
- El reaper seguro ya clasifica procesos sin terminar procesos ajenos; las mejoras de
  inactividad deben apoyarse en ese modelo, no añadir un segundo lifecycle:
  `tools/dayz_mcp/process_lifecycle.py:2244`.
- `result_prune` ya conserva escalares falsy, por lo que los nuevos bools de observabilidad
  pueden expresar `false` sin desaparecer: `product-spec.md` changelog 2026-08-07.
- El instalador ya prepara el Knowledge Pack; el bootstrap debe ser extracción determinista
  desde esa ruta sellada, sin red: `tools/dayz_mcp/knowledge.py:291`.
- El instrumento `effective_schema` ya pasó cinco generaciones de ataques. V1–v4 fueron
  gameables; v5 usa casos opacos/generados y 21 controles. Debe reejecutarse contra los bytes
  actuales y recibir la cadena Grok→Opus antes de promoción; no se copia como verdad estática.
- LFQuad2 ya mantiene `GATES.md` y una puerta de frescura N1. La reparación del wrapper debe
  demostrar que un PBO preexistente no puede convertir un build fallido en PASS.

## Trazabilidad a DPF

Las entradas sirven intenciones existentes, pero varias carecen de criterio literal. Antes de
implementar, el plan debe proponer una ampliación acotada del DPF y pasar el gate R29:

- B (control sin input SO): input headless y UI con scope/eventos fieles.
- C (verdicts estructurados): cargo y telemetría semánticamente completos.
- D (píxeles): crop en el mismo espacio de coordenadas que `ui_tree`.
- E (usable/instalable): Knowledge autocontenido y contrato efectivo auditable.
- H (sesiones/lifecycle): diagnóstico de ocupación, Steam, misión sellada, modset reversible,
  registro de tools obsoleto y buzón con evidencia durable.

No se aprobará una fase huérfana del DPF. La enmienda exacta requiere aprobación humana antes
de tocar código.

## Suposiciones detectadas

- `[ASSUMPTION]` Enfusion debe propagar un evento al ancestro siguiente cuando un handler
  devuelve `false`. La fuente abierta confirma las firmas, no el dispatcher nativo. El plan
  debe ofrecer modo explícito `bubble` o un discriminador in-game; no puede afirmar paridad
  con el motor sin medirla.
- `[ASSUMPTION]` Un preflight de Steam puede distinguir modal bloqueante sin retrasar todos
  los arranques. El PID del registro sí es verificable; readiness por delta de CPU/RPT necesita
  viability tests antes de elegir mecanismo.
- `[ASSUMPTION]` Todas las misiones autorizadas por `mission_roots` toleran un sidecar de
  fingerprint fuera de `storage_1`. Debe probarse con ruta legacy, mismatch y rollback.
- `[ASSUMPTION]` El worktree del instrumento de esquema y sus artefactos v5 siguen íntegros.
  Se comprobarán hash, rama y suite antes de incorporarlos.
- `[ASSUMPTION]` El código dirty de `key_press`/`player_respawn` pertenece a un candidato
  completo. Debe revisarse como bytes ajenos y no como implementación de esta sesión.

## Riesgos y unknowns

- **Árbol compartido dirty**: un hash distinto antes de escribir obliga a rebaselinar el plan
  afectado; jamás se sobreescribe por similitud textual.
- **Cambio de contrato UI**: devolver `ok:0` estructurado en vez de excepción es observable.
  Requiere changelog, tests de consumidores y compatibilidad explícita.
- **Ambigüedad UI**: elegir el primer match mantiene compatibilidad pero permite efectos sobre
  el widget equivocado; el diseño recomendado falla cerrado con `ambiguous_path`.
- **Persistencia**: rotar `storage_1` resetea mundo/personaje aunque sea recuperable. No se
  borra nada; el backup y la política legacy/rollback deben quedar en el plan y el resultado.
- **PBO fresco**: comparar solo mtime o solo SHA anterior puede rechazar un build determinista
  idéntico. La puerta correcta construye en staging nuevo, valida el log/artefacto y despliega
  atómicamente el SHA staged; un PBO viejo nunca es input del veredicto.
- **Gates tautológicos**: `effective_schema` y PBO no se verifican comparando datos derivados
  consigo mismos. Los negativos usan predecesor real, artefacto obsoleto y mutantes opacos.
- **Revisor por familia**: Grok y Opus deben correr en sesiones frescas de solo lectura. Cada
  feedback conserva veredicto individual aunque el transporte agrupe un paquete compartido.

## Criterios de Fase 0 para pasar a planes

1. **PASS**: las 35 entradas tienen hoja propia, clasificación, evidencia citada y al menos
   un gate positivo, uno negativo y un veredicto `INCONCLUSIVE/setup-failed` distinguible.
2. **FAIL**: una entrada se cierra solo porque otra del mismo paquete pasó, o un cambio no
   traza a DPF, o un verificador pasa contra el predecesor defectuoso.
3. **INCONCLUSIVE**: el hash de un fichero compartido cambia entre plan y escritura, falta el
   artefacto durable de una contribución, o un gate in-game no puede adquirir la caja sin
   interferir con un run ajeno.

## Referencias externas

No hacen falta fuentes web para este triaje: las afirmaciones ejecutables se apoyan en el árbol
vivo, el vault y la evidencia del buzón. Las APIs DayZ que se añadan durante implementación se
reverificarán contra `P:/scripts` antes de escribirlas.

## Referente que funciona

- **UI**: nombres únicos ya producen clicks manejados; el delta sospechado es la búsqueda global
  entre homónimos. Se medirá con un widget único y uno duplicado dentro/fuera de `root`.
- **PBO**: un build a staging vacío con artefacto validado es el control positivo; el predecesor
  real es un destino con PBO viejo y AddonBuilder que imprime `Build failed` con exit 0.
- **Telemetría**: `vehicle_trace start` ya resuelve conductor/coche/ownership en la misma capa
  owner-client; sirve de referente semántico para `vehicle_telemetry`.
- **Versión LFPowerGrid**: los dos ficheros actuales 1.2.4/1.2.4 son el control positivo; un
  fixture mutado 1.2.4/1.2.3 debe poner roja la puerta.
- **Sin referente único** para fingerprint de registro MCP y aislamiento de `storage_1`; se usarán
  matrices legacy/current/mismatch/rollback y controles de no borrado.
