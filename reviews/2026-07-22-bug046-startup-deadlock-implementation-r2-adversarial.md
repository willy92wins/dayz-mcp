# Revisión adversarial independiente — implementación R2

## Veredicto

BLOCKED

R2 cierra correctamente los tres fallos funcionales principales de R1 —cleanup destructivo, script `__main__.py` y falso success de bind— y añade una integración real de seis candidatos/dos puertos. No obstante, queda un escape HIGH de clasificación mediante un módulo Python equivalente y dos gaps MEDIUM: la excepción del redirector no demuestra relación temporal de ancestro, y V24 todavía no prueba muerte/recovery de un `run_daemon` real en las fronteras aprobadas.

No se modificó producción. El único archivo creado es este informe.

## Snapshot revisado

| Archivo | SHA-256 |
|---|---|
| `tools/dayz_mcp/identity_migration.py` | `B37092E2665C81006A63A0FE9DE26229E57808F0B7EEF74590731C0BC31762CE` |
| `tools/dayz_mcp/daemon.py` | `869E61969EE12BFB848C0246F496A23F2E6B95E651EC76712BC60B06ACBE1C6D` |
| `tools/tests/test_identity_migration.py` | `9AD3274E9303801B77AA46618C941FA92C98060B380A9CE138CC00CA09186CC1` |
| `tools/tests/test_daemon_security_gate.py` | `DD78B8AAA38F813D7C2BB2930E9745BF4D46A5A1A85A93D08DB9E42BE1F45731` |
| `tools/tests/test_bug046_startup_deadlock.py` | `2AC5F2A5FB4B625EB80F08FB617C66917D109BAEFBA67CB4CD62BFABB7D00C93` |
| `tools/tests/test_daemon.py` | `5D7E68D35C910A9F3649E5691368871B7EB63D3303BE0DEE295B2A8CDCE7962A` |

## Cierre de hallazgos R1

### R1 HIGH-01 — receipt + auxiliares externos

**CERRADO.** Con receipt final válido, recovery ahora rechaza cualquier pending/next y valida marker exacto `receipt_pending` antes de eliminarlo (`tools/dayz_mcp/identity_migration.py:806-839`). Ya no llama indiscriminadamente a unlink sobre tres nombres. Los fixtures colocan bytes externos por separado en marker, next y pending, exigen excepción y preservan bytes (`tools/tests/test_bug046_startup_deadlock.py:91-111`).

### R1 HIGH-02 — script directo `dayz_mcp/__main__.py`

**CERRADO para la forma script.** La clasificación reconoce `__main__.py` bajo un directorio `dayz_mcp` y lo trata writer (`identity_migration.py:265-271`). V23 cubre direct embedded/default/client (`tools/tests/test_identity_migration.py:353-375`).

### R1 MEDIUM-01 — bind unavailable devolvía 0

**CERRADO.** Si `_bind_with_reclaim` devuelve `None`, `run_daemon` re-probea con key exacta: healthy→0; ausente/foreign/unhealthy→75 (`tools/dayz_mcp/daemon.py:451-469`). Los tests ajustaron la expectativa unhealthy a `DAEMON_STARTUP_CONTENDED` y mantienen 0 solo tras health (`tools/tests/test_daemon_security_gate.py:63-108`; `tools/tests/test_daemon.py:333-412`).

### R1 MEDIUM-02 — inexistencia de V24 multiproceso

**PARCIALMENTE CERRADO.** Existe una integración con seis procesos, dos puertos, elección global, `run_daemon`, bind/status/coordination reales y una única generación (`tools/tests/test_bug046_startup_deadlock.py:250-363`). La limitación residual se detalla en MEDIUM-02.

### R1 MEDIUM-03 — reparse/lock/syscall

**MAYORMENTE CERRADO.** Ambos locks rechazan name-surrogate, validan leaf regular, comparan identidad path↔handle antes y después de adquirir y preservan residual regular (`identity_migration.py:116-255`). Hay fixtures symlink/tag y fallos write/fsync/replace (`test_bug046_startup_deadlock.py:169-247`). Permanece el caso de zero-progress al inicializar el byte del lock, descrito en MEDIUM-03.

## Hallazgos R2

### HIGH-01 — `-m dayz_mcp.__main__` elude el scanner y puede ser embedded writer

**Evidencia:** el parser solo reconoce el par exacto `-m dayz_mcp` (`tools/dayz_mcp/identity_migration.py:278-286`). La forma `-m dayz_mcp.__main__` no contiene un argumento script `.../dayz_mcp/__main__.py`, por lo que tampoco entra en la nueva rama de líneas 265-271.

La forma es ejecutable real: con `tools/.venv-mcp/Scripts/python.exe -m dayz_mcp.__main__ --help` se cargó el mismo parser de `dayz_mcp`; `tools/dayz_mcp/__main__.py:3-7` llama a `server.run`. El probe exacto devolvió:

`_argv_targets_dayz_mcp(['python','-m','dayz_mcp.__main__','--embedded','--keyfile','K']) == False`.

**Impacto:** **safety/liveness race**. En `--embedded` o default, esta forma no entra en `run_daemon` ni en su startup election, pero sí puede migrar y activar `runs.json`/`coordination.json`. El scanner la ignora durante ambos quiescence checks. En otro puerto puede coexistir sobre el mismo root; en el mismo puerto el bind tardío no revierte la ventana persistente.

**Fix requerido `[DESIGN]`:** parsear el target real del intérprete, no buscar substrings sueltos. Reconocer como targets writer al menos:

- `-m dayz_mcp` con la gramática ya existente;
- `-m dayz_mcp.__main__` —fail-closed para todos sus modos es suficiente—;
- script target canónico `dayz_mcp/__main__.py` en la posición de script.

Añadir positivos blocker para `dayz_mcp.__main__` default/embedded/daemon/client y una integración de proceso. Añadir negativos donde `dayz_mcp/__main__.py` sea solo argumento de `pytest`, linter, `-c` o dato de otra aplicación; la implementación actual usa `any(argument)` (`identity_migration.py:265-270`) y puede recrear degradación de liveness por falso blocker.

### MEDIUM-01 — el redirector “ancestro” puede ser más nuevo que el hijo y tener hash de executable no ligado a lo observado

**Evidencia:** `capture_launch_ancestor_identity` obtiene el PID padre, observa path/argv, verifica path aprobado y luego toma un snapshot fuerte (`identity_migration.py:355-404`). Acepta si el `command_line_sha256` coincide con el hijo, pero no exige:

- `parent.creation_time_utc < child.creation_time_utc`;
- que el `executable_sha256` y argv hash del snapshot correspondan al `executable`/`argv` observados antes del snapshot;
- una segunda validación de la relación parent→child contra PID reuse.

`NativeProcessGuard` llama `executable_sha256` al hash del path normalizado, no de bytes (`tools/dayz_mcp/native_process_guard.py:62-82`). Por tanto, el path aprobado observado y el hash del snapshot deben vincularse explícitamente; ahora un PID puede cambiar entre ambas lecturas.

**Reproducción:** un fake parent inmediato con path/argv aprobados y un snapshot de PID 777 creado **una hora después** del hijo fue aceptado: `accepted_newer_than_child=True`. Un proceso posterior no puede ser ancestro; esto modela PID reuse/parent spoof.

La validación posterior de scanner sí fija PID+creation/path-hash+argv-hash (`identity_migration.py:407-493`), pero fija la identidad incorrectamente admitida; no repara el error de captura. El test actual solo inyecta identidades ya capturadas en `scan_dayz_mcp_processes` y prueba drift posterior (`tools/tests/test_identity_migration.py:435-472`). V24 prueba el redirector feliz del venv, no el reuse durante captura.

**Impacto:** **degradation/safety race** estrecha pero real. Un redirector reutilizado o proceso spoof con argv idéntica puede quedar exento como “ancestor” mientras prepara otro writer, reabriendo una ventana pre-migration.

**Fix requerido `[DESIGN]`:** dentro de un único snapshot coherente del parent capturar PID, create_time, exe y argv; comparar el snapshot resultante contra esos valores, exigir create_time estrictamente anterior al hijo y revalidar que el current process todavía reporta ese parent PID. Comparar `identity_hashes(observed_executable, observed_argv)` con ambos hashes del snapshot. Cualquier cambio/ilegibilidad devuelve `None` y el proceso permanece blocker. Añadir tests de PID reuse entre `ppid→exe/cmdline→snapshot`, parent posterior al hijo, exe swap, argv swap y wrapper que muere; conservar el positivo venv real.

### MEDIUM-02 — owner death/segunda ola no atraviesa migration+bind+activation reales

**Evidencia:** la integración de seis candidatos sustituye `_ensure_identity_migration` por `fixture_migration` (`tools/tests/test_bug046_startup_deadlock.py:260-287`). Prueba elección, bind, activation y una generación, pero no compone la transacción R9 real con el candidate drain.

El test de owner death mata un proceso mientras solo posee `daemon_startup_election` y luego adquiere el lock directamente (`test_bug046_startup_deadlock.py:365-397`). El test denominado “second wave progresses” libera al winner mediante un archivo, espera exits normales y después vuelve a adquirir el lock; no mata `run_daemon`, no publica una segunda generación y no recupera artifacts (`:398-461`).

Esto no satisface V24, que exige muerte en fronteras pre/post-migration/bind/activation y que una segunda ola publique generación usando recovery (`plans/2026-07-22-bug046-lease-queue-liveness-plan.md:856-860`).

**Impacto:** **validation gap de liveness**. La composición que motivó R7-R9 —winner muerto con marker/backup/receipt parcial mientras otros candidatos drenan— sigue sin prueba multiproceso.

**Fix requerido `[DESIGN]`:** ejecutar `run_daemon` fixture con migration R9 real sobre `RuntimePaths`/migration temp inyectable, barreras IPC en cada frontera, matar únicamente ese fixture y lanzar segunda ola. Exigir receipt/backup exactos, una generación healthy nueva, coordination consistente y cero autoridad doble para pre/post backup, segundo quiescence, receipt publish, bind, activation y status accreditation.

### MEDIUM-03 — zero-progress al inicializar locks se acepta como success

**Evidencia:** al crear un lock vacío, ambos helpers ejecutan `os.write(descriptor, b"\0")` sin comprobar que devuelva 1 (`identity_migration.py:177-180,227-230`). `_exclusive_write` sí rechaza progreso cero (`identity_migration.py:78-95`).

**Reproducción en Windows:** parcheando `os.write` para devolver 0, `daemon_startup_election` produjo `elected=True` y el lock quedó con tamaño 0 (`startup_zero_write_elected=True lock_size=0`). `msvcrt.locking` puede bloquear un rango más allá de EOF, por lo que esta ejecución concreta todavía tuvo exclusión, pero contradice el contrato “lock I/O fail-closed” y deja el estado de inicialización sin verificar.

El fixture `test_zero_progress_fsync_and_replace_failures_are_reentrant` precrea `.runs-v1.lock` con un byte antes de parchear write (`tools/tests/test_bug046_startup_deadlock.py:169-202`); por ello cubre writes de artifacts, no el write inicial de ninguno de los locks.

**Fix requerido `[EXACT]`:** capturar el retorno de `os.write` en ambos helpers y exigir exactamente 1; cualquier otro valor lanza el error estable `*_lock_unavailable`, cierra handle y nunca hace yield. Añadir zero/short/error/fsync para startup y migration lock, además del residual regular positivo.

## Validación ejecutada

El Python global pudo importar pytest pero no `mcp`, por lo que su collection de `test_daemon.py` falló por dependencia de entorno, no por código. Se repitió con el intérprete aprobado `tools/.venv-mcp/Scripts/python.exe` usando `unittest`:

- `test_identity_migration.py`: 16 tests, OK.
- `test_daemon_security_gate.py`: 7 tests, OK.
- `test_bug046_startup_deadlock.py`: 11 tests, OK, 1 skipped por privilegio de symlink.
- `test_daemon.py`: 21 tests, OK.

Total: **55 tests OK, 1 skipped**. Los resultados no cambian el BLOCKED porque faltan las negativas reproducidas y el V24 de recovery real.

## Conclusión

BLOCKED

R2 mejora materialmente la implementación y cierra todos los hallazgos R1 directos. No está lista para gate vivo porque `-m dayz_mcp.__main__` sigue siendo un writer invisible, la excepción de ancestor admite una identidad temporalmente imposible y owner-death recovery no está integrado. Corregir estos tres puntos, el zero-progress de locks y repetir revisión R3 sobre hashes estables antes de desplegar.
