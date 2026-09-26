# Triaje de AUDITORIA_MCP_2026-09-07.md ? L1

Revisi?n offline del ?rbol compartido, 2026-09-08. Base observada: `cb2cdd8`. S?lo se escribe en este directorio. Informe completado por tramos. Los censos intermedios y la comprobaci?n final distinguen cambios de otras lanes.

Los paths son relativos a la ra?z DayZ_MCP_dev salvo indicaci?n contraria. `evidence.txt` conserva las aperturas numeradas con tama?o y SHA-256. VIVO confirma el hecho delimitado en la entrada, no autom?ticamente su gravedad o la refactorizaci?n sugerida. NO VERIFICABLE no es un fallo reproducido. Las cifras de rendimiento sin medici?n no se elevan a defectos.

## 0. Alcance y metodolog?a

La ruta del brief `DayZ_MCP/scripts/5_Mission` no existe dentro del workspace; s? existen `addon/scripts/5_Mission` y el hermano `../DayZ_MCP/scripts/5_Mission`. Se usa `addon`, sujeto citado por la auditor?a, y se comprueba su correspondencia. P0/P1/P2 aparecen en el orden de propuestas (?6.2 y cierre ?8), no etiquetan seis defectos. ?9 es inventario; ?10 existe pero queda FUERA DE MI ALCANCE por la frontera expresa ?1?8.

## 1. Resumen ejecutivo ? orden de lectura

Revisar primero E-BUF-02, E-ERR-06 y E-P01: las conclusiones de inanici?n, throttle infinito y cast inv?lido no se sostienen. Despu?s P-OPT-03 y las propuestas de eliminar tests/autoridades: distinguir algoritmo real y garant?as independientes antes de refactorizar. Dentro de cada secci?n van primero los riesgos y correcciones de mayor utilidad, luego deuda estructural y pulido.

### S1 ? ?~70% el mismo fichero?; ?~400 l?neas?
**VEREDICTO: NO VERIFICABLE.** `addon/scripts/5_Mission/MCPBridge.c:225` y `addon/scripts/5_Mission/MCPClientBridge.c:403` comparten transporte pero difieren en identidad/watchdog y callbacks. Hay duplicaci?n (E-DUP), sin m?todo para demostrar 70%, ahorro ni mejor ratio por hora.

### S2 ? ?N relojes donde deber?a haber 1?
**VEREDICTO: FALSO.** `addon/scripts/5_Mission/MCPClientBridge.c:267` llama al runner y ?ste acaba llamando al di?logo (`:2555`): son fases anidadas, no cuatro loops aut?nomos. MissionServer y MissionGameplay pertenecen a actores distintos (`addon/scripts/5_Mission/MissionServer.c:19`, `addon/scripts/5_Mission/MissionGameplay.c:19`). La consolidaci?n es propuesta, no defecto probado.

### S3 ? ?polling + subprocess en caliente?
**VEREDICTO: VIVO.** `tools/dayz_mcp/server.py:78` mantiene 0,05 s; el alcance real de subprocess y logs se delimita en P-OPT-01/02/03. Sin medici?n no se confirma que sea el tercer problema m?s importante.

### S4 ? ?Se puede recortar ~30% sin perder se?al?
**VEREDICTO: NO VERIFICABLE.** `product-spec.md:145` y `product-spec.md:147` exigen acreditaci?n pre-request y recuperaci?n E2E multiproceso; n?mero de m?dulos no mide redundancia ni preservaci?n de esas garant?as. V?ase ?4.

### S5 ? ?Nada de esto impide que el MCP funcione?
**VEREDICTO: NO VERIFICABLE.** `product-spec.md:57`, `product-spec.md:122` y `product-spec.md:148` contienen aceptaci?n pendiente; s?lo lectura no acredita funcionamiento del despliegue actual.

## 2. ENFORCE SCRIPT ? hallazgos

### E-BUF-02 ? ?Si el daemon deja de consumir result, el pending nunca baja?
**VEREDICTO: FALSO.** `addon/scripts/5_Mission/MCPBridge.c:114` drena antes del guard y `:317` elimina el comando antes de despacharlo; no espera ACK de POST. El cliente tambi?n drena primero (`addon/scripts/5_Mission/MCPClientBridge.c:282`). La falta de ACK puede retener callbacks, no crea el mecanismo alegado sobre pending.

### E-ERR-06 ? ?throttle=inf cliente pasa el filtro?
**VEREDICTO: FALSO.** `addon/scripts/5_Mission/MCPClientBridge.c:1065` rechaza throttle >1 o <0 antes/junto al helper. Es real la asimetr?a de helpers (`:3759` frente a `addon/scripts/5_Mission/MCPBridge.c:2490`), pero no esa aceptaci?n de throttle infinito. No se extrapola a todos los dem?s campos.

### E-ERR-04 ? ?Shutdown asim?trico y fr?gil?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPClientBridge.c:4098` llama a Abort/Clear/RestoreGameplay; `:3911` desreferencia GetGame sin comprobarlo. `:237` y `:252` permiten dos entradas a Shutdown sin flag expl?cito. Es un riesgo defensivo real, no un crash reproducido; la asimetr?a preserva el terminal del di?logo y los callbacks (`:4066`, `:4103`), por lo que no debe borrarse en bloque.

### E-BUF-01 ? ?POST por comando?; ?m_CallbackRefs no tiene bound?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPBridge.c:3441` serializa y `:3451` retiene un callback por POST; `:3478` s?lo retira al recibir callback. Cliente igual en `addon/scripts/5_Mission/MCPClientBridge.c:3982`. Sin callbacks de retorno no hay techo local; no se midi? tasa ni memoria. MAX_DISPATCH limita cada recorrido, no demuestra por s? solo un m?ximo global de cuatro POST por frame (tambi?n terminan jobs).

### E-ERR-05 ? ?pollHz sin cota superior?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPBridge.c:195` y `addon/scripts/5_Mission/MCPClientBridge.c:371` aceptan todo valor positivo sin techo. Con 1000 el intervalo permite poll por frame disponible, condicionado por el GET en vuelo y backoff; no 1000 requests/s garantizados.

### E-ERR-01 ? ?PostResult descarta en silencio si !m_Configured||!m_Ctx?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPBridge.c:3436` y `addon/scripts/5_Mission/MCPClientBridge.c:3977` retornan sin log. La llegada de un comando aceptado a ese estado requiere reproducci?n del lifecycle; la rama silenciosa s? existe.

### E-ERR-02 ? ?difiere TODO lo que venga tras el primer world_spawn?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPBridge.c:279` mantiene deferFromWorldSpawn para el resto del lote. Es espera al drenaje siguiente, no necesariamente esperar a que termine el spawn; permitir adelantamientos puede romper orden causal (spawn?query).

### E-ERR-03 ? ?Jobs con kind desconocido cuelgan hasta timeout?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPBridge.c:2884` y `addon/scripts/5_Mission/MCPClientBridge.c:2561` devuelven false; `addon/scripts/5_Mission/MCPJobRunner.c:135` aplica deadline. Es una rama defensiva, sin entrada p?blica demostrada que fabrique ese kind; servidor s? procesa drive_probe en `addon/scripts/5_Mission/MCPBridge.c:2846`. Los 5 s no son universales para todos los jobs.

### E-BUF-03a ? ?m_Pending acotado ... m_Jobs no?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPBridge.c:335` impone MAX_PENDING; `:579` inserta job sin techo local/Contains, igual `addon/scripts/5_Mission/MCPJobRunner.c:80`. Los deadlines (`addon/scripts/5_Mission/MCPBridge.c:2860`) limitan retenci?n temporal, no constituyen un contador m?ximo.

### E-BUF-03b ? ?un id duplicado ... sobrescribiendo?
**VEREDICTO: NO VERIFICABLE.** `../scripts/1_core/proto/enscript.c:907` declara Insert devolviendo bool; `:880` documenta Set como operaci?n de actualizar/crear. La definici?n nativa de Insert no expone aqu? su conducta con duplicados: no se puede afirmar sobrescritura. `addon/scripts/5_Mission/MCPBridge.c:579` ignora el retorno. Adem?s el productor reserva IDs crecientes (`tools/dayz_mcp/loopback.py:1893`); faltar?a caso alcanzable de duplicaci?n y prueba del motor.

### E-TICK-01 ? ?4 loops de misi?n + 2 relojes auxiliares?
**VEREDICTO: FALSO.** `addon/scripts/5_Mission/MissionServer.c:26` y `addon/scripts/5_Mission/MissionGameplay.c:26` son dos entradas por actor; `addon/scripts/5_Mission/MCPClientBridge.c:267`?`addon/scripts/5_Mission/MCPJobRunner.c:90`?`addon/scripts/5_Mission/MCPClientBridge.c:2555`?`addon/scripts/5_Mission/MCPDialogController.c:266` son llamadas anidadas del mismo ciclo. No son cuatro bucles independientes desarmonizados.

### E-TICK-02 ? ?Capture(this,dt) ... siempre?
**VEREDICTO: VIVO.** `addon/scripts/4_World/MCP_CarScript.c:663` se invoca en cada OnInput, pero `:207` retorna inmediatamente si el trace no est? activo o no es ese coche. `:608` consulta GetGame s?lo cuando applyControl; el supuesto gasto de GetGame en fr?o ya est? evitado. Coste marginal sin medir.

### E-TICK-03 ? ?servidor NO usa MCPJobRunner?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPBridge.c:2821` conserva mapa y recorrido propios; cliente delega en `addon/scripts/5_Mission/MCPClientBridge.c:267`. Antes de compartir, conservar distinto orden process/readiness (`addon/scripts/5_Mission/MCPJobRunner.c:122` frente a `addon/scripts/5_Mission/MCPBridge.c:2839`).

### E-TICK-04 ? ?Doble reloj en di?logo?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPDialogController.c:230` guarda deadline del runner y de GetTickTime; `:944` acepta cualquiera y `:963` prefiere tiempo real para elapsed. Suprimirlo cambia sem?ntica temporal; la redundancia no acredita bug.

### E-DUP-01 ? ?TryInit clonado?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPBridge.c:139` y `addon/scripts/5_Mission/MCPClientBridge.c:317` repiten carga, validaci?n de URL/key y pollHz. Cliente a?ade contexto de poll y headers; no son literalmente iguales salvo un campo.

### E-DUP-02 ? ?FSM HTTP clonada?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPCallbacks.c:10` y `addon/scripts/5_Mission/MCPClientBridge.c:15` comparten release/dispatch; cliente adem?s descarta callbacks abandonados por identidad (`:20`) y tiene watchdog (`:287`). Duplicaci?n parcial; la fusi?n debe preservar esas diferencias. GetPollVersion no empieza en MCPBridge:2064: esa l?nea cachea el resultado.

### E-DUP-03 ? ?Drive-probe duplicado?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPBridge.c:2953` y `addon/scripts/5_Mission/MCPClientBridge.c:3165` comparten comprobaci?n de fixture; ownership difiere al a?adir net_strategy (`:3144`). El control server usa setters (`addon/scripts/5_Mission/MCPBridge.c:3101`), cliente renueva deadman (`addon/scripts/5_Mission/MCPClientBridge.c:3101`); compartir FSM sin respetar peer contradice G1 (`product-spec.md:120`).

### E-DUP-04 ? ?5 helpers de b?squeda esf?rica casi iguales?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPBridge.c:2769`, `:1565` y `addon/scripts/5_Mission/MCPClientBridge.c:1990` repiten limpieza y consulta espacial. Seleccionan primero, ?nico y m?s cercano respectivamente: es duplicaci?n de infraestructura, no sem?ntica intercambiable.

### E-DUP-05 ? ?Doble supresi?n de input?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPClientBridge.c:3901` y `addon/scripts/5_Mission/MCPDialogController.c:885` deshabilitan con flags propios. La restauraci?n difiere: Enable(true) (`:3926` cliente) vs Enable(false) (`:902` di?logo), m?s focus; un refcount exige revisar interacci?n real, no s?lo renombrar.

### E-HOT-04 ? ?IsSpawnReady ... CADA TICK ... radio 8 m?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPBridge.c:114`?`:2839`?`:2897`, radio `:13`. Se repite mientras no encuentra el subject. Quitar la observaci?n independiente y devolver OK tras un frame debilita B1 (`product-spec.md:54`); el coste a?n no est? medido.

### E-HOT-06 ? ?pre-aloca hasta 8.192 objetos?
**VEREDICTO: VIVO.** `addon/scripts/4_World/MCP_CarScript.c:160`?`:171` reserva maxSamples; `:440` inicia getters por muestra. Default 20 Hz/4096 (`addon/scripts/5_Mission/MCPMessages.c:143`). Llamarlo excesivo requiere medir; quitar campos contradice G3 y reducir el m?ximo aceptado 8192 cambia la capacidad p?blica actual, por lo que debe revisar el contrato expl?cito G3 (`product-spec.md:122`).

### E-HOT-01 ? ?EncodeQueryValue ... en cada poll?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPBridge.c:235`, `:2072` y `addon/scripts/5_Mission/MCPClientBridge.c:423` recodifican caps; ver ya se cachea (`addon/scripts/5_Mission/MCPBridge.c:2067`). Beneficio de cachear caps sin medir.

### E-HOT-02 ? ?GetGame() sin cachear (87 ocurrencias)?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPBridge.c:1088`, `:1107`, `:1108` repiten acceso dentro de un comando. Censo literal de los nueve .c de addon: 89, frente a los 87 no delimitados por la auditor?a; no mide coste; no se acredita optimizaci?n perceptible.

### E-HOT-03 ? ?GetPlayers ... por comando?; ?aliasing si ... reentra?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPBridge.c:2134`, `:2653`, `:2677` limpian/rellenan el mismo array. No todos los comandos lo hacen. Reentrada y corrupci?n son NO VERIFICABLES sin un callback que las provoque; GetPlayer cliente no sustituye lista autoritativa server (A1, `product-spec.md:42`).

### E-HOT-05 ? ?selecci?n O(n?limit)?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPBridge.c:2748` contiene b?squeda lineal del m?nimo por cada resultado; l?mites 200 m/128 en `:1392` y `:1400`. Top-k por inserci?n tambi?n puede ser O(n?k); la propuesta no garantiza mejora asint?tica.

### E-HOT-07 ? ?triple IndexOf ... por cada contacto f?sico?
**VEREDICTO: VIVO.** `addon/scripts/4_World/MCP_CarScript.c:252`?`:259` hacen hasta tres b?squedas, con cortocircuito; s?lo tras guard trace/coche/datos (`:246`). No son siempre tres ni para todos los coches inactivos.

### E-HOT-08 ? ?5 setters CADA frame ... control activo?
**VEREDICTO: VIVO.** `addon/scripts/4_World/MCP_CarScript.c:655`?`:659` mantiene cinco setters, condicionado adem?s por engineReady (`:634`). Reaplicar estado cada tick es contractual (`product-spec.md:120`); mover s?lo un setter necesita prueba de persistencia en el motor.

### E-HOT-09 ? ?DFS ... de TODO el workspace?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPClientBridge.c:2138` recorre scope sin l?mite de profundidad, pero para al encontrar dos matches (`:2144`); no siempre todo el workspace. CollectUiNodes s? limita nodos (`:2441`). Caches deben invalidarse ante cambios UI.

### E-HOT-10 ? ?new Param4 ... por ancestro?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPClientBridge.c:2488` y `:2507` alocan s?lo en fallbacks reflectivos; handlers tipados salen antes (`:2483`). Es coste condicional por click, sin evidencia para cachear objetos UI durante 1 s.

### E-HOT-11 ? ?4 Log por spawn?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPBridge.c:550`, `:552`, `:560`, `:562` y `:3460` mantienen logs; di?logo imprime en `addon/scripts/5_Mission/MCPDialogController.c:262`. Las fases distinguen validaci?n y CreateObjectEx: no declararlas debug sobrante sin evaluar el consumidor diagn?stico.

### E-BUF-04 ? ?Pool ... 64 ... s?lo last_valid?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPBridge.c:85` preasigna 64; `:2313` toma un objeto por l?nea y `:2336` copia tres escalares a last_valid. Hay oportunidad acotada de scratch reutilizable; no evidencia de presi?n de memoria relevante.

### E-GUI-01 ? ?config.cpp correcto y m?nimo ... id?ntico?
**VEREDICTO: VIVO.** `addon/config.cpp:21` registra World/Mission y `:27`/`:32` sus rutas; comparaci?n por bytes con ../DayZ_MCP/config.cpp da SHA-256 f5874213dbc4af71dbf95ab3ee08c02a0eac5094eb319b52ab17141bf67ec6ee. Se confirma estructura e igualdad, no compilaci?n/despliegue.

### E-GUI-02 ? ?coherente con MAX_FIELDS=6?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPDialogController.c:30`; `addon/gui/layouts/mcp_dialog.layout:61` inicia Row0 y `:251` Row5, con Error/5 botones en `:289`?`:370`; visible=0 y priority=2000 (`:3`, `:11`). Apariencia visual no probada.

## 3. HARNESS PYTHON ? hallazgos

### P-OPT-03a ? ?Lecturas de log completas por poll?
**VEREDICTO: FALSO.** `tools/dayz_mcp/server.py:2691` hace el scan desde lanzamiento antes del while de `:2716`; dentro usa `_new_log_lines` (`:2765`), que actualiza markers (`:2444`). `tools/dayz_mcp/log_tail.py:172` reanuda desde offset y limita bytes. S? puede repetirse el scan entre llamadas distintas; no cada poll del mismo waiter.

### P-OPT-03b ? ?carry+=chunk ? O(n?) para logs de 130k l?neas?
**VEREDICTO: FALSO.** `tools/dayz_mcp/server.py:2491` descarta todas las l?neas procesadas, conservando s?lo el sufijo incompleto. Muchas l?neas cortas no hacen crecer carry con todo el fichero. Una ?nica l?nea patol?gica sin LF s? puede causar trabajo repetido hasta el techo: es otro caso, no el alegado.

### P-ARC-02a ? ?Stack launcher de 7 capas?
**VEREDICTO: VIVO.** `tools/dayz_mcp/secure_launcher.py:78`, `tools/dayz_mcp/native_launcher_transaction.py:677` y `tools/dayz_mcp/native_launcher_backend.py:1280` pertenecen a rutas separadas; `tools/dayz_mcp/secure_launcher.py:115` ignora los dos argumentos del adaptador. ?7 capas? cuenta m?dulos auxiliares como si fueran saltos secuenciales: no demuestra sobreingenier?a. El sellado y la identidad tienen obligaciones H9/H10 (`product-spec.md:144`, `:145`).

### P-ARC-02b ? ?_IncrementalRedactor con O(n?) asumido en comentario?
**VEREDICTO: YA ARREGLADO.** `tools/dayz_mcp/secure_launcher.py:53` avanza ?ndice y `:74` recorta una vez. El comentario `:43` explica el patr?n anterior. `git log` identifica `9b0aed2` (2026-08-23), anterior a la auditor?a: ya estaba arreglado cuando se redact?; no fue una correcci?n de esta noche.

### P-ARC-03a ? ?Autoridad daemon en 6 ficheros?
**VEREDICTO: VIVO.** `tools/dayz_mcp/daemon_policy_contract.py:1` define valor puro; `tools/dayz_mcp/daemon_policy.py:319` y `tools/dayz_mcp/normal_daemon_policy.py:158` repiten carga/revalidaci?n; `tools/dayz_mcp/daemon_credential.py:186` gestiona refresh y `tools/dayz_mcp/accredited_daemon_transport.py:1` acredita socket. Hay reparto y alguna duplicaci?n real, pero no seis autoridades equivalentes.

### P-ARC-03b ? ?server ... revalida ... lo que require_matching_keyfile ya hace?
**VEREDICTO: FALSO.** `tools/dayz_mcp/host_config.py:122` s?lo canoniza/compara keyfile. `tools/dayz_mcp/server.py:1121`?`:1165` comprueba puerto, argv, cwd y ejecutables: no lo cubre ese helper. Fusionar sin conservarlos quitar?a checks H10.

### P-TICK-02 ? ?Triple reclaim/kill?
**VEREDICTO: VIVO.** `tools/dayz_mcp/orphan_guard.py:664` y `:971` contienen dos pol?ticas de reclaim; lifecycle clasifica (`tools/dayz_mcp/process_lifecycle.py:2741`) y el guard termina tras revalidar (`tools/dayz_mcp/native_process_guard.py:194`). No son tres kills redundantes: ancestry y health deben seguir separados (F5 `product-spec.md:107`). `tools/process-guard.ps1:18` usa identidad WMI/raw-commandline, no el mismo esquema nativo v2.

### P-TICK-05a ? ?Identidad copiada?
**VEREDICTO: VIVO.** `tools/dayz_mcp/native_process_guard.py:184` y `tools/dayz_mcp/process_lifecycle.py:2730` comparan los mismos campos; lifecycle adem?s normaliza case. Es candidato a helper puro manteniendo validaciones de forma en cada frontera.

### P-TICK-05b ? ?import circular orphan_guard?process_lifecycle?
**VEREDICTO: FALSO.** `tools/dayz_mcp/orphan_guard.py:696`/`:1006` importan lifecycle localmente; `tools/dayz_mcp/process_lifecycle.py:23` inicia los imports de proyecto y el censo AST completo (`inventory.txt`) no contiene import de orphan_guard. `tools/dayz_mcp/native_process_guard.py:16` s?lo importa ProcessRecord bajo TYPE_CHECKING. Hay acoplamiento, no el ciclo directo de runtime afirmado.

### P-TICK-01 ? ?7 loops que deber?an ser 1?
**VEREDICTO: VIVO.** `tools/dayz_mcp/lease_supervisor.py:103`, `tools/dayz_mcp/server.py:3058`, `tools/dayz_mcp/daemon.py:884` mantienen trabajos peri?dicos distintos; `tools/dayz_mcp/daemon.py:1591` excluye parent-death en daemon. Los pollers son por operaci?n y los actores distintos, no siete relojes siempre activos por sesi?n. `tools/dayz_mcp/lease_supervisor.py:185` y `tools/dayz_mcp/control_client.py:674` repiten protecci?n contra cancelaci?n. No se demuestra que un tick de 1 s preserve latencia, independencia de fallos ni elimine 3 hilos+2 tasks.

### P-OPT-02 ? ?netstat por llamada ... ~600 spawns?
**VEREDICTO: VIVO.** `tools/dayz_mcp/orphan_guard.py:378` y `:471` s? lanzan netstat; `tools/dayz_mcp/server.py:3091` sondea box. Pero `tools/dayz_mcp/process_lifecycle.py:4144` reutiliza snapshot durante 1,5 s para la misma revisi?n: no hay correspondencia obligatoria 1:1 entre status y spawn. La cifra de cientos es plausible sin demostrar; cach? de observabilidad nunca debe sustituir revalidaci?n de identidad al autorizar/terminar.

### P-OPT-01 ? ?Polling sleep(0.05) como norma?
**VEREDICTO: MOVIDO.** `tools/dayz_mcp/control_client.py:580` contiene el sleep antes citado en :548; `tools/dayz_mcp/server.py:78` mantiene constante 0,05, `tools/dayz_mcp/native_launcher_backend.py:1286` 1 ms y `tools/dayz_mcp/process_lifecycle.py:2820` espera de liberaci?n. No es busy-spin puro: sleep cede CPU. `wait_for` tiene m?nimo 0,5 s (`tools/dayz_mcp/server.py:2640`). Es polling real, no prueba de carga idle excesiva.

### P-ARC-01 ? ?Cadena session_* con 5 fachadas?
**VEREDICTO: MOVIDO.** La API de control est? ahora en `tools/dayz_mcp/control_client.py:257`, `:353`, `:521`, `:690`, no :225/:321/:489/:658. `tools/dayz_mcp/server.py:1302` sigue adaptando lazy-spawn; `tools/g0_abba_gate.py:801` duplica enqueue/wait con diferencias de cleanup. Protocols describen interfaces, no a?aden cinco saltos obligatorios a cada operaci?n.

### P-ARC-04 ? ?effective_schema en 4 ficheros?
**VEREDICTO: VIVO.** `tools/dayz_mcp/effective_schema.py:29` resuelve app, `tools/dayz_mcp/effective_schema_core.py:1` construye datos puros, `tools/dayz_mcp/effective_schema_catalog.py:1` es cat?logo independiente y `tools/dayz_mcp/effective_schema_runtime_validators.py:31` enumera validadores reales. Cuatro ficheros no implican duplicaci?n: fusionar or?culo y observado amenaza E5 (`product-spec.md:90`).

### P-ARC-05 ? ?dayz_test_* 6 ficheros con duplicaci?n admitida?
**VEREDICTO: VIVO.** `tools/dayz_mcp/native_launcher_transaction.py:250` reproduce composici?n de mods y `:277` explica la separaci?n del worker sellado. Extraer es posible s?lo manteniendo frontera de bundle, determinismo y pruebas del payload; no es borrado local de dos helpers.

### P-ARC-06 ? ?Tres lecturas open+sha256+verify casi id?nticas?
**VEREDICTO: VIVO.** `tools/dayz_mcp/launcher_registry.py:181` abre handle Windows sin compartir escritura, `:214` es helper de test; `tools/dayz_mcp/launcher_registry_update.py:77` lee registro acotado y compara identidad antes/despu?s. Hay operaciones repetidas, no tres verificaciones intercambiables: un hash_file trivial perder?a defensas de identidad/TOCTOU.

### P-ARC-07 ? ?Gates top-level que replican producto?
**VEREDICTO: VIVO.** `tools/g0_abba_gate.py:801` implementa adquisici?n/wait/cancel frente a `tools/dayz_mcp/control_client.py:690`. La replicaci?n concreta existe; ?cada uno? repite netstat/compute_ready no queda demostrado por ese ejemplo. Un harness com?n puede reducir mantenimiento, pero no debe compartir el or?culo con el producto que eval?a.

### P-OPT-04 ? ?Reintentos sin backoff?
**VEREDICTO: VIVO.** `tools/dayz_mcp/server.py:1287` permite un retry s?lo antes de enviar bytes; `tools/dayz_mcp/daemon_credential.py:213` reintenta con deadline original y falla ante segundo 401 (`:234`); `tools/dayz_mcp/process_lifecycle.py:2820` usa espera fija. Es deliberadamente acotado; retry gen?rico con m?s intentos/jitter puede violar H12 (`product-spec.md:147`). El grep backoff s?lo de Python no describe el backoff Enforce (`addon/scripts/5_Mission/MCPBridge.c:130`).

### P-OPT-05 ? ?Timeouts dispersos sin timeouts.py?
**VEREDICTO: VIVO.** `tools/dayz_mcp/server.py:78`, `tools/dayz_mcp/lease_supervisor.py:104`, `tools/dayz_mcp/daemon.py:873`, `tools/dayz_mcp/orphan_guard.py:380` y `:473` tienen presupuestos diferentes. Centralizaci?n documental razonable; igualdad de n?meros no equivale a identidad de prop?sito. No se verific? que un override gen?rico por entorno respete policy sellada.

### P-OPT-06 ? ?subprocess + PIL por captura?
**VEREDICTO: NO VERIFICABLE.** El mecanismo existe (`tools/mcp_capture.py:880`, `:944`, defaults `:61`), pero ?duele? depende de frecuencia y latencia no medidas. Captura necesita renderer y window-grab por contrato (`product-spec.md:169`); reutilizar PowerShell/geometry requiere medir e invalidar ante cambio de ventana.

### P-TICK-03 ? ?Retail/box probes duplicados?
**VEREDICTO: VIVO.** `tools/dayz_mcp/orphan_guard.py:389` y `tools/dayz_mcp/process_lifecycle.py:49` duplican set de im?genes; `tools/dayz_mcp/daemon.py:526`/`:534` inyecta probes, `tools/dayz_mcp/process_lifecycle.py:3895` los consume. Parte es cableado/capas, no enumeraciones independientes; ya hay cach? (`:4144`).

### P-TICK-04 ? ?session_status enriquecido en 3 sitios?
**VEREDICTO: VIVO.** `tools/dayz_mcp/server.py:1343` devuelve status, `:1346` construye solicitud box y `:3147` presenta blocked_on. No son tres copias del mismo enriquecimiento. `tools/dayz_mcp/lease_supervisor.py:152` exige terminal simple; `tools/dayz_mcp/control_client.py:586` exige recuperaci?n m?s estricta (audit_fault, generation, etc.): un ?nico assert_idle no puede relajar la segunda.

## 4. TESTS Y GATES ? hallazgos

Ninguno de estos veredictos afirma que los tests hayan pasado: se revisaron fuente, or?culos y trazado Git, sin ejecutar suites. La ausencia de una llamada a un helper no prueba ausencia de verificaci?n por otro mecanismo.

### T-ARC-01 ? ?Contrato que se auto-afirma?
**VEREDICTO: FALSO.** `tools/tests/test_daemon_contract.py:36` compara contra una construcci?n esperada separada, y `:75` ya compara con un argv literal. AssertIs (`:73`) exige reexportar la autoridad can?nica: es test estructural, no una tautolog?a de identidad consigo misma. La tabla condicional puede simplificarse, pero borrar :36?71 pierde opciones verificadas.

### T-ARC-02 ? ?verifica que daemon.py importe al contrato ... cubierto por ... statically_analysable?
**VEREDICTO: FALSO.** `tools/tests/test_daemon_contract.py:115` comprueba **host_config.py** y `:108` proh?be imports con red/procesos en el contrato. `tools/tests/test_sources_are_statically_analysable.py:68` s?lo ejecuta ast.parse: acepta m?dulos sint?cticamente v?lidos que violen esas fronteras. No es cobertura equivalente.

### T-ARC-03 ? ?mismo regex ... test_docs_truth invoca al check, no duplica?
**VEREDICTO: YA ARREGLADO.** `tools/tests/test_docs_truth.py:335` carga `check_readme_cites.py` y llama a mod.check (`:345`); ya hace la propuesta. Git sit?a esa delegaci?n en `d4bb250` (2026-08-23). `tools/tests/test_install_mcp.py:1264` cuenta tools registradas, algo distinto de validar paths/l?neas. No hay triple copia del mismo regex aqu?.

### T-OPT-01 ? ?E2E con proceso vivo convertible a unit?
**VEREDICTO: VIVO.** `tools/tests/test_client_credential_rotation_e2e.py:199` lanza daemon aislado y `:557` usa stdio; `tools/tests/test_session_e2e.py:49` usa HTTP real con port=0 (`:32`) y peer DayZ falso (`:75`). Es integraci?n offline, no juego vivo. Sustituirlo por _FakeRuntime no demuestra H12 multiproceso (`product-spec.md:147`); el ahorro de tiempo no se ha medido.

### T-OPT-02 ? ?venv real por caso?
**VEREDICTO: VIVO.** `tools/tests/test_bug046_startup_deadlock.py:1411` crea un venv real con timeout 60 s. Ocurre en un test concreto (`:1385`), antes de dos subcasos (`:1432`); no cada caso de toda la suite. Comprueba que int?rprete de forma id?ntica no sea acreditado. Mockearlo elimina justamente el discriminador independiente del host.

### T-OPT-03a ? ?Timeouts largos y sleeps fijos?
**VEREDICTO: VIVO.** `tools/tests/test_parent_watchdog.py:151` y `:176` son esperas del proceso fixture; `tools/tests/test_session_e2e.py:72` tiene join acotado a 2 s. Son m?ximos/polling, no duraci?n pagada ?ntegra; sin medir no se justifica techo global 2 s.

### T-OPT-03b ? ?TimeoutExpired(claude.exe, 30 s)? como coste
**VEREDICTO: FALSO.** `tools/tests/test_p0s_gate.py:233` crea un runner falso que **lanza inmediatamente una excepci?n** TimeoutExpired; no espera 30 s ni ejecuta Claude. La auditor?a mezcla valores simulados y esperas reales.

### T-OPT-04 ? ?ning?n driver declara si exige DayZ vivo?
**VEREDICTO: FALSO.** `tools/g0_abba_gate.py:4` declara broker ya arrancado; `tools/g0_site_gate.py:15` describe certify con spawn/drive. `tools/tramoA_verbs_gate.py:36` y `tools/tramoB_getin_gate.py:36` requieren readiness y llaman a world_spawn. Son drivers CLI; falta de skipUnless no los mete en unittest. `tools/tests/test_g0_abba_verdict.py:1` se declara puro. No se ejecutaron drivers.

### T-ARC-04 ? ?Test que testea el check?
**VEREDICTO: VIVO.** `tools/tests/test_launcher_registry_update.py:1001` prepara fixture y `:1033` llama checker.main. Es v?lido probar que un check discrimina recibos adversariales; `tools/checks/check_native_launcher_registry.py:27` proh?be fijar hash PE en la suite, no probar el checker. Fusionar todo alrededor de un ?nico or?culo sin controles negativos reduce independencia.

### T-ARC-05 ? ?Meta-tests en el gate unit?
**VEREDICTO: VIVO.** `tools/tests/test_sources_are_statically_analysable.py:44`, `tools/tests/test_no_literal_drive_letters.py:177` y `tools/tests/test_packaging_declarations.py:47` son checks est?ticos. Pero `tools/tests/test_command_validation_coverage.py:1` verifica aceptaci?n de payloads y `tools/tests/test_dependency_lock.py:207` integridad contra bytes: no todos son lint. Moverlos requiere conservar ejecuci?n obligatoria.

### T-DUP-01 ? ?retail_quarantine en 8+ suites?
**VEREDICTO: VIVO.** `tools/tests/test_loopback.py:92` valida frontera HTTP; `tools/tests/test_retail_quarantine.py:136` distingue lectura/mutaci?n; `tools/tests/test_process_lifecycle.py:1992` exige bloqueo antes de manifest/guard; `tools/tests/test_server_response_truth.py:48` verifica mensaje. Son invariantes distintas pese al mismo error. Importar el texto esperado desde producci?n quitar?a independencia al contrato de error. Censo literal actual: 13 m?dulos con el t?rmino, no medida de redundancia.

### T-DUP-02 ? ?launcher_registry en 7 sitios?
**VEREDICTO: VIVO.** `tools/tests/test_secure_launcher.py:40` comprueba forma/ruta p?blica, `tools/tests/test_registry_lock.py:46` lectores compartidos vs exclusivo y `tools/tests/test_launcher_registry_update.py:1001` recibos/checker. No son siete copias de un test. RegistryLock contiene comportamiento real, no tres asserts decorativos.

### T-DUP-03 ? ?H8 duplicado y gigante ... roster/sha casi id?nticos?
**VEREDICTO: VIVO.** `tools/_session_coordination/h8_distributed_codex_gate.py:22` ya importa numerosos helpers de h8_real. El primero exige cuatro tareas externas y roster (`:104`); `tools/_session_coordination/h8_real_codex_gate.py:1` crea cuatro proxies desde un proceso. Hay dos drivers grandes y acoplamiento, pero distinto or?culo de procedencia; no se acredit? porcentaje de duplicaci?n. `tools/tests/test_h8_distributed_gate.py:40` prueba rechazo de roster forjado y est? trazado por Git.

### T-DUP-04 ? ?Task7: 4 ficheros encadenados (4.335 l)?
**VEREDICTO: VIVO.** `tools/tests/test_task7_final_authority_regressions.py:12` y `tools/tests/test_task7_final_lifecycle_regressions.py:9` importan fixtures de otros tests. La cifra ya no vale: 1418+1213+1809+344=4784 l?neas actuales. Los dos finales est?n trazados (`git ls-files --error-unmatch`, inventory.txt); d2dd6d2 no elimina el acoplamiento.

### T-DUP-05 ? ?wait_for de 3 eslabones?
**VEREDICTO: VIVO.** `tools/tests/test_wait_for_launch_and_contract.py:38` importa test_wait_for; `tools/tests/test_wait_for_requires_a_live_run.py:32` importa el intermedio. Extraer fixtures sin alterar expectativas es mejora acotada; no se prob? un fallo de import.

### T-DUP-06 ? ?67 imports test?test?
**VEREDICTO: VIVO.** `tools/tests/test_task7_final_authority_regressions.py:12` y `tools/tests/test_wait_for_requires_a_live_run.py:32` son ejemplos abiertos. Censo AST guardado en inventory.txt encuentra **68** referencias ImportFrom/alias Import hacia tests.test_* en el snapshot intermedio. Es acoplamiento real; n?mero sensible a las otras lanes, no un fallo por s? mismo.

### T-CON-01 ? ?10 contratos, 4 mecanismos ... Sin tabla verbo?or?culo?
**VEREDICTO: VIVO.** Diez m?dulos test_* con ?contract? en nombre; `tools/tests/test_daemon_contract.py:73` usa identidad, `tools/tests/test_messages_contract.py:22` parser, `tools/tests/test_vehicle_telemetry_contract.py:15` hash/regex y `tools/tests/test_wait_for_launch_and_contract.py:46` timestamps/fixtures. Es heterogeneidad real, no necesariamente defecto: cada consumidor necesita or?culo apropiado. La ausencia absoluta de tabla no queda probada por la lista; `tools/dayz_mcp/effective_schema_catalog.py:9` ya incluye tool, fuente y casos de aceptaci?n/rechazo.

### T-CON-02 ? ?Triple fuente vehicle-trace?
**VEREDICTO: FALSO.** `tools/schemas/vehicle-trace-v1.json:3` define estructura del trace; `tools/fixtures/vehicle-trace-civilian-sedan-control-v1.json:2` es un **curso** con secuencia de control; `tools/tests/fixtures/vehicle_trace/negative_mutations.json:3` define adversarios. `tools/tests/test_vehicle_trace.py:23` consume expl?citamente fuentes diferentes. `tools/tests/test_vehicle_prepare_fixture.py:42`/`:62` prueba un verbo productivo; convertirlo s?lo en factory suprime pruebas de routing y validaci?n.

### T-CON-03 ? ?Fixtures gigantes inline?
**VEREDICTO: VIVO.** `tools/tests/test_tool_registry_fingerprint.py:5`?`:9` fija blobs externos de varios KB; `tools/tests/test_task7_final_authority_regressions.py:12` muestra reuso de fixtures desde m?dulos test. Es mantenimiento pesado, pero el literal independiente evita calcular expected con el mismo productor; mover a fixture binaria es compatible, regenerar/hash propio como ?nico or?culo no lo es.

### T-CON-04a ? ?Basura .bak y logs en el ?rbol?
**VEREDICTO: VIVO.** Censo inventory.txt: 26 *.bak* en tools/tests. `tools/tests/test_sources_are_statically_analysable.py:36` s?lo enumera *.py, as? que esos sufijos .py.bak_* no entran en ese check. Pueden ensuciar b?squedas amplias, no ese rglob concreto. No se borr? nada.

### T-CON-04b ? ?eliminar .bak (ya en git)?
**VEREDICTO: FALSO.** `git ls-files 'tools/tests/*.bak*'` devuelve vac?o (exit 0), registrado en inventory.txt. `tools/tests/test_sources_are_statically_analysable.py:36` tampoco los protege/recorre. Que exista el fichero original trazado no demuestra que los bytes de su backup est?n versionados; la premisa para borrarlos no est? acreditada.

### T-CON-05 ? ?mutation_gate infrautilizado ... ning?n test_*contract lo invoca?
**VEREDICTO: NO VERIFICABLE.** No aparece llamada textual en esos m?dulos; `tools/lote_harness/mutation_gate.py:2` define herramienta del **receptor**, invocada desde fuera, y `:10` relata uso en nueve lanes. Ausencia de import en tests no mide utilizaci?n de CLI/CI. `tools/tests/test_vehicle_trace.py:261` ya comprueba mutaciones por otra v?a. Har?an falta registros de ejecuci?n y presupuesto por contrato.

## 5. CONSOLIDADO POTENTIAL ? hip?tesis

### E-P01 ? ?Title ... si no hereda ... di?logo nunca abre?
**VEREDICTO: FALSO.** `../scripts/1_core/proto/enwidgets.c:219` declara **MultilineTextWidget extends TextWidget**. El cast de `addon/scripts/5_Mission/MCPDialogController.c:454` es compatible con el layout (`addon/gui/layouts/mcp_dialog.layout:26`); no necesita un vuelo para decidir esa herencia.

### E-P03 ? ?m_PollCtx/m_PollCallbackRefs/watchdog ... c?digo muerto?
**VEREDICTO: FALSO.** `addon/scripts/5_Mission/MCPClientBridge.c:512` explica contexto compartido y descarte por identidad; `:287` ejecuta watchdog, `:532` abandona callback y `:20` ignora callbacks obsoletos. Compartir contexto no hace muertos watchdog/refs. La rama reset para contextos distintos es condicional defensiva; no autoriza borrar todo el mecanismo.

### E-P05a ? ?exec_enforce ... arbitrario por REST loopback?
**VEREDICTO: FALSO.** `tools/dayz_mcp/loopback.py:1868` requiere allowlist y audit; `:1870` exige expresi?n exacta autorizada. Existe techo HTTP de 1 MiB (`:190`). El bridge `addon/scripts/5_Mission/MCPBridge.c:1706` ejecuta s?lo lo que recibe del daemon autenticado; omitir esa frontera sobredimensiona la exposici?n.

### E-P05b ? ?sin cap de longitud ni allowlist de main_fn?
**VEREDICTO: VIVO.** El bridge s?lo exige expr no vac?o (`addon/scripts/5_Mission/MCPBridge.c:1699`); daemon acepta main_fn string sin lista propia (`tools/dayz_mcp/loopback.py:1864`, `:1907`). No hay cap espec?fico 4096; s? techo de body y allowlist de expr. Endurecimiento adicional requerir?a caso y pol?tica, no confundirlo con ejecuci?n p?blica irrestricta. Breakglass es contractual (`product-spec.md:174`).

### E-P04 ? ?MCPBridge.c:812 ... DispatchPlayerRespawn ... doble jugador?
**VEREDICTO: MOVIDO.** La funci?n est? en **`addon/scripts/5_Mission/MCPClientBridge.c:812`**, no en servidor: `addon/scripts/5_Mission/MCPBridge.c:812` trata acceso al veh?culo. El orden RespawnPlayer (`:838`)?SimulateDeath (`:843`) existe, pero tambi?n es el orden vanilla (`../scripts/5_mission/gui/ingamemenu.c:348`, `:353`). Doble jugador no est? demostrado y requerir?a reproducci?n con build/estado concreto.

### E-P02 ? ?un solo coche/trace ... s_Car queda colgando?
**VEREDICTO: NO VERIFICABLE.** `addon/scripts/4_World/MCP_CarScript.c:155` impide segundo trace, `:182` guarda coche y `:585` lo limpia; `:422` comprueba !car al capturar. Es real el singleton; borrar externamente y observar si queda estado l?gico activo/trace_exists requiere motor. No se ha probado referencia colgante ni crash; limitar a un trace no contradice por s? solo G3.

### E-P06 ? ?UI recursiva sin cota de profundidad?
**VEREDICTO: VIVO.** `addon/scripts/5_Mission/MCPClientBridge.c:2165` recurre sin depth; CollectUiNodes tambi?n (`:2453`), pero su l?mite de nodos (`:2441`) acota indirectamente una cadena. CountUiWidgetsNamed no tiene ese l?mite general. Stack overflow es hip?tesis sin umbral medido; depth=32 debe fallar expl?citamente, no esconder matches y fingir unicidad.

### P-P01 ? ?capturas ... si son frecuentes?
**VEREDICTO: NO VERIFICABLE.** `tools/mcp_capture.py:880` lanza subprocess y `:944` abre imagen. Sin conteo/latencia por consumidor no hay evidencia de cuello de botella; >50/d?a no constituye un presupuesto de rendimiento justificado. Repite P-OPT-06.

### T-P01 ? ?No se sabe qu? gates exigen DayZ vivo?
**VEREDICTO: FALSO.** `tools/g0_abba_gate.py:4`, `tools/g0_site_gate.py:15` y `tools/tramoA_verbs_gate.py:36` lo hacen visible; `tools/tests/test_g0_abba_verdict.py:1` es test puro del driver. Falta de flag es propuesta de UX/seguridad de ejecuci?n, no desconocimiento indecidible offline. Repite T-OPT-04.

### T-P02 ? ?Infrautilizado ... quiz? lentitud en CI?
**VEREDICTO: NO VERIFICABLE.** `tools/lote_harness/mutation_gate.py:38` lanza s?lo m?dulos expl?citos y `:2` asigna uso al revisor; no mide cu?ntas veces CI lo invoca ni su coste. Repite T-CON-05.

## 6. PROPUESTA DE UNIFICACI?N ? coste, riesgo y contrato

Estimaciones de revisi?n, no plazos comprometidos: bajo = edici?n acotada m?s comprobaci?n local; medio = varios m?dulos y regresiones; alto = frontera de ejecuci?n/autoridad o validaci?n in-game. Los nombres de clases nuevas son **[DESIGN] de la auditor?a**, no APIs existentes verificadas. No se implement? ninguna propuesta.

### 6.1 Enforce

| Propuesta de la auditor?a | COSTE | RIESGO y relaci?n con product-spec |
|---|---|---|
| TickHub + BridgeBase + HTTP/callbacks | Alto | Alto: cliente tiene callback identity/watchdog (`addon/scripts/5_Mission/MCPClientBridge.c:20`, `:287`), servidor no. No contradice por s? misma A2/A5 (`product-spec.md:43`, `:46`), pero debe conservar independencia por actor y resiliencia. El 70%/400 l?neas no es presupuesto fiable. |
| DriveProbe + WorldQuery | Alto | Alto: server y owner-client controlan distinto (`addon/scripts/5_Mission/MCPBridge.c:3101`, `addon/scripts/5_Mission/MCPClientBridge.c:3101`); primer/?nico/m?s cercano no son equivalentes (`addon/scripts/5_Mission/MCPBridge.c:2769`, `:1565`, `addon/scripts/5_Mission/MCPClientBridge.c:1990`). Debe preservar B3/G1 y rechazo de ambig?edad B4 (`product-spec.md:56`, `:57`, `:120`). |
| Servidor adopta JobRunner | Medio | Medio: cambia orden ready/process (`addon/scripts/5_Mission/MCPBridge.c:2839`, `addon/scripts/5_Mission/MCPJobRunner.c:122`). Sin contradicci?n si conserva deadlines, correlaci?n A3 (`product-spec.md:44`) y resultados. |
| Di?logo pierde segundo reloj | Medio | Alto sin prueba con pausas/time multiplier: `addon/scripts/5_Mission/MCPDialogController.c:944` usa dos deadlines. Conservar timeout terminal del contrato de di?logo; eliminar respaldo no es mera limpieza. |
| Guards pollHz/duplicate_id/unknown_job_kind/result-dropped | Bajo?medio | Medio: clamp es cambio de config; duplicados no tienen sobrescritura demostrada; kinds son internos. Compatible con A4/A5 si se define error y no se altera correlaci?n. Fuentes: `addon/scripts/5_Mission/MCPBridge.c:195`, `:579`, `:2884`, `:3436`. |
| Poll lento pese a backlog | Medio | Alto e injustificado por E-BUF-02: `addon/scripts/5_Mission/MCPBridge.c:114` ya drena. Cambia backpressure sin demostrar bloqueo; podr?a aumentar presi?n. |
| Early-out OnInput, ToLower contactos, caps cache, GetGame local | Bajo?medio | Bajo/medio con prueba real: Capture ya tiene early-out (`addon/scripts/4_World/MCP_CarScript.c:207`), contactos tienen cortocircuito (`:252`), caps se recodifica (`addon/scripts/5_Mission/MCPBridge.c:235`). Conservar reaplicaci?n/deadman G1; beneficio por medir. |
| Borrar logs de fase y pool de 64 | Bajo?medio | Medio: pool puede ser scratch porque copia escalares (`addon/scripts/5_Mission/MCPBridge.c:2336`); logs distinguen validate/create (`:550`). No eliminar trazabilidad diagn?stica ?til de H13 (`product-spec.md:148`) sin sustituci?n. |
| InputLock compartido con refcount (diagrama) | Medio | Alto: focus, HUD, simulation y PlayerControlEnable difieren (`addon/scripts/5_Mission/MCPDialogController.c:902`, `addon/scripts/5_Mission/MCPClientBridge.c:3926`). Debe conservar G4 (`product-spec.md:123`) y no liberar el bloqueo de otro consumidor. |

### 6.2 Python

| Propuesta de la auditor?a | COSTE | RIESGO y relaci?n con product-spec |
|---|---|---|
| SupervisorTick de 1 s, waiters suscritos | Alto | Alto: obligaciones a 45/30 s y pollers de 0,05/0,5 s no comparten budget (`tools/dayz_mcp/server.py:78`, `:84`, `tools/dayz_mcp/lease_supervisor.py:103`). Un trabajo bloqueado no puede retrasar heartbeats/cleanup. No contradice s?lo si mantiene H4/H9 (`product-spec.md:139`, `:144`). P0 es prioridad propuesta, no severidad de incidente. |
| HostInventory / PortSnapshotCache | Medio | Alto si se usa para autorizar: ya hay cache de box (`tools/dayz_mcp/process_lifecycle.py:4144`). S?lo reutilizar observaci?n; H6/H10 exigen identidad/snapshot actual en la frontera (`product-spec.md:141`, `:145`). |
| SessionClient ?nico + vistas | Medio?alto | Medio/alto: retry pre-request y validaci?n de recuperaci?n tienen sem?nticas propias (`tools/dayz_mcp/server.py:1287`, `tools/dayz_mcp/control_client.py:586`). Mantener cancelaci?n request-bound H9, sin convertir health failure en nuevo spawn indiscriminado. |
| ProcessAuthority / identidad / PS1 delega a Python | Alto | Alto: el PS1 usa hashes WMI distintos (`tools/process-guard.ps1:18`); no cambiar identidad persisted ni ancestry/health silenciosamente. Compatible s?lo con H6 y F5 preservados. |
| Colapsar launcher/daemon authority + bundle_io | Alto | Alto: handle pinning y acreditaci?n pre-byte no son helpers de hash gen?ricos (`tools/dayz_mcp/launcher_registry.py:181`, `tools/dayz_mcp/accredited_daemon_transport.py:1`). Afecta m?dulos sellados (`tools/build_native_launcher.py:53`), exige resellado posterior coordinado si se implementa. H9/H10 prevalecen sobre ahorro de l?neas. |
| LogFollower compartido | Medio?alto | Medio: tail actual ya es incremental (`tools/dayz_mcp/log_tail.py:172`), scan launch es por llamada (`tools/dayz_mcp/server.py:2691`). Debe mantener markers, rotaci?n, truncamiento y aislamiento de run; justificar por medici?n de llamadas concurrentes. |
| retry.py + timeouts.py/env | Medio | Alto si cambia replays: H12 exige exactamente un retry y deadline original (`product-spec.md:147`; `tools/dayz_mcp/daemon_credential.py:234`). Centralizar documentaci?n no contradice; pol?tica gen?rica de reintento/env s? puede hacerlo. |
| GateHarness + mover a tools/gates | Medio | Medio: reubicar cambia imports y rutas (`tools/_session_coordination/h8_distributed_codex_gate.py:22`); mantener or?culos de procedencia independientes y gates H8/H9. El directorio no corrige el defecto por s? solo. |

### 6.3 Tests

El t?tulo dice ocho movimientos, pero el p?rrafo enumera **seis** (`AUDITORIA_MCP_2026-09-07.md:274`), sin justificaci?n del 30%.

| Propuesta | COSTE | RIESGO y contrato |
|---|---|---|
| Extraer helpers wait_for/authority/client_runtime y prohibir imports test?test | Medio | Bajo/medio si conserva casos/expectativas; fuentes `tools/tests/test_wait_for_launch_and_contract.py:38`, `tools/tests/test_task7_final_authority_regressions.py:12`. No contradice contrato. |
| Fusionar quarantine/registry/docs | Alto | Alto si se eliminan fronteras: lifecycle (`tools/tests/test_process_lifecycle.py:1992`) y HTTP (`tools/tests/test_loopback.py:92`) no son equivalentes. Docs ya delega (`tools/tests/test_docs_truth.py:345`). Preservar H3/H5/H6/H10. |
| Harness contractual ?nico y fuente ?nica trace | Alto | Alto: schema, curso y adversarios son diferentes (`tools/tests/test_vehicle_trace.py:22`). Generar expected desde productor amenaza independencia exigida en E5 (`product-spec.md:90`). |
| Etiquetar slow y retirar Popen/venv/stdio de ruta unit | Medio | Alto si dejan de ejecutarse: H12 pide E2E aislado (`product-spec.md:147`); procesos auxiliares no significan DayZ live. Etiquetar es compatible si integraci?n sigue obligatoria. |
| Borrar backups/mover logs | Bajo | Alto para datos no trazados: git no contiene los 26 backups; no autorizar borrado por la premisa ?ya en git?. Archivo recuperable primero. Sin cambio de producto. |
| mutation_gate por contrato | Medio?alto | Alto en ?rbol compartido: herramienta modifica/restaura bytes (`tools/lote_harness/mutation_gate.py:15`). Usar checkout aislado y medir coste; compatible con controles independientes E5/E6, no ejecutarla concurrentemente sobre producci?n. |

## 7. PROPUESTAS ADICIONALES ? coste, riesgo y contrato

| # y propuesta | COSTE | RIESGO / contradicci?n con product-spec |
|---|---|---|
| 7.1 M?trica de ticks/dispatched cada 60 s | Bajo?medio | Bajo con log acotado. Ya existe m_Tick (`addon/scripts/5_Mission/MCPBridge.c:105`), falta histograma/coste. Compatible con A2/H13; ?5 l?neas? no incluye dise?o de medici?n/validaci?n. |
| 7.2 depth?32 en walks UI | Bajo?medio | Medio: una b?squeda truncada no puede devolver falso ?nico. `addon/scripts/5_Mission/MCPClientBridge.c:2165` y `:2453`; B4 exige ambig?edad fail-closed (`product-spec.md:57`). Necesita resultado expl?cito de l?mite o recorrido iterativo; no ?y listo?. |
| 7.3 MCP_DEBUG global para Print/Log | Medio | Medio/alto: proteger observabilidad de fallos; fases actuales en `addon/scripts/5_Mission/MCPBridge.c:550`. Un #define incondicional deja debug activado y no ahorra en release; ?mbito de macro/packaging requiere verificar compilador. H13 (`product-spec.md:148`) pide diagn?stico. |
| 7.4 pollHz 1..10 en Python | Medio | Medio: la lectura mostrada de dayz_mcp.json pertenece al bridge (`addon/scripts/5_Mission/MCPBridge.c:153`, `:195`), no se demostr? un lector daemon equivalente que consuma pollHz. No aplicar en un punto inventado. Compatibilidad A5 por definir; primero localizar productor/consumidor real. |
| 7.5 netstat_spawns_total en doctor | Medio | Bajo para contar en cada call site (`tools/dayz_mcp/orphan_guard.py:378`, `:471`), medio para agregaci?n entre procesos: doctor separado no ve el contador del daemon autom?ticamente. Compatible con H7/H13 si se define ventana/pid y fuente; instrumento preferible a asumir 600 spawns. |
| 7.6 DAYZ_MCP_LIVE en todo Popen/venv/stdio_client | Medio | Alto: confunde integraci?n offline con juego vivo (`tools/tests/test_session_e2e.py:75`, `tools/tests/test_client_credential_rotation_e2e.py:557`). Si deja H12 fuera de CI normal contradice su evidencia E2E. Separar categor?as por efecto, no por token Popen. |
| 7.7 Linter no-test-imports-test | Medio | Medio: primero extraer fixtures; activarlo ahora falla por 68 referencias del snapshot. `tools/tests/test_task7_final_authority_regressions.py:12` demuestra dependencia. Compatible con contrato si no borra cobertura. |
| 7.8 Tabla de TTL en QUICKSTART | Bajo | Bajo si distingue heartbeat, caducidad y budget: 45 s no es TTL de lease; H4 fija 120 s (`product-spec.md:139`), server fija waits en `tools/dayz_mcp/server.py:79`. No hay obligaci?n de TTL ?nico; documentar prop?sitos evita confusi?n. |

## 8. PUNTUACI?N ? revisi?n de las valoraciones

No sustituyo notas subjetivas por otra escala inventada. Cada fila enlaza los mecanismos comprobados; ninguna acredita estado desplegado.

### SCORE-01 ? ?Bridge servidor 6,5?
**VEREDICTO: NO VERIFICABLE.** `addon/scripts/5_Mission/MCPBridge.c:335` acota pending y `:2897` consulta spawn; la nota no define rubric. Duplicaci?n 70% no probada; defectos se delimitan en ?2.

### SCORE-02 ? ?Bridge cliente 6,0?
**VEREDICTO: NO VERIFICABLE.** `addon/scripts/5_Mission/MCPClientBridge.c:267` usa runner; `:1065` refuta la aceptaci?n de throttle=inf. No hay base cuantitativa para 6,0.

### SCORE-03 ? ?CarScript + trace 7,0?
**VEREDICTO: NO VERIFICABLE.** `addon/scripts/4_World/MCP_CarScript.c:207` s? tiene retorno fr?o; 8192 es parte de G3 (`product-spec.md:122`). La nota mezcla coste no medido con requisitos.

### SCORE-04 ? ?Di?logo/UI 7,5?
**VEREDICTO: NO VERIFICABLE.** `addon/scripts/5_Mission/MCPDialogController.c:944` mantiene doble reloj; `../scripts/1_core/proto/enwidgets.c:219` invalida la sospecha del cast. No queda una escala verificable.

### SCORE-05 ? ?Mensajes/config 8,0?
**VEREDICTO: NO VERIFICABLE.** `addon/config.cpp:21` y `addon/scripts/5_Mission/MCPJobRunner.c:90` corroboran estructura, no una puntuaci?n. Copias iguales por bytes, sin build ejecutado.

### SCORE-06 ? ?Daemon/server core 5,5?
**VEREDICTO: NO VERIFICABLE.** `tools/dayz_mcp/server.py:2491` y `:2691` invalidan reescaneo por poll/O(n?) general; su tama?o no demuestra degradaci?n. Nota no utilizable para priorizar sin perfil.

### SCORE-07 ? ?Sesi?n/procesos/lifecycle 5,0?
**VEREDICTO: NO VERIFICABLE.** `tools/dayz_mcp/orphan_guard.py:990` separa health de ancestry y `tools/dayz_mcp/process_lifecycle.py:4144` cachea probes; no son todos costes redundantes. Criterio num?rico ausente.

### SCORE-08 ? ?Launcher/native/seguridad 5,0?
**VEREDICTO: NO VERIFICABLE.** `tools/dayz_mcp/secure_launcher.py:74` ya evita desplazamiento por byte; `tools/dayz_mcp/launcher_registry.py:181` preserva handle. Reducir cantidad de ficheros no acredita mayor seguridad o calidad.

### SCORE-09 ? ?Tests/gates/contratos 4,5?
**VEREDICTO: NO VERIFICABLE.** `tools/tests/test_client_credential_rotation_e2e.py:557` ejercita consumidor real; `tools/tests/test_vehicle_prepare_fixture.py:62` tiene negativos distintos. No hay mapa que demuestre eliminaci?n del 30% conservando se?al; conteos cambian concurrentemente.

### SCORE-10 ? ?Docs/handoff 6,0 ... sin ?ndice com?n (8 ROOT vs 195)?
**VEREDICTO: FALSO.** La premisa verificable est? contradicha por `GATES.md:3`?`:7`: enlaza familias de hojas y explica **195 total, incluidos los ocho ROOT**, no 8 frente a otros 195. La nota 6,0 y grado de mantenimiento de PROJECT-MAP siguen siendo opini?n sin medici?n.

### SCORE-GLOBAL ? ?Nota global ponderada 6,1/10?
**VEREDICTO: NO VERIFICABLE.** `AUDITORIA_MCP_2026-09-07.md:295`?`:306` no define pesos ni rubric. 6,1 coincide con media simple de las diez notas, pero no valida sus premisas, varias corregidas aqu?.

## Correcciones transversales y cambios de esta noche

- `server.py` conserva tipos estrictos en registro (`tools/dayz_mcp/server.py:3187`, `:3430`); 91eecca arregla coerci?n bool. La auditor?a ?2?5 no contiene un hallazgo Python equivalente que convertir autom?ticamente a YA ARREGLADO; no confundirlo con IsFiniteFloat de Enforce.
- Steam conserva timeout como fallo (`tools/dayz_mcp/steam_preflight.py:407`?`:415`) y publica motivo (`tools/dayz_mcp/dayz_test_tool.py:1442`). Cambia el comportamiento de error, no elimina todos los sondeos de P-OPT-01/04.
- `tools/dayz_mcp/daemon.py:1646` usa CREATE_NO_WINDOW tras cb2cdd8. La auditor?a no identifica el bug de consola; no demuestra por s? mismo cierre de polling/autoridad ni supervivencia real del nuevo despliegue.
- Los cinco m?dulos de d2dd6d2 est?n trazados, comprobados por `git ls-files --error-unmatch`; salida ?ntegra en inventory.txt. No se ejecutaron. Sus imports y tama?o siguen ah?; trazarlos no resuelve T-DUP-03/04.
- Inventario inicial: 64 Python de producto, 17 top-level, 185 tests; intermedio: 187 tests, 68 imports test?test, 37 markdown de gates. GetGame() literal en los nueve .c de addon: 89, no 87. Task7: 4784 l?neas, no 4335. Las dimensiones medidas son cardinalidad y bytes/lineas, **no** coste CPU, cobertura ni complejidad efectiva.
- Todos los .c/config/layout de addon comparados con ../DayZ_MCP coinciden en bytes (inventory.txt). El brief daba la ruta hermano como si estuviera dentro del dev workspace; se corrigi? sin escribir en ella. MissionServer=409 B y MissionGameplay=443 B: ?0 de la auditor?a intercambia esos tama?os.
- Recomendaci?n documental: conservar la auditor?a original como evidencia hist?rica acompa?ada de este triaje y su snapshot; no usarla como backlog verificado ni plan aprobado de recortes. La decisi?n de trazar o mover el original queda al receptor, como exige el brief.


El control final detect? una edici?n concurrente de `tools/dayz_mcp/host_config.py`: se reabri? el diff y `require_matching_keyfile` pas? de :119 a **:122**, con el mismo cuerpo relevante. Se actualiz? la cita; los cambios ajenos en resolver/diagn?stico quedan FUERA DE MI ALCANCE de implementaci?n. No se atribuyen a esta lane.

## Resumen de veredictos

Los **79 IDs originales de ?2?5 est?n cubiertos**. Se separaron afirmaciones compuestas para no esconder una conclusi?n falsa dentro de un hecho cierto: resultan 87 entradas en ?2?5; con resumen ejecutivo y puntuaci?n, 103. Repeticiones de ?5 y ?8 se mantienen por trazabilidad, por lo que estos totales **no equivalen a 103 defectos ?nicos**. VIVO incluye observaciones estructurales/positivas expresamente delimitadas, sin aprobar severidad ni propuesta.

| Secci?n | VIVO | YA ARREGLADO | MOVIDO | FALSO | NO VERIFICABLE | Total |
|---|---:|---:|---:|---:|---:|---:|
| 1. Resumen ejecutivo ? orden de lectura | 1 | 0 | 0 | 1 | 3 | 5 |
| 2. ENFORCE SCRIPT ? hallazgos | 29 | 0 | 0 | 3 | 1 | 33 |
| 3. HARNESS PYTHON ? hallazgos | 14 | 1 | 2 | 4 | 1 | 22 |
| 4. TESTS Y GATES ? hallazgos | 14 | 1 | 0 | 6 | 1 | 22 |
| 5. CONSOLIDADO POTENTIAL ? hip?tesis | 2 | 0 | 1 | 4 | 3 | 10 |
| 8. PUNTUACI?N ? revisi?n de las valoraciones | 0 | 0 | 0 | 1 | 10 | 11 |
| **Total** | 60 | 2 | 3 | 19 | 19 | **103** |

Prioridad para el receptor: corregir las premisas falsas antes de encargar refactors; reproducir los riesgos de callback/lifecycle y medir carga; preservar las fronteras de autoridad y los or?culos independientes. REPORT/STATE son handoff documental, no aprobaci?n de un build ni evidencia in-game.
