# 6927 — lote D: la caja se ocupa por quien sostiene un puerto de juego (commit `4e227a3`)

Ficha: `dayz_test_run(port=2302, wait_for_box_s=600)` volvió `succeeded` en 13 s y a los 20 s cayó un servidor AJENO de 6 min
que sostenía el 2302; `session_status.box` decía libre. Diagnóstico (`reviews/2026-09-04-lote-V/DIAGNOSTICO-6927.md`): el sondeo
enumeraba solo `DayZDiag_x64.exe` por nombre y sacaba `ports_in_use` del argv; nada en el MCP mata PIDs ajenos: lanzó ENCIMA.

## Cambio (5 ficheros de producto + 2 tests; Codex: R9 tres rondas (1 CRITICAL + 8 MAJOR en R1, todo aplicado o documentado; R3 BLOQUEANTES=0))
- `orphan_guard.py`: `snapshot_udp_port_holders()` lee la tabla de sockets UDP del sistema (psutil, respaldo `netstat -ano -p UDP`),
  nombra cada PID desde un snapshot ToolHelp; `known=False` si ninguna fuente contestó.
- `process_lifecycle.py`: `ProcessLifecycle(port_probe=...)`; `foreign` gana filas por imagen DayZ sin ficha (`source: "port"`, sin
  pid); `ports_in_use` sale del SO para procesos registrados o DayZ; `port_scan_known` (sondeo ilegible = caja ocupada); `start_run`
  rechaza con sondeo FRESCO (`active_run_exists`, audit `port_in_use_foreign`: holder no registrado con imagen DayZ en cualquier
  puerto, o cualquier imagen en el puerto pedido; `port_scan_unknown` si el sondeo falla). `wait_for_box_s` espera solo, nunca limpia.
- `daemon.py` cablea el sondeo; descripciones de `session_status`/`dayz_test_run` y README documentan el contrato.
- Tests: `tests/test_box_port_occupancy.py` (15) y `tests/test_orphan_guard_udp.py` (8). Control positivo vivo en este host: la
  tabla vio UDP 2302/2304 sostenidos por el pid 45428 = `DayZDiag_x64.exe` (run registrado de la sesión par) → no foreign, sí en
  `ports_in_use`.
- Suite completa: ver `SUITE-D.txt` (+ `SUITE-D-final.txt` si se repitió con la máquina en reposo).

## Auditoría R9 (tres ángulos, Codex gpt-5.6-sol, sesiones frescas en paralelo)
- `AUDIT-RACE.md` (carreras/caché/rendimiento), `AUDIT-ADMIN.md` (admin/reboot/recovery/máquina de estados), `AUDIT-LOSS.md`
  (pérdida de datos ajena/seguridad/fail-closed); `REVIEW-CODEX.md` (corrección + mutantes). Veredictos y lo aplicado: ver
  cada fichero y `RONDA2-D.md` si hubo segunda ronda.

## Lo que NO cubre (para el gate in-game de Guillermo)
- El instante entre el sondeo y el bind del lanzamiento (misma ventana que ya tenía el sondeo por nombre).
- Un servidor ajeno que arranque DESPUÉS del propio no se detecta hasta el siguiente lanzamiento; `session_status.box.foreign`
  sí lo lista mientras vive (ficha hermana cabd: la comprobación previa en `run_batch_f1.ps1` sigue siendo de LFHeli).
- Gate in-game: reproducir con un `DayZServer_x64.exe`/DayZDiag ajeno en el 2302 y comprobar `active_run_exists` +
  `foreign=[{port:2302, image:..., source:port}]` y que `wait_for_box_s` espera. El daemon vivo corre este lote solo tras su
  siguiente re-spawn (generación estable).

Ficheros archivados: COMUN.txt, COMUN-R2.txt, DELTA-R2b.txt, BRIEF-REVIEW.txt, BRIEF-AUDIT-RACE.txt, BRIEF-AUDIT-RACE2.txt, BRIEF-AUDIT-ADMIN.txt, BRIEF-AUDIT-LOSS.txt, BRIEF-REVIEW-R2.txt, BRIEF-AUDIT-RACE-R2.txt, BRIEF-AUDIT-ADMIN-R2.txt, BRIEF-AUDIT-LOSS-R2.txt, REVIEW-CODEX.md, AUDIT-RACE.md, AUDIT-ADMIN.md, AUDIT-LOSS.md, DIFF-D.patch, DIFF-D2.patch, SUITE-D.txt, SUITE-D2.txt, suite-full-loteD.log, suite-full-loteD2.log, focal-r2b.log, RONDA2-D.md, AUDIT-ADMIN-R2.md, AUDIT-LOSS-R2.md, AUDIT-RACE-R2.md, REVIEW-CODEX-R2.md, REVIEW-CODEX-R3.md, SUITE-D3.txt.

## Anexo 2026-09-06 - ficha b8b0 (fixture dependiente del host)

`test_netstat_is_the_fallback_when_psutil_is_absent` dejaba sin parchear la reintento por PID de `snapshot_udp_port_holders()`
(`_toolhelp_lookup`), asi que con un proceso vivo cuyo pid fuera 45428 la fila volvia con nombre y el test se ponia rojo
(Reserva lo midio el 2026-09-04 con un conhost.exe). Arreglo en la capa de tests: el lookup se inyecta (None en el test del
fallback; `(45428, "DayZServer_x64.exe")` en un test nuevo que fija el reintento). Modulos test_orphan_guard_udp +
test_box_port_occupancy + test_box_port_wait: 63 OK. Commit `8ff937e`.
