# Verificación final — lote D, ronda 3

## VEREDICTO

**BLOQUEANTES=0.**

El lote puede commitearse tal cual. Los tres deltas sometidos a ronda 3 están cerrados: la banda interna es 2302–2999 y la espera ante tabla ilegible retorna inmediatamente; `session_status.blocked_on` prescribe reparar la tabla y no entrar en la FIFO; y `foreign_ports` permite componer correctamente un run activo con un puerto ajeno, incluyendo cualquier puerto válido solicitado. Los dos mutantes exigidos pusieron en rojo sus tests discriminadores y los ficheros fueron restaurados al SHA-256 original.

Identidad verificada: rama `work/inbox-20260830-modules`, `HEAD=cdae73248d0d18b07fe0154b1fb916dce7b271ca`.

## F-1 — Suites

Ejecutadas dos veces desde `tools/`, con el intérprete obligatorio, `PYTHONPATH=.` y `PYTHONDONTWRITEBYTECODE=1`.

**[EXACT — ejecutado]**

```powershell
& '.\.venv-mcp\Scripts\python.exe' -m unittest `
  tests.test_box_port_occupancy tests.test_orphan_guard_udp `
  tests.test_box_port_wait -v
```

Resultado final: `Ran 62 tests in 0.135s` — `OK`, RC=0.

**[EXACT — ejecutado]**

```powershell
& '.\.venv-mcp\Scripts\python.exe' -m unittest `
  tests.test_box_occupancy tests.test_process_lifecycle tests.test_daemon `
  tests.test_session_status_blocked_on tests.test_docs_truth `
  tests.test_mcp_tools tests.test_loopback tests.test_lifecycle_cli -v
```

Resultado final: `Ran 485 tests in 15.360s` — `OK (skipped=3)`, RC=0. Total solicitado: **547 tests ejecutados, 3 skipped, 0 fallos, 0 errores**.

La banda queda implementada literalmente como `range(2302, 3000)`, es decir, 2302–2999 (`tools/dayz_mcp/process_lifecycle.py:50-54`). El test de espera comprueba retorno inmediato, cero sleeps, conservación del ticket y una sola llamada al daemon (`tools/tests/test_box_port_wait.py:17-48`).

## F-2 — ADMIN R2-H-01: `session_status.blocked_on`

**PASS.** Repetí `audit_admin_r2_repro.py`, RC=0. Para `port_scan_known=false`, la salida fue:

**[EXACT — salida observada]**

```text
DayZ test box, port_scan_unknown; next: restore the daemon's view of the host UDP socket table (psutil/netstat, process attribution) -- wait_for_box_s does not help
```

La rama lee `port_scan_reason`, usa `port_scan_unknown` como fallback y retorna la reparación antes de la rama genérica de caja ocupada (`tools/dayz_mcp/server.py:2907-2931`). Los controles fijan además que un run con tabla legible conserva `join the box FIFO` y que una caja libre devuelve `None` (`tools/tests/test_box_port_wait.py:138-160`).

Mutante exigido: retiré temporalmente la rama `port_scan_known is False` de `_session_status_blocked_on` y ejecuté el test discriminador.

**[EXACT — resultado del mutante]**

```text
test_unreadable_table_names_the_repair_not_the_fifo ... FAIL
AssertionError: 'port_scan_unknown' not found in
'DayZ test box; next: call dayz_test_run(..., wait_for_box_s=<n>) to join the box FIFO'
Ran 1 test — FAILED (failures=3), RC=1
```

Los tres fallos son sus subtests para `port_scan_unknown`, `port_attribution_unknown` y razón ausente. SHA mutado: `41FBC42A393E7000BF778D5A9B8E4E6C87ED217D712D9A82BC7C2AF8C5E7809A`. SHA restaurado de `server.py`: `6014232BD85B9B12EF5715A84E8E500B3294FB79D54F8E3679D10DDDCA8EDA28`.

## F-3 — LOSS R2-M-1: diagnóstico compuesto y exclusión de runs gestionados

**PASS.** Reproduje la ruta real `ProcessLifecycle.box_occupancy()` → `_failed_active_run_result(...)` con un run gestionado en 2402, un holder `renamed.exe` en 2302 y request a 2302.

**[EXACT — salida observada]**

```text
COMBINED_BOX.foreign_ports=[2302]
COMBINED_BOX.ports_in_use=[2402,2302]
COMBINED_RESULT.error_code=active_run_exists
COMBINED_RESULT.occupied_by_run_id=run-existing
COMBINED_RESULT.reason=port_in_use_foreign
COMBINED_RESULT.port=2302
COMBINED_RESULT.hint=the box is busy (see occupied_by_run_id) AND port 2302 is held by a process that is not a managed run: after the box frees, pass another port= or wait for that holder to exit; waiting for the box alone does not free the port
```

Con el run gestionado sosteniendo su propio 2302, el resultado fue `occupied_by_run_id=run-existing`, `foreign_ports=[]`, `reason=null` y `port=null`. El mecanismo excluye primero todo PID registrado y solo después incorpora el puerto al conjunto ajeno (`tools/dayz_mcp/process_lifecycle.py:3397-3406,3455-3479`); ordena y deduplica enteros antes de construir `_BoxProbes` (`:3500-3514`) y `_derive_box` publica únicamente esa lista (`:348-360`). No se publican PID, imagen ni ruta en `foreign_ports`.

El diagnóstico consulta `foreign_ports`, no `ports_in_use`, y compone ambos bloqueos cuando `runs` no está vacío (`tools/dayz_mcp/server.py:2715-2757`). Ambos builders públicos aplican esa información (`tools/dayz_mcp/server.py:2760-2801`). Las regresiones directas están en `tools/tests/test_box_port_wait.py:90-124`; la exclusión de puertos del run gestionado, en `tools/tests/test_box_port_occupancy.py:174-186`.

Mutante exigido: añadí temporalmente a `foreign_ports` los puertos de PIDs registrados.

**[EXACT — resultado del mutante]**

```text
test_registered_run_ports_come_from_the_socket_table ... FAIL
AssertionError: Lists differ: [2402, 2404] != []
Ran 1 test — FAILED (failures=1), RC=1
```

SHA mutado: `C0D0AFF1419B1349D22491734902124F82CCD55C99B546B89F38002AA55A8155`. SHA restaurado de `process_lifecycle.py`: `90BC209C97032807351002D16E905EA0A31B58F08379DF4F32DAFCD0504112A2`.

## F-4 — LOSS R2-B-1: cualquier puerto solicitado y coste/ruido

**PASS.** El request público admite 1024–65530 (`tools/dayz_mcp/dayz_test_request.py:326`). Repetí el fixture end-to-end de lifecycle y builder tanto para 3002 como para 65530, con holder no-DayZ `renamed.exe`.

**[EXACT — salida observada]**

```text
requested=3002  foreign_ports=[3002]  reason=port_in_use_foreign  port=3002
requested=65530 foreign_ports=[65530] reason=port_in_use_foreign  port=65530
```

En ambos casos el hint nombra el puerto, recomienda otro `port=` o esperar al holder y declara que `wait_for_box_s` no ayuda mientras la caja se lee libre. El test persistente recorre ambos límites fuera de banda (`tools/tests/test_box_port_wait.py:126-135`).

Medí la tabla UDP real de este host con `snapshot_udp_port_holders()` y ningún PID registrado restado: `known=true`, 89 filas holder y **48 enteros únicos** en `foreign_ports`; todos eran `int`. El umbral pedido de 200 no se alcanza, por lo que no propongo tope.

## F-5 — Provenance

El parche declara exactamente nueve ficheros. `git apply --reverse --check DIFF-D3.patch` dio RC=0 al inicio y al cierre; por tanto, los bytes actuales de los nueve corresponden al parche completo. `git diff --check HEAD -- <los 9 paths>` dio RC=0.

`git diff --stat HEAD -- <los 9 paths>` muestra los seis ficheros ya seguidos por Git:

**[EXACT — salida observada]**

```text
tools/README-mcp.md                 |   4 +-
tools/dayz_mcp/daemon.py            |   7 +-
tools/dayz_mcp/loopback.py          |   6 +
tools/dayz_mcp/orphan_guard.py      | 136 +++++++++++++++++++++
tools/dayz_mcp/process_lifecycle.py | 235 +++++++++++++++++++++++++++++++++++-
tools/dayz_mcp/server.py            |  88 +++++++++++++-
6 files changed, 470 insertions(+), 6 deletions(-)
```

Los tres tests son aún untracked, por lo que `git diff --stat` no los enumera. El stat del parche —y el reverse-check que sí valida sus bytes— es:

**[EXACT — salida observada]**

```text
tools/README-mcp.md                    |   4
tools/dayz_mcp/daemon.py               |   7
tools/dayz_mcp/loopback.py             |   6
tools/dayz_mcp/orphan_guard.py         | 136
tools/dayz_mcp/process_lifecycle.py    | 235
tools/dayz_mcp/server.py               |  88
tools/tests/test_box_port_occupancy.py | 430
tools/tests/test_orphan_guard_udp.py   | 167
tools/tests/test_box_port_wait.py      | 164
9 files changed, 1231 insertions(+), 6 deletions(-)
```

SHA-256 de los seis ficheros de producto:

**[EXACT — salida observada]**

```text
B894D07F50B8A942325AA008E9360FF99DE90E9C41418BE48A95591E16B52623  tools/README-mcp.md
65AE92E923BEA50BD3E1BB122E7C537EA71B6B8F202551522F7703501D4708CD  tools/dayz_mcp/daemon.py
9C3C270E84813013FC5396D26D0326D19AEE073557E6B2E6A57ACC9CF4F20936  tools/dayz_mcp/loopback.py
82902F24AD0CBEF99978BB27A0AA66C18A3DDA1708AE0F229A5AE22A7FD8CA31  tools/dayz_mcp/orphan_guard.py
90BC209C97032807351002D16E905EA0A31B58F08379DF4F32DAFCD0504112A2  tools/dayz_mcp/process_lifecycle.py
6014232BD85B9B12EF5715A84E8E500B3294FB79D54F8E3679D10DDDCA8EDA28  tools/dayz_mcp/server.py
```

Los SHA focales se mantuvieron estables durante la segunda ejecución completa de F-1. El árbol contiene trabajo ajeno en otros paths; se ignoró conforme al encargo.

## BLOQUEANTES

Ninguno. No existe repro `[EXACT]` rojo sobre los bytes restaurados del lote en los tres deltas de ronda 3.

## BACKLOG

- **Sin backlog nuevo de ronda 3.** La muestra real publica 48 enteros, por debajo del umbral de 200.
- **Cosmético, no bloqueante:** el docstring de `_port_conflict_fields` todavía afirma que el puerto retenido está en `ports_in_use`, aunque la implementación corregida usa `foreign_ports` (`tools/dayz_mcp/server.py:2715-2723,2736-2738`). Conviene alinear ese comentario en otro lote; no cambia el contrato ni la ejecución.
- Se mantienen, sin reabrir, los backlogs expresamente heredados: carrera residual entre segundo sondeo y bind, reconciliación gestionada por puerto, compatibilidad transitoria `port_probe=None` y test persistente de wire.

## LO QUE NO PUDE VERIFICAR

- No ejecuté DayZ, daemon, launcher ni tráfico de red; `COMUN.txt` lo prohíbe. La tabla UDP real se leyó de forma pasiva.
- No abrí sockets reales en 3002/65530 ni lancé procesos holder: esos casos se verificaron con el fixture normalizado que consume el lifecycle, tal como permiten las fronteras, y con las funciones productivas de caja y diagnóstico.
- No ejecuté el discover global del repositorio; el gate pedido fue exactamente 62 + 485 tests.
- No actualicé la memoria Obsidian: el vault es de solo lectura en esta sesión y el artefacto durable autorizado es este dictamen.
