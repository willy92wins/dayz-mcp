# Revisión Codex — lote B

## VEREDICTO

**BLOQUEANTES=0.** El diff es correcto y las tres descripciones son veraces contra los caminos de código revisados. Puede commitearse tal cual.

Provenance comprobada: HEAD `2d069d2`, rama `work/inbox-20260830-modules`. `DIFF-B.patch` y el diff vivo de los dos ficheros coinciden línea por línea (`PATCH_MATCH=True`, 121/121 líneas). Estado final del diff acotado:

```text
 tools/dayz_mcp/server.py     | 23 +++++++++++++++---
 tools/tests/test_wait_for.py | 55 ++++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 75 insertions(+), 3 deletions(-)
20  3  tools/dayz_mcp/server.py
55  0  tools/tests/test_wait_for.py
```

`git diff --check -- tools/dayz_mcp/server.py tools/tests/test_wait_for.py` no produjo salida.

## B-1 — test fb-20260829-025502-251d

Ejecutado desde `tools/` con el intérprete y entorno exigidos:

```text
$env:PYTHONPATH='.'; $env:PYTHONDONTWRITEBYTECODE='1'
.\.venv-mcp\Scripts\python.exe -m unittest tests.test_wait_for -v
...
Ran 22 tests in 9.518s

OK
```

El test nuevo construye un único `script.log`, añade cinco líneas de boot y una respuesta durable, calcula SHA-256, hace las dos llamadas y vuelve a calcular SHA-256 en `tools/tests/test_wait_for.py:521-553`. Sus asserts fijan default satisfecho, cero vencido y `probes >= 1` en `tools/tests/test_wait_for.py:555-563`.

Repro adicional del mismo escenario, imprimiendo el estado que el unittest normalmente no muestra:

```text
sha_before= f31c1dec3c69cc859282614aba523bcf247674ba89fe67cc96a1f45d705a0078
sha_between= f31c1dec3c69cc859282614aba523bcf247674ba89fe67cc96a1f45d705a0078
sha_after= f31c1dec3c69cc859282614aba523bcf247674ba89fe67cc96a1f45d705a0078
same_bytes= True size= 85 lines= 6
default= {'ok': True, 'satisfied': True, 'timed_out': False, 'probes': 1}
zero= {'ok': True, 'satisfied': False, 'timed_out': True, 'probes': 3}
default_observed_contains_needle= True
```

Mutante obligatorio [EXACT]: cambié solo el default de `execute_wait_for` en `tools/dayz_mcp/server.py:2345`, de `200` a `0`, manteniendo intacto el default de la tool pública en `tools/dayz_mcp/server.py:4582`. El módulo se puso rojo:

```text
FAIL: test_response_durable_before_the_marker_is_still_seen
AssertionError: False is not true

FAIL: test_same_bytes_default_sees_and_zero_misses
AssertionError: False is not true

Ran 22 tests in 11.776s
FAILED (failures=2)
```

Restauré el literal a mano. SHA-256 de `server.py` antes del mutante y después de restaurarlo: `473820D1793958FBFA745863EEE6275C1DA131324B5502FAF8F1CD34A59135B7`. La prueba nueva volvió a `OK` tras la restauración.

Conclusión: no es tautológica. El mutante elimina exactamente la propiedad que pretende fijar y mata el test nuevo; también mata el test previo del brazo default. La segunda llamada hace tres sondas, no un cortocircuito.

## B-2 — `entities_query`

Lectura del camino real:

- La tool valida `pos` y lo copia sin transformación a `args`, luego llama al bridge con esos mismos args: `tools/dayz_mcp/server.py:3837-3859`.
- El bridge valida las tres coordenadas y reconstruye exactamente `Vector(x, y, z)`, sin caso especial para Y=0: `addon/scripts/5_Mission/MCPBridge.c:2576-2605`.
- `DispatchEntitiesQuery` entrega ese `validation.pos` directamente a `GetObjectsAtPosition3D`: `addon/scripts/5_Mission/MCPBridge.c:1382-1409`.
- En contraste, `player_teleport` documenta el contrato Y=0 en `tools/dayz_mcp/server.py:3640-3649`, pasa el vector al bridge en `tools/dayz_mcp/server.py:3664-3672`, y el bridge sustituye expresamente `position[1]` por `GetGame().SurfaceY(...)` en `addon/scripts/5_Mission/MCPBridge.c:1125-1151`.

Conclusión: la descripción nueva de `entities_query` en `tools/dayz_mcp/server.py:3828-3834` es correcta. `entities_query` no ajusta Y; `player_teleport` sí lo hace cuando Y==0.

## B-3 — `logs_since`

Lectura del mecanismo:

- La docstring de `read_since` fija el contrato en `tools/dayz_mcp/log_tail.py:149-151`.
- Sin marker, el inicio es 0; el cap corta por líneas completas; `consumed = start + len(complete)` y ese valor pasa a `TailMarker.offset`: `tools/dayz_mcp/log_tail.py:172-180`, `:189-213`.
- `logs_since` mantiene un presupuesto `remaining`, pasa ese límite a `read_since`, guarda el marker devuelto y descuenta solo `len(result["lines"])`: `tools/dayz_mcp/server.py:3298-3318`.

Repro barato [EXACT] con diez líneas:

```text
first.lines= ['line-0']
first.offset= 7 expected= 7 size= 70 eof= 70
drain[1].lines=10 offset=70 size=70
drain[2].lines=0 offset=70 size=70
marker_at_eof= True
```

El primer marker queda tras la primera línea, no en EOF. Una lectura grande consume todo; la llamada siguiente devuelve cero líneas conservando offset==EOF. Por tanto, la receta de “leer hasta cero y conservar ese marker” es cierta.

Perfiles y formatos: `_sibling_profile_dirs` añade el hermano `_client`/`_server` en `tools/dayz_mcp/server.py:1843-1857`; `resolve_log_files` acepta `.rpt` y `.log` en `tools/dayz_mcp/log_tail.py:20`, `:121-128`; `_current_launch_logs` conserva el RPT y el log de script más nuevos en `tools/dayz_mcp/server.py:1895-1908`, `:1981-2004`. El probe del bridge emite mediante `Print` en `addon/scripts/5_Mission/MCPBridge.c:3553-3556`, es decir, por el canal de script log que la tool sí lee.

Conclusión: el texto nuevo en `tools/dayz_mcp/server.py:3230-3245` coincide con el mecanismo y no está mal colocado.

## B-4 — `capture_screenshot`

Lectura estática del camino de captura, sin ejecutar DayZ:

- `mcp_capture.py` fija `tools/mcp-grab.ps1` como backend canónico en `tools/mcp_capture.py:83-87` y lo lanza en un proceso PowerShell en `tools/mcp_capture.py:477-523`.
- Ese proceso declara `SetProcessDPIAware` y `SetProcessDpiAwareness`, e intenta primero `SetProcessDpiAwareness(2)` (per-monitor aware): `tools/mcp-grab.ps1:63-71`.
- La declaración se ejecuta antes de enumerar ventanas: `tools/mcp-grab.ps1:229-231`.
- Después obtiene `GetWindowRect`, `GetClientRect` y `ClientToScreen` en `tools/mcp-grab.ps1:88-97`, construye ambos rects y los emite junto a la captura en `tools/mcp-grab.ps1:273-303`.
- `mcp_capture.py` publica esos datos como `window_surface`/`client_surface` en `tools/mcp_capture.py:630-667`, `:706-722`.

Conclusión: la afirmación de píxeles físicos tiene respaldo en el proceso que realmente llama a Win32. No es necesario que el servidor Python padre declare DPI awareness: las coordenadas se obtienen dentro del helper PowerShell, y ese helper se declara DPI-aware antes de leerlas. El texto nuevo en `tools/dayz_mcp/server.py:4092-4103` no contradice el código.

## B-5 — wire y suites

Ejecuté una sesión en memoria con `create_connected_server_and_client_session(app._mcp_server)` y `session.list_tools()` sobre:

```python
app, runtime = server.build_app(
    server.ServerConfig(key="k", port=0, log_sink=lambda _m: None)
)
```

Salida literal recortada:

```text
Processing request of type ListToolsRequest
tool_count= 60
entities_query missing= []
logs_since missing= []
capture_screenshot missing= []
```

Los fragmentos comprobados fueron los nuevos contratos de Y, marker/EOF/script log y píxeles físicos/DPI; los tres llegaron completos por el wire.

Suite B-5 exacta:

```text
.\.venv-mcp\Scripts\python.exe -m unittest \
  tests.test_docs_truth tests.test_mcp_tools tests.test_lote_v_products \
  tests.test_effective_schema_catalog tests.test_tool_registry_fingerprint -v
...
Ran 96 tests in 9.251s
OK (skipped=3)
```

Corrida final conjunta de B-1 y B-5, ya con el mutante restaurado:

```text
Ran 118 tests in 19.325s
OK (skipped=3)
```

Conclusión: wire y tests están verdes. El recuento real del árbol revisado no coincide con la referencia “119 OK, 2 skips”: el loader enumera 20 + 45 + 6 + 5 + 20 = 96 casos en los cinco módulos de B-5. Los tres skips observados son dos checks de sparse-addon y uno por ausencia de `P:\scripts`.

## BLOQUEANTES

Ninguno.

## BACKLOG

- Reconciliar el recuento de referencia “119 OK, 2 skips” con el inventario actual “96 tests, 3 skips” de B-5. No bloquea este diff: los 96 casos existentes se ejecutaron y todos los no saltados pasaron.
- Añadir asserts permanentes de los fragmentos semánticos nuevos en una prueba de wire dedicada. El probe ad hoc los acredita hoy y el fingerprint sigue verde, pero un test directo daría un fallo más explicativo ante una regresión de texto.
- Endurecimiento opcional: `MCPGrab.DpiAware()` ignora el HRESULT de `SetProcessDpiAwareness(2)` y solo usa el fallback si hay excepción (`tools/mcp-grab.ps1:70-71`). Comprobar el HRESULT y publicar un error tipado permitiría fallar cerrado si un host futuro no acepta la declaración. No contradice el comportamiento actual ni el criterio de este lote.

## LO QUE NO PUDE VERIFICAR

- No ejecuté DayZ, daemon, red ni una captura Win32 real, por frontera expresa. B-4 queda verificado contra el camino estático que ejecuta el helper y declara DPI awareness, no contra una observación visual de esta sesión.
- No pude ejecutar `test_symbol_line_citations_resolve` porque `P:\scripts` no está presente en este entorno; unittest lo marcó `skipped`, no `failed`.
- No pude explicar con evidencia disponible por qué el recuento previo era 119/2 y el árbol vivo enumera 96/3 en B-5; dejé la divergencia en BACKLOG y no la convertí en fallo funcional.
- No actualicé memoria del vault: el único hecho nuevo es el dictamen acotado de un diff aún sin commit y queda durable en este fichero; tocar memoria de proyecto habría excedido las fronteras del encargo.
