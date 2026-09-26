## VEREDICTO
BLOQUEANTES=1.

## BLOQUEANTES

### B-01 — P2: una excepción de `evaluate_steam_session()` rompe el contrato de resultado estructurado

- **Producto roto:** P2.
- **Path:line:** `tools/dayz_mcp/dayz_test_tool.py:714-716` invoca el evaluador sin contener sus excepciones; `tools/dayz_mcp/server.py:2985-2991` transforma la excepción escapada en un `ToolError` de la fachada.
- **Problema:** en una ejecución cliente real de la tool, si `evaluate_steam_session()` lanza en vez de devolver `SteamSessionResult`, la llamada no devuelve el sobre fallido de 17 claves. FastMCP lanza `ToolError: ...dayz_test_failed:RuntimeError`. Por tanto el consumidor no puede ramificar por `status`/`error_code`, que es precisamente la propiedad observable de P2. La ejecución del launcher no llega a comenzar.
- **Fix sugerido:** contener las excepciones ordinarias de esa llamada en `dayz_test_tool.py` y proyectarlas, fail-closed, mediante `_compact_result` con el mismo conjunto de claves que las demás filas. El cambio debe conservar el precheck exclusivamente en `not preflight and mode in {"client", "all"}`. No propongo ningún refactor adyacente.
- **Comando de repro, literal y copiable:**

```powershell
Set-Location -LiteralPath 'C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\938664ff-361c-4575-aa56-c7c06c7ca2a8\scratchpad\lote-D\ws\tools'
$env:PYTHONPATH='.'
@'
import unittest
from unittest.mock import AsyncMock, patch
from dayz_mcp import server, dayz_test_tool
from tests.test_client_mode import _fixture_client_runtime
from tests.test_dayz_test_tool import _policy, _sealed, _Opened, _Bundle, RUN_ID
from tests.test_mcp_tools import _content_json

class Repro(unittest.IsolatedAsyncioTestCase):
    async def test_dayz_test_run_returns_structured_failure_if_steam_evaluator_raises(self):
        config = server.ServerConfig(mode='client', key='k', port=12345, client_platform='codex', log_sink=lambda _m: None)
        runtime = _fixture_client_runtime(config)
        with patch.object(server, 'ClientRuntime', return_value=runtime):
            app, _ = server.build_app(config)
        lifecycle = {'runs':[{'run_id':RUN_ID,'state':'RUNNING_IDLE','mod':'@ExampleMod','processes':[]}]}
        with patch.object(dayz_test_tool, '_require_idle_session', new=AsyncMock()), patch.object(runtime, 'lifecycle_status', new=AsyncMock(return_value=lifecycle)), patch.object(dayz_test_tool, 'open_approved_launcher', return_value=_Opened()), patch.object(dayz_test_tool.secure_launcher, 'load_verified_bundle', return_value=_Bundle(_sealed(_policy()))), patch.object(dayz_test_tool.secure_launcher, 'execute_secure_launcher_request', new=AsyncMock()), patch.object(dayz_test_tool, 'evaluate_steam_session', side_effect=RuntimeError('probe')):
            raw = await app.call_tool('dayz_test_run', {'project':'ExampleMod','mode':'client','run_id':RUN_ID,'extra_mods':['@DayZ_MCP']})
        result = _content_json(raw)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'steam_session_stale')

unittest.main()
'@ | & 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -
```

- **Salida que da hoy:**

```text
E
======================================================================
ERROR: test_dayz_test_run_returns_structured_failure_if_steam_evaluator_raises (__main__.Repro.test_dayz_test_run_returns_structured_failure_if_steam_evaluator_raises)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\938664ff-361c-4575-aa56-c7c06c7ca2a8\scratchpad\lote-D\ws\tools\dayz_mcp\server.py", line 2959, in dayz_test_run
    result = await dayz_test_tool.execute_dayz_test_run(
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    ...<20 lines>...
    )
    ^
  File "C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\938664ff-361c-4575-aa56-c7c06c7ca2a8\scratchpad\lote-D\ws\tools\dayz_mcp\dayz_test_tool.py", line 715, in execute_dayz_test_run
    steam = evaluate_steam_session()
  File "C:\Python314\Lib\unittest\mock.py", line 1175, in __call__
    return self._mock_call(*args, **kwargs)
           ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^
  File "C:\Python314\Lib\unittest\mock.py", line 1179, in _mock_call
    return self._execute_mock_call(*args, **kwargs)
           ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^
  File "C:\Python314\Lib\unittest\mock.py", line 1234, in _execute_mock_call
    raise effect
RuntimeError: probe

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Lib\site-packages\mcp\server\fastmcp\tools\base.py", line 101, in run
    result = await self.fn_metadata.call_fn_with_arg_validation(
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    ...<4 lines>...
    )
    ^
  File "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Lib\site-packages\mcp\server\fastmcp\utilities\func_metadata.py", line 94, in call_fn_with_arg_validation
    return await fn(**arguments_parsed_dict)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\938664ff-361c-4575-aa56-c7c06c7ca2a8\scratchpad\lote-D\ws\tools\dayz_mcp\server.py", line 2991, in dayz_test_run
    raise ToolError(f"dayz_test_failed:{type(exc).__name__}") from exc
mcp.server.fastmcp.exceptions.ToolError: dayz_test_failed:RuntimeError

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "C:\Python314\Lib\asyncio\runners.py", line 127, in run
    return self._loop.run_until_complete(task)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^
  File "C:\Python314\Lib\asyncio\base_events.py", line 719, in run_until_complete
    return future.result()
           ~~~~~~~~~~~~~^^
  File "<stdin>", line 16, in test_dayz_test_run_returns_structured_failure_if_steam_evaluator_raises
  File "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Lib\site-packages\mcp\server\fastmcp\server.py", line 346, in call_tool
    return await self._tool_manager.call_tool(name, arguments, context=context, convert_result=True)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Lib\site-packages\mcp\server\fastmcp\tools\tool_manager.py", line 93, in call_tool
    return await tool.run(arguments, context=context, convert_result=convert_result)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Lib\site-packages\mcp\server\fastmcp\tools\base.py", line 117, in run
    raise ToolError(f"Error executing tool {self.name}: {e}") from e
mcp.server.fastmcp.exceptions.ToolError: Error executing tool dayz_test_run: dayz_test_failed:RuntimeError

----------------------------------------------------------------------
Ran 1 test in 0.088s

FAILED (errors=1)
```

## BACKLOG

- **P1, snapshot y localidad: conforme.** La captura se construye una sola vez al cierre de `build_app` (`tools/dayz_mcp/server.py:4508-4510`) y `bridge_status` sólo superpone la copia congelada (`tools/dayz_mcp/server.py:2736-2747,4043-4048`). Un probe sobre la fachada observó `capture_calls_during_build=1`, fingerprint y timestamp idénticos en dos llamadas, y ninguna recaptura en la segunda. Añadir una tool después del cierre cambió el recálculo local de 60 a 61 tools, pero no el fingerprint publicado. No es bloqueante.
- **P1, no filtración: conforme.** La búsqueda de los cuatro nombres sólo encontró la publicación de producción en `tools/dayz_mcp/server.py:582-586`; el `/status` loopback devolvió cero de los cuatro y `tools/dayz_mcp/daemon.py`/el módulo loopback no los publican. `tests.test_mcp_tools.McpToolsTest.test_loopback_status_omits_tool_registry_overlay` también pasó. No es bloqueante.
- **P1, autoridad ausente o inválida: conforme con el alcance entregado.** El cableado pasa deliberadamente cinco blobs nulos (`tools/dayz_mcp/server.py:569-581`), de modo que el valor real publicado es `"unknown"`, nunca `False`. El parser de autoridad convierte bundle corrupto o truncado en autoridad desconocida (`tools/dayz_mcp/tool_registry_fingerprint.py:647-714`) y la comparación devuelve `"unknown"` para perfil/rol distinto (`tools/dayz_mcp/tool_registry_fingerprint.py:769-783`). El probe dio: `no_authority=["unknown","unknown"]`, `corrupt_authority=["unknown","unknown"]`, `truncated_authority=["unknown","unknown"]`, `different_pair="unknown"`, `matching_pair_and_digest="fresh"`, `different_digest="stale"`. No existe en este cableado una fuente externa legible que pueda producir `fresh`/`stale`; eso es coherente con P1 tal como está acotado a overlay local, pero queda señalado para que no se interprete `unknown` como una comparación realizada.
- **P2, rutas y alias: conforme.** El parser acepta los cuatro alias y sólo acepta una ruta absoluta si `_path_is_within` la acredita contra `mission_roots` (`tools/dayz_mcp/dayz_test_request.py:297-303`). El probe de la fachada aceptó una ruta dentro del root hasta devolver el fallo estructurado de Steam; rechazó con `bad_dayz_test_request` tanto una ruta fuera del root como el hermano por prefijo y la salida mediante `..`. Los alias `chernarus`, `livonia`, `sakhal` y `lfheli` fueron aceptados por el parser. No es bloqueante.
- **P2, sobre uniforme: conforme en `execute_dayz_test_run`.** `_compact_result` define las 17 claves en un único punto (`tools/dayz_mcp/dayz_test_tool.py:482-516`). Ejecuté filas de éxito, fallo tipado del worker, preflight, Steam caduca con nueve PID y Steam caduca con cero PID: las cinco devolvieron exactamente `{artifacts_paths, bridge_ready, cleanup_degraded, client_alive, elapsed_s, error_code, mode, phase, process_alive, project, reason, remediation, run_id, server_alive, status, steam_live_pids, steam_registered_pid}`. Con nueve PID salieron los ocho primeros; con cero salió `[]`, según `tools/dayz_mcp/dayz_test_tool.py:730-732`. No es bloqueante.
- **P2, condiciones de consulta: conforme.** La guarda de `tools/dayz_mcp/dayz_test_tool.py:714` evitó toda consulta a Steam con `preflight=True, mode="all"` y con `preflight=False, mode="server"`; ambos probes terminaron con éxito y contador de consultas cero. Un `SteamSessionResult(error_code=None)` permitió continuar hasta el launcher y acabó en éxito. No es bloqueante.
- **P2, validación terminal: conforme.** No hay ninguna ocurrencia de `skip_run_id_context` en el árbol entregado. Toda salida del launcher pasa por `_validate_terminal_context` en `tools/dayz_mcp/dayz_test_tool.py:611-615`, cuyo invariante sigue activo en `tools/dayz_mcp/dayz_test_tool.py:519-530`. La suite focal de 41 tests pasó.
- **Pregunta transversal:** no encontré otro camino por el que el diff invierta éxito/fallo fuera de P1/P2. La retirada del gate duplicado de misión sólo entrega al parser las rutas absolutas contenidas, mientras alias y escapes conservan el comportamiento descrito arriba.
- **Pruebas verdes:** con el intérprete impuesto y `PYTHONPATH=.`: `tests.test_dayz_test_tool` — 41 tests, OK; `tests.test_mcp_tools tests.test_tool_registry_fingerprint tests.test_steam_preflight tests.test_dayz_test_request` — 74 tests, OK; `tests.test_box_occupancy` — 31 tests, OK. `py_compile` de `server.py` y `dayz_test_tool.py` terminó con código 0.
- **Aparato del orquestador, no bloqueante por mandato del encargo:** al ampliar la corrida con `tests.test_request_path_authority`, hubo 10 errores de fixture al intentar abrir/sellar paths bajo este workspace temporal/reparse; el módulo focal no llegó a ejecutar esos casos. No lo cuento contra P1/P2. El resultado total de esa corrida ampliada fue `Ran 128 tests`, `FAILED (errors=10, skipped=2)`.
- **Memoria durable:** no actualicé el vault. Este encargo autorizó como escritura únicamente `REVIEW-CODEX.md`, y el dictamen conserva toda la evidencia nueva relevante.

## LO QUE NO PUDE VERIFICAR

- No pude ejercer el registro ni procesos reales de Steam en Windows. Verifiqué la lógica con resultados y excepciones inyectados; el proveedor concreto quedó cubierto por su suite unitaria, no por una sesión Steam real.
- No pude verificar una autoridad externa real para P1 porque el cableado entregado construye siempre un `AuthorityBundleBytes` sin blobs (`tools/dayz_mcp/server.py:569-581`). Sí verifiqué los casos conocido, corrupto, truncado y perfil/rol distinto sobre el parser puro.
- No pude ejecutar el launcher nativo, DayZ, una misión ni PBO reales; el entorno declarado no los contiene. Los límites observables se verificaron en la fachada FastMCP con el launcher sustituido.
- No pude obtener una corrida válida de `tests.test_request_path_authority` dentro del árbol temporal por los 10 errores de apertura/sellado del fixture indicados en BACKLOG. Esto limita la verificación independiente de toda su matriz de acreditación, aunque los tres ataques de contención exigidos se ejecutaron directamente y pasaron.
- No ejecuté la suite global completa del repositorio; ejecuté 146 tests verdes directamente relacionados y una corrida ampliada parcialmente bloqueada por el fixture del aparato.

## PREMISAS DEL ORQUESTADOR QUE CREO FALSAS

Ninguna de las premisas del §2 quedó refutada.

- Reconté el árbol vivo: `server.py` tiene 0 ocurrencias de `tool_registry_fingerprint`; su `bridge_status()` real devolvió 9 claves. `dayz_test_tool.py` tiene 0 ocurrencias de `steam_preflight` y conserva el rechazo temprano `bad_mission` en la línea indicada.
- Confirmé que el árbol entregado no contiene `skip_run_id_context` y que la validación de contexto terminal sigue siendo obligatoria.
- Intenté refutar la contención de misión con el hermano por prefijo y con una ruta que normaliza fuera mediante `..`; ambos fueron rechazados. La ruta absoluta contenida fue aceptada.
