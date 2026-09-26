# Codex — Technical Handoff / auditoria estatica del repo publico `willy92wins/dayz-mcp` (2026-08-17)

> **Origen**: `DayZ-MCP_Technical_Handoff_2026-08-17.docx` (Codex, presupuesto extraordinario de tokens; el usuario lo
> trajo el 17-08 ~17:00). Copia binaria al lado: `2026-08-17-codex-technical-handoff-repo-audit.docx`. Este .md es la
> extraccion de texto (parrafos + tablas) hecha por Claude con `docx_to_md.py`; los permalinks [C-xx] del original no
> sobreviven a la extraccion (el indice A.1 abajo mapea cada [C-xx] a su fichero).
> **Snapshot revisado**: commit `31040de` (16-08). **Desfase conocido**: el repo/arbol ya se movio (playbooks/, `uid` en
> teleport/give/notify, `entities_query`, `action_use`, cookbook docs; 39 tools → 48). Cada hallazgo se re-verifica contra
> el arbol ACTUAL antes de aceptarlo (G2 / DZ-R2.1).
> **Estado**: SIN TRIAR. Lectura preliminar de Claude (no decisiones) en `HANDOFF.md` LIVE-STATE cierre 15 y en
> `30_Sessions/2026-08-17-DayZ_MCP-batch6-buzon-playbooks.md` §Parte 4. La sesion siguiente separa grano/paja y decide.

---

| TECHNICAL HANDOFF |
|---|

# DayZ-MCP
Revisión técnica, seguridad, experiencia de agentes y roadmap
Documento de traspaso para continuar el desarrollo del repositorio con contexto suficiente, prioridades claras, criterios de aceptación y trazabilidad completa hacia el código revisado.
| Repositorio | willy92wins/dayz-mcp |
|---|---|
| Snapshot revisado | 31040de18d5b745235003ea23a039125fce9fbe5 |
| Fecha de revisión | 17 de agosto de 2026 |
| Tipo de revisión | Estática: código fuente, documentación y metadatos del repositorio |
| Estado recomendado | Alpha pública avanzada; no declarar estable hasta cerrar P1 y release engineering |
| Audiencia | Mantenedor, siguiente agente, reviewer de seguridad y contributor técnico |

|  | Regla de trazabilidad Toda afirmación factual sobre el código actual termina con una referencia [C-xx] enlazada a un permalink del commit revisado. Las propuestas de futuro se marcan como recomendaciones y se apoyan, cuando procede, en referencias normativas [N-xx]. |
|---|---|

Preparado como handoff; no sustituye una auditoría dinámica de DayZ/Windows.
## Contenido
| 1 | Resumen ejecutivo y decisión |
|---|---|
| 2 | Alcance, método y límites |
| 3 | Arquitectura entendida y fortalezas |
| 4 | Registro priorizado de hallazgos |
| 5 | Hallazgos P1: corrección inmediata |
| 6 | Hallazgos P2/P3: producto, mantenimiento y operación |
| 7 | Plan para minimizar fricción de agentes |
| 8 | Funciones adicionales recomendadas |
| 9 | Roadmap de implementación |
| 10 | Primer PR recomendado y definición de done |
| 11 | Plan de pruebas |
| 12 | Instrucciones para el siguiente agente |
| A | Índice de fuentes de código y referencias normativas |

## 1. Resumen ejecutivo y decisión
DayZ-MCP resuelve un problema técnicamente difícil: cerrar el bucle autónomo de desarrollo de mods y permitir que un agente opere una instancia DayZ mediante tools tipadas, estado estructurado y control autoritativo, sin depender de teclado u OCR.
Evidencia de código: [C-01] Descripción del producto, 39 tools, arquitectura, instalación, seguridad y tests · [C-14] MCPBridge.TryInit, polling, GetFirstHuman y comandos de servidor · [C-15] MCPClientBridge.TryInit y comprobación de prefijo loopback · [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos
|  | Decisión recomendada Mantener el núcleo arquitectónico. No rediseñar desde cero. Antes de promocionar el proyecto como estable, cerrar cuatro hallazgos P1, añadir CI/release engineering y reducir la superficie cognitiva para agentes mediante tools de intención y operaciones atómicas. |
|---|---|

### 1.1 Evaluación resumida
| Dimensión | Valoración | Motivo | Evidencia |
|---|---|---|---|
| Originalidad / valor | Alta | El producto combina bridge Enforce, daemon compartido, tools MCP y ejecución de escenarios. | [C-01] [C-14] [C-15] [C-17] |
| Arquitectura | Fuerte | Separación clara entre servidor, cliente renderizado, daemon, lifecycle y coordinación. | [C-01] [C-23] [C-24] [C-25] |
| Seguridad interna | Fuerte con gaps | Loopback, key, whitelist, TTL y auditoría están presentes; quedan fronteras inconsistentes. | [C-16] [C-26] [C-27] [C-30] |
| Pruebas | Muy amplia | La suite publicada cubre concurrencia, lifecycle, credenciales, seguridad, tools y captura. | [C-02] [C-31] |
| Portabilidad pública | Baja hoy | El instalador está vinculado a rutas y hashes de una máquina concreta. | [C-10] [C-12] [C-13] |
| Fricción para agentes | Media-alta | La capacidad existe, pero el agente debe coordinar demasiadas primitivas y estados. | [C-01] [C-17] [C-23] |
| Madurez de release | Insuficiente | No hay workflows públicos, protección de main, tags ni releases; versión 0.0.0. | [C-03] [C-04] [C-05] [C-06] [C-07] |

### 1.2 Riesgo agregado
No se identificó un P0 evidente durante la revisión estática. Esta conclusión significa únicamente que no apareció una ruta obvia de ejecución remota no autenticada o una credencial real versionada; no demuestra ausencia de vulnerabilidades dinámicas.
Los cuatro P1 son corregibles sin sustituir la arquitectura: portabilidad y privacidad del instalador, enforcement loopback del bridge servidor, límites del HTTP local y coherencia MIME de WebP.
El mayor retorno posterior no vendrá de añadir veinte primitivas más, sino de empaquetar las capacidades existentes en una experiencia: instalar una vez, verificar readiness y pedir una intención completa.
### 1.3 Resultado esperado tras el roadmap
Experiencia objetivo
| Usuario:  dayz-mcp setup --register
Agente:   dayz_ready()
Agente:   scenario_run(spec=...)
Sistema:  adquiere lease, prepara escena, ejecuta, observa, valida y limpia |
|---|

## 2. Alcance, método y límites
### 2.1 Snapshot y materiales revisados
La revisión se fijó al commit 31040de18d5b745235003ea23a039125fce9fbe5. El mensaje del commit declara 1.253 tests, cero fallos, cero errores y 28 skips en la máquina autora; ese resultado no fue reproducido de forma independiente en esta revisión.
Evidencia de código: [C-02] Commit 31040de; mensaje de verificación y lote de correcciones
Se revisaron la superficie FastMCP, el servidor HTTP loopback, daemon y lifecycle, coordinación por leases, bridges Enforce, instalación, launcher, captura, documentación, fixtures de seguridad y estructura de tests.
Evidencia de código: [C-14] MCPBridge.TryInit, polling, GetFirstHuman y comandos de servidor · [C-15] MCPClientBridge.TryInit y comprobación de prefijo loopback · [C-16] Handler._read_json, auth, whitelist, colas, TTL y servidor HTTP · [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos · [C-23] Leases, cola FIFO, operaciones, cleanup y auditoría · [C-24] Ciclo de vida de procesos y recuperación · [C-25] Daemon compartido, arranque, estado y watchdog · [C-31] Suite de pruebas publicada
### 2.2 Método
- Lectura de la arquitectura declarada y contraste con la superficie implementada.
- Trazado de fronteras de confianza: cliente MCP, daemon, HTTP local, bridge servidor, bridge cliente y sistema de archivos.
- Revisión de validación de argumentos, lifecycle, concurrencia, recuperación y persistencia.
- Revisión de instalación, dependencias, reproducibilidad y publicación.
- Evaluación de ergonomía para agentes: número de pasos, estado explícito, errores, artefactos y operaciones largas.
- Comparación de propuestas con MCP 2025-11-25 y prácticas oficiales de pip/GitHub.
### 2.3 Límites y no-afirmaciones
- No se ejecutó DayZDiag, DayZ Tools, el launcher nativo ni los bridges dentro del motor.
- No se reprodujo la suite completa en Windows.
- No se realizó fuzzing de HTTP, race testing dinámico, análisis de binarios ni revisión de ACL reales de una instalación.
- Las severidades reflejan impacto y probabilidad bajo el modelo loopback/local del proyecto; pueden cambiar tras pruebas dinámicas.
### 2.4 Convenciones del handoff
| P0 | Bloqueo inmediato: compromiso crítico o pérdida grave de control. |
|---|---|
| P1 | Debe corregirse antes de declarar release estable o impulsar adopción pública. |
| P2 | Importante para producto, operación o mantenimiento; planificar en el siguiente ciclo. |
| P3 | Mejora de robustez, calidad o mantenibilidad. |
| Confianza alta | La ruta está directamente presente en el código o metadatos revisados. |
| Confianza media | El riesgo se infiere de una combinación de rutas, sin ejecución dinámica. |
| [C-xx] | Fuente de código/repositorio fijada al snapshot o estado de GitHub. |
| [N-xx] | Referencia normativa o documentación oficial externa. |

## 3. Arquitectura entendida y fortalezas
### 3.1 Flujo principal
| Capa | Responsabilidad | Interfaz / estado | Evidencia |
|---|---|---|---|
| Cliente MCP | Expone tools tipadas y adapta errores/resultados para el agente. | FastMCP sobre stdio. | [C-17] |
| Cliente proxy | Descubre o arranca el daemon y reenvía llamadas autenticadas. | HTTP loopback hacia el daemon. | [C-17] [C-25] [C-27] |
| Daemon único | Posee el puerto, coordina sesiones y lifecycle compartido. | Colas, leases, auditoría y watchdog. | [C-23] [C-24] [C-25] |
| Bridge servidor | Ejecuta comandos autoritativos en MissionServer y devuelve estado. | Poll de comandos y POST de resultados. | [C-14] |
| Bridge cliente | Controla cámara, captura y acciones que requieren cliente renderizado. | Poll separado como peer client. | [C-15] |
| Launcher / lifecycle | Valida rutas, construye/arranca procesos y mantiene manifiestos. | Política, registro, run_id y recuperación. | [C-24] [C-28] [C-29] [C-36] [C-37] |

### 3.2 Fortalezas que deben preservarse
La separación server/client evita fingir que toda capacidad de DayZ es homogénea: las operaciones autoritativas viven en el servidor y la cámara/captura dependen de un cliente renderizado.
Evidencia de código: [C-01] Descripción del producto, 39 tools, arquitectura, instalación, seguridad y tests · [C-14] MCPBridge.TryInit, polling, GetFirstHuman y comandos de servidor · [C-15] MCPClientBridge.TryInit y comprobación de prefijo loopback
El daemon compartido evita que varias sesiones MCP compitan por el mismo puerto y utiliza coordinación explícita mediante leases, tickets FIFO, heartbeat, release y cleanup.
Evidencia de código: [C-23] Leases, cola FIFO, operaciones, cleanup y auditoría · [C-25] Daemon compartido, arranque, estado y watchdog
El servidor HTTP autentica con comparación constante, limita la superficie a una whitelist y aplica colas acotadas, TTL de comandos y flush de reconexión.
Evidencia de código: [C-16] Handler._read_json, auth, whitelist, colas, TTL y servidor HTTP
La ejecución arbitraria de Enforce no forma parte de la whitelist normal: se activa explícitamente, exige allowlist exacta y registra decisiones de auditoría.
Evidencia de código: [C-16] Handler._read_json, auth, whitelist, colas, TTL y servidor HTTP · [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos
El proyecto contiene mecanismos específicos para keyfiles, refresh de credenciales, autoridad de rutas, procesos huérfanos y auditoría de runtime.
Evidencia de código: [C-26] Validación endurecida de keyfile · [C-27] Carga, refresh y recuperación de credenciales · [C-29] Autoridad y validación de rutas de solicitudes · [C-30] Auditoría de seguridad en runtime
La suite publicada está orientada a condiciones reales de fallo, incluyendo carreras, credenciales, lifecycle, port reclaim, seguridad del daemon, tools y captura.
Evidencia de código: [C-31] Suite de pruebas publicada
|  | Principio de diseño a conservar Mantener el modelo fail-closed y el control autoritativo. Las mejoras de UX no deben eliminar las garantías; deben esconder la coordinación interna detrás de operaciones de intención y transacciones seguras. |
|---|---|

## 4. Registro priorizado de hallazgos
| ID | Sev. | Resumen | Conf. | Fuentes |
|---|---|---|---|---|
| P1-01 | P1 | Instalador no portable y fixtures con datos de la máquina autora | Alta | C-10, C-12, C-13 |
| P1-02 | P1 | El bridge servidor no aplica la política loopback que sí intenta aplicar el cliente | Alta | C-14, C-15, C-01 |
| P1-03 | P1 | HTTP local acepta Content-Length negativo o no acotado | Alta | C-16 |
| P1-04 | P1 | WebP se genera como WebP y se publica como PNG en la capa FastMCP | Alta | C-17, C-18, C-19 |
| P2-01 | P2 | Sin CI público, protección de main, tags ni releases | Alta | C-03, C-04, C-05, C-06, C-07 |
| P2-02 | P2 | Lock de dependencias Python incompleto | Alta | C-08, C-09, C-10, C-11 |
| P2-03 | P2 | Dos rutas de instalación con requisitos y efectos secundarios distintos | Alta | C-01, C-11, C-20, C-32 |
| P2-04 | P2 | Documentación principal desincronizada | Alta | C-01, C-20, C-21, C-22 |
| P2-05 | P2 | Mutaciones de jugador usan el primer humano en vez de una referencia estable | Alta | C-14, C-17 |
| P2-06 | P2 | wait_for permite pattern vacío, contadores negativos y timeout no positivo | Alta | C-17 |
| P2-07 | P2 | capture_screenshot permite escribir full-res en cualquier directorio accesible | Alta | C-17, C-18 |
| P3-01 | P3 | Dependencia de APIs privadas de FastMCP | Alta | C-17 |
| P3-02 | P3 | Módulos grandes y contratos duplicados en tres capas | Media | C-14, C-16, C-17, C-23, C-24, C-41 |
| P3-03 | P3 | Validaciones y límites menores pendientes | Media | C-14, C-17, C-18 |

Los P1 se describen como defectos o fronteras incumplidas directamente observables en el snapshot. Los P2/P3 combinan defectos de producto, riesgos operativos y deuda de mantenibilidad.
## 5. Hallazgos P1: corrección inmediata
| P1-01  Instalador no portable y fixtures con datos de una estación real |
|---|
| Prioridad: P1 | Confianza: Alta | Esfuerzo: M | Estado: Pendiente |

### Qué ocurre
El instalador Python resuelve un manifiesto dentro del propio repositorio y valida para Claude y Codex una ruta absoluta, el nombre del ejecutable, el tamaño, la forma PE x64 y el SHA-256 exacto.
Evidencia de código: [C-10] INSTALLER_CLI_MANIFEST, validación PE/ruta/tamaño/hash y registro
El manifiesto publicado contiene rutas bajo C:\Users\guill y hashes/tamaños concretos de los ejecutables instalados en la máquina autora.
Evidencia de código: [C-12] Rutas, tamaños y hashes de Claude/Codex de la máquina autora
El fixture de “not found” conserva en stderr el listado de MCPs configurados en esa máquina, incluyendo AnswerOverflow, Gmail, Google Calendar, Google Drive y dayz-mcp.
Evidencia de código: [C-13] Salida capturada con MCPs configurados en la máquina autora
### Impacto
Otra máquina, versión o ruta legítima de Claude/Codex puede fallar la validación antes de registrar el servidor. Además, el repositorio publica información local innecesaria, aunque no se observe una credencial secreta en esos fixtures.
Evidencia de código: [C-10] INSTALLER_CLI_MANIFEST, validación PE/ruta/tamaño/hash y registro · [C-12] Rutas, tamaños y hashes de Claude/Codex de la máquina autora · [C-13] Salida capturada con MCPs configurados en la máquina autora
### Remediación propuesta
- Eliminar los manifiestos generados en una estación real y reemplazarlos por fixtures totalmente sintéticos.
- Descubrir localmente los clientes durante setup y generar el manifiesto vivo bajo %LOCALAPPDATA%\DayZ_MCP.
- Separar los fixtures de test del estado operativo; los tests deben crear ejecutables PE sintéticos o usar mocks.
- Añadir comandos setup, doctor, upgrade y uninstall bajo un único instalador canónico.
- Valorar una reescritura del historial si se desea retirar las rutas y el inventario de MCPs también de commits anteriores.
### Criterios de aceptación
- ☐ Una cuenta Windows con nombre distinto instala y registra el MCP sin editar ficheros del repo.
- ☐ No existe ninguna cadena C:\Users\guill en el árbol versionado ni en fixtures activos.
- ☐ Actualizar Claude o Codex no exige cambiar el repositorio.
- ☐ El instalador muestra qué binario encontró y pide consentimiento antes de fijar su identidad local.
Superficie a modificar: [C-10] INSTALLER_CLI_MANIFEST, validación PE/ruta/tamaño/hash y registro · [C-12] Rutas, tamaños y hashes de Claude/Codex de la máquina autora · [C-13] Salida capturada con MCPs configurados en la máquina autora
| P1-02  El bridge servidor no fuerza loopback |
|---|
| Prioridad: P1 | Confianza: Alta | Esfuerzo: S-M | Estado: Pendiente |

### Qué ocurre
MCPBridge.TryInit comprueba que cfg.url y cfg.key no estén vacíos, copia la URL y crea el RestContext; no valida el esquema, host o puerto.
Evidencia de código: [C-14] MCPBridge.TryInit, polling, GetFirstHuman y comandos de servidor
MCPClientBridge.TryInit sí rechaza URLs que no comiencen por http://127.0.0.1:, por lo que la frontera se aplica de forma inconsistente entre peers.
Evidencia de código: [C-15] MCPClientBridge.TryInit y comprobación de prefijo loopback
El README presenta el producto como loopback-only, sin remote mode y ejecutado en la misma máquina que DayZ.
Evidencia de código: [C-01] Descripción del producto, 39 tools, arquitectura, instalación, seguridad y tests
### Impacto
Una configuración modificada o mal copiada del bridge servidor puede dirigir polls y resultados a un endpoint remoto, transportando la key en query string y datos estructurados del juego. La precondición es acceso previo al fichero de configuración, pero la garantía publicada deja de ser una propiedad del código.
Evidencia de código: [C-14] MCPBridge.TryInit, polling, GetFirstHuman y comandos de servidor · [C-01] Descripción del producto, 39 tools, arquitectura, instalación, seguridad y tests
### Remediación propuesta
Contrato compartido propuesto
| validate_base_url(url):
  scheme == "http"
  host == "127.0.0.1"
  port in 1..65535 and decimal only
  path == "/"
  no userinfo, query or fragment
  otherwise: fail closed |
|---|

- Implementar el mismo validador estricto en ambos bridges; un prefijo no basta para validar una URL completa.
- Separar credenciales del bridge servidor, bridge cliente, cliente MCP y administración para reducir blast radius.
- Añadir pruebas positivas y negativas en Enforce y Python para host, puerto, userinfo, fragmentos y rutas extra.
### Criterios de aceptación
- ☐ Ambos bridges aceptan únicamente una base URL canónica de loopback.
- ☐ http://127.0.0.1.evil.invalid:, userinfo@127.0.0.1, puertos no numéricos y rutas extra son rechazados.
- ☐ La documentación describe exactamente la validación implementada.
Superficie a modificar: [C-14] MCPBridge.TryInit, polling, GetFirstHuman y comandos de servidor · [C-15] MCPClientBridge.TryInit y comprobación de prefijo loopback · [C-01] Descripción del producto, 39 tools, arquitectura, instalación, seguridad y tests
| P1-03  Lectura HTTP no acotada y manejo incompleto del cuerpo |
|---|
| Prioridad: P1 | Confianza: Alta | Esfuerzo: S | Estado: Pendiente |

### Qué ocurre
Handler._read_json convierte Content-Length a entero y pasa el valor directamente a rfile.read sin comprobar límite inferior, máximo permitido ni longitud real recibida.
Evidencia de código: [C-16] Handler._read_json, auth, whitelist, colas, TTL y servidor HTTP
La misma función captura JSONDecodeError, pero no UnicodeDecodeError; el servidor se basa en ThreadingHTTPServer sin un pool de workers acotado declarado en esa clase.
Evidencia de código: [C-16] Handler._read_json, auth, whitelist, colas, TTL y servidor HTTP
### Impacto
Content-Length negativo puede convertir la lectura en “hasta EOF”, y un valor enorme o un emisor lento puede ocupar un hilo y memoria. Debido al bind loopback y la key, el riesgo principal es denegación de servicio local autenticada, no exposición remota directa.
Evidencia de código: [C-16] Handler._read_json, auth, whitelist, colas, TTL y servidor HTTP
### Remediación propuesta
Pseudocódigo de hardening
| MAX_REQUEST_BODY_BYTES = 256 * 1024

if length < 0:        return 400
if length > MAX:      return 413
raw = read_exact(length, timeout=...)
if len(raw) != length:return 400
catch UnicodeError, OverflowError, OSError |
|---|

- Añadir timeout de lectura y un límite de concurrencia por worker pool o semáforo.
- Definir Content-Type aceptado y devolver errores JSON estables.
### Pruebas mínimas
- ☐ Content-Length = -1.
- ☐ Content-Length superior al límite.
- ☐ UTF-8 inválido.
- ☐ Cuerpo incompleto respecto al Content-Length.
- ☐ JSON válido pero no objeto.
- ☐ Cliente que abre la conexión y deja de transmitir.
Superficie a modificar: [C-16] Handler._read_json, auth, whitelist, colas, TTL y servidor HTTP
| P1-04  Inconsistencia MIME en capturas WebP |
|---|
| Prioridad: P1 | Confianza: Alta | Esfuerzo: S | Estado: Pendiente |

### Qué ocurre
mcp_capture codifica WebP cuando fmt=webp y declara mimeType image/webp.
Evidencia de código: [C-18] _mime_for, encode_bytes, resolve_capture_dir y write_fullres
La tool capture_screenshot convierte únicamente image/jpeg a formato jpeg; cualquier otro MIME se envuelve como png, por lo que bytes WebP pueden publicarse con metadata PNG.
Evidencia de código: [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos
La suite de captura comprueba JPEG y PNG a nivel de encoder/captura, pero no aparece un test del recorrido completo WebP -> respuesta FastMCP con validación simultánea de MIME y magic bytes.
Evidencia de código: [C-19] Cobertura de captura JPEG/PNG y ventana; ausencia del recorrido FastMCP WebP
### Impacto
El cliente puede rechazar, interpretar mal o perder la imagen. El problema está limitado a la opción WebP, que no es el default, pero es una opción pública de la tool.
Evidencia de código: [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos · [C-18] _mime_for, encode_bytes, resolve_capture_dir y write_fullres
### Remediación propuesta
- Preferencia: devolver un ImageContent con el MIME exacto, sin forzar la abstracción Image cuando no soporta WebP.
- Alternativa temporal: retirar WebP del schema público o transcodificar explícitamente a PNG/JPEG antes de devolverlo.
- Añadir un test que compruebe MIME, magic header, apertura con Pillow y resultado serializado por MCP.
MCP permite contenido de imagen con mimeType explícito y también enlaces a resources, por lo que la corrección puede mantener compatibilidad sin etiquetar bytes incorrectamente.
Evidencia de código: [N-01] outputSchema, annotations, structuredContent, resource links y taskSupport
### Criterios de aceptación
- ☐ Los bytes y el MIME coinciden para JPEG, PNG y WebP.
- ☐ El test atraviesa la tool pública, no solo el encoder auxiliar.
- ☐ La opción no soportada por un cliente falla con error accionable, no con contenido corrupto.
Superficie a modificar: [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos · [C-18] _mime_for, encode_bytes, resolve_capture_dir y write_fullres · [C-19] Cobertura de captura JPEG/PNG y ventana; ausencia del recorrido FastMCP WebP
## 6. Hallazgos P2/P3: producto, mantenimiento y operación
| P2-01  Release engineering insuficiente |
|---|
| Prioridad: P2 | Confianza: Alta | Esfuerzo: M | Estado: Pendiente |

GitHub no mostraba workflows en el repositorio, main figuraba sin protección ni required status checks, no existían tags o releases y el paquete declaraba versión 0.0.0.
Evidencia de código: [C-03] Estado de workflows en la fecha de revisión · [C-04] protected y required status checks · [C-05] Listado de releases · [C-06] Listado de tags · [C-07] Versión, Python mínimo y dependencias declaradas
El mensaje del commit declara una suite verde, pero sin CI público ese resultado no se reproduce automáticamente para cada cambio.
Evidencia de código: [C-02] Commit 31040de; mensaje de verificación y lote de correcciones · [C-03] Estado de workflows en la fecha de revisión
- Añadir workflow Windows con Python 3.10, 3.12 y 3.14; separar tests offline, integración y gates que requieren DayZ.
- Activar branch protection, revisión obligatoria y checks requeridos.
- Publicar wheel y launcher con versión semántica, SBOM y atestación de procedencia.
GitHub documenta atestaciones de builds y SBOM verificables; encajan especialmente bien con el launcher nativo reproducible.
Evidencia de código: [N-07] Procedencia de builds, SBOM y verificación
Superficie a modificar: [C-03] Estado de workflows en la fecha de revisión · [C-04] protected y required status checks · [C-05] Listado de releases · [C-06] Listado de tags · [C-07] Versión, Python mínimo y dependencias declaradas · [C-37] Construcción del launcher nativo y reproducibilidad
| P2-02  Lock de dependencias Python incompleto |
|---|
| Prioridad: P2 | Confianza: Alta | Esfuerzo: M | Estado: Pendiente |

requirements-mcp.txt y pyproject.toml fijan tres dependencias directas, pero no contienen hashes ni el grafo transitivo completo.
Evidencia de código: [C-08] Dependencias directas fijadas · [C-07] Versión, Python mínimo y dependencias declaradas
dependency-lock.json fija el wheel vendorizado de psutil, CPython embebido y toolchains, pero no actúa como lock completo de todas las dependencias Python instaladas.
Evidencia de código: [C-09] Artefactos, CPython embebido y toolchains fijados
Los instaladores ejecutan pip install sobre requirements y una instalación editable del proyecto.
Evidencia de código: [C-10] INSTALLER_CLI_MANIFEST, validación PE/ruta/tamaño/hash y registro · [C-11] Invoke-Python, venv, pip y registro
- Generar locks completos por versión de Python con todos los wheels transitivos y SHA-256.
- Instalar releases con --require-hashes, --only-binary :all: y el wheel del proyecto con --no-deps.
- Reservar el editable para desarrollo y separar lock portable de toolchain-lock.local.json ignorado por Git.
La guía oficial de pip exige hashes para todas las dependencias cuando se activa hash-checking y recomienda --only-binary :all: para bloquear sdists.
Evidencia de código: [N-06] --require-hashes, --only-binary :all: y dependencias transitivas
Superficie a modificar: [C-07] Versión, Python mínimo y dependencias declaradas · [C-08] Dependencias directas fijadas · [C-09] Artefactos, CPython embebido y toolchains fijados · [C-10] INSTALLER_CLI_MANIFEST, validación PE/ruta/tamaño/hash y registro · [C-11] Invoke-Python, venv, pip y registro · [C-33] Reescritura de la sección toolchains del lock
| P2-03  Instalación duplicada y requisitos contradictorios |
|---|
| Prioridad: P2 | Confianza: Alta | Esfuerzo: M | Estado: Pendiente |

El README raíz indica Python 3.10+ y propone install-mcp.ps1 -Register; la documentación de tools desarrolla otra ruta basada en install_mcp.py y enfatiza Python 3.14.
Evidencia de código: [C-01] Descripción del producto, 39 tools, arquitectura, instalación, seguridad y tests · [C-20] Instalación detallada y estado documentado de hardening
Invoke-Python usa py -3.14 cuando existe el launcher py y retorna inmediatamente, por lo que no implementa fallback si py existe pero Python 3.14 no está instalado.
Evidencia de código: [C-11] Invoke-Python, venv, pip y registro
La configuración de host contempla timeouts globales muy elevados para Claude/Codex, y el instalador aplica esa política como parte del registro.
Evidencia de código: [C-10] INSTALLER_CLI_MANIFEST, validación PE/ruta/tamaño/hash y registro · [C-32] Configuración de host y timeouts de Claude/Codex
- Unificar en dayz-mcp setup, usando el intérprete actual siempre que cumpla >=3.10.
- Hacer explícitos los cambios globales del host con flags y confirmación; no aplicarlos silenciosamente.
- PowerShell debe ser un wrapper fino del instalador canónico, no una segunda implementación.
Superficie a modificar: [C-01] Descripción del producto, 39 tools, arquitectura, instalación, seguridad y tests · [C-10] INSTALLER_CLI_MANIFEST, validación PE/ruta/tamaño/hash y registro · [C-11] Invoke-Python, venv, pip y registro · [C-20] Instalación detallada y estado documentado de hardening · [C-32] Configuración de host y timeouts de Claude/Codex
| P2-04  Documentación desincronizada |
|---|
| Prioridad: P2 | Confianza: Alta | Esfuerzo: M | Estado: Pendiente |

README.md presenta la superficie actual de 39 tools, mientras dayz-mcp-architecture.md y product-spec.md conservan una superficie anterior y lenguaje de diseño histórico.
Evidencia de código: [C-01] Descripción del producto, 39 tools, arquitectura, instalación, seguridad y tests · [C-21] Documento de arquitectura histórica / superficie anterior · [C-22] Contrato histórico, número anterior de tools y referencias internas
product-spec.md también remite a materiales internos que no forman parte de la publicación, y tools/README-mcp.md conserva tareas descritas como pendientes que ya tienen implementación parcial en el código.
Evidencia de código: [C-20] Instalación detallada y estado documentado de hardening · [C-22] Contrato histórico, número anterior de tools y referencias internas · [C-14] MCPBridge.TryInit, polling, GetFirstHuman y comandos de servidor · [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos
- Separar docs/current de docs/history y etiquetar explícitamente los documentos históricos.
- Generar tool-reference.md desde el registro FastMCP: schema, output, riesgo, precondiciones, errores y ejemplo.
- Añadir SECURITY.md, CONTRIBUTING.md, CHANGELOG.md y política de versiones.
Superficie a modificar: [C-01] Descripción del producto, 39 tools, arquitectura, instalación, seguridad y tests · [C-20] Instalación detallada y estado documentado de hardening · [C-21] Documento de arquitectura histórica / superficie anterior · [C-22] Contrato histórico, número anterior de tools y referencias internas
| P2-05  Selección inestable de jugadores y objetos |
|---|
| Prioridad: P2 | Confianza: Alta | Esfuerzo: M-L | Estado: Pendiente |

El bridge expone query_all_players, pero varias mutaciones resuelven el actor mediante GetFirstHuman; el helper devuelve el primer elemento de GetPlayers.
Evidencia de código: [C-14] MCPBridge.TryInit, polling, GetFirstHuman y comandos de servidor
Las tools públicas de teleport, inventory y otras operaciones no exponen un player_ref estable al agente.
Evidencia de código: [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos
- Introducir referencias opacas: player_ref, object_ref y vehicle_ref, basadas en UID/net identity/run ownership.
- Cero candidatos debe devolver not_found; más de uno, ambiguous_target con candidatos sanitizados.
- Toda mutación debe devolver la referencia exacta afectada y permitir idempotencia cuando sea viable.
Superficie a modificar: [C-14] MCPBridge.TryInit, polling, GetFirstHuman y comandos de servidor · [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos
| P2-06  wait_for puede satisfacer condiciones equivocadas |
|---|
| Prioridad: P2 | Confianza: Alta | Esfuerzo: S | Estado: Pendiente |

execute_wait_for define pattern vacío por defecto, valida únicamente que sea string y evalúa pattern in line; en Python, la cadena vacía coincide con cualquier línea nueva.
Evidencia de código: [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos
value se valida como entero pero no como no negativo, y timeout_s se limita por máximo sin exigir un valor positivo.
Evidencia de código: [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos
- Exigir pattern no vacío en log_matches; regex solo con longitud y timeout acotados.
- Exigir value >= 0, timeout_s > 0 y máximo explícito de poll_interval_s.
- Añadir condiciones estructuradas: object_exists, player_at_position, inventory_contains, vehicle_speed_at_least, peer_ready y animation_phase.
Superficie a modificar: [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos
| P2-07  Captura full-res con ruta arbitraria |
|---|
| Prioridad: P2 | Confianza: Alta | Esfuerzo: S-M | Estado: Pendiente |

La tool pública capture_screenshot acepta save_dir y lo reenvía al backend de captura.
Evidencia de código: [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos
resolve_capture_dir prioriza la ruta explícita, la normaliza a absoluta y write_fullres crea el directorio y escribe un JPEG con nombre basado en timestamp.
Evidencia de código: [C-18] _mime_for, encode_bytes, resolve_capture_dir y write_fullres
- Retirar save_dir de la superficie pública y fijar un capture root acreditado.
- Crear ficheros de forma exclusiva con UUID, cuotas, retención y limpieza por run/session.
- Devolver un resource link dayz://captures/{id}, no una ruta absoluta del host.
MCP permite que una tool devuelva enlaces a resources; esto evita gastar contexto y desacopla al agente del filesystem local.
Evidencia de código: [N-01] outputSchema, annotations, structuredContent, resource links y taskSupport · [N-03] Resources, templates, subscriptions y URIs
Superficie a modificar: [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos · [C-18] _mime_for, encode_bytes, resolve_capture_dir y write_fullres
| P3-01  Uso de APIs privadas de FastMCP |
|---|
| Prioridad: P3 | Confianza: Alta | Esfuerzo: S | Estado: Pendiente |

_patch_public_argument_alias accede a app._tool_manager y modifica tool.parameters y tool.fn_metadata.call_fn_with_arg_validation.
Evidencia de código: [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos
- Sustituir por alias de Pydantic o por un nombre público no reservado, usando APIs soportadas.
- Añadir test de schema público para evitar regresiones al actualizar mcp.
Superficie a modificar: [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos
| P3-02  Concentración de lógica y contratos duplicados |
|---|
| Prioridad: P3 | Confianza: Media | Esfuerzo: L | Estado: Pendiente |

El snapshot contiene módulos de gran tamaño, entre ellos server.py, loopback.py, session_coordination.py y process_lifecycle.py; la validación de varios comandos aparece tanto en la tool FastMCP como en el daemon y en el bridge Enforce.
Evidencia de código: [C-16] Handler._read_json, auth, whitelist, colas, TTL y servidor HTTP · [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos · [C-23] Leases, cola FIFO, operaciones, cleanup y auditoría · [C-24] Ciclo de vida de procesos y recuperación · [C-14] MCPBridge.TryInit, polling, GetFirstHuman y comandos de servidor · [C-41] Tamaños y distribución de módulos en el snapshot revisado
- Separar tools por dominio: world, players, vehicles, camera, lifecycle y sessions.
- Definir contratos en una fuente común y generar schema MCP, validación del daemon, documentación y fixtures; mantener en Enforce únicamente la defensa final necesaria.
- No hacer el refactor en el mismo PR que los P1; primero congelar contratos con tests.
Superficie a modificar: [C-14] MCPBridge.TryInit, polling, GetFirstHuman y comandos de servidor · [C-16] Handler._read_json, auth, whitelist, colas, TTL y servidor HTTP · [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos · [C-23] Leases, cola FIFO, operaciones, cleanup y auditoría · [C-24] Ciclo de vida de procesos y recuperación · [C-41] Tamaños y distribución de módulos en el snapshot revisado
| P3-03  Hardening menor pendiente |
|---|
| Prioridad: P3 | Confianza: Media | Esfuerzo: M | Estado: Pendiente |

world_time_set limita day a 1..31, pero no valida combinaciones reales de calendario; varias cadenas/listas públicas carecen de máximos de longitud explícitos.
Evidencia de código: [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos
Los bridges aceptan pollHz si es positivo, sin un máximo visible, y TryInit se invoca en cada tick mientras el bridge siga sin configurar.
Evidencia de código: [C-14] MCPBridge.TryInit, polling, GetFirstHuman y comandos de servidor · [C-15] MCPClientBridge.TryInit y comprobación de prefijo loopback
Los nombres de captura se basan en segundos y milisegundos, por lo que dos capturas concurrentes pueden colisionar teóricamente.
Evidencia de código: [C-18] _mime_for, encode_bytes, resolve_capture_dir y write_fullres
- Validar fechas reales, longitudes, cardinalidades y máximos de pollHz.
- Aplicar backoff al init fallido y creación exclusiva de capturas.
- Añadir rate limiting o un límite de requests concurrentes al HTTP local.
Superficie a modificar: [C-14] MCPBridge.TryInit, polling, GetFirstHuman y comandos de servidor · [C-15] MCPClientBridge.TryInit y comprobación de prefijo loopback · [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos · [C-18] _mime_for, encode_bytes, resolve_capture_dir y write_fullres
## 7. Plan para minimizar al máximo la fricción de agentes
Objetivo: el agente debe razonar sobre DayZ y el test, no sobre puertos, keys, tickets FIFO, heartbeats, rutas de profiles o lifecycle del daemon.
### 7.1 Experiencia objetivo
Flujo feliz propuesto
| dayz-mcp setup --register

dayz_ready()
scenario_run({
  "project": "ExampleMod",
  "scene": {...},
  "actions": [...],
  "assertions": [...],
  "cleanup": "always"
}) |
|---|

### 7.2 Un setup de un solo paso
- Detectar DayZ y DayZ Tools mediante Steam/registro y localizar profiles/missions.
- Comprobar Python, Visual Studio Build Tools y Windows SDK.
- Descubrir clientes MCP instalados sin depender de rutas versionadas.
- Crear credenciales y ACL, instalar bridge/launcher y registrar el MCP.
- Ejecutar un smoke test y devolver un diagnóstico estructurado único.
El instalador actual ya contiene piezas separadas para registro, launcher, configuración de host y doctor; la propuesta es convertirlas en una transacción coherente y portable.
Evidencia de código: [C-10] INSTALLER_CLI_MANIFEST, validación PE/ruta/tamaño/hash y registro · [C-11] Invoke-Python, venv, pip y registro · [C-32] Configuración de host y timeouts de Claude/Codex · [C-35] Extensiones de vehículo y control · [C-36] Bootstrap e instalación del registro de launchers · [C-37] Construcción del launcher nativo y reproducibilidad
- Modo quick: descargar una release firmada/atestada y verificarla.
- Modo reproducible: recompilar localmente, relockear toolchain y verificar doble build.
### 7.3 Dos superficies de tools
| Superficie | Tools | Público | Objetivo |
|---|---|---|---|
| dayz-mcp-simple | 8-12 tools de intención | Agentes generales y usuarios | Resolver tareas completas con pocos pasos y errores accionables. |
| dayz-mcp-expert | Primitivas actuales | Modders avanzados, debugging y automatización especializada | Conservar control fino, diagnósticos y composición manual. |

La superficie actual registra tools individuales para mundo, jugador, vehículo, cámara, lifecycle y sesión; conservarlas como expert evita perder capacidad mientras se añade una capa simple.
Evidencia de código: [C-01] Descripción del producto, 39 tools, arquitectura, instalación, seguridad y tests · [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos
### 7.4 Ocultar leases en el camino normal
dayz_test_run ya encapsula la propiedad del lease y heartbeat dentro de la operación, mientras las tools session_* exponen coordinación explícita.
Evidencia de código: [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos · [C-23] Leases, cola FIFO, operaciones, cleanup y auditoría
- Toda mutación simple adquiere y libera automáticamente cuando no existe una transacción activa.
- scenario_run mantiene un lease interno durante toda la operación y ejecuta cleanup en finally.
- Los tokens nunca se devuelven al modelo; session_* queda en expert/admin.
- Un deadman y el cleanup del daemon siguen siendo la última línea de defensa.
### 7.5 Tasks para operaciones largas
La operación actual mantiene llamadas y timeouts prolongados para launch, cola y espera; MCP 2025-11-25 introduce Tasks como estados durables con polling y recuperación de resultado.
Evidencia de código: [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos · [C-23] Leases, cola FIFO, operaciones, cleanup y auditoría · [C-24] Ciclo de vida de procesos y recuperación · [N-02] Tasks experimentales, negociación de capacidades y lifecycle
- Negociar capabilities; usar execution.taskSupport=optional o required solo cuando el cliente lo soporte.
- Conservar fallback síncrono/acotado para clientes sin Tasks.
- Vincular task_id a la identidad/sesión y aplicar TTL, cancelación y control de acceso.
|  | Importante Tasks figura como característica experimental en la especificación 2025-11-25. Debe implementarse detrás de capability negotiation y no reemplazar el fallback hasta validar clientes reales. Evidencia: [N-02] Tasks experimentales, negociación de capacidades y lifecycle |
|---|---|

### 7.6 Contrato de resultados accionable
Envelope propuesto
| {
  "ok": false,
  "code": "client_peer_not_ready",
  "retryable": true,
  "message": "El bridge cliente aún no ha hecho poll.",
  "remediation": {
    "action": "wait",
    "tool": "dayz_ready",
    "after_seconds": 2
  }
} |
|---|

La implementación actual tipa numerosos errores, pero algunas rutas degradan a bad_args, remote_error o códigos que el agente debe interpretar sin remediation estructurada.
Evidencia de código: [C-16] Handler._read_json, auth, whitelist, colas, TTL y servidor HTTP · [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos
- Usar un catálogo único de errores con retryable, actor responsable, estado observado y siguiente acción.
- Incluir outputSchema y structuredContent validable en cada tool.
- Declarar title, readOnlyHint, destructiveHint, idempotentHint y openWorldHint según el comportamiento real.
La especificación MCP permite outputSchema, structuredContent, anotaciones y taskSupport por tool.
Evidencia de código: [N-01] outputSchema, annotations, structuredContent, resource links y taskSupport
### 7.7 Resources, prompts y completion
| Capacidad | Propuesta |
|---|---|
| Resources | dayz://status, dayz://capabilities, dayz://players, dayz://runs/{id}, dayz://runs/{id}/logs, dayz://artifacts/{id}, dayz://captures/{id}, dayz://projects |
| Prompts | /dayz-smoke-test, /dayz-debug-mod, /dayz-stage-event, /dayz-vehicle-regression, /dayz-recover-run |
| Completion | project, mission, run_id, player_ref, object_ref, classname, animation_source |

Resources permiten exponer contenido y artefactos mediante URIs, prompts permiten flujos reutilizables y completion reduce errores de argumentos.
Evidencia de código: [N-03] Resources, templates, subscriptions y URIs · [N-04] Prompts descubribles y parametrizables · [N-05] Autocompletado de argumentos
### 7.8 Operaciones compuestas y atómicas
Hoy un escenario visual puede requerir spawn, preparación de vehículo, teleport, entrada, motor, cámara, espera, captura, restore y release como llamadas separadas.
Evidencia de código: [C-01] Descripción del producto, 39 tools, arquitectura, instalación, seguridad y tests · [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos
- camera_capture debe colocar cámara, esperar estabilidad, capturar y restaurar, todo dentro de una única transacción.
- scene_apply debe preparar el mundo declarativamente y devolver un diff de lo creado/modificado.
- scenario_run debe ejecutar acciones y assertions con cleanup garantizado y artefactos asociados.
## 8. Funciones adicionales recomendadas
### 8.1 Prioridad máxima
| Función | Responsabilidad | Prioridad |
|---|---|---|
| dayz_ready() | Consolida daemon, peers, versiones, run, ventana, launcher, profiles y siguiente acción. | Muy alta |
| dayz_repair(actions, dry_run=true) | Repara configuración/registro/PBO/procesos conocidos con preview y evidencia. | Muy alta |
| player_resolve(query) | Resuelve identidad estable y evita el primer jugador. | Muy alta |
| object_find(filters) | Devuelve referencias estables y rechaza ambigüedad. | Muy alta |
| scene_apply(spec, dry_run=false) | Aplica escena declarativa bajo una transacción. | Muy alta |
| camera_capture(target, framing, restore=true) | Cámara + estabilidad + captura + restore atómico. | Muy alta |
| scenario_run(spec) | Pasos, assertions, reintentos, artifacts y cleanup. | Muy alta |
| assert_state(condition) | Assertions estructuradas reutilizables. | Alta |
| scene_cleanup(scope) | Limpia solo recursos creados por run/session. | Alta |
| artifact_get(ref) | Entrega logs, traces y capturas como resources. | Alta |

### 8.2 Desarrollo de mods
- config_search: Buscar classnames, slots, animation sources y memory points para evitar nombres inventados.
- config_inspect: Mostrar clase padre, scope, modelo, attachments, cargo y propiedades relevantes.
- mod_build_check: Sintaxis/config, build PBO, launch, análisis de logs y diagnóstico resumido.
- log_diagnose: Convertir RPT/script logs en errores tipados con archivo, línea y causa probable.
- baseline_record: Guardar screenshot, estado, telemetría y logs como baseline versionada.
- baseline_compare: Comparar una ejecución con baseline y producir diff estructurado.
El repositorio ya dispone de build/launch, log tail, captura y vehicle trace; estas funciones componen capacidades existentes en contratos de alto nivel.
Evidencia de código: [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos · [C-24] Ciclo de vida de procesos y recuperación · [C-34] Resolución, lectura y límites de logs · [C-37] Construcción del launcher nativo y reproducibilidad
### 8.3 Vehículos
| Función | Uso |
|---|---|
| vehicle_spawn_ready | Spawn, debug prep, fluidos y estado mínimo listo. |
| vehicle_repair / vehicle_refuel | Fixture determinista y mantenimiento de escenario. |
| vehicle_set_fluids / vehicle_set_health | Preparación precisa de regresiones. |
| vehicle_drive_sequence | Secuencia temporal declarativa; evita muchas llamadas de control. |
| vehicle_drive_to | Conducción a objetivo con límites y abort conditions. |
| vehicle_trace_compare | Compara trace actual con fixture/baseline. |
| vehicle_collision_probe | Escenario controlado de colisión y telemetría. |
| vehicle_reset | Detiene control, recoloca y deja el vehículo seguro. |

La base ya contiene control owner-side, telemetry, trace y extensiones de CarScript; la propuesta añade orquestación y assertions.
Evidencia de código: [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos · [C-18] _mime_for, encode_bytes, resolve_capture_dir y write_fullres · [C-35] Extensiones de vehículo y control
### 8.4 Operación de servidor: perfil separado
El README presenta usos administrativos como players, staging, time/weather y messaging, pero las mutaciones críticas deben separarse del perfil de desarrollo.
Evidencia de código: [C-01] Descripción del producto, 39 tools, arquitectura, instalación, seguridad y tests · [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos
- player_message, player_kick y player_ban.
- server_lock, server_unlock, server_save, server_backup y server_restart.
- event_schedule y event_cancel.
- whitelist_manage.
|  | Control de riesgo Kick, ban, restore, backup y restart deben requerir perfil admin, scopes explícitos, confirmación humana y audit trail. No deben aparecer en la superficie simple por defecto. |
|---|---|

### 8.5 Observabilidad
- metrics_snapshot: estado puntual de latencias, colas, peers, runs y leases.
- health_history: evolución de readiness y reconexiones.
- queue_metrics: profundidad, espera y expiración por peer.
- command_audit: historial sanitizado de tool -> command -> result.
- peer_events: conexión, versión, key reload y liveness.
- run_timeline: fases, procesos, artifacts y cleanup de un run_id.
El código ya registra timestamps de enqueue/result, queue depth, poll ages, credential recovery y lifecycle; falta exponerlo como observabilidad coherente.
Evidencia de código: [C-16] Handler._read_json, auth, whitelist, colas, TTL y servidor HTTP · [C-23] Leases, cola FIFO, operaciones, cleanup y auditoría · [C-24] Ciclo de vida de procesos y recuperación · [C-25] Daemon compartido, arranque, estado y watchdog · [C-39] Estado persistente del runtime
## 9. Roadmap de implementación
| Fase | Objetivo | Entregables | Cubre |
|---|---|---|---|
| Fase 0 | Privacidad y limpieza | Eliminar artefactos de estación real; fixtures sintéticos; decidir reescritura de historial. | P1-01 |
| Fase 1 | Defectos P1 | Instalador portable, URL estricta, HTTP acotado, WebP, wait_for y regresiones. | P1-01..04 + P2-06 |
| Fase 2 | Release fiable | CI Windows, protección de rama, versionado, releases, lock con hashes, SBOM/attestation, docs actuales. | P2-01..04 |
| Fase 3 | UX de agentes | dayz_ready, auto-leases, simple/expert, errores accionables, schemas, Tasks, Resources/Prompts/Completion. | Plan §7 |
| Fase 4 | Capacidad de alto nivel | Refs estables, scene_apply, camera_capture, scenario_run, assertions, baselines y diagnósticos. | Plan §8 |
| Fase 5 | Refactor interno | Separación por dominios y generación de contratos tras congelar compatibilidad. | P3-02 |

### 9.1 Dependencias entre fases
- Cerrar P1-01 antes de publicar binarios o pedir pruebas a terceros: el setup actual no es portable.
- Cerrar P1-02/P1-03 antes de ampliar administración o remote orchestration.
- Añadir CI antes del refactor de módulos; la suite debe actuar como contrato de comportamiento.
- Introducir entity refs antes de scene_apply/scenario_run para evitar consolidar el patrón “first player”.
- Añadir resources/artifacts antes de baselines para no devolver rutas locales ni blobs excesivos.
### 9.2 Orden de ownership sugerido
| Workstream | Owner ideal | Archivos principales |
|---|---|---|
| Installer / packaging | Python + Windows packaging | install_mcp.py, install-mcp.ps1, host_config.py, pyproject/locks |
| Bridge security | Enforce + seguridad | MCPBridge.c, MCPClientBridge.c, config contract |
| Daemon HTTP | Python backend | loopback.py, daemon.py, tests HTTP |
| MCP API / UX | MCP + producto | server.py, contracts, resources/prompts/tasks |
| Release engineering | CI / supply chain | .github/workflows, build launcher, SBOM/attestation |
| Docs | Maintainer técnico | README, docs/current, generated tool reference |

## 10. Primer PR recomendado y definición de done
|  | Nombre sugerido hardening: portable setup, strict loopback, bounded HTTP and capture MIME |
|---|---|

### 10.1 Scope obligatorio
- ☐ Sustituir los manifiestos reales por fixtures sintéticos y generar identidad local durante setup.
- ☐ Aplicar validación estricta de loopback en ambos bridges.
- ☐ Acotar y endurecer Handler._read_json.
- ☐ Corregir WebP o retirarlo temporalmente de la superficie pública.
- ☐ Corregir wait_for: pattern, value, timeout y tests.
- ☐ Añadir un workflow Windows visible que ejecute la suite offline.
### 10.2 Fuera de scope
- Refactor masivo de server.py/session_coordination.py.
- Nuevas funciones administrativas.
- Remote mode.
- Migración completa a Tasks.
- Rediseño de todos los schemas de tools.
### 10.3 Definición de done
| Área | Criterio |
|---|---|
| Portabilidad | Instala desde una cuenta Windows distinta sin editar rutas y sin referencias a C:\Users\guill. |
| Privacidad | No queda inventario real de MCPs ni rutas personales en fixtures/versionado. |
| Loopback | Todos los casos no canónicos son rechazados por server y client bridge. |
| HTTP | Negativo, oversize, UTF-8 inválido, body incompleto y slow body fallan 4xx/timeout sin hang. |
| Captura | MIME y magic bytes coinciden para todos los formatos públicos. |
| wait_for | pattern vacío y counts/timeouts inválidos fallan localmente con código claro. |
| CI | Workflow Windows verde y requerido antes de merge. |
| Docs | README muestra un único setup y limitaciones actualizadas. |

Hallazgos que justifican el PR: [C-10] INSTALLER_CLI_MANIFEST, validación PE/ruta/tamaño/hash y registro · [C-12] Rutas, tamaños y hashes de Claude/Codex de la máquina autora · [C-13] Salida capturada con MCPs configurados en la máquina autora · [C-14] MCPBridge.TryInit, polling, GetFirstHuman y comandos de servidor · [C-15] MCPClientBridge.TryInit y comprobación de prefijo loopback · [C-16] Handler._read_json, auth, whitelist, colas, TTL y servidor HTTP · [C-17] Registro FastMCP, wait_for, capture_screenshot y alias de argumentos · [C-18] _mime_for, encode_bytes, resolve_capture_dir y write_fullres · [C-03] Estado de workflows en la fecha de revisión
### 10.4 Commits sugeridos
- test: add synthetic installer and negative HTTP fixtures
- fix(installer): discover and pin local MCP clients
- fix(bridge): enforce canonical loopback URL on both peers
- fix(http): bound request bodies and decode failures
- fix(capture): preserve exact image MIME
- fix(wait): reject ambiguous wait conditions
- ci: add Windows offline gate
- docs: consolidate setup and security guarantees
## 11. Plan de pruebas
### 11.1 Pirámide de pruebas
| Nivel | Qué valida | Dónde |
|---|---|---|
| Unitarias | Parsers, schemas, URL, Content-Length, MIME, errores y refs. | Python unittest + tests de contrato Enforce. |
| Integración local | Cliente MCP -> daemon -> HTTP -> fake peers. | FakePeer y fixtures existentes. |
| Windows host | Instalación, registro, procesos, ACL, launcher y rollback. | GitHub Actions self-hosted/VM o job Windows sin DayZ cuando sea posible. |
| In-game gate | Bridge real, spawn, camera, vehicle, restore y logs. | Máquina de integración con DayZDiag. |
| Supply chain | Hashes, wheel, launcher reproducible, SBOM y attestation. | Workflow de release. |

### 11.2 Matriz de regresión P1
| Componente | Caso | Esperado |
|---|---|---|
| Installer | CLI en otra ruta/versión válida | Descubre, confirma y genera manifest local. |
| Installer | CLI cambiado después del pin | Falla de forma explícita y ofrece re-accreditation. |
| URL | 127.0.0.1 canónico | Aceptado. |
| URL | host remoto / userinfo / fragment / path extra | Rechazado antes de crear RestContext. |
| HTTP | Content-Length -1 | 400; conexión cerrada. |
| HTTP | Cuerpo > MAX | 413 sin leer todo el cuerpo. |
| HTTP | UTF-8 inválido | 400 JSON estable. |
| HTTP | Slow/incomplete body | Timeout/400; worker liberado. |
| Capture | fmt=webp | MIME image/webp y magic RIFF/WEBP, o error “unsupported”. |
| wait_for | log_matches + pattern vacío | bad_args local. |

### 11.3 Gates recomendados
- unittest discover sobre toda la suite offline.
- Test de packaging: pyproject, requirements y lock sincronizados.
- Ruff/format y type check gradual.
- CodeQL y secret scanning.
- Verificación de ausencia de rutas personales y snapshots operativos.
- Build reproducible del launcher en job dedicado.
- Smoke de registro Claude/Codex contra fixtures sintéticos.
La suite existente y los scripts de build/launcher proporcionan una base amplia; el primer objetivo de CI es hacer visible y repetible el gate offline ya declarado.
Evidencia de código: [C-02] Commit 31040de; mensaje de verificación y lote de correcciones · [C-31] Suite de pruebas publicada · [C-37] Construcción del launcher nativo y reproducibilidad
## 12. Instrucciones para el siguiente agente
### 12.1 Estado que debe asumir
- Trabajar siempre contra el commit base 31040de18d5b745235003ea23a039125fce9fbe5 o revalidar todo hallazgo que toque archivos cambiados.
- No afirmar que los 1.253 tests están reproducidos hasta ejecutar el gate en una máquina adecuada.
Evidencia de código: [C-02] Commit 31040de; mensaje de verificación y lote de correcciones
- No cambiar la arquitectura autoritativa ni relajar fail-closed para reducir fricción.
- No mezclar el refactor de módulos con el PR de P1; primero tests y fixes acotados.
- Tratar rutas, manifiestos y capturas como datos sensibles del host, aunque no contengan passwords.
### 12.2 Primeras acciones
- Crear una rama hardening/p1-public-release.
- Añadir tests que fallen para P1-01..04 y P2-06 antes del cambio productivo.
- Sustituir fixtures reales por datos sintéticos.
- Implementar fixes en commits separados y revisar el diff de seguridad.
- Añadir CI Windows y publicar el resultado de la suite.
- Actualizar README y crear CHANGELOG de la primera versión candidata.
### 12.3 Preguntas que requieren decisión del mantenedor
- ☐ ¿Se reescribe el historial para retirar las rutas/listados reales o basta con corregir main?
- ☐ ¿La release quick distribuirá launcher precompilado o obligará a reproducible local build?
- ☐ ¿Qué clientes MCP forman parte del soporte oficial inicial?
- ☐ ¿Se mantendrá embedded mode como camino público o solo como testing/back-compat?
- ☐ ¿Qué operaciones se permiten en servidores live y cuáles quedan restringidas a DayZDiag/dev?
- ☐ ¿Cuál será el identificador estable de player/object/vehicle entre server y client bridge?
### 12.4 Señales de que el trabajo está listo para pasar a Fase 3
- ☐ Setup limpio en una VM Windows nueva.
- ☐ CI pública verde y main protegida.
- ☐ P1 cerrados con tests de regresión.
- ☐ Primera release versionada y verificable.
- ☐ Documentación current coherente con el schema generado.
- ☐ No hay datos de estación real versionados.
|  | Cierre del handoff El proyecto ya posee el núcleo difícil. La siguiente etapa consiste en convertir seguridad y capacidad interna en una interfaz pública reproducible, portable y simple para agentes, sin perder control autoritativo ni auditabilidad. |
|---|---|

## Apéndice A. Índice de fuentes de código y referencias
Todos los enlaces [C-xx] a archivos usan el commit 31040de18d5b745235003ea23a039125fce9fbe5. Cuando una fuente representa estado del repositorio —Actions, protección, tags o releases— el enlace apunta a la vista o API correspondiente consultada el 17 de agosto de 2026.
### A.1 Código y estado del repositorio
| ID | Fuente | Localizador | Tipo |
|---|---|---|---|
| [C-01] | README.md | Descripción del producto, 39 tools, arquitectura, instalación, seguridad y tests | Código / repositorio |
| [C-02] | Commit revisado | Commit 31040de; mensaje de verificación y lote de correcciones | Código / repositorio |
| [C-03] | GitHub Actions | Estado de workflows en la fecha de revisión | Código / repositorio |
| [C-04] | Rama main | protected y required status checks | Código / repositorio |
| [C-05] | Releases | Listado de releases | Código / repositorio |
| [C-06] | Tags | Listado de tags | Código / repositorio |
| [C-07] | tools/pyproject.toml | Versión, Python mínimo y dependencias declaradas | Código / repositorio |
| [C-08] | tools/requirements-mcp.txt | Dependencias directas fijadas | Código / repositorio |
| [C-09] | tools/dependency-lock.json | Artefactos, CPython embebido y toolchains fijados | Código / repositorio |
| [C-10] | tools/install_mcp.py | INSTALLER_CLI_MANIFEST, validación PE/ruta/tamaño/hash y registro | Código / repositorio |
| [C-11] | tools/install-mcp.ps1 | Invoke-Python, venv, pip y registro | Código / repositorio |
| [C-12] | reports/security/installer-cli-manifest-v1.json | Rutas, tamaños y hashes de Claude/Codex de la máquina autora | Código / repositorio |
| [C-13] | reports/security/installer-not-found-fixtures-v1.json | Salida capturada con MCPs configurados en la máquina autora | Código / repositorio |
| [C-14] | addon/scripts/5_Mission/MCPBridge.c | MCPBridge.TryInit, polling, GetFirstHuman y comandos de servidor | Código / repositorio |
| [C-15] | addon/scripts/5_Mission/MCPClientBridge.c | MCPClientBridge.TryInit y comprobación de prefijo loopback | Código / repositorio |
| [C-16] | tools/dayz_mcp/loopback.py | Handler._read_json, auth, whitelist, colas, TTL y servidor HTTP | Código / repositorio |
| [C-17] | tools/dayz_mcp/server.py | Registro FastMCP, wait_for, capture_screenshot y alias de argumentos | Código / repositorio |
| [C-18] | tools/mcp_capture.py | _mime_for, encode_bytes, resolve_capture_dir y write_fullres | Código / repositorio |
| [C-19] | tools/tests/test_mcp_capture.py | Cobertura de captura JPEG/PNG y ventana; ausencia del recorrido FastMCP WebP | Código / repositorio |
| [C-20] | tools/README-mcp.md | Instalación detallada y estado documentado de hardening | Código / repositorio |
| [C-21] | dayz-mcp-architecture.md | Documento de arquitectura histórica / superficie anterior | Código / repositorio |
| [C-22] | product-spec.md | Contrato histórico, número anterior de tools y referencias internas | Código / repositorio |
| [C-23] | tools/dayz_mcp/session_coordination.py | Leases, cola FIFO, operaciones, cleanup y auditoría | Código / repositorio |
| [C-24] | tools/dayz_mcp/process_lifecycle.py | Ciclo de vida de procesos y recuperación | Código / repositorio |
| [C-25] | tools/dayz_mcp/daemon.py | Daemon compartido, arranque, estado y watchdog | Código / repositorio |
| [C-26] | tools/dayz_mcp/pinned_keyfile.py | Validación endurecida de keyfile | Código / repositorio |
| [C-27] | tools/dayz_mcp/daemon_credential.py | Carga, refresh y recuperación de credenciales | Código / repositorio |
| [C-28] | tools/dayz_mcp/secure_launcher.py | Lanzador seguro y autoridad de ejecución | Código / repositorio |
| [C-29] | tools/dayz_mcp/request_path_authority.py | Autoridad y validación de rutas de solicitudes | Código / repositorio |
| [C-30] | tools/dayz_mcp/security_runtime_audit.py | Auditoría de seguridad en runtime | Código / repositorio |
| [C-31] | tools/tests/ | Suite de pruebas publicada | Código / repositorio |
| [C-32] | tools/dayz_mcp/host_config.py | Configuración de host y timeouts de Claude/Codex | Código / repositorio |
| [C-33] | tools/relock_toolchain.py | Reescritura de la sección toolchains del lock | Código / repositorio |
| [C-34] | tools/dayz_mcp/log_tail.py | Resolución, lectura y límites de logs | Código / repositorio |
| [C-35] | addon/scripts/4_World/MCP_CarScript.c | Extensiones de vehículo y control | Código / repositorio |
| [C-36] | tools/dayz_mcp/launcher_registry_update.py | Bootstrap e instalación del registro de launchers | Código / repositorio |
| [C-37] | tools/build_native_launcher.py | Construcción del launcher nativo y reproducibilidad | Código / repositorio |
| [C-38] | tools/launcher-policy.example.json | Ejemplo de política de launcher | Código / repositorio |
| [C-39] | tools/dayz_mcp/runtime_state.py | Estado persistente del runtime | Código / repositorio |
| [C-40] | tools/dayz_mcp/core.py | Versionado del bridge y estados bloqueados | Código / repositorio |
| [C-41] | Árbol completo del commit | Tamaños y distribución de módulos en el snapshot revisado | Código / repositorio |

### A.2 Referencias normativas
| ID | Fuente | Localizador | Tipo |
|---|---|---|---|
| [N-01] | MCP 2025-11-25 - Tools | outputSchema, annotations, structuredContent, resource links y taskSupport | Referencia normativa |
| [N-02] | MCP 2025-11-25 - Tasks | Tasks experimentales, negociación de capacidades y lifecycle | Referencia normativa |
| [N-03] | MCP 2025-11-25 - Resources | Resources, templates, subscriptions y URIs | Referencia normativa |
| [N-04] | MCP 2025-11-25 - Prompts | Prompts descubribles y parametrizables | Referencia normativa |
| [N-05] | MCP 2025-11-25 - Completion | Autocompletado de argumentos | Referencia normativa |
| [N-06] | pip - Secure installs | --require-hashes, --only-binary :all: y dependencias transitivas | Referencia normativa |
| [N-07] | GitHub - Artifact attestations | Procedencia de builds, SBOM y verificación | Referencia normativa |

### A.3 Nota de citación
Una referencia enlaza al archivo completo y el localizador nombra la clase, función, documento o propiedad que fundamenta la afirmación. El uso de permalinks evita que un cambio posterior en main altere la evidencia revisada.