# Auditoría LOSS — ronda 2, lote D

## VEREDICTO

CRITICAL=0 (con repro ejecutable) · MAJOR=1 · BACKLOG=1

La corrección de M-3 es robusta frente al truncado pedido y no encontré ningún caso que pierda un holder real manteniendo `known=True`. M-2 queda incompleta: cuando coexisten un run gestionado y un holder ajeno en el puerto solicitado, el diagnóstico del run gana y vuelve a recomendar esperar aunque la espera no pueda liberar ese puerto. El rango vigente ya cubre los puertos habituales 2302/2402/2502, pero no todo el dominio que acepta la API.

Snapshot auditado: `HEAD=cdae73248d0d18b07fe0154b1fb916dce7b271ca`, rama `work/inbox-20260830-modules`, árbol sucio preexistente. `COMUN-R2.txt` describía todavía `range(2302, 2321)`, pero el árbol vivo auditado contiene `range(2302, 3000)` en `tools/dayz_mcp/process_lifecycle.py:50-54`; `DIFF-D2.patch` y dos archivos de implementación/test tienen una marca temporal posterior a `COMUN-R2.txt` y a `SUITE-D2.txt`. Por ello el veredicto se basa en el árbol vivo y en pruebas focales nuevas, no en la suite histórica aislada.

Prueba focal nueva, desde `tools/`, con el intérprete obligatorio, `PYTHONPATH=.` y `unittest`:

`[EXACT — ejecutado]`

```powershell
$env:PYTHONPATH='.'
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv-mcp\Scripts\python.exe -m unittest -v tests.test_orphan_guard_udp tests.test_box_port_occupancy tests.test_box_port_wait tests.test_box_occupancy tests.test_daemon_security_gate tests.test_daemon_contract
```

Resultado: `Ran 154 tests in 1.303s` · `OK` · exit code 0.

## C-1 — M-3: truncado y entradas raras de `netstat`

El parser trata una fila candidata `UDP` incompleta como fallo del volcado completo: las rutas de fila malformada devuelven `None` en `tools/dayz_mcp/orphan_guard.py:400-439`. El fallback propaga esa indeterminación y `snapshot_udp_port_holders` devuelve `known=False` en `tools/dayz_mcp/orphan_guard.py:464-498`.

Repro pedido:

| Entrada | Parser | Snapshot sin psutil | Clasificación |
|---|---:|---|---|
| `UDP 0.0.0.0:2302` truncada | `None` | `known=False`, `holders=[]` | Volcado desconocido; fail-closed |
| PID `0` | `[(2302, None)]` | `known=True`, holder 2302 sin atribución | Holder real conservado |
| PID `4` | `[(2302, 4)]` | `known=True`, holder 2302, `System` | Holder real conservado |
| `[::1]:2302 ... 777` | `[(2302, 777)]` | `known=True`, holder 2302, `renamed.exe` | Holder real conservado |
| Columna extra no numérica tras PID | `[(2302, None)]` | `known=True`, holder 2302 sin atribución | Holder real conservado |
| Prefijo OEM cp850 no ASCII + fila válida | `[(2302, 777)]` | `known=True`, holder 2302, `renamed.exe` | Holder real conservado |

La decodificación usa la página preferida y reemplazo seguro en `tools/dayz_mcp/orphan_guard.py:339-342`. PID cero y una cola extra no numérica degradan la atribución, pero no eliminan el puerto: después `_collect_probes` convierte cualquier holder sin nombre en `port_scan_known=False` en `tools/dayz_mcp/process_lifecycle.py:3462-3472,3488-3495`. No hay falso negativo: cero casos con holder perdido y `known=True`.

Conclusión C-1: PASS. CRITICAL=0.

## C-2 — M-2: diagnóstico de holder ajeno y combinaciones con run activo

Caso simple, caja libre y `renamed.exe` sosteniendo 2302: `_derive_box` conserva el puerto en `ports_in_use` sin marcar el proceso como DayZ (`tools/dayz_mcp/process_lifecycle.py:325-357,3462-3474`). `_failed_active_run_result(..., port=2302)` publica correctamente, mediante `tools/dayz_mcp/server.py:2715-2754,2774-2798`:

```text
reason: port_in_use_foreign
port: 2302
hint: port 2302 is held ... pass another port= or wait for the holder to exit; wait_for_box_s does not help while box reads free
```

Este texto sí es suficiente para un agente débil: identifica el recurso, explica que `wait_for_box_s` no resuelve ese estado y da las dos salidas útiles.

Caso combinado reproducido por la ruta de lifecycle + builder, con un run propio/ajeno vivo en 2402 y `renamed.exe` en 2302:

```text
box.occupied = true
box.ports_in_use = [2402, 2302]
box.runs = [run-existing en 2402]
box.foreign = []

resultado para otro caller:
error_code = active_run_exists
occupied_by_run_id = run-existing
reason = null
port = null
hint = retry with wait_for_box_s=<n>
```

Para el dueño del run, la única diferencia fue el hint `stop it with dayz_test_stop(run_id=run-existing)`; también recibió `reason=null` y `port=null`. Con `wait_for_box_s=0.1`, el fake hizo tres lecturas y dos sleeps de 0.05 s antes de timeout; el resultado final siguió sin `reason` ni `port` y conservó la receta genérica de esperar. La causa leída es el retorno temprano al diagnosticar el primer run en `tools/dayz_mcp/server.py:386-438`; la rama que mira el conflicto de puerto solo se alcanza si no hay runs (`tools/dayz_mcp/server.py:439-466`). Además, `_port_conflict_fields` exige expresamente `not runs` en `tools/dayz_mcp/server.py:2736-2743`. El test actual fija esa prioridad y exige `reason is None` en `tools/tests/test_box_port_wait.py:89-102` (`test_managed_run_still_wins_the_diagnosis`).

Conclusión C-2: FAIL parcial; hallazgo R2-M-1 (MAJOR). No mata proceso ni corrompe datos: es degradación funcional/diagnóstica. Un agente débil puede gastar toda la espera o parar/esperar el run y descubrir el holder persistente solo en el siguiente intento.

## C-3 — M-1: `DayZServer_x64.exe` en el probe y rechazo pre-bind

El probe productivo enumera `DayZDiag_x64.exe` y `DayZServer_x64.exe` en `tools/dayz_mcp/daemon.py:526-533`. Con este argv típico:

```text
DayZServer_x64.exe -config=serverDZ.cfg -port=2302 -profiles=C:\Private\Servers\Alpha\profiles -mod=C:\Mods\@CF;D:\Workshop\@DayZ_MCP
```

`parse_dayz_launch_argv` (`tools/dayz_mcp/process_lifecycle.py:132-165`) produjo:

```text
ports = [2302]
mods = ["@CF", "@DayZ_MCP"]
profiles = "C:\\Private\\Servers\\Alpha\\profiles"
```

El parser interno conserva el path crudo; la redacción sucede al proyectar el proceso en la caja. El payload público quedó con `profiles="Alpha"`, puerto 2302 y solo basenames de mods, mediante `tools/dayz_mcp/process_lifecycle.py:117-129,3435-3440`. `_foreign_diag_reason` devolvió `foreign_diag_process` (`tools/dayz_mcp/process_lifecycle.py:3598-3606`) y `start_run` evalúa ese veto antes de escanear/crear/lanzar la nueva instancia (`tools/dayz_mcp/process_lifecycle.py:1918-1936,2020-2067`).

Inventario local de solo lectura:

- Instalación cliente: `DayZDiag_x64.exe`, `DayZ_BE.exe`, `DayZ_x64.exe`.
- Instalación server: `DayZServer_x64.exe`; no apareció `DayZServer_BE.exe`.
- `DayZDiag_x64.exe -server` queda cubierto por nombre.
- `DayZ_BE.exe` y `DayZ_x64.exe` no son huecos del `diag_probe`: entran por `retail_probe`; cualquier proceso retail mantiene cuarentena en `tools/dayz_mcp/process_lifecycle.py:1449-1461`, y `_canonical_error` solo autoriza `DayZDiag_x64.exe` local mientras deriva retail/BE a limpieza manual en `tools/dayz_mcp/process_lifecycle.py:1530-1542`.

Conclusión C-3: PASS para las imágenes estándar presentes y las que reconoce el propio proyecto (`tools/dayz_mcp/orphan_guard.py:389-397`). Un binario servidor renombrado/custom sigue fuera de la enumeración pre-bind; es el límite ya documentado en M-1/M-4 y no lo cuento de nuevo como bloqueante.

## C-4 — Clasificación fail-closed de todos los `return`

### `_port_holders` — `tools/dayz_mcp/process_lifecycle.py:3254-3299`

| Return | Línea | Abre/cierra | Razón |
|---|---:|---|---|
| `(None, [])` si `port_probe is None` | 3264-3265 | ABRE | Compatibilidad legacy; B-2 ya documentado |
| `("port_scan_unknown", None)` por excepción | 3266-3269 | CIERRA | No certifica caja libre |
| Igual por payload/`holders` con forma inválida | 3270-3278 | CIERRA | Entrada opaca |
| Igual por holder/port/PID inválido | 3279-3290 | CIERRA | Rechaza snapshot parcialmente corrupto |
| `(None, observed)` | 3291-3299 | CIERRA según datos | Nombres inválidos se normalizan a `None`; la atribución se cierra después |

### `_foreign_port_reason` — `tools/dayz_mcp/process_lifecycle.py:3301-3331`

| Return | Línea | Abre/cierra | Razón |
|---|---:|---|---|
| Error del scan o `port_scan_unknown` | 3311-3316 | CIERRA | Propaga indeterminación |
| `port_in_use_foreign` | 3322-3326 | CIERRA | Cualquier imagen DayZ; cualquier imagen en el puerto solicitado |
| `port_attribution_unknown` | 3327-3331 | CIERRA | Holder sin nombre/PID atribuible |
| `None` | 3331 | ABRE solo si explicable | Todos los holders son PIDs registrados o no bloquean el puerto solicitado |

El `continue` de PID registrado está en `tools/dayz_mcp/process_lifecycle.py:3318-3321`; no abre ante un proceso desconocido.

### `_diag_snapshot_empty` — `tools/dayz_mcp/process_lifecycle.py:3572-3596`

| Return | Línea | Abre/cierra | Razón |
|---|---:|---|---|
| `False, diag_snapshot_unknown` | 3573-3583 | CIERRA | Probe ausente, excepción o forma inválida |
| `False, manual_cleanup_required` | 3584-3585 | CIERRA | Hay proceso diag |
| `False, <port reason>` | 3586-3590 | CIERRA | Scan de puertos no fiable |
| `False, manual_cleanup_required` | 3591-3593 | CIERRA | Holder DayZ |
| `False, port_attribution_unknown` | 3594-3595 | CIERRA | Holder sin nombre |
| `True, ""` | 3596 | ABRE | Snapshot diag vacío y sin holder DayZ/innombrado |

La combinación `port_probe=None` puede llegar al último return y abrir; es exactamente B-2, expresamente no bloqueante en este encargo.

### `snapshot_udp_port_holders` — `tools/dayz_mcp/orphan_guard.py:482-522`

| Return | Línea | Abre/cierra | Razón |
|---|---:|---|---|
| `{"known": False, "holders": []}` | 498 | CIERRA downstream | Ni psutil ni netstat dieron snapshot válido |
| `{"known": True, "holders": rows}` | 508-522 | Depende de filas, de forma segura | PID/nombre no resoluble se conserva como `None`, no se elimina el puerto |

### Constructores productivos de `ProcessLifecycle`

El grep/AST del repo encontró un único constructor productivo: la llamada indirecta `bounded_io(ProcessLifecycle, ...)` en `tools/dayz_mcp/daemon.py:519-540`. Pasa `port_probe=orphan_guard.snapshot_udp_port_holders` en `tools/dayz_mcp/daemon.py:531-533`. Los demás constructores están en tests. Ningún constructor productivo omite `port_probe`.

Conclusión C-4: PASS respecto a la ronda 2; no hay nueva apertura silenciosa. Se mantiene B-2 como deuda documentada.

## C-5 — Cobertura de `_DAYZ_PORT_RANGE`

El rango vivo no es ya 2302-2320: es `range(2302, 3000)`, es decir, 2302-2999 (`tools/dayz_mcp/process_lifecycle.py:50-54`). Cubre la evidencia de uso habitual encontrada:

- Default 2302 en `tools/dayz_mcp/server.py:3174`, `tools/dayz_mcp/dayz_test_tool.py:128` y `tools/dayz_mcp/dayz_test_request.py:284`.
- 2402 en `tools/run-s0-gate.ps1:11`, `tools/spike0/mcp-grab-diag.ps1:13` y tests como `tools/tests/test_box_occupancy.py:79-84`.
- 2502 en `tools/tests/test_box_occupancy.py:304-314,2134-2153`.

Repro con holder no-DayZ `renamed.exe`: 2302, 2402, 2502 y 2999 aparecieron en `ports_in_use`; 3002 no apareció y la caja quedó `known/free`. No hay por tanto el MAJOR condicional pedido para los puertos habituales 2402/2502.

Sí existe una divergencia contractual: el request admite cualquier entero entre 1024 y 65530 en `tools/dayz_mcp/dayz_test_request.py:326-327`, mientras el diagnóstico de holders no-DayZ termina en 2999. En un request válido a 3002, `start_run` todavía bloquearía por holder en el puerto solicitado (`tools/dayz_mcp/process_lifecycle.py:3322-3326`), pero `peek_box` no publicaría ese puerto y `_failed_active_run_result` volvería a quedar sin la explicación M-2. No encontré evidencia de que el proyecto lance habitualmente en 3002 o superior, por lo que lo clasifico BACKLOG, no MAJOR.

Conclusión C-5: PASS para el uso habitual; hallazgo R2-B-1 para el dominio aceptado completo.

## HALLAZGOS

### R2-M-1 — MAJOR — Un run gestionado oculta el conflicto simultáneo del puerto solicitado

- Archivo/líneas: `tools/dayz_mcp/server.py:386-466,2715-2754`; test que fija la conducta en `tools/tests/test_box_port_wait.py:89-102`.
- Problema: la selección de diagnóstico es excluyente y retorna al primer run. Aunque `ports_in_use` contenga el puerto pedido sostenido por un proceso ajeno, el cliente recibe solo la receta de esperar/parar el run.
- Repro: run `run-existing` vivo en 2402 + `renamed.exe` en 2302 + request a 2302. Resultado observado: `active_run_exists`, `reason=null`, `port=null`, hint `retry with wait_for_box_s=<n>`; `wait_for_box_s=0.1` agotó el timeout sin poder cambiar el holder.
- Impacto: degradación funcional/diagnóstica y al menos un ciclo fallido adicional; la espera puede ser inútil mientras el holder persista.
- Fix sugerido `[DESIGN]`: componer los bloqueos. Mantener la instrucción relativa al run, pero, si el puerto solicitado figura en `ports_in_use`, añadir `port` y una segunda instrucción inequívoca: después de liberar la caja, ese puerto sigue/puede seguir ocupado y debe elegirse otro o terminarse su holder. Para afirmar `port_in_use_foreign` con precisión, preservar en el box la relación puerto/PID/clase o propagar el motivo exacto del rechazo de lifecycle; quitar sin más `not runs` puede confundir un puerto perteneciente al propio run gestionado con uno ajeno.
- Gate propuesto `[DESIGN]`: fixture combinado run-en-2402 + `renamed.exe`-en-2302, tanto con `wait_for_box_s=0` como `>0`; PASS solo si el resultado conserva la acción del run y además publica `port=2302` con una receta que no promete que esperar liberará el puerto.

### R2-B-1 — BACKLOG — El diagnóstico está limitado a 2302-2999 aunque la API acepta hasta 65530

- Archivo/líneas: `tools/dayz_mcp/process_lifecycle.py:50-54,3462-3474`; contrato en `tools/dayz_mcp/dayz_test_request.py:326-327`.
- Problema: un holder no-DayZ en un puerto solicitado válido fuera de la banda, reproducido en 3002, no aparece en `ports_in_use` de `peek_box`.
- Impacto: la protección pre-launch sigue cerrando, pero el diagnóstico vuelve a ser mudo para ese request válido.
- Fix sugerido `[DESIGN]` (preferido): hacer que la observación usada para construir el resultado incluya siempre el puerto concreto del request, en vez de depender solo de una banda global. Alternativas con trade-off: publicar todos los holders con límite explícito, o restringir el contrato de `port` a la banda realmente soportada.
- Gate propuesto `[DESIGN]`: para cada puerto aceptado de frontera elegido (por ejemplo 2302, 2999, 3002 y 65530), un holder no-DayZ en el puerto solicitado debe aparecer en el diagnóstico o el request debe ser rechazado como fuera de dominio antes de lifecycle.

## LO QUE NO PUDE VERIFICAR

- No lancé DayZ, el daemon, sockets de prueba ni procesos reales; toda mutación se limitó a `unittest` y fakes, y el inventario de sockets/ejecutables fue de solo lectura, como exigía el encargo.
- No ejercité `server.py:dayz_test_run` extremo a extremo a través del transporte MCP. C-2 se reprodujo con la ruta real `ProcessLifecycle.peek_box`/`execute_wait_for_box` y los builders de resultado, sustituyendo únicamente probes, reloj y sleep por fakes.
- No existe una lista oficial verificable de todos los nombres futuros/custom de servidor DayZ. Verifiqué los binarios presentes en las instalaciones locales y los cuatro nombres que reconoce el repo; un ejecutable renombrado sigue siendo un límite conocido, no una nueva regresión de ronda 2.
- No repetí la suite completa de 2628 tests porque el encargo limita la ejecución a tests con fakes y no verifiqué que toda la suite cumpla esa condición. `SUITE-D2.txt` registra `FAILED (failures=4, skipped=6)`, pero fue generado antes del último cambio de rango/test: dos fallos corresponden al estado 2302-2320 anterior y dos son hashes congelados de fases spawn ajenos a C-1..C-5. La suite focal nueva de 154 tests sí está verde.
- No modifiqué código productivo ni memoria externa del proyecto. El único archivo escrito es este informe.

Integridad al cierre: los SHA-256 de los siete archivos focales coincidieron antes y después de las pruebas con el snapshot inicial (`process_lifecycle.py A5489306…`, `orphan_guard.py 82902F24…`, `daemon.py 65AE92E9…`, `server.py CF0F3F62…`, `test_box_port_occupancy.py 5BC3F79A…`, `test_orphan_guard_udp.py B40AFC0…`, `test_box_port_wait.py 2A53AA83…`).
