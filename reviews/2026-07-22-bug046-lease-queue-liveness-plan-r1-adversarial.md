# Revisión adversarial R1 — BUG-046 lease queue liveness

**Fecha:** 2026-07-22  
**Revisor:** Codex, segunda pasada independiente  
**Plan revisado:** `plans/2026-07-22-bug046-lease-queue-liveness-plan.md`  
**SHA-256 verificado:** `CF8DE555EEF67DAE2B12080E1D1EF01C594FBA673C0168325E3F20F113B88194`  
**Informe R0 contrastado:** `reviews/2026-07-22-bug046-lease-queue-liveness-plan-adversarial.md`

## Veredicto

**BLOCKED**

La R1 mejora de forma sustancial el protocolo causal de grant/cancel/release y separa honestamente los gates externos de PowerShell y Claude. Sin embargo, no es seguro arrancar cambios con este plan todavía:

1. La política que rechaza cualquier ancestro reparse rechaza la ruta real y única que el propio plan registra, porque `LF_VStorage_dev` y sus ancestros bajo OneDrive son `ReparsePoint`.
2. El plan promete un fence de fallo visible y `doctor FAIL`, incluso cuando falla la compensación de un grant, pero no diseña ningún estado observable/durable ni incluye `doctor.py` en el alcance. Tras reinicio tampoco existe un mecanismo que preserve ese fail-closed.

Hay además tres huecos de severidad media: preservación del error primario durante cleanup, concurrencia de la transacción de configuración y orden de generación del SHA del launcher.

## Scorecard

| Área | Nota | Resultado |
|---|---:|---|
| Causalidad prepared → commit → publish | 8/10 | Bien cerrada salvo fallo doble de compensación |
| Cancelación 200/202/cancelling | 9/10 | Diseño coherente y comprobable |
| Fence de release/auditoría | 5/10 | Bloquea grants, pero no tiene representación observable/durable completa |
| Launcher y secreto en env | 6/10 | Buen aislamiento del token; regla reparse incompatible con la ruta real |
| Instalador transaccional | 7/10 | Backup/rollback bien orientado; falta control de escritores concurrentes |
| Baseline y gates externos | 9/10 | Drift y gates externos quedan declarados sin falsos PASS |
| Viability tests | 8/10 | Amplios, pero no cubren los bloqueos descritos abajo |

## Revalidación completa de hallazgos R0

| R0 | Estado en R1 | Evidencia / conclusión |
|---|---|---|
| HIGH-01 cancel vs `grant_inflight` | **CERRADO** | El ticket permanece identificable, `cancel_requested` se instala antes de I/O y el claim revalida antes de publicar (`plan:121-152`, `plan:223-266`). 202 significa resolución en curso y 200 ausencia/no-publicabilidad (`plan:141-147`, `plan:276-284`). |
| HIGH-02 grant causal / lease oculto | **PARCIAL** | Prepared y commit ocurren sin `_active`, con una sola publicación posterior (`plan:133-145`, `plan:253-266`, `plan:435`). Esto cierra la carrera normal. No cierra el fallo de la compensación: se promete `doctor` inconsistente sin diseñar el estado ni el fence durable; véase ADV-R1-H02. |
| HIGH-03 release audit tardía/busy | **PARCIAL** | Gate busy conserva queued y `_release_audit_inflight` impide grant (`plan:121-129`, `plan:148`, `plan:431-435`). El fallo/stall no tiene ruta real a status/doctor ni persistencia tras restart; véase ADV-R1-H02. |
| HIGH-04 trust amplio del launcher | **PARCIAL** | ID → registro administrado path+SHA y caller sin path/hash es una frontera mejor (`plan:101-113`, `plan:328-351`). La regla de ancestros hace inutilizable la entrada real; véase ADV-R1-H01. |
| HIGH-05 token heredado por AddonBuilder | **CERRADO EN DISEÑO** | `dayz-test.ps1` captura y elimina Process env antes del build, y solo repone alrededor de `lifecycle_cli` con `finally` (`plan:111`, `plan:603-609`). V19 exige fake AddonBuilder sin secretos. |
| HIGH-06 PowerShell `Restricted` | **CERRADO PARA TRABAJO OFFLINE / GATE EXTERNO ABIERTO** | No se usa `Bypass`; `BLOCKED_EXTERNAL_POWERSHELL_POLICY` queda explícito y requiere acción del usuario (`plan:115-117`, `plan:617-619`). No se atribuye PASS falso. |
| HIGH-07 request no durable / timeout host | **CERRADO COMO DECISIÓN LIMITADA** | La R1 declara explícitamente long-poll request-bound, máximo 30 min y no durable tras perder host/request (`plan:7`, `plan:127`, `plan:159-174`). Los probes de timeout se declaran temporales y la validación real queda gateada. Es una limitación de producto consciente, no una garantía de cola durable. |
| MEDIUM-01 config no transaccional | **PARCIAL** | Hay parse/validate, escritura atómica y rollback byte-exacto (`plan:535-564`), pero no lock/CAS frente a escritores concurrentes; véase ADV-R1-M02. |
| MEDIUM-02 baseline count insuficiente | **CERRADO** | Dos capturas estables, IDs/kind/fingerprint/hash y stop antes del primer diff si persiste el import drift (`plan:379-407`). El estado actual no verde se conserva como blocker, no como baseline aceptado silenciosamente. |
| MEDIUM-03 cleanup incompleto | **PARCIAL** | Toda salida no-active entra en `finally`, pero el pseudocódigo puede sustituir la excepción primaria si cleanup lanza; véase ADV-R1-M01. |
| MEDIUM-04 pipe/signal | **CERRADO EN DISEÑO** | Drains concurrentes, salida acotada, SIGTERM/Ctrl-C y prohibición de matar DayZ están cubiertos por V16/V21 (`plan:212-217`, `plan:611-615`). |
| MEDIUM-05 TOCTOU/reparse | **NO CERRADO** | Doble validación e identidad reducen la ventana (`plan:108-109`, `plan:599-601`), pero el rechazo indiscriminado de ancestros invalida la ruta autorizada. Queda además una ventana residual entre segunda validación y apertura del script por PowerShell. |
| MEDIUM-06 Claude 2+2/provenance | **CERRADO HONESTAMENTE COMO GATE EXTERNO** | H9 conserva `BLOCKED_EXTERNAL_CLAUDE_SESSION` hasta una sesión Claude real y no permite sustituirlo por procesos locales (`plan:163-174`, `plan:646-656`). |
| LOW-01 test substring/comando futuro | **CERRADO** | Los criterios piden schema/semántica y el comando focal futuro está etiquetado `[DESIGN]`, no `[EXACT]` (`plan:379-407`, `plan:660-675`). |

## Hallazgos bloqueantes

### ADV-R1-H01 — La política anti-reparse rechaza el launcher real

**Severidad:** HIGH — degradación funcional total del launch limpio.

**Evidencia:**

- El plan registra únicamente `LF_VStorage_dev/tools/dayz-test.ps1` (`plan:108`) y exige rechazar cualquier ancestro reparse tanto en contrato como en tests (`plan:109`, `plan:599-601`).
- Comprobación directa con `Get-Item` sobre la ruta registrada: el archivo es regular, pero `LF_VStorage_dev\tools`, `LF_VStorage_dev`, `DayZ Projects`, `Documentos` y `OneDrive` tienen el atributo `Directory, Archive, ReparsePoint`.
- Por tanto, V17 no protege solamente contra un homónimo o swap: impide siempre llegar a acquire/spawn en el único caso aprobado.

**Impacto:** la solución al problema comunicado por Claude —que Codex dispare el launch con el token solo en el env del hijo— seguiría sin existir operacionalmente. No es un gate externo: es una contradicción interna del diseño.

**Corrección requerida `[DESIGN]`:** definir una política compatible con el almacenamiento real antes de TDD. Como mínimo, el plan debe distinguir archivo final reparse de ancestros OneDrive aprobados, registrar/verificar explícitamente la topología permitida y probar la ruta real positiva además de symlink/junction negativos. Si el modelo de amenaza exige resistir un swap activo del ancestro, debe diseñarse ejecución desde bytes ya verificados o una primitiva Windows de identidad estable; el doble hash por path no elimina por sí solo la ventana final.

**Viability test que falta:** la ruta real registrada bajo OneDrive pasa; un archivo final link/reparse, un ancestro no registrado y una retargetización de identidad fallan antes de inyectar secretos.

### ADV-R1-H02 — `doctor FAIL` y el fail-closed de auditoría no tienen estado observable/durable

**Severidad:** HIGH — autoridad/auditoría inconsistente tras fallo doble o reinicio.

**Evidencia:**

- La R1 promete que release audit failed/stalled queda visible y produce `doctor FAIL`, y que el fallo de `session_grant_revoked` marca auditoría inconsistente (`plan:129`, `plan:144`, `plan:148`, `plan:431`).
- El snapshot vigente solo expone `revision`, `active`, `releasing`, `granting`, `handoff_pending`, `queue` y `cleanup_workers` (`tools/dayz_mcp/session_coordination.py:1237-1258`).
- `doctor` exige y valida esos campos, pero no conoce fallo/stall de release ni inconsistencia de auditoría (`tools/dayz_mcp/doctor.py:365-490`).
- Task 2 modifica coordinación/tests, pero el plan no incorpora un contrato de status persistido ni `doctor.py`; además declara que el schema de snapshot permanece (`plan:353-359`, `plan:409-457`).
- `claimable` enumera fences transitorios (`plan:121`) pero no un latch de inconsistencia posterior a fallo de compensación. Si el proceso reinicia, los marcadores solo en memoria desaparecen aunque el ledger pueda haber quedado con grant sin compensación.

**Impacto:** el plan no puede cumplir sus propias aserciones de visibilidad/doctor. En el peor caso, el ledger contiene un `session_granted` cuya compensación falló; el proceso no publica ese active, pero una reclamación posterior o un restart puede continuar sin una señal fail-closed reconstruible.

**Corrección requerida `[DESIGN]`:** añadir al contrato una avería de auditoría latched con causa, lease/ticket/evento y timestamp; excluirla de `claimable`; exponerla en snapshot/status; persistirla o reconstruirla de forma determinista tras restart; y hacer que `doctor` emita FAIL mientras no exista reparación administrativa explícita. Incluir `runtime_state.py`, `doctor.py` y sus tests en el alcance si son los puntos reales usados. Las claves/nombres finales deben verificarse al implementar.

**Viability tests que faltan:** fallo de compensación bloquea todo grant posterior; status lo expone; doctor falla; restart conserva/reconstruye el bloqueo; no se limpia por un wait/acquire ordinario.

## Hallazgos medios

### ADV-R1-M01 — El `finally` no garantiza preservar la excepción primaria

**Severidad:** MEDIUM — diagnóstico incorrecto y cleanup ambiguo.

El pseudocódigo ejecuta `await shield_bounded(...)` directamente dentro de `finally` (`plan:522-524`), mientras el texto exige preservar el error original (`plan:529`). En Python, si esa espera lanza, su excepción sustituye a la que causó la salida. `shield_bounded` no tiene un contrato definido de no-throw.

**Corrección requerida `[DESIGN]`:** especificar que cleanup devuelve un resultado/degradación y nunca lanza al caller, o capturar explícitamente error primario y error de cleanup y relanzar el primero. V10b debe verificar tipo/mensaje/causa primaria, no solo que hubo excepción.

### ADV-R1-M02 — Rollback byte-exacto puede pisar cambios concurrentes de config

**Severidad:** MEDIUM — corrupción/lost update de configuración ajena.

La R1 propone snapshot de bytes, `add`, patch, verify y restauración completa en fallo (`plan:554-560`). La transacción actual tampoco tiene exclusión interproceso (`tools/install_mcp.py:838-870`). Si Claude, Codex u otro instalador modifica otra entrada entre snapshot y rollback, restaurar todos los bytes borra ese cambio aun cuando el parser preserve entradas en el camino nominal.

**Corrección requerida `[DESIGN]`:** definir lock interproceso para ambos archivos durante toda la transacción o compare-and-swap por hash antes de cada replace/rollback, con conflicto explícito y recuperación que no sobrescriba bytes desconocidos. Añadir fixture con escritor concurrente simulado.

### ADV-R1-M03 — El SHA “post-cambio” se genera antes de cambiar el consumidor

**Severidad:** MEDIUM — secuencia del plan no ejecutable tal como está escrita.

Task 6A Step 2 ordena generar la entrada con SHA post-cambio (`plan:601`), pero el cambio de `dayz-test.ps1` ocurre en Step 4 (`plan:605`). Al aplicar Step 4, el hash recién generado queda obsoleto y el launcher fail-closed rechaza el script.

**Corrección requerida `[DESIGN]`:** crear primero schema/parser y fixtures sin fijar la entrada final; modificar y validar el consumidor; generar luego el SHA final; ejecutar un test de drift negativo y otro positivo sobre el artefacto ya definitivo.

## Riesgos residuales no bloqueantes

- La cola sigue siendo request-bound y se cancela al perder host/request. La R1 lo declara; no debe describirse después como ejecución durable o indefinida.
- La doble validación path+SHA reduce, pero no elimina, el intervalo entre la segunda lectura y la apertura por `powershell.exe -File`.
- El import drift `PowerShellProcessGuard` sigue siendo un gate real de baseline. La R1 hace lo correcto al detenerse antes del primer diff si persiste; no debe “normalizarse” como fallo aceptado.
- `BLOCKED_EXTERNAL_POWERSHELL_POLICY` y `BLOCKED_EXTERNAL_CLAUDE_SESSION` pueden permanecer abiertos durante TDD offline, pero no permiten declarar PASS live/E2E.

## Condiciones exactas para una R2 verde

1. Resolver ADV-R1-H01 con una política y test positivo sobre la ruta real OneDrive.
2. Resolver ADV-R1-H02 con contrato observable, doctor, persistencia/recovery y fence de claim.
3. Cerrar ADV-R1-M01/M02/M03 en la secuencia y viability tests.
4. Mantener sin rebaja los gates externos PowerShell/Claude y el stop por baseline drift.
5. Repetir revisión adversarial del plan actualizado antes del primer cambio de producción.

Hasta entonces, se puede investigar o editar el plan, pero **no arrancar implementación/TDD como plan aprobado**.
