# BUG-046 Lease Queue Liveness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. REQUIRED discipline: superpowers:test-driven-development and dayz-mcp-verify for the final live gate.

**Goal:** Eliminar los leases FIFO concedidos a sesiones que ya no están esperando, proporcionar una adquisición que permanezca en cola y añadir un launcher seguro que inyecte identidad/token por entorno al `dayz-test.ps1` hijo sin exponerlos en argv, logs o transcript.

## Estado de implementación — 2026-07-22

- Núcleo FIFO/request-bound: implementado y revisado `CORE GREEN`; cierre en
  `reviews/2026-07-22-bug046-final-implementation-r10-adversarial.md`.
- Validación offline: focal 395 tests `OK` (1 skip), gate local multiproceso `PASS`
  con cero grants sin wait vivo y suite global sin regresiones nuevas.
- H9: pendiente. El launcher productivo permanece fail-closed y el registro vacío;
  no se ha adjudicado PE nativo/neutral frente a wrapper PowerShell registrado.
- Deploy/gate mixto real 2 Claude + 2 Codex: pendiente; `session_status` devolvió
  `daemon_unavailable` y no se arrancó ni reinició el daemon durante el cierre.

**Architecture:** El daemon conserva la cola FIFO existente, pero deja de promover automáticamente su cabeza durante `release`/expiry. La promoción usa una transición ligada a una llamada `session_wait` viva: WAL de reconciliación, prepared audit, commit causal audit y solo entonces publicación de active/token. `session_acquire_wait` genera un `operation_id` antes de I/O, siempre encola un ticket —aunque la autoridad esté libre— y cancela por esa operación; un tombstone impide que una request tardía publique después de cancel-before-arrival. Un marker WAL no secreto se arma antes de toda transición de grant/release que dependa del ledger; el camino normal lo marca `completed`, mientras un fallo deja un latch observable y durable que bloquea grants hasta reparación administrativa explícita e idempotente. La request larga usa slices HTTP de hasta 30 segundos, emite progreso y limpia operación/ticket ante cualquier salida no activa; no es durable tras perder el host/request. Los timeouts de Claude y Codex se configuran por encima del máximo de espera mediante una transacción de config con handles Windows exclusivos y journal, sin falso CAS por rename. Un CLI Python dedicado adquiere su propia sesión y solo confía en consumidores registrados por root/identidad/SHA; abre y hashea el script mediante un handle Windows que impide write/delete hasta que termina el hijo. El consumidor PowerShell retira las credenciales del entorno general y las repone únicamente para `lifecycle_cli`. No se introduce un ejecutor durable de comandos ni persistencia de leases/tickets como autoridad recuperable.

**Tech Stack:** Python 3.14, `threading.Condition`, `asyncio`, FastMCP 1.27.2, HTTP loopback local, PowerShell de instalación, `unittest`.

## Global Constraints

- TDD estricto: ninguna modificación de producción antes de observar el test nuevo fallar por la razón esperada.
- Mantener fail-closed en identidad, ticket, lease y auditoría.
- No matar procesos DayZ. La validación viva usa el lifecycle guard y el protocolo acquire/use/release/status.
- No cambiar arquitectura de P0.S, persistencia DayZ, payloads de red ni formatos binarios.
- No hacer refactors incidentales. Cada línea debe trazar a BUG-046 o a su criterio DPF.
- El lease token y la identidad completa nunca aparecen en argv, stdout/stderr sin redactar, excepciones, audit auxiliar, evidencias ni documentos. Solo existen en memoria y en el entorno del hijo aprobado.
- Todo identificador nuevo de este documento está marcado `[DESIGN]`; los identificadores existentes y comandos ya comprobados están marcados `[EXACT]`.
- El repositorio no tiene `.git`; no habrá commits. Cada checkpoint se documenta con diff, pruebas y hashes de archivos tocados.

### Revisión R1 tras auditoría adversarial

- `[EXACT]` La primera auditoría independiente está en `reviews/2026-07-22-bug046-lease-queue-liveness-plan-adversarial.md` y emitió `BLOCKED` con 7 HIGH, 6 MEDIUM y 1 LOW.
- Esta R1 reemplaza expresamente cualquier texto anterior incompatible sobre cancelación simple, grant de un solo paso, “durabilidad” de la request, confianza por forma de path o gate mixto autónomo.
- No se inició producción después del bloqueo. Los únicos cambios previos a R1 fueron plan/research/review y probes temporales de configuración ya eliminados.
- `[EXACT]` Probe Codex 2026-07-22: una entrada temporal con `tool_timeout_sec = 1860` fue leída por `codex mcp get --json` como `1860.0` y después eliminada.
- `[EXACT]` Probe Claude 2026-07-22: una entrada temporal con `"timeout": 1860000` fue mostrada por `claude mcp get` como `Timeout: 1860000ms` y después eliminada.
- `[EXACT]` `mcp add -c`/`mcp add-json` no persistieron estos campos durante el alta; el instalador necesita edición nativa estructurada y rollback byte-exacto.
- `[EXACT]` La línea base global sufrió drift externo durante la revisión: pasó de 584 tests/14 resultados no verdes a 519 tests/4 import errors + 2 failures porque `PowerShellProcessGuard` dejó de existir en `process_lifecycle.py` mientras `process_guard_gate.py:17` aún lo importa. BUG-046 no corregirá ese drift; el primer diff queda condicionado a un baseline estable y fingerprintado.

### Revisión R2 tras la segunda auditoría adversarial

- `[EXACT]` La revisión R1 está en `reviews/2026-07-22-bug046-lease-queue-liveness-plan-r1-adversarial.md` y emitió `BLOCKED`: 2 HIGH y 3 MEDIUM.
- `[EXACT]` La ruta autorizada está bajo OneDrive. `Get-Item` muestra `ReparsePoint` en `OneDrive`, `Documentos`, `DayZ Projects`, `LF_VStorage_dev` y `tools`, pero `LinkType`/`Target` vacíos; `fsutil reparsepoint query` devuelve tags cloud `0x9000701A`, `0x9000601A` y `0x9000E01A`. El archivo final es regular, `Archive`, sin reparse point.
- `[EXACT]` Windows SDK `10.0.26100.0/um/winnt.h:15736-15741` y Microsoft Learn `winnt/nf-winnt-isreparsetagnamesurrogate` definen name-surrogate mediante el bit `0x20000000`; los tags cloud observados no tienen ese bit. Python 3.14 `docs.python.org/3/library/os.html#os.stat` documenta que `stat(..., follow_symlinks=False)` deja de seguir name-surrogates, pero sigue reparse points resolubles que no son name-surrogate.
- `[EXACT]` `tools/dayz_mcp/runtime_state.py:102-185,203-224` persiste observabilidad por generación y hoy no conserva un fault; `tools/dayz_mcp/doctor.py:365-497` tampoco lo valida. R2 los incorpora expresamente.
- `[EXACT]` `tools/dayz_mcp/loopback.py:66-69,1199-1242` ya posee rutas administrativas HMAC con confirmación y `tools/dayz_mcp/admin_cli.py:43-160` exige TTY. La reparación de auditoría reutiliza esa frontera, no una tool MCP ordinaria.
- No se inició producción después del segundo bloqueo. R2 sustituye el rechazo indiscriminado de reparse points por una política name-surrogate + identidad fijada, añade WAL/fault/doctor/recovery, preserva el error primario, añade CAS y corrige el orden del SHA.

### Revisión R3 tras la tercera auditoría adversarial

- `[EXACT]` La revisión R2 está en `reviews/2026-07-22-bug046-lease-queue-liveness-plan-r2-adversarial.md` y emitió `BLOCKED`: 3 HIGH y 3 MEDIUM.
- `[EXACT]` `tools/dayz_mcp/server.py:299-320` ejecuta HTTP mediante `asyncio.to_thread`; `C:/Python314/Lib/asyncio/threads.py:12-25` confirma que cancelar el await no detiene el thread. R3 crea un `operation_id` antes de la primera request y usa enqueue-always-ticket + tombstone cancel-before-arrival.
- `[EXACT]` La R2 llamó CAS a una secuencia hash→`os.replace`, pero `os.replace.__doc__` solo garantiza rename/overwrite, no compare-and-swap. R3 elimina esa afirmación y no usa replace para el destino de config.
- `[EXACT]` Probes read-only 2026-07-22 abrieron `.claude.json` y `.codex/config.toml` simultáneamente con `CreateFileW(GENERIC_READ|GENERIC_WRITE, share=0)` sin escribir. La primitive local puede establecer exclusión obligatoria; si en aplicación no puede abrir ambos, la transacción no toca ninguno.
- `[EXACT]` Windows SDK `um/fileapi.h:385-387,1045-1050,1098-1106,1152-1161` declara `FlushFileBuffers`, `SetEndOfFile`, `SetFilePointerEx` y `WriteFile`; `um/winnt.h:10252-10253` declara `GENERIC_READ|GENERIC_WRITE`.
- R3 añade terminal WAL normal `completed`, repair por `event_id` determinista + `write_once`, tabla completa snapshot/marker y semántica queued cuando el audit gate está busy. Producción sigue intacta.

### Revisión R4 tras la cuarta auditoría adversarial

- `[EXACT]` La revisión R3 está en `reviews/2026-07-22-bug046-lease-queue-liveness-plan-r3-adversarial.md` y emitió `BLOCKED`: 1 HIGH y 3 MEDIUM.
- R4 conserva original+target exactos y file identity en artefactos ACL. Reconoce únicamente torn states demostrables de la escritura secuencial propia; antes de rollback persiste `recovery_source`, de modo que un segundo crash también es recuperable. Un writer externo con identity/bytes ajenos sigue en conflicto.
- `write_once` compara núcleo semántico redacted/canónico excluyendo solo `timestamp_utc` y `daemon_generation` inyectados; event id igual con lease/reason/fault distintos falla cerrado.
- Saturación de tombstones entra en `doctor.py`/tests con finding FAIL público, y clear normal fallido conserva `completed` cleanup-pending; no se convierte contradictoriamente en fault.

### Addendum R5 — interbloqueo de arranque descubierto en validación viva

- `[EXACT]` Tras drenar por sí solo el listener viejo de `8765`, el arranque de la generación nueva falló con `RunsBackupGateError: process_scan_incomplete`; un probe read-only con el mismo `psutil` contó 52 procesos `python -m dayz_mcp --client`, 0 daemon y 35 padres distintos. No se terminó ningún proceso.
- `[EXACT]` `tools/dayz_mcp/identity_migration.py:135-145,163-202` clasifica cualquier `-m dayz_mcp` como bloqueante antes de distinguir modo. `tools/dayz_mcp/server.py:1224-1267,1280-1299` prueba que `--client` es el proxy stdio que no posee el puerto ni construye `RunManifestStore`; `tools/dayz_mcp/daemon.py:145-194` prueba que la autoridad que abre/migra `runs.json` se construye en daemon.
- Impacto: **degradation de liveness** y deadlock cooperativo. Los clientes vivos impiden crear el backup/migración requerida para iniciar el daemon; sin daemon, esos mismos clientes quedan esperando y siguen presentes como supuestos blockers.
- Cambio propuesto `[DESIGN]`: sustituir el predicado amplio por una clasificación fail-closed de procesos capaces de tocar autoridad local: `--daemon`, `--embedded`, modo sin selector (default embedded), selectores ambiguos y `p0s_daemon_bootstrap.py` bloquean; `--client` exacto y único no bloquea. Se mantiene verificación completa de exe/cmdline/identidad y cualquier proceso ilegible sigue fallando cerrado.
- Gate nuevo: fixtures positivo/negativo para cada modo y proceso ambiguo; con N clientes reales vivos y ningún listener, `backup-runs-v1` debe completar una sola vez y el daemon aprobado debe arrancar. Daemon/embedded/bootstrap concurrentes siguen produciendo `dayz_mcp_process_present`; ningún proceso se mata como parte del gate.
- `[EXACT]` La revisión R5 está en `reviews/2026-07-22-bug046-startup-deadlock-plan-r5-adversarial.md` y emitió `BLOCKED`: excluir clients es correcto, pero 52 locks por proceso aún pueden lanzar 52 candidatos daemon que se bloquean entre sí antes del bind.
- R6 añade elección cross-process en el lado cliente **antes de spawn**. `[DESIGN] daemon_startup_election(port, timeout_s)` usa ownership de lock del SO, no existencia de lock-file. Bajo esa exclusión, el ganador vuelve a probar listener; si hay candidato writer-capable sin listener, espera su resolución acotadamente y no lanza rival; solo cuando no hay listener ni candidato lanza uno. Los perdedores esperan el lock y al adquirirlo vuelven a probar en vez de hacer spawn.
- Recuperación: si el cliente ganador muere, el SO libera el lock. El siguiente waiter adquiere, primero prueba listener y luego observa cualquier candidato daemon vivo; espera que se haga healthy o desaparezca antes de decidir spawn. Si persiste ilegible/no saludable, falla cerrado sin rival ni kill. `run_daemon` conserva health probe, allowed-current migration y bind como autoridad final; un daemon externo entre elección y bind provoca salida segura/fallo del gate, no doble autoridad.
- `[EXACT]` La revisión R6 está en `reviews/2026-07-22-bug046-startup-deadlock-plan-r6-adversarial.md` y emitió `BLOCKED` con 3 HIGH: los clientes ya cargados eluden un lock client-side; daemon directo tampoco participa; un lock por puerto no protege el `RuntimePaths.root` global.
- R7 **sustituye** la elección client-side R6. `[DESIGN] daemon_startup_election(paths)` se adquiere dentro de `run_daemon`, después del probe inicial y antes de migration, y se comparte por `RuntimePaths.root` sin separar por puerto. Todo candidato nuevo participa aunque lo lance un cliente viejo, una shell directa u otro puerto. Ownership es un handle/lock del SO; el archivo persistente no significa posesión.
- El ganador hace un segundo health probe bajo lock, migra con su identidad exacta allowed-current, hace bind y activa coordinación antes de liberar. Los perdedores no esperan vivos como `--daemon`: si el lock está ocupado, re-probe y salen 0 sin migration/bind; el `ClientRuntime` que los lanzó ya espera al daemon ganador. El ganador reintenta acotadamente solo `dayz_mcp_process_present` para dejar drenar candidatos perdedores/mixed-version; errores de identidad, scan, listener, backup o integridad no se reintentan.
- Si el ganador muere, el SO libera el lock; la siguiente ola/request elige otro. Un lock-file residual se ignora como estado. Daemon viejo que no participa permanece visible como writer-capable y el ganador R7 espera acotadamente/falla cerrado; si el viejo alcanza health/bind primero, los probes/bind finales hacen salir al nuevo sin segunda autoridad. Dos puertos bajo el mismo root se serializan y el segundo queda bloqueado por el daemon writer ya vivo: no existen dos autoridades de `runs.json`/`coordination.json`.
- `[EXACT]` La revisión R7 está en `reviews/2026-07-22-bug046-startup-deadlock-plan-r7-adversarial.md` y emitió `BLOCKED`: un loser puede aparecer después de crear backup y antes del segundo quiescence, dejando `incomplete_runs_backup_artifacts`; además recovery no era obligatoriamente progresiva y loser directo no debe fingir success.
- R8 añade transacción durable al backup. `[DESIGN] runs-backup-transaction.json` (schema cerrado, sin contenido de runs) se persiste antes del primer byte con source path+metadata/source_absent, destinos exactos y phase. Backup y receipt se escriben a artefactos propios fsynced; el receipt solo se publica desde pending mediante rename atómico bajo el lock de migration. Cada phase se persiste/fsync antes de avanzar.
- Recovery R8 corre bajo lock **después de un quiescence limpio**. Si source conserva metadata exacta del marker, revierte byte-exacto solo los artefactos que el marker demuestra propios —incluido backup parcial—, verifica ausencia y reintenta; si final receipt+backup ya son exactos, roll-forward valida/limpia marker. Source drift, paths/schema ajenos, final inválido sin provenance o artifacts sin marker siguen en `incomplete_runs_backup_artifacts`/conflict sin borrar. Un fallo del segundo quiescence deja marker recuperable, no un estado permanentemente ambiguo.
- Compatibilidad: receipt pre-R8 válido se sigue aceptando. Backup huérfano legacy sin marker no se borra. Rollback a binario pre-R8 acepta un commit final válido; una transacción R8 incompleta falla cerrada por backup sin receipt y conserva evidencia. No se toca `runs.json`.
- Loser sin lock hace re-probe: si no hay daemon healthy devuelve `[DESIGN] DAEMON_STARTUP_CONTENDED=75`, no 0. V24 exige que la siguiente ola tras muerte del ganador **progrese**; “o falla cerrado” solo es válido en fixtures de external drift deliberado.
- `[EXACT]` La revisión R8 está en `reviews/2026-07-22-bug046-startup-deadlock-plan-r8-adversarial.md` y emitió `BLOCKED` con un HIGH restante: el marker mutable también necesita old-or-new; un crash durante create/update no puede destruir la única provenance.
- R9 publica cada revisión del marker mediante `[DESIGN] marker.next`: create-exclusive/truncate controlado del temp fijo, write-all, file flush, parse/hash/schema verify y `os.replace` atómico **solo del marker**, bajo el lock de migration. `revision` crece monótonamente y cada update exige el SHA del marker anterior en memoria. Crash antes de replace conserva marker anterior válido; crash después conserva el nuevo. En creación inicial, marker final se publica antes de backup; temp parcial sin marker implica que ningún artifact de datos estaba autorizado y se limpia bajo quiescence.
- Cleanup R9 también es old-or-new: receipt final exacto es autoridad de commit aunque marker quede; recovery valida commit y elimina marker/temp idempotentemente. Marker válido sin commit conserva provenance para rollback. Stale/partial `marker.next` nunca sustituye marker final y se elimina solo tras clasificar final/source/artifacts; marker final corrupto o paths/hash/revision ajenos siguen conflict sin borrar datos.
- Gates inyectan crash/fallo en cada chunk/syscall de initial marker, cada phase, replace, temp cleanup, final marker cleanup y segundo recovery. Exigen marker anterior o nuevo siempre parseable, source/backup byte-exactos y progreso en la segunda ejecución sin drift.
- Este addendum R9 requiere revisión adversarial literal `GREEN` antes de modificar `identity_migration.py` o `daemon.py`.

### Addendum R10 — publicación no-clobber e identidad del target CPython

- `[EXACT]` La revisión adversarial de implementación R3 está en `reviews/2026-07-22-bug046-startup-deadlock-implementation-r3-adversarial.md` y emitió `BLOCKED`: `_argv_targets_dayz_mcp` clasifica como writers procesos Python ajenos que usan las formas válidas `-cCODE`, `-mMODULE` o `--check-hash-based-pycs`; además, una escritura externa sincronizada dentro de `os.replace` puede ser sobreescrita después del último SHA check.
- `[EXACT]` El CLI local de Python 3.14 documenta `-c cmd`, `-m mod`, `-W arg`, `-X opt`, `--check-hash-based-pycs always|default|never`, `-?`/`-h`/`--help`, `--help-env`, `--help-xoptions`, `--help-all` y `-V`/`--version`. Probes reales confirman agrupación de flags sin valor antes del selector: `-Imhttp.server`, `-OOmjson.tool`, `-BcCODE` y `-bbmdayz_mcp.__main__`.
- `[DESIGN]` El tokenizer R14 consume cada argumento corto carácter a carácter: flags sin valor `bBdEiIOPqRsSuvx` continúan; `c` convierte todo el sufijo —o el argumento siguiente si está vacío— en command string y termina como no-writer; `m` obtiene del mismo modo el module target y lo clasifica exactamente; `W`/`X` consumen el sufijo completo o el argumento siguiente como valor, nunca como target, **y después continúan el scan en el siguiente argv sin retornar clasificación**; `h`/`?`/`V` son terminales no-writer. `--check-hash-based-pycs` consume exactamente uno de `always|default|never` y continúa; las opciones terminales largas no escriben; `--` fija el script siguiente; el target stdin `-` es no-writer. Una opción inválida para Python 3.14 termina el intérprete y no bloquea. La paridad de tail cliente se aplica **sólo** al target canónico acreditado exacto `-m dayz_mcp`, incluida la forma compacta/agrupada que resuelve exactamente ese module target. `-m dayz_mcp.__main__` y cualquier script directo `dayz_mcp/__main__.py` permanecen writer para todos sus tails porque su resolución/path no está acreditada por el scanner. En el target canónico, uno o más selectores `--client` sin modo distinto son client; default, daemon/embedded, mezcla de modos o tail inválido/desconocido permanece fail-closed.
- R10 **sustituye** las revisiones mutables R9 para transacciones nuevas. `[DESIGN]` El marker publicado es una provenance inmutable en phase `prepared`/revision 1 durante toda la transacción. Backup completo/parcial, pending receipt y receipt final ya aportan los checkpoints observables necesarios; sin receipt, recovery revierte únicamente artefactos que sean prefijos byte-exactos de los derivados del marker; con receipt final exacto, hace roll-forward. Las phases R9 revision 2/3 existentes se siguen leyendo y recuperando para compatibilidad, pero el escritor R10 no las genera.
- `[DESIGN]` La publicación inicial usa `os.rename(marker.next, marker)` en Windows, nunca `os.replace`. La garantía verificada es publicación Windows no-clobber de una sola syscall en la misma carpeta/volumen: si `marker` apareció, `FileExistsError` preserva sus bytes. No se afirma power-loss durability ni atomicidad Windows no documentada. Fuera de Windows falla cerrado. `marker.next` se crea exclusivo, se valida antes del rename y carece de autoridad por sí solo. Tras rename se revalida marker byte-exacto. Un crash antes conserva solo temp sin artefactos autorizados; después el gate debe observar marker final old/new válido. Se mantiene el lock cooperativo, pero la syscall ya no pisa drift no cooperativo en la frontera de publicación.
- `[DESIGN]` Recovery acepta receipt final exacto con marker preparado inmutable o con una phase R9 legacy compatible. Si marker está **ausente** y no hay data artifacts, `next` ausente o cualquier prefix byte-exacto del marker inicial R10 se retira y reintenta. Si marker final está ausente, corrupto o ajeno y existen data artifacts, falla cerrado sin borrar. Con marker presente, la clasificación usa any-match sobre el estado observable: rev1 acepta `next` ausente o cualquier byte string para el que `rev2_expected.startswith(actual_next)`; rev2 acepta ausente/prefix de rev3; rev3 exige `next` ausente. Un prefix tomado del marker inicial junto a rev1 sólo es conflict si **no** es también prefix de rev2; el gate negativo corta después del primer byte divergente y prueba `common-1`, `common` (aceptan) y `common+1` del initial (preserva). Cualquier no-prefix es conflict y cero deletes; `next` nunca autoriza rollback por sí mismo.
- `[DESIGN]` Rollback: un commit R10 que cae tras publicar receipt y antes de retirar marker prepared es deliberadamente fail-closed para el binario R9. El procedimiento soportado es restaurar/ejecutar recovery R10 hasta obtener receipt válido sin marker y solo entonces volver a R9. No se toca formato de `runs.json` ni del receipt; un rollback directo preserva bytes pero no promete arrancar. El gate ejecuta explícitamente R9 sobre ese estado, comprueba rechazo sin deletes y luego R10 completa cleanup.
- `[EXACT]` `tools/dayz_mcp/server.py:1245-1296` construye hoy `argparse.ArgumentParser` con abreviaturas implícitas, value-options tipadas, `--keyfile` obligatorio y grupo de modo mutuamente exclusivo. Probes reales confirman clientes vivos válidos con `--idle-timeout -1` y `--task-label=-nightly`, además de `--keyfile=K`; abreviaturas como `--keyf` también son aceptadas hoy. R14 fija `allow_abbrev=False`: las formas completas separadas/`--option=value`, valores negativos tipados y repetición del mismo `--client` siguen válidos; abreviaturas dejan de ser contrato y fallan antes de crear writer. No cambia formato persistente ni payload de red; rollback a binario anterior vuelve a aceptar abreviaturas, mientras configs/launchers canónicos con nombres completos funcionan en ambos sentidos.
- R14 sustituye la tabla duplicada R13. `[DESIGN]` Crear `dayz_mcp/server_cli.py` como fuente única, liviana y sin imports MCP: construye el mismo `argparse.ArgumentParser(allow_abbrev=False)` para `server.parse_args` y ofrece un parse silencioso que sustituye `error/exit` por resultados tipados, sin `SystemExit`, stdout ni stderr. El scanner sólo invoca esa clasificación para target exacto `-m dayz_mcp`: parse válido con `mode=client` es no-writer; parse válido default/daemon/embedded o parse inválido es writer; `--help` terminal es no-writer. Así `--idle-timeout -1` y `--task-label=-nightly` siguen la semántica real, mientras `--task-label -nightly`, `--task-label --daemon --client`, abreviaturas, unknown y modos mixtos fallan cerrados. El mismo builder elimina drift futuro entre CLI y scanner.
- `[DESIGN]` Compatibilidad del narrowing: clientes vivos lanzados como `-m dayz_mcp.__main__` o script directo deben reiniciarse con `-m dayz_mcp`; mientras sigan vivos bloquean deliberadamente el arranque fail-closed. Los registros Claude/Codex y launchers observados ya usan el target canónico. README documenta este límite y nombres de opción completos. Daemon/embedded/default bloquean en los tres entrypoints.
- Gates R14: procesos reales con flags separados, compactos y agrupados verifican inclusión/ausencia de PID. Bloquean `-bbmdayz_mcp.__main__`, `-BWignore -mdayz_mcp.__main__ --embedded`, `-BXdev -mdayz_mcp --daemon`, `-W ignore dayz_mcp/__main__.py --client`, `-X dev -- dayz_mcp/__main__.py` y `--check-hash-based-pycs default -mdayz_mcp.__main__`. No bloquean `-Imdayz_mcp --client --keyfile K`, `--client --keyfile K --idle-timeout -1`, `--client --keyfile=K --task-label=-nightly`, client repetido con keyfile, `-OOmhttp.server`, `-BcCODE`, `-BWignore <foreign-script>`, `-W -m dayz_mcp` ni stdin `-`; `server.parse_args` y scanner rechazan/no observan como durable `--keyf`. Un writer externo creado dentro de la syscall de publicación queda byte-exacto, la operación falla con conflict y no crea backup/receipt. Crash en cada chunk/syscall de initial next, antes/después de rename y revalidación, backup/pending, receipt publish y cleanup conserva provenance válida o temp sin datos; segunda ejecución progresa. Compatibilidad recorre cada prefix de `next` R9 rev2/rev3, markers R9 rev1/2/3, receipt legacy y drift; la frontera common-prefix rev1/rev2 se prueba explícitamente. Este addendum requiere revisión adversarial literal `GREEN` antes de modificar producción.
- `[EXACT]` Durante las revisiones de plan R12-R14 aparecieron cambios externos no realizados por este pipeline en `identity_migration.py`, `server.py` y sus tests antes de GREEN. Se consideran implementación provisional no autorizada: después del GREEN se congelan hashes, se revisan línea a línea contra R14 y se preservan o corrigen con patches mínimos; no se sobrescriben ni se atribuyen a esta sesión sin evidencia.

| Hallazgo R0 | Resolución R1 | Gate |
|---|---|---|
| HIGH-01 cancel vs grant-inflight | marker previo a I/O + fases queued/inflight/provisional + 200/202 semánticos | V6/V7/V7b |
| HIGH-02 causalidad grant/wait | prepared no autoritativo + commit causal único antes de publish + compensación | V5/V5b/V5c |
| HIGH-03 release audit tardío | fence in-memory hasta terminal; busy=queued, fail/stall=visible fail-closed | V20 |
| HIGH-04 trust del launcher | registry administrado path+SHA; caller solo pasa ID | V17/V18 |
| HIGH-05 secretos en AddonBuilder | scrub en `dayz-test.ps1`; env temporal solo para lifecycle CLI | V19 |
| HIGH-06 Restricted/NotSigned | implementación offline; gate vivo explícitamente externo hasta autorización RemoteSigned | Task 6A.11 |
| HIGH-07 “durable”/timeouts | contrato request-bound; schemas verificados; config transaccional + smoke por host | V11/Task 5.6 |
| MEDIUM-01 config no transaccional | extender transacción del installer Python con backup/rollback bytes | Task 5 |
| MEDIUM-02 baseline por conteo | dos runs estables + IDs/kind/fingerprints/hashes | Task 1.0 |
| MEDIUM-03 cleanup incompleto | finally para toda salida no active; error primario preservado | V10b |
| MEDIUM-04 pipes/señales | drains concurrentes, backpressure y contrato Windows | V16/V21 |
| MEDIUM-05 TOCTOU/reparse | registry/hash/ancestros/identidad, doble validación pre-spawn | V17 |
| MEDIUM-06 2+2 no autónomo | gate externo con proveniencia; nunca labels simulados | Task 8 |
| LOW-01 substrings/`[EXACT]` futuro | parser de tabla DPF; comando focal `[DESIGN]` | Task 1/§7 |

| Hallazgo R1 | Resolución R2 | Gate |
|---|---|---|
| HIGH-01 ruta OneDrive rechazada | permitir reparse cloud no-name-surrogate; root registrado; handle/identidad/hash fijados; positivo sobre la ruta real | V17/V17b |
| HIGH-02 fault de auditoría no durable | WAL separado armado antes de transición; latch en status/snapshot/doctor; recovery por restart y reparación admin TTY | V5d/V20b/V22 |
| MEDIUM-01 cleanup oculta error | wrapper cleanup no-throw + captura `BaseException`; tipo/mensaje/causa primarios invariantes | V10b |
| MEDIUM-02 rollback pisa config | locks laterales ordenados + CAS antes de replace/rollback; conflicto no sobrescribe bytes ajenos | V11b |
| MEDIUM-03 SHA prematuro | modificar/probar consumidor primero; generar identidad/SHA y registro final después | Task 6A.5 |

| Hallazgo R2 | Resolución R3 | Gate |
|---|---|---|
| HIGH-01 acquire inmediato oculto | high-level enqueue siempre; `operation_id` previo + tombstone cancela incluso antes de llegada; active derivado revocable sin token | V7c/V10/V10c |
| HIGH-02 WAL armed no limpiable | CAS `armed→completed` normal; clear solo `completed|repaired`; recovery y crash matrix por estado | V5c/V20b/V22 |
| HIGH-03 falso CAS config | handles exclusivos `share=0` sostenidos durante compare→in-place write→verify/rollback + journal de recuperación | V11b/V11c |
| MEDIUM-01 repair no idempotente | `write_once(event_id)` bajo lock y scan de audit/backups + `repair_phase` CAS por evento | V22b |
| MEDIUM-02 marker ausente ambiguo | tabla startup exhaustiva; snapshot-fault + marker absent = fault sintético | V22c |
| MEDIUM-03 audit gate busy inicial | enqueue-always-ticket retorna queued/progress; nunca 503 por contención | V8b |

| Hallazgo R3 | Resolución R4 | Gate |
|---|---|---|
| HIGH-01 torn config no recuperable | clasificador exacto own-torn + write-all + recovery_source para rollback reentrante; external drift conserva conflicto | V11d |
| MEDIUM-01 write_once metadata | comparación de core canónico sin metadata writer; retry cross-generation | V22b |
| MEDIUM-02 tombstone doctor omitido | Task 3 incluye doctor/tests y finding de admission fence | V7d |
| MEDIUM-03 clear completed ambiguo | completed cleanup-pending + retry, sin fault ni reemitir ledger/token | V5f/V20c |

## 1. Estado verificado y causa raíz

### 1.1 Contrato vigente

- `[EXACT]` `product-spec.md:121-134` define Intent H y H1-H8. H4 exige FIFO y TTL; H8 exige el gate distribuido.
- `[EXACT]` `product-spec.md:134-147` registra que H8 se sustituyó por cuatro runners Codex; la validación real 2 Claude + 2 Codex sigue pendiente.
- `[EXACT]` `plans/2026-07-21-p0s-native-lifecycle-security-plan.md:400-419` exige que el futuro `ControlClient` extraiga la semántica `session_*`. BUG-046 debe corregirse antes para no cristalizar la semántica defectuosa.

### 1.2 Implementación actual

- `[EXACT]` `tools/dayz_mcp/session_coordination.py:13-15`: TTL de ticket/lease 120 s, slice máximo de espera 30 s y cola máxima 64.
- `[EXACT]` `tools/dayz_mcp/session_coordination.py:435-586`: `SessionCoordinator.wait(...)` renueva el ticket y devuelve `202 queued` al acabar un slice.
- `[EXACT]` `tools/dayz_mcp/session_coordination.py:1266-1276`: `_expire_due()` llama a `_grant_next_locked()` cuando queda libre la autoridad.
- `[EXACT]` `tools/dayz_mcp/session_coordination.py:1400-1433`: `_release_active_locked()` también concede automáticamente después del cierre de auditoría.
- `[EXACT]` `tools/dayz_mcp/session_coordination.py:1540-1590`: `_grant_next_locked()` extrae la cabeza, crea `_Lease`, audita `session_granted` y publica `_active` sin requerir que exista un `wait` vivo.
- `[EXACT]` `tools/tests/test_session_coordination.py:232-240`: una prueba codifica expresamente la concesión automática antes de que B vuelva a esperar.
- `[EXACT]` `tools/dayz_mcp/server.py:270-294`: `ClientRuntime` conserva token/ticket local y locks de transición.
- `[EXACT]` `tools/dayz_mcp/server.py:378-418`: los métodos de cliente actuales son `session_acquire`, `session_wait`, `session_heartbeat`, `session_release` y `session_status`.
- `[EXACT]` `tools/dayz_mcp/server.py:299-312` usa timeout HTTP default 5 s; `session_wait` lo eleva como máximo a slice+1 s (`:386-395`). Tombstone TTL 120 s excede cualquier request de coordinación prevista.
- `[EXACT]` `tools/dayz_mcp/server.py:688-716`: FastMCP expone las cinco primitivas anteriores.
- `[EXACT]` `tools/dayz_mcp/loopback.py:1094-1147`: HTTP despacha acquire/wait/heartbeat/release/status y limita wait a 30 s.
- `[EXACT]` `tools/dayz_mcp/runtime_state.py:102-185`: `CoordinationSnapshotStore` persiste snapshots de observabilidad, pero no restaura tickets/leases como autoridad viva.
- `[EXACT]` `tools/dayz_mcp/runtime_state.py:25-39` define `RuntimePaths`; `:42-86` implementa el writer JSONL y `:203-224` filtra las claves del snapshot persistido. No existe hoy un store de reconciliación.
- `[EXACT]` `tools/dayz_mcp/daemon.py:145-175,219-225` construye audit/coordinator/store y consume la generación previa; es el punto real para restaurar un latch antes de servir coordinación.
- `[EXACT]` `tools/dayz_mcp/loopback.py:1149-1165` persiste el snapshot tras cada ruta de sesión; `tools/dayz_mcp/doctor.py:408-497` valida su schema vivo.
- `[EXACT]` `tools/dayz_mcp/session_coordination.py:77-83` define el payload público de identidad permitido (`platform`, `session`, `started_at_utc`, `task_label`); el WAL reutiliza solo esas claves.
- `[EXACT]` `LF_VStorage_dev/tools/dayz-test.ps1:121-146` exige `DAYZ_MCP_CLIENT_ID_JSON` y `DAYZ_MCP_LEASE_TOKEN` en el entorno y llama al lifecycle CLI sin pasarlos por argv.
- `[EXACT]` `C:/Users/guill/Tools/LFV_D2_Executor/lfv_executor/dayz.py:418-429` copia el entorno, inyecta identidad/token y ejecuta el lifecycle CLI con `shell=False`; `tests/test_dayz.py:952-953` verifica ambas variables en el entorno del hijo.
- `[EXACT]` `C:/Users/guill/ObsidianVault/AI/20_Runbooks/dayz-mcp-agent-session-protocol.md:27` prohíbe copiar esos valores a argv, logs, handoff o documentación durable.

### 1.3 Evidencia operacional

- `[EXACT]` `C:/Users/guill/ObsidianVault/AI/10_Projects/DayZ_MCP/research/2026-07-22-lease-queue-liveness-codex.md` contabiliza 30 grants FIFO en siete días y 5 grants no reclamados (16,7 %).
- `[EXACT]` En el incidente reproducido, el release de Codex quedó seguido por un grant ciego a Claude; el lease expiró sin uso y bloqueó la adquisición posterior. La secuencia auditada está documentada en el research anterior.
- Severidad: **degradation** de liveness. El daemon no muere, no hay crash ni corrupción; sesiones legítimas esperan leases que fueron concedidos a consumidores ausentes.

### 1.4 Línea base de 2026-07-22

- `[EXACT]` Suite focal: 126 tests, 126 OK.
- `[EXACT]` Primera observación global: 584 tests; 13 failures + 1 error lifecycle/reconcile. Segunda observación durante la auditoría: 519 tests; 4 import errors + 2 failures por drift externo. Ninguna es baseline aceptable por sí sola.
- `[EXACT]` Doctor: `{"findings":[],"ok":true,"summary":{"fail":0,"warn":0}}`.
- Gate de regresión: antes del primer diff se requieren dos observaciones idénticas y su manifest de IDs/fingerprints/hashes. La suite focal debe estar verde y la final no puede introducir ningún ID/fingerprint ausente del baseline congelado.

## 2. Decisión arquitectónica

### 2.1 Alternativa elegida primero: espera viva, no cola durable de comandos

Una cola durable capaz de “continuar una ejecución” arbitraria no puede reanudar el razonamiento interno de Claude/Codex. Para hacerlo tendría que aceptar y ejecutar comandos arbitrarios, definir un nuevo esquema persistente de jobs, credenciales, reintentos e idempotencia y convertirse en un segundo orquestador. Eso aumenta la superficie de seguridad y no resuelve la continuidad de la conversación que pidió el usuario.

La solución mínima que sí resuelve el fallo observado es:

1. Un ticket puede esperar y renovar su TTL sin poseer autoridad.
2. Solamente una llamada `wait` que sigue ejecutándose puede reclamar la cabeza cuando la autoridad está libre.
3. El cliente mantiene una única llamada MCP pendiente hasta adquirir o terminar explícitamente.
4. El host permite que esa llamada dure más que el máximo declarado.
5. Si el consumidor desaparece, su ticket expira o se cancela, pero jamás se crea un lease ciego por el mero `release` anterior.

### 2.2 Launcher seguro para `dayz-test.ps1`

Una shell “pelada” no recibe el token de una sesión MCP y no debe recibirlo manualmente: cualquier tool call o comando que lo interpolase quedaría en transcript. El patrón aprobado es un proceso Python que:

1. crea su propia `ClientIdentity` y llama al mismo daemon;
2. usa el flujo de espera viva hasta ser owner;
3. mantiene heartbeat solo durante la secuencia exclusiva de build/deploy/lifecycle;
4. acepta únicamente un consumidor registrado explícitamente por root canónico + identidad de root + path relativo + SHA-256 en `approved-launchers.json`; R2 registra solo `LF_VStorage_dev/tools/dayz-test.ps1`;
5. exige que `realpath(strict=True)` coincida con la ruta canónica, rechaza cualquier componente name-surrogate —symlink, junction o tag con bit `0x20000000`— y permite los reparse cloud no-name-surrogate observados en OneDrive;
6. abre el archivo final con `CreateFileW(GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN)`: el handle permite que PowerShell lea, pero impide write/delete/rename durante toda la vida del hijo; obtiene `FILE_ID_INFO`, hashea bytes desde ese mismo handle y coteja path↔handle antes de adquirir y pre-spawn;
7. lanza PowerShell con `shell=False` e identidad/token únicamente en una copia privada de `env`;
8. el propio `dayz-test.ps1` captura los dos valores en memoria al entrar y los elimina del entorno del proceso antes de preflight/build/AddonBuilder; `Invoke-LifecycleCli` los repone solo alrededor del hijo Python y los retira/restaura en `finally`;
9. drena stdout/stderr concurrentemente y redacta ambos secretos de toda salida antes de reenviarla;
10. libera en `finally` y comprueba status, incluso si el hijo falla o se cancela.

No será un runner arbitrario: no existe un argumento “confiar en este hash” controlado por el caller, ni confianza inferida por nombre/directorio. Ampliar el registro es un cambio separado y revisable. Los argumentos se transmiten como vector con `shell=False`; nunca como cadena evaluable. El registro no contiene secretos.

La primitive de handle queda cerrada contra las firmas locales antes de escribirla: `[EXACT]` `Windows Kits/10/Include/10.0.26100.0/um/fileapi.h:90-101` declara `CreateFileW`; `um/winnt.h:10252,15303-15305` declara `GENERIC_READ` y los share flags; `um/WinBase.h:9257-9262,9383-9391` declara `FILE_ID_INFO` y `GetFileInformationByHandleEx`; `um/WinBase.h:143-148` declara los flags. La implementación `ctypes` y sus `argtypes/restype` se prueban en Windows antes de usar el handle para secretos.

La invocación no usa `-ExecutionPolicy Bypass`. En el host actual todas las scopes están `Undefined`, la política efectiva es Restricted, el script está `NotSigned` y no existe `pwsh.exe`. La vía seleccionada requiere autorización del usuario para `CurrentUser=RemoteSigned`; hasta recibirla, se implementan/tests offline pero no se ejecuta ni se declara cerrado el gate PowerShell vivo. El hotfix de cola y el launcher offline son implementables sin alterar esa policy; la entrega global conserva `BLOCKED_EXTERNAL_POWERSHELL_POLICY` hasta autorización.

### 2.3 Máquina de estados

`claimable` es una condición calculada, no un estado persistido: `queue[0]` existe y `_active`, `_releasing`, `_grant_inflight`, `_handoff_pending`, `_release_audit_inflight`, `_audit_repair_inflight` y `_audit_fault` están vacíos/falsos. `_Ticket.cancel_requested` y las fases de `_GrantInFlight` existen solo en memoria de la generación; el WAL/fault de reconciliación sí se persiste porque debe sobrevivir al proceso.

| Estado | Evento | Resultado autorizado |
|---|---|---|
| cualquiera | high-level `enqueue(operation_id)` | ticket FIFO siempre; nunca grant; retry idempotente |
| libre, cola vacía | low-level `acquire` | grant inmediato legacy, ligado a operation id cuando usa ClientRuntime nuevo |
| ocupado o cola no vacía | `acquire` | ticket al final de FIFO |
| lease termina | release/expiry | autoridad libre; fence de auditoría terminal; después `notify_all`; no grant |
| release audit busy | cualquier wait | sigue queued/progress; nunca 503 por mera contención |
| release audit failed/stalled | cualquier wait | no grant; error/degradación explícita y doctor FAIL |
| WAL/fault recuperado tras restart | acquire/wait ordinario | no grant; status expone fault; solo admin repair exacto puede resolverlo |
| libre, cola no vacía | `wait` vivo del head | prepared audit → commit causal auditado → revalidación → active/token |
| libre, cola no vacía | `wait` de no-head | sigue queued |
| ticket queued | cancel propio | `cancel_requested`; no futura publicación; cancel audit + eliminación |
| ticket grant-inflight | cancel propio | marker bloquea commit; helper aborta; cancel termina eliminación |
| commit in-flight o lease derivado | cancel exacto | marker + compensación pre-publish o release exacto post-publish |
| ticket sin llamadas vivas | TTL | ticket expira; siguiente head puede reclamar en su propio `wait` |
| cancel antes de que llegue enqueue/acquire | `cancel_operation` | tombstone por identidad+operation; request tardía queda cancelled sin ticket/lease |

### 2.4 Invariantes

1. Nunca existe más de un `_active`, `_releasing` o `_grant_inflight` con autoridad efectiva.
2. Un grant FIFO solo puede nacer dentro de `wait(client, ticket_id, ...)` y solo para `queue[0]` con identidad exacta.
3. `release`, expiry, status y workers nunca llaman al helper de grant; solo cierran sus fences y notifican.
4. Claim crea `_grant_inflight` y reserva `_audit_gate` nonblocking antes del prepared. Busy restaura queued. Si lo obtiene, arma WAL; fallo de arm no escribe grant. Conserva el gate hasta prepared + commit/abort/revoked + completed/fault/clear, liberando `Condition` alrededor de I/O para permitir tombstones.
5. Claim escribe `session_grant_prepared` y después un único `session_granted` causal con `source=live_wait`, `ticket`, `operation_id`, `wait_request_id`, `wait_duration_s` y `wait_decision=active`, todavía bajo `_grant_inflight` y sin `_active`. Solo tras ambos writes, ausencia de cancel/tombstone y revalidación exacta publica active internamente, persiste un snapshot público que atribuye ese lease, marca WAL `completed` y lo limpia antes de devolver token. Si snapshot o terminal CAS falla, despublica/invalida ese lease, compensa, deja fault y no entrega token.
6. Si falla prepared o commit, no se publica active. Si commit pudo appendir antes de reportar fallo/cancel/cambio, se escribe `session_grant_revoked` con el mismo lease/ticket antes de limpiar el WAL; si falla la compensación o la transición a `fault`, el marker durable permanece `armed|fault` y bloquea toda concesión. Un fallo al retirar un marker ya `completed` no lo convierte en fault: conserva `completed` cleanup-pending, bloquea nuevos claims y reintenta únicamente el clear. Nunca hay active/token oculto entregado.
7. `operation_id` se genera en cliente antes de la primera request high-level. `cancel_operation` instala primero un tombstone bajo `Condition`; después resuelve queued, grant-inflight o active-derived exacto. Desde el marker no puede publicar/entregar autoridad aunque cancel llegue antes que enqueue/acquire o el audit espere.
8. `cancel_operation` puede devolver 202 durante resolución; 200 significa tombstone instalado y ausencia no publicable para esa identidad+operación. La herramienta alta espera acotada hasta 200/ausencia. Tombstones expiran tras TTL, tienen cap cerrado y nunca saltan de identidad.
9. `session_cancel(ticket)` legacy sin ticket propio exacto devuelve 403; `cancel_operation` solo puede invalidar un lease cuyo `source_operation_id` coincida. Ninguna ruta libera lease ajeno.
10. Release/expiry arma un WAL antes de invalidar/mover el active. `_release_audit_inflight` permanece hasta evento terminal, snapshot durable del estado post-release y transición a `completed`. Busy mantiene queued; fallo real o snapshot failure deja fault visible fail-closed. Si solo falla el clear posterior, la autoridad ya está libre pero un fence `completed` cleanup-pending mantiene la cola sin claims hasta que el retry retire exactamente ese marker, sin reemitir terminal ni crear otro token. Deadline excedido marca `release_audit_stalled` y no concede.
11. `session_acquire_wait` nunca devuelve queued; toda salida no activa ejecuta cleanup protegido/acotado y preserva error primario.
12. La espera larga está ligada a host/request. Caída total puede impedir cleanup; ticket expira y sin wait vivo no se promueve. No hay reanudación tras restart.
13. La espera no auto-renueva lease concedido; heartbeat solo durante trabajo exclusivo y release inmediato.
14. FIFO se conserva; consumidor ausente no se salta hasta cancel/TTL 120 s.
15. Launcher nunca acepta token/identidad CLI/chat; los obtiene del daemon.
16. PowerShell/AddonBuilder/hijos no lifecycle no conservan variables; solo lifecycle CLI las recibe temporalmente.
17. Launcher no guarda credenciales y redacta incrementalmente ambos streams con backpressure.
18. Launcher intenta heartbeat stop/release/status en finally; fallo es degradación, nunca kill DayZ.
19. Launcher solo ejecuta artefacto registrado root+identidad+path+SHA y fijado por handle; nunca shell/Invoke-Expression/Bypass.
20. Un WAL `armed|fault|repairing` abandonado por crash se restaura como fault/reparación pendiente. `completed` prueba terminal normal; `repaired` prueba reparación admin completa. Solo esos terminales pueden limpiarse por CAS exacto.
21. El marker no contiene token ni autoridad recuperable. Un restart invalida leases/tickets como hoy; el fault conserva únicamente la obligación de reconciliar el ledger antes de nuevos grants.
22. No existe ventana “grant en ledger, WAL limpio, lease no atribuible”: el orden obligatorio es WAL armed → ledger → publish in-memory → snapshot público durable → CAS completed → clear WAL → respuesta. Crash antes de completed recupera fault; crash después encuentra terminal normal + lease en snapshot y `daemon_restart_invalidated` lo atribuye.
23. `session_acquire_wait` nunca usa el grant inmediato: enqueue siempre crea/reutiliza ticket y audit-gate busy permanece queued. Incluso si `asyncio.to_thread` sobrevive a la cancelación, el operation tombstone impide un lease tardío.

## 3. Contrato DPF y criterios de aceptación

### 3.1 Cambio de producto

Modificar `DayZ_MCP_dev/product-spec.md` antes de código de producción:

- `[DESIGN]` H4: mantener FIFO/TTL, aclarando que una cabeza se promueve únicamente mediante un `session_wait` vivo y que release/expiry no crean leases para consumidores ausentes.
- `[DESIGN]` H9 — **Liveness real entre turnos y hosts**:
  - `session_acquire_wait` permanece pendiente y observable mientras el ticket siga queued y la request/host continúen vivos.
  - crea `operation_id` antes de I/O y siempre encola; cancel-before-arrival o respuesta acquire perdida no pueden dejar ticket/lease no cancelable.
  - La herramienta solo devuelve `active` o una terminación explícita; nunca `queued`.
  - timeout/cancel no deja un ticket/lease propio oculto.
  - release/expiry no conceden leases; una auditoría de release ocupada mantiene la cola esperando, y un fallo real queda visible fail-closed.
  - grant/release usan un WAL no secreto previo a la transición; un fallo se restaura tras restart, bloquea grants, aparece en status/doctor y requiere reparación administrativa explícita.
  - el launcher agent-safe usa un registro explícito root+identidad+path+SHA, fija el archivo con un handle read-only y limita identidad/token al proceso lifecycle sin exponerlos en tool args/transcript.
  - el gate multiproceso offline produce FIFO determinista y `fifo_grants_without_live_wait=0`.
  - el gate real 2 Claude + 2 Codex permanece abierto hasta disponer de dos sesiones Claude con proveniencia externa; no puede acreditarse con labels o runners locales.
- `[DESIGN]` H10 — **acreditación antes de secretos**:
  - todo cliente HTTP autenticado conecta primero y acredita el owner del socket exacto;
  - la acreditación exige PID único/estable, executable+argv+cwd canónicos y dos snapshots nativos v2 idénticos;
  - status 2xx o body/schema válido nunca sustituyen proveniencia OS;
  - foreign/rebind/PID/identity drift falla antes de `connection.request`, con cero bytes HTTP y sin key, identidad ni lease en el listener;
  - ClientRuntime, doctor, admin y lifecycle usan una única semántica verificada.
- `[DESIGN]` La evidencia histórica H8 permanece intacta; H9 no reescribe el pasado.

Trazabilidad:

| Trabajo | DPF | Intención servida |
|---|---|---|
| claim-on-live-wait | H4 + H9 | una cola debe esperar, no secuestrar autoridad |
| WAL/fault + reparación admin | H4 + H9 | un fallo doble no permite autoridad posterior con ledger ambiguo |
| cancelación exacta | H4 + H9 | abandono explícito sin afectar a otros |
| `session_acquire_wait` + progreso | H9 | la request viva no termina por slices de 30 s |
| timeout de hosts | H9 | la llamada MCP sobrevive al tiempo real de cola |
| gate mixto 2+2 externo | H8 + H9 | interoperabilidad real sin etiquetas simuladas; no cierra en esta sesión sin Claude |
| launcher seguro | H9 + contrato lifecycle | la ejecución limpia pertenece al harness, no a una shell con token copiado |
| corrección de plan P0.S | H9 + P0.S | no extraer la semántica defectuosa |
| acreditación pre-request del daemon | A4 + F3 + H7 + H10 | un listener local ajeno no recibe secretos ni se convierte en autoridad por responder 2xx |

### 3.2 Viability tests obligatorios

| ID | Fixture | Resultado verificable |
|---|---|---|
| V1 | A activo; B y C queued; A release | `_active is None`; B sigue posición 1; cero `session_granted` nuevo |
| V2 | estado V1; B ejecuta wait | B recibe 200/active; exactamente un grant; C queda posición 1 |
| V3 | B nunca vuelve a esperar y expira | B nunca fue lease; C reclama en su wait |
| V4 | dos waits concurrentes del mismo B | un solo grant, un solo lease/token activo |
| V5 | falla prepared audit | 503; no active; B permanece cabeza; no token |
| V5b | prepared OK, commit causal falla | no active/token; prepared no autoritativo; compensación si append fue incierto |
| V5c | crash/cambio tras WAL, prepared, commit, publish, snapshot, clear y antes de response | exactamente cero o un `session_granted` causal; siempre queda WAL o lease atribuible; nunca active oculto |
| V5d | no puede armarse el WAL previo al grant | no prepared/commit/active/token; B sigue head; error explícito |
| V5e | commit pudo appendir y compensación/transición a fault falla | marker `armed|fault` durable; cero token; todo grant posterior bloqueado |
| V5f | clear normal de `completed` falla | permanece completed cleanup-pending; no fault, no nuevo ledger/grant/token; retry exacto limpia |
| V6 | cancel head/middle/foreign | elimina solo ticket propio; preserva orden; foreign=403 |
| V7 | cancel antes/durante prepared/commit y post-publish | marker impide publish/compensa commit o libera solo lease derivado; nunca 403 seguido de active |
| V7b | respuesta wait perdida + cancel repetido | status final propio none; ninguna futura publicación del ticket |
| V7c | cancel llega antes que enqueue/acquire tardío | tombstone 200; request tardía cancelled; cero ticket/lease; siguiente waiter progresa |
| V7d | cap tombstones saturado | admission fence antes de autoridad; status/doctor FAIL con count/cap y sin identidades |
| V8 | acquire-wait pasa por varios slices | reporta progreso y termina active, nunca queued |
| V8b | autoridad libre, cola vacía, audit gate ocupado | high-level queda queued/progress y adquiere al liberarse; nunca 503 |
| V9 | acquire-wait agota máximo | tombstone/cancela operation exacta y devuelve error de timeout estable |
| V10 | task MCP cancelada | cleanup acotado; sin ticket/lease propio en status final |
| V10b | falla progress/schema/transporte en cada frontera | mismo cleanup protegido; error original preservado; degradación secundaria explícita |
| V10c | cancel durante `to_thread` de enqueue/wait, publish o postprocesado | operation cancelable ya conocida; own ticket/lease none sin esperar TTL |
| V11 | configuración de hosts | Claude `timeout > max_wait_ms`; Codex `tool_timeout_sec > max_wait_s` |
| V11b | fallo/drift durante open/write/verify/rollback | sin ambos handles exclusivos no hay write; rollback usa los mismos handles y verifica bytes |
| V11c | escritor no cooperativo en barrera compare→write | falla con sharing violation mientras ambos handles `share=0` viven; cero lost update |
| V11d | kill/torn write/rollback reentrante | own-torn se restaura byte-exacto; crash durante restore reanuda; external bytes/identity dan conflicto |
| V12 | E2E multiproceso con consumidor abandonado | ningún grant ciego; el siguiente waiter vivo progresa |
| V13 | gate real 2 Claude + 2 Codex, externo | BLOCKED si no hay proveniencia real; nunca se sustituye por labels |
| V13b | gate multiproceso local | orden esperado; mutación solo del owner; `fifo_grants_without_live_wait=0`; doctor limpio, sin afirmar 2+2 |
| V14 | launcher con daemon fake queued→active | espera sin token en argv; PowerShell inicial lo recibe para capturarlo y lo limpia antes de otros hijos |
| V15 | hijo imprime token/identidad a stdout/stderr | transcript contiene `[REDACTED]` y ninguna credencial literal |
| V16 | hijo falla o launcher recibe cancelación | release/status se intentan en `finally`; exit no se convierte en PASS |
| V17 | ruta real registrada bajo OneDrive cloud | valida root/identidad/hash, adquiere y llega a spawn fake; cloud no-name-surrogate no se rechaza |
| V17b | homónimo, hash drift, symlink/junction/name-surrogate o identity swap | rechazo antes de inyectar; el handle niega replace/delete hasta fin; release si ya adquirió |
| V18 | inspección de comando | `shell=False`; sin Bypass; args preservados como vector exacto |
| V19 | fake AddonBuilder + fake lifecycle | AddonBuilder no recibe variables; lifecycle recibe ambas exactas; entorno se restaura tras error |
| V20 | release audit lento/busy/fail/stall | busy sigue queued; success ordena terminal antes de prepared; fail/stall visible y sin grant |
| V20c | clear completed post-release falla | autoridad libre pero cola fenced cleanup-pending; retry clear progresa sin reemitir terminal |
| V21 | stdout+stderr superan pipe buffer | drains concurrentes sin deadlock, memoria acotada y redacción incremental |
| V22 | fault grant/release + snapshot + restart + doctor + admin repair | status/doctor FAIL y grants bloqueados tras restart; wait/acquire no limpian; repair TTY exacto reconcilia y desbloquea |
| V22b | crash tras cada evento/fase de admin repair | `write_once(event_id)` evita duplicados y reanuda fase exacta; una compensación semántica por fault |
| V22c | matriz snapshot null/fault × marker absent/estado | solo null+absent limpio; divergencias producen fault o terminal verificado, nunca limpieza implícita |
| V25 | socket conectado cuyo owner es foreign | `connect=1`, `request=0`, `http_bytes_sent=0`, key/identity/lease ausentes de toda captura |
| V25b | owner cambia expected→foreign entre las lecturas del 4-tuple | fail-closed antes de request; cero bytes/key |
| V25c | mismo PID pero executable/argv/cwd o snapshot B deriva/incompleto | fail-closed antes de request; cero bytes/key |
| V25d | daemon exacto, snapshots A/B idénticos | una sola request; método/path/query/body originales; respuesta bounded |
| V25e | routing ClientRuntime+doctor+admin+lifecycle | ningún callsite productivo usa directamente `urlopen`, `HTTPConnection.request` o concatena `?key` fuera del transporte acreditado |
| V25f | `launch_executable != native_executable`; cuatro consumidores | admin/doctor/lifecycle/ClientRuntime entregan native a `expected_executable`, launch sólo a `expected_argv[0]`; cualquier intercambio falla cerrado |
| V25g | missing/wrong-type fields, timeout bool/string/float, duplicados separados+`=`, ausencia individual de opcionales | schema/tipo exacto; error de provenance antes de key-read/connect; cada ausencia usa la semántica enumerada |
| V25h | dos configs read-only; reader/writer/replace/delete concurrentes; drift final; config ausente aparece | ambos handles read-only y simultáneos; readers sí; writers/deletes no; drift/aparición falla antes de key/connect; handles cierran en success/error |
| V25i | `expected.key` y `foreign.key` existentes; resolver failure en doctor/admin/lifecycle | mismatch/failure: `key_read=0`, `connect=0`, `request=0`; exact match alcanza el transporte |
| V25j | owner foreign/rebind con key ya leída localmente | `connect=1`, `request=0`, `http_bytes_sent=0`, `key_disclosed=0`; distinguir lectura local de divulgación al listener |
| V25k | módulo transitivo con aliases asignados/reexport/`getattr` y concat/f-string/`urlencode` de key | auditor detecta el bypass; sólo el sink acreditado y el probe nominal no autenticado quedan permitidos |
| V25l | referencias reales de `tools/mcp_client.py` | exclusión legacy cerrada: harness contra `mcp_server.py`, owner y cuatro scripts de evidencia; no pertenece al criterio H10 del daemon |

## 4. Superficie y contratos nuevos

Todos los nombres de esta sección son diseño pendiente de TDD.

### 4.1 Coordinador

[DESIGN]
```python
@dataclass
class _Ticket:
    # existing fields unchanged
    operation_id: str | None = None
    cancel_requested: bool = False

@dataclass
class _Lease:
    # existing fields unchanged
    source_operation_id: str | None = None

@dataclass
class _GrantInFlight:
    lease: _Lease
    ticket: _Ticket
    phase: Literal["prepared_audit", "commit_audit", "ready_to_publish"]

def cancel(
    self,
    client: ClientIdentity,
    ticket_id: str,
) -> tuple[int, dict]:
    """Mark exact ticket cancelled across queued/inflight/derived phases."""

def enqueue_wait(
    self,
    client: ClientIdentity,
    purpose: str,
    operation_id: str,
) -> tuple[int, dict]:
    """Always create/reuse a FIFO ticket; never publish authority."""

def cancel_operation(
    self,
    client: ClientIdentity,
    operation_id: str,
) -> tuple[int, dict]:
    """Tombstone before lookup; cancel late/queued/inflight/derived state."""

def _claim_waiting_head_locked(
    self,
    client: ClientIdentity,
    ticket: _Ticket,
) -> tuple[int, dict] | None:
    """Claim only queue[0] while called from that ticket's live wait."""
```

Contrato de `_claim_waiting_head_locked`:

- devuelve `None` cuando aún no es reclamable o el release audit sigue ocupado;
- mantiene el ticket en `_queue[0]` durante `session_grant_prepared` y registra `_grant_inflight` con fase exacta;
- reserva `_audit_gate` nonblocking; `busy` restaura estado queued y deja que wait duerma/progrese; callback false/exception es el único `audit_failed`;
- tras prepared OK escribe un solo `session_granted` causal sin publicar active;
- arma WAL antes de prepared; devuelve `(200, active_payload)` solo tras commit OK, ausencia de tombstone/marker, revalidación exacta, publicación interna, snapshot, CAS `completed` y clear durable;
- ante fallo/cancel/cambio compensa un commit incierto y no publica/exhibe token;
- nunca se invoca desde acquire, release, expiry, status o un worker de auditoría.

Contrato de `cancel`:

- bajo `Condition`, valida identidad exacta y marca `cancel_requested` antes de I/O;
- queued: audita y elimina; grant-inflight: el helper observa marker y aborta/revoca; active-derived: revoca solo si coinciden client + source_ticket;
- 202 significa “marker aceptado, resolución en curso y publicación futura prohibida”; 200 significa “ticket/lease derivado ausente y no publicable”;
- audit failure conserva el marker fail-closed. Repetir cancel o consultar status nunca puede producir la secuencia `403` seguida de active.
- `status.self.state="cancelling"` refleja un marker propio queued/inflight/provisional; no añade token ni cambia el snapshot persistido.

Contrato high-level por operación:

- `[DESIGN] operation_id` es UUID/128-bit aleatorio validado, conocido antes de I/O y reutilizado en retry; no es secreto ni autoridad.
- `[DESIGN] enqueue_wait` siempre devuelve queued/ticket —también con daemon libre—, es idempotente para misma identidad+operation+purpose y rechaza colisión distinta. Audit gate busy no afecta al enqueue.
- `[DESIGN] cancel_operation` instala tombstone antes de buscar. Si cancel llega primero, un enqueue/acquire tardío con esa identidad+operation devuelve cancelled y nunca crea ticket/lease.
- Tombstones usan TTL 120 s, cap 128 y purga temporal bajo `Condition`; saturación instala un fence de admisión: todo operation id nuevo se rechaza **antes** de ticket/grant hasta liberar capacidad. Doctor/status la hacen visible. No se reutiliza un operation id tombstoned.
- Ticket, `_GrantInFlight` y lease derivado conservan el mismo operation id. Cancel active-derived compara identidad+`source_operation_id`, por lo que no necesita token y no toca grants legacy/ajenos.
- `ClientRuntime.session_acquire` nuevo también genera operation id y cancela por él si su HTTP se pierde; `session_acquire_wait` usa `enqueue_wait` para que contención de audit gate siempre sea queued.

### 4.1A WAL y latch de auditoría

`[DESIGN]` `CoordinationFaultStore` usa `%LOCALAPPDATA%/DayZ_MCP/coordination-fault.json`, separado de `coordination.json`. Schema cerrado `format_version=1`:

[DESIGN]
```json
{
  "format_version": 1,
  "fault_id": "opaque-id",
  "daemon_generation": "generation-that-armed-it",
  "state": "armed-fault-completed-repairing-or-repaired",
  "operation": "grant-or-release",
  "phase": "closed-enum",
  "lease_id": "public-lease-id",
  "ticket_id": "public-ticket-id-or-null",
  "client": {"platform": "...", "session": "...", "started_at_utc": "...", "task_label": "..."},
  "reason": "closed-or-bounded-public-reason",
  "armed_at_utc": "ISO-8601",
  "failure": "closed-enum-or-null",
  "expected_snapshot_revision": 123,
  "repair_phase": "none-compensation-or-repair-event"
}
```

No admite keys extra, token, argv, command, propósito libre ni payload arbitrario. El store escribe temp same-directory + flush/fsync + `os.replace`; `arm` exige ausencia y cada transición usa CAS por `fault_id` + SHA de bytes. Terminal normal: `armed→completed` únicamente después de ledger + snapshot postcondición. Repair: `fault→repairing→repaired`. `clear` solo acepta `completed|repaired`. Si el proceso cae con `armed`, la siguiente generación lo interpreta como fault; `completed` solo se limpia tras verificar operación/lease/revisión contra snapshot. Un error de I/O al limpiar `completed|repaired` conserva ese estado terminal como cleanup-pending y un fence fail-closed; el retry solo repite el clear y jamás reemite ledger, muta autoridad ni degrada el terminal a `fault`.

`[DESIGN]` `SessionCoordinator` recibe callbacks `fault_arm`, `fault_transition`, `fault_clear` y `persist_snapshot`, más un `recovered_audit_fault` validado al arrancar. `_audit_fault` entra en `claimable=false`, `snapshot_payload.audit_fault` expone solo el schema público y `status` lo devuelve sin secretos. `CoordinationSnapshotStore` persiste esa vista; el fault store separado es la fuente de recuperación. `persist_snapshot` se ejecuta fuera de `Condition` pero dentro del fence y confirma revisión/postcondición antes de `completed`.

`[DESIGN]` `JsonlAuditWriter.write_once(event_id, event)` mantiene el lock durante scan+append e inspecciona `events.jsonl` y cinco backups. Igualdad usa JSON canónico `sort_keys` del evento ya redacted, excluyendo **solo** `timestamp_utc` y `daemon_generation` que `write` inyecta en `runtime_state.py:72-75`; conserva `event_id`, reason, decision, fault/lease/ticket/operation/client. Retry cross-generation con mismo core es success sin append; mismo ID con core distinto o JSON inválido falla cerrado. IDs: `<fault_id>:compensation` y `<fault_id>:repaired`.

`[DESIGN]` Reparación: nueva ruta autenticada `POST /admin/audit-repair`, nunca FastMCP ordinario. `admin_cli.py audit-repair --reason ...` exige TTY y `REPAIR <fault_id>`. CAS pasa fault→repairing; `write_once` escribe compensación/reconciliación y después repaired, actualizando `repair_phase` tras cada paso. Solo entonces CAS `repaired`, fence, snapshot-null, clear y notify. Crash/retry escanea event IDs antes de append y reanuda fase sin duplicado semántico/físico. Error conserva latch. No mata procesos, no restaura autoridad ni acepta fault id sin contrastarlo.

Arranque `[DESIGN]`: `daemon._activate_server_coordination` carga snapshot+marker antes del coordinador y aplica esta tabla; ninguna request se sirve antes:

| Snapshot | Marker | Resultado startup |
|---|---|---|
| `audit_fault=null` y autoridad pública válida/ausente | absent | limpio; luego invalidación normal de generación previa |
| `audit_fault!=null` | absent | fault sintético `fault_marker_missing`; no auto-repair |
| cualquiera | corrupt/unreadable | fault sintético `fault_store_unreadable`; no overwrite |
| cualquiera | `armed|fault` | restaurar fault; no grant |
| cualquiera | `repairing` | reanudar solo admin repair por phase+write_once; no grant |
| postcondición exacta coincide | `completed` | clear idempotente; después restart invalidation normal |
| postcondición no coincide | `completed` | fault sintético `wal_completion_mismatch` |
| snapshot-null/revisión esperada | `repaired` | finalizar clear de repair; limpio |
| otra combinación | `repaired` | fault sintético `repair_snapshot_mismatch` |

Marker absent equivale a limpio solo en la primera fila. `daemon_restart_invalidated` nunca borra fault. Fault sintético requiere restore/admin explícito y doctor FAIL.

`[DESIGN]` Los estados `completed|repaired` cuya postcondición exacta ya coincide pero cuyo archivo no puede retirarse se cargan como cleanup-pending, no como fault semántico. `status`/doctor distinguen esa degradación de un fallo de ledger, mantienen `claimable=false` y ejecutan un retry acotado del clear. Solo después de confirmar ausencia del marker se notifica la cola.

### 4.2 HTTP y runtime

[DESIGN]
```python
POST /session/cancel
{
    "identity": {"...": "identidad existente"},
    "ticket": "ticket-exacto"
}

POST /session/enqueue
{
    "identity": {"...": "identidad existente"},
    "purpose": "propósito acotado",
    "operation_id": "id-cliente-previo"
}

POST /session/cancel-operation
{
    "identity": {"...": "identidad existente"},
    "operation_id": "id-cliente-previo"
}

async def ClientRuntime.session_cancel(self, ticket: str) -> dict[str, Any]: ...

async def ClientRuntime.session_acquire_wait(
    self,
    purpose: str,
    max_wait_s: float = 1800.0,
    progress_cb: Callable[[float, float, str], Awaitable[None]] | None = None,
) -> dict[str, Any]: ...
```

Constantes propuestas:

- `[DESIGN]` `SESSION_ACQUIRE_WAIT_MAX_S = 1800.0`.
- `[EXACT]` slices internos limitados por el `WAIT_MAX_S = 30.0` existente.
- `[DESIGN]` timeout Codex por servidor `tool_timeout_sec = 1860`.
- `[DESIGN]` timeout Claude por servidor `timeout = 1860000` ms.

Los 60 s de margen separan el deadline funcional del corte del host y permiten cleanup/serialización.
Estos valores ya fueron aceptados por las builds locales mediante probes temporales. Falta el smoke de una llamada real larga en sesiones nuevas después de aplicar la configuración.

`session_acquire_wait` no expone `operation_id` en el schema FastMCP: lo crea el runtime antes de `enqueue`. El loop conserva `operation_id` aunque todavía no exista ticket. Toda salida no active llama `[DESIGN] _cancel_operation_until_absent_no_lock(operation_id)`; si el enqueue thread llega después, el tombstone lo absorbe. `returned_active=True` se asigna solo después de construir completamente el payload final que se devolverá.

### 4.3 Herramienta FastMCP

- `[EXACT]` FastMCP 1.27.2 inyecta `Context` por anotación en `tools/.venv-mcp/Lib/site-packages/mcp/server/fastmcp/server.py:1098-1129`.
- `[EXACT]` `Context.report_progress(progress, total=None, message=None)` existe en `tools/.venv-mcp/Lib/site-packages/mcp/server/fastmcp/server.py:1162-1180` y no falla cuando la petición no trae `progressToken`.

[DESIGN]
```python
@app.tool(
    description=(
        "Recommended lease entrypoint: remain queued with progress until this "
        "client owns the lease or an explicit timeout/cancellation occurs."
    )
)
async def session_acquire_wait(
    purpose: str,
    ctx: Context,
    max_wait_s: float = 1800.0,
) -> dict[str, Any]:
    ...

@app.tool(description="Cancel this client's exact FIFO ticket.")
async def session_cancel(ticket: str) -> dict[str, Any]:
    ...
```

Durante implementación se ajustará la anotación/default exactos a lo que acepte la versión local de FastMCP mediante test de registro; no se escribirá la firma de producción hasta que ese test rojo confirme el contrato.

### 4.4 Launcher seguro local

[DESIGN]
```python
async def run_approved_dayz_test(
    *,
    launcher_id: str,
    script_args: Sequence[str],
    purpose: str,
    config: ServerConfig,
    process_factory: Callable[..., Awaitable[ProcessLike]] = asyncio.create_subprocess_exec,
) -> int:
    """Acquire, inject secrets only via child env, redact output, release/status."""
```

[DESIGN]
```text
python -m dayz_mcp.secure_launcher \
  --launcher lf_vstorage_dayz_test \
  --purpose "<descripción no secreta>" \
  -- -Mode all -Build
```

El parser no define `--script`, `--root`, `--expected-sha`, `--lease-token` ni `--client-identity`. El ID resuelve root identity + relative path + hash desde el registro administrado; el caller no puede ampliar confianza. La identidad/token se obtienen después de abrir/verificar el handle y antes del hijo. El handle del script permanece abierto con share-read exclusivo hasta que PowerShell termina, cerrando la ventana path/hash→open sin copiar el script ni romper `$PSScriptRoot`.

### 4.5 Transacción recuperable de configuración de hosts

`[DESIGN]` La transacción usa un directorio privado ACL bajo `%LOCALAPPDATA%/DayZ_MCP/host-config-transactions/<transaction_id>/`. Antes del primer byte de destino persiste y fsync-a: manifest cerrado, original byte-exacto, target byte-exacto, longitudes, hashes SHA-256, `FILE_ID_INFO`, path canónico y estado global `prepared`. Original, target y posteriores `recovery_source` pueden contener configuración sensible: no se imprimen, no entran en argv/audit y solo son legibles por el usuario actual. Se abren **los dos** destinos con `GENERIC_READ|GENERIC_WRITE, share=0` en orden estable y se valida identidad+original desde esos mismos handles antes de pasar a `writing`.

`[DESIGN]` Cada destino se escribe mediante un bucle `write_all` secuencial desde offset cero que verifica `bytes_written > 0`, avanza exactamente el total escrito, ejecuta `SetEndOfFile` al terminar y después `FlushFileBuffers`; no se asume que un `WriteFile` cubra todo el buffer. Sean `O` los bytes originales, `T` el objetivo y `C` el contenido observado tras un crash antes del truncate. El clasificador `own_torn(O,T,C)` acepta exclusivamente:

```text
C == O
C == T
0 <= k <= min(len(O), len(T)) and C == T[:k] + O[k:]
len(O) < k <= len(T) and C == T[:k]
```

El tercer caso cubre target más corto, igual o más largo antes de truncar; el cuarto cubre extensión parcial. La igualdad es byte-exacta y el `k` se obtiene/valida, no se infiere de un simple hash permitido. Tras verificar ambos targets y sus parsers, el manifest pasa y se fsync-a como `committed`; solo ese estado acepta ambos `C == T` como éxito final.

`[DESIGN]` En recovery se adquieren de nuevo ambos handles exclusivos y se revalida `FILE_ID_INFO`. Si el estado no es `committed`, primero se clasifican **ambos** archivos sin escribir. Identidad distinta o bytes fuera de `own_torn` producen `registration_recovery_conflict`, conservan journal+destinos y realizan cero writes en ambos. Si los dos son admisibles, se persiste/fsync-a para cada uno el contenido actual exacto `S=recovery_source`, se cambia/fsync-a el manifest a `restoring_original` y solo entonces se restaura `O` con el mismo `write_all`.

`[DESIGN]` Si el proceso vuelve a caer durante rollback, el clasificador de restauración acepta únicamente `C == O`, `C == S`, `O[:r] + S[r:]` para `0 <= r <= min(len(O),len(S))`, o `O[:r]` para `len(S) < r <= len(O)`. Reanuda desde cero de forma idempotente, trunca, flush-ea y verifica bytes+parser. Cuando ambos originales coinciden, el manifest pasa a `restored` y se archiva. Un crash al persistir `recovery_source` antes de `restoring_original` sigue usando el clasificador inicial; después de esa transición siempre usa `S`. Bytes externos que sean byte-a-byte idénticos al target o a una secuencia propia son observacionalmente indistinguibles y semánticamente equivalentes al estado registrado; toda deriva distinguible, incluido cambio de identidad o una configuración válida diferente escrita in-place, queda en conflicto sin overwrite. Los fixtures preservan también el contenido en conflicto sin mostrarlo.

## 5. Persistencia, compatibilidad y rollback

### 5.1 Autoridad no recuperable + WAL de reconciliación — seleccionada

No se añade `claimable`, lease, ticket, job, campaign ni task recuperable a disco. `claimable` se calcula bajo `Condition` y un restart invalida la autoridad anterior. Sí se añade observabilidad/reconciliación no secreta: `coordination.json` gana la clave obligatoria `audit_fault` (`null` o vista pública cerrada) y `%LOCALAPPDATA%/DayZ_MCP/coordination-fault.json` actúa como WAL fuente. El marker no autoriza ni reanuda trabajo; solamente impide un grant nuevo hasta reconciliar el ledger.

El launcher añade una configuración de confianza no secreta `tools/approved-launchers.json`, separada de la autoridad del daemon. Schema cerrado `[DESIGN]`: top-level exacto `format_version=1` y `launchers`; una entrada inicial exacta con `id="lf_vstorage_dayz_test"`, root absoluto canónico, identidad `FILE_ID_INFO` del root, path relativo `tools/dayz-test.ps1` y SHA-256 uppercase del script post-cambio. Duplicados, keys extra, root/path/hash/identidad inválidos, escape del root o archivo ausente fallan antes de acquire.

### 5.2 Datos legacy

- Snapshot legacy sin `audit_fault` se normaliza a `null`; no se restaura lease/ticket como autoridad. Marker ausente equivale a limpio **solo** cuando el snapshot validado también tiene `audit_fault=null`.
- Snapshot-fault + marker ausente produce fault sintético. Marker válido `armed|fault|repairing` restaura latch; `completed|repaired` exige postcondición/revisión exacta antes de clear. Toda otra divergencia falla cerrado según la tabla §4.1A.
- Audit JSONL anterior continúa siendo legible. Se conserva `session_granted` y `reason="fifo_head"`; cambia el punto causal en que se emite, no el esquema.
- Los eventos nuevos `session_grant_prepared` y `session_grant_revoked` delimitan intent/compensación y nunca representan ownership entregado. `session_granted` conserva nombre/reason legacy y añade campos opcionales causales. El parser nuevo clasifica eventos legacy sin esos campos; lectores antiguos ignoran campos/eventos desconocidos.
- Herramientas primitivas `session_acquire` y `session_wait` continúan disponibles para scripts/gates.
- Ausencia de `approved-launchers.json` o de una entrada exacta deshabilita solo el launcher fail-closed; no afecta coordinación/MCP. No se auto-registra ningún script legacy por nombre.

### 5.3 Rollback

- Lectores antiguos toleran la clave extra `audit_fault`, pero al escribir vuelven a omitirla. Por ello rollback solo es operativo si no existe marker WAL y `audit_fault=null`.
- Los eventos de cancelación usan el evento existente `ticket_cancelled` con `[DESIGN] reason="client_cancel"`; lectores que ignoren razones desconocidas siguen funcionando.
- El código anterior ignora el registro de launchers y el fault store; ninguno contiene credenciales. Si existe un marker WAL, **no se permite rollback**: primero se conserva el binario nuevo y se ejecuta reparación admin explícita. Forzar rollback con marker existente perdería el fence aunque dejara el archivo en disco.
- Rollback del launcher restaura byte-exacto el `dayz-test.ps1` y elimina/archiva el registro solo después de verificar que no hay launcher activo.
- Antes de cambiar configuración real de hosts, guardar copias locales con timestamp fuera del repo y documentar su ruta. La restauración consiste en recuperar esas copias o volver a registrar la configuración anterior.
- La transacción de config añade un índice no secreto `%LOCALAPPDATA%/DayZ_MCP/host-config-transaction.json` y artefactos byte-exactos originales/target/recovery-source bajo un directorio ACL del usuario. Legacy los ignora. Antes de escribir se fsync-an manifest+artefactos; recovery adquiere ambos handles exclusivos, valida identidad y aplica los clasificadores de §4.5. Una deriva distinguible deja `registration_recovery_conflict` sin escribir ningún destino; `prepared|writing|restoring_original` converge a originales y `committed` exige ambos targets exactos.
- Revertir código restaura auto-grant, por lo que revierte también el arreglo; queda expresamente documentado como degradación conocida, no como rollback seguro operacional.
- Antes de tocar runtime persistente se copia `coordination.json`, el marker si existe y los audit JSONL a backup timestamp. No hay migración destructiva. La reparación archiva la copia exacta del marker resuelto antes de retirarlo de la ruta activa.

## 6. Plan de implementación TDD

### Task 1: Formalizar H9 y congelar pruebas de aceptación

**Files:**

- Modify: `DayZ_MCP_dev/product-spec.md`
- Create: `DayZ_MCP_dev/tools/tests/test_bug046_lease_queue_liveness.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_task9_protocol_docs.py`
- Create: `DayZ_MCP_dev/reviews/2026-07-22-bug046-baseline.json`

**Step 0 — Gate de estabilidad antes del primer diff:** hashear todos los archivos de producción/tests en scope y ejecutar dos veces suite focal + global sin cambios entre ejecuciones. Los dos resultados deben tener mismos tests descubiertos, IDs, clase failure/error y fingerprint normalizado. Si difieren o sigue el import roto por `PowerShellProcessGuard`, detener Task 1 y registrar drift externo; no arreglar lifecycle incidentalmente.

El baseline JSON tiene schema cerrado `[DESIGN]`: `format_version=1`, `captured_at_utc`, `source_hashes` (path→SHA256), `discovered_test_count`, `non_green` (array ordenado de objetos exactos `test_id`, `kind`, `message_fingerprint`) y `focal_green`. Ningún fallo nuevo se acepta aunque otro desaparezca y el total sea igual.

**Step 1 — RED contractual:** añadir un parser mínimo de la tabla DPF que exija filas normativas H4/H9 y campos de verificación exactos; no usar `assertIn` sobre texto libre. Ejecutar solo esos tests y observar fallo por fila H9 ausente.

[DESIGN]
```python
def test_product_spec_requires_live_wait_claim_and_no_blind_grant(self) -> None:
    rows = parse_acceptance_rows(PRODUCT_SPEC.read_text(encoding="utf-8"))
    self.assertEqual(rows["H4"].state, "✓ offline")
    self.assertRegex(rows["H4"].criterion, r"live wait.*release/expiry.*no concede")
    self.assertRegex(rows["H9"].verification, r"fifo_grants_without_live_wait=0")
```

**Step 2 — GREEN contractual:** editar H4/H9 sin alterar la evidencia histórica H8. Repetir el test.

**Step 3 — Congelar fixtures:** construir en el test nuevo helpers de identidad, reloj, auditoría y cleanup reutilizando las firmas reales ya existentes. No duplicar una segunda implementación del coordinador.

**Step 4 — Checkpoint:** registrar hashes de `product-spec.md`, tests y baseline. Antes de cada Task posterior, rehashear scope; cualquier drift ajeno detiene la escritura.

### Task 2A: Construir WAL/fault durable, doctor y reparación administrativa

**Files:**

- Modify: `DayZ_MCP_dev/tools/dayz_mcp/runtime_state.py`
- Modify: `DayZ_MCP_dev/tools/dayz_mcp/session_coordination.py`
- Modify: `DayZ_MCP_dev/tools/dayz_mcp/daemon.py`
- Modify: `DayZ_MCP_dev/tools/dayz_mcp/loopback.py`
- Modify: `DayZ_MCP_dev/tools/dayz_mcp/doctor.py`
- Modify: `DayZ_MCP_dev/tools/dayz_mcp/admin_cli.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_runtime_state.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_doctor.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_lifecycle_http.py`
- Create: `DayZ_MCP_dev/tools/tests/test_bug046_audit_fault_recovery.py`

**Step 1 — RED store/schema:** fixtures legacy, armed, fault, completed, repairing, repaired, corrupto, extra-key, secreto y CAS conflict. Exigir máquina de transiciones cerrada; solo completed/repaired pueden clear; fault/hash/generation/revisión incorrectos no modifican bytes.

**Step 2 — GREEN store mínimo:** añadir path derivado `RuntimePaths.coordination_fault_path` sin romper constructores actuales; implementar store con atomic write/fsync/replace y CAS. No conectar todavía grant/release.

**Step 3 — RED/GREEN snapshot + startup V22/V22c:** `audit_fault` obligatorio, legacy→null y tabla §4.1A exhaustiva. Barreras en armed→completed→clear y fault→repairing→repaired→clear. `daemon_restart_invalidated` no limpia fault; completed requiere postcondición exacta. Corrupt/missing mismatch produce latch sintético.

**Step 4 — RED/GREEN doctor:** schema válido `null|fault`; fault válido produce `[DESIGN] COORDINATION_AUDIT_FAULT` de severidad FAIL y `--require-clean` no cero; schema inválido conserva `DAEMON_STATUS_UNREADABLE`. Incluir `fault_id`, operation y phase públicos, nunca token/payload arbitrario.

**Step 5 — RED/GREEN admin repair V22b:** añadir `JsonlAuditWriter.write_once`, `/admin/audit-repair` y CLI. Exigir HMAC, reason, fault id de status, TTY y `REPAIR <fault_id>`. CAS fault→repairing; append/scan determinista por event id; CAS repair_phase tras compensación y repaired; CAS repaired; snapshot-null; clear. Crash después de cada append/CAS reanuda sin duplicado. Repetir el mismo event id con core canónico idéntico pero distinta `daemon_generation`/`timestamp_utc` es success sin append; cambiar reason/decision/fault/lease/ticket/operation/client con el mismo id o hallar audit corrupto conserva fault.

**Step 6 — Integración fail-closed:** `SessionCoordinator` recibe callbacks/store, `persist_snapshot` y fault recuperado; `claimable` y grants comprueban fault/repair fences. Exigir armed→audit→publish→snapshot→completed→clear→response y armed→invalidate→terminal→snapshot→completed→clear para release. Acquire/wait/cancel/status ordinarios jamás limpian fault; completed cleanup puede reintentarse sin grant hasta clear.

**Step 7 — Compatibilidad/rollback:** testear ausencia legacy, nueva clave ignorada por lector tolerante y prohibición documentada de rollback con marker activo. Backup/archivo del marker no contiene secretos.

### Task 2B: Sustituir auto-grant por claim-on-live-wait

**Files:**

- Modify: `DayZ_MCP_dev/tools/dayz_mcp/session_coordination.py`
- Modify: `DayZ_MCP_dev/tools/dayz_mcp/runtime_state.py`
- Modify: `DayZ_MCP_dev/tools/dayz_mcp/doctor.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_session_coordination.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_task7_final_authority_regressions.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_bug046_lease_queue_liveness.py`

**Step 1 — RED V1:** invertir la prueba de `test_session_coordination.py:232-240`: después de release no debe existir owner y B debe seguir queued. Ejecutar y confirmar que falla porque el código actual promueve B.

[DESIGN]
```python
status, released = coordinator.release(client_a, token_a)
self.assertEqual(status, 200)
self.assertIsNone(coordinator.status(client_b)["owner"])
self.assertEqual(coordinator.status(client_b)["self"]["position"], 1)
self.assertEqual(grant_events_after_release, [])
```

**Step 2 — RED V20/V20b:** bloquear el worker de auditoría de release y probar wait 0/largo, success tardío, callback fail, stall, fallo de arm/completed/clear y restart. El código actual debe fallar por early-clear, 503 por busy, orden físico incorrecto o ausencia de latch durable.

**Step 3 — GREEN release fence:** reservar release, armar WAL antes de invalidar/mover active y mantener `_release_audit_inflight`/`_handoff_pending` hasta evento terminal + snapshot post-release + CAS completed + clear. Quitar grants desde release/worker/expiry. Busy devuelve queued/progress. Arm fail deja active intacto; audit/snapshot/terminal-CAS fail o stall convierte marker en fault. Clear de completed se reintenta acotado; mientras falle queda cleanup-pending visible y ningún nuevo WAL/grant se arma.

**Step 4 — RED V2/V3/V5:** tests de wait del head, head ausente, audit gate ocupado, prepared fail, commit fail/append parcial y cambios de coordinación en cada write. Busy sigue queued; callback fail da 503 sin `_active`.

**Step 5 — GREEN claim causal:** reemplazar `_grant_next_locked` por `_claim_waiting_head_locked`, invocado solo desde wait. Reservar `_audit_gate` nonblocking; busy vuelve queued. Armar WAL antes de prepared; mantener ticket en cabeza y no crear `_active` durante prepared+commit. Tras writes, revalidar tombstone/fences/identidad, retirar ticket, publicar internamente, persistir snapshot, CAS completed y clear antes de entregar active/token. Ante commit incierto o snapshot/terminal-CAS fallido escribir compensación y fault; tombstone se revalida en cada frontera. Liberar audit gate en finally.

**Step 5b — RED/GREEN grant inmediato low-level:** `ClientRuntime.session_acquire` aporta operation id previo. El grant rápido usa armed→audits→publish→snapshot→completed→clear→response con `ticket_id=null` y revalida tombstone. Cancel-operation puede revocarlo sin token. Si audit gate está busy, se crea ticket queued en vez de 503. No crear un protocolo causal distinto.

[DESIGN]
```python
if (
    ticket is self._queue[0]
    and self._active is None
    and self._releasing is None
    and self._grant_inflight is None
    and not self._handoff_pending
    and not self._release_audit_inflight
    and not self._audit_repair_inflight
    and self._audit_fault is None
    and not self._is_operation_cancelled_locked(ticket.operation_id)
    and not ticket.cancel_requested
):
    claim_result = self._claim_waiting_head_locked(client, ticket)
    if claim_result is not None:
        return claim_result
```

**Step 6 — RED/GREEN V4/V5c/V5d/V5e:** barreras tras armed/prepared/commit/publish/snapshot/completed/clear/antes de response. Camino nominal elimina marker sin admin. Crash armed recupera fault; crash completed verifica/limpia y atribuye snapshot. Tombstone en cualquier barrera produce cero token y compensación exacta. Fallo de arm no toca ledger; fallo de snapshot/compensación/terminal-CAS deja fault.

**Step 7 — Migrar regresiones existentes:** localizar expectativas de auto-grant y migrarlas al wait vivo. Cambiar el contrato de orden a “release terminal antes de grant prepared/committed”; no relajar exclusión, cleanup ni orden físico.

**Step 8 — Validación focal:** ejecutar coordinator + authority. No avanzar con fallos ni con drift respecto al baseline.

### Task 3: Añadir cancelación exacta y segura

**Files:**

- Modify: `DayZ_MCP_dev/tools/dayz_mcp/session_coordination.py`
- Modify: `DayZ_MCP_dev/tools/dayz_mcp/loopback.py`
- Modify: `DayZ_MCP_dev/tools/dayz_mcp/daemon.py`
- Modify: `DayZ_MCP_dev/tools/dayz_mcp/doctor.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_bug046_lease_queue_liveness.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_session_http.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_client_mode.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_doctor.py`

**Step 1 — RED V6/V7/V7c:** probar ticket head/middle/foreign y operation cancel antes de llegada, durante enqueue, prepared/commit/publish/completed y antes de response. Mantener el HTTP `asyncio.to_thread` vivo tras cancelar await. Exigir tombstone previo, no ticket/lease tardío y siguiente waiter sin TTL.

**Step 2 — GREEN coordinator:** implementar `enqueue_wait` idempotente y `cancel_operation` tombstone-first. Operation se propaga ticket→inflight→lease. Claim/inmediato revalidan tombstone y abortan/revocan. Cancel 202 durante resolución y 200 cuando el tombstone garantiza no-publicación. Ticket foreign sigue 403; operation cancel de active exacto ya no necesita token.

**Step 3 — RED/GREEN HTTP:** añadir `enqueue`, `cancel` y `cancel-operation` a `_handle_session`, schemas exactos, límites/cap, persistencia y status. Cancel-before-arrival se prueba con dos requests reales en orden invertido.

**Step 3b — RED/GREEN admission fence V7d:** saturar el cap de tombstones y exigir rechazo de toda operación no vista **antes** de crear ticket/inflight/lease. `status` y doctor publican `[DESIGN] OPERATION_TOMBSTONES_SATURATED` con severidad FAIL y solo count/cap, nunca identidades; `--require-clean` sale no cero. Tras TTL/cleanup bajo lock el finding desaparece y la admisión vuelve sin restart.

**Step 4 — RED/GREEN runtime:** añadir cancel público por ticket y privado por operation. `session_acquire` genera operation id antes de `_session_call`; en `BaseException` cancela esa operación. Helpers 202 repiten/status; 200 tombstoned limpia estado. `ticket_invalid` solo equivale a ausente tras status sin active propio.

**Step 5 — RED/GREEN MCP:** exponer `session_cancel` y comprobar su schema/nombre en la lista de tools.

**Step 6 — Carrera completa:** barreras en cancel antes de request, handler, WAL, publish, completed y response perdida. Resultado final own ticket/lease none, tombstone acotado y ninguna publicación futura. Repetir con low-level immediate y high-level always-ticket.

### Task 4: Añadir `session_acquire_wait` con progreso y cleanup

**Files:**

- Modify: `DayZ_MCP_dev/tools/dayz_mcp/server.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_client_mode.py`
- Create: `DayZ_MCP_dev/tools/tests/test_session_acquire_wait.py`

**Step 1 — RED schema:** comprobar que FastMCP registra `session_acquire_wait`, `purpose`, `max_wait_s` y la inyección de `Context` sin publicar `ctx` como argumento de usuario.

**Step 2 — RED V8/V8b/V10c:** fake runtime: enqueue siempre queued, dos waits queued y tercero active; audit gate busy conserva queued. Cancelar durante el thread de enqueue y después de publish/antes de response; operation id existe antes y cleanup deja none.

**Step 3 — GREEN mínimo:** generar operation id antes de I/O; implementar enqueue→wait bajo `_session_transition_lock`, deadline, slices y progress. Conservar operation+ticket y `returned_active`; toda salida no entregada cancela por operation aunque ticket siga `None`.

[DESIGN]
```python
operation_id = new_operation_id()
ticket: str | None = None
returned_active = False
primary_error: BaseException | None = None
try:
    response = await self._session_call(
        "/session/enqueue",
        {"purpose": purpose, "operation_id": operation_id},
    )
    self._remember_acquire_or_wait(response)
    ticket = response["ticket"]
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ToolError("session_wait_timeout")
        await report(elapsed, max_wait_s, queued_message(response))
        response = await self._session_call(
            "/session/wait",
            {"ticket": ticket, "timeout_s": min(WAIT_MAX_S, remaining)},
            timeout=min(WAIT_MAX_S, remaining) + 1.0,
            stale_ticket=ticket,
        )
        self._remember_acquire_or_wait(response)
        if response["status"] == "active":
            final_response = self._with_own_identity(response)
            returned_active = True
            return final_response
except BaseException as exc:
    primary_error = exc
    raise
finally:
    if not returned_active:
        cleanup = await cleanup_operation_never_raises(operation_id, ticket)
        if not cleanup.ok:
            if primary_error is not None:
                primary_error.add_note(cleanup.public_degradation)
            else:
                raise ToolError("session_cleanup_failed")
```

**Step 4 — RED/GREEN V9:** al expirar deadline, cancelar/tombstone operation exacta y lanzar `[DESIGN] ToolError("session_wait_timeout")`. El error se añade al conjunto remoto/local solo donde corresponda; no se convierte en `remote_error`.

**Step 5 — RED/GREEN V10/V10b/V10c:** cancelar la task mientras enqueue/wait HTTP sigue en `asyncio.to_thread`, y fallar progress/schema/transporte/postprocesado en cada frontera. `[DESIGN] cleanup_operation_never_raises` cancela por operation aun sin ticket, luego status, protegido/acotado y no-throw. El caller relanza el mismo `BaseException`; tipo/mensaje/cause/context no cambian. `returned_active` solo cambia tras construir `final_response`. Sin conectividad, el high-level nunca pudo grant desde enqueue; ticket tardío no tiene wait vivo y TTL es fallback.

**Step 6 — Progreso:** adaptar `Context.report_progress` mediante callback. Mensaje sin token completo ni secretos; puede incluir posición, segundos transcurridos y propósito truncado. Si no hay progressToken, FastMCP hace no-op según la implementación verificada.

**Step 7 — Test de desaparición brusca/reconnect:** simular proceso muerto sin cleanup y transporte perdido con request HTTP aún viva. Después de release, demostrar que sin live wait no hay grant; si el request viejo alcanzó provisional, cancel marker/revocación o TTL dejan estado limpio. Un reconnect no reanuda la request ni promete durabilidad.

### Task 5: Alinear timeouts de Claude/Codex y el instalador

**Files:**

- Modify: `DayZ_MCP_dev/tools/install_mcp.py`
- Create: `DayZ_MCP_dev/tools/dayz_mcp/host_config.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_install_mcp.py`
- Create: `DayZ_MCP_dev/tools/tests/test_mcp_host_timeouts.py`
- Modify: `DayZ_MCP_dev/tools/README-mcp.md`
- Runtime config after tests: `C:/Users/guill/.claude.json`
- Runtime config after tests: `C:/Users/guill/.codex/config.toml`

**Step 1 — Mecanismos ya verificados antes de producción:**

- `[EXACT]` `C:/Users/guill/.claude/cache/changelog.md:409` documenta `timeout` MCP por servidor en milisegundos; valores menores de 1000 se ignoran.
- `[EXACT]` El schema local Codex acepta y persiste `mcp_servers.<name>.tool_timeout_sec=1860`; probe temporal leído como `1860.0` y eliminado.
- `[EXACT]` El schema local Claude acepta y persiste `mcpServers.<name>.timeout=1860000`; probe temporal mostrado como `Timeout: 1860000ms` y eliminado.
- `[EXACT]` `tools/install_mcp.py:98-100` define `RegistrationSpec`; `:389-436` parsea Codex; `:477-535` implementa provider CLI; `:838-870` ya ofrece transacción/rollback de registro; `:951-980` es la ruta canónica `--register` sin depender de ejecutar un `.ps1`.

Los probes demostraron que `mcp add` no persiste timeouts. R4 no llama a un subprocess writer mientras pretende bloquear el archivo ni usa hash→replace. Construye bytes finales estructurados en memoria, adquiere **ambos** configs con handles `GENERIC_READ|GENERIC_WRITE, share=0` en orden estable, vuelve a leer/comparar desde esos handles y mantiene exclusión hasta write→flush→verify o rollback. El journal y artefactos exactos de §4.5 se preparan antes del primer byte y nunca se imprime contenido.

**Step 2 — RED V11/V11b/V11c/V11d:** extender `RegistrationSpec` y fixtures JSON/TOML. Probar parse/target bytes sin secretos, preservación de otras entradas y fallos en open/journal/artifact/write parcial/flush/truncate/verify/rollback. Cubrir target menor/igual/mayor, corte tras cada byte/chunk/syscall, crash durante restore y segundo recovery. Barrera exacta tras compare y antes de write: proceso no cooperativo intenta write/replace y recibe sharing violation. Si uno de los dos handles no abre exclusivo, ambos destinos quedan byte-exactos. Reemplazo externo, identidad distinta o edición in-place válida pero diferente tras crash producen conflict y cero writes; verificar contenido conflictivo solo por hash/fixture, nunca imprimirlo.

**Step 3 — GREEN exclusive helper:** Claude se parsea con `json`; Codex con `tomllib` + editor de sección exacta que rechaza duplicados. Preparar target y validarlo antes de locks. Abrir ambos paths vía `CreateFileW(..., share=0)`; leer bytes/hashes/`FILE_ID_INFO` por esos mismos handles. Drift aborta sin write. Persistir originales+targets restringidos y manifest `prepared`; luego por handle ejecutar `SetFilePointerEx(0)`, bucle `WriteFile` con progreso positivo/total exacto, `SetEndOfFile`, `FlushFileBuffers`, re-read y parse/hash verify. No `os.replace` del destino.

**Step 4 — GREEN transaction/recovery:** integrar directo en `register_transaction` sin `mcp add` durante exclusión. Implementar exactamente la máquina y clasificadores de §4.5: artefactos original/target + identidad antes de write; estados `prepared→writing→committed` o `restoring_original→restored`; captura fsync de `recovery_source` antes de rollback; restauración reentrante. En recovery se clasifican ambos destinos antes de escribir: cualquier identidad/byte distinguiblemente externo = `[DESIGN] registration_recovery_conflict` y cero writes en ambos. Fallo normal y crash pre-commit convergen a originales exactos; committed exige targets exactos. Verificar ACL, bytes, parsers, hashes y que ninguna excepción/log contenga contenido de config.

**Step 5 — Aplicación real:** preflight comprueba journal limpio/recovered y abre ambos handles exclusivos antes de escribir. Aplicar una transacción, cerrar handles, verificar por parsers y por `codex mcp get --json`/`claude mcp get` sin mostrar env/config. Comprobar 1860 s / 1.860.000 ms. No cerrar tareas abiertas ni matar hosts; exclusión fallida aplaza, no fuerza.

**Step 6 — Smoke de host:** en una sesión nueva de cada host, ejecutar una tool fixture segura que supere el timeout anterior y termine antes de 1.800 s; comprobar cancelación del host y reconnect. Si Claude no está disponible, Codex puede quedar verificado pero Claude/H9-host permanece abierto, no simulado.

### Task 5B: Romper el deadlock clientes-vivos / daemon-ausente

**Files:**

- Modify: `DayZ_MCP_dev/tools/dayz_mcp/identity_migration.py`
- Modify: `DayZ_MCP_dev/tools/dayz_mcp/daemon.py`
- Create: `DayZ_MCP_dev/tools/dayz_mcp/server_cli.py`
- Modify: `DayZ_MCP_dev/tools/dayz_mcp/server.py`
- Modify: `DayZ_MCP_dev/tools/dayz_mcp/orphan_guard.py`
- Modify: `DayZ_MCP_dev/tools/dayz_mcp/doctor.py`
- Modify: `DayZ_MCP_dev/tools/dayz_mcp/admin_cli.py`
- Modify: `DayZ_MCP_dev/tools/dayz_mcp/lifecycle_cli.py`
- Modify: `DayZ_MCP_dev/tools/dayz_mcp/host_config.py`
- Create: `DayZ_MCP_dev/tools/dayz_mcp/security_runtime_audit.py`
- Modify after classification if executable/productive: `DayZ_MCP_dev/tools/mcp_client.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_identity_migration.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_client_mode.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_daemon_security_gate.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_doctor.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_lifecycle_cli.py`
- Create: `DayZ_MCP_dev/tools/tests/test_admin_cli.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_mcp_host_timeouts.py`
- Create: `DayZ_MCP_dev/tools/tests/test_security_runtime_audit.py`
- Create: `DayZ_MCP_dev/tools/tests/test_bug046_startup_deadlock.py`
- Modify: `DayZ_MCP_dev/tools/README-mcp.md`

**Step 1 — RED V23/R14:** añadir tests sin tocar producción. Fijar la matriz CPython 3.14 y tail canónico DayZ: procesos reales `-m dayz_mcp --client --keyfile K`, forma agrupada equivalente, `--idle-timeout -1`, `--task-label=-nightly`, `--keyfile=K` y client repetido no bloquean; `-m dayz_mcp.__main__ --client --keyfile K` y script directo `dayz_mcp/__main__.py --client --keyfile K` bloquean, igual que daemon/embedded/default de los tres entrypoints. Mantener positivos W/X y negativos Python ajeno de R13. El corpus compartido exige que `server.parse_args` y el parse silencioso produzcan el mismo modo/invalid para values, equals, duplicados, modos mixtos, `--task-label -nightly`, `--task-label --daemon --client`, unknown y `--keyf`; antes de GREEN, abreviatura y falsos blockers deben fallar. Cmdline/exe incompleto conserva `process_scan_incomplete`.

**Step 2 — GREEN mínimo:** crear el builder/parser silencioso compartido `server_cli.py`; hacer que `server.parse_args` lo use con `allow_abbrev=False`; y ajustar el dispatch de `_argv_targets_dayz_mcp` para aplicar clasificación de tail sólo al module target exacto `dayz_mcp`, dejando `dayz_mcp.__main__`/script directo siempre writer. Actualizar deliberadamente las expectativas antiguas de alternative-client. No relajar el snapshot nativo, la identidad del daemon permitido, la exclusión del backup, el doble quiescence check ni los hashes/receipt. Verificar que `parse_args` termina antes de `build_app`/`run_daemon` para help/invalid y que el parse silencioso no emite salida.

**Step 2b — RED/GREEN recovery R13:** para transacciones nuevas, publicar una única provenance inmutable `prepared/revision=1`: temp create-exclusive + write-all + flush + verify + `os.rename` Windows no-clobber, re-read byte-exacto y **cero updates de phase/`os.replace`**. Inyectar crash/fallo en cada chunk/syscall del initial next, dentro de la frontera rename con destino externo, antes/después de rename/revalidación, cada chunk/fsync de backup/pending, segundo quiescence, receipt publish y cada cleanup; la segunda ejecución progresa sin drift. Sin marker ni data artifacts, initial-next ausente/prefix se retira. Con marker rev1 se acepta exactamente `rev2_expected.startswith(actual_next)`; un initial-prefix sólo se preserva si no coincide también con rev2, con asserts en common-1/common/common+1. Recovery legacy acepta rev2 + next ausente/prefix de rev3 y rev3 sin next; recorre todos los cortes byte/chunk. Marker/source/artifact/path no-prefix falla cerrado con cero deletes. Receipt legacy funciona; backup huérfano sin marker permanece bloqueado. Gate de rollback: R9 rechaza sin deletes receipt R10 + marker prepared; R10 completa cleanup y deja rollback operativo documentado. Tests de target menor/igual/mayor y rollback pre-R8.

**Step 3 — RED/GREEN elección R8:** añadir lock cross-process no bloqueante, global por `RuntimePaths.root` y poseído por handle. `run_daemon` ejecuta probe inicial → elección → probe bajo lock → migration exacta/recovery → bind → activation; libera solo después de que status pueda acreditar generación. Lock busy causa probe final: healthy→0, ausente→75; nunca espera como segundo `--daemon`. El path no incluye key/token/puerto y un archivo residual no implica ownership. Reparse/identity drift/lock I/O fallan cerrados.

**Step 3a — RED/GREEN H10/V25:** congelar primero la proveniencia local esperada del daemon; conectar sin bytes de aplicación; acreditar el owner del socket exacto y solo entonces formar/transmitir query+key/body. Migrar doctor, admin y lifecycle al mismo transporte ya usado por ClientRuntime. La policy esperada nunca procede del response body ni de un argv observado como única fuente de verdad. `host_config.py` resuelve por lectura raw y consenso exacto las entradas canónicas `dayz-mcp` de `~/.claude.json` y `~/.codex/config.toml`, reutiliza el parser silencioso canónico y construye exe/argv/cwd sin ejecutar CLIs: 0 configs=`daemon_provenance_unavailable`, 1=`daemon_provenance_incomplete`, 2 con drift=`daemon_provenance_conflict`, siempre antes de connect/request; sólo dos configs equivalentes autorizan el transporte. No hay fallback a defaults ni formato persistente nuevo. Foreign, rebind y cualquier drift fallan antes de `request`. No se cambia endpoint, método, JSON ni persistencia.

**Step 3a.1 — Hardening adversarial de provenance/routing `[DESIGN]`:** separar dos autoridades sin alias ambiguo `executable`: `launch_executable = canonical(sys.executable)`, consensuado exactamente con ambos `command` y usado sólo para `daemon.build_daemon_argv(..., python=launch_executable)`; `native_executable = canonical(orphan_guard.full_image_path_of(os.getpid()))`, usado como único `expected_executable` y hash de imagen OS. El owner se acredita contra native, mientras su argv exacto conserva launch en `argv[0]`. Los cuatro consumidores —ClientRuntime, doctor, admin y lifecycle— deben falsar explícitamente el intercambio launch/native.

Schema raw cerrado: Claude contiene exactamente `type`, `command`, `args`, `timeout` y admite sólo la extensión host observada `env={}` —presente en la entrada real el 2026-07-22—; cualquier contenido o tipo distinto en `env` falla cerrado. `type == "stdio"` y timeout entero no-bool `1_860_000`. Codex contiene exactamente `command`, `args`, `tool_timeout_sec`, con timeout entero no-bool `1_860`. `command` es string absoluto/canónico exacto; `args` es lista de strings con prefijo exacto `-m dayz_mcp`. `--client`, `--port`, `--keyfile`, `--idle-timeout` y `--client-platform` son explícitos y únicos. Opcionales permitidos aparecen como máximo una vez contando `--opt value` y `--opt=value` como la misma opción; boolean con `=` y unknown son inválidos. Ausencias enumeradas: expected-version=`None`, require-version=`False`, exec-enforce=`False`, exec-allowlist=`None`, no-autospawn ausente=`auto_spawn=True`, task-label=`""`; task-label es metadata cliente y no entra en daemon argv/policy consensus. Resolver 0/1/2 por número real de entradas `dayz-mcp`, aunque existan los ficheros.

Fijar ambas configs antes del primer parse en orden determinista con `CreateFileW(GENERIC_READ, FILE_SHARE_READ, ...)`, no-follow/no-name-surrogate; mantener ambos handles hasta una relectura final de identidad+bytes. Readers concurrentes siguen permitidos; write/overwrite/replace/delete quedan denegados para ambos paths. Config read-only debe funcionar. Aparición de un path inicialmente ausente, reparse, drift final, I/O incierto o excepción cierra handles y produce `daemon_provenance_conflict` antes de key/connect.

Doctor/admin/lifecycle comparan port y path canónico del keyfile consensuado antes de `_read_key`, antes de connect. Config/provenance inválida o mismatch port/keyfile —incluidos dos ficheros existentes distintos— exige `key_read=0`, `connect=0`, `request=0`. Foreign/rebind/PID/identity drift sólo se conoce tras conectar: exige `connect=1`, `request=0`, `http_bytes_sent=0`, `key_disclosed=0`; una key leída localmente no cuenta como divulgada.

Crear `security_runtime_audit.py` extrayendo/sustituyendo el gate AST previo. Raíces: ClientRuntime, doctor, admin y lifecycle más imports productivos transitivos. Único sink autenticado: `orphan_guard.verified_daemon_http_request`; allowlist nominal separada sólo para `probe_listener_responsive`, sin key/body sensible. Detectar aliases importados/asignados, reexports, `getattr` dinámico no resoluble, `HTTPConnection.request` y construcción de `?key` por concat/f-string/`urlencode`. `tools/mcp_client.py` queda excluido nominalmente de H10: es harness legacy contra `mcp_server.py`, owner `DayZ_MCP phase-gate harness`, evidenciado por `run-poc.ps1`, `run-fase1.ps1`, `run-fase2.ps1` y `run-fase3.ps1`. Ampliar H10 al harness sería nuevo scope/DPF y requiere aprobación separada.

**Step 3a.2 — Cierre adversarial de ClientRuntime, provenance y auditor `[DESIGN]`:** `ClientRuntime.__init__` debe resolver el consenso dual antes de leer o aceptar una key, comparar port y path canónico del keyfile, y construir la acreditación exclusivamente con `native_executable`, `argv` y `cwd` de esa provenance; ni `sys.executable`, ni el PID local, ni `ServerConfig` pueden actuar como autoridad alternativa. Los negativos unavailable/incomplete/conflict, intercambio launch/native, swap final y mismatch port/keyfile deben demostrar `key_read=0`, `connect=0`, `request=0`; foreign/rebind/identity drift conserva `connect=1` pero `request=0` y cero bytes HTTP.

La fijación dual debe reabrir cada pathname canónico inmediatamente antes de devolver y comparar FileId+bytes con el handle original mientras ambos originales siguen abiertos; así un rename del directorio ancestro y recreación del pathname no puede convertir handles estables en provenance de otro nombre. El auditor debe: (a) clasificar cualquier `getattr` HTTP dinámico no resoluble dentro de la clausura productiva como `dynamic_http`; (b) propagar aliases de métodos ligados como `send = connection.request`; (c) inspeccionar cuerpos sensibles tanto posicionales como keyword en `Request`/`urlopen`; (d) resolver `ImportFrom` relativos y reexports; (e) detectar `?key`/`&key` en `JoinedStr`, `BinOp` y constantes parciales; y (f) incluir el path exacto `tools/mcp_client.py` como candidato antes de aplicar su exclusión documental cerrada. Un homónimo bajo `dayz_mcp/` nunca hereda esa exclusión. Cada mecanismo requiere fixture positivo y negativo focal sin procesos ni listeners.

**Step 3a.3 — Cierre adversarial de aliases, autoridad y handles `[DESIGN]`:** el consumidor revalida semánticamente la provenance resuelta antes de key: launch debe coincidir con el launcher local canónico, native con la imagen OS canónica del PID propio y `argv[0]` con launch; un intercambio launch/native coherente debe fallar con `key_read=connect=request=0`. Esta revalidación sólo verifica el objeto del resolver y no permite que `ServerConfig` suministre la policy de transporte. Añadir `auto_spawn_daemon` booleano a la provenance consensuada y usarlo en `ClientRuntime`; discrepancias del flag del `ServerConfig` se rechazan antes de key.

El auditor propaga aliases intermedios de módulos HTTP y conexiones, además de taint de query autenticada a través de asignaciones: f-string/concat guardado en variable y `params={'key': ...}` → `urlencode(params)` → variable → sink deben producir finding. Los datos no HTTP/no-key equivalentes son negativos obligatorios. `_PinnedConfigFile` cierra el handle en toda excepción posterior a `CreateFileW`, incluida `_final_handle_path`. Sustituir cualquier fixture basado en `subprocess`, `COMSPEC`, `cmd`, junction real o listener por fakes deterministas de Win32/path/identity y comprobar cierre; los selectores focales no pueden contener ni intentar launch. El gate final requiere `attempts=[]` e `intercept_count=0`.

**Step 3a.4 — Gramática cerrada del único probe nominal `[DESIGN]`:** no intentar demostrar tipos HTTP mediante un dataflow Python incompleto. Cualquier llamada directa `.request(...)`, alias de atributo `.request` o `getattr(..., name)` invocado con forma HTTP —dos o más argumentos posicionales, o keywords method/url— es sink conservador con independencia de factories condicionales, herencia importada/qualified o aliases de módulo/conexión. Una llamada no HTTP de un argumento conserva un fixture negativo para acotar falsos positivos.

`probe_listener_responsive` es la única excepción nominal y se valida por gramática fail-closed, no por búsqueda de substrings/taint parcial: exactamente una asignación top-level de URL que resuelva a `http://{host}:{int(port)}/status`, sin `?`, `&`, fragment, key/token/identity/lease, mappings, `urlencode`, `Request`, `IfExp` ni `BoolOp`; exactamente un `urlopen(url, timeout=timeout)` y ningún otro sink/cuerpo. Las funciones/clases/lambdas anidadas forman scopes separados y nunca alteran sus bindings; sus propios sinks siguen auditándose fuera de la excepción. Cualquier desviación produce `sensitive_probe`. La detección general de query usa frontera exacta `[?&]key(?:=|&|$)`, por lo que `?keyboard` y `&keynote` son negativos. Fixtures obligatorios: base relativa/absoluta/qualified importada, factory `IfExp`/`BoolOp`, alias ligado heredado, query condicional, mapping mutable, `dict(key=...)`, nested-scope negativo y los dos nombres de parámetro parecidos.

**Step 3a.5 — Carriers y scopes Python restantes `[DESIGN]`:** la forma HTTP conservadora incluye cualquier `ast.Starred` y cualquier keyword con `arg is None` (`**kwargs`), además de method/url explícitos. Los aliases mediante `NamedExpr` y targets de comprehension deben conservar el sink `.request`; fixtures no tautológicos cubren direct, bound, imported/reexport y `getattr` con `*args`, `**kwargs`, walrus y list/generator comprehension. El negativo de un argumento no HTTP sigue sin finding.

El texto/query sigue `Starred` y `NamedExpr` por su value; `AugAssign` sobre Name y Subscript `['key']` conserva taint. Los fixtures del canonical function y del probe usan la firma real y perturban sólo AugAssign/NamedExpr, de modo que el finding no pueda proceder de un rechazo anterior de firma. La gramática nominal exige `FunctionDef` síncrono, sin defaults posicionales, y defaults keyword exactos `timeout=1.0`, `host='127.0.0.1'`; async o host remoto falla cerrado. Al entrar en FunctionDef/AsyncFunctionDef/Lambda se eliminan aliases **y text bindings** de todos sus argumentos. Los targets de comprehension tienen scope propio Python 3: no borran aliases del scope exterior y sí sombrean dentro; añadir positivos/negativos antes, dentro y después de list/set/dict/generator comprehension.

**Step 3a.6 — Merge de ramas y walrus de comprehension `[DESIGN]`:** `If` se visita desde un snapshot común y hace merge conservador de alias/text de body y else —un marker HTTP o query presente en cualquier rama sobrevive; un else ausente equivale al estado previo—, evitando que una asignación segura de una rama no tomada borre el sink real. Un `NamedExpr` dentro de list/set/dict/generator comprehension liga su target al scope contenedor según Python 3; al cerrar el scope temporal, exportar conservadoramente los markers/text de esos targets al exterior. No modelar CFG completo.

Los tests no pueden aprobar por conteos compensados: una función por carrier o assertions exactos de `relative_path/function/line/kind`. El AugAssign mapping usa un valor inicial existente (`{'key': ''}`) para no lanzar `KeyError`; percent-encoded key se prueba en un módulo general con firma válida y se exige finding `authenticated_query`, no mediante rechazo previo del probe. Residuales como tuple-unpack/global/nonlocal/decorators/default-expressions quedan backlog salvo que aparezcan en la clausura productiva actual.

**Gate antes de GREEN:** V25f–V25l deben estar implementados y verdes; el focal debe incluir explícitamente admin, daemon security y runtime audit. Esta corrección sustituye la frase imposible “todo negativo = cero connect” y no cambia endpoint, payload ni persistencia.

**Step 4 — V24 integración:** barrera con N procesos `ClientRuntime`, incluyendo un fixture que conserva el `_ensure_daemon` pre-R7, observando cero listener. Puede haber N `spawn_fn`, pero exactamente un candidato gana elección, atraviesa migration/bind/activation y publica una generación; perdedores salen y todos los clientes conectan. Repetir con launch directo, dos puertos/mismo root, y daemon mixed-version no participante. Matar solo el **fixture wrapper propio** del ganador en cada frontera pre/post-migration/bind/activation: lock OS se libera y una segunda ola debe publicar una generación usando recovery R13 y compatibilidad de artifacts R9. Archivo de lock residual, PID reuse y daemon externo entre elección/bind no crean autoridad doble. Embedded/bootstrap siguen bloqueando. Cero terminaciones de procesos reales.

**Step 5 — vivo:** con los clientes host reales existentes, ejecutar el gate canónico solo cuando no haya listener. Dejar que el supervisor normal arranque la nueva generación; `session_status` y doctor deben ser legibles y limpios. No matar clientes ni daemon para fabricar el resultado.

### Task 6: Migrar protocolo y evitar que P0.S copie el bug

**Files:**

- Modify: `DayZ_MCP_dev/tools/README-mcp.md`
- Modify: `C:/Users/guill/ObsidianVault/AI/20_Runbooks/dayz-mcp-agent-session-protocol.md`
- Modify: las skills/instrucciones locales que actualmente ordenan el loop manual, solo si el grep confirma la referencia exacta
- Modify: `DayZ_MCP_dev/plans/2026-07-21-p0s-native-lifecycle-security-plan.md`
- Modify: `DayZ_MCP_dev/tools/tests/test_task9_protocol_docs.py`

**Step 1 — RED docs:** exigir que el entrypoint recomendado sea `session_acquire_wait`, que `session_acquire/session_wait` se etiqueten low-level y que cancel/timeout/release/status estén documentados.

**Step 2 — GREEN docs:** sustituir el loop manual recomendado por una llamada de alto nivel. Mantener preparación-before-acquire, heartbeat durante trabajo real, release temprano y status antes de handoff.

**Step 3 — P0.S:** en §8, añadir `session_cancel` a la paridad de primitivas y declarar que el `ControlClient` extrae la semántica claim-on-live-wait ya corregida. No implementar `ControlClient` en BUG-046.

**Step 3b — P0.S/H10:** corregir §8 y §9.3 para que ControlClient y el controller Utopia acrediten el owner del socket antes de status autenticado; no copiar el orden actual status→probe.

**Step 4 — Límites explícitos:** documentar “espera larga ligada a una request/host vivos”. No sobrevive cierre/restart/desconexión ni reanuda razonamiento. MCP Tasks/operación durable allowlisted queda fuera hasta capability negociada y spec separada.

### Task 6A: Implementar el launcher protocol-compliant de `dayz-test.ps1`

**Files:**

- Create: `DayZ_MCP_dev/tools/dayz_mcp/secure_launcher.py`
- Create: `DayZ_MCP_dev/tools/approved-launchers.json`
- Create: `DayZ_MCP_dev/tools/tests/test_secure_launcher.py`
- Modify: `DayZ_MCP_dev/tools/tests/test_task9_launcher_migration.py`
- Modify: `LF_VStorage_dev/tools/dayz-test.ps1`
- Modify: `DayZ_MCP_dev/tools/README-mcp.md`
- Modify: `C:/Users/guill/ObsidianVault/AI/20_Runbooks/dayz-mcp-agent-session-protocol.md`
- Modify: la skill canónica `dayz-test-ingame` solo si su `SKILL.md`/template confirma que ella es la fuente de launchers nuevos

**Step 0 — Preflight multi-proyecto/skill:** leer `LF_VStorage_dev/CLAUDE.md`, memoria mínima LF_VStorage, `skill-conventions/SKILL.md` y la skill `dayz-test-ingame` completa antes de decidir si se toca su template. Si el template no es fuente canónica vigente, no editarlo.

**Step 1 — RED V17/V17b/V18:** tests del schema cerrado root+identity+relative path+SHA y construcción de argv. La ruta real OneDrive debe pasar. Rechazar ID ausente/duplicado, root/path escape, homónimo, hash drift, archivo final reparse, cualquier componente name-surrogate, root identity drift y swap entre validación/open/pre-spawn. Exigir `shell=False`, `-NoProfile`, `-NonInteractive`, `-File`, ausencia de Bypass y argumentos exactos.

**Step 2 — GREEN parser/handle con fixtures:** implementar parser fail-closed sin crear todavía la entrada real. `realpath(strict=True)` debe coincidir; inspeccionar cada componente con `stat(..., follow_symlinks=False)` y rechazar `st_reparse_tag & 0x20000000`, no el atributo ReparsePoint genérico. Abrir script vía `CreateFileW` con `FILE_SHARE_READ` solamente, validar `FILE_ID_INFO`, hashear el mismo handle y mantenerlo hasta fin del hijo. Tests Windows en temp demuestran que lectura paralela funciona y replace/delete/retarget fallan mientras el handle vive.

**Step 3 — RED V19 consumidor:** extender tests de launcher migration: PowerShell captura credenciales al inicio de un run lifecycle, las elimina de Process env antes de build, fake AddonBuilder no las ve, fake lifecycle sí las ve temporalmente, y finally las elimina/restaura aun con error.

**Step 4 — GREEN consumidor:** modificar solo `LF_VStorage_dev/tools/dayz-test.ps1`: `Initialize-LifecycleCredentials` guarda en variables script-scope sin log y limpia Process env antes de `Invoke-Build`; `Invoke-LifecycleCli` repone alrededor del Python exacto y limpia/restaura en finally. Preflight read-only no exige credenciales; Kill inicializa antes de lifecycle.

**Step 5 — GREEN registro final, después del consumidor:** volver a ejecutar tests del consumidor; solo con el archivo definitivo calcular root `FILE_ID_INFO` y SHA desde handles, crear la única entrada `lf_vstorage_dayz_test` y ejecutar V17 positivo sobre esa entrada. Luego mutar una copia/fixture para demostrar hash drift negativo. Ningún paso posterior modifica el script sin regenerar y revalidar el registro.

**Step 6 — RED V14:** fake `ClientRuntime` queued→active y fake process. Exigir cero secretos en argv/output/evidencia y presencia exacta solo en el env inicial de PowerShell, que el consumidor va a capturar/limpiar.

**Step 7 — GREEN acquire/inject:** reutilizar `ClientRuntime.session_acquire_wait`; leer token/identidad desde memoria bajo lock; copiar env, insertar variables, crear hijo y retirar referencias del dict en finally. Revalidar root/file identity contra los handles fijados inmediatamente antes de spawn; si falla después de acquire, release/status sin spawn.

**Step 8 — RED/GREEN V15/V21:** stdout y stderr concurrentes mayores al pipe buffer, secretos fragmentados entre chunks. Dos drain tasks, redactor incremental con tail máximo dependiente de la longitud del secreto, escritura por chunks acotados y ninguna credencial en excepciones.

**Step 9 — RED/GREEN V16:** exit 0/no-cero, spawn fail, Ctrl-C/SIGTERM soportado y cancel async. Pedir terminación solo al wrapper PowerShell directo y esperar acotado; nunca matar DayZ/nombre/PID descendiente. Aunque el wrapper persista, release invalida token y status declara `launcher_child_still_running`. Un kill duro del proceso Python no garantiza finally y queda como riesgo TTL, no como PASS.

**Step 10 — Heartbeat/finally:** tarea periódica durante el hijo, detenida/esperada antes de release. En todos los caminos con lease: detener heartbeat, release, status y cerrar handles en último lugar. Heartbeat/release/status fallidos marcan degradación y evitan PASS.

**Step 11 — CLI/docs:** documentar launcher como única ruta agent-safe del consumidor registrado. Prohibir copiar token desde MCP a `$env:` manual. Añadir ejemplo sin secretos y proceso separado de registro de futuros consumidores.

**Step 12 — Gate vivo externo:** comprobar policy/AuthentiCode. Sin autorización `RemoteSigned`, no invocar `.ps1` y mantener `BLOCKED_EXTERNAL_POWERSHELL_POLICY`. Con autorización, aplicar cambio reversible documentado, probar preflight inocuo, luego gate real. Nunca Bypass.

### Task 7: E2E offline, métricas y regresión completa

**Files:**

- Modify: `DayZ_MCP_dev/tools/tests/test_session_e2e.py`
- Modify or create focused harness under `DayZ_MCP_dev/tools/_session_coordination/` only si los tests multiproceso existentes no pueden expresar V12
- Create: `DayZ_MCP_dev/reviews/2026-07-22-bug046-implementation-self-review.md`

**Step 1 — RED/GREEN V12:** proceso A adquiere; proceso B obtiene ticket y desaparece; proceso C espera. A libera. Verificar que B nunca genera grant, B expira/cancela y C adquiere desde su wait vivo.

**Step 1b — RED/GREEN V22/V22b/V22c integrado:** procesos mueren en cada frontera WAL/completed y cada append/repair_phase. Reiniciar sobre runtime temp; aplicar tabla snapshot/marker, fault/status/doctor y bloqueo. Admin repair con backend recuperado produce un solo event id por fase antes del primer grant nuevo.

**Step 2 — Métrica:** parsear audit JSONL del fixture y calcular:

- grants FIFO totales;
- grants sin `source=live_wait`/`wait_request_id` causal o con commit duplicado;
- cancelaciones por client_cancel y ticket_ttl;
- tiempo queued por ticket.

El criterio offline es `fifo_grants_without_live_wait=0`; la entrega de red posterior al commit no puede garantizarse matemáticamente y se cubre con cancel/TTL.

**Step 3 — Suite focal:** ejecutar todos los módulos de coordinación, HTTP, client-mode, protocol, authority, acquire-wait y host-timeouts.

**Step 4 — Suite global:** comparar contra `bug046-baseline.json`: mismo/ mayor discovery válido, ningún ID/fingerprint nuevo; desaparición de un fallo no permite reemplazarlo por otro. Si hashes o baseline derivan, parar por drift externo.

**Step 5 — Revisión independiente del diff:** revisar autoridad, carreras, auditoría, cleanup, secretos, schema y refactor incidental. Cada hallazgo incluye archivo:línea, severidad concreta y fix.

### Task 8: Gates reales disponibles, gates externos y cierre honesto

**Precondition:** tests offline verdes, config nueva efectiva en sesiones nuevas, doctor limpio y ningún proceso retail requiere intervención manual.

**Step 1 — Preflight live:** `session_status`; adquirir mediante `session_acquire_wait` solo al iniciar la secuencia exclusiva; no heartbeat mientras no haya trabajo.

**Step 2 — Gate local acreditable:** ejecutar el harness multiproceso con identidades/PID/generación reales y sin auto-label de plataforma. Esto valida liveness/autoridad local, no H8 mixto.

**Step 3 — Gate mixto externo:** dos Claude + dos Codex solo si usuario/Claude aportan sesiones reales. Evidencia mínima: host CLI/proceso verificado externamente, PID, session_id y misma daemon_generation. Sin ellas, mantener H1-mixto/H9-mixto abiertos; nunca usar cuatro labels locales como sustitución nueva.

**Step 4 — Aserciones/cierre:** FIFO, una mutación máxima, cero grant sin live wait, cancelación limpia y doctor/status final. Actualizar H9 solo con gates realmente ejecutados; conservar blockers PowerShell/Claude por nombre. Actualizar BUG-046, validation-matrix, decision-log, research y `AI/30_Sessions`.

## 7. Comandos de validación

[DESIGN]
```powershell
& '.\.venv-mcp\Scripts\python.exe' -m unittest tests.test_session_coordination tests.test_session_http tests.test_client_mode tests.test_runtime_state tests.test_doctor tests.test_lifecycle_http tests.test_admin_cli tests.test_daemon_security_gate tests.test_security_runtime_audit tests.test_task9_protocol_docs tests.test_task7_final_authority_regressions tests.test_bug046_audit_fault_recovery tests.test_bug046_lease_queue_liveness tests.test_session_acquire_wait tests.test_mcp_host_timeouts tests.test_secure_launcher
```

[EXACT]
```powershell
& '.\.venv-mcp\Scripts\python.exe' -m unittest discover -s tests -p 'test_*.py'
```

[EXACT]
```powershell
& '.\.venv-mcp\Scripts\python.exe' -m dayz_mcp.doctor --json --require-clean
```

No ejecutar scripts `.ps1` mediante `powershell -ExecutionPolicy Bypass`; la policy efectiva es Restricted. La configuración de hosts usa `python install_mcp.py --register`. El gate de `dayz-test.ps1` espera autorización `RemoteSigned` o permanece bloqueado externamente.

## 8. Criterio de luz verde

El plan puede pasar a implementación solo si la revisión adversarial confirma:

1. cobertura explícita de H4/H8/H9;
2. ausencia de grant desde release/expiry/status;
3. linearización única del claim dentro de wait;
4. cancelación fail-closed y segura frente a la carrera de transporte;
5. estrategia de timeout aceptada por ambos schemas locales y plan transaccional; smoke real queda gate post-config;
6. compatibilidad/rollback de audit, WAL/fault, config de hosts y registro de launcher;
7. viability tests suficientes para carreras, auditoría y abandono;
8. no introducción de un job runner arbitrario;
9. alcance trazable a BUG-046 sin refactor incidental.
10. launcher registrado root+identidad+path+SHA y fijado por handle, env lifecycle temporal, drains concurrentes, sin secreto en argv/log/transcript, `shell=False` y cleanup en `finally`;
11. PowerShell live y 2+2 identificados como gates externos que no impiden implementar offline pero sí impiden declarar cierre total.
12. ruta OneDrive real positiva, rechazo limitado a name-surrogates y archivo fijado por handle hasta terminar el hijo;
13. WAL normal pasa por completed; fault de auditoría/compensación queda en status/doctor tras restart y solo admin repair exacto lo resuelve;
14. cleanup preserva la excepción primaria; config usa handles share=0 + journal y no falso CAS por rename;
15. operation id previo, always-ticket high-level y tombstone cubren cancel-before-arrival, respuesta perdida y audit gate busy;
16. admin repair usa event IDs/write-once/fases y la tabla snapshot/marker no infiere limpio ante divergencia.

Si aparece un hallazgo crítico/alto, corregir este documento y repetir la revisión con el mismo subagente. Solo una conclusión explícita `GREEN` autoriza Task 1 de implementación.
