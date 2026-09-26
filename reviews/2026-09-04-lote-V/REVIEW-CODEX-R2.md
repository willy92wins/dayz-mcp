## VEREDICTO

BLOQUEANTES=0.

## R-01 / R-02

### R-01 (B-01, V2) — CERRADO

El arreglo ejecutado es exactamente el integrado en `0eff49efa66d230c35d137ef7ea91d1c6b60753e`: el blob de `tools/dayz_mcp/server.py` del workspace coincide con el del commit (`647c741438619824763d066a70e341f79de48e95`). La función conserva `players_raw` y sólo publica `reason` ante una lista cruda realmente vacía en `tools/dayz_mcp/server.py:1933-1962`.

**Repro literal B-01 [EXACT]:**

```powershell
Set-Location 'C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-V\ws\tools'
$env:PYTHONPATH='.'
$env:PYTHONDONTWRITEBYTECODE='1'
@'
import asyncio, json
from unittest.mock import patch
from dayz_mcp import server

def content_json(result):
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, dict):
        return result
    return json.loads(result[0].text)

async def main():
    app, runtime = server.build_app(server.ServerConfig(key="k", port=0, log_sink=lambda _m: None))
    async def fake(cmd, args, peer, timeout_s):
        if cmd == "entities_query":
            return {"ok": 1, "entities": []}
        if cmd == "query_all_players":
            return {"ok": 1}
        raise AssertionError(cmd)
    with patch.object(runtime, "call_bridge", fake):
        result = content_json(await app.call_tool("entities_query", {"pos":[0.0,0.0,0.0], "radius":1.0}))
    print(json.dumps(result, sort_keys=True))
    assert "reason" not in result, "probe had no players list, so absence was not positively established"

asyncio.run(main())
'@ | & 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -
exit $LASTEXITCODE
```

**Salida ahora (exit 0):**

```text
{"entities": [], "nearest_player_m": null, "ok": 1, "reliability": "remote_unverified"}
```

También pasó el caso persistente de las tres sondas (`campo ausente`, `players: None`, `players: "x"`) en `tests/test_wave_fixes_20260824.py:61-68`.

### R-02 (B-02, V4) — CERRADO

El deadline único se fija en `tools/dayz_mcp/server.py:2405-2406`; tras cada sonda insatisfecha, el código vuelve a medir el presupuesto y duerme `min(poll_interval_s, remaining_sleep)` fuera del lock en `tools/dayz_mcp/server.py:2529-2535`.

**Repro literal B-02 [EXACT]:**

```powershell
Set-Location 'C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-V\ws\tools'
$env:PYTHONPATH='.'
$env:PYTHONDONTWRITEBYTECODE='1'
@'
import asyncio, json, time
from unittest.mock import patch
from dayz_mcp import server

def content_json(result):
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, dict):
        return result
    return json.loads(result[0].text)

async def main():
    app, runtime = server.build_app(server.ServerConfig(key="k", port=0, log_sink=lambda _m: None))
    calls = 0
    async def stale(cmd, args, peer, timeout_s):
        nonlocal calls
        calls += 1
        raise server.ToolError("game_not_ready:reason=server_poll_stale")
    with patch.object(runtime, "call_bridge", stale):
        t0 = time.monotonic()
        result = content_json(await app.call_tool("wait_for", {"condition":"players_at_least", "value":1, "timeout_s":0.1, "poll_interval_s":0.5}))
        wall = time.monotonic() - t0
    print(json.dumps(result, sort_keys=True))
    print(f"wall_s={wall:.3f} calls={calls} deadline_s=0.100")
    assert wall <= 0.15, f"deadline exceeded: {wall:.3f}s"

asyncio.run(main())
'@ | & 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -
exit $LASTEXITCODE
```

**Salida ahora (exit 0):**

```text
{"condition": "players_at_least", "elapsed_s": 0.11019710000255145, "last_error": "game_not_ready:reason=server_poll_stale", "not_ready_probes": 1, "observed": null, "ok": true, "probes": 1, "satisfied": false, "timed_out": true, "tool": "wait_for"}
wall_s=0.110 calls=1 deadline_s=0.100
```

El acotado no rompe los contratos pedidos. Los cuatro tests focales `test_deadline_bounds_the_sleep`, `test_sleep_does_not_hold_tool_lock`, `test_waits_through_server_poll_stale` y `test_server_poll_stale_timeout_reports_last_error` pasaron junto con R-01 y R-03: `Ran 6 tests in 2.527s — OK`. Una sonda independiente con `timeout_s=1.15` y `poll_interval_s=0.5` hizo exactamente tres sondeos, con separaciones de `0.5077s` y `0.5136s`, y terminó en `1.1622s`; no se convirtió en un bucle ocupado.

## BLOQUEANTES

Ninguno. Los dos repros bloqueantes de la ronda 1 salen ahora con exit 0 y no encontré una regresión nueva reproducible en V1–V5.

## BACKLOG

- **R-03 está correcto en producto.** Las constantes y el helper están en `tools/dayz_mcp/server.py:1803-1833`; la prosa pública contiene literalmente el contrato pedido en `tools/dayz_mcp/server.py:3025-3034`; `build_app` parchea primero el enum y después las descripciones en `tools/dayz_mcp/server.py:4712-4713`. Una sesión cliente real en memoria (`create_connected_server_and_client_session` + `session.list_tools()`) recibió por el wire:

  ```json
  {
    "mode_description": "Launch mode. client reattaches only the client to a live run and REQUIRES run_id (server and world state preserved); server and all launch fresh and must NOT pass run_id.",
    "mode_enum": ["all", "server", "client"],
    "run_id_description": "Live run to reattach to. Required with mode=client; forbidden with mode=server|all (bad_dayz_test_request otherwise).",
    "tool_description_has_contract": true
  }
  ```

  El mismo schema pasó `jsonschema.validator_for(...).check_schema(...)` con `Draft202012Validator`; mantiene `type: object` y `required: ["project", "mode"]`. El test de producto está en `tests/test_lote_v_products.py:119-134` y pasó.

- **Cobertura persistente de R-03 menor que la comprobación pedida.** `RunIdMatrixDescriptionTest` comprueba el gestor interno de FastMCP, no una sesión `list_tools` por el wire. No es defecto observable hoy porque la sonda independiente por wire anterior pasó; queda como mejora de test para conservar esa garantía en futuras rondas.

- **No hay tests que pinen las descripciones antiguas.** Un grep sobre todos los `tools/tests/*.py` por los literales exactos entre comillas `"Mode"`, `'Mode'`, `"Run Id"` y `'Run Id'` devolvió `NO_EXACT_LITERAL_PINS`. El control positivo del mismo instrumento sí encontró `RUN_ID_MATRIX_MODE_DESCRIPTION` en `tests/test_lote_v_products.py:131`.

- **El comentario de `READY_REASONS` quedó corregido.** `tools/dayz_mcp/server.py:113-117` dice `Published ready.reason set`, declara el conjunto abierto y ya no contiene `Closed`.

- **El recuento publicado del oráculo está desactualizado.** El fichero ejecutado no contiene 29 checks: terminó `PASS=32 FAIL=0 UNMET=0 de 32 / ORACULO-VERDE`. Son los 27 anteriores, los dos casos de cierre B-01/B-02 y los tres V6. Es una discrepancia documental del aparato, no un defecto de V1–V5 ni de R-03.

- **Regresión acotada verde.** Con el intérprete obligatorio y `PYTHONPATH=.`: los tres módulos de ronda 2 terminaron `Ran 44 tests in 9.121s — OK`; el grupo transversal `test_mcp_tools + test_weak_agent_consumer_ux + test_lote_m_products` terminó `Ran 82 tests in 10.086s — OK`; `bash gate/suite.sh` terminó `SUITE-ACOTADA OK` (en `test_docs_truth`, 20 tests y 3 skips). Los cuatro ficheros del delta de ronda 2 tienen blobs idénticos a `0eff49e`.

## LO QUE NO PUDE VERIFICAR

- No ejecuté DayZ, red ni daemon, conforme al entorno de la revisión. R-03 se verificó mediante el transporte MCP real en memoria, no mediante un proceso stdio o un cliente externo.
- No repetí la suite completa de 2538 tests citada en `COMMIT-R2.txt`; por tanto no revalidé personalmente sus dos fallos baseline. Ejecuté el oráculo, la suite acotada y los grupos focales indicados arriba.
- No validé otro intérprete ni otro conjunto de versiones: toda ejecución funcional usó exactamente `DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe` con el workspace como fuente importada.
- No actualicé la memoria Obsidian: el entregable de esta ronda está restringido a `REVIEW-CODEX-R2.md` y el vault no es una raíz con permiso de escritura en esta sesión.
