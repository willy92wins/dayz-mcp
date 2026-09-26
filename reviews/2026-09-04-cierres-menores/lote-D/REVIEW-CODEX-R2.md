# Revisión de corrección — lote D, ronda 2

## VEREDICTO

**BLOQUEANTES=1.**

B-01 está cerrado conforme a la decisión de ronda 2: existe una segunda lectura inmediatamente antes de `launcher`, el holder que aparece durante `_prepare_instance` impide el lanzamiento y el texto declara honestamente la carrera residual entre esa lectura y el `bind` de DayZ. Sin embargo, el delta no puede commitearse tal cual: el diagnóstico público prometido pierde `reason: port_in_use_foreign` y el puerto para puertos válidos fuera del rango interno `2302..2999` (B-02).

Contexto revisado: `HEAD cdae73248d0d18b07fe0154b1fb916dce7b271ca`; `git apply --check --reverse DIFF-D2.patch` y `git diff --check` sobre los seis ficheros de producto dieron `RC=0`.

## V-1 — Suites

[EXACT] Ejecutadas desde `tools/` con `PYTHONPATH=.`, `PYTHONDONTWRITEBYTECODE=1` y `\.venv-mcp\Scripts\python.exe`:

```powershell
& '.\.venv-mcp\Scripts\python.exe' -m unittest tests.test_box_port_occupancy tests.test_orphan_guard_udp tests.test_box_port_wait -v
```

Resultado estable: `Ran 56 tests in 0.139s` — `OK`. El recuento es 56, no ~47; la subclase de ronda 2 repite los 15 heredados, como advertía el brief.

[EXACT]

```powershell
& '.\.venv-mcp\Scripts\python.exe' -m unittest tests.test_box_occupancy tests.test_process_lifecycle tests.test_daemon tests.test_instance_fence tests.test_session_status_blocked_on tests.test_docs_truth tests.test_mcp_tools tests.test_run_reaper tests.test_session_acquire_wait tests.test_loopback tests.test_lifecycle_cli -v
```

Resultado estable: `Ran 576 tests in 20.612s` — `OK (skipped=3)`. `tests.test_docs_truth` aislado: 20 tests, `OK (skipped=3)`.

Una primera pasada dio dos fallos mientras otro proceso todavía modificaba el árbol vivo. Los tracebacks ya no correspondían a las líneas leídas después. No los clasifiqué como fallos del producto: repetí ambas suites con SHA-256 antes/después, ningún fichero cambió durante la repetición y los resultados anteriores son los estables.

## V-2 — Reproducción de B-01 y variante cubierta

**Ventana residual dentro de `racing_launcher`: reproducida y aceptada por el contrato corregido.** El holder se crea dentro del launcher, después de la segunda lectura: `RESULT={"ok":true,"run_id":"run-1","state":"RUNNING"}` y `LAUNCH_CALLS=1`. El run durable queda `RUNNING`, con dueño `A`, lease `lease-A` y PID 9001. Esta es la ventana entre lectura y `bind` que el código comenta en `tools/dayz_mcp/process_lifecycle.py:2033-2036` y que el texto público declara.

**Holder que aparece en `_prepare_instance`: cubierto.** Resultado observado:

```text
RESULT={"error":"active_run_exists","run_id":"run-1","state":"EXITED"}
LAUNCH_CALLS=0
AUDIT={"event":"lifecycle_start_rejected","reason":"port_in_use_foreign","decision":"rejected","stage":"pre_launch","run_id":"run-1"}
```

La segunda lectura y el rechazo están en `tools/dayz_mcp/process_lifecycle.py:2037-2058`; `launcher` solo se alcanza después, en `:2060-2064`. El cierre durable deja el provisional `EXITED`, sin `owner_session_id`, sin `owner_lease_id` y con `processes=[]` (`:1761-1775`). `manifest.list_runs()` contiene exactamente ese `run-1` retirado. No aparece en `box_occupancy()` porque `_derive_box` excluye estados no activos (`:325-329`). El coordinador terminó con `pending_authorizations=[]` y `committed_commands={}`; el consumo de la reserva está en `tools/dayz_mcp/session_coordination.py:1303-1306` y la finalización en `tools/dayz_mcp/process_lifecycle.py:2132-2133`.

Conclusión: **V-2 VERDE; B-01 cerrado**.

## V-3 — Mutantes

Los cuatro mutantes dieron ROJO individualmente y fueron restaurados. SHA-256 finales: `process_lifecycle.py=A548930663BA8F18F1CBB21830FE794B76B60646D8B7FFE3BB77E4F5558D8009`, `orphan_guard.py=82902F24AD0CBEF99978BB27A0AA66C18A3DDA1708AE0F229A5AE22A7FD8CA31`, `server.py=CF0F3F620B27F4D932FA221B6DB2CBBAECCAB05F62C99ADDB9A90343229CB355`.

| Mutante | SHA mutado | Test discriminador | Rojo observado |
|---|---|---|---|
| (a) quitar `pre_launch_reason` (`tools/dayz_mcp/process_lifecycle.py:2037-2058`) | `E9CAEF08B9744A9E9BB1B048212B940E2AE2A7F76BDC662184384E38ED9B88DE` | `test_holder_appearing_before_the_launch_is_caught_by_the_pre_launch_read` | `None != 'active_run_exists'`; acabó `RUNNING`; `RC=1` |
| (b) devolver `None` al final de `_foreign_port_reason` (`:3331`) | `ADD0B0521DA5BAE5D2778B955F587EF59115A746F1A8CF822361D2BE3803A79D` | `test_nameless_holder_is_attribution_unknown_and_blocks` | `'identity_unavailable' != 'active_run_exists'`; `RC=1` |
| (c) volver a `continue` para fila UDP truncada (`tools/dayz_mcp/orphan_guard.py:400-439`) | `9C821F322AC5FDDD42DBA06D4C8B8592E460C775C53DC18C4566EECDE17ACA0C` | `test_a_truncated_udp_row_makes_the_whole_dump_untrusted` | lista de holders no era `None`; `RC=1` |
| (d) quitar fast-fail (`tools/dayz_mcp/server.py:2859-2868`) | `7FD9CF82400AECDBF0B730FB1B4D0E14F4908ECCF7761122409EC129E859AA46` | `test_port_scan_unknown_returns_at_once` | agotó estados de caja en vez de retornar; `RC=1` |

La primera reversión de (b) coincidió por error con otro `return None`; el gate SHA lo detectó. Corregí ambos contextos exactos antes de continuar. Las suites finales y los SHA anteriores prueban que no quedó el mutante ni la reversión errónea.

Conclusión: **V-3 VERDE**.

## V-4 — Texto público y correspondencia con el código

`README-mcp.md` y las descripciones MCP ya no prometen “never”. El README declara la relectura, la ventana residual hasta el `bind`, la ausencia de reserva, `port_in_use_foreign` con puerto y que esperar no arregla `port_scan_unknown` (`tools/README-mcp.md:76-80`). `dayz_test_run` repite esos límites en `tools/dayz_mcp/server.py:3140-3149`; `session_status` describe la tabla y los puertos en `:3094-3109`. La búsqueda del literal anterior `never started on top of it` devolvió cero coincidencias.

El código implementa:

- sondeo fresco y razones `port_scan_unknown` / `port_in_use_foreign` / `port_attribution_unknown` en `tools/dayz_mcp/process_lifecycle.py:3301-3331`;
- relectura pre-launch en `:2033-2058`;
- fast-fail de espera desconocida en `tools/dayz_mcp/server.py:2859-2868` y mapeo a `error_code=port_scan_unknown` en `:3211-3212`;
- enriquecimiento con `reason` y `port` solo si el puerto figura en `box.ports_in_use`, en `:2715-2754`.

`tests.test_docs_truth` sigue verde, pero la última condición revela B-02: el texto universal no coincide con toda la superficie de puertos aceptada. Por ello **V-4 ROJO**.

## V-5 — Backlog del predecesor

**BACKLOG-2 aplicado.** Los tres fallbacks de `_box_payload` incluyen ahora `scan_known=False` y `port_scan_known=False` (`tools/dayz_mcp/loopback.py:246-283`). La ruta normal conserva todas las claves del lifecycle mediante `dict(raw)` y solo completa mínimos (`:313-324`).

**BACKLOG-3 no aplicado, como se esperaba.** No existe una regresión persistente de wire para estas claves. Ejecuté una sesión MCP real en memoria mediante `create_connected_server_and_client_session`: `session_status.box` conservó `port_scan_known=false` y `port_scan_reason="port_attribution_unknown"`. `_box_from_status` también copia la caja antes de normalizarla (`tools/dayz_mcp/server.py:2678-2695`). La sonda terminó `RC=0`; no abrió sockets ni arrancó daemon/DayZ.

Conclusión: **V-5 VERDE**, con BACKLOG-3 aún pendiente.

## BLOQUEANTES

### B-02 — El rechazo de un puerto público válido puede perder `reason` y `port`

Severidad concreta: **degradación del contrato y de la remediación pública**. El lanzamiento se bloquea de forma segura; no es crash, exception ni corruption. Pero el cliente recibe el consejo falso de esperar, sin saber qué puerto cambiar.

Mecanismo leído:

1. La API admite `port` entre 1024 y 65530 (`tools/dayz_mcp/dayz_test_request.py:326-327`).
2. El guard rechaza cualquier imagen que posea exactamente el puerto pedido (`tools/dayz_mcp/process_lifecycle.py:3323-3326`).
3. La caja solo recuerda procesos no-DayZ/no-registrados si el puerto está en `_DAYZ_PORT_RANGE = range(2302, 3000)` (`:50-54`, `:3458-3465`).
4. El enriquecimiento público solo emite `reason: port_in_use_foreign` y `port` si ese puerto está en `box.ports_in_use` (`tools/dayz_mcp/server.py:2736-2753`).
5. `dayz_test_run` invoca ese enriquecimiento con la caja recién consultada y el puerto solicitado (`tools/dayz_mcp/server.py:3261-3280`).
6. README y descripción prometen ese diagnóstico sin limitarlo al rango interno (`tools/README-mcp.md:78`; `tools/dayz_mcp/server.py:3143-3147`).

[EXACT] Reproducción literal ejecutada desde `tools/`:

```powershell
$env:PYTHONPATH='.'
$env:PYTHONDONTWRITEBYTECODE='1'
@'
import json
from dayz_mcp.server import _enrich_active_run_result
from tests.test_box_port_occupancy import PortOccupancyRound2Test, _holders
from tests.test_process_lifecycle import IDENTITY_A

case = PortOccupancyRound2Test(methodName="test_requested_port_parsing")
case.setUp()
try:
    case.port_holders = _holders((3002, 5555, "python.exe"))
    box = case.lifecycle.box_occupancy()
    result = case.lifecycle.start_run(IDENTITY_A, case.token_a, case.request(port=3002))
    public = _enrich_active_run_result(result, box, port=3002)
    print("BOX=" + json.dumps(box, sort_keys=True))
    print("START=" + json.dumps({"error": result.get("error")}, sort_keys=True))
    print("PUBLIC=" + json.dumps({key: public.get(key) for key in ("error_code", "hint", "port", "reason")}, sort_keys=True))
finally:
    case.tearDown()
'@ | & '.\.venv-mcp\Scripts\python.exe' -
```

Salida literal:

```text
BOX={"foreign": [], "occupied": false, "port_scan_known": true, "port_scan_reason": null, "ports_in_use": [], "queue": [], "runs": [], "scan_known": true}
START={"error": "active_run_exists"}
PUBLIC={"error_code": "active_run_exists", "hint": "retry with wait_for_box_s=<n>", "port": null, "reason": null}
```

Fix sugerido: alinear el dominio del diagnóstico con el dominio de puertos aceptado. La opción coherente con el contrato actual es conservar en `ports_in_use` todo puerto pedido/admitido que pueda provocar el guard, o transportar directamente la razón y el puerto del rechazo hasta el resultado MCP. Alternativamente, restringir y documentar explícitamente el rango público. Añadir regresiones para 3002 y para los límites 1024/65530.

## BACKLOG

### BACKLOG-3 — Falta una prueba persistente de wire

La sonda de V-5 demuestra que hoy `port_scan_known` y `port_scan_reason` sobreviven, pero no hay un test de sesión MCP que lo pine. Convertir la sonda en regresión evitaría que una whitelist futura los borre.

### BACKLOG-NUEVO-1 — El fallo del audit pre-launch no se publica

Severidad: **degradación de observabilidad**, no fallo de seguridad ni fuga de reserva. `_audit` devuelve `False` si el sink falla (`tools/dayz_mcp/process_lifecycle.py:1202-1215`), y `_start_rejection` sí convierte eso en `audit_failed` (`:1598-1619`), pero la nueva rama pre-launch ignora el retorno (`:2041-2048`). Una sonda con fallo forzado del evento `lifecycle_start_rejected` devolvió `active_run_exists`, dejó el provisional correctamente `EXITED`, no lanzó y no añadió `cleanup_degraded`. Sugerencia: conservar el rechazo primario, pero publicar la degradación de audit o registrar un outcome equivalente.

### Riesgo aceptado — carrera entre segunda lectura y bind

No es bloqueante en esta ronda porque COMUN-R2 fijó explícitamente esa frontera y el texto ya es honesto. El repro de V-2 confirma que sigue existiendo; solo una reserva/hand-off o confirmación del bind en el consumidor podría eliminarla, no una tercera lectura.

Los demás asuntos que COMUN-R2 dejó explícitamente fuera de esta corrección no fueron re-clasificados como bloqueantes.

## LO QUE NO PUDE VERIFICAR

- No lancé DayZ, daemon, launcher PE ni procesos de red por la frontera del encargo. Las carreras se reprodujeron determinísticamente con los dobles del lifecycle.
- No ocupé un socket UDP real: la reproducción B-02 inyecta la misma fila normalizada que consume el lifecycle. Sí confirmé el camino causal completo por código y salida ejecutable.
- No ejecuté la suite global del repositorio; ejecuté las dos suites exactas solicitadas (56 + 576) y `tests.test_docs_truth` aislado.
- No actualicé Obsidian: el único artefacto solicitado y autorizado para esta revisión es este dictamen en `review-D`; no apareció ningún hecho durable adicional que requiriese alterar memoria de proyecto fuera del entregable.
