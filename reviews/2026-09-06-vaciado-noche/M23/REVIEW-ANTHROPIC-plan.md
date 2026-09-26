VEREDICTO: APROBAR CON CAMBIOS MENORES — el plan es completo, sus citas son exactas sobre 8ff937e y el diseño del gate/journal es sólido, pero la regla «hash local ≠ autoridad ⇒ stale + reopen_mcp_client» invierte la señal 9b7b en el flujo real de este repo (el cliente corre desde fuente y no existe un paso de despliegue al que atar el promotor); hay que cerrar ese hueco (R-001) antes de abrir L4, y L0-L2 pueden empezar ya.

# REVIEW-ANTHROPIC — PLAN-M23 (revisor de otra familia)

- Revisado: `PLAN-M23.md` (586 líneas, sha256 `6b415884105e4461…`, byte-idéntico a la copia del cwd de Codex) contra el brief `BRIEF-PLAN-M23.txt`, las tres fichas, S7, los worktrees v4 (8732ee3) y v8 (6b7759e), el commit fijado 8ff937e (extraído con `git show` al scratchpad) y HEAD 0873d53.
- Fecha: 2026-09-06. Solo lectura sobre todos los árboles; sin juego, daemon, launcher ni MCP; sin suite completa.
- Reproduje la medición de §3.2 con la sonda de Codex (`probe_baseline.py`, red y subprocesos bloqueados por audit hook, LOCALAPPDATA redirigido): 60 tools standard / 61 exec_enforce en claude y codex, alias `from` publicado, 54 tests 0 fallos, `AUDIT_COUNTS={"DESC-ENUM-MISMATCH":1,"PARAM-NAME-DIVERGENCE":8}`, RC=0.

## 1. Completitud y consistencia interna (a)

| Contrato del brief §3 | Estado | Evidencia |
|---|---|---|
| 3.1 Resumen 10 líneas | PRESENTE, 10 puntos | PLAN-M23.md:5-14 |
| 3.2 Inventario S7/v4/v8 con citas | PRESENTE, 27 filas | PLAN-M23.md:44-72 |
| 3.3 Diseño (a)(b)(c), [EXACT]/[DESIGN], JSON, journal, compatibilidad, sellados | PRESENTE | PLAN-M23.md:83-378 |
| 3.4 Lotes con oráculo previo, mutantes, comando, dependencias, mínimo útil | PRESENTE, L0-L5 lineal | PLAN-M23.md:380-498 |
| 3.5 Escenarios R8 y matriz (seis escenarios pedidos) | PRESENTE, 23 filas | PLAN-M23.md:500-530 |
| 3.6 Gates + manual + parada | PRESENTE | PLAN-M23.md:532-560 |
| 3.7 ≤5 preguntas con recomendación y coste | PRESENTE, 5 | PLAN-M23.md:562-572 |
| 3.8 LO QUE NO PUDE VERIFICAR | PRESENTE | PLAN-M23.md:574-586 |

- **No está truncado**: §3.8 termina en frase completa; las 8 secciones existen en el orden pedido. Lo que falta es el cierre del encargo (`EXIT-PLAN-M23.txt` con DONE + líneas): en el cwd de Codex solo hay `EXIT-PLAN` con `RC=1` (marcador del arnés al morir por cuota). Consistente con el dato de contexto.
- Numeración y dependencias: L0→L1→L2→L3→L4→L5, sin ciclos. Todos los mutantes citados en la matriz §3.5 (J1-J8, S4, S5, S9, P3, P4) están definidos en §3.4. Los gates G0-G6 de §3.6 casan con los lotes.
- **Lote mínimo útil (PLAN-M23.md:450)**: L0+L1+L2 cierra la pieza (a) y, con ella, d366 (aterrizar el instrumento como gate real) y la parte decidible de 141e. **No cierra 9b7b**, que es la ficha operativa («eso es una noche»). Además L2 no es pequeño (ver R-002): el mínimo útil declarado no es mínimo. Ver R-006 para un reparto que sí cierra 9b7b con presupuesto corto.
- Contradicción interna menor: §3.2 y §3.8 citan `COMMANDS-ROOT.txt` y `RESEARCH-M23.md` como evidencia entregada y no existen (R-007).

## 2. Inbox de hallazgos

| ID | Prio | Sección del plan | Hallazgo | Hecho verificado que lo sustenta |
|---|---|---|---|---|
| R-001 | **P1** | §3.3(b) reglas de estado (PLAN-M23.md:254) y §3.3(c) paso 2 (:338) | La regla «hash local completo ≠ actual de su variante ⇒ stale» más «el publisher se integra como paso obligatorio del despliegue Python» presupone un despliegue que en este repo no existe: el cliente MCP se registra como `"$VenvPython" -m dayz_mcp --client …` y corre desde el árbol fuente; cualquier commit/edición que cambie el contrato sin correr el promotor hace que **la sesión vieja A (H0 = autoridad) salga `fresh` y toda sesión nueva B (H1) salga `stale` con `reopen_mcp_client`**, que no arregla nada. Es exactamente el fallo silencioso de 9b7b, pero con la señal mintiendo en la dirección contraria. | `tools/install-mcp.ps1:479,491,495` registra `-m dayz_mcp --client --keyfile … --client-platform claude`; `tools/build_native_launcher.py:53-71` (PACKAGED_MODULES) no sella `server.py`; memoria del proyecto 2026-07-27 confirma que `server.py` corre desde fuente; durante la propia autoría HEAD avanzó tres commits, dos de ellos sobre `server.py` (`git diff --numstat 8ff937e 0873d53`: 23/4 líneas). El plan no define ningún estado para «autoridad por detrás de las fuentes» (grep de `source_files` en el plan: solo :290, lado publisher). |
| R-002 | P2 | §3.3(a) migración (PLAN-M23.md:175,177,188) y L2 (:436-450) | PASS de L2 exige migrar TODAS las promesas de entrada de las 60 descripciones al bloque formal, leer cada validador real para los `runtime_cases` y cobertura completa; L2 es por sí solo XL, mezcla mecanismo con migración de contenido y bloquea el «mínimo útil». | Medido sobre 8ff937e: 60 tools, 212 parámetros, 18.503 caracteres de descripción, 6 tools con `enum`, 5 descripciones con lista `a|b`. `server.py` fijado tiene 5.005 líneas de validadores que hay que leer uno a uno (PLAN-M23.md:161: «se obtienen leyendo cada validador real al implementar»). |
| R-003 | P2 | §3.3(c) layout (PLAN-M23.md:278-286) | No se especifica cómo un cliente localiza **su** `installation_id`: `install.json` vive bajo `tool-registry/<installation_id>/` y contiene la raíz de distribución, pero el cliente solo conoce su raíz. Sin resolución determinista, o todo cliente sale `unknown/config_variant_missing`, o lee la autoridad de otro worktree (los tres comparten `%LOCALAPPDATA%/DayZ_MCP`). | `runtime_state.py:194-200` (`RuntimePaths.from_env`) resuelve solo `LOCALAPPDATA/DayZ_MCP`; el plan (:286) dice que las rutas «no viajan en tool_registry» y (:498) que `publish` compara identidad con `install.json`, pero no hay lookup raíz→id para el lector. |
| R-004 | P2 | §3.3(a) bloque de argumentos (PLAN-M23.md:165-167,175) y Q2 (:569) | «Una línea por parámetro» en las 60 tools añade ≈212 líneas + 120 delimitadores ≈ 10-12 KB (+55-65 % sobre los 18.503 caracteres actuales) al contexto de **cada** sesión de cada cliente, duplicando el `inputSchema` (19.413 caracteres) que el cliente ya recibe. El coste no está cuantificado ni gateado. | Medición propia (`probe_desc_cost.py`, misma sonda que Codex): `description_chars=18503`, `input_schema_chars=19413`, `params_total=212`, `instructions_chars=876`. El plan no cita cifra alguna de tamaño. |
| R-005 | P2 | §3.3 frontera (PLAN-M23.md:91) y matriz de sellados (:378) | El `enum` publicado y validado de `dayz_test_run.mode` se lee de `dayz_test_modes.public_mode_names()` al construir la app y en cada llamada, y `dayz_test_modes.py` **está sellado** en `app.pyz`: una edición del fuente sin rebuild hace que la «autoridad» M23 publique y acepte un modo que el worker sellado rechaza. El plan solo vigila el cierre de imports (L5), no la igualdad fuente↔copia sellada de los módulos que alimentan el contrato. | `server.py:1822-1837` fijado (`_patch_mode_enum_from_authority`: `prop["enum"] = list(dayz_test_modes.public_mode_names())`, llamado en :4918); `build_native_launcher.py:57` incluye `dayz_test_modes.py` en PACKAGED_MODULES; `decisions/decision-log.md:340-344` documenta que cambiar un módulo sellado exige rebuild + re-registro de fingerprints; `tools/native-launchers/dayz-test-v1/closure-manifest.json` existe con sha256 por entrada. |
| R-006 | P2 | §3.4 dependencias (PLAN-M23.md:464-466,450) | L4 depende de L3 entero, pero las dimensiones daemon (generación de `/status`), PBO (bytes) y la local de R-001 no necesitan journal. Partir L4 en L4a (snapshot + daemon + PBO + fuentes propias, tras L1) y L4b (comparación con autoridad, tras L3) hace que el mínimo útil L0+L1+L2a+L4a cierre 9b7b en su forma operativa. | `daemon.py:650` ya publica `daemon_generation`; `server.py:1667-1676` (cliente) obtiene `/status` con `GET`; ninguna de esas lecturas toca `registry_authority`. |
| R-007 | P3 | §3.2 (PLAN-M23.md:36,38,81) y §3.8 (:586) | Punteros a evidencia propia rotos: `COMMANDS-ROOT.txt` y `RESEARCH-M23.md` no existen; la salida literal de la sonda está en `out-codex-PLAN.log`; `EXIT-PLAN-M23.txt` no se escribió. | `ls` del cwd de Codex (`scratchpad/review-M23/`): COMMANDS-BRANCHES/RUNTIME/S7.txt, S7-/BRANCHES-/RUNTIME-NOTES.md, `evidence/manifest-live-head.json` (291 ficheros, commit 8ff937e), `EXIT-PLAN` = `RC=1`. La medición sí es cierta: reproducida por mí (RC=0). |
| R-008 | P3 | §3.3(a) (PLAN-M23.md:188) y L2 oráculos (:442) | A-01 (dirección del renombrado `type→classname`) queda solo advisory. Los predicados `present`/`absent` (:163) ya permiten el «manifiesto explícito del resultado esperado» que pedía la revisión S7; el plan no lo dice ni lo prueba. | `REVIEW-CODEX-r1.md:11` (A-01, P1: «añadir un manifiesto explícito… ninguna conserva `type`»); `README.md` S7 §Siguiente paso 3. |
| R-009 | P3 | L2 (PLAN-M23.md:436-448) | El `TypeError` con `enum` no escalar (d366) **ya está corregido en V**: `effective_schema.py` de 8ff937e es byte-idéntico al de v8 tras 7fe4cb1 y `_same_values` usa pertenencia por `==`. El digest lo marcó ausente; el plan lo cubre para el hash (L1, :428 «enum bool/number/objeto») pero el auditor NUEVO de L2 no tiene caso explícito. | `diff` v8↔8ff937e: IDENTICAL; `effective_schema.py:271-280` fijado. |
| R-010 | P3 | §3.3(b) (PLAN-M23.md:256) | 9b7b(c) (nota en `instructions`) no se incluye; cuesta una línea, las `instructions` ya entran en el hash (:119,:144) y es lo único que una sesión pre-M23 reabierta una vez ve sin llamar nada. | `server.py:3005-3025` fijado: `instructions` es texto estático de `build_app`. |
| R-011 | P3 | §3.3(b) legacy (PLAN-M23.md:258) — R7 call-sites | El plan conserva los cuatro campos M14 pero no lista los consumidores que los fijan y deben seguir verdes. | `tools/tests/test_mcp_tools.py:641-663` exige los cuatro campos en `bridge_status` y `:665-677` su ausencia en `/status`; `orphan_guard.py:843-855` keyset exacto de `daemon_status` (cubierto por S7-put-field-in-daemon-status). |
| R-012 | P3 | §3.3(c) paso 9 (PLAN-M23.md:345) | El lock compartido de lectores está justificado en Windows (`os.replace` sobre `head.json` falla con sharing violation si un lector lo tiene abierto sin `FILE_SHARE_DELETE`; el lector existente usa `os.open`), pero el plan no dice por qué existe, y «fail-fast» sin reintento convierte una lectura de 1 ms de cualquiera de N sesiones en `busy` del publisher. | `runtime_state.py:1795-1804` (`_read_pinned_regular_file` → `os.open(path, O_RDONLY…)`); `registry_lock.py:100-109` (`_LOCKFILE_FAIL_IMMEDIATELY`, `launcher_registry_busy`); solo `request_path_authority.py:193` usa `CreateFileW`. |
| R-013 | P3 | §3.6 parada (PLAN-M23.md:554-556) | L3 toca admin + filesystem + journal durable ⇒ DZ-R9 (proyecto CLAUDE.md) pide auditoría por ángulos con mínimo dos auditores; el plan trae mutantes J1-J8 y cortes instrumentados pero no nombra los ángulos ni el auditor independiente, ni declara que el banco J los sustituye. | `DayZ Projects/CLAUDE.md` §R9; `GL:425` deroga solo el bucle «re-audit hasta cero», no la auditoría. |
| R-014 | P3 | §3.2 deriva (PLAN-M23.md:36) y L0 (:412) | Todas las citas de `server.py` ≥298 están desplazadas en HEAD (+17 hasta 3306, +19 desde 3380) y `result_prune.py:69→75`. El plan ya exige rebase; L0 debería incluir el comando concreto (`git diff 8ff937e..HEAD -- tools/dayz_mcp`) y no trasladar números. | `git diff -U0 8ff937e 0873d53 -- tools/dayz_mcp/server.py`: hunks en 298(+17), 3306, 3311, 3375, 3380; `result_prune.py`: 17(+1), 58(+5). |
| R-015 | P3 | L0/driver `--mutant` (PLAN-M23.md:386) | La copia temporal del sujeto debe **excluir `__pycache__`** y arrancar con `-B`: la ronda 1 de S7 se contaminó por un `.pyc` residual. El plan fija `PYTHONDONTWRITEBYTECODE` (:397) pero no la exclusión al copiar. | `README.md` S7 §Método («la sonda… dejó `__pycache__/effective_schema.cpython-314.pyc` y el implementador reconstruyó desde ese bytecode»); el árbol vivo tiene `tools/tests/__pycache__/*.pyc`. |

Recuento: **1 P1, 5 P2, 9 P3**.

### Detalle de los P1/P2 con resolución propuesta

**R-001.** Tres piezas, todas acotadas y con datos que el diseño ya lleva:
1. El cliente compara `source_files[{path_relative, sha256}]` del artefacto de autoridad (PLAN-M23.md:290) con los bytes en disco de su propia raíz. Si difieren ⇒ `registry_state="unknown"` con razón `authority_behind_sources` y `remediation="registry_admin_required"` (o un valor nuevo `publish_required`), nunca `stale/reopen_mcp_client`.
2. Dimensión local nueva, sin journal: al arrancar, SHA-256 de cada módulo `dayz_mcp.*` cargado (misma enumeración que `daemon.py:609-618`, por hash y no por mtime, así S4 no aplica); en cada status, rehash del disco. `sources_changed_since_start=true` es la señal 9b7b pura («tu proceso es más viejo que el código instalado: reabre»), decidible sin promotor, y `environment_changed` la incorpora. Es la «alternativa sin formato nuevo» de :272 como **complemento**, no como sustituto de (c).
3. Decisión del dueño (pregunta nueva, §5): quién corre `promote_effective_schema.py publish` y cuándo; recomendación: hook git `post-commit`/`post-checkout` en el árbol registrado + paso manual documentado para ediciones sin commit; los clientes siguen sin publicar (mantener J5).

**R-002.** L2a: `effective_schema_contracts.py`, auditor, bloque y `runtime_cases` para 5 tools piloto (`engine_set`, `scene_raycast`, `dayz_test_run`, `pipeline_feedback`, `wait_for`: cubren validador simple, alias, enum sellado, lista `a|b` en prosa y unión `marker`), con una lista explícita y **solo decreciente** de tools no migradas publicada en `coverage.unsupported_claims` (es cobertura visible, no fixture de corrección, así no cae en la lección S7). L2b: migración del resto en tandas, cada tanda con su mutante C7 sobre una tool recién migrada.

**R-003.** Un índice `tool-registry/index.json` `{sha256(raíz normalizada NFC+casefold): installation_id}` escrito solo por el publisher bajo `registry.lock`, o un `installation.json` en la raíz de distribución (fuera de `tool_registry`, no viaja). El lector deriva su clave de `Path(server.__file__).resolve()`; sin entrada ⇒ `authority.state="missing"`, razón `installation_unregistered`.

**R-004.** Renderizar el bloque solo para claims **no expresables en JSON Schema** (los `runtime_cases`/validador y las restricciones combinadas de :163), y medir en L2 el delta de bytes de descripción con tope declarado (p. ej. ≤ +15 %). Las promesas ya materializadas en `inputSchema` (:177) no se repiten en prosa. Es Q2 con otra respuesta; ver §5.

**R-005.** El promotor añade a `source_files` cada módulo de PACKAGED_MODULES que participe en la publicación (`dayz_test_modes.py` hoy) y compara su sha256 con la copia dentro de `app.pyz` (es un zip) o con `closure-manifest.json`; drift ⇒ `publish` devuelve `invalid` con razón `sealed_module_drift`. Se declara en la frontera de :91: «lo que valida el worker sellado puede diferir del contrato publicado si hay drift; el promotor lo bloquea».

**R-006.** Reordenar: L0 → L1 → L2a → L4a → L3 → L4b → L2b → L5 (o L2b antes de L5). G4 se parte en G4a (local, sin autoridad) y G4b.

## 3. Citas verificadas (b)

Método: `git show 8ff937e:<ruta>` al scratchpad y `sed -n` de cada línea citada; S7/v4/v8/SDK leídos en su ruta; HEAD comparado con `git diff -U0`. «Correcta» = la línea contiene lo que el plan dice y la semántica adyacente confirma la afirmación.

| # | Cita del plan | Afirmación | Resultado |
|---|---|---|---|
| 1 | `V/effective_schema.py:29,40,57` (:6) | resolver/record/param = proyección | Correcta; fichero sin cambios en HEAD |
| 2 | `V/effective_schema.py:12` (:109) | import de `server` a nivel de módulo | Correcta |
| 3 | `V/effective_schema.py:197` = `V8:197` (:54) | `sorted(synonym_set)` ya está en V | Correcta (ficheros idénticos) |
| 4 | `V/effective_schema.py:283,308` (:188) | el auditor viejo calla si el schema no tiene `enum` | Correcta (`if not isinstance(enum_vals, list): continue`, :309) |
| 5 | `V/server.py:589,591,3042,4393,4923` (:69,:8) | overlay congelado al cerrar `build_app`; cinco `None` a `read_authority_marker` | Correcta en 8ff937e; **desplazada en HEAD** (606/608/3059/4412/4942) |
| 6 | `V/server.py:602` (:258) | cuatro campos legacy | Correcta; HEAD 619-623 |
| 7 | `V/server.py:574,578` (:132) | `unknown` cae a `standard/claude` | Correcta; HEAD 591/595 |
| 8 | `V/server.py:2985,2989` (:192) | `build_app(config) -> tuple[FastMCP, Any]`, `lifespan` | Correcta; HEAD 3002/3006 |
| 9 | `V/server.py:4384,4918` + «no hay `remove_tool`» (:80) | registro condicional de `exec_enforce`; patches finales; grep `remove_tool` = 0 | Correcta; HEAD 4403/4937 |
| 10 | `V/server.py:4406` (:66) | `vehicle_get_in_client(pos, timeout_s)` sin `seat_index`/`expected_type` | Correcta; HEAD 4425 |
| 11 | `V/server.py:4420,4421,4424` (:161) | `engine_set` rechaza fuera de `start|stop` antes de `call_bridge` | Correcta; HEAD 4439-4443 |
| 12 | `V/server.py:1667` (:206) | `bridge_status_payload` del cliente lee `GET /status` | Correcta; HEAD 1684. Además `_call` (1425) invoca `_ensure_daemon` (1462): el modo normal **puede spawnear**, lo que justifica `registry_only` |
| 13 | `V/daemon.py:333,609,630,650,652,661` (:10,:72,:376) | generación UUID; stale por mtimes; `daemon_status` keyset | Correcta; sin cambios en HEAD |
| 14 | `V/effective_schema_core.py:102,129,211` (:64) | registro proyectado; `canonical_json_bytes` no rechaza NaN | Correcta (`json.dumps` sin `allow_nan=False`) |
| 15 | `V/effective_schema_runtime_validators.py:31,53,79` (:65) | enumera IDs, no ejecuta validadores | Correcta |
| 16 | `V/effective_schema_catalog.py:43,53,60` (:66,:179) | CATALOG_RECORDS con `seat_index`/`expected_type` | Correcta |
| 17 | `V/tool_registry_fingerprint.py:24,169,206,269,358,379,647,670` (:67,:68) | cinco claves; NFC; `artifact_version` 5; promotor en `_MINIMUM_PRODUCERS` y ausente del árbol | Correcta (`git ls-tree 8ff937e` no tiene `tools/promote_effective_schema.py`; sí `tools/mcp_capture.py` y `tools/checks/`) |
| 18 | `V/runtime_state.py:194,244,254,339,1795,1890,1924,1933,1957` (:70,:71,:274,:347) | `from_env`; `JsonlAuditWriter` con `threading.Lock` y reescritura completa; `_atomic_write_text(expected_sha256)`; `atomic_write_bytes` sin él | Correcta (reescritura en 345-348) |
| 19 | `V/registry_lock.py:80,87,100,109` (:70,:339) | `r+b` exige fichero; fail-fast; `launcher_registry_busy` | Correcta |
| 20 | `V/request_path_authority.py:477,523` (:260) | patrón de identidad antes/durante/después | Correcta la firma; semántica no verificada por mí |
| 21 | `V/loopback.py:2100` (:268) | `record_poll` recibe version/instance/pid/creation/caps | Correcta la firma; lista de campos no verificada |
| 22 | `V/make_release.py:22` (:268) | `PBO_ASSET_NAME` | Correcta |
| 23 | `V/orphan_guard.py:843,855` (:376) | keyset exacto de `daemon_status` | Correcta |
| 24 | `V/result_prune.py:69` (:376) | conserva claves desconocidas | Correcta en 8ff937e; **desplazada en HEAD** (:75); semántica confirmada (docstring :78-79) |
| 25 | `V/build_native_launcher.py:53` (:378) | PACKAGED_MODULES sella `server_cli/host_config/dayz_test_modes/request/worker`, no `server.py` ni `effective_schema*` | Correcta (15 módulos) |
| 26 | `V/server_cli.py:8,61` (:132) | `unknown` permitido; `grok→unknown` | Correcta (el alias está en :9) |
| 27 | `V/README-mcp.md:132` (:206) | `bridge_status()` sonda barata sin caja | Correcta |
| 28 | `V/product-spec.md:86,88,89,90,91` (:96) | E5 exige fingerprint canónico y `reopen_mcp_client` sin reiniciar daemon | Correcta |
| 29 | `V/tests/test_effective_schema.py:18,37` (:62,:63) | `unittest.TestCase`; test que exige hallazgos vivos | Correcta |
| 30 | `V/CLAUDE.md:14`, `V/GATES.md:24` (:81,:560) | «58 tools»; ROOT-REVIEWS | Correcta (ficheros sin trackear en git; leídos del árbol de trabajo) |
| 31 | `S7/effective_schema-r2.py:20,28,33,50,61,79` (:46-49) | códigos; construye otra app; proyección | Correcta |
| 32 | `S7/test_effective_schema-r2.py:11,111,152,164` (:48,:51) | casos alias/unión; A-03 | Correcta |
| 33 | `S7/gate-v2.py:76,205`; `REVIEW-CODEX-r2.md:21`; `README.md:3,61` (:50,:76) | fixtures legibles; bypass por snapshot | Correcta |
| 34 | `V4/_gate.py:92,139,670` (:52,:53) | generador; oráculo proyectado; contar llamadas | Correcta |
| 35 | `V8/effective_schema.py:78,88,122,128,197,271` (:55) | uniones/enums no hashables | Correcta; fichero idéntico al de V |
| 36 | `V8/_gate.py:278,307,458,479,495,521,637,660,810,875,936,1052,1065` (:56-61,:77,:79) | `_expected_registry` proyecta required/default/type/enum; `PYTHONHASHSEED=0` en env y `-I` en el worker | Correcta |
| 37 | `V8/_bench/must_reject/c3:11,35`, `H3:39`, `N1:98`, `run_bench.py:128`, `PROCEDENCIA.md:118` (:60,:61,:79) | mutantes; limpieza de site-packages | Correcta |
| 38 | `V8/tests:16,175`; `_REPORT-GATE-V8.md:281`; `_REPORT-FIX-UNION.md:338,396` (:62,:77,:78) | funciones libres; «registro completo»; 10 tests | Correcta |
| 39 | commit v8 `139c9f4` (:54) | dos ficheros; no añade hash/CAS/journal | Correcta (`git show --stat`: PROCEDENCIA.md +21, `effective_schema.py` 1/1) |
| 40 | `SDK fastmcp/server.py:315,435`; `types.py:1315,1339`; `shared/session.py:346` (:100,:80) | `list_tools`; `remove_tool`; `Tool(extra="allow")`; `model_dump(by_alias, json, exclude_none)` | Correcta (mcp 1.27.2) |
| 41 | `GL:404,417-423,425` (:554-560) | regla de parada | Correcta |
| 42 | §3.2 deriva: «23 añadidas/4 retiradas en server.py; HEAD 0873d53» (:36) | | Correcta (`git diff --numstat`) |
| 43 | §3.2 medición: 54 tests, 60/61 tools, 8+1 hallazgos (:38) | | **Reproducida** por mí (RC=0) |
| 44 | `F:1,50,102` (:26) | offsets de las fichas | Correcta |
| 45 | `COMMANDS-ROOT.txt`, `RESEARCH-M23.md` como evidencia entregada (:36,:38,:81,:586) | | **Rota**: no existen (R-007) |

**Recuento: 45 afirmaciones; 44 correctas sobre 8ff937e (9 de ellas desplazadas en HEAD por los tres commits: filas 5-12 y 24); 0 falsas o inventadas sobre código; 1 puntero a evidencia propia roto (fila 45).** Las filas 20 y 21 se verificaron solo por firma.

## 4. Cobertura por ficha (c)

**d366 — instrumento construido y no aterrizado.** Pide: aterrizar `resolve_effective_schemas`/`audit_contracts` como autoridad; gate no derrotable por snapshot ni por fixtures legibles; deja abiertos A-01, falsos positivos posicionales y `TypeError` con enum no escalar.
- Cubierto: aterrizaje como gate de repositorio con oráculo independiente (SDK serializado por el test, PLAN-M23.md:425), fixtures generados con `challenge_nonce` (:390), comparación JSON íntegra (:130,:142) y mutantes R1-snapshot/R2-projection/C2-fixture-dispatch (:431,:444). Falsos positivos posicionales: eliminados por construcción al sustituir la heurística por bloque formal parseado por delimitadores (:165).
- Parcial: A-01 solo advisory (R-008; expresable con `present/absent`). `TypeError` ya corregido en V; falta el caso explícito en el auditor nuevo (R-009).
- Hueco propio: el plan no cita el **archivo fuente** del instrumento actual como identidad (es idéntico al v8 6b7759e); L1 debería registrar ese hash en el manifest de L0.

**141e — el contrato no es auditable leyendo el cuerpo.** Pide: instrumento post-`build_app` (capa 2, con alias y perfiles) y declarar la frontera Enforce.
- Cubierto: captura tras todos los patches (:192,:4918-4923 del fijado), `effect_verification="in_game_required"` (:158), frontera explícita (:91,:552), y la «cuarta fuente» de S7 (validadores de petición) cubierta con `runtime_cases` contra el wrapper real con fakes (:154,:170).
- Hueco: la fuente sellada del enum de `dayz_test_run.mode` (R-005) es una cuarta-bis fuente que el plan no nombra.

**9b7b — sesión anterior al despliegue no ve los verbos nuevos.** Pide (a) generación + hash del registro en `bridge_status`/`session_status`; (b) aviso `tool_registry_stale: reopen`; (c) mínimo, nota en `instructions`.
- Cubierto: (a) y (b) con el bloque `tool_registry` (:210-248), `registry_only` sin caja ni spawn (:198-204), generación con baseline `first_observed` (:206), PBO por bytes (:260-268), gate manual G6 A/B (:544-550).
- Parcial: (c) descartada de facto (R-010).
- **Defecto de cobertura**: en el flujo real del repo la señal se invierte si no se corre el promotor (R-001). Tal como está, 9b7b queda cerrada en el diseño y abierta en la operación.
- Lo que 9b7b **no** pide y el plan tampoco promete (bien): recarga en caliente.

## 5. Riesgos de diseño (d)

1. **Gate derrotable por snapshot/fixtures.** Bien tratado: nonce por corrida (:390), oráculo = `app.list_tools()` serializado por el test sin pasar por el candidato (:425), comparación de JSON íntegro (:130), prohibición de derivar `expected` del output (:173), C6 mata «expected desde el schema» (:444), calibración con PASS propio (:392). Residuo: exclusión de `__pycache__` en la copia temporal (R-015). Correcto además que el driver sea de repositorio y no juez de delegación (:386): las defensas de aislamiento de v8 quedan fuera con motivo (:61).
2. **Compatibilidad con clientes abiertos.** Aditiva: `registry_only: bool = False` no invalida `bridge_status()` (schema actual `{"properties": {}, "type": "object"}`, sin `additionalProperties:false`); `session_status()` mantiene firma; cuatro campos M14 conservados (:258; tests en R-011); `result_prune` no toca claves desconocidas. El único cambio masivo visible es el bloque de descripción (R-004). Una sesión pre-M23 no recibe nada retroactivo y el plan lo declara (:525,:582).
3. **Módulos sellados / rebuild.** Verificado: `server.py`, `effective_schema*`, `runtime_state.py` y los módulos nuevos no están en PACKAGED_MODULES (`build_native_launcher.py:53-71`); el cliente registrado corre desde fuente (`install-mcp.ps1:479`). La afirmación «M23 no exige rebuild» es cierta. Riesgo no nombrado: R-005.
4. **Journal en `%LOCALAPPDATA%`.** Diseño sólido: punto único de commit = `os.replace` de `head.json` (:343), contratos y eventos content-addressed e idempotentes (:342), CAS por epoch+revision+sha(head) que evita A→B→A (:340), recover que archiva sin adoptar (:349-351), reset con epoch nueva (:355). Crash entre escrituras: los huérfanos son inertes y el siguiente lector solo cree a `head` (matriz :507-513, correcta). Dos sesiones: solo leen; un cliente nunca publica (J5). Huecos: lookup de instalación (R-003) y el porqué del lock de lectores + reintento acotado (R-012). `%LOCALAPPDATA%` no está bajo OneDrive: correcto para escrituras atómicas.
5. **Daemon reiniciado a mitad.** El registro no cambia con el daemon; `daemon.changed` sale de `daemon_generation` de `/status` (`daemon.py:650`), baseline `startup|first_observed` (:206) y `registry_only` devuelve `cached/unknown` sin afirmar vida (:206,:518). Correcto. La publicación no toca al daemon (promotor offline), así que un reinicio durante `publish` no afecta al CAS.
6. **PBO redesplegado sin reinicio.** Bytes con handle estable, sin memoización por mtime/tamaño (:262, mutante S4), `loaded_sha256=null` declarado (:268). Correcto y honesto; la ruta del PBO por configuración de instalación, no por parámetro de tool (:260), evita el recorrido de raíces arbitrarias.

## 6. Las cinco preguntas al dueño (e)

| Q | Planteamiento | Recomendación del plan | Mi juicio |
|---|---|---|---|
| Q1 observar sin daemon/caja | Bien planteada | `registry_only` opcional en `bridge_status` | De acuerdo: el modo normal puede spawnear (`server.py:1425/1462`), así que el flag es necesario; un verbo nuevo no aporta nada que el flag no dé. |
| Q2 qué hace mecánica la prosa | Bien planteada, coste incompleto | Bloque de argumentos desde claims + prueba schema/validador | De acuerdo en claims independientes + `runtime_cases`; **no** en «una línea por parámetro en las 60 tools» sin medir: falta el coste de contexto (R-004). Alternativa: bloque solo para lo no expresable en `inputSchema`. |
| Q3 formato del journal | Bien planteada | JSON inmutable por evento + `head.json` atómico, sin GC | De acuerdo; es la opción más simple que cumple CAS y recuperación con los helpers que ya existen. |
| Q4 `type`/`classname` y sesiones abiertas | Bien planteada | Conservar nombres; coexistencia solo advisory | De acuerdo; añadir el manifiesto A-01 como claims (R-008) para que una fase futura de renombrado sí sea bloqueante. |
| Q5 qué es «cambio de PBO» | Bien planteada | Bytes del PBO desplegado acreditado; `loaded_sha256=null` | De acuerdo. |

**Preguntas que faltan y el dueño debe decidir antes de implementar:**
- **Q6 (bloquea L4): ¿quién corre el promotor y cuándo?** El cliente corre desde fuente y no hay paso de despliegue Python. Opciones: (a) hook git `post-commit`/`post-checkout` en el árbol registrado + paso manual documentado [recomendada; coste: un hook y una línea en README-mcp]; (b) solo manual [coste: la autoridad envejece y la señal se invierte, R-001]; (c) auto-publicación por la primera sesión nueva [rechazada por integridad: J5 debe seguir muriendo].
- **Q7 (bloquea L2): ¿se acepta +10-12 KB de descripción por sesión, o el bloque se limita a claims no expresables en JSON Schema?** [recomendada la segunda; coste: menos «legibilidad» en la descripción, que el `inputSchema` ya cubre].
- **Q8 (cambia la matriz de estados): ¿entra en M23 la dimensión local «fuentes propias cambiadas desde el arranque» (SHA-256 del cierre cargado)?** [recomendada: sí; cierra 9b7b sin depender del promotor y es Python puro; coste: un estado más y ~30 hashes por status].

## 7. Próximo paso

- APROBAR CON CAMBIOS MENORES: aplicar R-001 (con Q6/Q8 decididas), R-002/R-006 (reparto de lotes) y R-003 antes de la ronda 1 de L4; R-004 (Q7) antes de L2b; R-005 en L5. L0, L1 y L2a pueden empezar sobre HEAD 0873d53 tras el rebase de citas (R-014).
- No hace falta rehacer el plan ni reabrir las decisiones (a)(b)(c) del dueño.

## 8. LO QUE NO PUDE VERIFICAR

- No existen los módulos M23 ni sus gates: no ejecuté `check_m23.py`; los seis comandos de §3.4 siguen siendo propuesta.
- No lancé juego, daemon, launcher ni suite completa; no usé herramientas MCP; no abrí `app.pyz` (la lista de sellados sale de `build_native_launcher.py:53-71` y de la memoria del proyecto del 2026-07-27, no de inspeccionar el zip) y `grep` de `dayz_test_modes` en `closure-manifest.json` no dio hit: la afirmación de R-005 sobre el sellado se apoya en PACKAGED_MODULES, no en el manifest.
- `loopback.py:2100` y `request_path_authority.py:477,523`: verifiqué firma y ubicación, no la semántica completa que el plan les atribuye.
- R-012: la sharing violation de `os.replace` con lector concurrente es semántica Win32 documentada (`_wopen` sin `FILE_SHARE_DELETE`), no la medí en este host.
- R-004: la cifra +10-12 KB es aritmética sobre 212 parámetros y el formato literal de :165, no un render real del bloque.
- No reejecuté los bancos v4/v8 ni leí íntegras `S7-NOTES.md`, `BRANCHES-NOTES.md`, `RUNTIME-NOTES.md` ni el log de 1,7 MB de Codex (solo `grep` para localizar la salida de la sonda).
- No verifiqué que `Tool(extra="allow")` (`types.py:1339`) implique que FastMCP **emita** campos extra en `tools/list`; la sonda publicó solo `name/description/inputSchema/outputSchema` con `exclude_none=True`.
- `V/CLAUDE.md` y `V/GATES.md` no están en git (`?? CLAUDE.md`, `?? GATES.md`): las cité del árbol de trabajo, igual que el plan.
- No comprobé si `decisions/decision-log.md` contiene una decisión que prohíba nuevos ficheros en `%LOCALAPPDATA%/DayZ_MCP`; el `grep` por `journal|tool_registry|fingerprint|reopen` no devolvió ninguna que contradiga el plan.
