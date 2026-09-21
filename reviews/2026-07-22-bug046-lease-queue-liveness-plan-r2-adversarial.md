# Auditoría adversarial R2 — BUG-046 lease queue liveness

## TL;DR

**Veredicto literal: BLOCKED**

La R2 resuelve de forma convincente la incompatibilidad con OneDrive, incorpora un WAL/fault observable y durable, preserva el error primario, corrige el orden del SHA y mantiene honestos los gates externos. La primitive Windows fue contrastada contra el SDK local y con una prueba empírica: el handle `GENERIC_READ + FILE_SHARE_READ` permitió lectura y bloqueó write, replace y rename del directorio padre.

No obstante, quedan tres bloqueos de diseño antes de TDD:

1. `session_acquire_wait` puede ser cancelado mientras el `/session/acquire` inmediato continúa en `asyncio.to_thread`; como ese grant usa `ticket_id=null`, el `finally` no instala cancelación y el protocolo prohíbe cancelar grants inmediatos. Queda un lease oculto hasta TTL.
2. El WAL nominal no puede finalizar según su propio contrato: `clear` solo acepta `state=repaired`, pero grant/release intentan limpiar directamente un marker `armed` sin transición terminal normal definida.
3. Los sidecar locks son cooperativos y “leer SHA → `os.replace`” no es un CAS atómico. Un escritor no cooperativo puede modificar el archivo entre ambas operaciones y perder sus bytes.

La reparación administrativa también necesita una fase durable por evento o una regla explícita de deduplicación para ser realmente idempotente tras crash.

## Identidad del entregable

- Plan: `plans/2026-07-22-bug046-lease-queue-liveness-plan.md`.
- SHA-256 esperado y verificado: `89C668690BC685B8F55F4B973DD4D6E555738C61FB018750A4F54275AD7710F7`.
- R0 contrastada íntegramente: `reviews/2026-07-22-bug046-lease-queue-liveness-plan-adversarial.md`.
- R1 contrastada íntegramente: `reviews/2026-07-22-bug046-lease-queue-liveness-plan-r1-adversarial.md`.
- No se editó código de producción ni el plan durante esta revisión.

## Inventario de fuentes

| Fuente | Autoridad | Uso |
|---|---|---|
| `product-spec.md:121-147` | Contrato vigente | Intent H, H4, H7 y evidencia histórica H8 |
| Plan R2 SHA verificado | Entregable auditado | Diseño/TDD propuesto; no se toma como prueba de implementación |
| `tools/dayz_mcp/session_coordination.py` | Código primario | Estado de lease/ticket, audit gate y snapshots vivos |
| `tools/dayz_mcp/server.py` | Código primario | `asyncio.to_thread`, estado cliente y respuestas acquire/wait |
| `tools/dayz_mcp/runtime_state.py` | Código primario | Atomicidad por evento, snapshot y persistencia vigente |
| `tools/dayz_mcp/daemon.py`, `loopback.py`, `doctor.py`, `admin_cli.py` | Código primario | Startup, status, persistencia y frontera admin reales |
| Windows SDK 10.0.26100.0 | Fuente primaria local | Flags, structs y firmas de handle/file identity |
| Python 3.14 local | Fuente primaria local | `asyncio.to_thread` y semántica disponible de `os.replace` |
| Ruta real `LF_VStorage_dev/tools/dayz-test.ps1` | Consumidor real | OneDrive/reparse, dependencias y scope del secreto |

## Matriz del contrato R2

| Criterio | Estado | Evidencia / conclusión |
|---|---|---|
| Claim FIFO solo desde wait vivo | **PARCIAL / BLOCKED** | El claim FIFO está bien linearizado, pero el entrypoint alto todavía usa acquire inmediato no cancelable; ADV-R2-H01. |
| Cancel 200/202/cancelling | **CERRADO PARA TICKETS** | Marker previo a I/O y compensación cubren queued/inflight/active-derived (`plan:157-172,298-304,562-583`). No cubre grant inmediato sin ticket. |
| Prepared → commit → publish único | **PARCIAL / BLOCKED** | El orden y crash matrix están definidos (`plan:167-185,226-230`), pero el marker nominal no tiene transición limpiable; ADV-R2-H02. |
| Release fence fail-closed | **PARCIAL / BLOCKED** | Busy/fail/stall y restart están cubiertos (`plan:151-154,173,250-252,528-536`), sujetos al mismo defecto de clear WAL. |
| Status/snapshot/doctor fault | **CERRADO EN DISEÑO** | Archivos, schema, startup y finding FAIL ya están en scope (`plan:306-334,477-504`). |
| Admin repair exacto | **PARCIAL** | TTY/HMAC/fault-id/state repaired están bien cerrados, pero el batch multi-evento no es idempotente frente a crash intermedio; ADV-R2-M01. |
| OneDrive + TOCTOU launcher | **CERRADO EN DISEÑO** | Root identity, no-name-surrogate, final handle y SHA están especificados y verificados localmente (`plan:122-141,246-248,696-733`). |
| Token solo en lifecycle | **CERRADO EN DISEÑO** | Consumer scrub + scope temporal + tests fake (`plan:133-135,715-729`). |
| Error primario exacto | **CERRADO EN DISEÑO** | Captura `BaseException`, helper no-throw y invariantes de tipo/mensaje/cause/context (`plan:599-641`). El lease inmediato perdido es un problema distinto. |
| Config host transaccional | **PARCIAL / BLOCKED** | Locks/CAS lógico/rollback conflict mejoran R1, pero no existe CAS atómico frente a escritor no cooperativo; ADV-R2-H03. |
| SHA registry post-consumidor | **CERRADO** | El registro final se genera después de modificar y revalidar el `.ps1` (`plan:711-719`). |
| Baseline drift | **CERRADO COMO GATE** | Dos runs idénticos, fingerprints/hashes y stop si persiste `PowerShellProcessGuard` (`plan:447-475`). |
| PowerShell/Claude externos | **CERRADO HONESTAMENTE COMO BLOCKERS EXTERNOS** | No Bypass, no 2+2 simulado y no cierre total sin sesiones/policy reales (`plan:141,733,762-772,791`). |
| Compatibilidad/rollback | **PARCIAL** | Legacy y rollback con marker están razonados (`plan:418-443`); la transición nominal del marker debe corregirse primero. |

## Revalidación completa de R0

| Hallazgo R0 | Estado en R2 | Resultado adversarial |
|---|---|---|
| HIGH-01 cancel vs grant-inflight | **CERRADO PARA TICKET / ABIERTO PARA IMMEDIATE** | Las fases con ticket están cubiertas. Aparece una ventana equivalente en el primer `/acquire` del entrypoint alto; ADV-R2-H01. |
| HIGH-02 causalidad grant/wait | **PARCIAL** | Prepared/commit causal, WAL, snapshot y compensación son correctos conceptualmente; clear nominal contradictorio bloquea la secuencia; ADV-R2-H02. |
| HIGH-03 release audit tardío | **PARCIAL** | Busy no da 503, stall/fail quedan latched y doctor los ve. La liberación normal del WAL no está definida de forma ejecutable. |
| HIGH-04 trust del launcher | **CERRADO** | Registro exacto root identity + relative path + SHA; caller solo ID; handle mantiene el objeto fijado. |
| HIGH-05 secreto heredado por AddonBuilder | **CERRADO EN DISEÑO** | `dayz-test.ps1` entra en scope y limpia Process env antes del build. |
| HIGH-06 Restricted/NotSigned | **GATE EXTERNO HONESTO** | Offline permitido; live permanece `BLOCKED_EXTERNAL_POWERSHELL_POLICY`; nunca Bypass. |
| HIGH-07 falsa durabilidad/timeouts | **CERRADO COMO LÍMITE EXPLÍCITO** | Request-bound, no resume tras host/restart; probes y smokes separados. |
| MEDIUM-01 config no transaccional | **NO CERRADO DEL TODO** | Rollback y conflictos están diseñados, pero el CAS propuesto no es atómico; ADV-R2-H03. |
| MEDIUM-02 baseline por conteo | **CERRADO** | IDs, kind, fingerprints y source hashes. |
| MEDIUM-03 cleanup parcial | **CERRADO PARA ERROR PRIMARIO / ABIERTO PARA ACQUIRE SIN TICKET** | Helper no-throw preserva el mismo objeto; no puede limpiar una request acquire cuyo resultado nunca llegó; ADV-R2-H01. |
| MEDIUM-04 pipes/señales | **CERRADO EN DISEÑO** | Dos drains, redacción incremental, backpressure y señales Windows. |
| MEDIUM-05 path/reparse TOCTOU | **CERRADO EN EL MODELO DECLARADO** | Handle real bloquea mutación/rename; name-surrogate y root/file identity se revalidan. |
| MEDIUM-06 2+2/provenance | **CERRADO COMO GATE EXTERNO** | PID/session/generation y Claude real obligatorios. |
| LOW-01 substring/EXACT | **CERRADO** | Parser contractual y comandos futuros `[DESIGN]`. |

## Revalidación completa de R1

| Hallazgo R1 | Estado en R2 | Resultado adversarial |
|---|---|---|
| HIGH-01 OneDrive rechazado | **CERRADO** | Política basada en bit name-surrogate y positivo real; evidencia Windows validada abajo. |
| HIGH-02 fault no observable/durable | **PARCIAL / BLOCKED** | Ya existen schema, store, status, doctor, startup y repair en scope. Falta una transición nominal compatible con la regla de clear; ADV-R2-H02. |
| MEDIUM-01 cleanup oculta error | **CERRADO** | `cleanup_never_raises`, captura `BaseException`, relanza mismo objeto y solo añade note pública (`plan:626-641`). |
| MEDIUM-02 rollback pisa config | **PARCIAL / BLOCKED** | Detecta drift antes de operaciones, pero compare→replace sigue intercalable; ADV-R2-H03. |
| MEDIUM-03 SHA prematuro | **CERRADO** | Task 6A.5 genera identidad/SHA únicamente tras consumidor definitivo (`plan:719`). |

## Hallazgos HIGH

### ADV-R2-H01 — Cancelar durante el acquire inmediato deja un lease oculto sin ticket cancelable

**Severidad:** HIGH. **Impacto concreto:** degradation de liveness y ownership oculto hasta TTL.

**Evidencia primaria:**

- El state machine conserva grant inmediato cuando autoridad y cola están libres (`plan:149`) y Task 2B lo formaliza con `ticket_id=null` (`plan:536`).
- El protocolo prohíbe que cancel libere un grant inmediato (`plan:172,575`).
- El entrypoint alto inicializa `ticket=None`, espera `/session/acquire` y solo asigna ticket si recibe la respuesta queued (`plan:599-610`). Su `finally` limpia únicamente cuando `ticket is not None` (`plan:626-636`).
- La llamada real usa `await asyncio.to_thread(...)` (`tools/dayz_mcp/server.py:299-320`). Python 3.14 implementa `to_thread` como `await loop.run_in_executor(...)` (`C:/Python314/Lib/asyncio/threads.py:12-25`): cancelar el await no deshace una operación de thread que ya está ejecutando el HTTP.
- El lease real admite `source_ticket_id=None` (`tools/dayz_mcp/session_coordination.py:113-122`).
- Task 3 pide respuesta active perdida dentro de `asyncio.to_thread`, pero simultáneamente mantiene grant inmediato=403 (`plan:573-575`). V10 exige estado propio final limpio (`plan:236-237`). Esos requisitos no pueden satisfacerse en la rama inmediata actual.

**Interleaving reproducible:**

1. `session_acquire_wait` entra en `/session/acquire` con `ticket=None`.
2. La task MCP se cancela; la coroutine entra en `finally`, no tiene ticket y no hace cleanup.
3. El thread HTTP continúa; el daemon completa WAL/ledger/snapshot/clear y publica un grant inmediato.
4. La respuesta active se pierde. El cliente no conoce token ni ticket.
5. `session_cancel` no puede identificarlo y devuelve 403 por contrato. El lease bloquea la cola hasta TTL.

Esto recrea exactamente la clase de fallo que BUG-046 intenta eliminar, aunque el origen sea una respuesta acquire perdida en vez de un release ciego.

**Corrección requerida `[DESIGN]`:** el entrypoint alto debe crear un identificador cancelable conocido por el cliente antes de que el daemon pueda publicar autoridad. La opción más simple es que su primer paso siempre produzca un ticket/operación pendiente —también cuando el daemon está libre— y que la autoridad nazca después desde el wait vivo. Alternativamente, se necesita un request-id cliente idempotente, persistido en el lease, que permita cancelar/revocar la rama inmediata sin conocer token. Mantener el immediate grant legacy para la tool low-level no obliga a usarlo en `session_acquire_wait`.

**Tests obligatorios adicionales:** barreras antes de recibir `/acquire`, después de publish y antes de response; cancelación del await mientras el handler sigue; respuesta inmediata perdida; cancel repetido por el identificador previo; status final propio none; siguiente waiter progresa sin esperar TTL. Mover también `returned_active=True` después de construir con éxito la respuesta final, para que un fallo de postprocesado no suprima cleanup.

### ADV-R2-H02 — El WAL normal no tiene un estado que pueda limpiarse

**Severidad:** HIGH. **Impacto concreto:** protocolo no ejecutable o limpieza indebidamente confundida con reparación administrativa.

**Evidencia:**

- El store define `state` como armed/fault/repaired y afirma de forma normativa que `clear` solo acepta `state=repaired` (`plan:308-328`). Task 2A.1 vuelve a exigir que solo repaired sea limpiable (`plan:492`).
- Grant nominal exige WAL armed → ledger → publish → snapshot → clear → response (`plan:167-169,185,294,534-536`). Release nominal exige arm → invalidate → terminal → snapshot → clear (`plan:173,502,530`).
- Ninguna de esas rutas normales cambia el marker a `state=repaired`; ese estado se reserva expresamente a la reparación admin tras escribir compensación/reconciliación (`plan:332,500`).

Por tanto hay solo dos implementaciones posibles y ambas contradicen la spec:

- llamar `clear` sobre `armed`, que el store debe rechazar; o
- marcar una operación nominal como `repaired`, haciendo indistinguible “commit normal completado” de “operador auditó una reparación” y activando la semántica especial de startup para repaired.

**Corrección requerida `[DESIGN]`:** cerrar la máquina durable con estados distintos. Dos diseños válidos posibles:

- permitir un clear nominal de `armed` únicamente por CAS exacto de `fault_id`, hash, generación propietaria y fase terminal después de confirmar snapshot; `repaired` queda exclusivo de admin; o
- introducir un estado terminal normal distinto de repaired, escribirlo por CAS tras snapshot y definir su recovery/clear idempotente.

El plan debe decir qué ocurre si el CAS terminal normal falla o si hay crash antes/después de esa transición. Los tests V5c/V20b/V22 deben incluir un camino nominal completo que demuestre que el marker desaparece sin admin y que un marker intermedio siempre recupera fault.

### ADV-R2-H03 — “SHA esperado + `os.replace`” no es CAS atómico frente a escritores no cooperativos

**Severidad:** HIGH. **Impacto concreto:** corruption/lost update en `.claude.json` o `config.toml` globales.

**Evidencia:**

- La R2 adquiere sidecar locks, relee SHA antes de cada replace/rollback y afirma que CAS protege contra escritores no cooperativos (`plan:666-672`). El propio plan reconoce que el lock solo es cooperativo (`plan:672`).
- El runtime local describe `os.replace` únicamente como rename que sobrescribe destino; no recibe expected hash/version ni ofrece compare-and-swap (`os.replace.__doc__`, Python 3.14 local).
- Existe una ventana inevitable en la secuencia propuesta: `hash(dest)==esperado` → escritor Claude/Codex escribe bytes ajenos → `os.replace(staged,dest)` sobrescribe esos bytes. El sidecar no detiene a ese escritor.
- V11b inyecta “antes de replace” (`plan:239,668`), pero el plan no exige explícitamente la barrera **después del último compare y antes del replace**, que es la frontera defectuosa.

**Corrección requerida `[DESIGN]`:** no afirmar CAS atómico con esas primitivas. Antes de implementación debe escogerse y probarse una de estas fronteras:

- gate de quiescencia que cierre/impida escritores de ambos hosts durante la transacción, más locks cooperativos y CAS lógico; o
- una primitive/servicio de actualización que ofrezca exclusión o versión atómica real en Windows y esté verificada localmente.

En cualquier caso, rollback jamás debe restaurar bytes completos tras observar identidad/hash inesperados. Añadir una barrera determinista inmediatamente después del último compare y antes del replace; el resultado seguro es conflicto sin overwrite. Si la primitive elegida no puede pasar ese test, la config real no se muta.

## Hallazgos MEDIUM

### ADV-R2-M01 — La reparación multi-evento no es idempotente tras crash intermedio

**Severidad:** MEDIUM. **Impacto concreto:** ledger duplicado/ambiguo y métricas de reconciliación incorrectas.

Admin repair escribe al menos `session_grant_revoked|session_release_reconciled`, después `coordination_audit_repaired` y solo entonces marca `state=repaired` (`plan:332,500`). El writer actual garantiza atomicidad por evento individual, no por batch (`tools/dayz_mcp/runtime_state.py:55-86`).

Si el proceso cae después del primer evento o después de `coordination_audit_repaired` pero antes del CAS del marker, el marker sigue fault y el retry repite eventos. El schema no contiene `repair_phase`, event-id determinista ni regla de deduplicación por `fault_id`, aunque el texto promete retry idempotente.

**Corrección requerida `[DESIGN]`:** persistir por CAS la fase de reparación después de cada evento durable, o incluir identificadores deterministas y definir que startup/admin escanea y reconoce exactamente los eventos ya appendidos antes de continuar. Añadir crash barriers después de cada append y verificar una sola compensación/reconciliación semántica por fault.

### ADV-R2-M02 — “Marker ausente” tiene dos interpretaciones incompatibles

**Severidad:** MEDIUM. **Impacto concreto:** recovery ambiguo ante drift/corrupción externa.

`plan:428` dice que marker ausente equivale a no fault. La línea siguiente dice que, si snapshot y marker divergen, el marker es fuente pero la discrepancia aparece como fault y nunca se infiere limpio (`plan:429`). Para snapshot `audit_fault!=null` + marker ausente no pueden ser ciertas ambas reglas.

**Corrección requerida `[DESIGN]`:** definir explícitamente la tabla startup para las cuatro combinaciones snapshot null/fault × marker absent/armed|fault|repaired. La opción fail-closed coherente es que snapshot-fault + marker absent produzca fault sintético no autorreparable; marker absent solo equivale a limpio cuando el snapshot también es null y su schema/revisión son válidos.

### ADV-R2-M03 — Audit gate busy en el primer acquire no tiene semántica de cola

**Severidad:** MEDIUM. **Impacto concreto:** degradation de liveness por 503 transitorio.

La R2 define que un claim FIFO con audit gate busy vuelve queued (`plan:167,290-296`), pero el grant inmediato usa el mismo protocolo WAL/audit sin especificar qué hace si el gate está busy (`plan:536`). El objetivo del usuario es que la ejecución se ponga en cola, no que termine por contención interna.

**Corrección requerida `[DESIGN]`:** el entrypoint alto siempre-ticket propuesto en ADV-R2-H01 debe convertir también esta contención en queued/progress. Añadir fixture autoridad libre + queue vacía + audit gate ocupado: `session_acquire_wait` no devuelve 503 y adquiere cuando el gate queda libre.

## Verificación positiva de OneDrive/Windows

No se conserva el hallazgo R1-H01; la R2 lo resuelve con evidencia primaria suficiente.

- `CreateFileW` y sus argumentos están declarados exactamente en SDK `um/fileapi.h:90-101`.
- `GENERIC_READ` está en `um/winnt.h:10252`; `FILE_SHARE_READ/WRITE/DELETE` en `um/winnt.h:15303-15305`.
- `FILE_FLAG_SEQUENTIAL_SCAN`, `FILE_FLAG_BACKUP_SEMANTICS` y `FILE_FLAG_OPEN_REPARSE_POINT` están en `um/WinBase.h:143-148`.
- `FILE_ID_INFO` y `GetFileInformationByHandleEx` están en `um/WinBase.h:9257-9262,9383-9391`.
- El bit name-surrogate es exactamente `0x20000000` (`um/winnt.h:15736-15741`). El tag real observado en `LF_VStorage_dev/tools` fue cloud `0x9000E01A`, sin ese bit.
- Python 3.14 sobre la ruta real devolvió `st_reparse_tag=0` para los cloud reparse no-name-surrogate —comportamiento esperado porque sigue esos puntos— y `realpath` permaneció en la ruta canónica.
- Prueba temporal con la misma apertura propuesta: write al archivo, `os.replace` y rename del padre devolvieron access denied mientras el handle estuvo vivo. Esto confirma que PowerShell puede reabrir para lectura pero no se puede intercambiar el objeto/ancestro nominal durante la vida del hijo en el filesystem local probado.
- Task 6A incluye positivo real, symlink/junction/name-surrogate negativos, root identity drift, handle lifetime y SHA final post-consumidor (`plan:711-729`).

Riesgo residual aceptable: OneDrive o un editor con handles incompatibles puede hacer fallar la apertura; el comportamiento correcto es fail-closed y reintento posterior, no relajar el share mode ni inyectar secretos.

## Preservación exacta de error primario

R1-M01 queda cerrada. La R2 captura `BaseException`, relanza el mismo objeto y hace que cleanup capture internamente sus errores (`plan:626-641`). Añadir una note no cambia tipo, mensaje, `__cause__` ni `__context__`; V10b los exige explícitamente.

El ajuste pendiente de `returned_active` descrito en ADV-R2-H01 no cuestiona esa preservación: evita que una excepción de postprocesado sea interpretada falsamente como entrega active completada.

## Baseline, gates y compatibilidad

- El drift de `PowerShellProcessGuard` no se normaliza ni se arregla incidentalmente. Task 1 se detiene antes del primer diff si persiste (`plan:456`).
- PowerShell continúa bloqueado externamente bajo Restricted/NotSigned; no se usa Bypass ni se declara live PASS (`plan:141,733,791`).
- Claude real 2+2 sigue abierto y exige proveniencia PID/session/generation; los procesos locales no lo sustituyen (`plan:764-772`).
- Snapshot legacy normaliza `audit_fault` a null, eventos legacy siguen legibles y las primitivas low-level permanecen (`plan:426-433`).
- Rollback con marker activo está correctamente prohibido (`plan:435-443`). Una vez corregida la máquina de estados del marker, esta política es ejecutable.
- Los archivos de Task 2A incluyen todos los puntos omitidos en R1: runtime state, coordinator, daemon, loopback, doctor, admin CLI y tests (`plan:477-504`).
- La secuencia de SHA del launcher está corregida: parser/fixtures → consumidor → tests → identity/SHA/registro definitivo (`plan:711-719`).

## Falta validar en implementación/live

Estas validaciones no rebajan los HIGH anteriores y no pueden contarse como cerradas solo por el plan:

1. `ctypes.argtypes/restype`, root directory handle y FILE_ID_INFO sobre la ruta real en el test Windows definitivo.
2. Crash injection real por proceso en cada frontera WAL/ledger/publish/snapshot/terminal/clear/response.
3. Admin repair reintentado tras cada append individual y tras CAS/state transition.
4. Config mutation con barrera después del último compare, no solo antes del compare.
5. Smoke de timeout en una sesión nueva Codex y otra Claude.
6. Gate PowerShell solo tras autorización RemoteSigned y gate 2+2 solo con sesiones Claude reales.

## Análisis de falsos positivos

- No se marca como fallo que los cloud reparse devuelvan tag cero mediante `os.stat`; la documentación y el resultado local indican que esos puntos resolubles se siguen. Lo que debe rechazarse es name-surrogate y drift de identidad.
- No se exige persistir lease/ticket ni introducir un job runner. El WAL solo conserva obligación de reconciliación, no autoridad.
- No se considera error que un crash antes de clear fuerce admin repair aun si el ledger quizá completó: es fail-closed deliberado.
- No se exige que PowerShell live o Claude 2+2 cierren antes de TDD offline; sí impiden cierre total.
- No se atribuye a BUG-046 el import drift de lifecycle ni se permite arreglarlo de paso.

## Correcciones mínimas para una R3 verde

1. Hacer que `session_acquire_wait` conozca un identificador cancelable antes de cualquier grant y cubrir acquire response perdida/cancelada.
2. Definir un terminal WAL normal distinto de admin repair, o autorizar clear nominal con CAS/fase/generación exactos.
3. Resolver la ventana compare→replace de config con una frontera realmente exclusiva o gate de quiescencia verificable; añadir la barrera exacta al test.
4. Hacer idempotente el batch de admin repair por fase/event-id y resolver la tabla snapshot/marker divergente.
5. Repetir la revisión adversarial sobre el nuevo hash antes del primer diff.

## Veredicto final

**BLOCKED**

La R2 no autoriza Task 1 todavía. Puede editarse el plan y ejecutar probes read-only, pero no iniciar producción ni TDD bajo este documento como spec aprobada.
