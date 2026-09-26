## A - Ficheros creados/modificados

**Entrega de implementación y gate offline completada.** Receptor: Claude. Fecha del encargo: 2026-09-08. Antes = tamaño al empezar la lane; 0 en archivos nuevos.

| Ruta absoluta | Bytes antes -> después | Cambio |
|---|---:|---|
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\5_Mission\MCPBridge.c` | 80958 -> 81651 | G1 aviso una vez, G2 reserva antes del poll, G3 clamp de pollHz. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\5_Mission\MCPClientBridge.c` | 93901 -> 94351 | G3 clamp simétrico, G4 GetGame en restore, G5 Shutdown idempotente. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_guards_bridge.py` | 0 -> 10120 | 8 contratos fuente: seis casos nuevos y dos controles; selector BEFORE. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\guards-2026-09-08\baseline.json` | 0 -> 479 | Tamaños y SHA-256 originales de ambas fuentes. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\guards-2026-09-08\changes.json` | 0 -> 3311 | Reemplazos exactos independientes G1-G5. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\guards-2026-09-08\diff.log` | 0 -> 415 | Diff stat/check literal de ambas fuentes y sus códigos. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\guards-2026-09-08\final-check.log` | 0 -> 1250 | Relectura/hashes y consolidación del gate offline. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\guards-2026-09-08\green.log` | 0 -> 179740 | Verde y dos regresiones completas; conserva helper exit 5 inicial. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\guards-2026-09-08\independence.json` | 0 -> 853 | Casos que fallan por guard retirado; cero errores de harness. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\guards-2026-09-08\independence.log` | 0 -> 211863 | Salida de las cinco reversiones en memoria. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\guards-2026-09-08\MCPBridge.c.BEFORE` | 0 -> 80958 | Copia original byte a byte del servidor antes de editar. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\guards-2026-09-08\MCPClientBridge.c.BEFORE` | 0 -> 93901 | Copia original byte a byte del cliente antes de editar. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\guards-2026-09-08\modules.txt` | 0 -> 974 | Censo original de 32 Python: 31 tests y un helper. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\guards-2026-09-08\red.log` | 0 -> 205559 | Salida original completa: 8 tests, seis fallos esperados, exit 1. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\guards-2026-09-08\run_gate.py` | 0 -> 2482 | Runner secuencial con logs y relectura; filtra el helper. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\guards-2026-09-08\runs.json` | 0 -> 43619 | Comandos, resúmenes y exit codes de cada invocación. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\guards-2026-09-08\verify_independent.py` | 0 -> 2698 | Revierte cada guard en memoria y exige solo sus fallos. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\guards-2026-09-08\STATE.md` | 0 -> 25255 | Informe A-E escrito por tramos; evidencia, decisiones y límites. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\guards-2026-09-08\DONE` | 0 -> 0 | Marcador vacío de entrega verificada. |

BEFORE intactos. Cada escritura propia se releyó y comparó byte a byte desde host; hashes fuente en baseline.json/final-check.log.

## B - Resultado de las pruebas

Gate offline: **388 pruebas verdes en 32 módulos reales** (380 en los 31 preexistentes + 8 nuevas). No es la suite completa ni compilación Enforce.

Comando literal del rojo, ejecutado con cwd en la raíz del repo:
```powershell
& 'tools\.venv-mcp\Scripts\python.exe' 'reviews\guards-2026-09-08\run_gate.py' red
```
Comando hijo reproducible y salida literal (el runner fija cwd y entorno explícitos):
```powershell
Set-Location -LiteralPath 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools'
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:GUARDS_SOURCE_DIR = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\guards-2026-09-08'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_guards_bridge -v
Ran 8 tests in 0.046s
FAILED (failures=6)
EXIT CODE: 1
```

Los seis fallos son G1, G2, G3 cliente, G3 servidor, G4 y G5; los dos controles existentes pasan. Se leen directamente MCPBridge.c.BEFORE y MCPClientBridge.c.BEFORE: no se reemplazó la fuente compartida. Salida completa con trazas: red.log.

```powershell
& 'tools\.venv-mcp\Scripts\python.exe' 'reviews\guards-2026-09-08\run_gate.py' green
```
Comando hijo y salida literal:
```powershell
Set-Location -LiteralPath 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools'
$env:PYTHONPATH = 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:GUARDS_SOURCE_DIR -ErrorAction SilentlyContinue
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_guards_bridge -v
Ran 8 tests in 0.064s
OK
EXIT CODE: 0
```

Regresión nominal, secuencial, ejecutada dos veces. La primera invocación incluyó el helper encontrado por la búsqueda; salió 1 exclusivamente por el helper. El runner se corrigió para seleccionar tests.test_* y la segunda invocación terminó con exit 0:
```powershell
& 'tools\.venv-mcp\Scripts\python.exe' 'reviews\guards-2026-09-08\run_gate.py' regression
```

Corrección del censo, conservando la salida inicial:
```text
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests._addon_paths -v
Ran 0 tests in 0.000s
NO TESTS RAN
EXIT CODE: 5
```

Los 31 módulos que sí contienen tests, comandos hijos exactos representados para PowerShell y resumen literal de la última ejecución (mismo cwd/entorno que el verde anterior):
```powershell
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_action_use -v
Ran 3 tests in 0.108s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_addon_tree_has_no_write_artifacts -v
Ran 5 tests in 0.002s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_batch6 -v
Ran 13 tests in 0.213s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_bridge_client_capabilities -v
Ran 8 tests in 0.003s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_bridge_server_capabilities -v
Ran 9 tests in 0.024s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_d09_d10_spawn_timeout_object_id -v
Ran 2 tests in 0.025s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_effective_schema_catalog -v
Ran 5 tests in 0.002s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_entities_has_cargo -v
Ran 5 tests in 0.006s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_entities_query_cargo -v
Ran 12 tests in 0.408s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_instance_fence -v
Ran 64 tests in 2.098s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_inventory_give -v
Ran 5 tests in 0.106s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_key_press -v
Ran 6 tests in 0.200s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_loopback -v
Ran 74 tests in 0.943s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_mcp_tools -v
Ran 51 tests in 22.735s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_object_anim -v
Ran 4 tests in 0.141s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_object_inspect -v
Ran 4 tests in 0.141s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_player_respawn -v
Ran 4 tests in 0.091s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_player_teleport -v
Ran 9 tests in 0.474s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_poll_key_reload_contract -v
Ran 2 tests in 0.001s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_poll_watchdog_contract -v
Ran 8 tests in 0.003s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_restore_gameplay_contract -v
Ran 12 tests in 0.632s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_surface_query -v
Ran 4 tests in 0.094s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_task9_spawn_phase_markers -v
Ran 7 tests in 0.001s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_ui_click_scriptview -v
Ran 6 tests in 0.002s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_ui_enforce_contract -v
Ran 15 tests in 0.008s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_ui_error_diagnostics -v
Ran 10 tests in 0.640s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_ui_reload_layout -v
Ran 10 tests in 0.094s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_vehicle_prepare_fixture -v
Ran 4 tests in 0.096s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_vehicle_telemetry_contract -v
Ran 10 tests in 0.027s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_vehicle_trace_contract -v
Ran 7 tests in 0.112s
OK
EXIT CODE: 0

& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_world_spawn_ground_contract -v
Ran 2 tests in 0.001s
OK
EXIT CODE: 0
```

green.log conserva ambas ejecuciones completas y runs.json sus comandos, resúmenes y códigos. modules.txt conserva el censo original de 32 ficheros; tras añadir test_guards_bridge.py hay 32 módulos de tests y un helper.

Independencia: se reconstruye cada variante en memoria desde la fuente final y changes.json; no se escriben variantes sobre los .c compartidos.
```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'tools\.venv-mcp\Scripts\python.exe' 'reviews\guards-2026-09-08\verify_independent.py'
G1: 8 tests, 1 expected failures, 7 pass; other guards remain green
G2: 8 tests, 1 expected failures, 7 pass; other guards remain green
G3: 8 tests, 2 expected failures, 6 pass; other guards remain green
G4: 8 tests, 1 expected failures, 7 pass; other guards remain green
G5: 8 tests, 1 expected failures, 7 pass; other guards remain green
INDEPENDENCE PASS=5 FAIL=0; shared sources unchanged; EXIT CODE: 0
```

Diff literal (diff.log):
```text
git diff --stat -- addon/scripts/5_Mission/MCPBridge.c addon/scripts/5_Mission/MCPClientBridge.c
 addon/scripts/5_Mission/MCPBridge.c       | 21 +++++++++++++++++++++
 addon/scripts/5_Mission/MCPClientBridge.c | 23 +++++++++++++++++++++--
 2 files changed, 42 insertions(+), 2 deletions(-)
EXIT CODE: 0

git diff --check -- addon/scripts/5_Mission/MCPBridge.c addon/scripts/5_Mission/MCPClientBridge.c
EXIT CODE: 0
```

final-check.log recoge hashes, reproducción exacta del diff desde los BEFORE y consolidación del gate; exit 0. Cada comando hijo se invocó por argv de subprocess con el intérprete pedido, sin discovery global.

## C - Hallazgos y decisiones

**Implementados G1-G5, sin refactor N3.** Cambios exclusivamente en las dos fuentes autorizadas, el test nuevo y este directorio. Sin commit ni git add por prohibición expresa del brief; decisions/decision-log.md sigue staged como entrada ajena. Sin modificación del protocolo ni MCP_BRIDGE_VERSION, tools/dayz_mcp/, launcher, daemon vivo o juego. Ningún archivo modificado pertenece a PACKAGED_MODULES (tools/build_native_launcher.py:53-72, abierto); no hace falta resellado nativo por estos cambios. El PBO y su despliegue quedan para el receptor.

Citas de addon relativas a C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev; vanilla desde C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts.

1. **G1 — aviso acotado, servidor.** MCPBridge.c:3450 conserva el retorno sin transporte y añade un único aviso por instancia con el id; latch en :57, inicializado una vez en :82. No se rearma en el tick. No reintenta el resultado: hace visible el descarte. Log ya existía; no hay API nueva.

2. **G2 — hay poda; se limita la admisión.** El original tenía ReleaseCallback en MCPBridge.c.BEFORE:3478-3489; MCPCallbacks.c:14,23,32,51,60,69 lo llama en éxito, error y timeout. No se acredita fuga permanente. El riesgo es acumulación con POST lentos/sin callback. StartPoll, MCPBridge.c:234, ahora reserva un lote de 64 dentro de un techo de 128 usando callbacks + pendientes + jobs. Productor leído: tools/dayz_mcp/loopback.py:156 MAX_QUEUE=64; :1390-1403 suma las colas del peer; :1813-1823, :1891 y :1928 aplican capacidad; :2237 y :2333 generan el lote. Con 64 obligaciones todavía cabe otro lote de 64; con 65 se aplaza otro poll. El GET ocupa una referencia que el callback libera antes de despachar el lote. Cada comando normal produce un resultado inmediato o un job y luego un resultado. ProcessJobs y DrainPending continúan antes de StartPoll; PostResult no tiene un techo que descarte resultados. No hay cola nueva ni log de saturación. ACK/error/timeout quitan referencias y permiten reanudar. El test recorre 512 combinaciones de callbacks/pendientes/jobs y lee la capacidad del productor por AST. La garantía depende de ese productor y los callbacks actuales; no cubre JSON arbitrario de otro servidor.

3. **G3 — clamp simétrico a 60.0 Hz.** MCPBridge.c:203 y MCPClientBridge.c:376. Se conserva cfg.pollHz positivo hasta 60; por encima se limita, no se rechaza la configuración. Los no positivos/NaN siguen sin entrar en la rama positiva y conservan el valor anterior (5 en una instancia nueva). +Infinito, si el parser lo admite, se limita por la comparación; no se afirma que JsonFileLoader acepte NaN/infinito. 60 corresponde al tick nominal del brief; el productor normal escribe 5 (loopback.py:967). No se usa Math ni una API finite inventada.

4. **G4 — guard del juego.** MCPClientBridge.c:3915 comprueba GetGame antes de las dos desreferencias. El player ya estaba comprobado. ../scripts/3_game/gameplay.c:636 define GetGame devolviendo g_Game, fuera de ifdef; ../scripts/3_game/global/game.c:72-82 destruye CGame y pone g_Game a null. Las llamadas ordinarias pasan por juego activo (IsClientInGame en MCPClientBridge.c:663); el destructor entra por Shutdown sin ese gate. Decisión conservadora: singleton/refcounts permiten cleanup durante el cierre global y la fuente leída no garantiza su orden respecto a CGame. **[HIPÓTESIS]** ese orden tardío concreto, no reproducido; sí está verificado el mecanismo que hace nulo GetGame. No se presenta como incidente observado ni protección total del teardown.

5. **G5 — dos entradas no equivalen a ticks posteriores.** Fuentes activas: addon/scripts/5_Mission/MissionGameplay.c:3-6 -> MCPClientBridge.ShutdownInstance; MCPClientBridge.c:254 llama Shutdown y después libera m_Instance; el destructor en :239 vuelve a llamar Shutdown. No se encontró tercer llamante en addon. El primer cierre desengancha callbacks que mantenían refs fuertes al bridge. La destrucción al liberar la última referencia está documentada por Bohemia en [Enforce Script Syntax](https://community.bistudio.com/wiki/DayZ%3AEnforce_Script_Syntax), consultado en esta sesión. La invariante sobre ausencia de ticks posteriores se sostiene en el árbol activo, pero **no prueba unicidad de Shutdown**. La segunda pasada parte de postedTerminal=false y repite Clear de referencias y cleanup global; MCPCarDrive.Clear (addon/scripts/4_World/MCP_CarScript.c:22-26) y MCPVehicleTrace.Abort (:326-334) operan estado estático. Se añade latch al comienzo, MCPClientBridge.c:4070, manteniendo m_Configured hasta después del primer PostUiDialogJob y la rama que evita reset tras terminal. No hay guard nuevo en OnTick ni PostResult. La duración real del callback después de destruir la instancia sigue dependiendo del motor.

**Mapa cliente/servidor y APIs.** G1-G2: estado local de transporte servidor. G3: configuración local en ambos. G4-G5: cleanup local cliente. Sin SyncVars ni RPC nuevos. Count de array/map verificados en ../scripts/1_core/proto/enscript.c:380 y :833; Log existente en ambas fuentes. No se introdujo método nativo. Condiciones y llamadas nuevas en una sola línea. Las unidades están separadas en changes.json y la reversión discrimina cada una.

**Correcciones al brief y al contexto.** Insert de G2 está en BEFORE:3451, no 3452; ya existe poda. Los “32 módulos” eran 31 módulos y un helper; el nuevo completa 32 módulos reales. El plan leído sí separa unificación (N1 del plan) de N3 de guards y menciona ambos llamantes/riesgos concretos (reviews/audit-plan-2026-09-08/PLAN.md:118,132-140); no era únicamente rechazo del refactor. El brief actual aprueba esta intervención más estrecha. dayz-harness-apis.md:25 dice que GetGame es engine-injected sin definición; gameplay.c:636 actual lo contradice. P: no está disponible; vanilla se leyó desde ../scripts. El validador de la skill no existe en tools/dayz-script-validator/scripts/script_validator.py (Test-Path: False). Una tentativa de generar el auxiliar del informe falló por comillas anidadas antes de escribir archivo alguno; se sustituyó por generación directa, relectura y comprobación de tamaño. Las fuentes no cambiaron tras los gates.

**In-game conjunto para el receptor, sin solicitar otra ejecución en esta sesión:**
- Cargar ambos bridges en Enforce; polling normal a 5 Hz, límite con configuración excesiva y herramientas ordinarias.
- Ráfaga con POST lentos: cota de referencias bajo el productor actual; jobs/pending terminan, resultado por comando aceptado; reanudación tras ACK/error/timeout sin spam ni expiraciones nuevas para la carga prevista.
- Desconectar con diálogo abierto: único terminal y sin reset que lo cancele; cambiar misión sin repetir cleanup global.
- Restaurar simulación, controles y HUD; salir/cambiar misión sin referencia nula en RestoreGameplay.
- G1: repetir intentos de resultado sin transporte; un aviso por instancia.

**Memoria:** este STATE es el handoff autorizado para Claude. Vault, índice de sesiones, HANDOFF.md, bug-ledger y buzón pipeline_feedback son **FUERA DE MI ALCANCE** por la lista exclusiva del brief. No se escribieron. La corrección de GetGame/censo queda aquí para que el receptor la incorpore. El LIVE-STATE anterior no se retomó como otra tarea.

## D - QUE PUEDE ESTAR MAL EN LA PREMISA DE ESTE ENCARGO

- G1 prueba una rama silenciosa, no una pérdida alcanzable en sesión sana. El arreglo aporta diagnóstico; no entrega ni reintenta el resultado descartado.
- G2 no era una lista sin poda. El techo exige contar obligaciones y reservar el lote del productor; limitar Insert rompería resultados ya calculados. MAX_QUEUE=64 es una dependencia verificada y vigilada por test, pero no una cota frente a JSON arbitrario ni retries nativos distintos de los caminos leídos. 128 permite dos lotes máximos; es una decisión conservadora, no un presupuesto de rendimiento medido.
- Cualquier techo efectivo aplaza admisiones bajo saturación. Si “no cambiar comportamiento observable” exige igualdad de latencia en toda carga posible, es incompatible con una cota efectiva. Se conserva despacho/terminal de lo ya aceptado y no se crea error wire; no se acredita equivalencia de throughput ni ausencia de nuevas expiraciones en cargas extremas. Eso queda como validación pendiente, no como garantía de los tests fuente.
- G3 ya tenía límite práctico: un poll en vuelo y una llamada a StartPoll por tick. pollHz grande no creaba múltiples GET dentro de un mismo tick. El clamp sanea configuración; no prueba ahorro de CPU ni un fallo real previo.
- G4 es el punto con menor certeza de alcanzabilidad. CGame anula g_Game y existe la entrada de destructor, pero falta el orden nativo exacto. Si el motor garantiza destruir siempre este singleton antes de CGame, el guard sería redundante. No se dedujo esa garantía de un comentario sobre OnTick; se explicita la hipótesis y se adopta retorno conservador de cleanup.
- G5 tiene una cadena concreta de segunda entrada. El comentario original hablaba solo de futuros ticks. El latch corrige esa diferencia, pero no demuestra que el terminal sobreviva a la destrucción de todos los campos por ARC. No se declara cerrado ese comportamiento nativo con tests de texto.
- El argumento de guards simplemente enterrados en un refactor omite que N3 sí documentaba riesgos de backpressure y terminales. Aquí se interviene de forma acotada; la equivalencia de runtime sigue sin cerrarse offline.

## E - LO QUE NO PUDE VERIFICAR

- Compilación/carga Enforce: sin compilador/linter en este árbol; script_validator tampoco existe en la ruta anunciada. Los tests Python inspeccionan fuente, no el compilador de DayZ.
- Juego, daemon vivo, lease/session_status y REST en el motor: **prohibidos por este brief**. No se usaron tools DayZ MCP, no se arrancó/reinició producción ni se ocupó 8765.
- In-game conjunto, tráfico lento real, latencia/throughput bajo saturación, todos los null dereference y supervivencia del terminal tras ARC: requieren motor, **prohibido por este brief**; lista para el receptor en C.
- Orden ~CGame / ~MissionGameplay / ~MCPClientBridge al salir: nativo y no demostrado por las fuentes. G4 conserva hipótesis explícita, sin reproducer de juego.
- Techo G2 frente a productor que ignore MAX_QUEUE o callbacks nativos repetidos: no cubierto. La cota presupone productor actual y resultado por comando; no se cambió protocolo/transporte para imponer otra relación.
- Teardown completo con g_Game nulo: G4 cubre RestoreGameplay. DeleteOwnedCamera usa g_Game.ObjectDelete si hay cámara propia (MCPClientBridge.c:3965); no se amplió a corregir todo el cleanup.
- Suite completa: **prohibida por este brief** por contención. Solo módulos nombrados; corrida global en serie pendiente del receptor.
- Despliegue/PBO y hashes de artefactos cargados: **fuera de esta ejecución por el brief**; ninguna fuente nueva se desplegó, empaquetó o probó en juego.
- Revisión independiente de Claude: pendiente del receptor. **Subagentes/fan-out prohibidos por este brief**; no se simula otra familia revisora.
- Memoria del vault/buzón/índices y commit: **FUERA DE MI ALCANCE por este brief**; entrega durable aquí, sin git add ni commit. Gate offline no equivale a aprobación de release.
