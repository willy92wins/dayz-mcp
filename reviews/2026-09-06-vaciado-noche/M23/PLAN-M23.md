# PLAN-M23 — esquema efectivo como autoridad y señal de registro desactualizado

## 3.1 Resumen ejecutivo

1. Implementar una autoridad de contrato basada en la publicación completa de FastMCP después del registro, con gate obligatorio del repositorio; traza DPF E5 (V/product-spec.md:90).
2. Completar el instrumento ya aterrizado: el árbol contiene effective_schema.py, pero su salida actual es una proyección (V/tools/dayz_mcp/effective_schema.py:29,40,57).
3. Hacer bloqueantes las contradicciones entre promesas de entrada formalizadas, esquema publicado y validación Python observable; conservar explícita la cobertura no decidible.
4. Añadir una comparación viva de autoridad a la captura congelada del proceso MCP; actualmente se congela incluso la comparación contra una autoridad ausente (V/tools/dayz_mcp/server.py:589,3042,4923).
5. Publicar mediante CAS un hash reproducible, independiente de PID/tiempo, y conservar el historial durable mediante eventos inmutables más una cabecera atómica.
6. Señalar por separado cambio del registro, cambio de generación del daemon y cambio de bytes del PBO desplegado; la generación existente se conserva (V/tools/dayz_mcp/daemon.py:650).
7. No implementar recarga de tools en sesiones abiertas, cambios Enforce, acreditación de efectos del puente ni rediseño de coordinación/lifecycle.
8. Riesgo principal 1: un gate de proyección o fixtures reconocibles puede aprobar un snapshot inútil; resolver con publicación íntegra y calibración generada.
9. Riesgo principal 2: confundir ausencia, concurrencia o journal incompleto con fresh; resolver con estados desconocidos, CAS y un único punto de commit.
10. Riesgo principal 3: romper clientes o afirmar que PBO desplegado equivale a PBO cargado; conservar contratos anteriores y declarar esa frontera.

## 3.2 Inventario del antecedente

### Base, método y convención de citas

Fecha de autoría: 2026-09-06. Consumidores: implementador asignado por el orquestador, suite de repositorio y procesos MCP que consultan estado. Este documento es autoría de plan; no autoriza ni acredita implementación o despliegue realizados en esta sesión.

Decisión no interactiva: el encargo es la especificación y autoriza decidir el diseño. Las preguntas de §3.7 son decisiones documentadas para una revisión posterior, no bloqueos. Descubrimiento completado antes de redactar: fichas completas; lectura de S7, v4/v8 y mecanismos vivos; sondas puras; cinco módulos unitarios aislados. Se aplicaron las skills de planificación y gates; sus ceremonias de aprobación, ubicación por defecto y oferta de ejecución quedan desplazadas por el encargo. No se ejecutó la suite global ni se invocó ninguna tool del daemon/juego.

| Alias | Ruta absoluta / identidad de fuente |
|---|---|
| F | Directorio de trabajo del encargo, FICHAS-M23.txt; ficha d366 comienza en :1, 141e en :50 y 9b7b en :102 |
| S7 | C:/Users/guill/ObsidianVault/AI/10_Projects/DayZ_MCP/reviews/2026-08-29-s7-instrumento-esquema-efectivo |
| V4 | C:/tmp/mcp-s7-gate2, rama work/effective-schema-gate-v4, commit 8732ee3 |
| V8 | C:/tmp/mcp-s7-gate6, rama work/effective-schema-gate-v8, commit 6b7759e |
| V | C:/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev, **commit fijado 8ff937e0a85ab2a638d353978b8c57aa52952135**; citas contra sus bytes, conservados en ./evidence/live-head/ |
| SDK | V/tools/.venv-mcp/Lib/site-packages; dependencia instalada leída localmente, no una API recordada |
| GL | C:/Users/guill/.claude/skills/gates-ledger/SKILL.md |

Una cita V/ruta:línea significa ese fichero en el commit fijado, no HEAD móvil. evidence/manifest-live-head.json registra SHA-256 y tamaño de 291 archivos copiados mediante lecturas git. Los directorios native-launchers no se copiaron. Los tres worktrees y el vault permanecieron de solo lectura para esta autoría.

**Deriva externa observada.** Al entrar, HEAD era 8ff937e. Durante la lectura, server.py cambió 23 líneas añadidas/4 retiradas y result_prune.py cambió también; después HEAD avanzó a 0873d534c9233872fc6256155f04c9fd198df536. Se conserva el delta en COMMANDS-ROOT.txt/COMMANDS-RUNTIME.txt. Antes de implementar, rebasar las ubicaciones de inserción y repetir L0 sobre el commit elegido. No trasladar automáticamente números de línea. Esto no altera el alcance M23.

**Resultados realmente ejecutados.** Con el intérprete V/tools/.venv-mcp/Scripts/python.exe, fuentes fijadas en cwd/evidence/live-head/tools, PYTHONPATH explícito, bytecode desactivado y timeout 50 s: 54 tests, cero failures/errors. Módulos: test_effective_schema, test_effective_schema_core, test_effective_schema_catalog, test_effective_schema_runtime_validators y test_tool_registry_fingerprint. La sonda observó 60 tools standard y 61 exec_enforce, para claude y codex; el alias from está publicado. Estas cuentas son mediciones de cobertura, nunca el oráculo de identidad. El auditor heredado devolvió ocho PARAM-NAME-DIVERGENCE y un DESC-ENUM-MISMATCH. Comando reproducible de esta medición: python -B ./probe_baseline.py; stdout y stderr literales en COMMANDS-ROOT.txt. El primer intento quedó SETUP-FAILED por bloquear el socketpair privado de asyncio; se corrigió ese guard del arnés, sin modificar producción, y se repitió.

### Inventario y decisión de reutilización

[EXACT] Las firmas que se reproducen en esta tabla existen en las fuentes citadas. “Reutilizar” no acredita que el fichero completo sea una autoridad válida.

| Fuente / pieza | Tratamiento | Motivo y cita |
|---|---|---|
| S7: resolve_effective_schemas() y async _resolve_async() | Adaptar el punto de observación | Leen list_tools después de build_app, pero construyen otra app con defaults (S7/effective_schema-r2.py:28,33). La autoridad debe observar la app/config que se publica. |
| S7: códigos PARAM-NAME-DIVERGENCE y DESC-ENUM-MISMATCH | Reutilizar literalmente para diagnóstico legacy | Son identificadores existentes; no obligan a conservar sus heurísticas como criterio bloqueante (S7/effective_schema-r2.py:20). |
| S7: alias from, required y unión de marker | Reutilizar los casos; adaptar arnés | Discriminan registro frente a cuerpo, pero no prueban totalidad del registro (S7/test_effective_schema-r2.py:11,111). |
| S7: params con required/default/type/enum | Descartar como material autoritativo | Omite restricciones, nulabilidad y diferencia default ausente/null (S7/effective_schema-r2.py:50,61,79). |
| S7: gate-v2.py | Descartar como puerta de promoción | Fixtures literales y comparación parcial; bypass de snapshot documentado en la revisión posterior (S7/gate-v2.py:76,205; S7/REVIEW-CODEX-r2.md:21; S7/README.md:61). |
| S7: A-03 y coincidencia global type/classname | Descartar como incumplimiento automático | Puede denunciar conceptos distintos por compartir nombres; el propio test exige ese resultado (S7/test_effective_schema-r2.py:152,164). |
| V4: randomización y controles | Adaptar la idea, no el contrato reducido | El generador fuerza type/enum/default en cada parámetro y el oráculo los indexa; faltan formas del consumidor real (V4/_gate.py:92,139). |
| V4: contar llamadas a build_app | Descartar como aceptación | Cuenta ejecución, no la veracidad de lo publicado (V4/_gate.py:670). |
| V8: commit 139c9f44cc94c4dd613cc291221780c31fd792bb | Reutilizar literalmente | Ordena sorted(synonym_set), estabilizando el texto evidence; **no añade hash, CAS ni journal** (V8/tools/dayz_mcp/effective_schema.py:197; diff de ese commit). Ya está en V/tools/dayz_mcp/effective_schema.py:197. |
| V8: fixes de uniones 7f58aa8 y 7fe4cb1 | Conservar como vista diagnóstica | Recursión y enums no hashables mejoran el inspector, pero siguen eliminando null y deduplicando con igualdad Python (V8/tools/dayz_mcp/effective_schema.py:78,88,122,128,271). |
| V8: generadores de dos familias | Adaptar | Generan nombres/valores en cada corrida; ampliar al JSON publicado entero, perfiles, defaults ausentes, metadatos y restricciones anidadas (V8/_gate.py:278,307,521,1065). |
| V8: _first_difference(expected, actual, path="$") | Reutilizar algoritmo literal con JSON completo | Compara tipos, claves y listas recursivamente; el defecto está en el DTO que recibe (V8/_gate.py:637). |
| V8: _expected_registry / _schema_type / _schema_enum | Descartar como oráculo | Proyectan y conservan sólo la primera rama enum; quedaron anteriores al fix de uniones del instrumento (V8/_gate.py:458,479,495). |
| V8: _validate_findings | Adaptar | Valida estructura y triples; falta comprobar bytes reproducibles del reporte y cobertura real (V8/_gate.py:660,1052). |
| V8: banco de mutantes | Reutilizar defectos, adaptar interfaces | Snapshot/dispatch, tipos muertos y auditor parcial son discriminadores útiles (V8/_bench/must_reject/c3_snapshot_dispatch.py:11,35; V8/_bench/must_reject/H3_dead_types.py:39; V8/_bench/must_reject/N1_incomplete_auditor.py:98). |
| V8: worker aislado, IPC y ataques .pth | Descartar de la suite M23 | Son mecanismos de juez de delegación; el banco incluso altera site-packages (V8/_gate.py:875; V8/_bench/run_bench.py:128). M23 es un gate del repositorio. |
| Tests libres pytest de V8 | No copiar literalmente | unittest recoge cero casos; V8 tiene funciones libres (V8/tools/tests/test_effective_schema.py:16,175). El árbol V ya las convirtió a unittest.TestCase (V/tools/tests/test_effective_schema.py:18). |
| V: test_live_audit_flags_type_classname_divergence | Convertir en fixture sintética | Hoy exige que el árbol siga produciendo hallazgos, no que el contrato cumpla (V/tools/tests/test_effective_schema.py:37). |
| V: effective_schema_core | Adaptar sin romper envoltorio legacy | Acepta registros finalizados, conserva input_schema, pero el registro proyectado omite otros campos MCP y la serialización no rechaza NaN explícitamente (V/tools/dayz_mcp/effective_schema_core.py:102,129,211). |
| V: effective_schema_runtime_validators | Reutilizar enumeración como apoyo, no como validación | Enumera IDs por propiedad/adaptador; no ejecuta el validador ni infiere su semántica (V/tools/dayz_mcp/effective_schema_runtime_validators.py:31,53,79). |
| V: effective_schema_catalog | Conservar legacy; no declararlo exhaustivo | Incluye seat_index/expected_type, que la firma fijada de vehicle_get_in_client no publica (V/tools/dayz_mcp/effective_schema_catalog.py:53,60; V/tools/dayz_mcp/server.py:4406). No inventar esos parámetros como parte de M23. |
| V: RegistrySnapshot y huella M14 | Conservar lector/comparador legacy | La huella cubre una proyección de cinco campos, no instructions/outputSchema/_meta; además normaliza NFC (V/tools/dayz_mcp/tool_registry_fingerprint.py:24,269,358,379). |
| V: authority v5 | Preservar lectura legacy; no fabricar sus sidecars | Exige cinco blobs y artifact_version=5; el promotor nombrado entre producers no existe en este árbol (V/tools/dayz_mcp/tool_registry_fingerprint.py:169,206,647,670). |
| V: overlay de bridge_status | Sustituir comparación congelada por lectura actual | Captura al cerrar build_app, pero pasa cinco None al lector y congela source_stale junto con el fingerprint (V/tools/dayz_mcp/server.py:589,591,3042,4393,4923). |
| V: atomic_write_bytes(path, payload) y lock de registro | Reutilizar con adaptación acotada | Sustitución UTF-8 atómica y lock interproceso fail-fast existen; el wrapper no expone expected_sha256 y el lock exige fichero preexistente (V/tools/dayz_mcp/runtime_state.py:1890,1957; V/tools/dayz_mcp/registry_lock.py:80,87,100). |
| V: JsonlAuditWriter | Descartar para journal concurrente | Su lock es de threading y reescribe el archivo completo; no serializa dos procesos (V/tools/dayz_mcp/runtime_state.py:244,254,339). |
| V: generación y daemon_modules.stale | Conservar | La generación es UUID; stale compara mtimes del cierre cargado y no acredita igualdad de contrato/PBO (V/tools/dayz_mcp/daemon.py:333,609,630,661). |

### Contradicciones resueltas

1. “Construido, no aterrizado” describe S7, no V: V contiene instrumento, tests unittest y plumbing M13/M14. Prevalece el código posterior (S7/README.md:3; V/tools/dayz_mcp/effective_schema.py:29; V/tools/tests/test_effective_schema.py:18).
2. “Registro completo” del informe v8 significa comparación profunda de una proyección, no del JSON MCP completo. Prevalece el fichero y la sonda: items, minimum, nulabilidad y default ausente/null cambian el raw sin cambiar la proyección (V8/_REPORT-GATE-V8.md:281; V8/_gate.py:495; COMMANDS-BRANCHES.txt).
3. Los informes dicen diez tests y ausencia de tests permanentes del fix; el commit 7fe4cb1 añade cuatro después. Prevalecen ese commit y V8/tools/tests/test_effective_schema.py:175, no V8/_REPORT-FIX-UNION.md:338,396.
4. PROCEDENCIA afirma PYTHONHASHSEED=0, pero el worker usa -I y lo ignora. Lo que oculta el texto variable es comparar sólo triples (V8/_bench/PROCEDENCIA.md:118; V8/_gate.py:810,936,1052). La sonda lo reprodujo con procesos separados; no heredar esa explicación.
5. El briefing menciona remove_tool por perfil; en V/server.py fijado no hay llamadas remove_tool. Hay registro condicional de exec_enforce y patches finales. No inventar remociones existentes; probar una remoción sintética y respetar las que introduzca el commit de integración (V/tools/dayz_mcp/server.py:4384,4918; SDK/mcp/server/fastmcp/server.py:435).
6. La cuenta 58 de CLAUDE.md es anterior a la medida aislada 60/61; no se usa ninguna cuenta histórica como expected del gate (V/CLAUDE.md:14, documento de trabajo leído; COMMANDS-ROOT.txt).

## 3.3 Diseño por pieza (a)(b)(c)

### Decisiones y límites comunes

**[DESIGN] Contrato normativo propuesto.** Todas las interfaces y campos nuevos de esta sección son diseño que debe implementar la lane asignada; no se presentan como APIs ya existentes. Las citas adyacentes acreditan los puntos de integración actuales.

- Una autoridad activa por instalación, con variantes por configuración pública; clientes sólo leen. Abrir una sesión vieja nunca publica su snapshot como nueva autoridad.
- Registro efectivo, generación daemon y PBO son tres identidades independientes. Ninguna sustituye a otra.
- La autoridad de contrato decide qué se publica y qué se promete en Python. No valida altura de spawn, targeting, efectos físicos, comandos aceptados por Enforce ni bytes cargados por el juego.
- “Decidible en Python” exige observación del registro y de la validación Python; no significa comprender automáticamente toda prosa inglesa. Las promesas mecánicas usarán una forma definida; lo demás se publica como cobertura no verificada.
- No renombrar type a classname por coexistencia. Conservar argumentos y llamadas que funcionaban; corregir las descripciones/claims a su nombre público actual.
- No transformar las huellas legacy M14 en huellas completas manteniendo implícito el algoritmo. Dos versiones de huella distintas nunca se comparan como si fueran iguales.

Traza de todos los lotes: E5 para autoridad, señal y mutantes; E3 para distribución; E4 para exclusión entre escritores; E6 como apoyo al publish recuperable. E5 contiene explícitamente fingerprint y reopen_mcp_client (V/product-spec.md:86,88,89,90,91). El formato nuevo de journal es requisito explícito del encargo, no una ampliación incidental.

### (a) Resolución completa, promesas y gate del repositorio

**[EXACT] Consumidor observado.** FastMCP.list_tools(self) devuelve MCPTool y publica name/title/description/inputSchema/outputSchema/annotations/icons/_meta; Tool admite campos extra. La serialización del SDK usa by_alias=True, mode="json", exclude_none=True (SDK/mcp/server/fastmcp/server.py:315; SDK/mcp/types.py:1315,1339; SDK/mcp/shared/session.py:346).

**[DESIGN] API nueva en effective_schema.py:**

    async def resolve_effective_registry(app, *, profile, role, public_config) -> dict:
        # Recibe la app terminada. No reconstruye otra, no entra en lifespan,
        # no llama tools ni consulta daemon/juego.
        # Retorna el documento M23 definido debajo.

Mantener resolve_effective_schemas() y audit_contracts(schemas=None) como interfaz legacy de inspección durante M23. Mover el import de server al interior de su wrapper legacy si hace falta evitar ciclos; actualmente está a nivel de módulo (V/tools/dayz_mcp/effective_schema.py:12). El camino nuevo nunca invoca asyncio.run dentro de un handler async.

**[DESIGN] Documento de contrato, tipos exactos:**

    {
      "format_version": 1,
      "canonicalization": "dayz-mcp-effective-v1",
      "profile": "standard",
      "role": "claude",
      "public_config": {"session_ttl_s": 120.0},
      "instructions": "texto publicado, o null si el SDK publica ausencia",
      "tools": [
        {
          "name": "nombre",
          "description": "texto publicado",
          "inputSchema": {"type": "object", "properties": {}},
          "outputSchema": {"type": "object"}
        }
      ]
    }

profile: string, standard o exec_enforce. role: string, claude/codex/unknown. public_config: objeto JSON de configuración que afecta a la publicación, inicialmente session_ttl_s:number finito positivo. instructions:string|null. tools:array no vacío de **objetos MCP íntegros**, no limitado a las claves ilustradas. El ejemplo muestra un elemento mínimo habitual, no un whitelist. Cualquier campo publicado adicional se conserva y se compara.

El rol unknown es una variante explícita, no un alias silencioso de claude: el CLI ya permite unknown y proyecta grok a unknown, mientras el overlay actual cae a standard/claude (V/tools/dayz_mcp/server_cli.py:8,61; V/tools/dayz_mcp/server.py:574,578). El promotor prueba las seis combinaciones perfil×rol con TTL 120; configuraciones públicas diferentes deben declararse en el manifest de esa instalación. Si no existe variante compatible, state=unknown/config_variant_missing. No incluir rutas, claves, etiquetas de tarea, tiempos ni session_id en public_config.

**[DESIGN] Canonicalización nueva en effective_schema_core.py:**

    def canonical_effective_registry_bytes(registry: dict) -> bytes:
        # Validar JSON finito, unicidad de nombres y objeto no vacío.
        # Ordenar tools por name y claves de objetos; preservar el resto.
    def effective_registry_sha256(registry: dict) -> str:
        # SHA-256 hexadecimal minúsculo de los bytes canónicos anteriores.

Reglas: UTF-8 sin BOM; sin espacios no significativos; claves ordenadas; listas interiores conservan orden; preservar string, bool, int, float, null y presencia de claves. No colapsar anyOf/oneOf, no resolver $ref para hashear, no quitar defaults null, no aplicar NFC a nombres/valores, no confundir false con 0 ni true con 1. Las claves JSON duplicadas se rechazan al leer documentos; NaN/infinito/ciclos/entrada demasiado profunda son error, no salida parcial. Límite propuesto: profundidad 128, contrato por variante 8 MiB, autoridad agregada 64 MiB; exceso da SETUP-FAILED y exige ajustar el límite declarado, jamás truncar.

El hash cubre toda la publicación y las instrucciones, no el texto de findings, el journal, la generación ni el PBO. Los metadatos volátiles quedan fuera de la descripción y del registro por construcción de su productor, no mediante un filtro silencioso del exportador. Si una tool publica información volátil, el test de reproducibilidad debe fallar.

**[DESIGN] Promesas mecanizables, sin oráculo tautológico.** Crear effective_schema_contracts.py con declaraciones independientes de la lectura de app:

    {
      "contract_version": 1,
      "tool": "engine_set",
      "claims": [
        {"param": "mode", "predicate": "enum", "value": ["start", "stop"]}
      ],
      "runtime_cases": [
        {"id": "engine_start", "args": {"mode": "start"}, "expect": "accepted"},
        {"id": "engine_invalid", "args": {"mode": "invalid"}, "expect": "rejected"}
      ],
      "effect_verification": "in_game_required"
    }

Este es diseño sobre un validador existente que rechaza modos distintos de start/stop antes de call_bridge (V/tools/dayz_mcp/server.py:4420,4421,4424). Los nombres/casos restantes se obtienen leyendo cada validador real al implementar; no generarlos del esquema que se pretende probar.

Predicados iniciales cerrados: present, absent, required, type, enum, const, default_present, default, minimum, maximum, minItems, maxItems, minLength, maxLength y additionalProperties. Para rutas anidadas, selector JSON Pointer relativo a inputSchema. Comparación tipada de JSON, con error ante predicado desconocido. Las restricciones combinadas que no quepan se prueban por pares aceptado/rechazado del wrapper Python y se identifican en cobertura; no traducirlas aproximadamente a enum.

**[DESIGN] Forma literal del bloque:** empieza con la línea “Arguments (contract v1):”, termina con “End arguments.” y contiene una línea por parámetro, ordenada por nombre. Cada línea es “- <nombre JSON>: <predicado>=<valor JSON compacto>; ...”, con predicados en el orden cerrado anterior. Nombres y valores usan codificación JSON; ejemplo: - "mode": required=true; enum=["start","stop"]. Los claims de rutas anidadas llevan pointer:string; para un parámetro simple, el selector por defecto es /properties/<nombre escapado como JSON Pointer>. required se consulta en la lista del objeto padre; default_present distingue ausencia; present/absent comprueban identidad de clave, no truthiness. El auditor parsea delimitadores y tokens exactos; bloques duplicados, truncados o de versión desconocida fallan. No atribuye listas por proximidad de palabras.

Cada tool tendrá un bloque legible de argumentos en su descripción, renderizado desde **claims declarados** (por ejemplo, “mode: required; allowed values start | stop”). El gate contrasta:
1. Bloque publicado contra la representación exacta de sus claims, detectando edición/remoción.
2. Claims contra el inputSchema real post-registro.
3. Casos aceptado/rechazado contra el wrapper real, con call_bridge/efectos sustituidos por fakes.
4. Cobertura del catálogo contra el conjunto de tools observado, sin que catálogo ni fixtures fabriquen el registro esperado.

Generar el bloque desde claims no acredita el punto 2 ni 3; éstos usan fuentes distintas. Está prohibido extraer claims/expected desde el output del exportador o desde el mismo dato transformado. Mutar por separado descripción, schema, catálogo y validador debe cambiar el veredicto.

Migrar todas las promesas de entrada presentes en las descripciones al bloque formal. El resto de prosa describe efectos o contexto y se registra como non_decidable_text con cita/familia; no se etiqueta “auditado”. Un argumento nombrado fuera de ese bloque con una restricción de entrada requiere formalización o cobertura explícita antes del PASS. La revisión inicial cataloga esas cláusulas: el gate no se anuncia como verificador universal de lenguaje natural.

Cuando el cuerpo ya limita valores y el esquema dice str libre, materializar la restricción pública usando el productor/wrapper existente; no añadir restricciones de aceptación nuevas. Cuando la prosa promete algo que el cuerpo no permite, corregir la promesa o registrar un cambio de contrato separado. La política de M23 conserva las llamadas previamente válidas.

No adoptar automáticamente CATALOG_RECORDS M13 como completo: contiene compromisos aún no presentes en este commit. El nuevo catálogo M23 se limita a lo que existe, sin implementar seat_index/expected_type ni cerrar funcionalidades ajenas (V/tools/dayz_mcp/effective_schema_catalog.py:43,53,60).

**[DESIGN] Auditor nuevo:**

    def audit_effective_contracts(registry, declarations, runtime_results) -> dict:
        # Informe completo, entrada inválida explícita, sin I/O.

Salida: report_version:int=1; verdict:PASS|FAIL|SETUP_FAILED; registry_hash:string64|null; findings:array de objetos; coverage:objeto. Cada finding tiene code:string, tool:string|null, param:string|null, pointer:string, expected:JSON, observed:JSON, evidence:string no vacío. Orden determinista por code/tool/pointer y después JSON canónico; duplicados prohibidos. coverage contiene tool_names:array[string], checked_claim_ids:array[string], runtime_case_ids:array[string], non_decidable_text:array[objetos], unsupported_claims:array[objetos].

PASS exige conjunto de tools no vacío, cero discrepancias, ninguna claim Python sin soporte/caso exigido y cobertura completa de las declaraciones activas. La prosa/efecto Enforce declarada no decidible permanece visible. PARAM-NAME-DIVERGENCE por mera coexistencia se mantiene sólo en legacy/advisory; no bloquea ni fuerza renombrado. Un enum prometido y ausente del schema **sí** bloquea, aunque el auditor viejo calle (V/tools/dayz_mcp/effective_schema.py:283,308).

### (b) Señal por sesión y observación de despliegue

**[DESIGN] Modificar server.py sin cambiar build_app(config) -> tuple[FastMCP, Any].** Sustituir el overlay congelado por dos componentes: snapshot inmutable de arranque y observaciones actuales. Capturar el registro completo al entrar en el lifespan, antes de servir tools, cuando build_app ya terminó todos los patches; el gate también podrá llamar directamente al resolver sin entrar en lifespan. Mantener una inicialización async idempotente para pruebas/invocaciones directas. Puntos actuales: V/tools/dayz_mcp/server.py:2985,2989,4918,4923.

El snapshot se forma una sola vez por instancia app e incluye session_id aleatorio, captured_at_utc, identidad de variante, registro/hash local, epoch/revision/hash de autoridad observados al arrancar y observación PBO inicial. No volver a capturarlo al recibir stale. Si la autoridad falta al arrancar, dejar constancia: una aparición posterior no convierte retrospectivamente “desconocido” en “igual al arranque”.

**[DESIGN] Superficie pública elegida:**

    async def bridge_status(registry_only: bool = False) -> dict:
        # False: comportamiento actual de bridge_status + diagnóstico M23.
        # True: lectura local de snapshot, head y PBO; sin daemon/lease/caja.
    async def session_status() -> dict:
        # Firma actual; adjuntar el mismo diagnóstico a su salida existente.

El argumento nuevo es opcional; una llamada antigua bridge_status() conserva validez. La rama registry_only no llama touch, _call, session_status, call_bridge, descubrimiento/auto-spawn ni escaneo de procesos. Devuelve ok:true y tool_registry; no fabrica ready:true ni una generación fresca. La vía normal conserva su tratamiento actual de errores de auth/transporte; sólo errores propios de diagnóstico M23 se convierten en unknown dentro del bloque. No tragarse errores de autenticación del daemon.

Fuente de generación en el modo normal: daemon_generation del GET /status ya acreditado, no otro UUID. Congelar baseline de generación en la primera observación acreditada, con baseline_source="first_observed"; si no se obtuvo en arranque, no afirmar que representa el arranque. registry_only devuelve la última generación observada con observation="cached" o unknown; un campo cached nunca acredita que el daemon siga vivo. Detectar un reinicio a mitad requiere la siguiente lectura normal de bridge_status(), que no adquiere caja (V/tools/dayz_mcp/server.py:1667; V/tools/dayz_mcp/daemon.py:650; V/tools/README-mcp.md:132).

**[DESIGN] JSON público aditivo:**

    {
      "tool_registry": {
        "schema_version": 1,
        "session_id": "uuid de este proceso MCP",
        "profile": "standard",
        "role": "claude",
        "hash_algorithm": "dayz-mcp-effective-v1",
        "captured_at_utc": "RFC3339 UTC",
        "local_hash": "64 hex",
        "authority": {
          "state": "known",
          "epoch": "32 hex",
          "revision": 7,
          "head_sha256": "64 hex",
          "registry_hash": "64 hex"
        },
        "registry_state": "fresh",
        "tool_registry_stale": false,
        "daemon": {
          "baseline_generation": "uuid opaco o null",
          "observed_generation": "uuid opaco o null",
          "changed": false,
          "baseline_source": "startup",
          "observation": "live"
        },
        "pbo": {
          "state": "known",
          "baseline_sha256": "64 hex o null",
          "deployed_sha256": "64 hex o null",
          "published_sha256": "64 hex o null",
          "changed": false,
          "matches_published": true,
          "loaded_sha256": null
        },
        "environment_changed": false,
        "reasons": [],
        "remediation": null
      }
    }

Tipos: hashes string64|null; epoch string32|null; revision entero >=0|null; session_id/captured_at/profile/role/algoritmo strings; states enums; changed, matches_published, tool_registry_stale y environment_changed boolean|null. En unknown, los datos no acreditados son null, no ceros ni cadenas vacías. reasons:array[string] ordenada/deduplicada. remediation:null|"reopen_mcp_client"|"registry_admin_required"|"verify_deployment".

registry_state: fresh|stale|unknown. authority.state: known|missing|busy|invalid|unsupported. pbo.state: known|not_configured|missing|unreadable|unstable|too_large|timeout. daemon.observation: live|cached|unknown; baseline_source: startup|first_observed|unknown.

Reglas: hash local completo distinto del actual de su variante => stale. Epoch distinto del congelado => stale/authority_reset aunque los hashes coincidan. Misma epoch y hash actual igual => fresh, aunque haya revisiones intermedias; authority.revision permite ver la historia A→B→A. Algoritmo incompatible, variante ausente, head inválido o baseline de autoridad desconocida => unknown con razón. Un reinicio de daemon por sí solo no significa registro stale. environment_changed es true si alguna dimensión conocida cambió; false sólo si todas las dimensiones requeridas fueron observadas iguales; null si falta evidencia y ninguna demuestra cambio.

Advertencias superiores opcionales, preservando las existentes: tool_registry_stale, daemon_generation_changed, deployed_pbo_changed, registry_authority_unknown. No generar spam: advertencias estructuradas por respuesta y, si se registran en stderr, sólo al cambiar el estado/identidad.

Conservar los cuatro campos superiores legacy ya publicados: tool_registry_fingerprint, tool_registry_captured_at, tool_registry_source_stale, tool_registry_remediation (V/tools/dayz_mcp/server.py:602). Durante M23 conservan algoritmo/semántica M14; si no existe autoridad v5 válida, source_stale sigue unknown. El nuevo objeto versionado es la autoridad para M23 y se documenta esa precedencia. No comparar un hash legacy con uno M23 ni fabricar artefactos v5 para poner esos campos verdes. Su retirada requeriría otro cambio de contrato.

**[DESIGN] PBO sin reinicio.** Crear un probe de bytes desplegados dentro de registry_status.py. Recibe una ruta configurada localmente por instalación/admin, nunca un parámetro de tool. Resolver un único DayZ_MCP.pbo bajo directorios acreditados; revalidar identidad/ruta y leer mediante handle estable, con SHA-256 de bytes y límite 64 MiB. Si cambia durante la lectura, state=unstable. El patrón existente de lectura fija identidad antes/durante/después; no acredita por sí solo los padres (V/tools/dayz_mcp/runtime_state.py:1795; V/tools/dayz_mcp/request_path_authority.py:477,523).

No memoizar por tamaño/mtime: el gate cambia bytes preservando ambos. Timeout total propuesto 1 s por observación, sin escrituras; si excede, unknown y no bloquear otras tools. El publisher valida el tamaño permitido antes de activar la configuración. No copiar PBO ni recorrer raíces arbitrarias desde status.

[DESIGN] Como el límite de respuesta no cancela una lectura bloqueada del SO, permitir como máximo un probe en curso por app. Si vence 1 s, devolver timeout; consultas siguientes no crean más workers mientras el anterior siga activo. Una finalización tardía no cambia el baseline ni se presenta como observación fresca de una llamada posterior; su resultado puede descartarse. Cierre de handle en finally. La prueba inyecta un lector bloqueado y acredita respuesta acotada y un solo trabajo pendiente.

Comparar bytes actuales tanto contra snapshot de sesión como contra el PBO registrado en la promoción. Si un admin reemplaza el PBO sin pasar por el promotor, la sesión vieja ve changed=true; la nueva puede tener changed=false pero matches_published=false. En ambos casos hay aviso de despliegue, con daemon y registry_hash idénticos. La observación no publica una nueva autoridad automáticamente.

loaded_sha256 permanece null. El poll actual recibe version/instance/pid/creation/caps, no bytes del PBO (V/tools/dayz_mcp/loopback.py:2100). La ruta del asset de release tampoco demuestra el despliegue actual (V/tools/make_release.py:22). Un cambio transitorio del PBO que se revierte entre dos observaciones sólo es reconstruible si pasó por el journal del promotor; no prometer detección retroactiva de escrituras no observadas.

### (c) Calibración, publicación CAS y journal durable

**[DESIGN] Alternativa sin formato nuevo, evaluada primero:** mantener hashes sólo en memoria y compararlos al consultar status evitaría persistencia, pero perdería historia entre sesiones/reinicios y no permitiría CAS/admin recovery. No cumple la pieza (c) decidida por el dueño; no se adopta.

Crear tools/promote_effective_schema.py (CLI de repositorio, hoy ausente) y dayz_mcp/registry_authority.py. La raíz deriva de RuntimePaths.from_env, cuyo método ya resuelve LOCALAPPDATA/DayZ_MCP (V/tools/dayz_mcp/runtime_state.py:194).

**[DESIGN] Layout elegido:**

    %LOCALAPPDATA%/DayZ_MCP/tool-registry/<installation_id>/
      install.json
      registry.lock
      head.json
      contracts/<sha256>.json
      events/<txid>.json
      archive/<epoch>/...

installation_id: UUID32 generado al provisionar, estable durante upgrades y distinto para instalaciones separadas. Las tres ramas de desarrollo no comparten identidad publicada por defecto. install.json contiene schema_version:1, installation_id, raíz de distribución local acreditada, variantes de public_config y ruta del PBO acreditada o null; no contiene secretos. Sólo publisher/admin escribe, nunca un cliente. Las rutas permanecen locales y no viajan en tool_registry.

Journal elegido: **un JSON inmutable por evento**, no JSONL append concurrente. Evita cola parcial de append y no requiere reescribir el historial completo. head.json selecciona el último evento comprometido; los eventos enlazan a su predecesor por hash. Los clientes pueden comparar epoch/revision/head_sha256 y, si hace falta, el admin recorre la cadena. Un evento huérfano en disco no es una promoción.

**[DESIGN] Documento de autoridad:** contratos por variante y hash completo; campos de procedencia fuera de cada hash de registro. Incluye artifact_version:1, installation_id:string32, variants:array de objetos {profile,role,public_config,registry,registry_hash}, source_commit:string|null, source_files:array[{path_relative,sha256}], deployed_pbo_sha256:string64|null y gate_report_sha256:string64. variants no vacío, identidades únicas; todos los hashes se recalculan desde los bytes incluidos. source_commit es informativo; el hash de los archivos y de la publicación es la evidencia. No incluir al propio artefacto ni a head en su material de hash.

**[DESIGN] Evento inmutable:**

    {
      "schema_version": 1,
      "installation_id": "32 hex",
      "epoch": "32 hex",
      "revision": 7,
      "txid": "32 hex",
      "operation": "publish",
      "previous_head_sha256": "64 hex o null",
      "previous_event_sha256": "64 hex o null",
      "artifact_sha256": "64 hex",
      "created_at_utc": "RFC3339 UTC",
      "reason": "texto local acotado"
    }

operation: bootstrap|publish|reset|rollback. reason:string no vacío, <=240 caracteres. revision:entero >=0, exactamente anterior+1 dentro de epoch; bootstrap/reset comienzan en 0 con epoch nueva. Timestamp no ordena eventos ni participa en decisiones CAS. El archivo se nombra por txid; el hash se computa sobre sus bytes canónicos.

**[DESIGN] Cabecera:**

    {
      "schema_version": 1,
      "installation_id": "32 hex",
      "epoch": "32 hex",
      "revision": 7,
      "txid": "32 hex",
      "event_sha256": "64 hex",
      "artifact_sha256": "64 hex"
    }

**[DESIGN] APIs del módulo:**

    def read_registry_authority(root, *, variant) -> dict:
        # Lee head/event/artifact coherentes; no repara ni escribe.
    def publish_registry_authority(root, artifact, *, expected_head, txid, reason) -> dict:
        # expected_head = {epoch, revision, head_sha256}, o null sólo bootstrap.
    def recover_registry_authority(root) -> dict:
        # Admin/publisher bajo lock; valida, conserva el commit, archiva huérfanos.
    def reset_registry_authority(root, artifact, *, expected_head, reason) -> dict:
        # Backup verificado + epoch nueva; nunca restablece fresh por asumir vacío.

Resultado publish: status=committed|unchanged|conflict|busy|invalid|unknown_commit; previous/current:token|null; txid:string32; reason:string. Committed incluye head_sha256, epoch y revision efectivos. unknown_commit obliga a releer y consultar txid antes de reintentar; no equivale a fracaso sin escritura.

**[DESIGN] Secuencia de publicación y punto de commit:**

1. Fuera del lock: construir apps en procesos nuevos, con configuración explícita y fakes de runtime que impiden lifecycle; capturar publicación real, ejecutar gate y preparar contrato canónico. Verificar __file__/intérprete/raíz y hash del cierre de fuentes antes y después. No aceptar un snapshot pregrabado como productor.
2. Revalidar que el candidato corresponde a los bytes instalados que atenderá una sesión nueva; si un editor/deploy cambió fuentes, abortar y regenerar. El publisher se integra como paso obligatorio del despliegue Python. Un cliente nuevo cuyo hash difiera del head nunca publica “para arreglarlo”.
3. Adquirir registry.lock exclusivo de esa instalación, fail-fast. Provisionarlo una sola vez de forma exclusiva; no sustituir el archivo de lock durante reset. Adaptar los errores launcher del helper al dominio M23 (V/tools/dayz_mcp/registry_lock.py:80,100,109).
4. Leer/validar head, evento y artefacto anteriores bajo lock. Comparar **epoch + revision + SHA-256 de los bytes de head** con expected_head. No comparar sólo registry_hash: A→B→A sería un falso CAS.
5. Si CAS falla, devolver conflict sin escribir evento/cabecera; no actualizar expected ni reintentar automáticamente. Si artefacto idéntico y precondición válida, devolver unchanged sin sumar revisión.
6. Escribir primero contracts/<hash>.json de forma durable, validar hash; si existe, verificar bytes idénticos. Después escribir events/<txid>.json durable, con creación exclusiva o igualdad exacta idempotente. Temporales y archivos definitivos dentro del mismo volumen.
7. Revalidar precondición y reemplazar head.json atómicamente. **Éste es el único punto de commit.** Publicar el valor anterior y el nuevo en el evento/token. No hace falta una segunda marca COMMITTED.
8. Releer y verificar head→event→artifact antes de devolver committed. Soltar lock. Si el proceso muere después del replace, el siguiente lector reconoce el commit por head aunque no hubiera respuesta.
9. Lectores toman lock compartido fail-fast para una lectura acotada coherente; busy devuelve unknown/busy conservando snapshot local. No esperan a la promoción ni mezclan documentos de revisiones distintas.

Reutilizar atomic_write_bytes para documentos UTF-8 bajo este lock; para la cabecera exponer un wrapper específico de CAS que use también expected_sha256 ya soportado por _atomic_write_text. No cambiar coordinación ni usar su journal. El fsync actual cubre el temporal antes de os.replace, no una transacción entre archivos (V/tools/dayz_mcp/runtime_state.py:1890,1924,1933,1957).

**[DESIGN] Recover:** head válido manda. Temporales y eventos no referenciados se archivan bajo lock, sin adoptarlos automáticamente ni avanzar una intención a medias. Head que refiere un evento ausente, hash incorrecto, cadena inconsistente o versión futura => invalid/unsupported, publicación bloqueada hasta recovery admin. No elegir “el evento de mayor número” ni reconstruir autoridad por mtime. Último head comprometido completo puede conservarse en el backup admin; recuperación desde backup crea epoch nueva para que las sesiones detecten el reset.

[DESIGN] “No referenciado” significa fuera de la cadena transitiva de eventos comprometidos y de los backups de epochs retenidas, no simplemente distinto del evento actual. Los antecesores comprometidos se conservan. Reset copia y verifica el backup antes de publicar la nueva epoch; no mueve/borrar archivos necesarios para la cabecera anterior mientras ésta siga activa.

**[DESIGN] Admin y rollback de formato.** Legacy v5 M14 permanece en su ubicación y con sus lectores; no se reescribe a M23. Al arrancar código pre-M23, ignora el nuevo directorio y mantiene su comportamiento anterior. Código M23 con sólo datos legacy muestra authority missing/unknown hasta bootstrap; no convierte v5 en autoridad completa por copiar su digest. Formato futuro desconocido se conserva sin sobrescribir.

Regenerar normalmente es publish con expected_head y nuevas evidencias; no limpia historia. Limpiar/reset exige copiar install/head/contracts/events a archive/<epoch>, comprobar hashes del backup y emitir recibo local antes de crear epoch nueva. Prohibido borrar primero. Rollback de código registra una **nueva promoción** con el contrato del código restaurado; no retrocede revision/epoch. Si se pierde todo el directorio por intervención externa, clientes vivos muestran missing; el siguiente bootstrap genera nueva installation_id/epoch y exige readmisión explícita, no continuidad fabricada.

Retención elegida para M23: no GC automático de eventos/contratos comprometidos. Status lee sólo cabecera, evento actual y artefacto necesario, no el historial. El admin puede archivar epochs completas verificadas; una política de borrado queda fuera del lote.

### Matriz de archivos y módulos sellados

[DESIGN] Lista acotada de cambios de producción, sin refactor adyacente:

| Archivo | Trabajo M23 | Dependencias / rebuild |
|---|---|---|
| tools/dayz_mcp/effective_schema.py | Resolver async completo y auditor nuevo; legacy conservado | No está en PACKAGED_MODULES. |
| tools/dayz_mcp/effective_schema_core.py | Canonicalización completa nueva, estricta y versionada | Mantener envelope M13 legacy. No sellado. |
| tools/dayz_mcp/effective_schema_contracts.py, nuevo | Claims, bloque de descripción y cobertura de validación Python | No importar desde módulos sellados. |
| tools/dayz_mcp/registry_authority.py, nuevo | CAS, journal, lector, recover/reset | Reutiliza lock y escritura atómica; no coordinación. |
| tools/dayz_mcp/registry_status.py, nuevo | Snapshot/diff de sesión y probe PBO acotado | Sólo lectura en runtime. |
| tools/dayz_mcp/server.py | Captura después del registro; status aditivo y registry_only | Firma build_app intacta; bridge_status gana argumento opcional. |
| tools/dayz_mcp/runtime_state.py | Wrapper público de escritura CAS, sin modificar formatos existentes | Delegación a mecanismo ya existente. No sellado. |
| tools/promote_effective_schema.py, nuevo | CLI check/publish/recover/reset, configuración local de instalación | No arranca daemon ni juego. |
| tools/checks/check_m23.py, tools/tests/test_m23_*.py, nuevos | Driver acotado, fixtures generadas y mutantes | No empaquetados; no ejecutar la suite global en este host. |
| tools/README-mcp.md y ledger M23 | Flujo y contrato versionado, comandos y veredictos | Documentación; ningún PBO nuevo. |

No es necesario cambiar daemon.py, loopback.py, session_coordination.py, control_client.py ni result_prune.py para la solución elegida: se consumen sus campos y se añade el overlay en server.py. La generación normal ya existe y /status es la fuente; añadir campos a daemon_status rompería su keyset exacto (V/tools/dayz_mcp/daemon.py:650,652; V/tools/dayz_mcp/orphan_guard.py:843,855). result_prune conserva claves desconocidas (V/tools/dayz_mcp/result_prune.py:69).

**Rebuild:** PACKAGED_MODULES es la autoridad de cierre en V/tools/build_native_launcher.py:53. No incluye los módulos anteriores. Por tanto M23 Python/status/journal no exige por sí mismo reconstruir app.pyz ni PBO. Sí lo exigiría tocar server_cli, host_config, dayz_test_modes, dayz_test_request, dayz_test_worker u otro elemento de esa tupla, o importar una dependencia nueva desde su cierre; esa ampliación no se propone. L5 compara el cierre real y falla si se introduce esa dependencia sin rebuild acreditado. No se acreditó el app.pyz desplegado en esta sesión.

## 3.4 Secuencia de implementación en lotes pequeños

### Contrato de ejecución de los gates

**[DESIGN] Antes del código de cada lote:** escribir sus fixtures/casos en unittest.TestCase o IsolatedAsyncioTestCase, ejecutar el rojo por el comportamiento que falta y conservar input, stdout/stderr y exit. Después implementar, repetir y calibrar con los mutantes nombrados. Ninguna ronda 1 comienza hasta que los checks y la lista cerrada de contraejemplos de ese lote están escritos y ejecutables.

**[DESIGN] Crear primero tools/checks/check_m23.py.** Driver de tests de repositorio, no juez de un implementador. Argumentos: --lot L0|L1|L2|L3|L4|L5|all; --seed int; --timeout int=50; --mutant identificador opcional; --output ruta local opcional. --mutant selecciona una mutación del sujeto dentro de una copia temporal, nunca modifica el repo instalado. Subproceso unittest usa sys.executable, cwd tools del árbol de prueba y PYTHONPATH=.; timeout real, no un comentario.

Salida JSON única con gate_version, lot, seed, source_root, source_commit, python_executable, tests_run, skipped, verdict, checks y mutants. Cada check incluye id, observed, expected y verdict. Exit 0 sólo PASS y marcador M23-GATE-PASS:<lot>; 1 para FAIL del sujeto; 2 para SETUP-FAILED (timeout, import/test ausente, cero casos, skip requerido, fixture no alcanzada o entorno incorrecto). No inferir éxito de un contador ni de rc=0 sin el marcador y las identidades esperadas.

[DESIGN] --seed fija la distribución de formas para reproducir cobertura, pero cada corrida añade un challenge_nonce nuevo de entropía del SO para nombres, valores y claves adicionales. El oráculo genera el registro completo antes de invocar al sujeto; el manifest final guarda seed+nonce+fixtures para replay diagnóstico. Un replay fijo no sustituye la calibración fresca de aceptación. Para las tres pruebas entre procesos se reutiliza un único fixture recién generado y se varía sólo PYTHONHASHSEED. No guardar un banco literal de respuestas que permita despacho por reconocimiento.

La calibración tiene su propio PASS: cada mutante debe ser rechazado por su check discriminador, con fixture alcanzada; un error de import/timeout no cuenta como mutante muerto. Control fiel debe pasar el mismo gate. --mutant aislado devuelve 1 con el check esperado rojo; el modo normal comprueba ese resultado y puede devolver 0.

**[EXACT: sintaxis de lanzamiento existente; DESIGN: archivos/flags M23 nuevos]** Ejecutar desde tools del checkout de implementación seleccionado:

    $env:PYTHONPATH = '.'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    ./.venv-mcp/Scripts/python.exe -B checks/check_m23.py --lot L0 --seed 2300 --timeout 50
    ./.venv-mcp/Scripts/python.exe -B checks/check_m23.py --lot L1 --seed 2301 --timeout 50
    ./.venv-mcp/Scripts/python.exe -B checks/check_m23.py --lot L2 --seed 2302 --timeout 50
    ./.venv-mcp/Scripts/python.exe -B checks/check_m23.py --lot L3 --seed 2303 --timeout 50
    ./.venv-mcp/Scripts/python.exe -B checks/check_m23.py --lot L4 --seed 2304 --timeout 50
    ./.venv-mcp/Scripts/python.exe -B checks/check_m23.py --lot L5 --seed 2305 --timeout 50

Estos seis comandos **se proponen para implementación**, no se han ejecutado: los módulos M23 aún no existen. L0 debe dejarlos ejecutables antes de la ronda 1 de cada producto. La medida de autoría separada es probe_baseline.py, ya ejecutado.

### L0 — Fijar consumidor, baseline y arnés

Dependencias: ninguna; no cambia lógica de producción. Archivos: tests/fixtures generadoras, check_m23.py, ledger del lote y manifest de fuentes. Traza E5/E6.

- Registrar commit/dirty diff y hashes del árbol candidato, ruta real del intérprete, SDK instalado y configuración de cada variante.
- Releer mecanismos citados si cambió HEAD; medir publicación cruda, no fijar “60 tools” como expected.
- Arneses de extracción no entran en lifespan ni abren puertos; tests runtime inyectan transporte/probe/paths y TEMP/LOCALAPPDATA propios. Sólo helpers de test desechables pueden crearse/terminarse.
- Congelar criterios, IDs de mutantes y tope de rondas en el ledger antes de enviar la ronda 1.

**Oráculo:** test_m23_harness.py. Control válido retorna PASS; fixture inexistente, test vacío, skip, timeout y falso stdout PASS con exit 1 deben producir SETUP-FAILED/FAIL según el contrato, sin aceptación. Mutante H0: siempre imprimir M23-GATE-PASS; debe caer. Comando: el de L0 anterior. Si el driver no acredita que alcanzó el sujeto, no se abre L1.

### L1 — Exportador completo y huella reproducible

Dependencia L0. Archivos: effective_schema.py/core y test_m23_effective_registry.py. Traza E5.

**Rojo primero:** generar una tool de nombre no conocido al importar el módulo, con outputSchema y un campo extra; resolverla tras alias/remoción. El exportador heredado omite campos y debe fallar una comparación completa. No esperar un error incidental.

**Oráculos independientes:**
- Lista SDK serializada directamente por el test, sin usar el exportador/core del candidato, frente al documento completo del candidato.
- Casos estándar y exec, roles claude/codex/unknown; remoción y alias de un nombre generado después de construir la app. El perfil esperado nace de la configuración/registro real.
- Misma definición en tres procesos con PYTHONHASHSEED=1, 2 y random, sin -I; comprobar flags/entorno y bytes/hash completos idénticos. Repetir variando orden de registro y claves.
- Cambiar por separado cada eje: nombre, alta/baja, description, instructions, required, items, límites, default ausente/null/falsy, anyOf/oneOf anidados, enum bool/number/objeto, $defs/$ref, outputSchema, annotations, _meta y keyword extra generada. Todos cambian el hash.
- No registro, nombres duplicados, JSON no finito, profundidad/tamaño excedidos: error declarado, nunca hash de contrato vacío o parcial.

Mutantes cerrados: R1-snapshot, R2-projection, R3-before-alias, R4-default-null-collapse, R5-narrow-union, R6-ignore-output-meta, R7-ignore-profile, R8-seeded-python-hash, R9-empty-registry. Todos deben caer por una diferencia observada; control fiel pasa. Comando L1; repro individual, por ejemplo:

    # [DESIGN] Debe salir 1, check registry_full_equality rojo, fixture_reached=true.
    ./.venv-mcp/Scripts/python.exe -B checks/check_m23.py --lot L1 --seed 2301 --timeout 50 --mutant R2-projection

### L2 — Autoridad bloqueante del contrato y claims

Dependencia L1. Archivos: effective_schema_contracts.py, auditor, descripciones/schema patches estrictamente necesarios y test_m23_contract_gate.py. Traza E5.

**Rojo primero:** descripción declara enum generado, schema no tiene enum; el auditor heredado devuelve vacío y el nuevo gate debe fallar. Otro caso: quitar la claim o la tool completa no puede borrar la obligación de cobertura.

**Oráculos:** bloque formal completo, claims contra registro real, validación Python pos/neg con fakes y declaración de frontera in-game. Caso de alias correcto no denuncia from_pos. Coexistencia type/classname con conceptos distintos no bloquea. La tool con default=0/false/"" conserva sus valores y acepta el mismo input. Declaración de parámetro ausente no se acepta por estar en catálogo M13.

Mutantes: C1-no-audit, C2-fixture-dispatch, C3-drop-enum-check, C4-delete-claim, C5-remove-validator, C6-generate-expected-from-schema, C7-delete-description-block, C8-foreign-param-claim. C6 se mata cambiando el schema publicado sin tocar la declaración independiente. Control fiel pasa; errores de datos y claims desconocidas dan SETUP-FAILED. Comando L2.

Final del lote: check obligatorio conectado a unittest del repo y a la ruta de CI usada por el proyecto. Pre-commit puede invocar el mismo check, pero no es autoridad única opcional. No añadir una segunda implementación del auditor en CI.

[DESIGN] Entrada mínima concreta de suite: test_m23_contract_gate.py contiene RepositoryContractTests.test_published_contract_matches_claims, que construye la app con dependencias de runtime inertes, observa el listado SDK y exige PASS del auditor sobre declaraciones independientes. unittest discover la recoge por clase/método. El driver llama ese mismo test; no basta probar sólo schemas sintéticos. Si no existe CI configurada al integrar, la entrada unittest sigue siendo obligatoria y L5 documenta su comando, sin inventar una plataforma de CI.

**Lote mínimo útil si se corta presupuesto: L0+L1+L2.** Entrega la pieza (a) y su calibración como gate real. No cerrar 9b7b ni afirmar CAS/journal o M23 completo si se para aquí.

### L3 — CAS y journal recuperable

Dependencias L1+L2. Archivos: registry_authority.py, wrapper CAS acotado, promotor CLI, test_m23_registry_journal.py. Traza E5/E4/E6.

**Rojo primero:** dos publishers con el mismo token previo producen dos commits si no hay exclusión/CAS. El oráculo usa dos procesos auxiliares de fixture, no threads solamente.

**Oráculos:** único ganador y otro conflict; cadena/digests válidos; rechazo por epoch/revision/SHA de cabecera, incluyendo A→B→A; publish idéntico unchanged sin revisión; retry del mismo txid idempotente; lectura concurrente coherente o busy; truncado/corrupción/version futura unknown y cero reparación por clientes.

Cortes instrumentados: tras temporal de contrato, contrato durable, temporal de evento, evento durable, temporal de head, replace de head y antes de respuesta. Cada corte termina únicamente el helper de fixture que lanzó el test y recupera desde un proceso nuevo. No simular todo con una excepción en el mismo proceso: el lock debe liberarse por salida del escritor.

Mutantes: J1-no-lock, J2-hash-only-cas, J3-head-first, J4-replay-orphan, J5-client-publishes, J6-reset-same-epoch, J7-assume-replace-failed, J8-trust-corrupt-head. Verdad esperada en §3.5. Comando L3. Disco lleno/permiso denegado se inyectan en filesystem temporal; no afectar el host.

### L4 — Snapshot de sesión y estado con PBO

Dependencia L3. Archivos: registry_status.py, server.py y test_m23_registry_status.py. Traza E5.

**Rojo primero:** A captura autoridad r0, publisher publica r1, A vuelve a consultar; el overlay actual continúa congelado. B abre después con contrato r1. Debe observarse A stale/B fresh sin recapturar A.

**Oráculos:**
- bridge_status(registry_only=true) con daemon inexistente: cero calls a transporte/spawn/lifecycle, cero tickets/leases, diagnóstico local válido.
- Dos apps concurrentes A/B; sólo el publicador avanza journal. Consultas repetidas conservan hash/snapshot local A.
- GET /status fake cambia generación G0→G1 con mismo contrato: daemon.changed=true, tool_registry_stale=false.
- PBO fixture cambia bytes con mismo nombre/tamaño/mtime y mismo daemon/schema/version/caps: pbo.changed=true. Sesión B detecta mismatch contra publish aunque su baseline ya contenga bytes nuevos.
- Autoridad ausente, bloqueo, versión futura, rol unknown y variante TTL no publicada: nunca fresh falso.
- Fixture de respuesta /status acreditada conserva intacto daemon_status; conservar null/false/[] dentro del bloque nuevo.
- Cliente que sólo llama bridge_status() y session_status() con schemas viejos sigue invocando las firmas anteriores.

Mutantes: S1-frozen-comparison, S2-refresh-baseline, S3-generation-only, S4-pbo-mtime-only, S5-unknown-is-fresh, S6-rewrite-old-head, S7-put-field-in-daemon-status, S8-prune-unknown-null, S9-new-session-hides-unpublished-pbo, S10-registry-only-spawns. Comando L4.

### L5 — Integración, publicación y compatibilidad

Dependencia L4. Archivos: CLI promotor, documentación, ledger y test_m23_promotion.py. Traza E3/E5/E6.

**Rojo primero:** cambiar una fuente/schema después de generar el candidato y antes del publish. Debe fallar y conservar head anterior. Un gate PASS antiguo sobre otros bytes no autoriza el publish.

**Oráculos:** comando check sin escrituras; publish con CAS en instalación temporal; rollback conserva llamadas antiguas y crea nueva revisión; código legacy ignora journal M23; versión futura no sobrescrita; instalación paralela no contamina otra; no se aceptan rutas arbitrarias de PBO; cierre PACKAGED_MODULES sin nuevas dependencias no empaquetadas.

Mutantes: P1-stale-evidence, P2-cross-installation, P3-reuse-old-revision, P4-overwrite-future, P5-unsealed-import. Comando L5. La CLI escribe y lee su candidato en cwd/staging, nunca publica a LOCALAPPDATA durante tests salvo raíz temporal inyectada.

**[DESIGN] Comandos de operación futura** (desde tools; la ruta del candidato es un artefacto concreto generado en ese checkout):

    ./.venv-mcp/Scripts/python.exe -B promote_effective_schema.py check --output ../reports/m23-candidate.json
    ./.venv-mcp/Scripts/python.exe -B promote_effective_schema.py publish --candidate ../reports/m23-candidate.json --expected-head ../reports/m23-expected-head.json
    ./.venv-mcp/Scripts/python.exe -B promote_effective_schema.py recover --installation ../reports/m23-installation.json
    ./.venv-mcp/Scripts/python.exe -B promote_effective_schema.py reset --installation ../reports/m23-installation.json --expected-head ../reports/m23-expected-head.json --reason "admin recovery"

check no escribe autoridad; output es local solicitado. publish obtiene instalación del candidato acreditado y compara su identidad con install.json. Bootstrap sólo acepta expected-head con JSON null y directorio provisionado coherente; no interpretar un error de lectura como ausencia. recover/reset son operaciones de admin documentadas, nunca acciones de una tool invocadas automáticamente.

## 3.5 Escenarios R8 y matriz de estado

[DESIGN] “Crash” en esta tabla significa que termina el proceso auxiliar escritor. Una excepción capturada se registra como exception y no se confunde con ese caso. “Corrupción” significa bytes/formato incoherentes; no se declara por una lectura temporal busy.

| Escenario / corte | Qué queda en disco | Qué hace el siguiente arranque/lector | Señal / oráculo |
|---|---|---|---|
| Happy path r0→r1 | Contrato y evento r1 completos; head r1 | Valida cadena/hash y selecciona r1 | A vieja stale, B nueva fresh; L3/L4 |
| Termina durante temporal del contrato | Head r0; temporal incompleto | Ignorar temporal; publisher recover lo archiva | Nunca avanza r1; J3 |
| Contrato durable, sin evento | Head r0; blob no referenciado | r0 sigue autoritativo; blob huérfano no publica | r0 válido; L3 |
| Evento durable, antes de head | Head r0; contrato/evento r1 huérfanos | No replay automático; recover archiva bajo lock | r0 válido, r1 no committed; J4 |
| Durante escritura del temporal de head | Head r0; temporal y evento r1 | Igual que anterior; no leer temporal como head | r0 o unknown si I/O no acreditable |
| Tras replace de head, antes de respuesta | Head r1 completo; evento/contrato r1 | Reconoce commit y txid; no duplica revisión | committed después de relectura; J7 |
| Error post-replace de identidad/lectura | Commit puede haber ocurrido | Devuelve unknown_commit; resolver mediante head y txid | Prohibido retry ciego; L3 |
| Head r1 apunta a evento ausente/corrupto | Referencias incoherentes | unknown/invalid; cliente no repara | J8; ningún fresh |
| Reader coincide con publisher | Lock ocupado o snapshot completo | Retorna unknown/busy o versión íntegra anterior/nueva | Nunca mezcla head r1 con contrato r0 |
| Dos publishers esperan r0 | Uno adquiere/commitea; segundo encuentra otro token | Segundo conflict sin reintento automático | Un committed por token; J1/J2 |
| Dos sesiones A predeploy / B postdeploy | Una autoridad r1 compartida; snapshots sólo locales | A conserva H0; B captura H1 | A stale, B fresh; sin lease |
| Daemon reinicia G0→G1 a mitad | Journal no cambia por reinicio | Siguiente status normal observa G1; baseline G0 permanece | daemon.changed=true; registry fresh si H igual |
| registry_only después de reinicio no consultado | Mismo journal; generación última conocida | observation=cached/unknown, no afirmar daemon fresh | Contrato/PBO se verifican localmente; generación pendiente |
| PBO se redeploya sin daemon restart | Bytes P1; journal puede seguir describiendo P0 | Rehash de archivo estable detecta diferencia | pbo.changed/matches_published; S4/S9 |
| Nuevo cliente tras PBO no publicado | Baseline cliente P1; autoridad P0 | Comparar además contra published_sha256 | changed=false pero matches_published=false; aviso |
| Admin regenera contrato | Evento nuevo r+1, epoch conservada | CAS normal con evidencia actual | No borra historial; changed sólo según diferencias |
| Admin limpia/reset con mismo contrato | Backup completo verificado; epoch nueva r0 | Snapshot con epoch anterior => stale/authority_reset | J6; no ABA falso |
| Admin borra journal externamente | Ausencia real | unknown/missing, sin bootstrap automático desde cliente | S5; admin_required |
| Rollback de código | Evento nuevo con contrato anterior, revisión creciente | Sesiones comparan el contrato restaurado; no retroceder token | P3; legacy no lee M23 |
| Cliente anterior al soporte M23 | No tiene código lector nuevo | Sus tools continúan; recibe sólo lo que su proceso implementa | No prometer aviso retroactivo; reapertura inicial de adopción |
| Versión futura de head o datos legacy v5 | Bytes se conservan | Unsupported o missing M23; no conversión implícita | P4; unknown visible |
| Archivo PBO falta, excede límite o cambia durante lectura | No se escribe nada | missing/too_large/unstable y hashes no acreditados null | No asumir igualdad con baseline |
| Cambio PBO transitorio entre observaciones sin publisher | No quedó evento que lo pruebe | No puede reconstruirse retroactivamente | Límite declarado; no falso historial |

Garantía de recuperación de L3: salida abrupta del proceso y errores de I/O reproducidos en fixtures locales. **No se acredita resistencia a corte de energía del host** únicamente con os.fsync+os.replace; ese experimento de almacenamiento queda explícito en §3.8. La política ante pérdida/incoherencia de cualquier referencia es unknown y conservación de evidencia, no reconstrucción optimista.

## 3.6 Gates y parada del bucle de revisión

| Gate | Consumidor / prueba | PASS | FAIL / SETUP-FAILED |
|---|---|---|---|
| G0 | Driver L0 | Casos/fixture reales alcanzados, entorno fijado | Cero casos, skip o timeout no pasan |
| G1 | Exportador L1 | JSON íntegro igual al SDK, determinismo y sensibilidad | Cualquier campo omitido, hash variable o mutante vivo |
| G2 | Contrato L2, suite obligatoria | Promesas formalizadas y validadores coinciden; cobertura explícita | Discrepancia o claim Python no cubierta |
| G3 | Disco L3, dos procesos | CAS único, punto de commit y recuperación medidos | Dos commits por token, replay huérfano o fresh corrupto |
| G4 | Consumidor MCP L4 | A/B, daemon y PBO distinguidos sin caja; firmas antiguas válidas | Recaptura silenciosa, generación sola o PBO stat-only |
| G5 | Promoción L5 | Evidencia liga bytes fuente/candidato/publicación, rollback y aislamiento | Snapshot stale, otra instalación o formato futuro sobrescrito |
| G6 manual final | Dos sesiones MCP reales sobre distribución M23 | Aviso A stale/B fresh y PBO independiente; abajo se define | No se acredita por sólo hash/count o por PASS offline |

**[DESIGN] Gate manual final, sin escenario de juego obligatorio.** En una instalación de prueba controlada:
1. Abrir sesión A con distribución M23/H0; llamar bridge_status(registry_only=true), guardar snapshot/token.
2. Publicar una distribución revisada H1 que cambie un descriptor o añada una tool de prueba aprobada, por CAS, sin reiniciar el daemon. Abrir sesión B; llamar el mismo verbo en A y B.
3. A mantiene local_hash=H0 y devuelve tool_registry_stale=true/remediation=reopen_mcp_client; B tiene H1/fresh. Ninguna llamada adquiere ticket/lease ni inicia juego.
4. En la instalación de prueba, redeplegar el PBO acreditado con bytes distintos y llamar de nuevo: señal PBO cambia con el mismo daemon y hash de registro. Guardar hash real pre/post, no sólo mtime. No usar el PBO activo de otra sesión.
5. Para el subcaso daemon restart, gestionarlo mediante el lifecycle/session runbook y con lease en una ventana asignada; nunca matar procesos directamente. Llamar bridge_status() antes/después y observar sólo generación cambiada si H no cambió. Terminar la secuencia exclusiva, liberar lease y documentar session_status.
6. Si no se dispone de instalación/ventana, G6 queda PENDING o ABANDON con motivo/observador nombrado; no rebautizarlo PASS. Esta autoría no ejecutó ninguno de esos pasos.

La comprobación manual observa la superficie MCP y los bytes desplegados, no efectos Enforce. Si el dueño exige acreditar que el puente ejecuta el contrato, será otra tarea in-game; 141e no se cierra con esa promesa ficticia.

**Parada vinculante.** VERDE y lista cerrada de mutantes de §3.4 escritos antes de ronda 1. Para código de cada lote, máximo **dos rondas** implementador/revisor; la tercera es ORCHESTRATOR_NEEDED. Para el documento/artefacto de proceso, una ronda según GL:423; sin convergencia, el orquestador lo retira/degrada con motivo, no abre una cadena indefinida. Regla fuente: GL:404,417,418,422,423.

Un hallazgo bloquea sólo con línea repro: y comando ejecutable que ponga rojo un criterio acordado. Sin repro va a backlog numerado; con producto verde, una propuesta de “oráculo más fuerte” o formato no reabre el bucle. Dos ataques distintos contra la misma métrica revelan un defecto de observabilidad: declarar ABANDON de esa dimensión y quién puede observarla, no seguir parcheando contadores (GL:419,420,421). El orquestador mantiene la decisión de integración; el autor del plan no declara cero defectos universales.

**Criterios verificables antes de R22:** G1 prueba consumidor independiente y mutante snapshot; G2 prueba enum prometido ausente con check rojo; G3 mata dos publishers con mismo token; G4 prueba PBO con mtime/tamaño preservados; G5 prueba cambio de fuente antes de publish; G0 distingue setup fallido de rechazo del sujeto. Seis criterios concretos, no “verificar que funciona”.

El mandato histórico ROOT-REVIEWS del ledger pide otra forma de cierre; para M23 manda la regla de parada posterior del encargo/GL y se documenta el ledger específico sin editar silenciosamente el raíz durante esta autoría (V/GATES.md:24, documento de trabajo leído; GL:425). No usar las 2794 pruebas citadas en el briefing como contador requerido ni atribuirles un PASS: no se ejecutaron. Los dos centinelas de test_task9_spawn_phase_markers quedan como rojos conocidos aportados por el encargo, no como resultados medidos aquí.

## 3.7 Preguntas para el dueño

Decisiones tomadas para avanzar sin operador. No se solicita respuesta ahora ni se condiciona la escritura del plan.

| Pregunta para revisión posterior | Opción recomendada y adoptada | Coste de la alternativa |
|---|---|---|
| ¿Cómo observar sin daemon/caja? | Añadir registry_only opcional a bridge_status; el modo normal conserva su ruta | Un verbo nuevo aumenta inventario; consultar siempre daemon impide diagnóstico local cuando está ausente |
| ¿Qué contrato hace mecánica la prosa? | Bloques de argumentos desde claims independientes + prueba contra schema/validador; prosa de efectos declarada no decidible | NLP/regex libre produce huecos y falsos positivos; describir todo como no verificable incumple (a) |
| ¿Cómo conservar el journal? | Eventos JSON inmutables y head atómico; sin GC automático en M23 | JSONL requiere recuperar append parcial; SQLite añade otra dependencia/operación; borrar historia exige política y backup |
| ¿Cómo tratar nombres type/classname y sesiones abiertas? | Conservar nombres públicos existentes; corregir promesas y marcar coexistencia sólo advisory | Renombrar requiere migración/aliases y podría romper clientes; queda fuera de la necesidad del instrumento |
| ¿Qué significa detectar cambio de PBO? | Bytes del archivo desplegado configurado/acreditado; loaded_sha256=null | Acreditar bytes cargados exige otro mecanismo de arranque/puente y gate in-game; sólo versión/caps/mtime pierde redeploys |

## 3.8 LO QUE NO PUDE VERIFICAR

- No se implementó M23 ni se ejecutaron sus gates propuestos. PLAN-M23.md define el trabajo; el resultado medido es únicamente el descubrimiento y las 54 pruebas aisladas del antecedente vivo fijado.
- No se lanzó juego, daemon, lifecycle ni suite completa; no se llamó session_status de las sesiones vivas. Es una excepción deliberada al runbook de cierre de juego porque este encargo prohíbe acceder a esos procesos y no se adquirió lease.
- No se acreditó PBO desplegado/cargado, path local efectivo de distribución, contenido de app.pyz ni manifest instalado. El inventario de módulos sellados procede del builder leído. Hubo acceso denegado en native-launchers, conservado en los logs.
- No se prueba comprensión universal de descripciones libres ni conducta Enforce. El gate nuevo deberá reportar cobertura mecánica y no decidible por separado; el censo de caps no cambia esta frontera.
- No se reejecutaron los bancos completos v4/v8 ni sus suites históricas. Se leyeron gates/reportes y mutantes relevantes; no se auditó íntegramente cada implementación alternativa. Los resultados históricos se atribuyen a sus archivos/commits.
- No se acreditó tolerancia a pérdida de energía del filesystem, ni que una lectura de archivo regular con timeout tenga latencia garantizada sobre almacenamiento arbitrario. L3 cubre terminación de proceso; status degrada a unknown cuando no puede observar.
- No se puede avisar retroactivamente a un proceso MCP anterior al código lector M23 sin reabrirlo al menos una vez. Tampoco reconstruir cambios de PBO no registrados y revertidos entre observaciones.
- La configuración exacta de cada instalación/variante debe provisionarse al implementar; no se inferirá de una ruta de release o de un proceso ajeno. Variantes ausentes se declaran unknown.
- HEAD vivo avanzó durante la lectura. El baseline de este plan es 8ff937e con copias/hash locales; L0 debe validar el delta y las citas al integrar. No se declara equivalencia global entre árboles.
- Algunas lecturas exploratorias se truncaron; sus salidas literales se conservan y las piezas decisivas se releyeron por bloques o se fijaron en evidence/live-head. Las notas de cada lane distinguen lecturas completas, búsquedas y probes.
- El vault se dejó intacto por mandato explícito de solo lectura. La evidencia durable entregada en cwd es PLAN-M23.md, RESEARCH-M23.md, S7-NOTES.md, BRANCHES-NOTES.md, RUNTIME-NOTES.md, COMMANDS-*.txt y el manifest de fuentes; no se escribieron handoffs fuera del directorio autorizado.
