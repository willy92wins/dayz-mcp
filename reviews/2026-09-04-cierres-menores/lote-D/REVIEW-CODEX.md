# Revisión de corrección — lote D

## VEREDICTO

**BLOQUEANTES=1.**

El lote mata los cuatro mutantes obligatorios, conserva el payload nuevo hasta la superficie MCP y pasa las suites pedidas. No encontré ninguna ruta de `dayz_test_run` que mate, reclame o adopte un PID ajeno por haberlo visto en la tabla UDP.

No puede commitearse **tal cual** con la garantía pública absoluta «never started on top of it»: hay una ventana reproducible entre el último sondeo y `launcher()` y el propio README declara que no existe reserva del puerto. Es una brecha de garantía/corrección, no un crash, una excepción ni corrupción demostrada.

Contexto de bytes revisados:

- Brief: decía `HEAD 7670703`; el árbol vivo estaba en `cdae73248d0d18b07fe0154b1fb916dce7b271ca`. `7670703` es ancestro inmediato.
- Los blobs base de los cinco ficheros versionados coinciden con los `index` de `DIFF-D.patch` y `git apply --check --reverse DIFF-D.patch` dio `RC=0`: el parche entregado sí describe los bytes vivos revisados.
- `git diff --check` sobre los cinco ficheros de producto dio `RC=0`.

## V-1 — Suites

[EXACT] Ejecutado desde `tools/`, con el intérprete y variables del brief:

```powershell
$env:PYTHONPATH='.'
$env:PYTHONDONTWRITEBYTECODE='1'
& '.\.venv-mcp\Scripts\python.exe' -m unittest tests.test_box_port_occupancy tests.test_orphan_guard_udp -v
```

Salida literal recortada:

```text
Ran 23 tests in 0.056s

OK
```

Repetición final sobre los bytes restaurados:

```text
Ran 23 tests in 0.069s

OK
```

[EXACT] Segunda suite:

```powershell
& '.\.venv-mcp\Scripts\python.exe' -m unittest tests.test_box_occupancy tests.test_process_lifecycle tests.test_daemon tests.test_instance_fence tests.test_session_status_blocked_on tests.test_docs_truth tests.test_mcp_tools tests.test_run_reaper tests.test_session_acquire_wait -v
```

Salida literal recortada:

```text
Ran 493 tests in 19.991s

OK (skipped=3)
```

Repetición final:

```text
Ran 493 tests in 22.100s

OK (skipped=3)
```

Conclusión: **VERDE**. El recuento vivo es 493, no los 491 indicados por el orquestador; el `HEAD` avanzó un commit, pero no hubo fallos.

## V-2 — Mutantes obligatorios

SHA-256 original de `tools/dayz_mcp/process_lifecycle.py`:

```text
2FD51F9DA9B0675974B414A372C0D8CE99197540DBE819329A2FA3273CFC8426
```

Cada mutación se aplicó sola, se ejecutó únicamente su test discriminador y se revirtió manualmente. Tras A, B, C y D, el SHA restaurado fue exactamente el anterior. Control final restaurado: `Ran 15 tests ... OK` para `tests.test_box_port_occupancy`.

| Mutante | SHA mutado | Test | Resultado rojo literal recortado |
|---|---|---|---|
| A: quitar `_is_dayz_image(...) or` en `tools/dayz_mcp/process_lifecycle.py:3281` | `6D7F9DC8D57EBFE19EE34743E2198B70472F4393C12ECCA5351C750E19E5EE3B` | `test_start_refuses_while_a_foreign_dayz_image_holds_any_port` | `AssertionError: 'identity_unavailable' != 'active_run_exists'`; `FAILED (failures=1)` |
| B: devolver `None` en `_foreign_port_reason`, sustituyendo el sondeo fresco de `tools/dayz_mcp/process_lifecycle.py:3274` | `8EA60C9E677D153BC8FC7A7D4E737EAF14313F680C8094B3459E4FDCA5C23D8D` | `test_start_uses_a_fresh_probe_not_the_box_cache` | `AssertionError: 'identity_unavailable' != 'active_run_exists'`; `FAILED (failures=1)` |
| C: poner `port_scan_known=True` en la rama desconocida de `tools/dayz_mcp/process_lifecycle.py:3402-3408` | `C64F80D728BE4C4CA197BA26A0DA7D8EA6207ABA30E0B208B4677E36B7071087` | `test_unreadable_socket_table_is_an_occupied_box` | tres subcasos: `AssertionError: False is not true`; `FAILED (failures=3)` |
| D: quitar `not dayz` del filtro de `tools/dayz_mcp/process_lifecycle.py:3415-3419` | `CCB3DD09D3CFC723CFCD635B1D34598BEECB039A1FD13A39D23695952399C9EE` | `test_unrelated_services_never_touch_the_box` | `AssertionError: True is not false`; `FAILED (failures=1)` |

Nota de arnés: un primer control post-restauración se lanzó por error desde la raíz y produjo `ModuleNotFoundError: No module named 'tests'`. Se repitió desde `tools/` con el entorno obligatorio y dio 15/15 `OK`; no fue un fallo del producto.

Conclusión: **los cuatro tests discriminan la ausencia de su cambio** y el fichero terminó byte-idéntico.

## V-3 — Forma y propagación de `session_status.box`

Lectura verificada:

- `tools/dayz_mcp/process_lifecycle.py:336-346` construye la caja e incluye `scan_known` y `port_scan_known`.
- `tools/dayz_mcp/loopback.py:246-258` hace `dict(raw)`; después solo añade/normaliza campos conocidos (`:278-315`), por lo que no crea una whitelist que elimine claves nuevas.
- `tools/dayz_mcp/server.py:2678-2695` hace otro `dict(box)` y normaliza listas/`occupied`; también conserva claves desconocidas.

[EXACT] Sonda con `create_connected_server_and_client_session`, caja con `port_scan_known:false`, fila `foreign` nueva y una clave centinela desconocida:

```text
{"blocked_on":"DayZ test box; next: call dayz_test_run(..., wait_for_box_s=<n>) to join the box FIFO","box":{"foreign":[{"image":"DayZServer_x64.exe","mods":[],"port":2502,"profiles":null,"source":"port"}],"future_unknown_key":"survives","occupied":true,"port_scan_known":false,"ports_in_use":[2502],"queue":[],"runs":[],"scan_known":true}}
```

La sonda afirmó que `port_scan_known is False`, que `future_unknown_key == "survives"` y que `json.dumps(payload)` no contiene `pid`; `RC=0`.

[EXACT] Sonda de consumidores con la misma fila `foreign`:

```text
FIELDS={"age_s": 0.0, "foreign": true, "hint": "retry with wait_for_box_s=<n>", "label": "", "mod": "", "occupied_by_run_id": null, "port": 2502}
FAILED={"error_code": "active_run_exists", "foreign": true, "hint": "retry with wait_for_box_s=<n>", "port": 2502, "run_id": null, "status": "failed"}
```

`occupancy_error_fields` acepta `mods:[]` porque el puerto válido basta (`tools/dayz_mcp/process_lifecycle.py:426-450`); `_failed_active_run_result` incorpora esos campos sin depender de `image`/`source` (`tools/dayz_mcp/server.py:2730-2752`).

Conclusión: **VERDE** en la ruta normal. Véase BACKLOG-2 para la forma degradada cuando falta/falla el lifecycle.

## V-4 — Espera FIFO y ausencia de destrucción ajena

Lectura verificada:

- `execute_wait_for_box` rechaza `occupied=True`, exige además ser cabeza de la cola y solo entonces reclama el ticket (`tools/dayz_mcp/server.py:2777-2837`). El `tool_lock` cubre cada consulta/reclamación, no el sueño (`:2800-2804`, `:2834-2837`).
- `dayz_test_run` entra siempre por esa espera cuando `wait_for_box_s>0` (`tools/dayz_mcp/server.py:3137-3158`) y llama al launcher después (`:3162-3186`).
- El guard de puerto corre antes de crear el provisional y antes de `self.launcher(...)` (`tools/dayz_mcp/process_lifecycle.py:1902-1922`, `:1933-2024`). Un conflicto observado sale por `_start_rejection`; no pasa al launcher.
- La única terminación en el camino de fallo de arranque recibe el objeto `launched` que acaba de devolver **ese** `self.launcher` (`tools/dayz_mcp/process_lifecycle.py:1734-1750`, `:2157-2179`). No recibe un PID/holder de la tabla UDP.
- Búsqueda mecánica de `foreign/holders` combinados con `terminate/kill/reclaim` en `tools/dayz_mcp`: cero sitios. La ruta de puertos solo aparece en el sondeo/wiring (`orphan_guard.py:400-504`, `process_lifecycle.py:3214-3437`, `daemon.py:519-531`).

[EXACT] Sonda MCP en memoria, empezando con un holder de puerto y pasando después a libre:

```text
EVENTS=["saw_foreign_port_holder", "saw_free_box_at_fifo_head", "fifo_claim", "launch_called", "ticket_done"]
RESULT={"run_id": "12345678-1234-4234-8234-1234567890ab", "status": "succeeded"}
```

La aserción exigió exactamente ese orden. Conclusión: **VERDE para un holder ya observado y para el FIFO; cero ruta destructiva ajena encontrada**. La garantía atómica falla en el interleaving del BLOQUEANTE-1.

## V-5 — Redacción de PID y rutas de host

- La fila pública de puerto se forma sin `pid` en `tools/dayz_mcp/process_lifecycle.py:3420-3430`; el PID solo es clave interna de deduplicación.
- La fila de diagnóstico tampoco publica PID y sanea perfiles/mods a nombres (`tools/dayz_mcp/process_lifecycle.py:94-120`, `:3389-3395`). Los tests existentes pinean `pid` y `C:\\` ausentes (`tools/tests/test_box_port_occupancy.py:148-163`; `tools/tests/test_box_occupancy.py:245-274`).
- `_snapshot_all_process_entries` toma `entry.szExeFile`, no `QueryFullProcessImageNameW` (`tools/dayz_mcp/orphan_guard.py:232-254`). La estructura declara `szExeFile` como nombre de imagen de 260 bytes (`:105-122`).
- Control vivo, solo lectura: `TOOLHELP_ENTRIES=575 PATHLIKE_NAMES=0`; se rechazaba cualquier nombre con `\\`, `/` o `:`.
- La sonda MCP de V-3 verificó además que el payload público completo no contiene `pid`.

Conclusión: **VERDE**; no hallé fuga de PID ni ruta de host en la nueva superficie.

## V-6 — README y descripciones

- Los motivos de auditoría son exactos: `_foreign_port_reason` produce `port_in_use_foreign` o `port_scan_unknown` (`tools/dayz_mcp/process_lifecycle.py:3274-3285`), y `_start_rejection` pasa `audit_reason` a `_audit` (`:1585-1597`). Los tests los fijan en `tools/tests/test_box_port_occupancy.py:239-265`, `:279-299`.
- README describe el holder DayZ, ambos motivos y FIFO en `tools/README-mcp.md:76-80`.
- Las dos descripciones públicas están en `tools/dayz_mcp/server.py:3038-3054` y `:3072-3093`.
- Sonda real de `list_tools` en memoria:

```text
session_status: missing=[]
dayz_test_run: missing=[]
```

Se exigieron los literales `foreign DayZ processes seen by image or by a held UDP port`, `ports_in_use from the socket table`, `audit port_in_use_foreign`, `never started on top of it`, `wait_for_box_s>0 waits` y `FIFO`.

Conclusión: los literales **sí llegan al cliente**, pero «never started on top of it» contradice la ventana sin reserva demostrada abajo.

## BLOQUEANTES

### B-01 — La garantía «never started on top of it» es falsa ante una carrera entre sondeo y lanzamiento

Severidad concreta: **brecha de corrección/garantía de seguridad de lanzamiento**. No demostré crash, excepción, corrupción ni que el MCP mate el proceso ajeno.

Mecanismo leído:

1. El último sondeo ocurre en `tools/dayz_mcp/process_lifecycle.py:1913-1922`.
2. Después se confirma auditoría/reserva de operación y se persiste el provisional (`:1923-1988`).
3. La llamada real a `self.launcher(...)` no ocurre hasta `:2020-2024`.
4. No hay reserva del socket; el propio `tools/README-mcp.md:80` dice literalmente `There is no reservation registry`.
5. Aun así, README `:78` y descripción MCP `tools/dayz_mcp/server.py:3086-3090` prometen `never started on top of it`.

[EXACT] Repro ejecutado desde `tools/` con el venv obligatorio. Modela el interleaving permitido: el sondeo devuelve libre; al entrar en el launcher, otro actor ya posee 2302; no hay segundo sondeo ni reserva y el run queda `RUNNING`.

```powershell
$env:PYTHONPATH='.'
$env:PYTHONDONTWRITEBYTECODE='1'
@'
import json
from tests.test_box_port_occupancy import PortOccupancyTest, _holders
from tests.test_process_lifecycle import IDENTITY_A, process, snapshot

case = PortOccupancyTest(methodName='test_start_proceeds_when_holders_are_unrelated')
case.setUp()
try:
    state = {'ports': _holders()}
    case.lifecycle.port_probe = lambda: state['ports']
    launched_record = process(case.launcher.pid)
    case.guard.snapshots[launched_record.pid] = snapshot(launched_record)
    original_launcher = case.launcher

    def racing_launcher(*args, **kwargs):
        state['ports'] = _holders((2302, 31337, 'python.exe'))
        return original_launcher(*args, **kwargs)

    case.lifecycle.launcher = racing_launcher
    result = case.lifecycle.start_run(
        IDENTITY_A, case.token_a, case.request(port=2302)
    )
    print('RESULT=' + json.dumps(
        {k: v for k, v in result.items() if k != '_http_status'},
        sort_keys=True,
    ))
    print(f'LAUNCH_CALLS={len(original_launcher.calls)} '
          f'PORT_PROBE_AFTER={json.dumps(state["ports"], sort_keys=True)}')
    assert result.get('ok') is True
    assert len(original_launcher.calls) == 1
finally:
    case.tearDown()
'@ | & '.\.venv-mcp\Scripts\python.exe' -
```

Salida literal:

```text
RESULT={"ok": true, "run_id": "run-1", "state": "RUNNING"}
LAUNCH_CALLS=1 PORT_PROBE_AFTER={"holders": [{"name": "python.exe", "pid": 31337, "port": 2302}], "known": true}
```

Fix sugerido:

- Si el contrato real es «rechazar holders visibles en el sondeo fresco», corregir README y descripción MCP para decirlo explícitamente y declarar la carrera residual; añadir este interleaving como regresión/documentación de frontera.
- Si «never» es requisito literal, hace falta un diseño de reserva/hand-off o una acreditación del bind en el consumidor real. Añadir otro sondeo reduce la ventana, pero no la cierra.

## BACKLOG

### BACKLOG-1 — Holder no-DayZ en el puerto pedido: rechazo seguro, diagnóstico/remediación públicos estériles

Severidad: **degradación**. `_foreign_port_reason` rechaza correctamente cualquier imagen en el puerto pedido (`tools/dayz_mcp/process_lifecycle.py:3281-3284`), pero `_collect_probes` excluye imágenes no-DayZ tanto de `foreign` como de `ports_in_use` (`:3414-3419`). El servidor reconstruye el error desde esa caja (`tools/dayz_mcp/server.py:3198-3206`), por lo que publica `foreign=false`, `port=null` y recomienda `wait_for_box_s`; la espera ve la caja libre y reintenta inmediatamente contra el mismo conflicto.

[EXACT] Resultado de la sonda ejecutada:

```text
BOX={"foreign": [], "occupied": false, "port_scan_known": true, "ports_in_use": [], "queue": [], "runs": [], "scan_known": true}
START={"error": "active_run_exists"}
AUDIT_REASONS=["port_in_use_foreign"]
PUBLIC={"error_code": "active_run_exists", "foreign": false, "hint": "retry with wait_for_box_s=<n>", "port": null}
```

Fix sugerido: hacer el diagnóstico del rechazo sensible al `port` pedido y devolver una receta que permita elegir otro puerto; no convertir todos los servicios UDP en ocupantes globales de la caja, porque el mutante D demuestra que esa política rompe el contrato.

### BACKLOG-2 — La caja degradada omite `scan_known`/`port_scan_known`

Cuando no existe lifecycle, no hay lector o el lector lanza, `_box_payload` fabrica una caja ocupada sin ninguno de los dos flags (`tools/dayz_mcp/loopback.py:249-277`). La ruta normal sí conserva `port_scan_known`; por tanto no bloquea este lote, pero la forma pública no es total en fallos. Sugerencia: incluir ambos como `false` en los tres fallbacks y fijarlo con un test de `session_status` en memoria.

### BACKLOG-3 — Falta una regresión persistente de wire para la clave nueva

Los tests del lote fijan `port_scan_known` en `_derive_box`/`box_occupancy`, pero no a través de una sesión MCP. La sonda V-3 demuestra que hoy funciona; convertirla en test impediría que una whitelist futura de `_box_payload` o `_box_from_status` borre la clave.

## LO QUE NO PUDE VERIFICAR

- No lancé DayZ, daemon ni procesos de red, por la frontera explícita. La carrera de B-01 se reprodujo con un interleaving determinista del probe/launcher, no ocupando un socket UDP real.
- No ejecuté el launcher nativo/PE ni el consumidor DayZ real. La revisión de «no mata ajenos» cubre la ruta Python disponible y los dobles de test. `tools/native-launchers/dayz-test-v1/` era además inaccesible en esta vista (`Access denied`/entradas borradas en `git status`).
- No repetí la suite global de 2595 tests. `SUITE-D.txt` informa `FAILED (failures=6, skipped=6)` por seis fallos de baseline ajenos al lote; por ello este dictamen no afirma que el repositorio global esté verde, solo las 23 + 493 pruebas exigidas.
- No actualicé memoria durable de Obsidian: el entregable solicitado es este artefacto de revisión aislado y no se autorizó escritura fuera de `review-D`.
