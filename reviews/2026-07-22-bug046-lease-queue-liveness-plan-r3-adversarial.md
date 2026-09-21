# Auditoría adversarial R3 — BUG-046 lease queue liveness

## TL;DR

**Veredicto literal: BLOCKED**

La R3 cierra los dos primeros HIGH de R2 y casi todo el tercero:

- `operation_id` existe antes de I/O, el high-level siempre encola, cancel-before-arrival instala tombstone y el low-level nuevo también puede revocarse sin token.
- El WAL ya tiene terminal normal `completed`, estados de repair separados, tabla startup exhaustiva y repair por fases/event IDs.
- La config ya no usa un falso CAS por rename: abre ambos destinos con handles Windows `share=0` y mantiene exclusión durante compare/write/verify/rollback.

Queda un HIGH en la recuperación de config. La escritura in-place son syscalls separadas (`WriteFile` → `SetEndOfFile` → `FlushFileBuffers`). Un crash tras `WriteFile` y antes de truncate/flush puede dejar bytes que no coinciden ni con `original_hash` ni con `target_hash`. El plan dice simultáneamente que `prepared|partial` se revierte y que cualquier hash desconocido es conflicto sin overwrite. Por tanto una caída puede dejar `.claude.json` o `config.toml` truncado/mixto y sin recuperación automática, contradiciendo el gate que pretende validar.

Hay tres ajustes medios: comparación canónica de `write_once` frente a timestamp/generación inyectados, alcance de doctor para saturación de tombstones y una frase contradictoria sobre clear fallido de un WAL `completed`.

## Identidad y alcance

- Plan auditado: `plans/2026-07-22-bug046-lease-queue-liveness-plan.md`.
- SHA-256 esperado y verificado: `936E2132D02FE6E868BF1F718E865EC304C8433296557CA91B9242AE655E9BE4`.
- Contrastadas íntegramente R0, R1 y R2.
- Fuentes primarias: código actual de coordinator/runtime/installer, Python 3.14 local y Windows SDK 10.0.26100.0.
- No se editó plan ni producción.

## Matriz de las correcciones R2 → R3

| Hallazgo R2 | Estado R3 | Evidencia |
|---|---|---|
| H01 acquire inmediato oculto | **CERRADO EN DISEÑO** | `operation_id` previo, enqueue always-ticket, tombstone-first, active derivado por operation y cleanup aun con `ticket=None` (`plan:168-207,315-363,667-737`). |
| H02 WAL armed no limpiable | **CERRADO EN DISEÑO** | `armed→completed`, clear solo `completed|repaired`, snapshot previo al terminal y crash matrix startup (`plan:188-206,365-411,585-650`). |
| H03 falso CAS config | **PARCIAL / BLOCKED** | Exclusión `share=0` cierra escritor concurrente vivo; recovery de torn in-place write sigue contradictorio, ADV-R3-H01. |
| M01 repair no idempotente | **PARCIAL** | `write_once(event_id)` + `repair_phase` resuelven el orden, pero falta canonizar metadata inyectada, ADV-R3-M01. |
| M02 marker ausente ambiguo | **CERRADO** | Tabla completa; solo snapshot-null + absent es limpio (`plan:397-411,520-521`). |
| M03 audit gate busy inicial | **CERRADO** | High-level enqueue no toca audit gate; low-level busy encola y V8b prohíbe 503 (`plan:207,258-266,359,629,689`). |

## Revalidación íntegra de R0

| R0 | Resultado en R3 |
|---|---|
| HIGH-01 cancel vs grant-inflight | **CERRADO** para ticket, high-level y low-level nuevo mediante operation/tombstone. |
| HIGH-02 causalidad grant/wait | **CERRADO EN DISEÑO**: prepared + commit causal + publish + snapshot + completed + response. |
| HIGH-03 release audit tardío | **CERRADO EN DISEÑO**: busy queued, terminal físico previo, fault/stall durable. |
| HIGH-04 trust launcher | **CERRADO**: root FILE_ID + relative path + SHA + handle fijado. |
| HIGH-05 secreto en AddonBuilder | **CERRADO**: scrub antes de build, env temporal solo lifecycle. |
| HIGH-06 Restricted/NotSigned | **GATE EXTERNO HONESTO**: nunca Bypass ni falso PASS. |
| HIGH-07 falsa durabilidad/timeouts | **CERRADO COMO LÍMITE**: request-bound explícita y smokes por host. |
| MEDIUM-01 config no transaccional | **PARCIAL / BLOCKED** por crash/torn write; ADV-R3-H01. |
| MEDIUM-02 baseline por conteo | **CERRADO** por IDs/kind/fingerprints/hashes y stop-before-diff. |
| MEDIUM-03 cleanup incompleto | **CERRADO**: operation existe aunque ticket no haya llegado y el error primario se conserva. |
| MEDIUM-04 pipes/señales | **CERRADO EN DISEÑO**. |
| MEDIUM-05 TOCTOU/reparse | **CERRADO EN EL MODELO VERIFICADO**. |
| MEDIUM-06 2+2 no autónomo | **GATE EXTERNO HONESTO**. |
| LOW-01 tests débiles | **CERRADO** por parser contractual y etiquetas `[DESIGN]`. |

## Revalidación íntegra de R1

| R1 | Resultado en R3 |
|---|---|
| HIGH-01 OneDrive rechazado | **CERRADO**: cloud no-name-surrogate positivo y name-surrogate negativo. |
| HIGH-02 fault no durable/doctor | **CERRADO EN DISEÑO**: store, snapshot, status, doctor, startup y admin repair están en archivos/TDD. |
| MEDIUM-01 cleanup enmascara error | **CERRADO**: mismo `BaseException`, helper no-throw, flag active posterior al payload final. |
| MEDIUM-02 rollback config pisa cambios | **PARCIAL / BLOCKED**: share=0 evita interleaving vivo; crash recovery incompleta. |
| MEDIUM-03 SHA prematuro | **CERRADO**: registro final después del consumidor definitivo. |

## Hallazgo HIGH

### ADV-R3-H01 — Un crash durante la escritura in-place produce un hash “desconocido” que el recovery se niega a restaurar

**Severidad:** HIGH.  
**Impacto concreto:** corruption recuperable solo manualmente de configuración global y pérdida operacional del MCP en uno o ambos hosts.

**Evidencia del plan:**

- Se preparan journal/backups y un journal `prepared|partial` debe revertirse a original; al mismo tiempo, hash distinto de original/target es conflicto sin overwrite (`plan:534`).
- Los destinos se escriben mediante syscalls independientes: `SetFilePointerEx(0)`, `WriteFile`, `SetEndOfFile`, `FlushFileBuffers`, reread/verify (`plan:762-768`).
- Task 5.4 promete matar el proceso tras cada write y recuperar a original si el journal está prepared/partial, pero limita recovery a hashes original|target (`plan:768`).

**Evidencia SDK local:**

- `WriteFile` es una llamada separada y devuelve `lpNumberOfBytesWritten` (`Windows Kits/10/Include/10.0.26100.0/um/fileapi.h:1152-1161`).
- `SetEndOfFile` es otra llamada (`fileapi.h:1045-1050`).
- `FlushFileBuffers` es otra llamada (`fileapi.h:382-387`).
- `SetFilePointerEx` también es independiente (`fileapi.h:1098-1106`).

**Interleaving concreto:**

1. Original `O` tiene longitud mayor que target `T`.
2. Journal/backups quedan durablemente `prepared`; ambos handles siguen exclusivos.
3. `WriteFile(T)` termina. El archivo contiene `T + cola_de_O` porque aún no se ejecutó `SetEndOfFile`.
4. El proceso muere.
5. Recovery abre ambos handles, pero el hash no es `original_hash` ni `target_hash`.
6. La regla “unknown = conflict, zero overwrite” prohíbe restaurar el backup. El host conserva JSON/TOML mixto o inválido.

También debe contemplarse `WriteFile` que informa menos bytes que los solicitados. El plan no exige todavía un write-all loop que valide `lpNumberOfBytesWritten`.

**Corrección requerida `[DESIGN]`:** cerrar expresamente la recuperación de estados transitorios propios. Una opción compatible con la arquitectura elegida:

- journal/backup restringido conserva original y target exactos, transaction id, file identity y longitudes;
- el writer usa un loop write-all y registra/verifica cada cantidad escrita;
- recovery reconoce de forma determinista streams que solo pueden resultar de su propia escritura in-place —prefijo de target + sufijo de original, target completo antes de truncate y demás fronteras enumeradas— y puede restaurarlos a original;
- cualquier byte stream que no coincida con original, target ni un estado transitorio demostrablemente propio sigue siendo conflicto sin overwrite;
- un escritor externo posterior al crash se inyecta antes de recovery y debe producir conflicto, no restauración.

Alternativamente, el plan puede declarar recuperación manual para torn writes, pero entonces debe retirar las afirmaciones de rollback automático/crash recovery y no aplicar config real hasta que el usuario acepte ese riesgo. Para GREEN bajo el criterio actual, el camino automático prometido debe ser ejecutable.

**Tests obligatorios:** target más corto, igual y más largo; `WriteFile` parcial; kill después de cada chunk, después de WriteFile antes de SetEnd, después de SetEnd antes de Flush, después de cada archivo y después de journal committed; external writer después del crash; parse/hash exactos tras rollback y ningún contenido impreso.

## Hallazgos MEDIUM

### ADV-R3-M01 — `write_once` no define cómo compara eventos entre generaciones

**Severidad:** MEDIUM.  
**Impacto:** repair legítimo puede quedar bloqueado como “mismo event_id, payload distinto”.

El writer actual inyecta `timestamp_utc` y `daemon_generation` al escribir (`tools/dayz_mcp/runtime_state.py:72-75`). R3 escanea current+backups y rechaza un event ID duplicado con payload distinto (`plan:393-395`). Tras crash/restart, timestamp y daemon generation necesariamente cambian si se reconstruye el evento.

**Corrección requerida `[DESIGN]`:** definir el payload canónico de igualdad. Puede almacenar en el marker los campos generados del primer intento y reutilizarlos, o comparar únicamente el núcleo semántico cerrado excluyendo metadata que el writer añade. V22b debe reintentar desde otra daemon generation y aceptar el mismo núcleo, pero rechazar reason/lease/operation/fault distintos.

### ADV-R3-M02 — La saturación de tombstones promete doctor visible, pero Task 3 no incluye doctor

**Severidad:** MEDIUM.  
**Impacto:** degradation de admisión global difícil de diagnosticar.

El contrato establece cap 128 y admission fence visible en doctor/status (`plan:361`). Task 3 añade el comportamiento en coordinator/loopback/server, pero su lista no incluye `doctor.py` ni `test_doctor.py` (`plan:656-677`). Task 2A sí toca doctor, pero ocurre antes de introducir tombstones.

**Corrección requerida `[DESIGN]`:** incluir doctor y sus tests en Task 3 —o declarar explícitamente en Task 2A el futuro schema del admission fence— y verificar finding WARN/FAIL estable, conteo/cap sin identidades completas y recuperación automática tras TTL.

### ADV-R3-M03 — Clear fallido de `completed` tiene dos destinos distintos

**Severidad:** MEDIUM.  
**Impacto:** implementación divergente del recovery nominal.

El contrato general dice que si “compensación o clear falla” el marker pasa a fault (`plan:190`). La implementación TDD dice que un clear fallido de `completed` permanece cleanup-pending y se reintenta, sin nuevos grants (`plan:623`); Task 2A también permite retry de completed (`plan:595`). `completed→fault` no aparece en la máquina cerrada.

**Corrección requerida `[DESIGN]`:** aclarar que un clear **normal** fallido conserva `completed` y bloquea hasta retry; solo un clear fallido dentro de la ruta de compensación/repair conserva fault/repairing. Añadir test que pruebe el estado exacto y que no reemita ledger ni entregue token.

## Verificaciones positivas principales

### Operation/tombstone

- Operation id se crea antes del primer await y se conserva aunque ticket siga null (`plan:356-363,456,691-737`).
- Enqueue siempre devuelve ticket y jamás publica autoridad (`plan:315-321,359`).
- Cancel instala tombstone antes de lookup; request tardía no crea ticket/lease (`plan:181,191-193,360`).
- Saturación fail-closed rechaza nuevas operaciones antes de ticket/grant (`plan:361`).
- Low-level nuevo liga immediate a operation y puede revocarlo sin token (`plan:363,629,667-677`).
- V7c/V10c mantienen vivo el thread subyacente y exigen progreso del siguiente waiter sin esperar TTL.

### WAL/recovery

- Estados separados `armed|fault|completed|repairing|repaired` y transiciones terminales explícitas (`plan:365-411`).
- Orden grant/release correcto: snapshot precede CAS completed y clear precede response (`plan:188-206,623-650`).
- Solo terminales completed/repaired se limpian.
- Tabla startup cubre marker missing/corrupt, armed/fault, repairing, completed, repaired y mismatch.
- Repair usa event IDs deterministas, scan bajo writer lock y repair phase CAS; solo falta canonizar metadata.

### Config exclusivity

- Las firmas SDK citadas son reales.
- Abrir ambos destinos `GENERIC_READ|GENERIC_WRITE, share=0` antes del primer byte elimina la carrera compare→write con escritores vivos.
- Si el segundo handle no abre, no se escribe ninguno.
- No se ejecuta `mcp add` mientras se pretende exclusión ni se usa `os.replace` sobre el destino.
- Journal/backups preceden a la primera mutación y un hash externo desconocido no se sobrescribe.

### Resto del contrato

- Error primario exacto preservado; `returned_active` cambia después del payload final.
- Política OneDrive/name-surrogate y handle del launcher no se rebajan.
- SHA registry se genera tras el consumidor definitivo.
- Baseline drift sigue siendo stop-before-diff.
- PowerShell Restricted y Claude 2+2 permanecen gates externos honestos.
- TDD incluye código real de runtime/status/doctor/admin y no introduce job runner ni autoridad recuperable.

## Caveats y validación live pendiente

- El gate PowerShell no puede cerrarse sin autorización RemoteSigned; nunca Bypass.
- El gate mixto no puede cerrarse sin dos Claude reales con proveniencia externa.
- La espera es request-bound, no durable tras cierre/restart del host.
- El import drift `PowerShellProcessGuard` puede detener Task 1 antes del primer diff y no pertenece a BUG-046.

## Condiciones mínimas para R4 verde

1. Resolver torn-write recovery o declarar/aceptar explícitamente recovery manual y ajustar criterios.
2. Definir comparación canónica de `write_once` entre generaciones.
3. Añadir doctor al scope del admission fence.
4. Unificar semántica de clear fallido en `completed`.
5. Repetir revisión sobre el nuevo hash antes de Task 1.

## Veredicto final

**BLOCKED**

No se autoriza aún implementación/TDD bajo R3 como spec aprobada. La arquitectura central de lease/WAL/launcher ya está en condiciones cercanas a GREEN; el bloqueo restante está concentrado en la garantía de recuperación de los dos archivos globales de configuración.
