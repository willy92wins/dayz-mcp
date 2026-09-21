GREEN

# Auditoría adversarial R4 — BUG-046 lease queue liveness

## Veredicto

El plan R4, SHA-256 `FEC1AD3691F89C500501C40182A0EF16821877C7E705E2C807DAF21F9AA74E97`, queda aprobado para comenzar Task 1/TDD. No permanece ningún HIGH, MEDIUM ni contradicción que haga insegura o no ejecutable la implementación. Los gates PowerShell y Claude siguen abiertos correctamente como validaciones externas; no impiden trabajo offline ni autorizan cierre total.

## Revalidación R0–R3

| Bloque | Estado R4 | Evidencia |
|---|---|---|
| Grant FIFO ciego / release | CERRADO | Solo wait vivo reclama; release/expiry notifican, no conceden (`plan:183-221,660-689`). |
| Cancel/grant-inflight/respuesta perdida | CERRADO | Operation id previo, always-ticket high-level, tombstone-first, source operation en lease y V7/V7c/V10c (`plan:315-381,708-780`). |
| Audit busy/fail/stall | CERRADO | Busy queda queued; fault/stall bloquean y aparecen en status/doctor. |
| Causalidad grant → snapshot → response | CERRADO | WAL armed → ledger → publish → snapshot → completed → clear → response (`plan:202-220,407-431`). |
| Fault durable/restart/admin repair | CERRADO | Estados cerrados, tabla startup exhaustiva, write-once y repair phases (`plan:389-431,624-636`). |
| OneDrive/TOCTOU launcher | CERRADO | Cloud no-name-surrogate positivo; symlink/junction negativos; root/file identity, SHA y handle share-read sostenido. |
| Secreto en PowerShell/AddonBuilder | CERRADO | Captura/scrub temprano; env solo alrededor de lifecycle; drains redactados. |
| Error primario | CERRADO | Helper no-throw, mismo BaseException y `returned_active` posterior al payload final. |
| Config de hosts | CERRADO EN DISEÑO/TDD | Handles simultáneos share=0, journal y recovery reentrante exacto (`plan:532-547,805-811`). |
| Tombstone admission fence | CERRADO | Cap fail-closed, status/doctor FAIL, TTL recovery y tests (`plan:273,379,714`). |
| SHA registry | CERRADO | Se genera después del consumidor definitivo. |
| Baseline/gates externos | CERRADO HONESTAMENTE | Drift detiene antes del diff; nunca Bypass ni 2+2 simulado. |

## Config recovery — comprobación adversarial

El clasificador cubre las longitudes y fronteras exigidas:

- `C == O` y `C == T` cubren no-write/target completo.
- `C == T[:k] + O[k:]` cubre write parcial y target menor/igual antes de truncate.
- `C == T[:k]` cuando `len(O) < k <= len(T)` cubre extensión parcial de target mayor.
- `write_all` exige progreso positivo y total exacto antes de `SetEndOfFile` y `FlushFileBuffers` (`plan:534-543`).
- Ambos archivos se clasifican antes de escribir; identidad o bytes externos causan conflicto con cero writes (`plan:545`).
- Antes del rollback se fsync-a `recovery_source` y el estado `restoring_original`; un segundo crash se clasifica contra `O` y `S` y reanuda desde cero (`plan:545-547`).
- Original/target/manifest/file identity existen antes del primer byte y permanecen privados bajo ACL (`plan:532`).
- Las APIs citadas existen en el SDK local: `WriteFile` `fileapi.h:1152-1161`, `SetEndOfFile:1045-1050`, `SetFilePointerEx:1098-1106`, `FlushFileBuffers:382-387`, `GENERIC_READ|GENERIC_WRITE` en `winnt.h:10252-10253`.

No se confunde deriva externa con torn propio: solo secuencias byte-exactas demostrables son restaurables; identidad distinta o edición distinguible conserva conflicto y evidencia.

## Write-once, admission y terminal cleanup

- `write_once` compara JSON canónico redacted excluyendo exclusivamente `timestamp_utc` y `daemon_generation`, los dos campos inyectados por el writer real en `runtime_state.py:72-75`. Conserva event/reason/decision/fault/lease/ticket/operation/client y falla cerrado ante mismo ID con core distinto (`plan:411,632`).
- Tombstone saturation entra expresamente en doctor/tests como `OPERATION_TOMBSTONES_SATURATED`, FAIL, solo count/cap y recuperación tras TTL (`plan:273,714`).
- Un clear I/O fallido de `completed|repaired` conserva el terminal como cleanup-pending, bloquea claims y reintenta solo clear; no reemite ledger, no muta autoridad y no se degrada a fault (`plan:204,208,407,431,662`).

## Arquitectura y seguridad completas

- No hay job runner ni autoridad persistente recuperable.
- Tombstone cap y FIFO cap fallan cerrados.
- Cancel foreign sigue sin poder afectar lease ajeno.
- Snapshot/marker corrupto, ausente o divergente nunca se interpreta como limpio salvo la fila null+absent validada.
- Admin repair requiere HMAC, TTY, fault id de status y confirmación exacta.
- Launcher no acepta token/path/hash desde argv; `shell=False`, sin Bypass.
- Token/identidad no se guardan en journal, WAL, registry, audit ni evidencias.
- Legacy/rollback quedan definidos; rollback de coordinación está prohibido con marker activo.
- TDD enumera crash barriers, concurrencia, respuesta perdida, writes parciales, rollback reentrante, pipes, señales y suites focal/global.

## Gates que permanecen abiertos sin invalidar GREEN

- `BLOCKED_EXTERNAL_POWERSHELL_POLICY`: live `.ps1` requiere autorización RemoteSigned.
- Gate real 2 Claude + 2 Codex: requiere sesiones Claude con proveniencia externa.
- Baseline actual: si persiste el import drift de `PowerShellProcessGuard`, Task 1 se detiene antes del primer diff.

## Conclusión

R4 es suficientemente precisa, fail-closed y verificable para implementación TDD. GREEN autoriza empezar Task 1; no autoriza declarar terminado el gate live ni omitir ninguno de los blockers externos documentados.
