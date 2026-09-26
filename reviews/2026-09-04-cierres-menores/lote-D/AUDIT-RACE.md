# Auditoría de concurrencia y coste — lote D

Árbol revisado: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev`, rama `work/inbox-20260830-modules`, HEAD observado `cdae73248…`, con cambios ajenos sin confirmar. No se modificó el árbol vivo ni se lanzó DayZ, el daemon o tráfico de red. Los únicos cambios de esta revisión son los scripts de prueba dentro del scratchpad.

## VEREDICTO

**CRITICAL=1 (con repro ejecutable) · MAJOR=0 · BACKLOG=1**

No aprobaría el lote D tal como está: el sondeo fresco arregla el holder preexistente de la ficha, pero todavía permite llamar a `launcher(...)` si un holder aparece después del sondeo. El repro `audit_race_repro.py:124-156` falla hoy y demuestra exactamente esa secuencia. Separadamente, el fallback `netstat` puede mantener serializadas las operaciones de lifecycle hasta 10 s; es una degradación de disponibilidad acotada y fail-closed, no un lanzamiento inseguro.

Verificación focal ejecutada con el intérprete exigido:

**[EXACT]**

```powershell
$env:PYTHONPATH='.'
$env:PYTHONDONTWRITEBYTECODE='1'
& '.\.venv-mcp\Scripts\python.exe' -m unittest -v `
  tests.test_box_port_occupancy `
  tests.test_orphan_guard_udp `
  tests.test_box_occupancy.BoxOccupancyTest.test_starting_provisional_invalidates_empty_occupancy_cache
```

Salida literal recortada:

```text
Ran 24 tests in 0.171s
OK
```

Suite afectada más amplia ejecutada:

**[EXACT]**

```powershell
& '.\.venv-mcp\Scripts\python.exe' -m unittest `
  tests.test_process_lifecycle tests.test_box_occupancy tests.test_daemon `
  tests.test_box_port_occupancy tests.test_orphan_guard_udp
```

```text
Ran 356 tests in 6.732s
OK
```

## A-1 — Ventana entre sondeo y lanzamiento

**Respuesta:** la ventana nueva de puertos es **menor y está contenida** en la ventana que ya tenía el sondeo por nombre, porque ambos se ejecutan bajo el mismo `_operation_lock`, pero `_foreign_port_reason` ocurre después de `_foreign_diag_reason`. No es una ventana cerrada.

- `start_run` adquiere `_operation_lock` en `tools/dayz_mcp/process_lifecycle.py:1810`.
- El sondeo por imagen se hace en `process_lifecycle.py:1905-1912`.
- El sondeo fresco de sockets se hace después, en `process_lifecycle.py:1913-1922`; `_foreign_port_reason` llama directamente a `_port_holders()` en `process_lifecycle.py:3261-3276`, sin pasar por `_probes_for_snapshot` ni por la caché.
- `launcher(...)` no se invoca hasta `process_lifecycle.py:2020-2024`.
- Entre ambos quedan auditoría/commit, creación y persistencia del manifiesto `STARTING` (`process_lifecycle.py:1947-1975`), invalidación de caché (`:1988`) y `_prepare_instance` (`:2006-2018`). Por tanto, las 107 líneas de separación no son una duración, pero sí contienen I/O/hook externos y no existe cota temporal en este método.
- `_operation_lock` excluye otras operaciones del mismo objeto `ProcessLifecycle`; no impide que otro proceso local haga bind durante ese intervalo.

Repro ejecutable con un probe inicialmente vacío y un holder que aparece dentro de `_prepare_instance`, antes de entrar en el fake `launcher` (`audit_race_repro.py:124-156`):

**[EXACT]**

```powershell
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' .\audit_race_repro.py
```

Salida literal recortada:

```text
test_a1_foreign_holder_appearing_after_probe_must_prevent_launch ... FAIL
test_a3_two_sessions_are_serialized_until_first_pid_is_registered ... ok
test_a4_slow_port_probe_holds_operation_lock ... ok

Ran 3 tests in 0.234s
FAILED (failures=1)
A1 result= {'ok': True, 'run_id': 'run-1', 'state': 'RUNNING'}
probe_threads=['MainThread']
holders_at_launcher=[[{'port': 2302, 'pid': 31337, 'name': 'DayZServer_x64.exe'}]]
launcher_calls=1
```

El fallo es el control positivo: el contrato del test exige que el launcher no sea llamado, pero fue llamado una vez mientras el fake ya observaba el 2302 ocupado.

**Cierre barato [DESIGN]:** un segundo sondeo inmediatamente antes de `launcher` solo estrecha la carrera; no puede eliminarla. La autoridad final debe ser el bind del sistema operativo. Tras crear el proceso, comprobar durante la reconciliación de arranque que el puerto solicitado pertenece al PID recién lanzado o a un PID ya registrado; si no, liquidar el intento mediante el lifecycle guard y devolver fallo. Además, un exit temprano por fallo de bind debe reconciliarse como fallo de lanzamiento. Esto cierra el caso incluso si el competidor aparece después del último sondeo.

## A-2 — Caché de 1,5 s y caminos de lanzamiento

**Respuesta:** la lectura de espera sí puede ver un falso `occupied=False` durante menos de 1,5 s, pero **ningún lanzamiento se autoriza únicamente con esa caché**. El claim obtenido a partir de esa lectura conduce después a `start_run`, que vuelve a leer sockets de forma fresca. No hay CRITICAL adicional en A-2; queda la carrera posterior de A-1.

Cadena comprobada:

1. La TTL es `_BOX_OCCUPANCY_CACHE_S = 1.5` (`process_lifecycle.py:43`). `box_occupancy()` llama a `_probes_for_snapshot(..., use_cache=True)` (`process_lifecycle.py:3287-3302`), que reutiliza probes con la misma revisión y edad menor de 1,5 s (`process_lifecycle.py:3440-3457`). Un holder externo no incrementa `_box_revision`, por lo que el falso libre es posible.
2. `execute_wait_for_box` sondea `session_box_status(wait=True, ...)`, exige caja libre y cabeza FIFO, y luego reclama el ticket (`tools/dayz_mcp/server.py:2777-2833`). El intervalo normal de sondeo es 1 s (`server.py:81,2783-2795`).
3. La coordinación solo concede el claim al primer ticket (`tools/dayz_mcp/session_coordination.py:1843-1900`). Un claim ajeno bloquea `start_run` (`session_coordination.py:1823-1829`; llamada en `process_lifecycle.py:1894-1901`).
4. El handler procesa el claim antes de construir el payload de caja (`tools/dayz_mcp/loopback.py:3306-3325`), y `_box_payload` fuerza `occupied=True` si hay claim (`loopback.py:278-310`). Que `execute_wait_for_box` no vuelva a exigir `occupied=False` después del claim no abre el lanzamiento: el claim es el token de serialización, no la autorización final sobre sockets.
5. Después de la espera, `dayz_test_run` ejecuta el arranque bajo `client.tool_lock` (`server.py:3137-3165`); `/lifecycle/start` acaba en `lifecycle.start_run(...)` (`loopback.py:3370-3380`), donde ocurre el sondeo fresco de `process_lifecycle.py:1913-1922`.

El test existente `tests/test_box_port_occupancy.py:279` precarga `occupied=False`, hace aparecer el holder y confirma que `start_run` rechaza usando una segunda llamada al probe. `tests/test_box_occupancy.py:209` confirma además que insertar el provisional `STARTING` invalida la caché vacía.

## A-3 — Dos lanzadores concurrentes

**Respuesta:** el PID real se registra **después** de que `launcher` retorna, no antes (`process_lifecycle.py:2020-2063`). Sin embargo, dentro del daemon único no existe un instante en el que ambos `start_run` pasen:

- El primer hilo mantiene `_operation_lock` desde `process_lifecycle.py:1810` hasta el `finally` de `:2092-2093`.
- Antes de llamar al launcher ya persiste un manifiesto `STARTING` (`process_lifecycle.py:1947-1975`); `STARTING` pertenece a `_ACTIVE_STATES` (`process_lifecycle.py:32-35`).
- Tras retornar el launcher, incorpora el `ProcessRecord`, cambia a `RUNNING` y reemplaza el manifiesto (`process_lifecycle.py:2058-2063`) **antes** de liberar `_operation_lock`.
- El segundo hilo solo puede entrar entonces; encuentra el run activo y devuelve `active_run_exists` en `process_lifecycle.py:1832-1893`, antes de su propio sondeo.

Repro con dos sesiones/reservas independientes, coordinador deliberadamente permisivo, launcher bloqueado por `Event` y un único `ProcessLifecycle` (`audit_race_repro.py:158-209`):

**[EXACT]**

```text
A3 probes_while_first_blocked=['launcher-1']
second_alive=True
results={
  'one': {'ok': True, 'run_id': 'run-1', 'state': 'RUNNING'},
  'two': {'error': 'active_run_exists', '_http_status': 409}
}
launcher_calls=1
final_processes=[9001]
```

`second_alive=True` mientras el primero estaba dentro del launcher y un único nombre en `probes_while_first_blocked` demuestran que el segundo no llegó siquiera al sondeo. La coordinación real es más restrictiva por el claim FIFO, así que el fake no oculta esa barrera.

## A-4 — Coste y bloqueo del lock

**Respuesta:** en la ruta normal de psutil el coste medido cabe holgadamente en el poll de 1 s; no conviene añadir una caché separada de puertos. Sí, el fallback `netstat` puede bloquear `_operation_lock` hasta su timeout de 10 s.

`snapshot_udp_port_holders` llama a psutil (`tools/dayz_mcp/orphan_guard.py:435-452`), usa `netstat` solo si psutil devuelve `None` y fija `timeout=10` (`orphan_guard.py:455-487`), y después hace un snapshot ToolHelp para nombres (`orphan_guard.py:488-504`).

Medición ejecutada desde `tools`, con una llamada de calentamiento y 20 repeticiones por operación mediante `time.perf_counter`:

**[EXACT]**

```powershell
& '.\.venv-mcp\Scripts\python.exe' 'C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\review-D\audit_race_measure.py'
```

```json
{
  "python": "...\\tools\\.venv-mcp\\Scripts\\python.exe",
  "psutil": "7.2.2",
  "measurements": [
    {"name":"snapshot_udp_port_holders","repetitions":20,"mean_ms":11.717,"median_ms":11.03,"min_ms":9.194,"max_ms":17.139,"last_known":true,"last_rows":91},
    {"name":"snapshot_processes_by_name(DayZDiag_x64.exe)","repetitions":20,"mean_ms":16.68,"median_ms":16.913,"min_ms":9.633,"max_ms":25.39,"last_known":true,"last_rows":2}
  ]
}
```

La suma orientativa de ambos probes frescos es 28,397 ms de media, un 2,84 % del poll de 1 s. No es una medición atómica ni un percentil de cola, pero basta para descartar que la ruta normal necesite otra caché en este host. La caché existente reduce además la frecuencia de `box_occupancy`; `start_run` debe conservar su lectura fresca.

El bloqueo queda trazado así: `start_run` toma `_operation_lock` en `process_lifecycle.py:1810`, llama al probe en `:1913-1915` → `:3261-3274`, y el fallback espera dentro de `subprocess.run(..., timeout=10)` en `orphan_guard.py:455-468`. Repro mecánico con un `port_probe` lento (`audit_race_repro.py:211-258`):

**[EXACT]**

```powershell
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' .\audit_race_repro.py RaceAuditTest.test_a4_slow_port_probe_holds_operation_lock
```

```text
test_a4_slow_port_probe_holds_operation_lock ... ok
Ran 1 test in 0.113s
OK
A4 second_blocked=True probe_calls_while_slow=1
results={'one': {'ok': True, ...}, 'two': {'error': 'active_run_exists', '_http_status': 409}}
```

El repro demuestra la posesión del lock, no una demora real de 10 s de `netstat`. La consecuencia es una **degradación**: el resto de operaciones serializadas espera; el fallo de ambas fuentes devuelve `known=False` y el arranque se rechaza, por lo que no es un bypass de seguridad (`orphan_guard.py:483-487`; `process_lifecycle.py:3274-3276`).

**Mitigación [DESIGN]:** reducir el timeout del fallback a un presupuesto cercano al poll (por ejemplo, 1 s) y conservar fail-closed al agotar el tiempo. No cachear un `False/libre` para `start_run`; si se quisiera evitar bloquear operaciones no relacionadas, habría que sacar solo la obtención lenta fuera del lock y aceptar únicamente un snapshot cuya marca de frescura siga vigente al validar, lo que añade complejidad que las mediciones normales no justifican.

## A-5 — Lista parcial sin error

**Respuesta:** el código del proyecto sí confiaría en una lista parcial como `known=True`, pero **no encontré un camino de permisos en psutil 7.2.2/Windows que devuelva menos filas sin error**. La documentación local indica que con privilegios limitados pueden faltar `fd` o PID, no la fila; el backend obtiene tablas completas del API de Windows o lanza.

Evidencia del venv que realmente ejecuta los tests:

- `tools/.venv-mcp/Lib/site-packages/psutil/__init__.py:2198-2202` define el snapshot system-wide y documenta `fd=-1`/`pid=None` con privilegios limitados; `:2224-2225` delega al backend.
- `tools/.venv-mcp/Lib/site-packages/psutil/_pswindows.py:339-359` llama una sola vez a `cext.net_connections`, convierte todos sus elementos y no captura `AccessDenied` ni devuelve una bandera de completitud.
- En el fuente oficial de [psutil 7.2.2 para Windows](https://github.com/giampaolo/psutil/blob/release-7.2.2/psutil/arch/windows/socks.c), `__GetExtendedUdpTable` usa `UDP_TABLE_OWNER_PID`, reintenta si crece el buffer y ante otro error devuelve `NULL` con excepción; el enumerador recorre `dwNumEntries`. Es consistente con el contrato de Microsoft de [`GetExtendedUdpTable`](https://learn.microsoft.com/en-us/windows/win32/api/iphlpapi/nf-iphlpapi-getextendedudptable): éxito entrega la tabla solicitada y el resto son códigos de error.

En el proyecto, cualquier lista devuelta —incluso vacía— termina como `known=True` (`orphan_guard.py:483-504`); el fallback solo se usa cuando psutil devuelve `None` por excepción (`orphan_guard.py:439-441,483-485`). Lo demostré condicionando el upstream con un fake parcial:

**[EXACT]**

```text
{'known': True, 'holders': []}
netstat_fallback_called=False
```

Eso prueba la debilidad condicional del consumidor, no que el upstream real tenga ese comportamiento. La lectura comparativa no atómica de la tabla local tampoco mostró una omisión de psutil respecto de `netstat`:

```text
{'psutil_rows': 62, 'netstat_rows': 55,
 'psutil_minus_netstat': 7, 'netstat_minus_psutil': 0, 'netstat_rc': 0}
```

Como ambas capturas se hicieron una detrás de otra sobre un sistema vivo, la diferencia de siete filas solo es observación temporal, no prueba de superioridad o completitud. No elevo A-5 a hallazgo. Cruzar siempre psutil con `netstat` duplicaría coste y superficie de fallo sin evidencia de que el contrato actual lo necesite.

## HALLAZGOS

### H-01 — CRITICAL — TOCTOU entre el sondeo fresco y el launcher

- **Problema:** un holder que aparece después de `_foreign_port_reason` puede coexistir con la llamada a `launcher`; `_operation_lock` no protege frente a procesos externos.
- **Secuencia exacta:** (1) `start_run` toma `_operation_lock`; (2) `_foreign_diag_reason` no ve servidor; (3) `_foreign_port_reason` devuelve tabla vacía; (4) otro proceso local hace bind del puerto solicitado durante auditoría/persistencia/preparación; (5) `launcher` se ejecuta sin revalidación; (6) el resultado puede reportarse `RUNNING` si el fake/launcher devuelve PID e identidad válidos.
- **Repro [EXACT]:** `audit_race_repro.py:124-156`; comando y fallo literal en A-1. Hoy devuelve `ok=True` y `launcher_calls=1` cuando `holders_at_launcher` ya contiene `{port:2302,pid:31337}`.
- **Fix propuesto [DESIGN]:** tratar el bind real como árbitro y añadir reconciliación post-launch de propiedad del puerto solicitado, liquidando el proceso vía lifecycle guard si el puerto no pertenece al PID lanzado/registrado. Un segundo sondeo pre-launch es defensa adicional, no cierre suficiente.

### H-02 — BACKLOG — fallback `netstat` bloquea operaciones de lifecycle hasta 10 s

- **Problema:** `subprocess.run(..., timeout=10)` corre dentro del `_operation_lock`; durante ese tiempo otro start/stop/reap serializado no progresa. Es degradación de disponibilidad, no crash ni corrupción.
- **Secuencia exacta:** (1) psutil falta o lanza; (2) `_udp_holders_via_netstat` se bloquea; (3) el primer `start_run` conserva `_operation_lock`; (4) otra operación espera; (5) al timeout, el sondeo queda desconocido y el start falla cerrado.
- **Repro [EXACT]:** `audit_race_repro.py:211-258` prueba con `Event` que el segundo hilo permanece bloqueado y no alcanza un segundo probe. No ejecuté un `netstat` artificialmente colgado; por tanto la cifra de 10 s procede del timeout leído en `orphan_guard.py:455-468`, no de una demora observada.
- **Fix propuesto [DESIGN]:** reducir el timeout a aproximadamente 1 s conservando `known=False`/rechazo al vencer. No añadir caché separada para decisiones de lanzamiento.

## LO QUE NO PUDE VERIFICAR

- No ejecuté DayZ ni el daemon, conforme a las fronteras. Por ello no verifiqué qué exit/readiness produce DayZ real al perder el bind, ni si la reconciliación existente lo convierte siempre en fallo. H-01 demuestra que se invoca el launcher, no que un proceso DayZ real termine coexistiendo funcionalmente en 2302.
- No pude producir un caso real de permisos que hiciera a psutil devolver una tabla UDP parcial sin excepción. La implementación fijada de psutil 7.2.2 apunta al comportamiento completo-o-error; el fake de A-5 solo demuestra qué ocurriría si ese contrato cambiara.
- No forcé a `netstat.exe` a tardar 10 s. El repro A-4 verifica la retención del lock con un probe bloqueado; el máximo procede de la constante de timeout leída.
- No certifico la suite global como verde. El artefacto recibido `SUITE-D.txt` registra `Ran 2595 tests in 245.246s` y `FAILED (failures=6, skipped=6)` en fallos ya descritos por `COMUN.txt`/handoff como trabajo concurrente no perteneciente a este foco. Mi suite afectada fresca quedó en 356/356.
- No actualicé el vault de memoria durable: está fuera de las raíces de escritura de esta sesión. Este informe y los repros quedan en el scratchpad solicitado.
