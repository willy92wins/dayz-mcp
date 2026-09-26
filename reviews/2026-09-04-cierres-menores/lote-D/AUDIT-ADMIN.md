# Auditoría R9 — lote D: admin / reboot / recovery / máquina de estados

## VEREDICTO

CRITICAL=0 · MAJOR=4 · BACKLOG=0

El cambio cierra el fallo original de lanzar sobre un puerto UDP ocupado, pero no está listo para promoción sin corrección. Hay dos atascos operativos reproducibles, el error `port_scan_unknown` se oculta al consumidor y existe un fail-open cuando la tabla de sockets es legible pero no se puede atribuir el nombre de un PID.

Identidad de los bytes auditados: el árbol vivo terminó exactamente en los hashes destino de `DIFF-D.patch` para los siete ficheros del lote (`8362981`, `dd2afa0`, `aada4d1`, `72edb02`, `ad54b41`, `3e3cb3f`, `76400b2`). El `HEAD` observado fue `cdae73248d0d18b07fe0154b1fb916dce7b271ca`, no el `7670703` citado por `COMUN.txt`; por ello el dictamen acredita los bytes destino del patch, no la identidad del commit base.

Verificación focal ejecutada desde `tools/` con el intérprete obligatorio:

```text
[EXACT] $env:PYTHONPATH='.'; $env:PYTHONDONTWRITEBYTECODE='1';
& '.\.venv-mcp\Scripts\python.exe' -m unittest -v \
  tests.test_box_port_occupancy tests.test_orphan_guard_udp \
  tests.test_process_lifecycle tests.test_daemon tests.test_lifecycle_cli \
  tests.test_box_occupancy tests.test_server_response_truth

Ran 379 tests in 10.418s
OK
```

Esto prueba la regresión cubierta por el lote; no refuta los cuatro hallazgos, porque ninguno de los tests existentes ejerce las transiciones cruzadas descritas abajo.

## B-1 — Reboot con run vivo y salida del operador

### Camino normal: no queda bloqueado

Un run `RUNNING` recuperado tras reiniciar el daemon conserva sus `ProcessRecord` y pasa a `RUNNING_IDLE`; un `STARTING`/`STOPPING` conserva los records y pasa a `UNRECONCILED` (`tools/dayz_mcp/process_lifecycle.py:915-947`). Todos esos estados siguen activos (`_ACTIVE_STATES = RUN_STATES - {"EXITED"}` en `process_lifecycle.py:32-39`), por lo que sus PIDs entran en `registered_pids` (`process_lifecycle.py:3342-3346`) y el nuevo testigo no los clasifica como foreign.

`recover_unacknowledged_before_listen` llama a `begin_release_owner` para lanzamientos sin ACK (`tools/dayz_mcp/daemon.py:111-147`). Si la identidad sigue siendo exacta, ese camino termina los `ProcessRecord` registrados; si falla identidad o terminación, `_persist_unreconciled_and_arm` conserva los records en `UNRECONCILED` y arma recovery fault (`process_lifecycle.py:2686-2815`, especialmente `2741-2773` y `2817-2829`). La reparación TTY `lifecycle-recovery-repair` existe en `admin_cli.py:54-57,130-164` y vuelve a intentar el cleanup mediante `repair_recovery_fault` (`process_lifecycle.py:2840-2920`).

Para un `UNRECONCILED` que retuvo un proceso vivo y cuya sonda diag coincide, sí hay vía sin matar a mano:

1. `admin_reconcile(run_id, pid)` lo pasa a `RUNNING_IDLE` (`process_lifecycle.py:3535-3640`).
2. `lifecycle_cli adopt` lo pasa a `RUNNING` (`lifecycle_cli.py:63-66,138-140`; `process_lifecycle.py:2520-2605`).
3. `lifecycle_cli stop` termina por el guard y lo retira (`lifecycle_cli.py:61-66,138-140`; `process_lifecycle.py:2255-2518`).

Salida literal recortada del fake ejecutado:

```text
[EXACT] B1_UNRECONCILED_ROUTE={
  "reconcile":{"reconciled":true,"state":"RUNNING_IDLE"},
  "adopt":{"ok":true,"state":"RUNNING"},
  "stop":{"ok":true,"state":"EXITED","terminated":1},
  "terminated_pids":[4242]
}
```

### Camino excepcional: `EXITED` o proceso no registrado vivo

`RunRecord.validate` prohíbe que un `EXITED` durable conserve processes (`process_lifecycle.py:498-553`). Por tanto, si una terminación confirma falsamente el cierre, o si sobrevive un hijo no registrado, el holder ya no pertenece a ningún run activo y el nuevo sondeo lo convierte en foreign (`process_lifecycle.py:3261-3285,3342-3438`).

En ese estado no existe salida gestionada:

- `reap_dead_run` sólo admite `RUNNING`, `RUNNING_IDLE` o `UNRECONCILED` (`process_lifecycle.py:39,3118-3149`).
- `adopt_run` sólo admite `RUNNING_IDLE` (`process_lifecycle.py:2572-2578`).
- `stop_run` exige un run `RUNNING` adoptado, salvo el caso especial “never started” sin procesos (`process_lifecycle.py:2276-2288`).
- `admin_reconcile` rechaza `EXITED` como `run_not_reconcilable` (`process_lifecycle.py:3568-3578`).
- `lifecycle_cli` sólo ofrece `start|stop|adopt|reap|status`; no ofrece adopción/terminación por puerto (`lifecycle_cli.py:53-68`).
- El estado público foreign omite el PID, de modo que ni siquiera entrega una identidad accionable (`process_lifecycle.py:3389-3395,3421-3430`).

Reproducción ejecutada con un `RunRecord(EXITED, processes=[])` y un holder `DayZServer_x64.exe` en 2302:

```text
[EXACT] B1_EXITED_NO_ROUTE={
  "box":{"occupied":true,"runs":[],"foreign":[{"image":"DayZServer_x64.exe","port":2302,"source":"port"}]},
  "results":{
    "reap":{"error":"run_not_reapable"},
    "adopt":{"error":"run_not_adoptable"},
    "stop":{"error":"run_not_adopted"},
    "admin_empty":{"error":"run_not_reconcilable"},
    "start":{"error":"active_run_exists"}
  },
  "terminate_calls":0
}
```

Conclusión B-1: el reboot normal conserva una salida. El estado `EXITED` + holder vivo, y el hijo vivo que queda fuera del manifiesto, dejan la caja correctamente ocupada pero sin recuperación MCP/CLI; antes el proceso era invisible y se lanzaba encima. Es una regresión operativa MAJOR, no CRITICAL: se reprodujo la máquina de estados con fakes, pero no se arrancó un proceso DayZ real por la frontera del lote.

## B-2 — `admin_reconcile(..., empty=True)` no consulta el nuevo testigo

Hoy `empty=True` exige que `run.processes` ya esté vacío y llama dos veces a `_diag_snapshot_empty()` (`process_lifecycle.py:3579-3585,3616-3619`). Esa sonda sólo ve lo que el daemon cablea como `diag_probe`, exactamente `snapshot_processes_by_name(["DayZDiag_x64.exe"])` (`daemon.py:519-531`); `_diag_snapshot_empty` únicamente comprueba que esa lista esté vacía (`process_lifecycle.py:3509-3523`). No consulta `_port_holders()`.

El CLI valida sólo estado y lista `processes`, y confirma con `FORCE <run> EMPTY` (`admin_cli.py:165-220`). Por tanto, un `DayZServer_x64.exe` vivo que sostiene un UDP pasa el gate `empty`.

Reproducción ejecutada:

```text
[EXACT] B2_ADMIN_EMPTY={
  "before":{"occupied":true,"runs":[{"state":"UNRECONCILED"}],"foreign":[{"image":"DayZServer_x64.exe","port":2402}]},
  "admin_result":{"empty":true,"reconciled":true,"state":"EXITED"},
  "durable_state":"EXITED",
  "after":{"occupied":true,"runs":[],"foreign":[{"image":"DayZServer_x64.exe","port":2402}]}
}
```

También acepta `empty` cuando la tabla de sockets es desconocida:

```text
[EXACT] before.port_scan_known=false, before.occupied=true
admin_result={"empty":true,"reconciled":true,"state":"EXITED"}
after.port_scan_known=false, after.occupied=true, durable_state="EXITED"
```

El manifiesto no queda corrupto, pero la operación certifica “EMPTY”, retira el único handle administrativo y deja la caja ocupada por un proceso que ya no puede gestionarse. Es MAJOR.

## B-3 — Proceso propio muerto, puerto aún ocupado e hijos no registrados

El sondeo nuevo enumera sockets **UDP** mediante `psutil.net_connections(kind="udp")` o `netstat -ano -p UDP` (`orphan_guard.py:435-468`). UDP no tiene estado `TIME_WAIT`; un `TIME_WAIT` TCP no entra en esta sonda. Una reserva de rango del sistema sin socket/holder tampoco aparecerá aquí. El caso relevante para este cambio es un socket todavía abierto por otro PID.

Sólo se registra el PID devuelto por `self.launcher(...)`: se lee `launched.pid`, se construye un único `ProcessRecord` y se añade a `completed.processes` (`process_lifecycle.py:2020-2059`). No hay censo de descendientes ni registro de BattlEye/crash handlers. El reaper clasifica únicamente `run.processes` (`process_lifecycle.py:3035-3049`). Por ello, un hijo que herede el socket queda fuera de `registered_pids` y `_foreign_port_reason` lo rechaza (`process_lifecycle.py:3277-3285`).

Reproducción con padre registrado 4242 muerto y un crash handler 9999 sosteniendo el puerto pedido 2302:

```text
[EXACT] B3_CHILD_HOLDER={
  "reap":{"ok":true,"state":"EXITED"},
  "box_after_reap":{"occupied":false,"foreign":[],"ports_in_use":[]},
  "start":{"error":"active_run_exists"},
  "start_audit_reason":"port_in_use_foreign",
  "terminate_calls":0
}
```

La aparente contradicción es real: si el hijo no tiene nombre DayZ, `box_occupancy` lo ignora porque no conoce el puerto solicitado por la futura llamada (`process_lifecycle.py:3410-3419`); `wait_for_box_s` puede reclamar una caja aparentemente libre y el sondeo fresco de `start_run` la rechaza después. Si el hijo se llama `DayZ_BE.exe`, sí entra como foreign DayZ y `execute_wait_for_box` espera hasta el deadline porque sólo avanza con `occupied=False` (`server.py:2777-2836`).

El rechazo es correcto y fail-closed. El defecto es operativo: al ser descendiente de un run gestionado pero no estar registrado, cae en el mismo callejón sin salida de B-1. No se cuenta aparte de H-01.

## B-4 — Orden de `start_run`, reserva y proyección del error

La reserva queda coherente. El orden real contiene un primer gate retail en `process_lifecycle.py:1813-1814`; después de validar manifiesto/claim calcula `registered_pids`, ejecuta `_foreign_diag_reason`, luego `_foreign_port_reason`, vuelve a comprobar retail y sólo después audita/commitea (`process_lifecycle.py:1902-1935`).

Ambos motivos nuevos pasan por `_start_rejection` como `audit_reason`, pero el wire sigue siendo `active_run_exists` (`process_lifecycle.py:1905-1922`). `_start_rejection`:

1. escribe `lifecycle_start_rejected` con `port_in_use_foreign` o `port_scan_unknown`;
2. si el audit falla, igualmente llama a `reject_reservation`;
3. si el audit pasa, llama a `_reject_reserved`, que consume la reserva mediante `SessionCoordinator.reject_reservation` (`process_lifecycle.py:1472-1485,1585-1606`; `session_coordination.py:1413-1476`).

El repro lanzó dos rechazos seguidos y midió `pending_authorizations_after_two_rejects=0`; no hay lease/reservation atascado.

El problema está en el consumidor. `_failed_active_run_result` sólo llama a `occupancy_error_fields(box)` y no lee el audit (`server.py:2730-2752`). `occupancy_error_fields` no copia `port_scan_known` ni proyecta un reason; cuando `occupied=True` sin run/foreign pone `foreign=True` y conserva el hint genérico (`process_lifecycle.py:373-453`). Resultado literal:

```text
[EXACT] B45_PORT_SCAN_UNKNOWN={
  "box":{"occupied":true,"scan_known":true,"port_scan_known":false,"runs":[],"foreign":[]},
  "audit_reasons":["port_scan_unknown","port_scan_unknown"],
  "dayz_test_run_projection":{
    "error_code":"active_run_exists",
    "foreign":true,
    "hint":"retry with wait_for_box_s=<n>",
    "port_scan_known":null
  },
  "pending_authorizations_after_two_rejects":0
}
```

El hint manda esperar ante una dependencia persistente que esperar no repara. Es MAJOR.

Texto propuesto:

> `UDP port scan unavailable; waiting will not free the box. Restore psutil/netstat socket visibility (or restart/repair the daemon), confirm session_status.box.port_scan_known=true, then retry. Do not launch DayZ manually.`

## B-5 — Recovery fault, retail quarantine y visibilidad

`port_scan_unknown` no arma `lifecycle-recovery-faults`: sus únicas apariciones de producto están en `_port_holders`/`_foreign_port_reason`; los armados de recovery fault nacen de cleanup/recovery (`process_lifecycle.py:2731,2817-2837`; cable en `daemon.py:477-538`). El rechazo ocurre antes de crear/commitear un `RunRecord`, por lo que no hay estado durable que una reparación de manifest pueda limpiar.

Con retail presente gana el primer gate `_quarantined()` de `start_run` (`process_lifecycle.py:1813-1814`); sin retail, el fallo del puerto gana antes de los rechecks retail y `_commit_reserved` (`process_lifecycle.py:1913-1935`). Son frenos independientes.

Cuando la sonda de sockets falla, `_collect_probes` devuelve `port_scan_known=False` (`process_lifecycle.py:3401-3408`) y `_derive_box` publica `occupied=True` más ese campo (`process_lifecycle.py:315-346`). `_box_payload` conserva el dict devuelto por lifecycle (`loopback.py:246-263`) y `/session/status` lo adjunta como `box` (`loopback.py:3306-3325`); la tool `session_status` devuelve ese status (`server.py:3049-3054`). Por tanto, **sí** llega al cliente de `session_status`.

No llega a `dayz_test_run`: B-4 demuestra que la proyección elimina el campo y la causa. Además `_session_status_blocked_on` recomienda entrar en la FIFO siempre que `occupied=True`, sin excepción para `port_scan_known=False` (`server.py:2848-2862`). Una avería persistente de psutil+netstat deja la caja fail-closed indefinidamente —comportamiento de seguridad correcto— pero la superficie principal lo describe como otro run/foreign y prescribe una espera inútil. Es el mismo MAJOR H-03, no otro conteo.

Hay además un fail-open distinto: `snapshot_udp_port_holders` declara `known=True` aunque ToolHelp no pueda nombrar los PIDs; en ese caso rellena `name=None` (`orphan_guard.py:471-504`). `_port_holders` acepta ese nombre (`process_lifecycle.py:3251-3258`) y `_foreign_port_reason` permite el lanzamiento si el holder está en otro puerto, porque no puede reconocerlo como DayZ y no coincide con el puerto solicitado (`process_lifecycle.py:3277-3285`). Repro ejecutado con holder 777 sin nombre en 2402 y lanzamiento pedido en 2302:

```text
[EXACT] {"box":{"occupied":false,"port_scan_known":true,"foreign":[]},
 "start":{"ok":true,"run_id":"run-1","state":"RUNNING"},
 "launcher_calls":1,"audit_reasons":[]}
```

Si el PID 777 era realmente `DayZServer_x64.exe` y la atribución falló, se incumple el contrato “una imagen DayZ en cualquier puerto ocupa la caja” sin ninguna señal al cliente. Es MAJOR.

## HALLAZGOS

### H-01 — MAJOR — holder vivo fuera del manifiesto no tiene recuperación gestionada

- Secuencia exacta: proceso padre registrado muere → hijo/no registrado conserva UDP → reaper retira el run a `EXITED` → holder pasa a foreign → `start` rechaza → `reap`, `adopt`, `stop` y `admin_reconcile` rechazan → cero llamadas a `guard.terminate`.
- Repro: `[EXACT]` `B1_EXITED_NO_ROUTE` y `B3_CHILD_HOLDER`, salidas literales en B-1/B-3.
- Impacto: caja bloqueada hasta que el proceso salga solo o el operador lo mate por fuera del lifecycle. Antes del lote el holder podía ser invisible; ahora el lanzamiento peligroso se evita, pero no existe salida segura soportada.
- Fix propuesto `[DESIGN]`: conservar una entidad durable `ORPHAN_HOLDER` ligada al run cuando el holder sea descendiente verificable, o añadir un verbo **TTY-only** de reconciliación por puerto que: resuelva PID fresco dentro del daemon, capture identidad completa, exija confirmación exacta `FORCE PORT <port> PID <pid>`, revalide socket+identidad inmediatamente antes de actuar, permita sólo imagen DayZ o descendencia demostrada, audite antes de terminar y jamás acepte PID/argv arbitrario desde MCP.

### H-02 — MAJOR — `admin_reconcile --empty` certifica vacío ignorando el testigo UDP

- Secuencia exacta: run `UNRECONCILED` con `processes=[]` → DayZServer ajeno sostiene 2402 (o port scan es unknown) → diag-only empty pasa dos veces → admin persiste `EXITED` → `box.occupied` sigue true y ya no hay run reconciliable.
- Repro: `[EXACT]` `B2_ADMIN_EMPTY`, incluida variante `port_scan_known=false`.
- Impacto: estado administrativo contradictorio y pérdida del último handle de recuperación del run; converge en H-01.
- Fix propuesto `[DESIGN]`: sustituir el gate por una comprobación conjunta, dos veces (antes y después del audit), que exija diag conocido-vacío **y** port scan conocido sin holder DayZ. Un scan desconocido debe responder `port_scan_unknown`; un holder DayZ debe responder `manual_cleanup_required` con port/image redaccionados. Añadir fixtures para `DayZServer_x64.exe`, `DayZ_BE.exe`, `pid=None`, port probe exception y la carrera entre los dos snapshots.

### H-03 — MAJOR — `port_scan_unknown` se oculta y el cliente prescribe una espera que no puede arreglarlo

- Secuencia exacta: psutil/netstat fallan → status calcula `occupied=true, port_scan_known=false` → `start_run` audita `port_scan_unknown` y devuelve wire `active_run_exists` → `_failed_active_run_result` pierde flag/reason → hint `retry with wait_for_box_s=<n>` → cada espera agota su timeout mientras persiste la avería.
- Repro: `[EXACT]` `B45_PORT_SCAN_UNKNOWN`; dos rechazos, dos audit reasons correctos y cero reservas pendientes.
- Impacto: caja “ocupada para siempre” de forma opaca; el operador recibe la receta equivocada y no sabe que debe reparar visibilidad de sockets.
- Fix propuesto `[DESIGN]`: hacer que `occupancy_error_fields` proyecte `port_scan_known`; si es false, fijar `reason="port_scan_unknown"`, `foreign=false` y el texto propuesto en B-4. `_failed_active_run_result`, `_enrich_active_run_result` y `_session_status_blocked_on` deben conservar esa discriminación. El wait puede fallar inmediatamente con ese diagnóstico, porque la FIFO no remedia una sonda rota.

### H-04 — MAJOR — fallo de atribución de nombre se publica como scan conocido y deja pasar otro DayZ

- Secuencia exacta: tabla UDP responde `(2402, pid=777)` → snapshot ToolHelp no devuelve ese PID → `snapshot_udp_port_holders` emite `known=true,name=null` → box se declara libre → lanzamiento en 2302 no coincide por puerto y el nombre no prueba DayZ → launcher llamado.
- Repro: `[EXACT]` salida literal al final de B-5 (`launcher_calls=1`).
- Impacto: recrea el fallo de admisión que motivó el lote cuando la fuente de sockets funciona pero la atribución de imagen no.
- Fix propuesto `[DESIGN]`: separar `socket_scan_known` de `pid_names_known`. Para todo PID holder no registrado y sin nombre, reintentar resolución individual y volver a comprobar que el socket sigue ligado al mismo PID. Si continúa sin atribución, no declarar el box global libre: publicar `port_scan_known=false`/`port_attribution_unknown` y bloquear con diagnóstico explícito. El control negativo debe incluir un servicio UDP no-DayZ correctamente nombrado para no convertir todos los servicios del host en ocupantes.

## LO QUE NO PUDE VERIFICAR

- No ejecuté DayZ, daemon, launcher ni procesos de red, por la frontera expresa de `COMUN.txt`. Los repros son deterministas con fakes sobre las clases reales.
- No verifiqué que BattlEye o un crash handler de esta build hereden realmente el socket UDP. Sí verifiqué qué hace la máquina de estados si ocurre: el hijo no se registra y queda sin salida gestionada.
- No verifiqué puertos excluidos/reservados por Windows sin socket. La implementación sólo enumera sockets UDP; esos casos no producen holder y quedan fuera del nuevo gate.
- `SUITE-D.txt` heredado registra `2595 tests`, 6 failures y 6 skips; no atribuí esos seis rojos al lote. Mi ejecución fresca y acotada fue 379/379.
- Durante la auditoría otro proceso modificó transitoriamente `process_lifecycle.py:3407` (`port_scan_known=True`) y lo restauró. No lo conté como defecto: antes del dictamen, el hash volvió a `72edb021753b8244ee1a8ad905652979eb18c5f9`, exactamente el destino de `DIFF-D.patch`, y los 379 tests pasaron sobre esos bytes.
- No actualicé memoria durable en Obsidian: el destino solicitado es este scratchpad y el vault está fuera de las raíces de escritura autorizadas de la sesión.
