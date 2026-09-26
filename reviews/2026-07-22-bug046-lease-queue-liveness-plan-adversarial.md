# Auditoría adversarial — plan BUG-046 vs contrato de coordinación (2026-07-22)

## TL;DR

- **Veredicto: BLOCKED.** La dirección `claim-on-live-wait` es correcta y elimina el grant ciego original, pero el plan todavía permite recrear un lease oculto en la carrera `wait/grant-inflight/cancel`, no define una secuencia auditable que vincule el grant a la respuesta `wait=active` y deja ambiguo el fence de auditoría tardía de `release`.
- El launcher propuesto tampoco está listo para implementación: su allowlist por forma de ruta confía en cualquier `*/tools/dayz-test.ps1` editable bajo `DayZ Projects`, mantiene el token en el entorno de PowerShell durante procesos hijos no lifecycle y no puede ejecutar hoy el consumidor real porque la política efectiva es `Restricted` y el script está sin firma.
- No se encontró necesidad de un runner arbitrario ni de cambiar el formato persistente. Esas dos decisiones del plan son correctas. La corrección mínima es endurecer el state machine, acotar el launcher a consumidores explícitamente confiables, cerrar la estrategia PowerShell y verificar los timeouts de ambos hosts **antes** de código de producción.

## Alcance y fuentes

### Inventario de autoridad y vigencia

| Fuente | Autoridad | Vigencia / uso en esta auditoría |
|---|---|---|
| `DayZ_MCP_dev/product-spec.md:119-149` | Contrato autoritativo | Vigente para Intent H y H1-H8. H9 todavía es propuesta del plan, no contrato aprobado. |
| `DayZ_MCP_dev/plans/2026-07-22-bug046-lease-queue-liveness-plan.md` | Entregable auditado | Propuesta; no prueba el estado real. |
| `DayZ_MCP_dev/tools/dayz_mcp/session_coordination.py` | Implementación primaria | Estado real del coordinador y sus fences. |
| `DayZ_MCP_dev/tools/dayz_mcp/server.py`, `loopback.py`, `runtime_state.py` | Implementación primaria | Estado real de runtime, HTTP, FastMCP y snapshot. |
| `DayZ_MCP_dev/tools/tests/test_session_coordination.py`, `test_task7_final_authority_regressions.py`, `test_client_mode.py` | Referencia ejecutable | Contratos/races que el plan debe migrar sin relajarlos. |
| `AI/10_Projects/DayZ_MCP/research/2026-07-22-lease-queue-liveness-codex.md` | Evidencia derivada con citas primarias | Vigente para el incidente y la métrica 5/30; no sustituye los tests ni el código. |
| `AI/20_Runbooks/dayz-mcp-agent-session-protocol.md` | Protocolo L2 autoritativo | Vigente para secreto por entorno, heartbeat, release y status. |
| `LF_VStorage_dev/tools/dayz-test.ps1` | Consumidor real | Vigente; exige las dos variables de entorno y crea otros procesos durante build/lifecycle. |
| `C:/Users/guill/Tools/LFV_D2_Executor/lfv_executor/dayz.py` | Referente conocido que funciona | Referencia útil, pero más estrecha: inyecta el token al `lifecycle_cli.py` empaquetado, no a cualquier script de proyecto. |
| FastMCP 1.27.2 local y CLI/config de Claude/Codex | Fuente primaria local | Firma de progreso y schemas consultados; persistencia real de los nuevos timeouts aún no demostrada. |

### Estado real fijado antes de auditar

- El defecto original está verificado: `release` y expiry llaman `_grant_next_locked()` (`session_coordination.py:1266-1276,1400-1433,1509-1514`), y ese helper crea/publica un lease sin un `wait` vivo (`session_coordination.py:1540-1590`). La evidencia operacional registra 5 grants FIFO no recogidos de 30 (`research/2026-07-22-lease-queue-liveness-codex.md:20-34`).
- `SessionCoordinator.wait(...)` renueva el ticket al entrar y después espera por slices acotados (`session_coordination.py:435-586`). `_write_audit_locked(...)` libera el `Condition` durante I/O (`session_coordination.py:1749-1783`), por lo que toda transición que audita necesita revalidación/fence explícito.
- `_find_ticket_locked(...)` considera un ticket dentro de `_grant_inflight`, no solo dentro de la cola (`session_coordination.py:1647-1657`). Ese detalle es decisivo para la carrera de cancelación.
- El snapshot es observabilidad y no restaura autoridad de la generación anterior (`runtime_state.py:102-185`); por tanto el hotfix puede mantenerse sin migración persistente.
- El host actual tiene política PowerShell efectiva `Restricted`; todas las scopes están `Undefined`. `LF_VStorage_dev/tools/dayz-test.ps1` está `NotSigned`. Esto confirma el bloqueo que el propio plan menciona en `plan:88,515`.
- La línea base global se declara como 584 tests con 13 fallos + 1 error (`plan:53-58`), pero el plan no congela los 14 IDs/fingerprints. Se marca `[TBD-verify: exact 14 baseline test IDs and failure fingerprints]`.

## Matriz del contrato

| Criterio | Qué exige | Estado del plan | Evidencia / razón |
|---|---|---|---|
| Intent H | Claude/Codex no intercalan mutaciones, no abandonan ownership y usan FIFO fail-closed | **PARCIAL / BLOCKED** | La dirección evita el grant ciego, pero las carreras HIGH-01 a HIGH-03 aún pueden dejar autoridad oculta o terminar la espera por contención interna. `product-spec.md:121-123`. |
| H4 | FIFO estricta, idempotencia, TTL 120 s y avance tras abandono | **PARCIAL / BLOCKED** | `plan:128,153-160` cubre los casos nominales, pero no especifica la linearización cancel-vs-grant ni el fence de auditoría tardía. |
| H7 | Ledger sin secretos y reconstrucción acquire→wait→grant→use→release | **PARCIAL / BLOCKED** | El retorno directo propuesto desde `_claim_waiting_head_locked` omite o deja fuera de la misma transición el audit `session_wait decision=active`; ver HIGH-02. `product-spec.md:133`. |
| H8 | Gate real sobre una caja; hoy existe sustitución histórica 4-Codex | **PARCIAL** | El plan preserva correctamente la evidencia histórica (`plan:135`), pero añade un gate real 2+2 sin cerrar disponibilidad/proveniencia del host Claude. `product-spec.md:134-147`; ver MEDIUM-06. |
| H9 propuesto | Espera MCP viva, no `queued` terminal, cleanup sin ticket/lease oculto, launcher agent-safe, gate mixto | **FALTA / BLOCKED** | H9 aún no está en el contrato y su implementación propuesta falla en cancelación, durabilidad declarada, confianza del launcher y PowerShell. `plan:126-146`. |
| Protocolo de secreto | Identidad/token solo por entorno del proceso lifecycle; nunca argv/log/durable | **PARCIAL / BLOCKED** | No aparecen en argv, pero quedan en PowerShell durante AddonBuilder/otros hijos; ver HIGH-05. `dayz-mcp-agent-session-protocol.md:23-31`. |
| Compatibilidad/rollback | Legacy legible y rollback explícito | **CERRADO EN DISEÑO** | No hay nueva clave de snapshot ni formato durable; `plan:283-300` coincide con `runtime_state.py:102-185`. |
| No runner arbitrario | No convertir el MCP en ejecutor genérico de comandos | **CERRADO EN DISEÑO** | `plan:62-72,487` rechaza correctamente jobs arbitrarios. El launcher sí necesita una trust boundary más estrecha, pero no exige un runner genérico. |
| TDD/viability | RED antes de producción y carreras reproducibles | **PARCIAL / BLOCKED** | V1-V18 son una buena base, pero faltan los fixtures concretos de HIGH-01 a HIGH-03, MEDIUM-03 y MEDIUM-05. |

## Hallazgos por severidad

### CRITICAL

No se verificó un hallazgo CRITICAL. Los riesgos encontrados son bloqueantes para implementación, pero el lease está acotado por identidad/TTL y no se observó una vía remota o persistente de autoridad ilimitada.

### HIGH

#### HIGH-01 — `cancel` no lineariza contra `_grant_inflight`; puede recrear exactamente el lease oculto que BUG-046 pretende eliminar

- **Path:line**: `plan:102-114,178-200,386-396,437-443`; `session_coordination.py:435-586,1549-1590,1647-1657,1749-1783`; `server.py:299-320`.
- **Problema**: el plan dice que `cancel` elimina un ticket queued o libera un `_active` derivado (`plan:388`), pero no define qué hace si el ticket ya fue extraído de `_queue` y está en `_grant_inflight`. Ese estado existe mientras el audit de grant corre y el `Condition` está liberado. Cancelar una coroutine que espera en `asyncio.to_thread` no detiene la request HTTP subyacente (`server.py:299-312`): el cleanup puede recibir 403 mientras el wait antiguo termina el audit y publica el lease.
- **Impacto concreto**: **degradation de liveness + autoridad oculta**. La tool cancelada no recibe token, pero el daemon puede mantener un lease activo hasta 120 s; es el mismo síntoma operativo de BUG-046 trasladado a otra ventana.
- **Corrección mínima**: definir una única transición para los tres casos `queued`, `grant_inflight` y `active-derived`. Añadir un marker/fence de cancelación ligado a `(client, ticket_id)` que: (1) reserve un queued ticket antes del audit de cancel; (2) marque `cancel_requested` si el grant está in-flight; (3) impida que el helper publique `_active` después de esa marca; y (4) solo libere un active con `client` y `source_ticket_id` exactos. El resultado 200 de cancel debe significar que ninguna publicación futura de ese ticket es posible.
- **Tests obligatorios**: barreras antes, durante y después de `session_granted`; cancelación con audit de grant bloqueado; respuesta del wait perdida; cancel repetido tras respuesta perdida; identidad foreign. El test no puede aceptar un 403 seguido de active.
- **Owner**: Codex (state machine/tests), Claude/usuario (aprobar contrato revisado).

#### HIGH-02 — El claim propuesto no conserva la causalidad durable `session_granted` → `session_wait(active)` y puede ocultar un lease si falla el segundo audit

- **Path:line**: `plan:110,153-157,195-200,353-367,527-534`; `session_coordination.py:462-483,501-526,1556-1590`; `runtime_state.py:55-86`.
- **Problema**: el snippet del plan retorna directamente `claim_result` (`plan:364-366`). El contrato del helper solo menciona auditar `session_granted` antes de publicar; no exige el evento causal `session_wait decision=active`. En el código actual ese evento se escribe en otra rama después de que `_active` ya existe (`session_coordination.py:501-526`). Si se mantiene esa separación y el audit de `session_wait` falla, se devuelve 503 pero el lease queda activo; si se retorna directamente desde el helper, el evento no existe y la métrica de V12 clasifica el grant como no reclamado.
- **Impacto concreto**: **degradation + evidencia inconsistente**. H7 no puede reconstruir qué llamada viva recibió el token; `fifo_grants_unclaimed=0` puede fallar aun cuando la operación respondió active, o un error de audit puede dejar autoridad invisible al runtime.
- **Corrección mínima**: convertir el claim en una transición causal explícita. Antes de publicar `_active`, auditar bajo el mismo fence tanto el grant como la entrega al `wait` causal, o definir un evento único/compensatorio que permita distinguir `grant_committed` de `grant_aborted`. Ningún error posterior al primer append debe devolver 503 dejando `_active` sin que el caller reciba token. Especificar también cómo se interpreta un append parcial, porque `JsonlAuditWriter.write()` es atómico por evento, no por lote (`runtime_state.py:55-86`).
- **Tests obligatorios**: fallo en el primer evento, fallo en el segundo, append parcial, callback que cambia coordinación después de cada evento, y aserción de exactamente una entrega causal por `lease_id`.
- **Owner**: Codex.

#### HIGH-03 — El plan elimina el auto-grant pero no adjudica el fence de auditoría tardía de `release`; una espera viva puede terminar 503 o violar el orden físico del ledger

- **Path:line**: `plan:92-110,338-373`; `session_coordination.py:1400-1433,1436-1538`; `test_task7_final_authority_regressions.py:31-83,85-132,1394-1481`.
- **Problema**: cuando el worker de release excede el wait acotado, el código actual limpia `_handoff_pending` antes de que el worker termine (`session_coordination.py:1417-1433`), y el worker vuelve a limpiarlo al final (`:1509-1514`). El plan ordena quitar `_grant_next_locked()` pero “conservar fences” sin decidir si el handoff sigue claimable durante ese worker. Si se conserva el early-clear, el nuevo wait puede competir por `_audit_gate`: con `blocking=False` termina en `audit_failed`/503 por mera contención; si gana la carrera, `session_granted` puede preceder físicamente a `session_release_finished`. Si se conserva `_handoff_pending` indefinidamente, un audit colgado puede pinnear la cola.
- **Impacto concreto**: **degradation de liveness o ledger causal inválido**. Una sesión que sí está esperando podría pararse por una contención interna transitoria, contradiciendo el objetivo principal.
- **Corrección mínima**: modelar explícitamente `release_audit_inflight` (puede ser condición calculada/no persistente) y distinguir `audit gate busy` de `audit callback failed`. Mientras sea solo busy, `session_acquire_wait` sigue queued y emite progreso; no devuelve 503 ni publica grant. Definir el comportamiento acotado si el worker nunca termina y migrar, no relajar, los tests existentes de `test_task7_final_authority_regressions.py:31-266,1394-1481`.
- **Tests obligatorios**: wait 0 y wait largo durante release audit; worker que termina tarde; callback que falla; orden físico release-finished antes de grant; ausencia de pin permanente o declaración fail-closed verificable en doctor.
- **Owner**: Codex.

#### HIGH-04 — La allowlist del launcher confía en scripts arbitrarios editables y les entrega autoridad de lease

- **Path:line**: `plan:74-88,258-281,489-515`; `server.py:74-94`; `C:/Users/guill/Tools/LFV_D2_Executor/lfv_executor/dayz.py:346-405`.
- **Problema**: exigir solo nombre `dayz-test.ps1`, padre `tools` y pertenencia a `DayZ Projects/<proyecto>` no es una trust boundary. Cualquier proyecto editable puede contener ese path y recibir identidad/token. Además, la firma propuesta recibe `ServerConfig`, que no tiene un root/allowlist de launchers (`server.py:74-94`). El referente conocido es más estrecho: calcula un `lifecycle_cli.py` empaquetado junto al keyfile y solo inyecta el entorno a ese ejecutable (`dayz.py:346-405`).
- **Impacto concreto**: **exposición de credencial + mutación no autorizada durante el TTL**. Un script no confiable puede usar o exfiltrar el token, escribirlo a disco/red o operar el daemon con la identidad completa; el redactor de stdout no evita esos canales.
- **Corrección mínima**: fase inicial allowlisted por identidad exacta de artefacto, no por forma de ruta. Para el alcance actual, registrar explícitamente `LF_VStorage_dev/tools/dayz-test.ps1` (y ningún otro) en una configuración administrada, con path canónico y una política de integridad aprobada (hash/manifest firmado o registro explícito actualizado por instalación). Validar todos los ancestros reparse y revalidar inmediatamente antes de spawn. Ampliar a otros mods requiere una acción de registro separada.
- **Tests obligatorios**: script homónimo en otro proyecto, modificación después de validación, symlink/reparse en cada ancestro y swap entre check/spawn.
- **Owner**: Codex (launcher), usuario/Claude (lista de consumidores confiables).

#### HIGH-05 — El token queda en el entorno de PowerShell durante AddonBuilder y otros hijos, más amplio que el contrato “solo lifecycle”

- **Path:line**: `plan:18,76-84,117-120,503-511`; `dayz-mcp-agent-session-protocol.md:23-31,44`; `LF_VStorage_dev/tools/dayz-test.ps1:121-150,400-429,527-550`.
- **Problema**: el launcher inyecta las variables al PowerShell completo. Ese script conserva el entorno durante build y crea AddonBuilder con `Start-Process` (`dayz-test.ps1:417-422`); los hijos heredan el entorno por defecto. El plan no incluye modificar `dayz-test.ps1`, por lo que no cumple su propia invariante de limitar las credenciales al hijo aprobado ni el runbook que las restringe al proceso que invoca lifecycle.
- **Impacto concreto**: **ampliación innecesaria de superficie de secreto**. Procesos de terceros durante build reciben identidad/token aunque no los necesitan.
- **Corrección mínima**: incluir el consumidor en el scope. Al inicio, capturar las credenciales en memoria y retirarlas del entorno global; en `Invoke-LifecycleCli`, establecerlas temporalmente solo alrededor del proceso `lifecycle_cli`, restaurarlas/eliminarlas en `finally`, y verificar que AddonBuilder/otros hijos no las reciben. Alternativamente, sacar las llamadas lifecycle a un helper empaquetado que reciba la autoridad sin contaminar el entorno general de PowerShell.
- **Tests obligatorios**: fake AddonBuilder sin variables; fake lifecycle con variables exactas; restauración tras error; ninguna credencial en errores/salida.
- **Owner**: Codex en `LF_VStorage_dev/tools/dayz-test.ps1` con aprobación de scope del usuario/Claude.

#### HIGH-06 — El launcher no es ejecutable en el host real bajo la política vigente; H9 no puede cerrarse con un gate condicionado que siempre bloquea

- **Path:line**: `plan:88,133-146,499-515`; consumidor real `LF_VStorage_dev/tools/dayz-test.ps1` (estado Authenticode actual: `NotSigned`).
- **Problema**: el plan promete que el launcher permite ejecutar `dayz-test.ps1`, pero al mismo tiempo acepta como resultado final registrar un bloqueo si la política sigue `Restricted`. La política efectiva verificada es `Restricted` y el script real está sin firma. La prohibición de `ExecutionPolicy Bypass` es correcta, pero deja la aceptación sin camino ejecutable.
- **Impacto concreto**: **bloqueo funcional**. Puede implementarse un launcher con tests verdes que nunca lance el harness real en esta máquina; no resuelve la petición operacional de Claude/usuario.
- **Corrección mínima**: adjudicar antes de implementación una vía compatible y autorizada: script firmado por un certificado confiable y política que lo admita, cambio de policy aprobado fuera del launcher, o reemplazo del consumidor por una entrada no `.ps1` con paridad validada. Si ninguna se autoriza, separar el hotfix de cola del launcher y mantener H9-launcher abierto; no declarar finalización.
- **Owner**: usuario/administrador para policy/confianza; Codex para preflight y evidencia. Nunca usar Bypass.

#### HIGH-07 — Una request larga no es “durable” y los timeouts de host siguen sin verificación de persistencia

- **Path:line**: `plan:62-72,214-229,445-469,487`; `research/2026-07-22-lease-queue-liveness-codex.md:33-55`; `C:/Users/guill/.claude/cache/changelog.md:409,844`; salida efectiva actual de `codex mcp get dayz-mcp --json` (`tool_timeout_sec=null`).
- **Problema**: `plan:487` llama “ejecución durable” a la request MCP pendiente, pero el research primario ya establece que una request larga es frágil frente a timeout/desconexión y que MCP Tasks exige capability. Además, Task 5 pospone para implementación el probe que decidirá cómo persiste Codex el timeout (`plan:461-465`); la configuración efectiva actual no contiene `tool_timeout_sec`, y `claude mcp get` tampoco muestra un timeout. Por el propio criterio de luz verde (`plan:581`), la estrategia aún no está verificada.
- **Impacto concreto**: **degradation / promesa de producto incorrecta**. Sobrevive a slices HTTP mientras el host y la request siguen vivos, pero no a cierre/restart/desconexión del host. Si la configuración no persiste, la tool volverá a cortarse y la sesión se parará.
- **Corrección mínima**: renombrar el contrato a “espera larga ligada a una request viva”, declarar explícitamente su límite, y ejecutar/registrar probes reversibles de Claude y Codex antes de Task 1. Solo después fijar unidades/valores y el mecanismo de rollback. Si “continuar tras perder el host” es criterio real, diseñar por separado una operación allowlisted durable o MCP Tasks negociado; no llamarlo resuelto por `session_acquire_wait`.
- **Tests/gates obligatorios**: smoke real > timeout por defecto en una sesión nueva de cada host, cancelación por host, reconnect, y lectura efectiva de config. `[TBD-verify: maximum accepted per-server timeout in the exact Claude and Codex builds]`.
- **Owner**: Codex (probes/installer), usuario/Claude (criterio de durabilidad fuerte).

### MEDIUM

#### MEDIUM-01 — La actualización global de configs no es transaccional

- **Path:line**: `plan:461-469`; `install-mcp.ps1:406-439`.
- **Problema**: el instalador hace remove-then-add de Claude y Codex. El plan guarda backups, pero no exige restauración automática si falla el add o la verificación después de eliminar una entrada.
- **Impacto concreto**: **degradation operacional**: un fallo parcial puede dejar uno de los hosts sin `dayz-mcp`, agravando la interrupción que se intenta arreglar.
- **Corrección mínima**: snapshot de ambas entradas, probe temporal, mutación de un host por vez, verify inmediato y rollback automático exacto ante cualquier fallo. Testear fallo en cada punto del remove/add/verify.
- **Owner**: Codex.

#### MEDIUM-02 — El gate global por “≤14 fallos” puede ocultar una regresión nueva

- **Path:line**: `plan:53-58,536-540`.
- **Problema**: comparar solo el número permite que un fallo preexistente desaparezca y uno nuevo aparezca manteniendo 14. El texto añade una protección parcial para archivos focales, pero no prohíbe un fallo nuevo fuera de esa lista.
- **Impacto concreto**: **regresión no detectada**.
- **Corrección mínima**: congelar antes del primer diff los 14 IDs, tipo (failure/error) y fingerprint de mensaje; la salida final debe ser subconjunto exacto del baseline o explicar cualquier delta. Ningún test nuevo fallido se acepta aunque el total no crezca. `[TBD-verify: exact 14 baseline test IDs and failure fingerprints]`.
- **Owner**: Codex.

#### MEDIUM-03 — Cleanup solo está especificado para timeout/`CancelledError`, no para fallo de progreso/transporte

- **Path:line**: `plan:408-443`; `server.py:299-320`; FastMCP local `server.py:1162-1180`.
- **Problema**: `Context.report_progress` es un `await` real cuando hay `progressToken`; el envío puede lanzar una excepción. `_session_call` también puede fallar por transporte. El plan solo exige cleanup en timeout y `CancelledError`. Un error entre slices puede dejar `active_ticket` local hasta TTL; combinado con una request `to_thread` todavía viva entra en HIGH-01.
- **Impacto concreto**: **degradation** y estado local stale.
- **Corrección mínima**: definir `finally`/cleanup para toda terminación no-active, distinguiendo errores cancelables de pérdida total de transporte. Repropagar el error original tras un cleanup protegido y acotado; status final si hay conectividad.
- **Tests obligatorios**: callback de progreso que falla, JSON/schema inválido, conexión cortada antes/durante/después del claim, cleanup que también falla.
- **Owner**: Codex.

#### MEDIUM-04 — El launcher no especifica drenaje concurrente ni cancelación del proceso real de Windows

- **Path:line**: `plan:507-515`; referente `dayz.py:395-405,539-563`.
- **Problema**: V15 cubre secretos fragmentados, pero no exige leer stdout y stderr concurrentemente; leer uno antes del otro puede bloquear si el otro llena su pipe. V16 usa “cancelación async”, que no demuestra el comportamiento de Ctrl-C, timeout/terminación del shell o cierre del proceso en Windows. Un `finally` de coroutine no corre si el proceso es terminado por fuerza.
- **Impacto concreto**: **degradation / cierre no reconciliado** durante una ejecución real.
- **Corrección mínima**: dos drain tasks concurrentes con redactor incremental y límite de memoria; handlers de señales soportadas; contrato explícito para hijo PowerShell todavía vivo; release/status best-effort sin matar DayZ por PID/nombre. Test de backpressure > pipe buffer y señal/timeout de proceso real controlado.
- **Owner**: Codex.

#### MEDIUM-05 — La validación de reparse/path no cierra TOCTOU ni todos los ancestros

- **Path:line**: `plan:81-86,499-505`; referencia adicional verificada `C:/Users/guill/Tools/LFV_D2_Executor/tests/test_tree_manifest.py:717-773`.
- **Problema**: “ruta resuelta bajo root” y “reparse escape” no especifican inspección de cada ancestro ni revalidación después de adquirir el lease. En Windows, validar antes de una espera de cola de hasta 1800 s y lanzar después deja una ventana amplia para swap.
- **Impacto concreto**: **degradation de seguridad**: puede ejecutarse un target distinto al validado con credenciales en entorno.
- **Corrección mínima**: validar forma/trust antes de adquirir, y revalidar identidad de archivo + todos los ancestros inmediatamente antes de spawn; si cambió, release y abortar. No confiar solo en `Path.resolve()`.
- **Owner**: Codex.

#### MEDIUM-06 — El gate 2 Claude + 2 Codex no tiene mecanismo autónomo ni prueba de proveniencia externa

- **Path:line**: `plan:134-145,165,542-552`; `product-spec.md:134-147`; `research/2026-07-22-lease-queue-liveness-codex.md:30-32,72-73`.
- **Problema**: el actor actual puede lanzar subagentes Codex, no sesiones Claude reales. El antecedente H8 ya demostró que un runner Python autoetiquetado no acredita un host real. El plan dice “tareas reales”, pero no define evidencia de PID/binario/session host ni cómo se coordinarán las dos Claude.
- **Impacto concreto**: **gate no ejecutable o falso positivo de interoperabilidad**.
- **Corrección mínima**: definir antes de Task 8 quién inicia cada sesión, evidencia externa mínima por host y condición de bloqueo si Claude no está disponible. No cerrar H8/H9 con labels del payload.
- **Owner**: usuario/Claude para sesiones Claude; Codex para harness y reconciliación.

### LOW

#### LOW-01 — Los tests contractuales por substring son demasiado débiles y algunos comandos futuros están etiquetados `[EXACT]`

- **Path:line**: `plan:312-323,554-569`.
- **Problema**: `assertIn("live wait")` y `assertIn("fifo_grants_unclaimed=0")` pueden pasar con texto no normativo. Los comandos focales están marcados `[EXACT]` aunque incluyen módulos que todavía no existen.
- **Impacto concreto**: **degradation de verificabilidad**, no fallo de runtime por sí solo.
- **Corrección mínima**: parsear la fila H4/H9 o exigir IDs/estructura exacta; etiquetar comandos que dependen de archivos futuros como `[DESIGN]` hasta crearlos.
- **Owner**: Codex.

## Falta validar offline / en runtime

Estas validaciones no pueden acreditarse solo leyendo el plan:

1. Persistencia real del timeout por servidor en las builds exactas de Claude y Codex y supervivencia de una tool por encima del timeout anterior.
2. Semántica de cancelación real del cliente MCP: cuándo se cancela la coroutine del servidor y cuánto permanece el `asyncio.to_thread` HTTP.
3. Gate real 2 Claude + 2 Codex con proveniencia externa, no auto-label.
4. Ejecución de `dayz-test.ps1` por una vía PowerShell autorizada sin Bypass; hoy policy/signature la bloquean.
5. Redacción bajo salida real fragmentada/backpressure y comportamiento frente a Ctrl-C/terminación del proceso launcher.

## Análisis de falsos positivos

- **No se marca como gap inmediato la ausencia de una campaign/job queue arbitraria.** El plan tiene razón: no puede reanudar razonamiento de un modelo y aumentaría la superficie de ejecución. Si se necesita durabilidad tras perder el host, debe ser una operación allowlisted o MCP Tasks negociado, no un shell runner genérico.
- **No se marca cambio de formato persistente.** `CoordinationSnapshotStore` no restaura autoridad y el plan conserva las claves; legacy/rollback están adecuadamente tratados.
- **No se marca el TTL 120 s como causa por sí solo.** Un wait vivo refresca el ticket; un head abandonado debe expirar. El defecto es conceder/cancelar durante ventanas no linearizadas, no la cifra 120 aislada.
- **No se marca la firma FastMCP de progreso como inventada.** `Context.report_progress(progress, total=None, message=None)` está verificada en FastMCP local `server.py:1162-1180`, y la inyección por anotación en `server.py:1098-1129`/`context_injection.py:11-46`.
- **No se atribuyen los 14 fallos globales a BUG-046.** Se acepta que son baseline preexistente; el hallazgo MEDIUM-02 es que falta congelar su identidad para no enmascarar un fallo nuevo.
- **No se pide ExecutionPolicy Bypass.** La negativa del plan es correcta. El hallazgo HIGH-06 es que falta una vía autorizada que haga cumplible la aceptación.

## Conflictos de fuentes expuestos

| ID | Fuentes | Conflicto | Resolución requerida |
|---|---|---|---|
| CONFLICT-01 | `research:33-55` vs `plan:487` | El research califica una request larga como frágil; el plan la llama durable. | Cambiar el contrato: “long-lived mientras host/request viven” o diseñar durabilidad real allowlisted. |
| CONFLICT-02 | `runbook:27` vs `plan:505` + `dayz-test.ps1:417-422` | El protocolo limita secreto al invocador lifecycle; el diseño lo deja en PowerShell durante otros hijos. | Scope temporal del env dentro del consumidor/helper. |
| CONFLICT-03 | `product-spec:134-147` vs `plan:134,542-552` | H8 histórico admite sustitución 4-Codex; H9 propuesto exige 2+2 real. | Mantener historia, pero definir H9 como gate nuevo abierto y con proveniencia. |
| CONFLICT-04 | referente `dayz.py:346-405` vs `plan:81-86` | El referente inyecta a un CLI empaquetado concreto; el plan amplía a cualquier script con forma válida. | Trust registry explícito; no inferir confianza por nombre/directorio. |

## Roadmap mínimo para desbloquear el plan

1. **Reescribir el state machine del plan**, sin código: cancel marker/fence para queued/grant-inflight/active-derived; batch causal grant+wait; fence de release-audit busy vs failed.
2. **Añadir viability tests concretos** para esas tres ventanas, usando barreras de los tests de autoridad existentes y preservando su cobertura de orden físico.
3. **Cerrar el launcher como trust boundary**: consumidor exacto registrado, revalidación pre-spawn, entorno lifecycle temporal y drenaje/señales.
4. **Adjudicar PowerShell** sin Bypass. Si no hay vía autorizada, separar launcher de BUG-046 y dejar su criterio abierto.
5. **Probar timeouts de ambos hosts antes de producción** y corregir la palabra “durable”. Hacer el installer transaccional.
6. Congelar los 14 fallos globales por ID/fingerprint y definir la proveniencia/operador del gate 2+2.
7. Repetir esta revisión adversarial sobre el plan corregido. Solo entonces procede una conclusión `GREEN`.

## Veredicto final

**BLOCKED**

El plan requiere correcciones bloqueantes antes de comenzar Task 1. No se autoriza implementación con el documento actual.
