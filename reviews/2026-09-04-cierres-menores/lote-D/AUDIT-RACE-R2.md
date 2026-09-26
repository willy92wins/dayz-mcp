# Auditoría de concurrencia y coste — lote D, ronda 2

Árbol auditado: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev`, rama observada `work/inbox-20260830-modules`, HEAD `cdae73248d0d18b07fe0154b1fb916dce7b271ca`, con cambios ajenos sin confirmar. No modifiqué el árbol vivo, no lancé DayZ ni el daemon y no generé tráfico de red. Solo leí la tabla local de sockets y ejecuté `unittest` con fakes.

Bytes de producto fijados para el cierre:

- `tools/dayz_mcp/process_lifecycle.py`: SHA-256 `A548930663BA8F18F1CBB21830FE794B76B60646D8B7FFE3BB77E4F5558D8009`.
- `tools/dayz_mcp/orphan_guard.py`: SHA-256 `82902F24AD0CBEF99978BB27A0AA66C18A3DDA1708AE0F229A5AE22A7FD8CA31`.

## VEREDICTO

**CRITICAL=0 (con repro ejecutable) · MAJOR=0 · BACKLOG=1**

La segunda lectura cierra el CRITICAL H-01 de ronda 1 para todo holder que aparezca antes de ella. Entre el retorno de esa lectura y la entrada al fake `launcher` no hay trabajo de producto ni callback: solo el `if`, la entrada al `try`, evaluación de tres argumentos y la propia llamada (`tools/dayz_mcp/process_lifecycle.py:2037-2063`). La ventana residual empieza cuando ya se entra en `launcher` y termina en el bind real de DayZ; está declarada explícitamente y no existe reserva de puerto (`tools/README-mcp.md:78`; `tools/dayz_mcp/server.py:3146-3149`). El repro de esa limitación pasa a BACKLOG, conforme al criterio del encargo.

Verificación fresca con el intérprete obligatorio:

**[EXACT]**

```powershell
$env:PYTHONPATH='.'
$env:PYTHONDONTWRITEBYTECODE='1'
& '.\.venv-mcp\Scripts\python.exe' -m unittest -v `
  tests.test_process_lifecycle tests.test_box_occupancy tests.test_daemon `
  tests.test_box_port_occupancy tests.test_orphan_guard_udp tests.test_box_port_wait
```

```text
Ran 389 tests in 7.321s
OK
```

Suite focal: 51/51 OK. Repros propios de ronda 2: 7/7 OK.

## A-1 — Relectura pre-launch y ventana restante

**Dictamen:** H-01 queda corregido para la secuencia reproducida en ronda 1. No hay un CRITICAL nuevo con holder que aparece dentro del launcher: es la limitación residual documentada y queda como BACKLOG.

Cadena verificada:

1. El sondeo de admisión sigue en `tools/dayz_mcp/process_lifecycle.py:1926-1936`.
2. Después de crear/persistir el provisional y ejecutar `_prepare_instance`, se hace un segundo `_foreign_port_reason(...)` fresco en `process_lifecycle.py:2033-2039`.
3. Si encuentra holder, audita `stage="pre_launch"`, liquida el provisional con `confirmed_error="active_run_exists"` y no llama al launcher (`process_lifecycle.py:2040-2058`).
4. Si no encuentra holder, la siguiente operación relevante es literalmente `self.launcher(...)` (`process_lifecycle.py:2059-2063`). `_foreign_port_reason` obtiene siempre un probe fresco a través de `_port_holders` (`process_lifecycle.py:3301-3316`).

Repetí exactamente el control de ronda 1 `audit_race_repro.py:124-156`:

**[EXACT]**

```text
test_a1_foreign_holder_appearing_after_probe_must_prevent_launch ... ok
Ran 1 test in 0.014s
OK
A1 result={'error':'active_run_exists','_http_status':409,
           'run_id':'run-1','state':'EXITED'}
probe_threads=['MainThread','MainThread']
holders_at_launcher=[]
launcher_calls=0
```

El antiguo holder que aparecía durante `_prepare_instance` queda cubierto por la segunda lectura. El repro nuevo `audit_race_r2_repro.py:121-152` confirma también `stage="pre_launch"`, dos probes y cero invocaciones del launcher.

Instrumentación de la ventana restante:

- El trazado de líneas de `audit_race_r2_repro.py:154-207` vio únicamente `2040, 2059, 2060, 2061, 2062, 2063` antes de entrar al fake. No apareció persistencia, auditoría, lock adicional, probe ni otra llamada.
- El trazador añade ~1,7 ms de sobrecoste y no sirve como medida temporal real.
- La medida sin trazador de `audit_race_r2_repro.py:209-254`, 20 repeticiones desde el retorno del segundo `_foreign_port_reason` hasta la entrada al fake, dio mediana **0,9 µs**, p95 **1,4 µs**, máximo **2,3 µs**. Es coste Python/fake en este host, no una cota del scheduler ni del bind de DayZ.

El control residual `audit_race_r2_repro.py:256-280` hace aparecer el holder al entrar en el propio fake launcher: el launcher se invoca una vez y el fake devuelve `RUNNING`. Eso prueba que un sondeo no puede reservar el puerto, pero no amplía la ventana documentada ni demuestra que DayZ real sobreviva a perder su bind.

## A-2 — Coste de dos sondeos por lanzamiento

**Dictamen:** sí, el coste normal observado cabe holgadamente frente al sondeo de estado de 0,5–1 s. Además, `session_status` no toma `_operation_lock`, por lo que no queda esperando ese lock.

Medición real de `snapshot_udp_port_holders()` con una llamada de calentamiento y 20 repeticiones (`audit_race_r2_measure.py:22-94`):

| Métrica | Resultado |
|---|---:|
| media por snapshot | 6,695 ms |
| mediana | 6,188 ms |
| p95 | 9,149 ms |
| máximo | 11,393 ms |
| filas por lectura | 90 |
| lecturas `known=True` | 20/20 |
| snapshot ToolHelp masivo | 1 por repetición |
| `_toolhelp_lookup` adicional por PID | 0 en 20 repeticiones |

Estimación conservadora de dos probes independientes usando la misma distribución: media **13,390 ms**, p95 duplicado **18,298 ms**, máximo observado duplicado **22,785 ms**. La media supone **2,678 %** de 500 ms y **1,339 %** de 1 s.

El camino explica la medida: primero obtiene sockets y luego un único snapshot masivo de procesos; solo un PID ausente de ese snapshot dispara `_toolhelp_lookup` individual (`tools/dayz_mcp/orphan_guard.py:494-507`). En este host no se disparó ninguno.

La comparación de concurrencia tampoco revela contención con estado: `_status_snapshot` declara y cumple que nunca toma `_operation_lock`, y solo toma brevemente `_activity_lock` alrededor de la revisión (`process_lifecycle.py:1167-1182`). Los polls nominales son 1 s para caja y mínimo 0,5 s para `wait_for` (`tools/dayz_mcp/server.py:81,84`); `execute_wait_for_box` hace cada lectura y duerme fuera del lock (`server.py:2823-2848`).

Caso degradado: si psutil falla y `netstat` agota sus 3 s, el primer probe ya devuelve unknown y rechaza el start, así que un lanzamiento rechazado no llega a pagar un segundo timeout. Sigue siendo una degradación acotada de operaciones serializadas, no un bypass.

## A-3 — Timeout de netstat y fail-closed

**Dictamen:** confirmado. El literal es `timeout=3`; `TimeoutExpired` se captura como `subprocess.SubprocessError`, el snapshot publica `known=False`, y `start_run` rechaza con `port_scan_unknown`.

Evidencia de código:

- `subprocess.run(["netstat", "-ano", "-p", "UDP"], capture_output=True, timeout=3)` está en `tools/dayz_mcp/orphan_guard.py:470-474`.
- `TimeoutExpired` cae en el `except (OSError, subprocess.SubprocessError)` y devuelve `None` (`orphan_guard.py:475-476`).
- Si tampoco hay fuente psutil, `snapshot_udp_port_holders` convierte `None` en `{"known": False, "holders": []}` (`orphan_guard.py:494-498`).
- Un probe no conocido se convierte en `port_scan_unknown`, nunca en lista vacía conocida (`tools/dayz_mcp/process_lifecycle.py:3264-3274`), y `_foreign_port_reason` propaga ese motivo (`process_lifecycle.py:3314-3316`).

Repro ejecutable con `subprocess.run` parcheado para lanzar `subprocess.TimeoutExpired` (`audit_race_r2_repro.py:282-307`):

**[EXACT]**

```text
A3-timeout {'snapshot': {'known': False, 'holders': []},
            'subprocess_kwargs': {'capture_output': True, 'timeout': 3}}
A3-fail-closed {'result': {'error': 'active_run_exists', '_http_status': 409},
                'audit_reasons': ['port_scan_unknown']}
```

El fake verificó además `launcher_calls=0`. El test de repositorio `tests.test_orphan_guard_udp.NetstatUdpParserRound2Test.test_netstat_timeout_is_bounded_to_three_seconds` también pasó.

## A-4 — Dos lanzadores concurrentes y orden de locks

**Dictamen:** el segundo lanzamiento sigue rechazándose, el primero completa y la relectura no introduce interbloqueo.

`start_run` conserva `_operation_lock` desde `tools/dayz_mcp/process_lifecycle.py:1823` durante ambos probes y la llamada al launcher. Por eso el segundo hilo permanece serializado mientras el primero está bloqueado dentro del fake. La relectura llama al probe sin tomar `_activity_lock`; la invalidación de caché sí sigue el orden permitido `_operation_lock → _activity_lock`. El comentario vinculante está en `process_lifecycle.py:1221-1229`: el orden inverso interbloquearía.

`session_status` constituye el discriminador: no toma `_operation_lock` (`process_lifecycle.py:1170-1182`), completó durante el launcher lento y observó `STARTING`.

Repro con dos hilos y launcher bloqueado por `Event` (`audit_race_r2_repro.py:309-382`):

**[EXACT]**

```text
A4-concurrent {
  'probe_threads_while_first_blocked': ['launcher-1', 'launcher-1'],
  'second_alive': True,
  'status_alive': False,
  'status_states': ['STARTING'],
  'results': {
    'one': {'ok': True, 'run_id': 'run-r2', 'state': 'RUNNING'},
    'two': {'error': 'active_run_exists', '_http_status': 409}},
  'launcher_calls': 1}
```

Tras liberar el `Event`, ambos hilos terminaron dentro del timeout del test. Esto descarta el interbloqueo en la secuencia ensayada y confirma una sola invocación al launcher.

## A-5 — Dos lecturas en `admin_reconcile --empty`

**Dictamen:** no encontré una carrera entre ambas lecturas que publique un estado de manifiesto inconsistente. La segunda lectura funciona como validación pre-commit: si aparece un holder entre lecturas, la operación devuelve 409 y no muta el run.

La secuencia exacta está completamente bajo `_operation_lock` (`tools/dayz_mcp/process_lifecycle.py:3641`):

1. Primera `_diag_snapshot_empty()` en `process_lifecycle.py:3652-3657`.
2. Audit-before-act en `process_lifecycle.py:3675-3686`.
3. Segunda `_diag_snapshot_empty()` en `process_lifecycle.py:3687-3692`.
4. Solo después se mutan owner, procesos y estado (`process_lifecycle.py:3704-3713`) y se persiste el retiro (`process_lifecycle.py:3714-3721`).

Cada `_diag_snapshot_empty` exige tanto snapshot diag vacío como tabla de puertos conocida, sin imagen DayZ ni holder sin atribuir (`process_lifecycle.py:3572-3596`).

Repro `audit_race_r2_repro.py:384-430`: primera tabla vacía, audit exitoso, segunda tabla con `DayZServer_x64.exe` en 2302.

**[EXACT]**

```text
A5-between-reads {
  'result': {'error': 'manual_cleanup_required', '_http_status': 409},
  'port_reads': 2,
  'stored_state': 'UNRECONCILED',
  'stored_processes': 0,
  'admin_events': [{'event': 'admin_reconcile', 'decision': 'confirmed', ...}]
}
```

El evento de auditoría queda escrito aunque la segunda evidencia aborte la operación. No lo considero inconsistencia de estado: es el registro audit-before-act de una decisión administrativa confirmada; el resultado 409 dice que no se aplicó, y no se publica diagnóstico retirado ni se cambia el manifiesto. No encontré consumidor que interprete por sí solo ese evento como commit exitoso. Tras la segunda lectura todavía existe la misma ventana inevitable frente a un proceso externo que aparezca después; no hay reserva del SO. Es la misma limitación contabilizada en H-R2-01, no un segundo hallazgo.

## HALLAZGOS

### H-R2-01 — BACKLOG — carrera residual dentro de launcher/bind

- **Problema concreto:** un proceso externo puede adquirir el puerto después del segundo sondeo, incluyendo durante la entrada a `launcher`, porque el daemon no posee una reserva del puerto.
- **Impacto demostrado:** el fake puede ser llamado una vez y devolver `RUNNING` si él mismo simula éxito aun habiendo hecho aparecer un holder. No se demostró que DayZ real quede funcional ni que el bind perdido se reporte como éxito.
- **Repro [EXACT]:** `audit_race_r2_repro.py:256-280`, salida `launcher_calls=1`, dos probes previos y holder visible al entrar al fake.
- **Clasificación:** BACKLOG, no CRITICAL, porque el encargo y la documentación aceptan expresamente la ventana lectura→bind y no hay código intermedio que la alargue (`tools/README-mcp.md:78`; `tools/dayz_mcp/server.py:3146-3149`).
- **Mitigación [DESIGN]:** solo si se decide cerrar este riesgo residual, usar una reserva/bind heredable o verificar post-launch la propiedad real del puerto y liquidar el intento por el lifecycle guard. Un tercer sondeo seguiría siendo TOCTOU.

No hay hallazgos MAJOR. H-02 de ronda 1 queda remediado: el timeout bajó de 10 s a 3 s y conserva fail-closed.

## LO QUE NO PUDE VERIFICAR

- No ejecuté DayZ ni el daemon, por frontera explícita. Por tanto no verifiqué el resultado real del bind de DayZ ni su transición de lifecycle cuando pierde la carrera residual; H-R2-01 prueba la llamada al launcher, no un servidor real coexistiendo en el mismo puerto.
- La medición de A-2 describe este host y esta tabla (90 filas, sin lookup individual). No es una cota universal para hosts con muchos PIDs ausentes del snapshot masivo ni para el fallback `netstat`.
- No forcé un `netstat.exe` real a colgarse: el valor de 3 s está verificado contra el literal y la propagación se reprodujo con `TimeoutExpired` parcheado, como pedía el encargo.
- No ejecuté la suite global de 2.628 tests. El artefacto recibido `SUITE-D2.txt` registra cuatro fallos previos a una reescritura concurrente posterior. Mi suite afectada fresca quedó 389/389 y la focal 51/51.
- Durante la auditoría observé una reescritura externa transitoria de `process_lifecycle.py`. No atribuí resultados a esa vista intermedia: repetí repros y suites contra el SHA-256 final fijado al inicio de este informe y volví a comprobarlo antes del cierre.
- No actualicé el vault de memoria: está fuera de las raíces de escritura y de la frontera de esta sesión. Este informe y los dos scripts reproducibles quedan en el scratchpad solicitado.
