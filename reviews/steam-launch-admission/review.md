Write está deshabilitado en esta sesión (solo lectura estricta), así que entrego el informe aquí.

# Revisión adversarial — steam-launch-admission (solo lectura)

Worktree `C:/Users/guill/worktrees/dayz-mcp-steam-guard`, rama `fix/steam-launch-admission`. Sin ejecución, sin mutaciones.

## Veredicto: **SOUND-with-fixes**

No encontré ningún falso PASS ni ninguna vía por la que un writer conserve permiso de mutación tras perder autoridad. Los ocho puntos del encargo están en el código, no solo en el plan. Los hallazgos son dos defectos reales de acotamiento/disponibilidad (P2), tres menores (P3) y una evidencia de pruebas que hay que cerrar.

## Verificado correcto (no rehacer)

| Punto | Dónde |
|---|---|
| 1. Opt-in booleano estricto, sellado y legacy | `process_lifecycle.py:1956` (`type(...) is not bool`), `dayz_test_request.py:39-66` (variantes canónicas con y sin campo), `:359-392`, `dayz_test_worker.py:325` (solo client/offline) |
| 2. Preparación tras admisión, antes de commit | `process_lifecycle.py:2372-2402` frente a `_commit_reserved` en `:2412`; el RLock se suelta exactamente una vez (`:2381`/`:2389`) y la readmisión reusa la MISMA `authority` (`:2398`) |
| 3. PID + FILETIME | `steam_preflight.py:101-119`; `steam_launch_guard.py:48-78`. `old_stable` se fija con `started` del inicio (`:90`) ⇒ un proceso reciente no envejece hacia la rama limitada; `limited` exige `expected == initial and not steam_restart_fallback` (`:99`) ⇒ reparar el DWORD sobre el mismo proceso antiguo no hereda la espera incondicional |
| 4. EOF/cancel/TTL/deadline | `steam_prepare_helper.py:29-42` (EOF ⇒ cancel), `:78-86` (sleep interrumpible). `reservation_active` llama a `_expire_due` (`session_coordination.py:1347`) ⇒ la desaparición abrupta sí se detecta en el poll de 50 ms |
| 4b. Sonda de clientes bloqueante | `steam_prepare_supervisor.py:206-213`: hilo aparte, solo lectura, nunca emite permiso tarde (`:191-197`); el monitor sigue comprobando autoridad y deadline |
| 5. Job Object | `steam_prepare_supervisor.py:48` (`0x2000\|0x800`, clase 9) y `steam_prepare_helper.py:123` (`CREATE_BREAKAWAY_FROM_JOB`, stdio DEVNULL); límite declarado en `:118-119` |
| 6. Exención por argv real | `process_lifecycle.py:2372` decide por `-server` en argv, no por etiqueta de rol; `_steam_mutation_allowed` (`:2222-2247`) exige cuarentena limpia, snapshot legible, identidad coincidente y `-server` real |
| 7. Recheck final read-only | `:2401` y `:2618`, sin segunda espera tras commit |
| 8. Códigos y plazos | `dayz_test_worker.py:29-54` (lista cerrada con los ocho códigos), `lifecycle_cli.py:43` y `app_main.py:312` (235 s solo start) |
| Fence de limpieza no acreditada | `steam_prepare_supervisor.py:226-228` + `claim()` `:136-144`; pruebas con subproceso real colgado en `test_steam_launch_guard.py:256-282` |

## P2-1 — La sonda de clientes retiene `_operation_lock` sin cota

- **Archivo:** `process_lifecycle.py:2224` (`with self._operation_lock:` en `_steam_mutation_allowed`), llamado desde el hilo de `steam_prepare_supervisor.py:207-213`.
- **Mecanismo:** el supervisor blindó su bucle contra una sonda lenta, pero la sonda toma el lock global y dentro de él llama a `self.guard.snapshot(pid)` y `self.argv_of(pid)` (`:2240-2242`), consultas al SO que pueden colgarse. El presupuesto de 215 s acota la preparación, no la sonda.
- **Escenario:** consent=true, Steam stale, el helper pide permiso, `argv_of` se bloquea sobre un handle que no responde. `prepare()` devuelve `steam_prepare_timeout` correctamente, pero el hilo huérfano mantiene `_operation_lock` indefinidamente: se cuelgan todos los start/stop/reap del daemon, incluido el `acquire()` de `:2389`.
- **Consecuencia:** disponibilidad, no corrección; nunca se emite permiso tarde.
- **Fix mínimo:** `acquired = self._operation_lock.acquire(timeout=10.0)`; si falla, `return False` (falla cerrado ⇒ `steam_client_active`), liberar en `finally`. Test: `argv_of` que bloquea + otro `start_run` concurrente que debe seguir respondiendo tras la cota.

## P2-2 — El reintento del worker puede abrir una SEGUNDA preparación con presupuesto nuevo

- **Archivo:** `dayz_test_worker.py:512-517`.
- **Mecanismo:** `invoke_start()` se reintenta ante cualquier `DayzTestWorkerError` con `operation_id`. La rama nueva de `_pre_admission_rejection` (`:428-439`, `run_id not in result`) evita el reintento cuando el daemon **responde** con código Steam nombrado — es lo que cierra `worker-retry-repro.txt` — pero no cubre el fallo de **transporte**, donde no hay código que inspeccionar. El contrato exige presupuesto absoluto "sin reiniciar el contador" (§5) y el contador nace en cada llamada a `SteamPreparationGate.prepare` (`steam_prepare_supervisor.py:161`).
- **Escenario:** 235 s de transporte (`app_main.py:312`) contra 215 + 5 de preparación + admisión + spawn + HTTP deja ~15 s de margen. Un start que agota la preparación y luego tarda en el spawn hace expirar el transporte; el worker reintenta y, si la primera ya soltó `claim()`, arranca otros 215 s.
- **Atenuantes reales:** con la primera en vuelo, `claim()` falla ⇒ `steam_prepare_busy` (`:2374-2376`); y `MAX_OPERATION_PIN_S=300` invalida la reserva y `authority_active` corta la segunda. El tope existe, pero es accidental.
- **Fix mínimo:** no reintentar el `start` de cliente ante fallo de transporte (usar `status` idempotente con el `operation_id`), o subir el deadline de transporte por encima de `215+5+margen` para que el timeout deje de ser el modo de fallo esperable.

## P3

- **P3-1** `steam_prepare_supervisor.py:71` + `:83-96`: `Queue(maxsize=8)` con `put_nowait`; si se llena, el `except Exception` mata el lector y el `finally` pierde también el `eof`, y el supervisor gira hasta los 215 s en vez de fallar rápido. Latente (hoy ~4 mensajes máximo). Fix: cola sin límite o flag `broken` tratado como EOF.
- **P3-2** `process_lifecycle.py:1180`: el gate por defecto es real. Construirlo es inocuo, pero cualquier consumidor o test que no inyecte el doble y arranque argv de cliente ejecutará un helper real contra el Steam de la máquina, lo que `GATES.md:49` prohíbe. Endurecimiento opcional: exigir el gate explícito en el constructor.
- **P3-3** (limitación declarada, no corrección pedida) `steam_preflight.py:211-216`: la rama `old_stable_unobserved` resuelve el log rotado del Steam antiguo, pero si el Steam que **nosotros** reiniciamos escribe >128 KiB tras el marcador, `limited` no aplica (`steam_launch_guard.py:99` exige `expected == initial`) y el resultado es `steam_prepare_timeout` sobre un Steam sano. A medir en la prueba real.

## Evidencia de pruebas — a cerrar antes de fusionar

- `targeted-tests.txt`: 499 OK, 8 skips. Consistente.
- `guard-tests.txt` y `worker-retry-repro.txt`: intermedias; sus tres fallos ya están corregidos en el fuente (`test_steam_launch_guard.py:443`, `:513-518`; `dayz_test_worker.py:432`). No los reporto como vigentes.
- **`suite.txt` está incompleta**: sin línea de resumen y sin trazas, pero el log verboso ya muestra FAIL/ERROR en `test_bug046_startup_deadlock` (`:316-335`), `test_db05_preflight_diagnostics` entero (`:977-989`), `test_h8_distributed_gate` (`:1211`), `:1654` y `test_security_runtime_audit` (`:2815`). Sin traza no puedo distinguir regresión de fallo ambiental del worktree, y `baseline-tests.txt` (398 tests) no cubre esos módulos. Codex debe terminar la corrida y comparar módulo a módulo contra la misma suite en `1c4c0ee`. Sospecha principal: `test_db05_preflight_diagnostics` importa `_MutableSteamProvider` de `tests/test_steam_preflight.py`, no modificado, que podría no exponer `process_creation_ticks`.

## Fuera de alcance

La revisión estática no prueba Steamworks: la validación in-game DayZ+VPP con cierre rápido y relanzamiento sigue pendiente, como declara el contrato. No audité la reconexión de clientes MCP stdio antiguos ni el supervisor/reload, excluidos explícitamente.