# Diseño de cierre — 35 entradas del buzón DayZ MCP

Fecha: 2026-08-30; revisión vinculante: 2026-08-31  
Estado: APROBADO POR EL USUARIO; revisión formal única con Grok 4.6 Medium mediante Cursor  
Research: `research/2026-08-30-pipeline-inbox-triage-codex.md`  
Ledger: `GATES.md` + 35 hojas `gates/inbox-*.md`

## Objetivo

Cerrar las 35 entradas abiertas sin perder su identidad, usando para cada una la cadena
obligatoria:

1. plan Codex;
2. revisión de plan Grok 4.6 Medium mediante Cursor;
3. materialización Codex solo si Grok da PASS;
4. revisión de implementación Grok 4.6 Medium mediante Cursor sobre los bytes finales;
5. resolución durable en el buzón.

Un cambio de alcance, API, criterio o bytes tras una aprobación invalida el hash y reinicia la
cadena del feedback afectado. Si el cambio toca un paquete compartido, reinicia todas las hojas
que trazan a ese paquete.

## Diseño de orquestación

### Identidad individual, transporte por paquete

Cada feedback tendrá un documento propio bajo `plans/inbox-20260830/` con:

- hechos `path:line`;
- disposición `CAMBIO` o `EVIDENCIA`;
- archivos exactos que puede tocar;
- pasos etiquetados `[EXACT]` o `[DESIGN]`;
- al menos un PASS, un FAIL y un INCONCLUSIVE/setup-failed;
- compatibilidad, legacy y rollback cuando aplique;
- hash SHA-256 del plan que recibe cada revisor.

Para evitar releer el mismo árbol 70 veces, se transportan hasta cinco planes por sesión fresca.
El prompt obliga al revisor a emitir un bloque y un veredicto independiente por feedback ID. Un
PASS global sin 35 decisiones individuales es inválido. Cada sesión recibe hashes exactos, las
autoridades v6 y sólo las fuentes crudas/código necesarias para sus cinco fichas como máximo.

Esto agrupa lectura, no autoridad: ninguna entrada hereda el PASS de su vecina.

### Ruta de revisión fijada por `delegar`

- Gate único: `cursor-grok-4.6-medium × cursor-agent`, sesión nueva, `--mode ask`, workspace ciego
  con la raíz fuente añadida y comprobación explícita del modelo servido en el evento `init`, del
  mismo `session_id` y de `result.subtype=success,is_error=false`.
- Cada lote contiene como máximo cinco fichas. La sesión sólo puede abrir las autoridades v6, los
  planes y fuentes exactas inyectadas; `reviews/**`, `gates/**`, evidencias históricas y recorridos
  recursivos amplios quedan prohibidos. Cualquier contaminación, modelo distinto o resultado no terminal produce
  `INCONCLUSIVE`, no PASS.
- El gate usa un único turno JSON por lote; no añade una calibración que duplique el peaje fijo de
  Cursor. Las revisiones de implementación nunca reutilizan la sesión que revisó el plan.

Historia de ruta: Grok Build agotó saldo con HTTP 402 el 2026-08-30 y se usó Opus por
`prime-agent`; después el usuario retiró Sonnet y, finalmente, ordenó mover las revisiones no
iniciadas y el paso 5 a Grok mediante Cursor. Las corridas Opus ya iniciadas se conservan como
descubrimiento, no como gate. Los 70 registros Sonnet permanecen sólo en
`reports/2026-08-31-sonnet-retirement-manifest.md`; ninguna preauditoría condiciona el gate vivo.

### Árbol compartido

- No se crea branch ni worktree sobre los bytes compartidos sin aprobación adicional.
- Antes de cada escritura se recalcula SHA-256 de todos los ficheros del plan. Drift equivale a
  `INCONCLUSIVE`, rebase de evidencia y nueva revisión; no se fuerza el parche.
- Cada línea nueva debe trazar a una hoja. Lo adyacente se registra, no se refactoriza.

## Ampliación DPF aprobada

Las 35 entradas sirven intenciones existentes y el usuario aprobó añadir siete criterios el
2026-08-30; no se declaran completados hasta que sus gates pasen:

### B4 — Control UI e input engine-native

La superficie pública conduce teclas y widgets sin input del SO. Una búsqueda de widget ambigua
falla cerrada o se acota por raíz; el resultado identifica petición, match y handler. El modo de
evento completo entrega coordenadas del widget y secuencia down/up/click, con bubbling explícito
y verificable. El input de tecla ya presente conserva una puerta de registro y dispatch.

### C3 — Observabilidad semántica

`entities_query` expone el predicado real de cargo sin listas de classnames y
`vehicle_telemetry` rellena de forma coherente presencia, asiento, tipo y classname cuando el
resolvedor owner-client encuentra coche. Una consulta acreditada sin jugadores informa
`no_player_connected`; un fallo remoto conserva estado no verificado.

### C4 — Esperas headless conservan señales inmediatas

`wait_for(log_matches)` conserva un lookback acotado para observar una respuesta publicada antes
de crear su marker. Los gates independientes fijan 200/201 líneas y el flujo causal
`action→respuesta ya publicada→wait_for` con ventanas 200/0.

### D3 — Espacio de coordenadas de captura

El crop normalizado usa por defecto el client area que comparte coordenadas con `ui_tree`; el
espacio legacy de ventana exterior permanece seleccionable y explícito. Un client rect ausente o
inválido falla cerrado cuando se pide espacio cliente.

### E5 — Superficie autocontenida, interpretable y auditable

El Knowledge Pack se puede preparar desde MCP usando solo rutas instaladas y selladas. El esquema
efectivo se obtiene después de registro/aliases/perfiles, incluye los validadores públicos
materializables y produce un fingerprint de la superficie. Sus gates fallan contra el predecesor
real y contra mutantes opacos, no contra snapshots conocidos. La misma intención cubre la
interpretación auditable de la superficie: registro stale por sesión, descripciones e instrucciones
que distinguen transporte de efecto, explican C4 y conservan resoluciones append-only con
referencia durable acotada.

### E6 — Integridad de artefactos auxiliares

Los mods y wrappers que alimentan un verdict del harness acreditan versión y procedencia
source→staging→PBO→publish. Un PBO preexistente, un fatal con exit 0 o una entrada stale fallan; un
SHA determinista idéntico sólo pasa cuando el candidato nació en staging y su contenido se comparó
contra una fuente independiente.

### H13 — Diagnóstico seguro de lifecycle y runs compartidos

El lifecycle detecta Steam ActiveProcess obsoleto antes de lanzar, distingue proceso cliente de
readiness, documenta reattach y errores `mode × run_id`, informa edad/actividad/generation sin
preemptar servidores vivos y mantiene fail-closed toda identidad, path, proceso o fuente no
acreditada. Las misiones absolutas y la persistencia por modset pertenecen a H11/H6; H13 sólo consume ese
contrato al explicar el estado del run, no amplía las raíces permitidas.

## Paquetes y orden

### P01 — Input headless y registro

Feedbacks: `1082`, `7743`, `3bb4`.

- Auditar los bytes dirty existentes de `key_press`/`player_respawn` como contribución externa.
- Añadir gates de schema, validación, peer client, dispatch y llamada a `Mission.OnKeyPress`.
- No reimplementar un verbo ya presente ni afirmar que inyección host-side funciona.

### P02 — Observabilidad de entidades y vehículos

Feedbacks: `40e4`, `0de3`.

- Añadir `has_cargo` a cada `MCPEntityHit`, derivado de `EntityAI.GetInventory().GetCargo()` tras
  verificar la firma vanilla exacta.
- Rellenar en telemetría `found/seated/seat/type/classname` desde `Transport` antes del cast opcional
  a `CarScript`; las métricas de coche son un eje posterior.
- Gates negativos: objeto sin cargo; jugador a pie; transporte presente con `CrewMemberIndex=-1`;
  y un `Transport` no-`CarScript` que conserve identidad/asiento sin inventar métricas de coche.

### P03 — Contrato UI

Feedbacks: `2762`, `f4f2`, `b2c4`, `20be`, `21f5`, `47c9`, `7743`, `f6ac`.

- Eco de `path`, `root` y `text` donde aplique.
- Resolución por raíz opcional; 0 matches=`widget_not_found`, >1=`ambiguous_path` con diagnóstico.
- Fallos de negocio UI vuelven como payload estructurado, no excepción destructiva.
- `ui_click` distingue secuencia directa/completa, usa centro real del widget y bubbling opt-in.
- `not_handled` queda documentado como «handler ejecutado y declinó».
- Gates con widget único, homónimos en raíces distintas, handler que consume, handler que declina
  y ausencia de client/ScriptView. El efecto in-game, no solo `ok`, decide el gate final.

### P04 — PBO fresco y despliegue recuperable

Feedback: `668f`.

- Construir en staging nuevo, capturar log, rechazar marcadores de fallo aunque exit=0, exigir el
  PBO staged, ejecutar el build gate sobre ese PBO y desplegar por reemplazo de sibling.
- En `PackOnly`, desempaquetar el PBO staged y exigir conjunto de entradas + SHA byte a byte contra
  el source compilable. En build binarizado, scripts/config convertida se comparan contra source y
  cada payload binarizado contra el output temporal de **esa misma corrida**; no basta buscar tokens.
- Un destino viejo nunca es evidencia del build nuevo. Un build determinista de SHA idéntico sí
  puede pasar si nació en staging y fue validado.
- Si el destino está bloqueado, el reemplazo falla y el PBO anterior permanece intacto.
- M24 usa únicamente `%TEMP%\dayz-mcp-m24\<txid>` para build/candidate y publica sólo
  `P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo`, con backup sibling create-only, journal y CAS de
  preimagen; `_compile` dentro del repo no es staging válido.
- Aplicar a las dos plantillas runtime (`.agents` y `.claude`) y a la copia viva de LFQuad2; no
  barrer copias de otros mods sin un encargo separado.
- Traza DPF: E6; el wrapper externo forma parte de la cadena de evidencia que el harness usa para
  decidir qué bytes se prueban, no del instalador E3.

### P05 — Esquema efectivo, modos y fingerprint

Feedbacks: `9d46`, `ffc7`, `d366`, `141e`, `f201`.

- Promover `effective_schema` desde
  `reports/2026-08-30-inbox-implementation/M23/dayz-effective-schema-v1.json` sólo después de
  rehashear productores y rerun v5; los otros cuatro canónicos M23, candidates, backups, journal y
  lock están cerrados en `physical-ownership-addendum-v1.md`.
- Mover el dueño de modos a un módulo sin ciclo y derivar schema/texto/validator del mismo dato.
- La autoridad leaf define tres modos públicos y `offline` interno; la firma usa `str` y M22
  materializa post-registro el enum efectivo `server|all|client`, evitando otro `Literal` manual.
- Registrar al final `dayz_effective_schema` (0 args), con payload version 1 de instructions,
  tools, input schemas, constraints públicos y scope `wire|in_game_required`; nunca certificar el
  efecto del bridge desde Python.
- Integrar validadores públicos que puedan materializarse sin análisis semántico arbitrario.
- Separar extractor y catálogo; inventario, validator cases y mutantes son fixtures independientes.
  La enumeración pública↔catálogo es simétrica y falla ante validator sin entrada o entrada huérfana.
- Calcular fingerprint canónico del schema efectivo; no esconder tools por conteo.
- Incluir `dayz_test_modes.py` en ambas allowlists del launcher, manifest con hash propio, `app.pyz`,
  rebuild reproducible y reaprobación registrada. Observar enum/tool cero-argumentos tanto con
  `app.list_tools()` como mediante un `tools/list` stdio real.

### P06 — Knowledge Pack autocontenido

Feedback: `782b`.

- Añadir prepare/status MCP sin argumentos ni rutas libres.
- Extraer determinísticamente desde el pack instalado a un fichero temporal y reemplazar el índice
  solo tras validarlo; pack ausente produce `knowledge_pack_missing`, no una receta shell.
- `find/show` remiten a status/prepare MCP, nunca a una receta shell, y funcionan inmediatamente
  después de prepare.
- Exponer por separado estado del índice y estado del pack; cubrir seis estados atómicos
  (`missing|invalid|valid` por cada eje) y su producto cartesiano completo de nueve celdas.
  La firma cero-argumentos debe rechazar propiedades extra en el wire, no sólo en un helper Python.

### P07 — Lifecycle y ergonomía

Feedbacks: `cc2d`, `a396`, `7c88`, `4d66`, `8f8c`.

- Preflight Steam: ActiveUser no nulo, PID registrado vivo y ejecutable `steam.exe`.
- Separar `client_process_alive` de `client_ready`; elegir discriminador mediante viability tests.
- Aceptar alias o ruta absoluta solo si está dentro de `mission_roots` de la policy sellada.
- Validar combinaciones mode/run_id arriba y devolver condición exacta.
- Documentar el reattach client+run_id y el nombre de clase Enforce de `action_use`.
- Añadir causa explícita `no_player_connected` y datos de edad/liveness al rechazo por ocupación.
- Registrar de forma acotada `run_command_activity` sólo tras aceptar un comando para un binding
  acreditado y correlacionarlo por `Binding.run_id`; el start exitoso es la actividad basal. El
  evento lleva reason/decision/duration y se escribe fuera del lock, cubriendo enqueue normal y
  exec. De ahí se derivan `last_activity_age_s` y `recent|stale|unknown`; timestamps futuros o fallo
  del writer dan unknown. El umbral de presentación es 900 s y nunca autoriza stop/reap.
- Derivar launch/current generation y tombstones acotados del audit JSONL existente, incluido stop
  de run ya retirado; ausencia de evidencia queda unknown.
- Steam stale devuelve PID registrado/vivos acotados y remedio estable sin usuario/path completo.
- No añadir reaper que mate un servidor vivo ni preemption FIFO automática.
- Implementar startup wait/deadline en `execute_wait_for`, dueño M22: reintentar únicamente el error
  público estructurado `game_not_ready:reason=server_poll_stale` hasta el deadline original. Es una
  lectura y no requiere inventar un `run_id`; `no_run` por fallo de snapshot,
  `client_not_polling` en la vía server actual, versión, auth, path, argumentos y transporte ambiguo
  fallan pronto.

### P08 — Persistencia por modset

Feedback: `4407`.

- Traza DPF: H11; el aislamiento ocurre únicamente al preparar un server/offline nuevo de
  `dayz_test_run`, y nunca durante `client`/reattach.

- Fingerprint SHA-256 de listas de mods normalizadas por rol, preservando el orden dentro de
  cada rol porque el orden de carga forma parte del contrato.
- El árbol es `<mission>/storage_1` y el sidecar sibling `<mission>/storage_1.modset.json`; la misión
  debe estar ya resuelta dentro de roots sellados. `<profiles>/storage_1` es un señuelo negativo.
- Matriz cerrada: sin storage/sidecar se publica el marker y no se rota; sin storage con marker
  válido se reutiliza; sin storage con marker inválido/mismatch se archiva sólo el marker. Storage
  legacy sin marker rota sólo el árbol; storage+marker válido reutiliza; storage+marker inválido o
  mismatch rota ambos con nombres reservados y compensación inversa de todas las fases previas.
- El digest `storage-tree-v1` enumera recursivamente, sin seguir symlinks/reparse points, rutas POSIX
  relativas ordenadas y tipo; para ficheros añade tamaño+SHA-256. Incluye directorios vacíos y falla
  cerrado ante escape, control o colisión case-fold. El SHA-256 del JSON canónico acredita el árbol.
- Antes de la primera mutación escribir+fsync un journal versionado con txid, digest, paths sellados,
  fase y backups reservados. Cada transición se publica por atomic-replace del journal. Antes de
  clasificar A–F, un pre-pass exige cero o un journal activo: `prepared|storage_moved|marker_moved`
  se compensa en reversa y reacredita; `marker_published` se valida y finaliza preservando pending;
  múltiple/malformado/`recovery_required` o compensación no convergente bloquea todo lanzamiento con
  `storage_recovery_required`. Un journal convergido se renombra a un sibling `.completed` reservado.
  Nunca reclasificar un journal activo como «sin storage».
- Escribir sidecar nuevo de forma atómica antes de lanzar. Incluye schema/version/algoritmo, listas
  canónicas y `rotation_pending`; un marker huérfano es un estado esperado, no prueba de mundo. La
  integración limpia pending sólo después de acreditar que el nuevo server/offline creó storage.
  Devolver `storage_rotated` sólo cuando esta llamada aparta un árbol real; pending conserva el aviso
  tras una caída. El backup de marker se informa por separado.
- Cuando rota, la respuesta pública añade aviso inequívoco de reset de mundo/personaje.
- Legacy: marker ausente se trata como desconocido. Rollback: el código viejo ignora el sidecar;
  restaurar requiere retirar el storage nuevo y devolver el backup documentado.
- Sólo journal activo y sidecar actual pueden hacer atomic-replace con preimagen/txid acreditados;
  storage, sidecars previos y backups usan rename a destino inexistente y nunca overwrite/delete.
- Gates: los seis estados de la matriz, digest de árbol con directorio vacío, journal huérfano en
  cada fase, journal múltiple/malformado, colisión de nombre, fallo de cada rename, compensación
  exitosa/fallida, fallo de sidecar y publish parcial; ninguna ruta puede perder bytes.
- El worker corre dentro del launcher sellado y su terminal actual tiene un conjunto cerrado de
  claves. Por tanto este paquete incluye el helper, el worker, la ampliación coordinada del recibo,
  `native_bundle.py`, la reconstrucción del launcher y su verificación de closure. El hook corre sólo
  antes de crear server, `all` (una vez antes del server) u offline nuevo; nunca en client attach,
  kill, preflight ni build-only. No se presentará como cambio que evita tocar el ejecutable empaquetado.

### P09 — Paridad de versión LFPowerGrid

Feedback: `344d`.

- No cambiar 1.2.4.
- Endurecer `P:\LFPowerGrid_dev\tools\build_guarded.py` y su test: deben existir exactamente las dos
  fuentes declaradas, cada una con exactamente un literal de versión, y ambas deben ser `1.2.4`.
- Gates antes de crear el PBO: PASS sólo `1.2.4/1.2.4`; FAIL ante fuente ausente, literal ausente o
  duplicado, `1.2.4/1.2.3` y valor coherente pero equivocado (`1.2.5/1.2.5`).
- Cerrar la ficha como estado ya corregido sólo tras acreditar esos gates y el PBO candidato contra
  el mismo expected independiente; no modificar los dos sources productivos si no hay drift.
- Traza DPF: E6; la paridad y el PBO candidato acreditan qué versión se está probando/publicando.

### P10 — Instrucciones y regresiones conocidas

Feedbacks: `243b`, `fc6e`, `d73b`, `251d`, `55dd`.

- Traza DPF: B1 para posición/flags, C4 para lookback causal y E5 para instrucciones y separación
  entre transporte y efecto.

- Mantener lookback default 200 y su explicación.
- Describir y=0 por mecanismo/flags, sin prometer igualdad de `pos_real.y` y superficie.
- Explicar que `ok` transporta el comando y que el campo de efecto decide éxito semántico.
- Gate de instrucciones contra pérdida de conocimiento y gate causal
  `action → respuesta ya publicada → wait_for`: 200 satisface y 0 vence.

### P11 — Registro MCP obsoleto

Feedbacks: `103f`, `9b7b`.

- Traza DPF: E5.

- Capturar al construir la app un snapshot inicial por proceso/sesión y compararlo con la autoridad
  post-arranque promocionada por M23, producida por una vía independiente. Dos llamadas al mismo
  helper/app no acreditan frescura.
- M14 captura por proceso FastMCP y `ControlIdentity.session_id` los campos
  `tool_registry_fingerprint`, `tool_registry_captured_at`, `tool_registry_source_stale` y
  `tool_registry_remediation="reopen_mcp_client"`; identidad desconocida nunca se declara fresh.
- Mensaje estable `reopen_mcp_client`; no se promete hot reload.
- Estados cerrados `fresh|stale|unknown`; ausencia, ambigüedad o identidad de sesión no acreditada
  nunca se declaran fresh.
- Limitación explícita: una sesión anterior a la introducción de este detector no puede avisar de
  su propia ausencia.

### P12 — Buzón durable

Feedback: `c7ca`.

- Traza DPF: E5.

- Mantener `resolution` acotado y añadir `evidence_ref` relativo a una de cuatro raíces durables;
  se rechazan URI, absoluto, drive y traversal.
- Calcular `age_s` y `age_label` legible (`s/m/h/d`) al listar; no persistir edad derivada.
- Formato append-only backward compatible: lectores viejos ignoran el campo nuevo.
- Rollback lee las mismas líneas; no hay migración destructiva.
- Gate público completo: `pipeline_resolve` añade el evento, `pipeline_inbox` refleja last-wins y
  limpieza de ref; `age_s/age_label` se calculan desde el `ts` original del feedback, no desde la
  resolución.

### P13 — Captura en client area

Feedback: `268a`.

- Conservar en la imagen elegida el client rect validado por el backend.
- Añadir `crop_space=client|window`, default `client`; transformar el bbox normalizado al rect
  cliente antes de downscale.
- `window` preserva comportamiento anterior. Un rect inválido con `client` falla cerrado.
- Distinguir `window_surface`, `client_surface` y `effective_surface`; hashes/estadísticas declaran
  cuál miden y `fullres` sigue siempre la efectiva.
- Gates públicos con borde/título artificiales y viewport coloreado conocido; default cliente nunca
  puede incluir chrome y sus metadatos permiten demostrarlo sin inferir por dimensiones.

## Viability tests globales

1. **PASS**: cada hoja tiene plan hash, PASS Grok/Cursor, bytes/evidencia materializada, PASS Grok/Cursor de
   implementación y resolución durable; el checker cuenta todas las puertas vivas como satisfechas.
2. **FAIL**: cualquier revisor devuelve REJECT, un hash cambia, un gate pasa contra el predecesor
   defectuoso, una entrada queda sin bloque individual o el buzón aún la lista abierta.
3. **INCONCLUSIVE**: drift concurrente, provider/model distinto del solicitado, stopReason no
   terminal, artefacto de revisor incompleto, falta de caja para un gate in-game o setup que no
   alcanza la capa que el gate pretende medir.

## Decisiones que requieren aprobación

### D1 — Transporte de revisiones

**Decisión aprobada**: una sesión Grok/Cursor fresca por lote de hasta cinco fichas y fase, con un
veredicto y hash individual por feedback. Sonnet queda fuera del flujo y del ledger ejecutable por
decisión expresa del usuario de 2026-08-31; su evidencia anterior permanece sólo como historial.

### D2 — Ambigüedad UI

**Recomendación**: `ambiguous_path` fail-closed por defecto y `root` para desambiguar. Alternativa:
conservar «primera coincidencia» y solo informar `matches`, que mantiene compatibilidad pero todavía
puede accionar el widget equivocado.

### D3 — Storage legacy

**Recomendación**: marker ausente o mismatch rota de forma automática a backup recuperable, como
pide la ficha y exige fail-closed. Alternativa: marker ausente solo avisa/preserva, que evita un
reset inicial pero conserva el crash de modstorage precisamente en el estado legacy más frecuente.

El usuario aprobó D1–D3 y la ampliación DPF el 2026-08-30, y pidió que el plan de ejecución
mantenga módulos suficientemente aislados para delegarlos en paralelo a varios subagentes. La
consecuencia vinculante es que cada módulo declarará `OWNS` lógico de archivos, interfaces de
entrada/salida, dependencias y un gate de ausencia de solape. Dos módulos que escriban el mismo
fichero no se ejecutan en paralelo: se fusionan o se ordenan explícitamente.

## Descomposición ejecutable v6 (vinculante)

La unidad de revisión continúa siendo cada feedback; la unidad de escritura es el módulo con
`OWNS` exclusivo definido en `plans/inbox-20260830/00-execution-dag.md` y materializado a rutas
físicas en `plans/inbox-20260830/physical-ownership-addendum-v1.md`. El graph version
`inbox-20260831-v6` es la única fuente normativa de nombres, aristas e interfaces. Resumen:

- Ola 0: `M01-CONTRACT-FREEZE → M00-DIRTY-AUDIT`.
- Ola 1, paralela: `M02`, `M04`, `M06`, `M07`, `M08`, `M09`, `M10`, `M11`, `M12`, `M15`, `M16`.
- Ola 2a: `M02→M03`, `M06→M17`, `M12→(M13∥M18)`.
- Ola 2b: `(M02+M04)→M05`, `M13→M14`, `(M16+M17+M18)→M19`.
- Ola 2c: `(M15+M18+M19)→M20`.
- Ola 2d: `(M19+M20)→M21`.
- Ola 3: `M22-SERVER-INTEGRATION`, único dueño de `tools/dayz_mcp/server.py`.
- Ola 4: `M23-FINAL-SCHEMA-PROMOTION ∥ M24-PBO-BUILD-DEPLOY`.
- Ola 5: `M25-EVIDENCE-CLOSE` y resoluciones individuales.

Las interfaces congeladas de UI, modos, registry, lifecycle, storage, inbox, Knowledge, captura y
PBO viven también en el DAG v6; cualquier cambio las rehashea y reinicia la revisión Grok/Cursor afectada.

## Apéndice histórico — descomposición v1 supersedida (NO EJECUTAR)

La v1 se retiró para evitar dos grafos contradictorios. Su procedencia queda en el historial de cambios; para ejecución y revisión sólo es válido `plans/inbox-20260830/00-execution-dag.md`.
