# Plan decidible de la auditoría — lane M1

**Propuesta al receptor: HACER H1–H2; resolver D1–D4 individualmente; rechazar N1–N16 en este alcance.** QA previo: 12/12 acuerdos, 0 discrepancias; evidencia en [QA-TRIAJE.md](QA-TRIAJE.md). Los 60 VIVO tendrán destino explícito en la tabla final. Este documento propone decisiones; no acredita que se hayan aprobado ni ejecutado cambios de producto.

Base: `4e34bda`, 2026-09-08. Rutas relativas a `C:/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev`. [EXACT] identifica fuente/comportamiento leído; [DESIGN] identifica cambios, nuevos archivos y criterios propuestos. Coste pequeño = edición localizada y validación offline breve; medio = varios consumidores y regresiones; grande = cruce de actores, autoridad, bundle o cobertura extensa. Son estimaciones por alcance, no horas medidas.

**Frontera:** toda unidad seleccionable aquí se resuelve con documentos, fuente y pruebas offline aisladas. Los cambios que necesitarían juego, renderer o caja compartida quedan en NO HACER, con la razón; no se propone ejecutarlos ni “validarlos después” para declararlos terminados. NO HACER tampoco reclasifica como FALSO un riesgo que sigue VIVO.

Para una unidad implementable, PASS exige su criterio observable y los módulos explícitos pertinentes; un fallo nuevo atribuible al cambio es FAIL; falta de entorno, skip del caso esencial o ausencia de evidencia es INCONCLUSO. Contar tests o conservar su número no demuestra por sí solo equivalencia: deben conservarse expectativas, negativos y discriminadores del consumidor. Ninguna unidad autoriza borrar cobertura por porcentaje.

Los cinco commits citados no cierran ninguno de los 60 VIVO como bloque. Sí corrigen subpremisas históricas: el import Steam ya está arreglado (`tools/tests/test_lote2_t2_steam.py:5`, 51759a6) y el generador existe (`tools/gen-project-map.ps1:1`, cef8567). La generación del mapa actual no queda acreditada por ello: H1 tiene dos rojos documentales reproducidos. 9f4ef85 aporta diagnóstico de preflight/attach y 4e34bda cambia acreditación de registros; no son prueba de que sobren sondeos o autoridades. cb2cdd8 ya estaba en la base del triaje.

## HACER

### H1 — Actualizar el artefacto PROJECT-MAP con el generador existente

**QUÉ [DESIGN]:** regenerar únicamente `PROJECT-MAP.md` mediante `tools/gen-project-map.ps1`; no crear otro generador ni editar la auditoría. La invocación documentada está en `tools/gen-project-map.ps1:113`; su escritura completa y verificación están en :172.

**POR QUÉ ahora:** el mapa actual manda leer un límite fijo de 125 líneas (`PROJECT-MAP.md:21`, :23) y lista scripts que no resuelven. Ejecuté solo `tests.test_docs_truth`: `Ran 20 tests in 0.272s`, `FAILED (failures=2, skipped=3)`, exit 1. Los dos fallos son ProjectMapHandoffCountsDocsTest y ProjectMapEntryPointsDocsTest; salida completa en [test-docs-truth.log](test-docs-truth.log). El generador actual ya emite el marcador correcto y enumera rutas relativas de scripts (`tools/gen-project-map.ps1:93`, :128). Atribuirlo a un fallo de OneDrive o a un escritor específico sería una hipótesis: no se observó la escritura causante.

**COSTE:** pequeño; un artefacto, un generador ya trazado, un módulo documental. **RIESGO:** sobreescribir una regeneración concurrente o producir otra foto efímera de tamaños. No cambia código ni criterios; protege lectura del estado real, acorde con E5/H7 (`product-spec.md:90`, :142).

**QUE LO DA POR HECHO:** la copia entregada contiene LIVE-STATE:END, no fija límites de HANDOFF y sus entry points existen; los dos casos identificados ejecutan y pasan dentro de `tests.test_docs_truth`. Los demás resultados se informan por separado; hay dos skips del escenario sparse checkout y uno porque P:/scripts está ausente. No acreditan esos escenarios. Releer tamaño y bytes tras generar. No exigir igualdad de SHA entre ejecuciones: el generador incluye fecha/hora en :110. Si el generador actual no satisface esos criterios, H1 queda INCONCLUSO y se delimita el fallo del generador; no ampliar silenciosamente a código.

**DEPENDENCIAS:** ninguna funcional; si se acepta H2, la última regeneración y comprobación de H1 va después de sus cambios documentales. `PROJECT-MAP.md` está fuera de los archivos escribibles de esta lane; aquí se entrega la evidencia, no se regenera. Esta unidad es complementaria a SCORE-10, que descartaba la falta de índice de gates, no el estado del mapa.

### H2 — Documentar cadencias y presupuestos con propósito y dueño

**QUÉ [DESIGN]:** añadir una tabla compacta en `tools/README-mcp.md` junto a “Session protocol / Managed lifecycle”; `QUICKSTART.md` recibe solo un enlace a esa tabla, conservando sus cinco pasos. No crear `timeouts.py`, no igualar valores ni introducir overrides de entorno.

**POR QUÉ ahora:** la información útil ya existe parcialmente en `tools/README-mcp.md:58` y :78, pero la propuesta §7.8 confunde heartbeat con TTL. La tabla consolida diferencias que afectan al consumidor; no promete ahorro de CPU. Cubre P-OPT-05 y §7.8.

**Contenido mínimo [EXACT, fuentes abiertas]:**

| Propósito | Valor actual | Fuente |
|---|---:|---|
| Heartbeat de lease durante trabajo exclusivo | 45 s | `tools/dayz_mcp/lease_supervisor.py:12` |
| TTL de lease y waiter de sesión | 120 s | `tools/dayz_mcp/session_coordination.py:15`; contrato H4, `product-spec.md:139` |
| Heartbeat del claim de caja durante launch | 30 s | `tools/dayz_mcp/server.py:83`, :3044 |
| TTL del claim de caja ya concedido | 600 s | `tools/dayz_mcp/session_coordination.py:16` |
| Presupuesto máximo solicitado a wait_for / wait_for_box | 600 s, cada operación | `tools/dayz_mcp/server.py:79`, :80 |
| Poll interno genérico / mínimo wait_for / poll box | 0,05 / 0,5 / 1 s; propósitos distintos | `tools/dayz_mcp/server.py:78`, :84, :81 |
| Pin máximo de una operación | 300 s | `tools/dayz_mcp/session_coordination.py:40` |
| Timeout de un sondeo netstat TCP / UDP | 10 / 3 s; no TTL | `tools/dayz_mcp/orphan_guard.py:380`, :473 |

**COSTE:** pequeño; dos documentos y comparación directa con constantes/consumidores. **RIESGO:** convertir implementación en recomendación de heartbeat manual para una tool que ya lo gestiona; distinguir control automático del launcher y trabajo exclusivo manual. No cambiar H4/H9/H12 (`product-spec.md:139`, :144, :147) ni añadir detalles de implementación a los pasos básicos de instalación.

**QUE LO DA POR HECHO:** el lector distingue cadencia, expiración y presupuesto; todos los números enlazan a su fuente vigente, 45 no aparece como TTL y no se recomienda heartbeat manual durante `dayz_test_run`. Los enlaces resuelven. Revisión documental basta; no añadir un test que copie la tabla como expected.

**DEPENDENCIAS:** ninguna funcional; si se acepta H1, terminar estos cambios documentales antes de su última regeneración.

## DECIDIR

Cada unidad pide una decisión distinta y registra una recomendación. No se ejecuta ninguna como consecuencia automática del QA.

### D1 — Extraer las fixtures compartidas de wait_for sin recortar casos

**Opciones:** A, extraer ahora la cadena acotada (recomendada si se va a seguir trabajando en wait_for); B, conservarla hasta el siguiente cambio funcional en ese dominio. La evidencia es deuda de mantenimiento, no un import roto hoy.

**QUÉ [DESIGN] si A:** crear `tools/tests/helpers_wait_for.py`, `tools/tests/helpers_client_runtime.py` y `tools/tests/helpers_mcp_response.py`; mover únicamente los helpers requeridos por los cuatro módulos wait_for. Adaptar `tools/tests/test_wait_for.py`, `tools/tests/test_wait_for_launch_and_contract.py`, `tools/tests/test_wait_for_requires_a_live_run.py`, `tools/tests/test_wait_for_marker.py`, `tools/tests/test_client_mode.py` y `tools/tests/test_mcp_tools.py`. Mantener reexports de helpers en los dos últimos para no modificar en este encargo todos sus consumidores ajenos.

**POR QUÉ / COSTE:** medio, por nueve archivos y dos fixtures distintas llamadas _FakeRuntime. La de `tools/tests/test_wait_for.py:43` responde a queries de players; la de `tools/tests/test_wait_for_launch_and_contract.py:55` rechaza cualquier bridge call. No fusionarlas bajo una abstracción genérica. Los imports problemáticos están en launch :38 y requires_a_live_run :32; la factory de ClientRuntime está en `tools/tests/test_client_mode.py:27`. A elimina la dependencia incidental entre suites; B evita ruido mientras no haya trabajo funcional previsto. T-DUP-05 y una parte identificable de T-DUP-06.

**RIESGO / contrato:** cambiar vida del keyfile temporal, reloj del fixture o alcance de patch puede convertir un negativo en falso verde. C4 (`product-spec.md:68`) exige conservar ventanas/markers; H10 exige no sustituir el comportamiento por una factory que ignore acreditación. No regenerar expectativas desde producto.

**QUE LO DA POR HECHO:** los cuatro módulos wait_for ya no importan tests.test_* y los tres helpers nuevos tampoco; siguen existiendo los dos comportamientos de runtime. Mismos casos y expectativas, importación individual limpia y resultados documentados de `tests.test_wait_for`, `tests.test_wait_for_launch_and_contract`, `tests.test_wait_for_requires_a_live_run`, `tests.test_wait_for_marker`, `tests.test_client_mode` y `tests.test_mcp_tools`, uno a uno, con procesos auxiliares aislados cuando corresponda. Un verde global por cantidad no basta. Si B, registrar aplazamiento de esta extracción, sin llamar resuelto a T-DUP-05.

**DEPENDENCIAS:** ninguna funcional; D3 puede catalogar antes las obligaciones. No activa aún un linter global ni migra los otros imports de las 68 referencias.

### D2 — Extraer el soporte de las cuatro suites Task7

**Opciones:** A, extracción mecánica preservando los casos (recomendada al próximo encargo sobre autoridad/lifecycle); B, dejar los imports hasta que se toque esa familia. Ninguna opción autoriza fusionar o borrar tests.

**QUÉ [DESIGN] si A:** crear `tools/tests/helpers_authority.py` con Clock/Sequence/identidades, Audit, Handle/Launcher/Guard, record/identity y LifecycleFixture, y `tools/tests/helpers_cli_io.py` para TtyInput. Adaptar los cuatro `tools/tests/test_task7_review_regressions.py`, `tools/tests/test_task7_rereview_regressions.py`, `tools/tests/test_task7_final_authority_regressions.py`, `tools/tests/test_task7_final_lifecycle_regressions.py` y `tools/tests/test_lifecycle_cli.py`. No fabricar un framework contractual.

**POR QUÉ / COSTE:** medio. Hay 4784 líneas en los cuatro módulos, pero se mueven helpers, no ese volumen de pruebas. Los imports están en final_authority :12/:18, rereview :33 y final_lifecycle :9/:10. `tools/tests/test_task7_review_regressions.py:247` muestra una fixture con manifest, probes y launcher falsos: es más que Clock/IDENTITY. T-DUP-04 y parte de T-DUP-06.

**RIESGO / contrato:** perder el reloj inyectable, copiar referencias en vez de valores o cambiar cleanup invalidaría los discriminadores H2/H4/H5/H6 (`product-spec.md:137`, :139, :140, :141). Mantener las expectativas independientes de producción.

**QUE LO DA POR HECHO:** los cuatro Task7 ya no importan tests.test_*; sus helpers tampoco; no desaparecen escenarios ni controles adversariales. Ejecutar separadamente `tests.test_task7_review_regressions`, `tests.test_task7_rereview_regressions`, `tests.test_task7_final_authority_regressions`, `tests.test_task7_final_lifecycle_regressions` y `tests.test_lifecycle_cli`. Conservar cuerpos de test/expected y registrar cualquier renombre de fixture. Si B, el acoplamiento queda explícitamente pendiente, sin un veto general a futuros cambios.

**DEPENDENCIAS:** ninguna con D1; evitar extracción simultánea de helpers comunes fuera de esta lista. No toca formatos de runs ni módulos empaquetados.

### D3 — Índice de pruebas por consumidor y efecto, o mantener los índices actuales

**Opciones:** A, añadir un índice acotado de contratos y efectos (recomendada si el receptor necesita repartir trabajo); B, mantener documentación existente. No hay demostración de que falte toda tabla: el catálogo efectivo ya contiene información de tools y los drivers declaran sus requisitos. La mejora buscada es consulta por criterio, no reducir ejecución.

**QUÉ [DESIGN] si A:** crear `docs/TESTING.md` y enlazarlo desde `tools/README-mcp.md`; listar inicialmente los diez módulos contract del censo y las familias de esta revisión. Cada fila: criterio de product-spec, consumidor, oráculo independiente, negativo existente, módulo/comando y efecto (fuente pura, integración offline aislada o driver que requiere juego). La categoría de juego solo documenta la frontera; no propone correrla aquí. T-CON-01, T-ARC-05; variante conservadora de §6.3.3/4 y §7.6.

**POR QUÉ / COSTE:** medio; lo costoso es seguir el consumidor y el negativo, no escribir una tabla. `tools/tests/test_session_e2e.py:32` usa puerto 0 y :75 peer falso; `tools/tests/test_client_credential_rotation_e2e.py:557` usa cliente stdio real como exige H12. El token Popen no identifica “necesita DayZ”.

**RIESGO / contrato:** un índice erróneo puede esconder E2E obligatorios; H12 (`product-spec.md:147`) sigue obligatorio y E5 (:90) exige controles independientes. No mover tests a pre-commit ni imponer DAYZ_MCP_LIVE a procesos auxiliares.

**QUE LO DA POR HECHO:** cada módulo del inventario seleccionado tiene consumidor y control verificables; se diferencia explícitamente integración offline de juego y no se elimina ninguna invocación existente. B cierra solo la decisión documental. No declarar un ahorro del 30%, menor duración ni equivalencia de cobertura.

**DEPENDENCIAS:** ninguna; si H2 también se acepta, integrar el enlace sobre su versión actual de README, no sobre una copia previa.

### D4 — Mutación receptora puntual frente a obligatoriedad por contrato

**Opciones:** A, conservar el uso puntual por el receptor (recomendada: no se midió infrautilización); B, acreditar un solo piloto offline del contrato de argv antes de decidir cualquier obligatoriedad en CI.

**QUÉ [DESIGN] si B:** en una copia aislada, usar `tools/lote_harness/mutation_gate.py` sobre `tools/dayz_mcp/daemon_contract.py` y únicamente `tests.test_daemon_contract`; guardar receta, salida y hashes en `reviews/audit-contract-pilot/` (nuevo). No modificar mutation_gate ni CI; ninguna mutación aterriza en el árbol compartido. Una receta candidata elimina el literal --daemon en el constructor de argv (`tools/dayz_mcp/daemon_contract.py:15`, literal en :21); debe casar exactamente una vez con su contexto, según la herramienta, antes de ejecutarla.

**POR QUÉ / COSTE:** medio por aislamiento, procedencia y control receptor; una sola pareja base/mutante. La herramienta exige base verde y mutante rojo (`tools/lote_harness/mutation_gate.py:13`) y el test compara argv literal (`tools/tests/test_daemon_contract.py:75`). T-CON-05/T-P02 siguen NO VERIFICABLE; esta decisión no los convierte en defectos.

**RIESGO / contrato:** mutar/restaurar un archivo compartido pisa cambios ajenos; usar copia aislada es condición de B. No recalcular el expected con el mismo constructor (E5/E6, `product-spec.md:90`, :91). No basar el éxito únicamente en el exit code de un proceso que falló por imports/entorno.

**QUE LO DA POR HECHO:** si A, decisión escrita con el motivo y sin etiqueta “infrautilizado”. Si B, base ejecuta el módulo y pasa; el mutante falla en la expectativa de argv, restauración por SHA coincide y se registra coste real. Presupuesto [DESIGN] del piloto: una receta, una ejecución completa; si es INCONCLUSO, se entrega ese resultado sin rondas automáticas. La decisión de CI queda abierta hasta ver el coste; no se inventa una ruta de workflow de CI no inspeccionada.

**DEPENDENCIAS:** ninguna para A; para B, criterio y receta registrados antes de mutar. No se resella ningún bundle, ni siquiera aunque daemon_contract figure en PACKAGED_MODULES.

## NO HACER

En estas unidades, “QUE LO DA POR HECHO” es el criterio para cerrar la **decisión de descarte**, no un falso PASS de código. Reabrir exige evidencia nueva que ataque el motivo concreto; el mero número de archivos, líneas o tests no es evidencia nueva. No se descartan los riesgos reales por falta de una caja en esta lane.

### N1 — Unificar transporte y jobs de ambos bridges en un único framework

**QUÉ se rechaza / archivos:** TickHub, BridgeBase, HttpUtil, DriveProbe y WorldQuery [DESIGN de la auditoría], más migrar el servidor a JobRunner. Afectaría `addon/scripts/5_Mission/MCPBridge.c`, `MCPClientBridge.c`, `MCPCallbacks.c`, `MCPJobRunner.c`, `MissionServer.c` y `MissionGameplay.c` bajo ese mismo directorio. No crear esos nuevos módulos.

**POR QUÉ no / COSTE:** grande. La duplicación está viva, pero no se demostró 70%, 400 líneas ni que los actores sean intercambiables. QA E-DUP-02 demuestra diferencias de contexto/callback/watchdog; la diferencia entre control server y owner-client ya es contractual en B3/G1 (`product-spec.md:56`, :120). Eliminar líneas no paga por sí solo la validación necesaria.

**RIESGO:** correlación y resiliencia A2/A3/A5; orden de jobs, selección de entidades y ownership de control. La equivalencia del motor no se valida con grep. **QUE LO DA POR HECHO:** registrar rechazo del refactor completo y conservar el motivo junto a E-DUP-01/02/03/04 y E-TICK-03. **DEPENDENCIAS:** ninguna; su aceptación necesitaría un alcance y evidencia distintos, no una dependencia oculta del plan offline.

### N2 — Cambiar reloj, bloqueo o recorrido UI por simplificación

**QUÉ se rechaza / archivos:** quitar el segundo reloj, introducir InputLock con refcount, cachear walks o imponer depth=32 sin semántica de truncamiento, en `addon/scripts/5_Mission/MCPClientBridge.c` y `addon/scripts/5_Mission/MCPDialogController.c`.

**POR QUÉ no / COSTE:** grande. `MCPDialogController.c:944` acepta dos deadlines deliberadamente y :963 usa tiempo real para elapsed; `MCPClientBridge.c:2165` sí recurre sin depth. Una cota que oculte el segundo widget convertiría ambigüedad en falso único; no es “y listo”. El estado físico de input/focus y el umbral de pila no se observaron.

**RIESGO:** B4, G4 y los terminales válidos del diálogo (`product-spec.md:57`, :123, :416). **QUE LO DA POR HECHO:** rechazar estos cambios como limpieza y mantener E-TICK-04/E-DUP-05/E-HOT-09/E-P06 como hechos o riesgos delimitados. No anunciar overflow reproducido. **DEPENDENCIAS:** ninguna; la validación de UI exige motor y queda FUERA DE ESTE ALCANCE, sin tarea in-game propuesta.

### N3 — Paquete de guards “baratos” de transporte/lifecycle Enforce

**QUÉ se rechaza aquí / archivos:** cambiar Shutdown, limitar callbacks/jobs, clamp de pollHz, añadir duplicate_id/unknown_job_kind y log de result descartado, en `addon/scripts/5_Mission/MCPBridge.c`, `addon/scripts/5_Mission/MCPClientBridge.c` y `addon/scripts/5_Mission/MCPJobRunner.c`.

**POR QUÉ no / COSTE:** grande; la edición es media, pero cerrar correctamente la equivalencia de lifecycle determina el coste total. Hay riesgos reales: `MCPBridge.c:3451` retiene callbacks; :3436 retorna sin log; `MCPClientBridge.c:237` y :252 permiten dos entradas a Shutdown y :3911 desreferencia GetGame. No se ha reproducido pérdida de un resultado aceptado, agotamiento ni terminación del proceso. “NO HACER” se limita a esta caja de herramientas offline; no es un cierre de E-ERR-04/E-BUF-01.

**RIESGO:** parar DrainPending por número de callbacks puede impedir progreso sin resolver el drenaje de POST de jobs. Un guard de Shutdown mal situado puede impedir el POST terminal conservado en `MCPClientBridge.c:4066`. Clamp/errores cambian config o wire; map.Insert con duplicados sigue opaco (QA E-BUF-03b). A3/A5, G3/G4 y terminal del diálogo prevalecen.

**QUE LO DA POR HECHO:** conservar los seis VIVO agrupados y rechazar su implementación “sin vuelo” como cierre demostrable. La propuesta §7.4 además parte del consumidor equivocado: `tools/dayz_mcp/loopback.py:967` **escribe** pollHz=5; no lee cualquier pollHz del JSON como afirma la auditoría. El consumidor abierto es `MCPBridge.c:195`. **DEPENDENCIAS:** ninguna; no se añade lector daemon ni se desplaza ese guard a una API inventada.

### N4 — Acelerar spawn suprimiendo orden causal u observación

**QUÉ se rechaza / archivos:** permitir adelantamientos tras world_spawn, dar OK un frame después sin enumeración y hacer poll lento pese a backlog, en `addon/scripts/5_Mission/MCPBridge.c` y `addon/scripts/5_Mission/MCPClientBridge.c`.

**POR QUÉ no / COSTE:** medio. `MCPBridge.c:279` difiere el resto del lote, no todo el tiempo hasta que acabe el job. `MCPBridge.c:2897` comprueba que encuentra el subject; B1 exige observación server-side (`product-spec.md:54`). QA E-BUF-02 ya refuta la inanición por ACK de result.

**RIESGO:** una consulta puede adelantar al spawn del que depende, o reportarse éxito sin efecto observado. **QUE LO DA POR HECHO:** registrar el rechazo de ambas aceleraciones y del poll por una causa falsa; conservar E-ERR-02/E-HOT-04 delimitados. **DEPENDENCIAS:** ninguna. No rebajar B1 para que un refactor tenga un gate más sencillo.

### N5 — Microoptimizaciones y métricas Enforce sin presupuesto demostrado

**QUÉ se rechaza / archivos:** early-out adicional, ToLower de contactos, caps pre-codificadas, cache de GetGame/players, selección top-k, cache de handlers, scratch de JSONL y nuevo contador de ticks/dispatched en `addon/scripts/4_World/MCP_CarScript.c`, `addon/scripts/5_Mission/MCPBridge.c`, `addon/scripts/5_Mission/MCPClientBridge.c`.

**POR QUÉ no / COSTE:** medio como lote, aunque cada edición aislada sea pequeña; sin ahorro medido que justifique el riesgo acumulado. `MCP_CarScript.c:207` ya retorna si no hay trace activo; `MCPBridge.c:235` sí recodifica caps. Un contador no mide coste del tick ni demuestra que MAX_DISPATCH sea insuficiente. No reemplazar el algoritmo por otro que también pueda ser O(n·k).

**RIESGO:** caducidad de player/widget, datos autoritativos A1, reentrada y compilación/runtime no probados. G1 prohíbe sacrificar control sostenido por ahorrar setters. **QUE LO DA POR HECHO:** rechazo de optimizaciones sin una métrica/budget justificable; no prometer “cero coste” ni memoria relevante recuperada. **DEPENDENCIAS:** ninguna. No se propone instrumentar ni perfilar el juego en esta lane.

### N6 — Reducir trace o dejar de reaplicar held-state

**QUÉ se rechaza / archivos:** bajar el máximo público de muestras, quitar campos o mover setters fuera del tick en `addon/scripts/4_World/MCP_CarScript.c`, `addon/scripts/5_Mission/MCPClientBridge.c`, `addon/scripts/5_Mission/MCPMessages.c`; el esquema afectado sería `tools/schemas/vehicle-trace-v1.json`.

**POR QUÉ no / COSTE:** grande por contrato y consumidores, aunque un literal sea barato de editar. G3 permite 8192 muestras con campos/ruedas/ownership definidos (`product-spec.md:122`); `tools/schemas/vehicle-trace-v1.json:36` conserva ese máximo. No se midió presión de memoria.

**RIESGO:** cambio incompatible del trace o pérdida de held-state/deadman G1 (:120). **QUE LO DA POR HECHO:** registrar rechazo explícito de E-HOT-06/E-HOT-08 como recorte; no rebajar capacidad ni calidad de evidencia. **DEPENDENCIAS:** ninguna; requiere decisión contractual distinta, no es mejora implícitamente aprobada por esta auditoría.

### N7 — Borrar logs de fase y envolver todo en MCP_DEBUG

**QUÉ se rechaza / archivos:** retirar logs o condicionar globalmente Print/Log en `addon/scripts/5_Mission/MCPBridge.c`, `addon/scripts/5_Mission/MCPClientBridge.c`, `addon/scripts/5_Mission/MCPDialogController.c`, con define en `addon/scripts/5_Mission/MCPMessages.c`.

**POR QUÉ no / COSTE:** medio, por inventario de consumidores diagnósticos y macros. El log de resultado conserva ticks (`MCPBridge.c:3460`) y el de shutdown terminal tiene función concreta (`MCPClientBridge.c:4076`). No se demostró coste material; definir MCP_DEBUG incondicionalmente tampoco elimina logs en release.

**RIESGO:** perder la evidencia que distingue comando, efecto, ACK y cleanup; H13 (`product-spec.md:148`). **QUE LO DA POR HECHO:** no llamar “debug sobrante” a E-HOT-11 ni retirar señal sin sustitución aprobada. **DEPENDENCIAS:** ninguna; no se modifica packaging/macros aquí.

### N8 — “Corregir” config y layout que el triaje describe como coherentes

**QUÉ se rechaza / archivos:** cambios de limpieza en `addon/config.cpp` y `addon/gui/layouts/mcp_dialog.layout`.

**POR QUÉ no / COSTE:** pequeño, pero sin defecto identificado. `addon/config.cpp:21` declara World/Mission y sus rutas; `addon/scripts/5_Mission/MCPDialogController.c:30` fija seis fields y el layout tiene Row5 en :251. E-GUI-01/E-GUI-02 son observaciones positivas.

**RIESGO:** romper carga o límites del formulario sin beneficio; contrato de campos de `product-spec.md:389`. **QUE LO DA POR HECHO:** cerrar la propuesta de trabajo vacía, conservar el estado VIVO positivo sin inventar un fix. **DEPENDENCIAS:** ninguna. No se declara compilación ni apariencia verificadas.

### N9 — SupervisorTick, HostInventory, caches y LogFollower como refactor global

**QUÉ se rechaza / archivos:** sustituir supervisores y pollers por un tick de 1 s, introducir inventario/caché de puertos compartido o un follower por run en `tools/dayz_mcp/server.py`, `daemon.py`, `lease_supervisor.py`, `orphan_guard.py`, `process_lifecycle.py`, `control_client.py`, `native_launcher_backend.py` y `log_tail.py` del mismo directorio.

**POR QUÉ no / COSTE:** grande. QA P-TICK-01 y P-OPT-03a refutan simultaneidad universal y reescaneo por poll; `tools/dayz_mcp/process_lifecycle.py:4136` ya cachea por revisión/edad. `orphan_guard.py:378` y :471 sí lanzan netstat, pero no se midió CPU ni tasa por consumidor. Un contador en doctor, proceso distinto, no representa automáticamente spawns del daemon. §7.5 carece de ventana, dueño y agregación acreditados; no se añade otro contador local fingiendo medir el sistema.

**RIESGO:** retrasar heartbeat, bloquear cleanup/reaper, servir identidad caducada o mezclar markers/runs. H4/H9/H10 y C4 (`product-spec.md:139`, :144, :145, :68). **QUE LO DA POR HECHO:** rechazar las unificaciones y el ahorro prometido, preservando S3/P-TICK-01/P-OPT-02/P-TICK-03 como observaciones delimitadas. **DEPENDENCIAS:** ninguna; H2 sí documenta sus presupuestos. No se propone una medición del daemon vivo o de la caja en este plan.

### N10 — Colapsar autoridad, identidad, launcher y bundle en utilidades comunes

**QUÉ se rechaza / archivos:** `daemon_authority.py`, `process_identity.py`, `bundle_io.py`, reducción a dos capas y extracción de mods/paths o VPP por conteo de ficheros [DESIGN de la auditoría]. Fronteras concretas afectadas: `tools/dayz_mcp/daemon_policy.py`, `normal_daemon_policy.py`, `daemon_policy_contract.py`, `host_config.py`, `daemon_credential.py`, `accredited_daemon_transport.py`, `secure_launcher.py`, `native_launcher_transaction.py`, `native_launcher_backend.py`, `native_bundle.py`, `launcher_registry.py`, `launcher_registry_update.py`, `dayz_test_worker.py`, `native_process_guard.py`, `process_lifecycle.py`, `orphan_guard.py`, además de `tools/process-guard.ps1` y `tools/build_native_launcher.py`.

**POR QUÉ no / COSTE:** grande. Hay duplicación concreta de carga y comparadores, pero también defensas distintas: QA P-ARC-03a; handle de lectura sin compartir escritura en `tools/dayz_mcp/launcher_registry.py:181`; composición duplicada por frontera sellada en `native_launcher_transaction.py:250` y :277. Los comparadores tampoco son idénticos: `native_process_guard.py:184` compara hashes literalmente y `process_lifecycle.py:2730` normaliza case. No se ha demostrado un defecto por esa diferencia.

**RIESGO:** perder acreditación antes del primer byte, integridad de handle, distinción ancestry/health o compatibilidad de identidades. F5/H6/H9/H10/H12 (`product-spec.md:107`, :141, :144, :145, :147) prevalecen. Varios archivos figuran en `tools/build_native_launcher.py:53`: un cambio real requeriría resellado coordinado. Esta lane no lo hace ni lo encarga sobre sesiones vivas.

**QUE LO DA POR HECHO:** rechazar el refactor global y la falsa equivalencia de comprobaciones; no crear un helper de hash que sustituya pinning. **DEPENDENCIAS:** ninguna. Los arreglos recientes de procedencia no autorizan recortar esa frontera ni acreditan el bundle desplegado.

### N11 — Unificar vistas de sesión, cleanup y retry sin preservar sus semánticas

**QUÉ se rechaza / archivos:** SessionClient único, assert_idle común, decorador general de retry/jitter, `timeouts.py` con overrides; afectaría `tools/dayz_mcp/server.py`, `control_client.py`, `lease_supervisor.py`, `daemon_credential.py`, `steam_preflight.py` y `process_lifecycle.py`.

**POR QUÉ no / COSTE:** grande. `tools/dayz_mcp/lease_supervisor.py:152` comprueba un terminal sencillo; `control_client.py:589` exige además recuperación sin audit_fault y otros estados. `daemon_credential.py:213` usa el deadline original y :234 rechaza segundo 401. Fachadas y valores iguales no implican mismo contrato.

**RIESGO:** replay adicional, nuevo daemon por auth fallida, lease oculto o promoción indebida. H9/H12 (`product-spec.md:144`, :147) lo limitan expresamente. **QUE LO DA POR HECHO:** rechazar el retry genérico y la reducción de garantías a un solo assert_idle. No confundir P-OPT-04 con ausencia de retry acotado. **DEPENDENCIAS:** ninguna; la variante documental de timeouts está en H2, no necesita este refactor.

### N12 — Fusionar esquema, suites y checkers eliminando oráculos independientes

**QUÉ se rechaza / archivos:** fusionar `tools/dayz_mcp/effective_schema.py`, `effective_schema_core.py`, `effective_schema_catalog.py`, `effective_schema_runtime_validators.py`; unificar o eliminar `tools/tests/test_retail_quarantine.py`, `test_loopback.py`, `test_process_lifecycle.py`, `test_server_response_truth.py`, `test_launcher_registry_update.py`, `test_secure_launcher.py`, `test_registry_lock.py`, `test_native_bundle.py`, `test_dependency_lock.py`, `test_task9_launcher_migration.py`, `test_daemon_contract.py`, `test_docs_truth.py`; convertir `tools/checks/check_native_launcher_registry.py` en oráculo único. Tampoco se fusionan schema/curso/adversarios de trace ni se elimina `tools/tests/test_vehicle_prepare_fixture.py`.

**POR QUÉ no / COSTE:** grande. `tools/dayz_mcp/effective_schema_catalog.py:1` es catálogo independiente, mientras `effective_schema.py:29` obtiene el esquema de la app registrada. `tools/tests/test_retail_quarantine.py:136` discrimina lectura/mutación; `test_task7_final_lifecycle_regressions.py:19` rechaza antes del launcher. `test_registry_lock.py:46` verifica convivencia y exclusión; `test_launcher_registry_update.py:1001` prueba al checker con fixtures. No son cuatro maneras redundantes de comprobar la misma cosa.

**RIESGO:** expected derivado del observado, desaparición de negativos HTTP/lifecycle/locking y pérdida de H3/H5/H6/H10/E5. QA T-CON-02 muestra el error de unir fuentes heterogéneas. T-ARC-01/02 fueron FALSO y la delegación docs→checker ya existía (`tools/tests/test_docs_truth.py:335`): no hay ese arreglo pendiente.

**QUE LO DA POR HECHO:** rechazar eliminación/fusión por conteo; mantener consumidores y tests de checkers. **DEPENDENCIAS:** ninguna; D3 puede indexarlos sin fusionarlos y D4 puede acreditar un control sin sustituir todos los demás.

### N13 — GateHarness único y un solo H8

**QUÉ se rechaza / archivos:** mover/fusionar `tools/g0_abba_gate.py`, `tools/g0_site_gate.py`, `tools/gate4a_mcp_client.py`, `tools/tramoA_verbs_gate.py`, `tools/tramoB_getin_gate.py`, `tools/_session_coordination/h8_distributed_codex_gate.py`, `tools/_session_coordination/h8_real_codex_gate.py`, con un GateHarness compartido [DESIGN].

**POR QUÉ no / COSTE:** grande para acreditar equivalencia, aunque mover archivos sea fácil. `tools/_session_coordination/h8_distributed_codex_gate.py:22` ya comparte helpers; :104 valida roster/procedencia externa. `h8_real_codex_gate.py:1` describe cuatro proxies desde otro modelo de ejecución. Un wrapper único no demuestra las dos procedencias.

**RIESGO:** confundir una simulación de concurrencia con H8/H9 externos (`product-spec.md:143`, :144), compartir oráculo con el producto, romper paths de gates. **QUE LO DA POR HECHO:** rechazo de la fusión/movida como refactor con ahorro demostrado. **DEPENDENCIAS:** ninguna; D3 puede describir diferencias. No se propone correr gates in-game ni lanzar agentes para acreditarlos.

### N14 — Recortar E2E, imponer límites uniformes o un linter global de golpe

**QUÉ se rechaza / archivos:** convertir E2E en _FakeRuntime, ocultar Popen/venv/stdio tras DAYZ_MCP_LIVE, techo unit de 2 s, mover checks fuera de ejecución obligatoria, partir god-files por longitud o externalizar blobs sin dolor localizado. Ficheros de referencia: `tools/tests/test_client_credential_rotation_e2e.py`, `test_session_e2e.py`, `test_bug046_startup_deadlock.py`, `test_parent_watchdog.py`, `test_sources_are_statically_analysable.py`, `test_command_validation_coverage.py`, `test_dependency_lock.py`, `test_tool_registry_fingerprint.py`, `test_box_occupancy.py`, `test_process_lifecycle.py` y `test_client_mode.py`.

**POR QUÉ no / COSTE:** grande en conjunto. H12 pide E2E multiproceso y `tools/tests/test_client_credential_rotation_e2e.py:557` lo ejerce; `tools/tests/test_session_e2e.py:75` usa peer falso, no DayZ. El venv de `tools/tests/test_bug046_startup_deadlock.py:1385` discrimina una referencia de host independiente, no un simple objeto simulado. 68 imports son acoplamiento, no 68 fallos ni una medida de tiempo. Externalizar un literal es posible, pero el coste de moverlo no está justificado por frecuencia de cambios medida.

**RIESGO:** suite verde porque ya no corre el consumidor, expectativas generadas desde producto, falso rojo en máquinas lentas y pérdida de cobertura. **QUE LO DA POR HECHO:** rechazar el 30%, los timeouts globales y el veto inmediato a todo import tests.test_ (§7.7). Mantener los tiempos reales separados de excepciones TimeoutExpired simuladas. **DEPENDENCIAS:** D1/D2 ofrecen extracciones acotadas; un linter global no queda habilitado por ellas porque quedan otras dependencias. D3 permite documentar categorías sin omitir tests.

### N15 — Borrar backups suponiendo que sus bytes están versionados

**QUÉ se rechaza / archivos:** borrado de los 26 archivos concretos enumerados en `inventory.json`, clave `backups`, todos relativos a `tools/tests/`; traslado masivo de logs/artefactos sin inventario y dueño.

**POR QUÉ no / COSTE:** pequeño para borrar, desconocido para recuperar datos perdidos. `git ls-files 'tools/tests/*.bak*'` dio salida vacía; no demuestra que sus bytes estén en Git. El inventario de esta lane conserva los 26 nombres exactos. No se ha demostrado que participen en el análisis de *.py.

**RIESGO:** pérdida de cambios ajenos y evidencia histórica; ningún criterio de producto pide esa pérdida. **QUE LO DA POR HECHO:** registrar rechazo del borrado por falsa premisa T-CON-04b, sin archivar ni mover datos de otra sesión. **DEPENDENCIAS:** ninguna. Una política de archivo/retención con dueño sería otro alcance, no una tarea añadida aquí.

### N16 — Cap arbitrario y allowlist adicional de main_fn para breakglass

**QUÉ se rechaza / archivos:** fijar expr<4096 o una allowlist nueva de main_fn en `tools/dayz_mcp/loopback.py` y `addon/scripts/5_Mission/MCPBridge.c` por la premisa de “REST arbitrario sin protección”.

**POR QUÉ no / COSTE:** medio; cambiaría política y casos ya autorizados sin un caso de abuso o compatibilidad delimitado. `tools/dayz_mcp/loopback.py:1868` exige allowlist/audit y :1870 expresión exacta; E2 y fuera de alcance definen breakglass auditado (`product-spec.md:87`, :174). Es real que main_fn se trata como string y no hay el cap específico de 4096; no equivale a ejecución pública irrestricta.

**RIESGO:** negar una expresión ya autorizada, romper tooling o transmitir falsa garantía de ejecución funcional headless (:178). **QUE LO DA POR HECHO:** rechazar ese umbral/allowlist sin política motivada; mantener E-P05b como hecho delimitado. **DEPENDENCIAS:** ninguna. La ausencia de un cap particular no se “arregla” escogiendo uno al azar.

### Trazabilidad completa de los 60 VIVO

Cada entrada tiene un destino principal. Las variantes o dependencias secundarias se explican en la unidad; no se contabilizan dos veces. H1 es un hallazgo documental complementario; D4 nace de propuestas y NO VERIFICABLE, por eso no tienen una entrada VIVO principal.

| Unidad | Entradas VIVO del triaje |
|---|---|
| H2 | P-OPT-05 |
| D1 | T-DUP-05 |
| D2 | T-DUP-04 |
| D3 | T-ARC-05, T-CON-01 |
| N1 | E-TICK-03, E-DUP-01, E-DUP-02, E-DUP-03, E-DUP-04 |
| N2 | E-TICK-04, E-DUP-05, E-HOT-09, E-P06 |
| N3 | E-ERR-04, E-BUF-01, E-ERR-05, E-ERR-01, E-ERR-03, E-BUF-03a |
| N4 | E-ERR-02, E-HOT-04 |
| N5 | E-TICK-02, E-HOT-01, E-HOT-02, E-HOT-03, E-HOT-05, E-HOT-07, E-HOT-10, E-BUF-04 |
| N6 | E-HOT-06, E-HOT-08 |
| N7 | E-HOT-11 |
| N8 | E-GUI-01, E-GUI-02 |
| N9 | S3, P-TICK-01, P-OPT-02, P-TICK-03 |
| N10 | P-ARC-02a, P-ARC-03a, P-ARC-05, P-ARC-06, P-TICK-02, P-TICK-05a |
| N11 | P-OPT-04, P-TICK-04 |
| N12 | P-ARC-04, T-ARC-04, T-DUP-01, T-DUP-02 |
| N13 | P-ARC-07, T-DUP-03 |
| N14 | T-OPT-01, T-OPT-02, T-OPT-03a, T-CON-03, T-DUP-06 |
| N15 | T-CON-04a |
| N16 | E-P05b |

**Cobertura: 60/60, sin duplicados ni omisiones.** MOVIDO no equivale a arreglado: P-OPT-01 queda con N9, P-ARC-01 con N11 y E-P04 se conserva como hipotesis de respawn en fuente cliente (no se propone trabajo de motor). Los 2 YA ARREGLADO y los 19 FALSO no entran como fixes. Los 19 NO VERIFICABLE no se promueven a defectos.

### Juicio exhaustivo de las propuestas de las secciones 6 y 7

Se normalizan 40 componentes para que ninguna frase del diagrama/pasos quede sin destino: 16 de 6.1, 10 de 6.2, seis movimientos de 6.3 y ocho extras de 7. El titulo de 6.3 dice ocho movimientos, pero su parrafo enumera seis. La tabla no inventa 40 defectos ni 40 encargos. Cada coste, riesgo, contrato, criterio y dependencia esta en la unidad de destino.

| Componente | Propuesta original | Unidad / cajon | Juicio |
|---|---|---|---|
| 6.1-01 | TickHub + BridgeBase + HttpUtil/callbacks/TryInit/finite/Shutdown | N1 | Rechazar la unificación; guards concretos se juzgan además en N3. |
| 6.1-02 | DriveProbe + WorldQuery | N1 | Rechazar la equivalencia de actores/selección. |
| 6.1-03 | Servidor adopta MCPJobRunner | N1 | Sin evidencia de equivalencia de orden/actor. |
| 6.1-04 | Diálogo pierde su segundo reloj | N2 | No quitar un deadline deliberado. |
| 6.1-05 | Clamp pollHz | N3 | Cambio de config Enforce no cerrable aquí. |
| 6.1-06 | duplicate_id | N3 | Insert nativo y alcanzabilidad sin demostrar. |
| 6.1-07 | unknown_job_kind fail-fast | N3 | Kind interno, caso público no probado. |
| 6.1-08 | Log result dropped | N3 | Rama real; no inventar un cierre del lifecycle. |
| 6.1-09 | Poll lento con backlog | N4 | Mecanismo de inanición refutado. |
| 6.1-10 | Early-out OnInput | N5 | Capture ya retorna en frío; beneficio sin medir. |
| 6.1-11 | ToLower en contactos | N5 | Coste/semántica no acreditados. |
| 6.1-12 | Caps pre-codificadas | N5 | Microoptimización sin presupuesto. |
| 6.1-13 | GetGame local | N5 | No se ha medido impacto. |
| 6.1-14 | Borrar cuatro logs de spawn | N7 | Señal diagnóstica sin sustitución. |
| 6.1-15 | Borrar pool de 64, usar scratch | N5 | Mejora posible sin presión demostrada. |
| 6.1-16 | InputLock con refcount del diagrama | N2 | No equivalencia de input/focus/restore. |
| 6.2-01 | SupervisorTick 1 s y waiters suscritos | N9 | Budgets/actores y fallos no intercambiables. |
| 6.2-02 | HostInventory + PortSnapshotCache | N9 | Ya hay caché; no usar observación para autorizar. |
| 6.2-03 | LogFollower por run | N9 | No hay reescaneo completo por poll. |
| 6.2-04 | SessionClient + vistas/idle único | N11 | Conservar lazy-spawn y recuperación estricta. |
| 6.2-05 | ProcessAuthority/identidad/PS1 delega | N10 | Políticas de reclaim e identidad no equivalentes. |
| 6.2-06 | Colapsar launcher/DaemonAuthority, separar VPP | N10 | Rechazado por fronteras H9/H10/bundle. |
| 6.2-07 | bundle_io y CLI fina | N10 | Hash genérico no sustituye handles/pinning. |
| 6.2-08 | retry.py genérico | N11 | H12 limita retries y deadline. |
| 6.2-09 | timeouts.py y overrides | N11, H2 | Código rechazado; documentación aceptable. |
| 6.2-10 | GateHarness + mover gates/H8 | N13 | Proveniencia y paths sin equivalencia probada. |
| 6.3-01 | helpers wait_for/authority/client_runtime y prohibición global | D1, D2, N14 | Decidir extracciones acotadas; veto global rechazado. |
| 6.3-02 | Fusionar quarantine/registry/docs | N12 | No suprimir fronteras; docs ya delega. |
| 6.3-03 | Harness de contratos único y una fuente trace | N12, D3 | Fusionar rechazado; índice documental es decisión separada. |
| 6.3-04 | slow y retirar Popen/venv/stdio del unit | N14, D3 | No quitar E2E obligatorio; documentar efectos si se elige. |
| 6.3-05 | Borrar backups/mover logs | N15 | Bytes no acreditados como versionados. |
| 6.3-06 | mutation_gate por contrato | D4 | Recomendado uso puntual; alternativa piloto aislado, no CI automático. |
| 7-01 | Métrica Enforce cada 60 s | N5 | Un contador no mide coste ni capacidad por sí solo. |
| 7-02 | depth<=32 en walks | N2 | Truncamiento no puede fingir unicidad. |
| 7-03 | MCP_DEBUG global | N7 | Macro incondicional no define release y puede ocultar señal. |
| 7-04 | Validar pollHz al leer JSON en daemon | N3 | La ruta abierta escribe 5; consumidor real es el bridge. |
| 7-05 | netstat_spawns_total en doctor | N9 | Proceso, ventana y agregación no definidos. |
| 7-06 | DAYZ_MCP_LIVE para todo Popen/venv/stdio | N14, D3 | Confunde integración offline con juego. |
| 7-07 | Linter no-test-imports-test | N14, D1, D2 | No activarlo sobre 68 referencias; extracciones parciales primero si se eligen. |
| 7-08 | Tabla de TTL en QUICKSTART | H2 | Tabla por propósito en README técnico y enlace desde Quickstart. |

### Secuencia y objeciones que debe intentar el revisor

Primero resolver H1/H2 y las cuatro elecciones D por separado. Si se acepta H2, actualizar sus documentos antes de la ultima regeneracion/verificacion de H1. D1 y D2 son independientes por dominio; D3 documenta y D4 no cambia CI. No existe dependencia que obligue a aceptar una unidad N para completar una H/D.

Grok puede refutar la prioridad mostrando que H1 ya esta regenerado en el arbol receptor, que H2 duplica una tabla existente, que D1/D2 cambian oraculos o no cierran su dependencia declarada, o que una unidad N tiene un fallo concreto y un arreglo verificable offline omitido aqui. El motivo mas debil es el coste de mantenimiento de D1/D2: se infiere de acoplamiento, sin incidencia reproducida. Por eso son DECIDIR. La restriccion de no proponer juego/caja explica N3, pero no borra sus riesgos de callbacks/Shutdown. Ninguna nota de la auditoria funciona como presupuesto de rendimiento ni ninguna puntuacion como prioridad mecanica.
