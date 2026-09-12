> [!WARNING]
> **STALE (2026-09-12)**
> No es autoridad de producto. El HEAD actual es v1.2 / `origin/main` @ `edc7bb3`. No reabrir hallazgos sin evidencia nueva.

# Auditoría de sobreingeniería e implementaciones subóptimas — segunda ronda

**Repositorio:** P:\DayZ_MCP_dev  
**Fecha:** 2026-08-22  
**Snapshot revisado:** commit 9cdfc42a971a61e60b8537dac36eb5837ea0e6d3, rama master  
**Alcance:** código y documentación versionados en el snapshot indicado  
**Modalidad:** revisión estática, mediciones estructurales, pruebas unitarias dirigidas y micropruebas aisladas. No se modificó código ni se lanzó DayZ.

---

## 1. Objetivo y relación con la primera ronda

Esta segunda ronda se concentra exclusivamente en:

- complejidad que no parece proporcional al valor actual;
- superficies heredadas que obligan a mantener más de una arquitectura;
- abstracciones o DSL cuyo coste excede su uso comprobado;
- duplicación que crea varias autoridades para la misma decisión;
- implementaciones funcionales pero subóptimas en tiempo, memoria, mantenibilidad o claridad;
- mecanismos cuyo propósito puede ser válido, pero cuya necesidad no está suficientemente demostrada.

Deliberadamente no repito como hallazgos principales los puntos O-01 a O-08 de AUDITORIA_PROFUNDA_2026-08-22.md: el tamaño de build_app, el parche de alias privados de FastMCP, los DTO universales, la duplicación general de transporte bridge, las autoridades Win32, el tamaño de SessionCoordinator, validate_command_args y el predominio general de pruebas de forma. Cuando uno de esos temas aparece aquí, lo hace por evidencia nueva o por una propuesta de simplificación distinta.

### Etiquetas de certeza

- **[CONFIRMADO]**: el patrón o defecto está demostrado directamente por el código, una medición reproducible o una prueba controlada.
- **[PROBABLE]**: la evidencia apunta con fuerza a sobreingeniería, pero la decisión depende de una intención de producto o de consumidores externos no visibles en el repositorio.
- **[POTENCIAL / NO CONFIRMADO]**: existe un riesgo plausible; no se observó una incidencia real o faltan datos operativos para afirmarlo.

### Terminología de impacto

Uso términos concretos: corrupción o ambigüedad de datos, degradación de rendimiento, coste de mantenimiento y riesgo de regresión. No califico nada como crash porque esta ronda no observó la muerte de ningún proceso.

---

## 2. Resumen ejecutivo

La segunda ronda encontró tres decisiones de arquitectura que concentran la mayor oportunidad de simplificación:

1. **[PROBABLE] El repositorio mantiene dos topologías de ejecución y una isla histórica de pruebas de fase.** La documentación presenta cliente/daemon como topología normal y niega el fallback embebido interactivo, pero el CLI todavía predetermina a embedded y conserva 4.533 líneas de runners y clientes POC históricos. Antes de borrar nada hay que inventariar CI y scripts externos.
2. **[PROBABLE] El sistema de playbooks ya tiene la forma de una plataforma extensible, mientras su uso versionado son tres playbooks DRAFT y once pasos.** Hay once operadores, ciclo de certificación, dos caminos live y adaptación mediante internals privados, aunque seis operadores no se usan y ningún playbook está congelado.
3. **[PROBABLE] El inbox de feedback se comporta como un gestor de incidencias incrustado en el MCP principal.** Además del coste de superficie, su identificador supuestamente no colisionable sí puede colisionar y su premisa de append inferior a 4 KiB no es compatible con su propia entrada máxima.

Hay además mejoras más acotadas y de menor riesgo:

- **[CONFIRMADO]** el redactor incremental elimina bytes por el frente de un bytearray y presenta crecimiento superlineal;
- **[CONFIRMADO]** la política normal de daemon está implementada en capas duplicadas;
- **[CONFIRMADO]** existe un ciclo de imports que se rompe mediante import local y tipos debilitados;
- **[CONFIRMADO]** el recuento/listado de herramientas tiene varias autoridades documentales y ya ha derivado;
- **[CONFIRMADO]** hay bootstrap repetido en 49 pruebas y residuos muertos conservados por tests;
- **[CONFIRMADO]** la misma validación UUID4 está copiada cinco veces;
- **[CONFIRMADO]** call_bridge y enqueue_bridge duplican preparación y clasificación de errores dentro de cada runtime;
- **[POTENCIAL]** los aliases y herramientas de bajo nivel pueden aumentar la confusión del consumidor, pero no hay telemetría que demuestre degradación de selección.

Mi recomendación global es reducir primero superficies completas, no “refactorizar por elegancia”. Retirar una topología o un camino live produce más simplificación que introducir nuevas jerarquías para organizar lo existente.

---

## 3. Línea base y verificación realizada

### 3.1 Tamaño medido

Sobre los archivos Python versionados:

- 67 archivos de producción y 44.765 líneas físicas;
- 54 archivos bajo tools/dayz_mcp y 37.087 líneas;
- 13 archivos Python de producción fuera de ese paquete y 7.678 líneas;
- 129 archivos de prueba y 55.584 líneas;
- relación aproximada pruebas/producción por líneas: 1,24;
- 48 funciones de producción superan 100 líneas en el barrido usado y 13 superan 200;
- build_app, en tools/dayz_mcp/server.py:2333, continúa siendo la función mayor, con aproximadamente 1.489 líneas; este dato contextualiza la superficie, pero no se repite aquí como hallazgo.

El volumen de pruebas no es por sí solo evidencia de sobreingeniería. Numerosas pruebas negativas cubren seguridad, identidad de procesos y condiciones de carrera que sí son relevantes.

### 3.2 Pruebas dirigidas

Se ejecutó desde tools:

.\.venv-mcp\Scripts\python.exe -B -m unittest tests.test_playbook_runner tests.test_playbook_tool tests.test_secure_launcher tests.test_pipeline_feedback tests.test_weak_agent_consumer_ux

Resultado: **99 pruebas, OK**, en 1,762 segundos. Por tanto, varios hallazgos de esta auditoría describen coste, duplicación o escalabilidad; no implican que la funcionalidad básica esté actualmente rota.

### 3.3 Pruebas adversariales aisladas

- Se forzó de forma controlada la misma marca temporal y el mismo token aleatorio para dos entradas del inbox. Ambas recibieron el ID fb-20260822-120000-beef. Una resolución posterior se asoció sólo a la segunda entrada; la primera quedó sin resolver.
- Se serializó una entrada legal con 8.000 caracteres á. Su registro JSONL codificado en UTF-8 ocupó 16.311 bytes, no menos de 4 KiB.
- Se midió el redactor incremental con un secreto de 128 bytes. La mediana pasó de 342 ns/byte para 1 KiB a 940 ns/byte para 64 KiB. Un flujo de 1 MiB, entregado en 64 bloques de 16 KiB, tardó 0,580 s, aproximadamente 1,72 MiB/s.

Son micropruebas controladas, no telemetría de producción. Sirven para demostrar mecanismos y tendencias, no para estimar carga real del usuario.

---

## 4. Hallazgos priorizados

## SO-01 — Dos topologías de ejecución y una isla histórica de 4.533 líneas

**Clasificación:** [PROBABLE] sobreingeniería de arquitectura; [CONFIRMADO] coste de superficie.  
**Prioridad:** Alta.  
**Impacto:** coste de mantenimiento, rutas divergentes, pruebas duplicadas y ambigüedad de entrada.

### Evidencia

La intención documentada es una sola topología normal:

- product-spec.md:130-138 define como criterio que las sesiones interactivas usen --client;
- tools/README-mcp.md:46-49 dice explícitamente que no existe fallback embebido interactivo y reserva embedded para CI o retrocompatibilidad.

El CLI, sin embargo, conserva la decisión inversa como default:

- tools/dayz_mcp/server_cli.py:66-88 expone ambos modos y fija mode="embedded" en parser.set_defaults;
- tools/dayz_mcp/server.py:480-484 documenta embedded como valor de retrocompatibilidad en ServerConfig;
- tools/dayz_mcp/server.py:504-707 contiene el runtime embebido;
- tools/dayz_mcp/server.py:3861-3895 mantiene la bifurcación principal entre cliente y embebido.

Además existe una isla histórica completa:

| Archivo | Líneas físicas |
|---|---:|
| tools/mcp_client.py | 1.806 |
| tools/mcp_server.py | 21 |
| tools/run-poc.ps1 | 852 |
| tools/run-fase1.ps1 | 659 |
| tools/run-fase2.ps1 | 568 |
| tools/run-fase3.ps1 | 627 |
| **Total** | **4.533** |

Los runners siguen apuntando a la pareja histórica:

- tools/run-poc.ps1:472-473;
- tools/run-fase1.ps1:357-358;
- tools/run-fase2.ps1:256-257;
- tools/run-fase3.ps1:256-257.

El propio auditor estático describe tools/mcp_client.py como runner histórico emparejado únicamente con el loopback desnudo de mcp_server.py:

- tools/dayz_mcp/security_runtime_audit.py:67-81.

El punto de entrada POC permanece en:

- tools/dayz_mcp/loopback.py:3156-3216, clase LoopbackServer;
- tools/dayz_mcp/loopback.py:3219-3235, main histórico.

También quedan verbos del bridge ligados a esa superficie:

- tools/dayz_mcp/loopback.py:53 y :73 registran vehicle_drive y drive_probe_client;
- tools/dayz_mcp/loopback.py:102 y :110 los exceptúan del esquema normal;
- el único llamador Python de producción encontrado para vehicle_drive es tools/mcp_client.py:1513;
- no se encontró llamador Python de producción para drive_probe_client, aunque el addon todavía los despacha en addon/scripts/5_Mission/MCPBridge.c:468-470 y :702, y addon/scripts/5_Mission/MCPClientBridge.c:580-582 y :694.

### Por qué es sobreingeniería

La retrocompatibilidad ha dejado de ser un adaptador estrecho: obliga a conservar una topología, un runtime, cuatro runners de fase, un cliente histórico, un servidor POC y verbos especiales. El código nuevo debe razonar sobre rutas que la documentación ya no presenta como uso normal.

No puedo confirmar que toda la superficie sea eliminable: CI externo, accesos directos del usuario o automatizaciones fuera del repositorio podrían seguir invocándola. Esa incertidumbre impide llamar a las 4.533 líneas “código muerto”.

### Propuesta [DESIGN]

1. Cambiar el default del CLI a client y exigir --embedded explícito durante una ventana de transición.
2. Inventariar referencias fuera del repositorio: tareas programadas, CI, accesos directos y scripts personales.
3. Mapear cada gate único de run-poc/run-fase1/2/3 a pruebas actuales daemon/client.
4. Si no quedan consumidores, retirar los cuatro runners y la pareja mcp_client.py/mcp_server.py.
5. Después, decidir si el runtime embebido público sigue siendo necesario. Los fixtures in-process pueden sobrevivir como utilidad interna sin mantenerlo como modo de usuario.
6. Retirar los verbos históricos únicamente cuando el addon y cualquier consumidor externo ya no los necesiten.

No propongo crear una interfaz base Runtime ni una fábrica genérica: eso organizaría la duplicación sin reducirla.

### Gate verificable

- búsqueda completa dentro y fuera del repositorio de cada entry point;
- equivalencia de los gates históricos contra la suite actual;
- arranque documentado y tests con client como default;
- cero invocaciones externas conocidas durante la ventana de transición;
- rollback simple: restaurar temporalmente el flag/default, sin migración persistente.

---

## SO-02 — El motor de playbooks es una plataforma general antes de tener carga que la justifique

**Clasificación:** [PROBABLE] sobreingeniería; [CONFIRMADO] divergencia entre superficies.  
**Prioridad:** Alta.  
**Impacto:** mantenimiento, seguridad semántica, acoplamiento a internals y experiencia inconsistente.

### Evidencia

La carga versionada es pequeña:

- playbooks/README.md:22-28 enumera tres playbooks;
- los tres están en estado DRAFT;
- suman once pasos: cuatro, cuatro y tres.

La infraestructura asociada comprende:

- playbooks/runner.py: 834 líneas;
- tools/dayz_mcp/playbook_tool.py: 271 líneas;
- tools/tests/test_playbook_runner.py y tools/tests/test_playbook_tool.py: 933 líneas conjuntas;
- 16 fixtures JSON de prueba.

El modelo ya incorpora una plataforma extensible:

- playbooks/README.md:8-20 define DRAFT → PROBED → CALIBRATED → FROZEN;
- playbooks/README.md:16 y product-spec.md:437-442 muestran que no existe todavía sidecar FROZEN y certified permanece falso;
- playbooks/runner.py:43-60 define once operadores;
- playbooks/README.md:74-77 reconoce que sólo cinco se usan actualmente y seis no tienen consumidor;
- playbooks/runner.py:416 contiene una condicional redundante cuyos dos brazos devuelven la misma expresión.

El adaptador carga el runner dinámicamente:

- tools/dayz_mcp/playbook_tool.py:61-79 usa importlib y sys.modules;
- tools/dayz_mcp/playbook_tool.py:194-228 llega a _tool_manager y fn_metadata, internals privados de FastMCP;
- tools/dayz_mcp/playbook_tool.py:247-269 llama a _async_run, función privada del runner.

Hay además dos caminos live con reglas distintas:

- playbooks/runner.py:671-736 implementa el camino CLI live;
- tools/dayz_mcp/playbook_tool.py:201-207 aplica denylist en el camino MCP;
- playbooks/README.md:90-95 admite que --live del CLI no aplica esa denylist;
- playbooks/runner.py:17-40 fija entorno virtual, puerto, keyfile y client-platform claude;
- playbooks/runner.py:778-796 no ofrece overrides equivalentes. Una ejecución desde Codex por ese CLI quedaría atribuida a Claude.

Las 99 pruebas dirigidas pasan, por lo que el problema principal es la proporción entre infraestructura y uso, no una rotura funcional general.

### Por qué es sobreingeniería

Se han pagado por adelantado operadores, estados de madurez, certificación, importación dinámica y dos adaptadores live para una carga actual de tres borradores. A la vez, el camino general aumenta el riesgo: la denylist difiere según el entry point y el adaptador depende de APIs privadas.

### Propuesta [DESIGN]

Mi opción preferida:

1. Mantener un único camino live: playbook_run dentro de la sesión MCP actual.
2. Deprecar primero --live del CLI; conservar el CLI sólo para parseo, validación y dry-run si sigue aportando valor.
3. Eliminar los seis operadores sin playbooks versionados y reintroducir uno sólo cuando exista un caso concreto.
4. Posponer CALIBRATED/FROZEN y el sidecar de certificación hasta que exista el primer artefacto realmente candidato a congelarse.
5. Sustituir el acceso privado al gestor FastMCP por una tabla explícita y estrecha de callables permitidos al construir la app.

Decisión de producto necesaria:

- si los playbooks son tres checklists internos estables, tres orquestadores Python tipados serían más simples que un DSL;
- si se desea que terceros creen TOML sin tocar Python, conservar TOML, pero con el esquema y operadores mínimos usados hoy.

No recomiendo un framework de plugins, una jerarquía de operadores ni otro motor intermedio.

### Gate verificable

- los tres playbooks actuales producen los mismos pasos y resultados;
- la denylist es idéntica en toda ruta ejecutable;
- la identidad de plataforma procede de la sesión actual;
- ninguna prueba o documento depende de los seis operadores retirados;
- la validación offline continúa sin necesitar daemon ni credenciales.

---

## SO-03 — El pipeline inbox es un gestor de incidencias incrustado y su almacenamiento no cumple sus propias premisas

**Clasificación:** [PROBABLE] scope creep; [CONFIRMADO] colisión posible y premisa de tamaño falsa; [POTENCIAL] interleaving concurrente.  
**Prioridad:** Alta si se conserva; alta oportunidad de simplificación si se externaliza.  
**Impacto:** ambigüedad/corrupción lógica de resoluciones, crecimiento sin límite y superficie pública.

### Evidencia de alcance

- tools/dayz_mcp/inbox.py aporta 158 líneas;
- tools/tests/test_pipeline_feedback.py aporta 198 líneas;
- tools/dayz_mcp/server.py:3715-3795 publica pipeline_feedback, pipeline_inbox y pipeline_resolve;
- tools/README-mcp.md:71-72 los incluye en la superficie normal;
- no encontré en product-spec.md un criterio de aceptación específico para pipeline_*; esto no demuestra que carezca de valor, pero sí que su inclusión en el núcleo no está trazada de forma explícita.

### Identificador que sí puede colisionar

La descripción pública promete que los IDs no pueden colisionar:

- tools/dayz_mcp/server.py:3717-3722.

La implementación usa:

- segundo de reloj más secrets.token_hex(2), es decir, 16 bits aleatorios;
- tools/dayz_mcp/inbox.py:14 y :63-66;
- no existe comprobación de unicidad antes del append.

En una prueba controlada, dos entradas recibieron el mismo ID. append_resolution resolvió sólo la segunda coincidencia, dejando la primera ambigua. Esto confirma el mecanismo de corrupción lógica bajo colisión, aunque no demuestra que haya ocurrido en uso real.

Como referencia probabilística, cien envíos dentro del mismo segundo tendrían alrededor de 7,3 % de probabilidad de al menos una colisión con 65.536 valores. La tasa normal probablemente sea muchísimo menor; no uso ese escenario como estimación operativa.

### Premisa de append incompatible con la entrada permitida

tools/dayz_mcp/inbox.py:28-31 justifica el append directo suponiendo un registro único inferior a 4 KiB. Sin embargo:

- tools/dayz_mcp/inbox.py:57-60 permite body de hasta 8.000 caracteres;
- 8.000 caracteres á produjeron un registro UTF-8 de 16.311 bytes.

Por tanto, la premisa de tamaño está confirmadamente equivocada. No he reproducido interleaving entre procesos en este filesystem, así que una línea JSONL partida o mezclada sigue siendo **[POTENCIAL / NO CONFIRMADA]**.

### Crecimiento

tools/dayz_mcp/inbox.py:104-158 lee el archivo completo, reconstruye todas las entradas, ordena todo y sólo después limita el resultado a un máximo de cien. Tiempo y memoria crecen con todo el historial aunque el consumidor pida pocas entradas.

### Propuesta [DESIGN]

Primero, la alternativa que no cambia formato:

1. Decidir si esta capacidad pertenece al MCP de ejecución. Mi voto es mantener, como máximo, pipeline_feedback append-only o mover las tres operaciones a una herramienta de desarrollo/admin separada.
2. Delegar triage, listado y resolución al tracker que ya use el proyecto, en vez de convertir cada agente MCP en cliente de un segundo gestor de incidencias.
3. Si se conserva JSONL, añadir exclusión mutua y reintento de ID dentro del formato actual, imponer límite por bytes y política explícita de rotación/archivo.
4. Hacer que la lectura limitada no necesite ordenar el historial completo; por ejemplo, leer un segmento reciente y resolver mediante índice sólo si la escala real lo exige.

Si se decide cambiar ID o almacenamiento:

- el lector nuevo debe aceptar IDs y registros legacy;
- el escritor puede usar un ID más robusto;
- un rollback a código anterior podría no resolver IDs nuevos, por lo que hace falta una ventana de compatibilidad o escritura dual;
- antes de una migración destructiva se requiere backup explícito.

No propongo SQLite como primera reacción: para un buzón pequeño, separar la capacidad o corregir el append existente es más simple.

### Gate verificable

- dos escrituras forzadas con la misma fuente de entropía no producen el mismo ID efectivo;
- body máximo ASCII y multibyte se guardan y leen íntegros;
- dos procesos escribiendo simultáneamente no generan JSON inválido;
- historial grande mantiene un coste acotado para limit=100;
- los datos previos siguen siendo legibles y resolubles.

---

## SO-04 — El redactor incremental hace borrado frontal por byte y escala de forma superlineal

**Clasificación:** [CONFIRMADO] implementación subóptima.  
**Prioridad:** Media.  
**Impacto:** degradación de rendimiento en salida abundante; no se observó pérdida de secretos.

### Evidencia

tools/dayz_mcp/secure_launcher.py:27-58:

- procesa la entrada byte a byte;
- elimina coincidencias con del pending[:longitud];
- elimina el primer byte con pending.pop(0) en :57.

Eliminar por el frente de un bytearray desplaza el resto repetidamente. La ruta maneja cuatro patrones en total: UTF-8 y UTF-16LE para cada uno de dos secretos:

- tools/dayz_mcp/secure_launcher.py:106-115 prepara valores UTF-8 y UTF-16.

El backend puede entregar bloques nativos de hasta 16 KiB:

- tools/dayz_mcp/native_launcher_backend.py:1033-1064;
- el límite se aplica en :1051.

Microbenchmark mediano de siete repeticiones con secreto de 128 bytes:

| Entrada | Tiempo | ns/byte |
|---:|---:|---:|
| 1 KiB | 0,350 ms | 342 |
| 4 KiB | 1,846 ms | 451 |
| 8 KiB | 4,457 ms | 544 |
| 16 KiB | 9,426 ms | 575 |
| 32 KiB | 21,705 ms | 662 |
| 64 KiB | 61,627 ms | 940 |

El coste por byte aumenta con el tamaño, coherente con los desplazamientos frontales. Un stream aislado de 1 MiB en bloques de 16 KiB rindió aproximadamente 1,72 MiB/s.

La corrección funcional básica está bien cubierta:

- tools/tests/test_secure_launcher.py:381-399 prueba secretos partidos entre chunks;
- tools/tests/test_secure_launcher.py:403-454 y :456-562 amplían la integración UTF-8/UTF-16;
- todas pasaron en esta ronda.

### Propuesta [DESIGN]

Usar un cursor sobre el buffer, emitir rangos y compactar una sola vez. Conservar sólo la cola de longitud max_secret_len - 1 necesaria para detectar un secreto partido entre chunks.

No recomiendo Aho-Corasick ni una librería de búsqueda múltiple: con cuatro patrones, un escaneo lineal pequeño es suficiente y evita reemplazar una sobrecomplejidad por otra.

### Gate verificable

- conservar exactamente los tests de secretos partidos UTF-8 y UTF-16;
- demostrar que ningún secreto alcanza stdout/stderr ni el resultado;
- comprobar que 64 KiB cuesta aproximadamente no más de cinco veces 16 KiB en el mismo equipo, evitando fijar milisegundos absolutos;
- comparar bytes de salida entre implementación actual y propuesta en un corpus generado.

---

## SO-05 — La política normal de daemon tiene más de una autoridad

**Clasificación:** [CONFIRMADO] duplicación semántica.  
**Prioridad:** Media.  
**Impacto:** riesgo de deriva y coste de revisión en código de autoridad.

### Evidencia

La superficie está repartida entre:

- tools/dayz_mcp/daemon_policy.py, 422 líneas;
- tools/dayz_mcp/normal_daemon_policy.py, 166 líneas;
- tools/dayz_mcp/daemon_policy_contract.py, 136 líneas.

tools/dayz_mcp/normal_daemon_policy.py:1 declara que implementa la política normal sin depender de módulos lifecycle-capable, e importa helpers del contrato en :9-14.

Dos módulos implementan la misma conversión normal de procedencia a política y carga:

- tools/dayz_mcp/daemon_policy.py:290-331;
- tools/dayz_mcp/normal_daemon_policy.py:129-166.

Sus consumidores se dividen:

- tools/dayz_mcp/server.py:38 importa la versión de daemon_policy;
- tools/dayz_mcp/secure_launcher.py:17-19 importa normal_daemon_policy.

Además daemon_policy repite helpers del contrato:

- _valid_text en tools/dayz_mcp/daemon_policy.py:86-93 frente a tools/dayz_mcp/daemon_policy_contract.py:18-25;
- _authority_payload en tools/dayz_mcp/daemon_policy.py:110-130 frente a tools/dayz_mcp/daemon_policy_contract.py:42-62;
- _authority_sha256 en tools/dayz_mcp/daemon_policy.py:133-140 frente a tools/dayz_mcp/daemon_policy_contract.py:65-72;
- _valid_absolute_path en tools/dayz_mcp/daemon_policy.py:96-107 no tiene llamadores de producción encontrados.

La separación de imports para evitar que el launcher cargue lifecycle puede ser válida. La duplicación de la decisión no es necesaria para conservar esa frontera.

### Propuesta [DESIGN]

- daemon_policy_contract debe ser dueño único de datos, payload canónico y hash;
- normal_daemon_policy debe ser dueño único de cargar/serializar política normal;
- daemon_policy debe limitarse al bootstrap lifecycle y reutilizar o reexportar el loader normal;
- conservar la frontera de imports con una prueba que demuestre que secure_launcher no arrastra módulos lifecycle.

No hace falta una jerarquía de políticas ni inyección de dependencias general.

### Gate verificable

- ambos entry points producen objetos y hashes idénticos para fixtures válidos;
- rechazan de forma idéntica payloads alterados;
- secure_launcher conserva su conjunto de imports permitido;
- el formato persistente no cambia.

---

## SO-06 — Ciclo launcher_registry ↔ registry_lock ocultado con import local y tipos debilitados

**Clasificación:** [CONFIRMADO] olor arquitectónico.  
**Prioridad:** Media-baja.  
**Impacto:** acoplamiento, análisis estático debilitado y mantenimiento.

### Evidencia

- tools/dayz_mcp/registry_lock.py:12-15 importa helpers privados _identity_from_stat y _reject_path_name_surrogates desde launcher_registry;
- tools/dayz_mcp/launcher_registry.py:294-305 importa acquire_registry_lock dentro de una función, patrón usado para evitar el ciclo;
- tools/dayz_mcp/launcher_registry.py:63-71 tipa _registry_lock como object;
- tools/dayz_mcp/launcher_registry.py:146-152 necesita ignorar el tipo al llamar close.

En el grafo estático de 54 módulos runtime y 101 aristas internas, éste fue el único componente fuertemente conexo encontrado.

### Propuesta [DESIGN]

Mover exclusivamente _FileIdentity y las primitivas de identidad/stat/path-name a un módulo neutral pequeño. registry_lock dependería de ese módulo, y launcher_registry podría importar el tipo concreto del lock en el nivel superior.

No convertir ese módulo en una colección genérica de filesystem utilities: debe tener una responsabilidad estrecha y estable.

### Gate verificable

- grafo de imports acíclico;
- mypy/pyright o el chequeo estático elegido ya no requiere object/type-ignore en ese campo;
- tests de symlink, hardlink, reemplazo y lock conservan comportamiento.

---

## SO-07 — El catálogo de herramientas tiene varias autoridades documentales y el test permite deriva parcial

**Clasificación:** [CONFIRMADO] implementación subóptima de documentación verificable.  
**Prioridad:** Media-baja.  
**Impacto:** mantenimiento repetitivo y documentación incorrecta.

### Evidencia

El número 54 y/o el catálogo se repiten en:

- README.md:2-4 y :66-78;
- dayz-mcp-architecture.md:10 y :125-130;
- product-spec.md:13-15.

tools/tests/test_install_mcp.py:1263-1287 obtiene herramientas mediante _tool_manager.list_tools(), un miembro privado, y vuelve a construir otra app en :1321-1337.

El gate documental en tools/tests/test_install_mcp.py:1314-1315 sólo exige que cada nombre real aparezca en el texto combinado. Un nombre obsoleto adicional puede sobrevivir y el test seguir verde.

El comentario en tools/tests/test_install_mcp.py:1293-1308 documenta una deriva previa ya publicada. El commit actual volvió a actualizar 53 → 54 en tres documentos. Esto demuestra coste y recurrencia, no sólo una posibilidad teórica.

Existe una vía pública verificada:

- tools/tests/test_weak_agent_consumer_ux.py:35 usa await app.list_tools().

### Propuesta [DESIGN]

- README.md debe ser el único dueño del recuento y del catálogo exacto;
- arquitectura y product spec deben enlazarlo sin copiar el número;
- una prueba debe parsear el bloque canónico y comparar conjuntos exactos, detectando tanto faltantes como sobrantes;
- preferir app.list_tools() público; si el contexto síncrono lo impide, encapsular la única dependencia privada en un helper de tests.

No recomiendo generar fragmentos sincronizados en tres documentos: añadiría una pipeline para sostener una duplicación que puede eliminarse.

### Gate verificable

- igualdad exacta entre catálogo documentado y app.list_tools();
- introducir deliberadamente un nombre extra hace fallar el test;
- cambiar el número sólo requiere editar una fuente.

---

## SO-08 — Bootstrap repetido en 49 tests y residuos muertos fijados por pruebas

**Clasificación:** [CONFIRMADO] deuda mecánica.  
**Prioridad:** Baja, adecuada para un cambio pequeño y aislado.  
**Impacto:** ruido, falsa superficie de configuración y coste de lectura.

### Bootstrap

Se encontraron 49 archivos de prueba que repiten la inserción de tools en sys.path. Sin embargo:

- tools/tests/test_000_path.py:1-9 ya establece un bootstrap temprano;
- README.md:221-226 prescribe ejecutar desde tools;
- tools/pyproject.toml:1-18 define el paquete instalable.

Mantener 49 copias permite que cada archivo diverja y oculta cuál es el contrato de ejecución realmente soportado.

### Configuración y helpers sin consumidor de producción

- ServerConfig.session_ttl_s y runtime_dir sólo se definen en tools/dayz_mcp/server.py:497-498; la única referencia adicional encontrada es una aserción de defaults en tools/tests/test_client_mode.py:1067-1068.
- _offset_before_last_lines aparece en tools/dayz_mcp/server.py:1634-1657 sin llamadores; la variante activa empieza en :1660 y se usa en :1714.
- BindingRegistry en tools/dayz_mcp/instance_fence.py:122-126 no tiene consumidor encontrado.
- compute_bridge_ready conserva una rama terminal no_run en tools/dayz_mcp/server.py:386 que queda cubierta estructuralmente por retornos anteriores.
- tools/dayz_mcp/playbook_tool.py:122-126 tiene ramas que producen el mismo resultado.

También se detectaron imports/constantes sin uso aparente:

- tools/dayz_mcp/daemon_policy.py:14 y :19;
- tools/dayz_mcp/orphan_guard.py:28 y :53;
- tools/dayz_mcp/native_launcher_backend.py:14-17;
- tools/dayz_mcp/request_path_authority.py:7;
- tools/dayz_mcp/server.py:18, :22 y :39.

Cada candidato debe volver a verificarse contra imports dinámicos y plataforma Windows antes de retirarse; “sin referencia textual” no equivale siempre a muerto.

### Propuesta [DESIGN]

- elegir un único contrato soportado de imports de tests;
- retirar las 49 inserciones si test_000_path y el comando oficial bastan;
- si se exige ejecutar cualquier archivo individual directamente, usar un bootstrap compartido, no 49 implementaciones;
- eliminar defaults, helpers e imports únicamente tras characterization test y búsqueda de consumo dinámico.

Este trabajo debe ser una limpieza pequeña, no una campaña de refactor adyacente.

### Gate verificable

- unittest discover desde el directorio documentado;
- ejecución individual de un test sólo si se declara soporte;
- importación de todos los módulos runtime;
- búsqueda de nombres y suite completa antes/después;
- cero cambios de comportamiento o formato.

---

## SO-09 — Validación UUID4 copiada cinco veces

**Clasificación:** [CONFIRMADO] duplicación pequeña.  
**Prioridad:** Baja.  
**Impacto:** deriva de semántica y mensajes de error.

### Evidencia

La misma validación de UUID versión 4 aparece en:

- tools/dayz_mcp/dayz_test_readiness.py:54;
- tools/dayz_mcp/dayz_test_request.py:78;
- tools/dayz_mcp/dayz_test_tool.py:227;
- tools/dayz_mcp/process_lifecycle.py:42;
- tools/dayz_mcp/runtime_state.py:689.

Las implementaciones son esencialmente equivalentes, con pequeñas diferencias en orden de excepciones y contexto.

### Propuesta [DESIGN]

Crear un helper de dominio estrecho para parsear/validar UUID4 y hacer que los cinco consumidores añadan sólo el contexto de su error.

No crear un módulo utilities genérico; identifiers.py o un módulo de tipos de dominio sería suficiente.

### Gate verificable

- matriz común: UUID4 válido, otra versión, mayúsculas, espacios, string inválido, tipo no string;
- conservar los contratos públicos de error donde formen parte de tests o respuestas MCP.

---

## SO-10 — call_bridge y enqueue_bridge repiten la misma preparación dentro de cada runtime

**Clasificación:** [CONFIRMADO] duplicación local; simplificación condicionada.  
**Prioridad:** Media-baja.  
**Impacto:** riesgo de que llamada síncrona y encolada diverjan.

### Evidencia

En el runtime embebido:

- tools/dayz_mcp/server.py:604-618, Runtime.call_bridge;
- tools/dayz_mcp/server.py:658-672, Runtime.enqueue_bridge.

En el runtime cliente:

- tools/dayz_mcp/server.py:1198-1239, ClientRuntime.call_bridge;
- tools/dayz_mcp/server.py:1286-1327, ClientRuntime.enqueue_bridge.

Dentro de ClientRuntime se repiten construcción de payload, lease, manejo de lease obsoleto y clasificación de estado/liveness. call_bridge añade después la espera, que es la diferencia esencial.

### Propuesta [DESIGN]

Extraer dentro de cada runtime un helper privado _enqueue_once con deadline explícito. call_bridge debe ser “encolar + esperar” y enqueue_bridge sólo “encolar”, compartiendo la preparación.

Preservar:

- un único presupuesto temporal extremo a extremo;
- el camino especial threaded de exec_enforce;
- la clasificación exacta de stale lease y liveness.

No recomiendo una clase base común entre Runtime y ClientRuntime mientras siga abierta la decisión de SO-01: primero reducir topologías; después simplificar lo que sobreviva.

### Gate verificable

- mismos payloads y errores para call/enqueue;
- timeout consumido desde un solo deadline;
- fixtures de stale lease, daemon muerto y exec_enforce;
- ningún cambio de protocolo ni formato persistente.

---

## SO-11 — Superficie pública redundante: posible coste cognitivo aún no medido

**Clasificación:** [POTENCIAL / NO CONFIRMADO].  
**Prioridad:** Observación; no retirar sin evidencia.  
**Impacto posible:** selección incorrecta de herramientas por agentes y documentación más difícil.

### Evidencia

- tools/dayz_mcp/server.py:2437-2441 presenta lease_acquire como alias directo de session_acquire_wait;
- tools/dayz_mcp/server.py:2380-2461 conserva cuatro operaciones de sesión de bajo nivel y recomienda preferir la operación de alto nivel;
- tools/tests/test_weak_agent_consumer_ux.py:35-60 fija intencionadamente parte de esta superficie como ayuda a consumidores débiles.

Un alias puede mejorar descubribilidad y las primitivas de bajo nivel pueden ser necesarias para recuperación. No hay telemetría de tool selection, errores de agentes o uso por cliente que permita afirmar que sobran.

### Propuesta [DESIGN]

- registrar durante un periodo qué nombres invocan realmente los consumidores, sin registrar secretos ni argumentos sensibles;
- medir errores de selección y dependencia externa;
- si lease_acquire no aporta llamadas únicas, deprecar el alias con transición documental;
- conservar primitivas de recuperación si tienen consumidores o escenarios diferenciados.

No recomiendo eliminar herramientas sólo para reducir el número 54.

---

## 5. Candidatos descartados o rebajados

Una auditoría de sobreingeniería también debe evitar simplificaciones peligrosas.

### 5.1 No sustituir automáticamente listas por deque

Algunas colas usan operaciones frontales, pero el tamaño está acotado a MAX_QUEUE=64 y existen insert/index. La ganancia sería marginal y deque complicaría otras operaciones. No lo reporto como problema material.

### 5.2 No reducir el validador de trazas de vehículo sólo porque es largo

La traza valida un artefacto independiente consumido por el engine. No encontré evidencia de que sea un verificador tautológico. Su tamaño necesita revisión funcional de dominio, no una poda por métrica.

### 5.3 No tratar las 55.584 líneas de tests como desperdicio

El ratio 1,24 es alto, pero seguridad, identidad, locks, rutas Win32 y regresiones de bridge justifican fixtures negativos. Esta ronda sólo señala duplicaciones concretas o pruebas que fijan estado muerto.

### 5.4 No retirar capas de seguridad nativa por “simplicidad”

No se evaluó un threat model que autorice eliminar controles de identidad, handles, hardlinks, reparse points o herencia. Simplificar autoridades duplicadas no equivale a debilitar invariantes.

### 5.5 No llamar eliminables a los runners históricos sin inventario externo

La ausencia de referencias internas demuestra aislamiento, no ausencia de consumidores externos. SO-01 queda deliberadamente como [PROBABLE].

---

## 6. Plan recomendado por retorno/riesgo

### Fase A — Decisiones de producto antes de refactor

1. Resolver si embedded y los runners de fase siguen siendo producto soportado.
2. Resolver si playbooks pretende ser DSL extensible por terceros o sólo tres procedimientos internos.
3. Resolver si pipeline inbox pertenece al runtime MCP o a tooling de proyecto.

Estas tres decisiones pueden eliminar superficies enteras. Refactorizarlas antes de decidir perpetuaría el coste.

### Fase B — Simplificaciones acotadas y verificables

1. Optimizar _IncrementalRedactor con cursor y compactación única.
2. Unificar la autoridad de política normal sin alterar formato.
3. Romper el ciclo launcher_registry/registry_lock con primitivas neutrales estrechas.
4. Establecer un único dueño del catálogo de herramientas.
5. Consolidar UUID4 y limpiar bootstrap/residuos confirmados.
6. Compartir enqueue dentro de cada runtime sólo si ambos runtimes sobreviven a la Fase A.

### Fase C — Retirada gradual

1. Poner client como default.
2. Marcar entry points históricos como deprecated con logging limitado.
3. Observar una ventana acordada e inventariar consumidores.
4. Retirar rutas sin uso y sus tests específicos.
5. Actualizar la documentación canónica y conservar un rollback sencillo durante la transición.

---

## 7. Priorización resumida

| ID | Certeza | Retorno esperado | Riesgo de cambio | Decisión previa |
|---|---|---:|---:|---|
| SO-01 | Probable / superficie confirmada | Muy alto | Alto | Sí: soporte legacy |
| SO-02 | Probable | Alto | Medio-alto | Sí: intención del DSL |
| SO-03 | Probable + defectos confirmados | Alto | Medio | Sí: pertenencia al core |
| SO-04 | Confirmado | Medio | Bajo-medio | No |
| SO-05 | Confirmado | Medio | Medio | No |
| SO-06 | Confirmado | Bajo-medio | Bajo-medio | No |
| SO-07 | Confirmado | Medio recurrente | Bajo | No |
| SO-08 | Confirmado | Bajo-medio | Bajo | No |
| SO-09 | Confirmado | Bajo | Bajo | No |
| SO-10 | Confirmado | Medio | Medio | Sí, después de SO-01 |
| SO-11 | Potencial | Desconocido | Medio | Sí: telemetría |

### Mi orden concreto

1. Inventario externo y decisión de SO-01.
2. Decisión de producto sobre SO-02.
3. Separar o corregir urgentemente SO-03.
4. Ejecutar SO-04 y SO-07 como mejoras pequeñas de retorno claro.
5. Consolidar SO-05 y romper SO-06 con pruebas de caracterización.
6. Agrupar SO-08 y SO-09 en una limpieza pequeña, sin refactor incidental.
7. Aplicar SO-10 sólo a la arquitectura superviviente.
8. Medir SO-11 antes de tocarlo.

---

## 8. Limitaciones y asuntos no confirmados

- No se lanzó DayZ ni se hizo prueba in-game.
- No se adquirió lease porque la auditoría fue de sólo lectura y no gestionó procesos compartidos.
- No se ejecutó un cliente MCP externo real ni se midió selección de herramientas por modelos.
- No existe telemetría de producción para cuantificar frecuencia del inbox, volumen de stdout o uso de runners legacy.
- El addon está parcialmente sparse en el workspace; las referencias citadas se verificaron sobre los blobs del commit HEAD.
- No se inspeccionaron tareas programadas, CI remoto, accesos directos o repositorios externos que puedan consumir entry points históricos.
- El microbenchmark del redactor aísla el algoritmo y no representa latencia completa del launcher.
- La posible escritura JSONL intercalada no se reprodujo; se confirmó únicamente que la premisa de menos de 4 KiB es falsa y que el diseño no demuestra atomicidad.
- Ninguna estimación de líneas “eliminables” se presenta como ahorro garantizado. Las 4.533 líneas de SO-01 son superficie medida, no un compromiso de borrado.

---

## 9. Conclusión

El mayor problema de simplicidad no está en funciones individuales, sino en **arquitecturas completas mantenidas en paralelo antes de confirmar que siguen siendo producto**: topología embebida/POC, plataforma general de playbooks y tracker de feedback dentro del MCP.

La secuencia correcta es decidir qué superficies deben existir, retirar las que no, y sólo después refactorizar lo superviviente. En los cambios locales, las oportunidades más claras son el redactor incremental, la autoridad duplicada de política, el ciclo de imports y la documentación multiautoridad.

No se recomienda una “gran rearquitectura” ni introducir nuevos frameworks. La simplificación de mayor calidad aquí consiste en menos modos, menos entry points, menos autoridades y contratos más estrechos.
