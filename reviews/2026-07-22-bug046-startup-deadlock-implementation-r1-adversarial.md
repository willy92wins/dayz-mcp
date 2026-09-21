# Revisión adversarial independiente — implementación R9

## Veredicto

BLOCKED

La implementación incorpora la arquitectura principal aprobada —elección global dentro de `run_daemon`, marker old-or-new y recovery reentrante—, pero contiene dos fallos HIGH y tres gaps MEDIUM que comprometen seguridad/liveness. La suite focal pasa porque no implementa el V24 aprobado ni cubre las fronteras que revelan los fallos.

No se modificó producción durante esta revisión. El único archivo creado es este informe.

## Snapshot revisado

Los cinco archivos permanecieron estables durante la pasada final:

| Archivo | SHA-256 |
|---|---|
| `tools/dayz_mcp/identity_migration.py` | `186C28A0C880930C97C4FE5643D18FA2B7469A8080A2695C0F86ED394B727EB8` |
| `tools/dayz_mcp/daemon.py` | `59DCEA564C2CCA3DFF283A3CC36B0E2CE79E39A513F5F9C1A23D25FE58ABC83F` |
| `tools/tests/test_identity_migration.py` | `917E813CC2F91B36EE539AD293DBE9893C919CDDDD8DD9544D5037EEFF720065` |
| `tools/tests/test_daemon_security_gate.py` | `583253212D84A9B2F8F742919C9830705BF7F7207AE856B95BEED4A72C578A14` |
| `tools/tests/test_bug046_startup_deadlock.py` | `21036EDB56C5A354F1320908C84AD0E1939BD399DF3DDD501ED4259B2404A663` |

No existe `.git` en `DayZ_MCP_dev`, por lo que no fue posible contrastar un diff/base commit. La revisión se hizo contra el contenido actual congelado y el plan R9 aprobado.

## Hallazgos

### HIGH-01 — roll-forward borra auxiliares externos sin validar provenance

**Evidencia:** cuando existe un receipt final, `_recover_backup_transaction` valida únicamente receipt+backup y luego llama `_safe_unlink_owned` sobre pending receipt, `marker.next` y marker final (`tools/dayz_mcp/identity_migration.py:688-701`). `_safe_unlink_owned` solo comprueba que el path sea un archivo regular/no-reparse y lo elimina; no valida schema, phase, hash ni bytes (`identity_migration.py:105-122`).

Esto contradice R9: final receipt es autoridad del **commit de datos**, pero marker/artifact drift debe fallar cerrado con cero deletes (`plans/2026-07-22-bug046-lease-queue-liveness-plan.md:78,856`). Un receipt válido no demuestra que cualquier archivo regular aparecido posteriormente bajo un nombre auxiliar pertenezca a la transacción.

**Reproducción en temp:** se creó un backup+receipt válido mediante `ensure_runs_v1_backup`, después se escribieron bytes externos distintos en:

- `runs-backup-transaction.json`;
- `runs-backup-transaction.next`;
- `runs-backup-receipt.pending`.

Una segunda llamada terminó con success y eliminó los tres (`external_aux_survive=False False False`). La suite no cubre esta combinación; solo prueba drift antes de publicar marker y partial backup (`tools/tests/test_bug046_startup_deadlock.py:48-127`).

**Impacto:** **corruption/destructive cleanup** de evidencia o artifacts externos, más falso roll-forward limpio.

**Fix requerido `[DESIGN]`:** separar autoridad de commit y autoridad de cleanup.

1. Validar primero el receipt+backup final.
2. Si existe marker, exigir marker R9 exacto para esos paths/source y phase `receipt_pending`; marker corrupto/ajeno produce conflict y cero deletes.
3. `pending` y `marker.next` no son estados normales después del rename final. Si existen, preservarlos como conflict salvo que un marker válido demuestre byte-exactamente una frontera propia admitida.
4. Sin marker, un receipt final exacto puede aceptarse como commit legacy/R9 ya limpio, pero no autoriza borrar auxiliares desconocidos.
5. Añadir fixtures independientes para marker, next y pending: absent, propio exacto/parcial y externo distinguible, con receipt final válido. Los externos deben conservar bytes.

### HIGH-02 — una entrada Python válida elude por completo la clasificación writer

**Evidencia:** `_argv_targets_dayz_mcp` solo reconoce el bootstrap absoluto o un par exacto `-m dayz_mcp`; si no encuentra `-m`, devuelve `False` (`tools/dayz_mcp/identity_migration.py:221-236`). Sin embargo, `tools/dayz_mcp/__main__.py:3-7` importa y ejecuta el mismo `server.run`, por lo que esta invocación es válida cuando el entorno tiene las dependencias:

`python <ruta-absoluta>/dayz_mcp/__main__.py --embedded ...`

El probe directo de `_argv_targets_dayz_mcp` devolvió `False` para esa argv. En modo `--embedded` o default, el proceso sí migra/activa `runs.json` y `coordination.json`, pero el scanner lo trata como Python extranjero. La elección R9 dentro de `run_daemon` no protege embedded.

Los tests solo cubren `-m dayz_mcp` y el bootstrap absoluto (`tools/tests/test_identity_migration.py:319-372`); no cubren el entrypoint real `__main__.py`.

**Impacto:** **safety/liveness race**. Un embedded directo puede aparecer entre quiescence y bind, no ser blocker y activar una segunda autoridad en otro puerto sobre el mismo `RuntimePaths.root`. En el mismo puerto, el bind puede evitar dos listeners, pero no repara la ventana de migración/estado persistente.

**Fix requerido `[DESIGN]`:** reconocer el entrypoint canónico `dayz_mcp/__main__.py` —y cualquier otro script realmente soportado que alcance `server.run`— mediante path absoluto/canónico verificado. La opción fail-closed más simple es clasificar todas esas formas directas como writer, incluso con `--client`; solo `-m dayz_mcp --client` con gramática exacta conserva la exención aprobada. Añadir fixtures direct/default/embedded/daemon/client y una integración con proceso real.

### MEDIUM-01 — bind no saludable todavía produce exit code 0

**Evidencia:** `_bind_with_reclaim` devuelve `None` tanto si otro daemon está healthy como si el listener está vivo, usa otra key o es inidentificable/unreclaimable (`tools/dayz_mcp/daemon.py:320-356`). `run_daemon` transforma cualquier `None` en salida 0 sin re-probar health (`daemon.py:444-455`).

El test actual codifica este falso success: mockea `_bind_with_reclaim` para devolver `None` y exige `result == 0` (`tools/tests/test_daemon_security_gate.py:38-67`). Solo el loser del startup lock tiene ya el contrato correcto healthy→0 / ausente→75 (`daemon.py:420-426`; tests `test_daemon_security_gate.py:69-100`).

**Impacto:** **degradation/false success**. Una shell o supervisor cree que existe autoridad utilizable aunque el puerto pertenezca a un listener foreign-key/unhealthy; los clientes terminan en `daemon_unavailable`.

**Fix requerido `[DESIGN]`:** devolver un outcome tipado desde `_bind_with_reclaim` o re-probar health exacto en `run_daemon`. Solo `already_healthy` devuelve 0; `live_unreclaimable`/foreign/unhealthy devuelve `DAEMON_STARTUP_CONTENDED` 75 o un error estable no-success. Actualizar el test que hoy exige 0 y añadir listener foreign-key/unresponsive.

### MEDIUM-02 — la suite “V24” no ejecuta V24

**Evidencia:** el plan exige N `ClientRuntime`, wrapper pre-R7, launch directo, dos puertos/mismo root, daemon mixed-version, muerte del ganador en todas las fronteras, segunda ola progresiva, PID reuse y external daemon (`plans/2026-07-22-bug046-lease-queue-liveness-plan.md:856-860`).

`test_bug046_startup_deadlock.py` contiene tres tests de artifacts y un test de ownership del lock con un subprocess (`tools/tests/test_bug046_startup_deadlock.py:32-169`). No instancia `ClientRuntime`, no ejecuta `run_daemon`, no crea N candidatos, no prueba cross-port/mixed-version/direct/embedded/bootstrap/PID reuse ni mata un winner en migration/bind/activation. `test_daemon_security_gate.py` usa mocks unitarios y tampoco compone esos elementos (`tools/tests/test_daemon_security_gate.py:25-124`).

La suite focal ejecutada pasó: `23 passed, 19 subtests passed in 0.94s`. Ese GREEN no prueba el criterio de aceptación R9 y permitió HIGH-01/HIGH-02.

**Fix requerido `[DESIGN]`:** implementar el V24 multiproceso descrito en el plan. Como mínimo:

- barrera N clients con un wrapper pre-R7 y conteos exactos de spawn/migration/bind/generation;
- launch `-m --daemon` y entrypoint directo;
- dos puertos/mismo root con un único coordinador;
- winner death en pre/post migration, bind, activation y status accreditation, seguido de una segunda ola healthy;
- daemon legacy no participante, embedded/bootstrap y external listener;
- PID reuse/identity drift;
- ninguna terminación fuera de procesos fixture propios.

### MEDIUM-03 — faltan gates por syscall y fail-closed de reparse del lock

**Evidencia de tests:** `fault_injector` solo se invoca en fronteras lógicas posteriores a writes/replaces (`tools/dayz_mcp/identity_migration.py:841-921`). `_exclusive_write` no ofrece inyección por chunk/write/fsync (`identity_migration.py:76-95`). El test reentrante mata después de siete phases publicadas (`tools/tests/test_identity_migration.py:145-198`), no durante cada chunk/syscall, short-write, fsync, unlink ni segundo recovery, pese a la exigencia R9 (`plan:79,856`).

**Evidencia de paths:** `daemon_startup_election` crea `RuntimePaths.root` y valida el archivo leaf, pero no rechaza/estabiliza un root/ancestro junction o name-surrogate (`identity_migration.py:125-180`). `_exclusive_gate_lock` abre el lock de migration y, si mide cero, escribe un byte sin validar reparse ni identidad path↔handle (`identity_migration.py:184-210`). R9 exige que reparse/identity drift/lock I/O fallen cerrados (`plan:858`).

**Impacto:** gaps de **safety/validation**. Un crash dentro de metadata I/O sigue sin prueba; un lock retargetable puede dividir la elección o hacer write a un target inesperado.

**Fix requerido `[DESIGN]`:** introducir un adaptador I/O inyectable o monkeypatches sistemáticos para cada `os.write/fsync/replace/unlink/open`, incluyendo cero progreso y segundo crash. Para locks, validar la cadena canónica según la política name-surrogate del plan, abrir por handle, cotejar identidad path↔handle antes y después de adquirir, y nunca escribir a un leaf reparse/retargetado. Añadir fixtures symlink/junction/identity swap y residual regular positivo.

## Aspectos correctos observados

- El startup lock es global por `RuntimePaths.root`, no por puerto, y usa ownership de byte-range, no existencia del archivo (`identity_migration.py:125-180`).
- Un loser de election no entra en migration; hace probe final y devuelve 75 sin health (`daemon.py:420-426`).
- El ganador mantiene el lock durante migration, bind, activation y health accreditation (`daemon.py:428-474`).
- Marker inicial se publica antes del backup; phases llevan revisión y SHA previo (`identity_migration.py:484-625,834-906`).
- Un transient blocker después del backup deja marker recuperable y el retry puede rollback/reintentar (`identity_migration.py:733-780,814-921`; `test_identity_migration.py:121-143`).
- Marker/source/artifact drift en la ruta sin final receipt se conserva fail-closed; partial owned prefix se recupera (`identity_migration.py:733-780`; `test_bug046_startup_deadlock.py:48-127`).
- Receipt legacy válido y backup byte-exacto continúan aceptándose (`identity_migration.py:393-440,696-701`).
- La clasificación `-m dayz_mcp` exige client único y gramática conocida; daemon/embedded/default/ambiguo/unknown bloquean (`identity_migration.py:221-278`; `test_identity_migration.py:353-372`).

## Orden de corrección recomendado

1. Corregir HIGH-01 y añadir sus fixtures antes de volver a ejecutar recovery sobre artifacts reales.
2. Cerrar el entrypoint directo HIGH-02 y añadirlo a V23.
3. Separar bind healthy de unavailable y corregir el test de falso success.
4. Implementar el V24 multiproceso real y los fault gates por syscall/reparse.
5. Ejecutar dos veces suite focal + global sobre hashes estables; después repetir revisión adversarial independiente.

## Conclusión

BLOCKED

La implementación no está lista para despliegue vivo. Puede borrar artifacts externos durante un roll-forward válido, puede ignorar un writer real lanzado mediante `dayz_mcp/__main__.py`, y aún devuelve success ante un bind no saludable. Además, los tests actuales no ejercitan el V24 que justificó el GREEN del plan. Se requiere una R2 de implementación después de corregir estos hallazgos; no se autoriza la aplicación real ni el gate vivo todavía.
