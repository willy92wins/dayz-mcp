# Council — DayZ-MCP como producto presentable

> Revisión de seis lanes ciegas sobre el **repo público clonado limpio** (`e106cf2`), no
> sobre el árbol de trabajo. El sustrato es lo que recibe un tercero.
> Encargo: encontrar todo lo que un Discord de modders con sesgo anti-IA usaría para
> tumbarlo, antes de que lo hagan ellos.
> Fecha: 2026-08-21.

## Método

Seis ángulos disjuntos, briefing común (`DOSSIER.md`), lanzados en paralelo y sin verse.
El orquestador escribió su lane **antes** de abrir ninguna otra: sin ese orden la ceguera
es teatro. Todo hallazgo de una lane es **candidato**; solo asciende a hecho tras
re-verificación propia contra el fichero real (`G2` cite-then-verify).

| Lane | Ángulo | Estado |
|---|---|---|
| Grok (`grok-4.6`) | Reusabilidad y portabilidad | ✅ 8 hallazgos · `end_turn`, 21 turnos, $0,32 |
| Fable (subagente ciego) | Caza de slop | ✅ 10 hallazgos |
| Fable (subagente ciego) | Superficie vista por un agente débil | ✅ 15 hallazgos |
| Codex (`gpt-5.6`, effort max) | Corrección y puerta retail | ✅ 16 hallazgos |
| Claude (orquestador) | Promesa del README vs evidencia | ✅ 6 hallazgos |
| Mecánica (orquestador) | Consistencia comprobable | ✅ 5 hallazgos |
| ~~Gemini~~ | — | ❌ `429 TerminalQuotaError`, free tier, 20 req/día |
| ~~Composer / Cursor~~ | — | ❌ `ActionRequiredError: usage limit` |

**Sustituciones declaradas.** El ángulo de Gemini (agente débil) se reasignó a un
subagente ciego. El de Composer (consistencia mecánica) lo cubrió el orquestador con
medición directa. Ninguno de los dos ángulos quedó sin cubrir, pero la diversidad de
proveedor se perdió en ambos y eso baja el valor de su convergencia.

**Contención.** Las lanes corrieron en solo lectura. Foto de hashes de los 234 ficheros
commiteados antes de lanzar, verificada después: **cero modificaciones**.

---

## Lo que aguanta, y hay que poner por delante

Esto no es cortesía: es el material con el que se gana a este público, y hoy está
enterrado o sin medir.

- **Un clon fresco pasa la suite en verde.** Medido en una máquina que nunca había visto
  el proyecto: venv nuevo, Python 3.14.3, solo las tres dependencias declaradas →
  `Ran 1770 tests in 247.252s — OK (skipped=29)`. La promesa del README es cierta.
- **Esos 1770 tests no son humo.** Conteo AST independiente: 1770 funciones de test, solo
  19 sin assert directo; muestra profunda de 5 ficheros con HTTP vivo contra peer falso
  por sockets, asserts sobre routing, códigos y payloads, cero `assertTrue(True)`. El
  conteo AST coincide **exactamente** con el `Ran 1770` ejecutado: dos vías
  independientes, mismo número.
- **El bridge Enforce aguanta en lo estructural** — que es justo lo que este público lee
  mejor que nadie: pool de objetos preasignado (`MCPBridge.c:54`, `:78-83`), presupuesto
  de dispatch por tick (`MAX_DISPATCH_PER_TICK`), arrays reutilizados con `Clear()` en
  `IsSpawnReady` (`:2736-2738`), callbacks con guarda y release. El problema del bridge
  es dialecto y duplicación, no ingeniería de tick.
- **El proyecto documenta lo que NO le funciona**, con la cita del código vanilla que lo
  explica: `MakeScreenshot` roto (T165276), conducción client-authoritative
  (`actionstartengine.c:51-58`), `exec_enforce` limitado en headless. Ese registro es
  exactamente lo que un artesano respeta, y hoy está en un fichero de 47 kB en castellano.
- **Hay mediciones in-game reales y fechadas**: error posicional 0,0313 m contra marcador
  independiente; `ticks_in_flight = 4741` con delay inducido de 600 ms; rumbo impuesto
  90° / medido 92,1°, impuesto 270° / medido 270,1° con desviación lateral de 2 cm en
  42 m. Ninguna aparece en el README.

---

## Registro de hallazgos

Severidad: **BLOQUEANTE** = si se presenta con esto, alguien lo tumba en público.
Coste: S = horas · M = días · L = semana o más.
Todas las rutas son relativas a la raíz del repo público.

### Grupo 1 — El producto no arranca en una máquina ajena

| # | Hallazgo | Sev. | Cita | Lane | Coste |
|---|---|---|---|---|---|
| **P-1** | El daemon escribe a `P:\DayZ_MCP_dev\reports\security\migration\P0S-IDENTITY-V2` en **cada arranque**. Sin esa ruta: `FileNotFoundError [WinError 3]` → `runs_backup_directory_unavailable` → no arranca. Como todo modder de DayZ tiene `P:` montado por exigencia de Bohemia, el caso típico no es que falle: es que **le crea una carpeta `DayZ_MCP_dev` dentro de su work drive**. | BLOQUEANTE | `identity_migration.py:106-108` (constante), `:1250` (default del parámetro), `:1280` (el `mkdir` que lanza), `daemon.py:222` (llamada incondicional; el override solo existe para tests) | Grok | S |
| **P-2** | 29 rutas absolutas a `C:\Program Files (x86)\Steam\...` clavadas en el builder del launcher nativo, y el `.cpp` compilado invoca esa misma ruta. Steam en `D:` o DayZ Tools fuera de sitio → `locked_file_missing`. La policy sellada deja cambiar `diag_executable`; el C++ no. | BLOQUEANTE | `build_native_launcher.py:67-96`, `:200-202`; `native-launchers/dayz-test-v1/src/launcher.cpp:1009-1010` | Grok | M |
| **P-3** | Dos instaladores, tres versiones de Python declaradas (README dice 3.10+, `README-mcp.md` dice 3.14, el `.ps1` fuerza `py -3.14`), y `-Register` exige **Claude y Codex**: un modder con solo Claude Code se queda a medias. | GRAVE | `README.md:88` vs `tools/README-mcp.md:5` vs `pyproject.toml:8` vs `install-mcp.ps1:36-44`; `install-mcp.ps1:414-418`; `install_mcp.py:409-412` | Grok | M |
| **P-4** | El lock cita un artefacto `project_relative` que **no viaja en el clon**, y el test que debería cazarlo hace `continue` cuando el fichero falta: la suite sigue verde. Además `psutil` tiene dos cadenas de suministro (wheel vendored + PyPI) según el instalador. | MEDIO | `dependency-lock.json:10-16`, `:50-55`; `tests/test_dependency_lock.py:214-223`; `install_mcp.py:960-968` vs `install-mcp.ps1:325-327` | Grok | S |

### Grupo 2 — El puente no es consumible

| # | Hallazgo | Sev. | Cita | Lane | Coste |
|---|---|---|---|---|---|
| **B-1** | `dayz_test_run` **no carga el puente**: `_mods()` compone `base_mods + "@" + runtime.mod + extra_mods`, y `runtime.mod` es el proyecto bajo test. El launcher del propio MCP produce un juego con el que el MCP no puede hablar, y devuelve `succeeded`. El fallo aflora tres verbos después como `server_poll_stale`. | BLOQUEANTE | `dayz_test_worker.py:204-210`; `dayz_test_readiness.py:1` (succeeded = proceso+UDP, nunca "el bridge sondea"); `launcher-policy.example.json:6` (sin `@DayZ_MCP`) | Grok + agente débil | M |
| **B-2** | **No hay receta de PBO, ni script de empaquetado, ni bikey.** La instrucción entera es una frase. Con `verifySignatures=2` un PBO sin firmar no carga, y ese es el modo por defecto de cualquier servidor público. | GRAVE | `README.md:98`; `git ls-files` sin resultados para bikey/keys/pack | Grok | M |
| **B-3** | Tres `modded class` sobre lo más pisado del vanilla (`MissionServer`, `MissionGameplay`, `CarScript`) sin una línea sobre load order ni convivencia. Llaman `super` correctamente — pero eso no está dicho, y un `CarScript::OnInput` ajeno sin `super` se come el drive del MCP. | MEDIO | `MissionServer.c:1,21`; `MissionGameplay.c:1,21`; `MCP_CarScript.c:601-605`; `config.cpp:8` | Grok | S |

### Grupo 3 — El código no aguanta la lectura de un artesano

| # | Hallazgo | Sev. | Cita | Lane | Coste |
|---|---|---|---|---|---|
| **S-1** | **Una clase entera muerta en el módulo de seguridad.** `AccreditedDaemonPolicy` se define completa con su `__post_init__` de validación y se pisa 70 líneas después con un rebind al contract. Verificado **en runtime**: el nombre resuelve a `daemon_policy_contract`. Setenta líneas de validación de seguridad que Python descarta al importar, publicadas. Ctrl+F, dos minutos. | BLOQUEANTE | `daemon_policy.py:284-354` (definición), `:356-357` (rebind); comprobado con `python -c` que resuelve al contract. Patrón menor igual: `host_config.py:119-121` (wrapper público sin un solo caller) | Fable slop | S |
| **S-2** | **La duplicación ya parió un bug.** La misma struct Win32 declarada dos veces con anchos distintos. Medido: `pinned_keyfile` ocupa **32 bytes** y lee `Directory` en offset **24** — fuera de los 24 que el kernel rellena; `DeletePending` (offset 20, ancho 4) se traga además el byte real de `Directory`. La copia correcta usa `c_ubyte`. | GRAVE | `pinned_keyfile.py:36-44` (`wintypes.BOOL`) vs `request_path_authority.py:42-50` (`ctypes.c_ubyte`); consumo en `pinned_keyfile.py:192-193`. Medido con `ctypes.sizeof` + offsets | Fable slop | S |
| **S-3** | "Ruta Windows absoluta válida" implementado **5 veces** con nombre propio y semántica distinta (unos exigen NFC, otros no); 7 módulos con validador `splitdrive` propio. El día que una acepte lo que otra rechaza, el mismo path pasa una capa y revienta en otra — y S-2 demuestra que ya ha pasado. | GRAVE | `daemon_policy_contract.py:28`, `daemon_policy.py:96`, `dayz_test_request.py:108`, `dayz_test_worker.py:118`, `native_broker_protocol.py:119` | Fable slop | M |
| **S-4** | `build_app` son ~1.358 de las 3.386 líneas de `server.py`: **el 40% del fichero en una función**, con las 52 tools dentro como closures y 60 defs anidadas. Imposible testear una tool sin construir las 52. Del paquete: 1.345 funciones, 13 superan 200 líneas. | GRAVE | `server.py:1954` (inicio) + 3.386 líneas totales; siguientes peores: `session_coordination.py:265-695` (431), `vehicle_trace.py:282-680` (399) | Fable slop | M |
| **S-5** | **El bridge etiqueta todos sus logs como prueba de concepto.** Es lo que el admin ve en su RPT, línea a línea, del mod "terminado". | MEDIO | `MCPBridge.c:3397` — `Print("[MCP-POC] " + message);` | Fable slop | S |
| **S-6** | ~70 referencias a un tracker privado que no existe en el repo (E4, F3.x, BUG-nnn, NUEVO-3, LL-156, D-55, GATE4A-002), más comentarios sueltos en castellano. Leen como tickets alucinados o como fuga de contexto privado; ambas confirman "nadie editó esto para publicarlo". | MEDIO | `daemon.py:5`, `loopback.py:980,1584,2991-2993`, `core.py:123`, `orphan_guard.py:556`, `instance_fence.py:1`, `inbox.py:29`, `MCPBridge.c:39` | Fable slop | S |
| **S-7** | Dios-objetos sin anotar: `SessionCoordinator` son 3.554 líneas y 111 métodos (lease FIFO + WAL + audits + tombstones + box queue + faults) con **0,5% de comentarios**; `process_lifecycle.py` con 0,2%. La masa no es relleno — es lógica densa real que nadie puede auditar ni heredar. Para este público, "solo su IA lo entiende" es tan letal como "está hinchado". | MEDIO | `session_coordination.py:181-3735`; `loopback.py:588-2248` (`ServerState`, 1.660 líneas); 13 funciones con anidamiento ≥6 | Fable slop | L |
| **S-8** | Un tercio del paquete se vigila a sí mismo: de 36.338 líneas, **12.189 (33,5%)** son maquinaria de acreditación e integridad del launcher y solo **5.632 (15,5%)** son las tools de juego. `security_runtime_audit.py` (1.751 líneas) es un linter del propio código fuente, empaquetado como módulo de runtime y **jamás invocado en producción**. | MEDIO | `security_runtime_audit.py`; única mención en producción es un comentario en `native_launcher_backend.py:901-903`; sus consumidores son 3 tests | Fable slop | S |
| **S-9** | El Enforce habla con acento no nativo: **47 bucles `while` frente a 2 `for`, cero `++`, 38 contadores `i = i + 1`** — mientras el source vanilla que este público lee a diario usa `for (int i = 0; i < n; i++)`. Y copia-pega literal entre los dos bridges: ~344 líneas idénticas (10,1% del bridge de servidor), incluido un percent-encoder entero. | MEDIO | `EncodeQueryValue` idéntico en `MCPBridge.c:1914-1967` y `MCPClientBridge.c:3054+`; conteo sobre los 4 `.c` principales | Fable slop | M |
| **S-10** | `validate_command_args`: 17 ramas `if cmd ==` y **76 `return False, "bad_args"` idénticos** en 316 líneas, con la validación duplicada a mano en la capa MCP. Cada tool nueva se añade copiando la rama anterior. | MENOR | `loopback.py:236-551`; duplicados en `server.py:2694`, `:3038`, `:3074` | Fable slop | M |

### Grupo 4 — Un agente débil no puede usar esto

| # | Hallazgo | Sev. | Cita | Lane | Coste |
|---|---|---|---|---|---|
| **A-1** | **La cuarentena retail es invisible para el agente que la sufre.** `grep -c retail server.py` = **0**: el 409 `retail_quarantine` del loopback colapsa a `remote_error` genérico. El escenario más probable de un modder (su DayZ retail abierto) termina en un error opaco, en bucle, sin que nada mencione retail. | BLOQUEANTE | `loopback.py:1107-1114` (emisión); `server.py:102-148` (`_REMOTE_ERROR_CODES`, 45 entradas, cero "retail"); `server.py:201-206` y `:765-778` (el colapso) | Agente débil | S |
| **A-2** | **Los valores por defecto de una tool no pasan su propio validador.** `vehicle_prepare_fixture` tiene `radius: float = 100.0` y el bridge rechaza `radius > 50` con `bad_args` pelado. El límite 50 no está escrito en ninguna parte de la superficie. Mismo patrón en `telemetry_read` (`radius=0.0` por defecto, y `mode=object_at` exige `>0`). | BLOQUEANTE | `server.py:2545` vs `MCPBridge.c:18` (`TELEMETRY_OBJECT_AT_MAX_RADIUS = 50.0`) y `:1667`; `server.py:2508`, `:2515-2516` | Agente débil | S |
| **A-3** | **101 puntos de emisión de `bad_args` pelado** (43 en Python, 58 en Enforce) frente a 12 mensajes que nombran el campo. `_invalid()` colapsa **24 causas distintas** en un solo código. `_require_vec3` **recibe** el nombre del campo y lo descarta. De ~120 códigos visibles al agente, ~20 son accionables. El patrón bueno ya existe y está pagado en `playbook_tool.py`. | GRAVE | `dayz_test_request.py:53-54` + 24 call-sites; `server.py:160`, `:1280-1289`, `:2936-2937`; patrón correcto en `playbook_tool.py:158-166`, `:182-191` | Agente débil | M |
| **A-4** | **El bucle acción→confirmación invierte veredictos.** `wait_for(log_matches)` es la primitiva que las propias descripciones recomiendan, y su `lookback` de 200 rebobina todos los logs: la línea de la sonda anterior satisface la espera nueva. No acepta `marker`, aunque `logs_since` sí devuelve uno. Coste ya pagado: 4 corridas in-game, ~3 h, veredictos que acusaban al mod con cero fallos reales. | GRAVE | `server.py:1530-1541`, `:1586`; firma en `:3167-3174` sin `marker` frente a `logs_since` en `:2306-2345`; recomendación en `:3133-3136` | Agente débil | M |
| **A-5** | **La demo canónica del producto se atasca.** Nada dice cuál de `vehicle_enter` / `vehicle_get_in_client` usar, y el de nombre natural es el que no cuaja: `seated:1` y acto seguido `engine_set` → `not_seated`. El único hilo entre ambos es la palabra "owned", a 500 líneas de distancia. El prefijo `LOW-LEVEL: prefer X` existe y funciona, pero no se aplicó a este par. | GRAVE | `server.py:2462-2468` vs `:2918-2924` vs `:2929-2934`; `MCPBridge.c:3093-3099`; el prefijo en `server.py:2001-2002` | Agente débil | S |
| **A-6** | **`world_spawn(rotation)` no es un ángulo.** Va al 4º argumento de `CreateObjectEx`, que el propio `dayz-harness-apis.md` documenta como `int iRotation = RF_DEFAULT` — un flag. Quien pase `rotation=90` no obtiene 90 grados. Y ~10 parámetros numéricos más sin unidad (`cam_orientation`, `fov`, `show_time`, `time`, `min_duration`, `phase`) frente a 2 bien documentados. | GRAVE | `server.py:2404-2411`; `MCPBridge.c:2408-2410`, `:549`; `dayz-harness-apis.md:31,86,217`. Contraste correcto: `server.py:2576-2582`, `:2676-2683` | Agente débil | S |
| **A-7** | **El `object_id` solo sirve para borrar.** `world_spawn` lo devuelve y `object_delete` lo acepta, pero `object_anim` y `object_inspect` direccionan por `type+pos`, con radio de 25 m y `ambiguous_object` sin desempate ni lista de candidatos. Un A/B con control y sujeto del mismo classname es imposible: hubo que teleportar el objeto de estudio. | GRAVE | `MCPBridge.c:3078`, `:20` (`OBJECT_LOOKUP_RADIUS = 25.0`), `:1444-1447`; `server.py:2421` vs `:2605-2611`, `:2660-2665`, `:2676-2683` | Agente débil | M |
| **A-8** | **El MCP no menciona el Knowledge Pack ni una vez.** Cero menciones en README, `README-mcp.md` y arquitectura. Mientras, la superficie exige ese vocabulario sin fuente: `object_inspect` pide nombres de memory points, `action_use` el classname exacto, `ui_tree` el nombre del root, `world_spawn` flags ECE con un solo ejemplo. Un agente con el MCP y sin el pack no está degradado: **está mudo, y no sabe por qué**. | GRAVE | `grep -ri knowledge` = 0 en los tres documentos; exigencias en `server.py:2660-2665`, `:3137-3141`, `:1984-1985` | Agente débil | S |
| **A-9** | La respuesta de escritura de `object_anim` informa la fase **vieja**: escribes `phase=1` y el `ok=1` dice `"phase":0`. Un campo con el nombre exacto de lo que acabas de escribir y el valor contrario, dentro de una respuesta de éxito. | MEDIO | `MCPBridge.c:1219-1222` (`SetAnimationPhase` interpola + `GetAnimationPhase` inmediato); `result_prune.py:11-13` | Agente débil | S/M |
| **A-10** | `capture_screenshot` tiene **la descripción más larga del catálogo** (~800 caracteres sobre presupuestos de tokens, escalas, webp, crop) y **ni una palabra** del frame congelado sin foco de ventana, que es el único caveat que produce conclusiones falsas. | MEDIO | `server.py:2844-2850`; bug medido y reproducido en el buzón (frame idéntico byte a byte con `ok=1`) | Agente débil | S |
| **A-11** | El veredicto de `playbook_run` **lee al revés**: `PASS_WITH_WARNINGS` cuenta como PASS y aprueba el dosel sin calibrar (el `FAIL` del techo se degrada a `WARN`), mientras `certified: false` acompaña a todo resultado. La regla operativa ("no spawnees salvo PASS limpio") vive solo en ficheros que el agente no ve. | MEDIO | `playbooks/runner.py:60`, `:574`; `playbooks/place_safely.toml:17`; regla en `playbooks/README.md:80-83` y `tools/README-mcp.md:131`, ausente de `server.py:3287-3291`; `playbook_tool.py:24`, `:140-144` | Agente débil | S |
| **A-12** | **La superficie sola cuesta ~8-10k tokens.** 53 registros, 178 parámetros públicos, 8.042 caracteres de descripciones. `dayz_test_run` sola tiene 19 parámetros. Con la config por defecto de un runtime local (`num_ctx` 4096) el catálogo no cabe antes del primer prompt. Precedente medido: la sonda qwen3.5 necesitó 548 s y 15 llamadas para lease + teleport + release. | MEDIO | Medido sobre `server.py`; `:2130-2150` (los 19 parámetros); mecanismo de registro condicional ya existente en `:2898` | Agente débil | M |
| **A-13** | `session_status` expone dos colas y **ningún "qué hago ahora"**: `grep blocked_on` = 0. Un agente leyó `owner=null` + `claimable=false`, concluyó "no puedo encolarme" y delegó en el humano — el antipatrón que el producto promete evitar. La cola correcta era la otra. El TTL de 120 s no aparece en ninguna descripción de tool. | MEDIO | `server.py:2092-2098`, `:381`; patrón `hint` ya existente en `process_lifecycle.py:38-39`, `:266`; diseño del campo ya redactado en el buzón | Agente débil | S |
| **A-14** | Con el juego apagado el agente recibe **`version_blocked`**: el nombre miente y empuja a investigar versiones, que es un callejón. La fe de erratas está escrita en **dos** READMEs que el agente no ve. Cuando un error necesita nota aclaratoria en dos documentos, el bug es el nombre del error. | MEDIO | `loopback.py:1262`; excusas en `playbooks/README.md:55-57` y `tools/README-mcp.md:151`; el estado ya se distingue en `server.py:267-268` | Agente débil | S |

### Grupo 5 — La presentación se contradice a sí misma

| # | Hallazgo | Sev. | Cita | Lane | Coste |
|---|---|---|---|---|---|
| **N-1** | **El documento de arquitectura publicado declara el proyecto no implementado**, y el README enlaza ahí como referencia autoritativa. El árbol local ya tiene la línea corregida, sin publicar. Es la frase más citable en contra que hay en el repo, la firma el autor, y está a un clic. | GRAVE | `dayz-mcp-architecture.md:9` — "Generado 2026-06-06. Documento de diseño; aún NO implementado." | Mecánica | S |
| **N-2** | **El primer paso del loop que vende el README exige compilar un launcher con MSVC.** La tabla presenta `dayz_test_run` como paso 1; la sección de instalación exige policy a mano, relock del toolchain, build reproducible y bootstrap del registry. Y es **falso como descripción del producto**: verificado que `server.py:28` importa `dayz_test_tool` a nivel de módulo pero el registry solo se resuelve en la llamada — **las otras 52 tools funcionan sin nada de eso**. Existe un camino corto real y el README no lo cuenta. | BLOQUEANTE | `README.md:34` (tabla) vs §"What is deliberately not in this repo"; `dayz_test_tool.py:12`; cadena de imports verificada | Claude | S |
| **N-3** | ~~El repo no sabe cuántas tools tiene.~~ **CORREGIDO 2026-08-21 tras medirlo bien: el hallazgo era mío, no del repo.** El árbol local tiene **53 tools vivas** al instanciar la app; el gate `PublicToolCountDocsTest` (`tools/tests/test_install_mcp.py:1262`) cuenta **52** descartando `ui_dialog` deliberadamente, y el titular del README dice **52**. Está gateado y es correcto. Mis cifras de «51 / 52 / 53» salían de contar decoradores `@app.tool` con un regex — que cuenta también los registros condicionales — sobre un clon un commit por detrás. **Residuo real, mucho menor**: `ui_dialog` es una tool viva y llamable que el README oculta, y el gate lo *obliga* con `assertNotIn("ui_dialog", readme)`. Es deliberado, no un olvido; la pregunta abierta es si una tool que un agente puede llamar debe estar sin documentar. | MENOR | `test_install_mcp.py:1262-1275`; medido instanciando `build_app` | Claude | S |
| **N-4** | **Tres de los cinco documentos publicados están en castellano** — incluidos los dos que el README ofrece como prueba. La mejor evidencia del proyecto es justo la que ese público no puede leer. | GRAVE | Conteo de marcadores: `product-spec.md` 217, `dayz-mcp-architecture.md` 109, `dayz-harness-apis.md` 38; README y `README-mcp.md` 0 | Mecánica | S |
| **N-5** | **La primera frase del README vende `exec_enforce`** y el `product-spec` lo declara fuera de alcance por limitación de motor. Dos lecturas, ambas malas: o el lector ve "ejecución arbitraria de Enforce" en un repo que va a meter en su servidor, o descubre que el titular vende algo no ejecutable. | GRAVE | `README.md:5`, `:67` vs `product-spec.md:170` (GATE4B-LIM); raíz en `dayz-mcp-architecture.md:267` | Claude | S |
| **N-6** | **La evidencia que convertiría a este público está enterrada** en un fichero de 47 kB. El README abre con afirmaciones de capacidad — el registro que este público castiga — y no enseña una sola medida. | GRAVE | `product-spec.md` (criterios A1/A2/A5/B3 con sus números) vs `README.md:1-30` | Claude | S |
| **N-7** | **No existe ningún quickstart.** Cero resultados de "quickstart"/"getting started" en todo el repo. La instalación ofrece dos caminos sin decir cuál te toca. Recuento del camino mínimo real hasta la primera tool respondiendo: **~16 pasos, con caídas en 8 de ellos**. | MEDIO | `grep -ri` = 0; `README.md:105-126`; recuento de la lane de portabilidad | Claude + Grok | S |
| **N-8** | **El árbol público es el directorio de trabajo**: `spike0/`, `run-fase1/2/3.ps1`, `run-poc.ps1`, `p0s_*`, `mcp_client.py` (1.806 líneas, driver de fases), `task9_build_a_smoke.py` (4.034 líneas) + su test de 5.897. **10.389 líneas** que ningún README menciona. Y no es residuo inerte: **la captura de pantalla de producción carga `spike0/mcp-grab.ps1` como script canónico**. | BLOQUEANTE | `mcp_capture.py:82-86`; ficheros listados en `tools/` | Fable slop | S |
| **N-9** | Dos ficheros commiteados llevan la ruta personal del autor y el nombre de un mod privado ajeno; `product-spec.md:139` y `dayz-mcp-architecture.md:117` filtran más contexto interno. `rg Users.guill` en el minuto tres. | MEDIO | `git ls-files \| xargs grep -l` → `task9_build_a_smoke.py:51-58`, `tests/test_task9_build_a_smoke.py` | Mecánica | S |
| **N-10** | Un auditor del propio repo (`_audit_paths.py`, en la raíz, sin documentar) da **falsos negativos en todas** sus comprobaciones de flags: reporta `--register` y `--verify-reproducible` como ausentes cuando existen, y su lista `present` sale vacía siempre. Una herramienta de verificación que siempre dice "roto" entrena a ignorarla. | MEDIO | Ejecutado; contrastado con `grep -c` sobre `install_mcp.py` y `build_native_launcher.py` (ambos = 1) | Mecánica | S |
| **N-11** | El repo público no tiene el trabajo verificado más reciente: `infected_drive`, medido in-game el 2026-08-20, no está publicado. | MENOR | HEAD público `e106cf2` | Mecánica | S |

### Grupo 6 — La puerta retail (lane Codex)

Codex mapeó **los once call-sites** de `retail_quarantine` y los clasificó por lo que
protegen de verdad. Esa tabla es el diseño de la puerta y está íntegra en
`lanes/lane-codex.md` §B.1. El resumen operativo:

| # | Hallazgo | Sev. | Cita | Coste |
|---|---|---|---|---|
| **R-1** | **La cuarentena mezcla protección del usuario con seguridad del sistema.** Un solo booleano decide cosas de naturaleza distinta: "no toques mi partida" (política, negociable) y "no mates el proceso equivocado" (seguridad, intocable). Mientras esa distinción no exista en el código, el opt-in no se puede implementar sin abrir de más. | ALTA | los 11 call-sites en `loopback.py` y `process_lifecycle.py`, clasificados en `lane-codex.md` §B.1 | M |
| **R-2** | **El discriminador retail no identifica una instancia: solo reconoce dos nombres.** La presencia de un proceso retail no dice a qué destino iría la mutación. El opt-in no puede reabrir peers ambiguos o no ligados. | ALTA | `loopback.py:1622-1623`, `:1669-1674` (el corte por versión ya liga destino) | M |
| **R-3** | **El usuario no puede distinguir "retail presente" de "probe roto".** Excepción, `known=false` y payload malformado producen bloqueo por razones distintas y el mismo 409 genérico. | ALTA | `loopback.py:2206-2217`; falta de razón tipada | S |
| **R-4** | El opt-in de mutación **no abre el lifecycle retail**: la allowlist diag-only de `process_lifecycle.py:976-988` es una decisión aparte y debe seguir siéndolo. | ALTA | `process_lifecycle.py:976-988` | — |
| **R-5** | **La cuarentena bloquea precisamente el cleanup que neutraliza controles.** `cleanup_owner` protege al SISTEMA (desarma a un owner que perdió autoridad) y hoy lo frena la señal retail. Es un defecto del diseño actual, no una carencia del opt-in. | ALTA | `loopback.py:2055-2107` | S |
| **R-6** | Retail puede aparecer **a mitad de un stop** y producir cleanup parcial: los probes repetidos cierran carreras al iniciar, no al parar. | ALTA | `process_lifecycle.py:1610`, `:1691`, `:1707`, `:1732` | M |

**Invariantes que el opt-in NO puede tocar** (§B.2 de su informe): autoridad exacta — el
opt-in no concede lease ni salta `reservation`/`commit`/`claim`; destino ligado a instancia
— las mutaciones solo llegan a un peer acreditado; el `identity guard` del kill sigue
siendo quien autoriza terminar un proceso, nunca el booleano retail; y `audit_failed` /
`manifest_failed` no pueden degradarse a éxito por haber abierto la puerta.

### Grupo 7 — Éxitos sin postcondición (lane Codex)

El patrón que Codex fue a buscar: **la tool devuelve éxito y no ha hecho lo que dice**.
Cinco de estos **no los había reportado nadie** — no salen del buzón, los encontró él
auditando handlers uno a uno.

| # | Hallazgo | Sev. | Cita | Nuevo |
|---|---|---|---|---|
| **C-1** | **`world_spawn` usa un verificador tautológico.** `IsSpawnReady` toma la posición **actual del propio objeto**, busca objetos en esa posición y devuelve listo si se encuentra a sí mismo. Un objeto siempre está donde está. Nunca compara contra la posición **pedida**, no mide deriva y no exige estabilidad entre ticks: el gate se autocumple para cualquier objeto vivo, aunque la física ya lo esté expulsando. Por eso el spawn urbano dio `ok=1` con el coche terminando a **Y = −40 km**. | ALTA | `MCPBridge.c:2729-2753`; job en `:536-571`; `PostJobSuccess` en `:3075-3090` | — |
| **C-2** | **`engine_set` devuelve `ok` aunque el readback lo contradiga.** Tras `EngineStart()`/`EngineStop()` hace `result.engine_on_server = car.EngineIsOn(); result.ok = true;` — **`ok` no se compara jamás con `mode`**. El mismo payload puede decir `ok=true, engine_on_server=false` para `mode=start`. El consumidor recibe la evidencia negativa y el bit principal diciendo éxito. | ALTA | `MCPClientBridge.c:782-815` (verificado); descripción pública en `server.py:2929-2939` | **sí** |
| **C-3** | **`vehicle_control` sustituye el TTL pedido por 3 segundos y responde `ok`.** La guarda es `if (holdTtl > 0 && holdTtl <= 30 && IsFiniteFloat(holdTtl)) ttl = holdTtl;` con `ttl` ya inicializado al default de 3. Pides 45 s de control sostenido, te da 3, y no hay campo de TTL efectivo en la respuesta: suelta el mando 42 segundos antes sin avisar. | ALTA | `MCPClientBridge.c:108-114` (constantes), `:870-886` (la guarda, verificada); `MCPMessages.c:404-466` (sin campo de TTL) | **sí** |
| **C-4** | **`capture_screenshot` puede devolver la ventana DayZ equivocada sin indicarlo.** Con servidor y cliente en la misma caja hay dos procesos DayZDiag. | ALTA | lane Codex §A-11 | **sí** |
| **C-5** | **`world_time_set` no verifica el multiplicador que incluye en su éxito.** La fecha correcta valida por arrastre algo que nunca se comprobó. | MEDIA | lane Codex §A-15 | **sí** |
| **C-6** | **Un DayZDiag ajeno impide retirar un run propio ya muerto.** El reaper converge en caja vacía pero no con un diag de otro. Variante no reportada del run fantasma. | ALTA | `process_lifecycle.py:2339-2360` | **sí** |
| **C-7** | `dayz_test_run` puede devolver `succeeded` sin un run utilizable por el MCP. **Converge con B-1** (Grok y la lane de agente débil llegaron por caminos distintos). | ALTA | `dayz_test_readiness.py:1`; `dayz_test_worker.py:204-210` | — |
| **C-8** | `vehicle_enter` confirma como estado final una observación server-side transitoria. **Converge con A-5.** | ALTA | `MCPBridge.c:3093-3099` | — |
| **C-9** | `object_anim` ignora su propio readback contrario. **Converge con A-9.** | ALTA | `MCPBridge.c:1219-1222` | — |
| **C-10** | `capture_screenshot` acepta un frame congelado "y hasta lo prefiere". **Converge con A-10** y lo agrava: no es solo que no avise, es que la lógica lo favorece. | ALTA | lane Codex §A-10 | — |

**Convergencia entre lanes que no se veían.** `dayz_test_run` sin puente lo levantaron
tres lanes independientes (Grok por el `_mods()`, agente débil por el síntoma tres verbos
después, Codex por la postcondición). `vehicle_enter`, `object_anim` y
`capture_screenshot` los levantaron dos cada uno. Esa convergencia entre proveedores
distintos es la señal más fuerte del council: no son opiniones sobre estilo, son el mismo
defecto visto desde tres sitios.


---

## Las preguntas incómodas que trajo el council

Cada lane tenía que escribir la pregunta con la que dejaría al autor sin respuesta delante
de su público. Se recogen verbatim porque son el guion del ensayo:

- **Slop**: «De estas 36.000 líneas de Python, ¿cuántas has leído tú — tú, no tus agentes?
  Porque en `daemon_policy.py`, el módulo que decide qué proceso puede tocar mi servidor,
  hay una clase entera de 70 líneas que está muerta, y se encuentra con Ctrl+F en dos
  minutos: si eso pasó el filtro, ¿qué filtro pasó el resto?»
- **Portabilidad**: «Si clono el repo en un Windows con Steam en `D:`, Python 3.12, DayZ
  Tools, solo Claude Code y sin unidad `P:`, ¿qué comando de tu README me deja
  `bridge_status` en verde en cinco minutos — y por qué ese comando no está en las líneas
  105-117?»
- **Agente débil**: «La única corrida de punta a punta que tienes de un modelo pequeño
  usando esto es tu sonda qwen3.5: 548 segundos y 15 llamadas para lease, teleport y
  release — y ese teleport movió al jugador de un run ajeno que corría sin lease. ¿Cuál es
  el modelo mínimo con el que afirmas en público que DayZ-MCP es usable, y dónde está la
  transcripción que lo demuestra?»
- **Promesa vs evidencia**: «Tu README dice que el bucle de desarrollo autónomo se cierra.
  Enséñame un mod publicado cuyo desarrollo cerraras con esto: el enlace al Workshop y el
  log de la sesión donde el agente construyó, lanzó, midió y corrigió sin que tú tocaras
  el cliente.»

Las cuatro tienen respuesta o la tendrán al cerrar la hoja de ruta. La última conviene
**provocarla**: el material existe y el README no lo usa.


---

## Anexo — cuatro hallazgos que solo aparecieron al ejecutar

> Añadido 2026-08-21 al cerrar el bloque A de la purga. Ninguno salió del council: los tres
> primeros los destapó **re-correr `boundary.py`**, y el cuarto, correr la suite entera. Vale
> la pena registrarlo como método: **siete lanes leyendo no vieron lo que vio un gate al
> ejecutarse una vez.**

| Id | Hallazgo | Severidad | Estado |
|---|---|---|---|
| **X-1** | Dos backups del bridge, 85 kB, dentro de la frontera de publicación | GRAVE | **arreglado hoy** |
| **X-2** | El *import check* de la frontera llevaba roto desde HEAD y nadie lo re-corrió | **BLOQUEANTE** | **arreglado hoy** |
| **X-3** | Los centinelas del hash del bridge están rojos, y ambos ficheros se publican | **BLOQUEANTE** | abierto, necesita decisión |
| **X-4** | La maquinaria que decide qué se publica no tiene ni un test | GRAVE | abierto |

### X-1 — El guard de backups conocía una sola convención de nombre

`is_write_artifact` (`tools/publish/boundary.py:35`) mira `Path.suffix` y entiende el
marcador pegado con **punto**: `MCPBridge.c.bak_pre_fencing_20260819` → suffix `.bak_…` →
cazado. Los pegados con **guion bajo** dejan el rabo entero dentro de `suffix`
(`MCPBridge.c_bak_infdrive_20260820` → suffix `.c_bak_…`) y pasaban limpios.

Dos backups del bridge estaban dentro de la frontera. Es la misma clase que el propio
comentario del guard presume de haber cazado el 19-ago («27 of these were inside the
boundary and would have shipped»): el agujero no era la idea, era que solo conocía una de
las dos convenciones que el proyecto usa.

Cerrado con `GLUED_WRITE_ARTIFACT = re.compile(r"_(bak|orig|rej)(_|$)")`, probado contra los
**cinco** backups reales de `DayZ_MCP\scripts\5_Mission\` (5/5 cazados) y seis negativos
que no deben caer (`notes.backup_data`, `archive.tar_baker`, `test_bak.py`…). Efecto medido
en el `included.json`: 246 → 244 ficheros, 4,85 → 4,76 MB, y el diff son exactamente esos dos.

### X-2 — Un gate verde en el papel y rojo en el árbol

`MANIFEST.md` del 20-ago decía `- none` en el *import check*. Al re-correrlo hoy:

```
import check: BROKEN -> tools/tests/test_task9_build_a_smoke.py imports excluded module task9_build_a_smoke
```

La regresión entró con `e106cf2` —que es HEAD— y **nadie volvió a correr la frontera
después**. El siguiente export habría publicado un repo cuya suite no arranca. Para el
público al que va dirigido esto, «clono y ni siquiera importa» es el final de la
conversación.

El módulo no puede publicarse nunca: hardcodea seis rutas absolutas del autor
(`task9_build_a_smoke.py:60,63,69,72,75`) y la clase de un mod privado
(`OBJECT_TYPE = "MERCEDES_AMGLF"`, `:32`). Cerrado excluyendo también su test, con el
mecanismo que ya existía para los tests de `tools/_*`. Tras el arreglo: `import check:
clean`, 243 ficheros, private hits 5 → **3**, portfolio exposure 27 → 26.

**Lo que este hallazgo dice del proceso** es más importante que el hallazgo: la frontera
solo es verdad el día que se corre, y hoy nada obliga a correrla. Ver X-4.

### X-3 — Los centinelas del bridge se publican rojos

`tools/tests/test_task9_spawn_phase_markers.py` congela el SHA-256 de `MCPBridge.c` en dos
mitades. Hoy, en el árbol:

```
BRIDGE_SHA256       ADD51C9BFBFEA3F2… (real)  !=  2C4B198BD54A6AB7… (congelado)
BASE_BRIDGE_SHA256  7CC833DE5FAB15B5… (real)  !=  ADFAB7C6150DD4E3… (congelado)
```

La causa está identificada y es legítima: el bridge ganó `DispatchInfectedDrive` (~89 líneas,
`:496` dispatch + `:1229` handler) el 20-ago. El cierre de esa sesión lo dice
(`HANDOFF.md:201`) y lo deja rojo a sabiendas, «hasta cerrar el fencing».

**Lo que ese cierre no midió**: `test_task9_spawn_phase_markers.py` **y**
`addon/scripts/5_Mission/MCPBridge.c` están los dos dentro de la frontera. Un rojo
deliberado en el árbol privado es una nota; en el repo publicado es la credencial más fuerte
que tiene este proyecto —«clónalo y la suite sale verde»— rota en la primera ejecución.

**No lo he tocado, y a propósito.** El propio test prohíbe re-congelar sin gate in-game:
«Re-congelar SOLO sobre estado gateado in-game, nunca sobre un edit source-only». Ahí hay
una tensión real que decidir, no un fix:

- el gate in-game **ya se pasó** — `infected_drive` está verificado con medidas (rumbo
  impuesto 90° → 92,1° real; 270° → 270,1°; `release` devuelve el mando), y la tool está
  viva en la superficie (`server.py:2731`);
- pero el cierre ató el re-congelado a otro trabajo («el fencing») que sigue abierto.

Las dos salidas son defendibles y la elección es del dueño: **re-congelar ya**, con el gate
de `infected_drive` como evidencia y una nota de que el fencing sigue pendiente; o **dejarlo
rojo y no exportar** hasta cerrarlo. Lo que no es defendible es exportar en el estado de hoy.

### X-4 — `tools/publish/` decide qué se publica y no tiene un solo test

X-1 y X-2 son el mismo defecto de fondo: la frontera es un script que alguien recuerda
correr. Está **entero sin trackear** (`boundary.py`, `included.json`, `export_public_repo.py`,
`diff_vs_repo.py`, `run_export_suite.py`) y sin una línea de cobertura, mientras el resto del
repo trae 1969 tests. Es el único componente donde un fallo silencioso publica un secreto o
rompe el clon de un tercero — exactamente la clase de cosa que el resto del proyecto sí gatea.

Candidato número uno de la fase 1, y barato: un test que corra `boundary.py` y exija
`import check: clean`, más otro que afirme que ningún fichero de la frontera casa con
`is_write_artifact`, cierran X-1 y X-2 para siempre.

### Estado de la suite tras el bloque A

`Ran 1969 tests in 250.259s — FAILED (failures=2, errors=1, skipped=4)`, con el intérprete y
el venv del repo. **Los tres son preexistentes; ninguno sale de los cambios de esta sesión**
(verificado: ningún fichero que toqué participa en los tres caminos).

- Los **2 fallos** son X-3 y sí viajarían al repo público.
- El **1 error** (`test_native_launcher_bundle … ValueError: app_module_drift`,
  `build_native_launcher.py:980`) es **solo local**: el bundle nativo construido quedó viejo
  respecto a su fuente (`src/app_main.py`, 13.229 bytes, 16-ago 21:05). En un clon no
  aparece — el bundle está excluido de la frontera y esos tests llevan
  `@requires_built_bundle`, que es de dónde salen los `skipped` del clon limpio. Higiene del
  árbol del autor, no un problema de publicación.
