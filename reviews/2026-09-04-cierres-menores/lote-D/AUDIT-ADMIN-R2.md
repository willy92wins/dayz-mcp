# Auditoría R2 — lote D: admin / reinicio / recuperación / máquina de estados

## VEREDICTO

CRITICAL=0 (con repro ejecutable) · MAJOR=1 · BACKLOG=1

H-02, H-03 y H-04 están cerrados en las rutas focales pedidas. El rechazo tardío
pre-launch deja el estado durable y la autoridad limpios, y el único constructor
productivo de `ProcessLifecycle` sí recibe `port_probe`. Persiste un defecto público:
`session_status.blocked_on` todavía prescribe entrar en la FIFO de la caja cuando
`port_scan_known=false`; esa acción contradice el diagnóstico nuevo y es inútil.

El BACKLOG contado es H-01 heredado (reconciliación gestionada por puerto), que este
brief ordena no reabrir como bloqueante.

Identidad observada: rama `work/inbox-20260830-modules`, HEAD
`cdae73248d0d18b07fe0154b1fb916dce7b271ca`. Hashes SHA-256 al cierre de la pasada:

```text
[EXACT] process_lifecycle.py  A548930663BA8F18F1CBB21830FE794B76B60646D8B7FFE3BB77E4F5558D8009
[EXACT] orphan_guard.py       82902F24AD0CBEF99978BB27A0AA66C18A3DDA1708AE0F229A5AE22A7FD8CA31
[EXACT] server.py             CF0F3F620B27F4D932FA221B6DB2CBBAECCAB05F62C99ADDB9A90343229CB355
[EXACT] daemon.py             65AE92E923BEA50BD3E1BB122E7C537EA71B6B8F202551522F7703501D4708CD
```

Verificación focal fresca, dos veces, desde `tools/`, con el intérprete obligatorio,
`PYTHONPATH=.` y `PYTHONDWRITEBYTECODE=1`:

```text
[EXACT] .\.venv-mcp\Scripts\python.exe -m unittest -q \
  tests.test_box_port_occupancy tests.test_orphan_guard_udp \
  tests.test_box_port_wait tests.test_box_occupancy \
  tests.test_process_lifecycle tests.test_daemon tests.test_lifecycle_cli

Ran 398 tests in 6.940s
OK
```

El repro independiente está en `AUDIT-ADMIN-R2-REPRO` =
`audit_admin_r2_repro.py`; se ejecutó con el mismo intérprete y todas sus aserciones
terminaron con RC=0.

## B-1 — H-02: `admin_reconcile(..., empty=True)`

**PASS.** La implementación ya combina el snapshot de procesos con `_port_holders()`:
una tabla desconocida conserva `port_scan_unknown`, una imagen DayZ devuelve
`manual_cleanup_required`, un holder no atribuible devuelve
`port_attribution_unknown`, y sólo holders nombrados no-DayZ acreditan vacío
(`tools/dayz_mcp/process_lifecycle.py:3572-3596`). `admin_reconcile` ejecuta ese gate
dos veces, antes y después del audit (`process_lifecycle.py:3652-3658,3689-3692`), y
sólo después calcula `RUNNING_IDLE`/`EXITED` y persiste la retirada
(`process_lifecycle.py:3704-3729`).

Salida literal resumida del repro público (no llamada directa al helper):

```text
[EXACT] B1_ADMIN_EMPTY.dayz_holder =
  result.error=manual_cleanup_required, durable_state=UNRECONCILED, box_occupied=true
[EXACT] B1_ADMIN_EMPTY.scan_unknown =
  result.error=port_scan_unknown, durable_state=UNRECONCILED, box_occupied=true
[EXACT] B1_ADMIN_EMPTY.attribution_unknown =
  result.error=port_attribution_unknown, durable_state=UNRECONCILED, box_occupied=true
[EXACT] B1_ADMIN_EMPTY.named_service =
  reconciled=true, durable_state=EXITED, box_occupied=false
```

En los tres rechazos el run no pasa a `EXITED`. Con la tabla limpia salvo
`svchost.exe:53`, el comportamiento histórico se conserva y el run sí termina en
`EXITED`.

## B-2 — H-03: diagnóstico por la ruta pública

**PASS parcial; un MAJOR residual en `blocked_on`.** `execute_wait_for_box` inspecciona
el payload de la caja y retorna inmediatamente `error="port_scan_unknown"`, sin dormir,
cuando `port_scan_known=false` (`tools/dayz_mcp/server.py:2823-2868`). La proyección de
un `active_run_exists` conserva `reason` y sustituye el hint por uno que dice
literalmente que esperar no ayuda (`server.py:2715-2753,2774-2798`).

```text
[EXACT] B2_PUBLIC_DIAGNOSIS.wait.error = port_scan_unknown
[EXACT] B2_PUBLIC_DIAGNOSIS.sleep_calls = []
[EXACT] B2_PUBLIC_DIAGNOSIS.client_calls = [{wait:true,ticket:null}]
[EXACT] B2_PUBLIC_DIAGNOSIS.failed_projection =
  {error_code:active_run_exists, reason:port_scan_unknown,
   hint:"... waiting does not help, restore that first"}
```

Sin embargo, `_session_status_blocked_on` sólo mira `occupied=true` y todavía emite:

```text
[EXACT] "DayZ test box; next: call dayz_test_run(..., wait_for_box_s=<n>) to join the box FIFO"
```

El cuerpo que lo produce está en `tools/dayz_mcp/server.py:2904-2917` y el valor se
publica en `session_status` en `server.py:3105-3110`. Se gradúa como MAJOR porque el
campo promete nombrar el **siguiente paso** y manda ejecutar precisamente la operación
que el nuevo contrato declara incapaz de reparar el estado. El daño queda acotado: esa
llamada falla ya en el primer status y no consume los 600 s, pero un agente que gobierna
su siguiente acción por `blocked_on` recibe una instrucción errónea reproducible.

Texto propuesto `[DESIGN]`:

> `UDP port visibility; next: do not wait or launch. Restore psutil/netstat socket visibility (or PID-name attribution for port_attribution_unknown), confirm session_status.box.port_scan_known=true, then retry.`

## B-3 — H-04: atribución fail-closed y reintento ToolHelp

**PASS.** `_foreign_port_reason` rechaza cualquier holder no registrado si su imagen es
DayZ o si ocupa el puerto pedido; si no coincide pero carece de PID/nombre, termina en
`port_attribution_unknown` (`tools/dayz_mcp/process_lifecycle.py:3301-3331`). La misma
ambigüedad hace que la caja publique `port_scan_known=false` y
`port_scan_reason="port_attribution_unknown"`
(`process_lifecycle.py:3447-3495`).

```text
[EXACT] holder (2402,777,None), launch 2302 ->
  error=active_run_exists, audit_reason=port_attribution_unknown, launcher_calls=0
[EXACT] holder (2402,777,"svchost.exe"), launch 2302 ->
  ok=true, state=RUNNING, launcher_calls=1
```

El retry individual está implementado tras el snapshot bulk en
`tools/dayz_mcp/orphan_guard.py:482-522`. El fake hizo que la primera lectura bulk no
contuviera PID 777; se verificó una única llamada `_toolhelp_lookup(777)` y el resultado
final fue:

```text
[EXACT] {known:true, holders:[{port:2302,pid:777,name:"DayZServer_x64.exe"}]}
```

## B-4 — estado durable tras rechazo pre-launch

**PASS.** La secuencia real commitea la autorización y persiste primero el run
provisional `STARTING` (`tools/dayz_mcp/process_lifecycle.py:1947-2002`), prepara el
binding y ejecuta un segundo sondeo fresco; si éste rechaza, audita `stage="pre_launch"`,
retira el minted y entra en `_settle_failed_launch`
(`process_lifecycle.py:2020-2058`).

El asentamiento sin proceso abierto construye un `EXITED` con dueño nulo y procesos
vacíos, lo persiste y sella terminal (`process_lifecycle.py:1747-1810`). El `finally`
consume el comando ya commiteado (`process_lifecycle.py:2132-2133`; mecanismo de
reserva/comando en `process_lifecycle.py:1500-1521`).

Repro con binding y port probe falsos:

```text
[EXACT] result = {error:active_run_exists, run_id:run-1, state:EXITED}
[EXACT] durable = {state:EXITED, owner_session_id:null,
                   owner_lease_id:null, processes:[]}
[EXACT] binding_calls = [prepare(minted-instance), retire_role(launch_failed)]
[EXACT] bindings_live = {}
[EXACT] pending_authorizations = 0
[EXACT] committed_commands = 0
```

Mientras el holder que causó el rechazo sigue vivo, la caja continúa ocupada por diseño.
Al retirarlo del fake y forzar un snapshot nuevo:

```text
[EXACT] box_after_holder_exit =
  {occupied:false,runs:[],foreign:[],ports_in_use:[],port_scan_known:true}
```

Para el reinicio se ejercieron las dos capas. En el mismo proceso,
`recover_unacknowledged_before_listen` ignora explícitamente `EXITED`
(`tools/dayz_mcp/daemon.py:111-147`). Al reconstruir el store como haría el daemon,
`RunManifestStore` poda de forma durable los `EXITED` antes de exponerlo
(`tools/dayz_mcp/process_lifecycle.py:615-639,697-722`). Resultado:

```text
[EXACT] recover_same_process=[]
[EXACT] recover_after_reload=[]
[EXACT] runs.json after reload={"version":1,"runs":[]}
```

No queda caja bloqueada ni autoridad colgada.

## B-5 — constructores productivos de `ProcessLifecycle`

**PASS; H-02 no se reabre por CLI.** Un censo AST de todos los `.py` bajo
`tools/dayz_mcp/` encontró un único constructor productivo: el `bounded_io(ProcessLifecycle,
...)` de `tools/dayz_mcp/daemon.py:519-520`; ese mismo constructor pasa
`port_probe=orphan_guard.snapshot_udp_port_holders` en `daemon.py:531-533`.

`lifecycle_cli.py` no reconstruye un lifecycle: forma una petición HTTP acreditada al
daemon (`tools/dayz_mcp/lifecycle_cli.py:23-50,119-170`). `admin_cli.py` hace lo mismo
(`tools/dayz_mcp/admin_cli.py:14-43`) y el verbo `reconcile` termina en POST
`/admin/reconcile` (`admin_cli.py:165-225`), cuyo handler llama al lifecycle ya montado
por el daemon (`tools/dayz_mcp/loopback.py:3489-3492`). Por tanto, ningún CLI productivo
entra hoy por el comportamiento legado `port_probe=None` documentado en
`process_lifecycle.py:3254-3265`.

## HALLAZGOS

### R2-H-01 — MAJOR — `session_status.blocked_on` prescribe la FIFO ante una avería que la FIFO no repara

- Repro ejecutable: `audit_admin_r2_repro.py`, clave
  `B2_PUBLIC_DIAGNOSIS.blocked_on`; RC=0 y salida literal reproducida en B-2.
- Mecanismo: `tools/dayz_mcp/server.py:2904-2917` no discrimina
  `box.port_scan_known=false`; `server.py:3105-3110` publica el texto como siguiente
  acción.
- Impacto concreto: un consumidor que obedece `blocked_on` llama a
  `dayz_test_run(wait_for_box_s=...)` cuando debe reparar visibilidad/atribución. La
  llamada falla inmediatamente gracias a H-03, así que no hay bloqueo de 600 s ni
  lanzamiento inseguro; sigue siendo una instrucción operativa falsa.
- Fix sugerido `[DESIGN]`: antes de la rama genérica de caja, discriminar
  `port_scan_known is False`, incluir `port_scan_reason` y devolver el texto propuesto
  en B-2. Fixture positivo para `port_scan_unknown`, otro para
  `port_attribution_unknown`, y control negativo con una caja realmente ocupada por un
  run, que debe conservar la receta FIFO.

### H-01 — BACKLOG heredado — no existe verbo gestionado de reconciliación por puerto

No se reabre ni se gradúa como bloqueante en esta ronda, por orden expresa de
`COMUN-R2.txt`. Sigue siendo el backlog documentado por el informe anterior para un
holder vivo fuera del manifiesto.

## LO QUE NO PUDE VERIFICAR

- No ejecuté DayZ, daemon, launcher ni tráfico de red; las fronteras de `COMUN.txt`
  lo prohíben. El reinicio se reprodujo con un `RunManifestStore` nuevo sobre el mismo
  `runs.json` y la función productiva `recover_unacknowledged_before_listen`.
- La suite global fresca no quedó utilizable como gate verde en este sandbox:
  `Ran 2636 tests in 205.362s — FAILED (failures=3, errors=84, skipped=24)`.
  Los errores observados incluyen `psutil.AccessDenied` al inspeccionar procesos host,
  rechazos de `request_path_authority` sobre directorios del sandbox y fixtures de
  captura sin sidecar. Los tres FAIL fueron la elección multiproceso del daemon afectada
  por ese `AccessDenied` y los dos centinelas SHA conocidos de
  `test_task9_spawn_phase_markers`; ninguno pertenece a B-1…B-5. Por eso el veredicto
  funcional se apoya en la suite focal fresca 398/398 y el repro independiente, no en
  una extrapolación del discover global.
- No provoqué una pérdida real de atribución ToolHelp ni reciclaje real de PID en
  Windows; se verificó con el fake pedido y con la ruta productiva real. El test vivo
  de sólo lectura de la tabla UDP incluido en la suite focal sí pasó.
- No pude actualizar memoria durable en Obsidian: el vault es de sólo lectura en esta
  sesión. El artefacto durable autorizado por el encargo es este informe en el
  scratchpad.
- No edité el árbol vivo. El único archivo auxiliar creado fuera del entregable fue el
  repro en este mismo scratchpad.
