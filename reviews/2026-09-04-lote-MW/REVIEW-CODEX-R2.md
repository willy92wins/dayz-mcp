# Revisión Codex — ronda 2, lotes M y W

Fecha: 2026-09-04  
Árbol leído y ejecutado: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev`  
Directorio de ejecución: `<árbol>\tools`  
Intérprete: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe`  
Alcance aplicado: únicamente cierre de R-01..R-04, backlog aplicado indicado por el orquestador y regresiones nuevas reproducibles en P0-P5/W6-W12. No se reabrieron los descartes de ronda 1.

## VEREDICTO

BLOQUEANTES=0.

## R-01 — CERRADO

El tipo público es `mode: str` (`tools/dayz_mcp/server.py:2964-2967`). `_patch_mode_enum_from_authority` publica el enum al construir la app y consulta de nuevo `public_mode_names()` antes de Pydantic en cada llamada (`tools/dayz_mcp/server.py:1773-1797`); `build_app` instala el helper antes del wrapper de schema cerrado (`tools/dayz_mcp/server.py:4626-4628`). El accessor lee el `MODE_RECORDS` vivo (`tools/dayz_mcp/dayz_test_modes.py:215-217`).

Repro B-01 literal, adaptado al árbol vivo:

```powershell
$env:PYTHONPATH='.'
@'
from dataclasses import replace
from dayz_mcp import dayz_test_modes
from dayz_mcp.server import ServerConfig, build_app
original = dayz_test_modes.MODE_RECORDS
try:
    offline = next(r for r in original if r.name == "offline")
    dayz_test_modes.MODE_RECORDS = (replace(offline, public=True),)
    authoritative = list(dayz_test_modes.public_mode_names())
    app, _ = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
    published = app._tool_manager.get_tool("dayz_test_run").parameters["properties"]["mode"]["enum"]
    print({"authority_after_substitution": authoritative, "published_by_new_app": published})
    assert published == authoritative, "build_app conserva el Literal congelado al importar server.py"
finally:
    dayz_test_modes.MODE_RECORDS = original
'@ | & 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -
```

Salida hoy, exit 0:

```text
{'authority_after_substitution': ['offline'], 'published_by_new_app': ['offline']}
```

Cierre conjunto de schema y validación por la cadena pública FastMCP: el schema wire publica `offline`; `server` es rechazado con el token requerido; `offline` supera el gate de modo y Pydantic y alcanza el cuerpo, que en una app embedded se detiene después con `session_tools_require_client_mode`.

```powershell
$env:PYTHONPATH='.'
@'
import asyncio
from dataclasses import replace
from dayz_mcp import dayz_test_modes
from dayz_mcp.server import ServerConfig, build_app
from mcp.shared.memory import create_connected_server_and_client_session

async def main():
    original = dayz_test_modes.MODE_RECORDS
    try:
        offline = next(r for r in original if r.name == "offline")
        dayz_test_modes.MODE_RECORDS = (replace(offline, public=True, default_when_omitted=True),)
        app, _ = build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None))
        async with create_connected_server_and_client_session(app._mcp_server) as session:
            listed = await session.list_tools()
            tool = next(t for t in listed.tools if t.name == "dayz_test_run")
            schema = tool.inputSchema
            server = await session.call_tool("dayz_test_run", {"project": "ExampleMod", "mode": "server"})
            offline_result = await session.call_tool("dayz_test_run", {"project": "ExampleMod", "mode": "offline"})
        text_server = " ".join(getattr(c, "text", "") for c in server.content)
        text_offline = " ".join(getattr(c, "text", "") for c in offline_result.content)
        print({"wire_enum": schema["properties"]["mode"]["enum"], "server_isError": server.isError,
               "server_text": text_server, "offline_isError": offline_result.isError,
               "offline_text": text_offline})
        assert schema["properties"]["mode"]["enum"] == ["offline"]
        assert server.isError and "bad_args: mode must be one of offline" in text_server
        assert offline_result.isError and "session_tools_require_client_mode" in text_offline
        assert "mode must be one of" not in text_offline
    finally:
        dayz_test_modes.MODE_RECORDS = original

asyncio.run(main())
'@ | & 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -
```

Salida hoy, exit 0:

```text
{'wire_enum': ['offline'], 'server_isError': True, 'server_text': 'Error executing tool dayz_test_run: bad_args: mode must be one of offline', 'offline_isError': True, 'offline_text': 'Error executing tool dayz_test_run: session_tools_require_client_mode'}
```

El test de regresión añadido también pasa:

```powershell
python -m unittest tests.test_lote_m_products.LoteMProductsTest.test_p5_enum_follows_a_substituted_authority_in_a_new_app
```

```text
Ran 1 test in 0.138s
OK
```

## R-02 — CERRADO

La aserción es ahora identidad estricta: `self.assertIs(True, params["from"]["required"])` (`tools/tests/test_effective_schema.py:25`).

Repro B-02 literal, adaptado al árbol vivo:

```powershell
$env:PYTHONPATH='.'
@'
import unittest
from unittest.mock import patch
import tests.test_effective_schema as ported
required_value = 1
schema={"scene_raycast":{"description":"x","params":{"from":{"required":required_value,"default":None,"type":"array","enum":None}}}}
case=ported.EffectiveSchemaTests("test_scene_raycast_exposes_from_not_from_pos")
result=unittest.TestResult()
with patch.object(ported,"resolve_effective_schemas",return_value=schema):
    case.run(result)
print({"failures":len(result.failures),"errors":len(result.errors),"required_value":required_value,"original_predicate_required_is_True":required_value is True})
assert result.failures or result.errors, "el port unittest acepta required=1; el assert original exigia identidad con True"
'@ | & 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -
```

Salida hoy, exit 0:

```text
{'failures': 1, 'errors': 0, 'required_value': 1, 'original_predicate_required_is_True': False}
```

El mutante `required=1` vuelve a ser rechazado; no hay debilitamiento truthy.

## R-03 — CERRADO

Con un launcher configurado, cualquier excepción al abrir el registro o leer/validar `closure-manifest.json` añade `NATIVE_BUNDLE_MANIFEST_UNREADABLE` con severidad `FAIL` (`tools/dayz_mcp/doctor.py:496-522`). Los dos casos de wiring están fijados en `tools/tests/test_doctor.py:1549-1577`.

Repro B-03 literal, adaptado al árbol vivo:

```powershell
$env:PYTHONPATH='.'
@'
from unittest.mock import patch
from tests.test_doctor import DoctorTest
from dayz_mcp import doctor
import dayz_mcp.launcher_registry as registry
case = DoctorTest("runTest")
case.setUp()
try:
    sources = case.sources(native_launcher_id="dayz-test-v1")
    with patch.object(registry, "open_approved_launcher", side_effect=OSError("manifest gone")):
        payload, rc = doctor.execute(sources)
    print({"rc": rc, "ok": payload["ok"], "summary": payload["summary"], "native": [f for f in payload["findings"] if str(f.get("code","")).startswith("NATIVE_BUNDLE")]})
    assert rc != 0, "doctor devolvio exito aunque no pudo leer el manifest del bundle"
finally:
    case.tearDown()
'@ | & 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -
```

Salida hoy, exit 0:

```text
{'rc': 1, 'ok': False, 'summary': {'fail': 1, 'warn': 0}, 'native': [{'code': 'NATIVE_BUNDLE_MANIFEST_UNREADABLE', 'severity': 'FAIL'}]}
```

Test dirigido adicional:

```powershell
python -m unittest tests.test_doctor.NativeBundleClosureWiringTests
```

```text
Ran 2 tests in 0.000s
OK
```

## R-04 — CERRADO

El camino vivo abre el launcher pero llama `check_native_bundle_externals(entries, stat=_stat_external_file)` sin pasar `opened.sha256` (`tools/dayz_mcp/doctor.py:496-516`). El token por defecto es literalmente `<sha256 of tools/approved-launchers.json after rollback-last>` y la remediación nombra el registro y `rollback-last` (`tools/dayz_mcp/doctor.py:466-480`).

Repro B-04 adaptado al nuevo contrato: inyecta un SHA de PE distintivo a través del objeto `opened` y hace pasar la comprobación por el call-site de producción.

```powershell
$env:PYTHONPATH='.'
@'
import json, re, tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from dayz_mcp import doctor
import dayz_mcp.launcher_registry as registry

pe_sha256 = "A1" * 32
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / "closure-manifest.json").write_text(json.dumps({
        "entries": [{"kind": "external", "path": r"C:\missing.dll", "size": 1, "sha256": "00" * 32}]
    }), encoding="utf-8")
    @contextmanager
    def fake_open(_launcher_id):
        yield SimpleNamespace(root=root, sha256=pe_sha256)
    findings = []
    with patch.object(registry, "open_approved_launcher", fake_open):
        doctor._check_native_bundle_closure(SimpleNamespace(native_launcher_id="dayz-test-v1"), findings)
remediation = str(findings[0]["remediation"])
hex64 = re.findall(r"(?i)(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", remediation)
expected = "<sha256 of tools/approved-launchers.json after rollback-last>"
print({"finding": findings[0]["code"], "remediation": remediation,
       "pe_hash_present": pe_sha256.lower() in remediation.lower(), "hex64_tokens": hex64})
assert findings[0]["code"] == "NATIVE_BUNDLE_EXTERNAL_DRIFT"
assert expected in remediation
assert "rollback-last" in remediation and "tools/approved-launchers.json" in remediation
assert pe_sha256.lower() not in remediation.lower()
assert hex64 == []
'@ | & 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -
```

Salida hoy, exit 0:

```text
{'finding': 'NATIVE_BUNDLE_EXTERNAL_DRIFT', 'remediation': 'Renew the native bundle: python tools/build_native_launcher.py --offline --verify-reproducible, then python -m dayz_mcp.launcher_registry_update rollback-last, then install-dayz-test-v1 --expected-sha256 <sha256 of tools/approved-launchers.json after rollback-last>.', 'pe_hash_present': False, 'hex64_tokens': []}
```

Un censo AST del archivo vivo encontró una sola llamada de producción, en línea 512, con kwargs `['stat']`; el control positivo del censo sí detectó `approved_sha256` en una llamada sintética.

## BLOQUEANTES NUEVOS

Ninguno. No reproduje ninguna regresión introducida por los arreglos en P0-P5/W6-W12.

Verificación de regresión ejecutada:

```powershell
python -m unittest tests.test_lote_m_products tests.test_doctor tests.test_effective_schema tests.test_interpreter_guard tests.test_lote_w_h9
```

```text
Ran 90 tests in 1.698s
OK
```

```powershell
python -m unittest tests.test_mcp_tools tests.test_pipeline_feedback tests.test_weak_agent_consumer_ux tests.test_mcp_capture
```

```text
Ran 104 tests in 11.080s
OK
```

```powershell
python -m unittest tests.test_docs_truth
```

```text
Ran 20 tests in 0.252s
OK (skipped=3)
```

El rojo ambiental de `test_docs_truth` descrito para los workspaces no reapareció en el árbol vivo completo; sus tres skips son condiciones ya contempladas por esa suite. No se contó como bloqueante ni se reabrió W12.

## BACKLOG

- **W9 aplicado — correcto.** `verdict` resuelve ambos operandos antes de `normcase` (`tools/tests/test_interpreter_guard.py:19-37`). El mutante con `..\Scripts` resolvió ambos paths al mismo ejecutable y devolvió `status='ok'`, exit 0.
- **Descripción de `capture_screenshot` — correcta.** La descripción publicada anuncia que el resultado son siempre dos bloques, enumera `crop_space` y `frame_sha256`, y explica comparar `frame_sha256` para detectar un frame congelado (`tools/dayz_mcp/server.py:3993-4001`). Una sonda sobre la descripción de la tool produjo `{'two_blocks': True, 'crop_space': True, 'frame_sha256': True, 'frozen_detection': True}`, exit 0.
- **BL-R04-01 — parámetro residual, no bloqueante.** `check_native_bundle_externals` aún acepta `approved_sha256` y `sha_token = approved_sha256 or ...` (`tools/dayz_mcp/doctor.py:441-468`). Ningún caller vivo lo pasa, por lo que R-04 está cerrado; sin embargo, una llamada directa futura que entregue un SHA volvería a interpolarlo. Repro: con `approved_sha256='A1'*32`, la remediación contiene ese valor, exit 0. Fix sugerido: eliminar el parámetro y usar siempre el placeholder literal. No es una regresión reproducible en el camino P0-P5/W6-W12 actual y no suma bloqueante.
- No se recontaron composición alias+closed, test W6 laxo ni el rojo ambiental del workspace: el orquestador los declaró backlog no aplicado en esta ronda.

## LO QUE NO PUDE VERIFICAR

- No ejecuté juego, red, daemon ni launcher nativo, por prohibición expresa del encargo. R-04 se verificó de forma determinista pasando un SHA de PE centinela por el objeto `opened` simulado y por el call-site real de `_check_native_bundle_closure`.
- No afirmo cobertura fuera de P0-P5/W6-W12: quedó fuera del criterio de bloqueante de esta ronda.
- No actualicé memoria de Obsidian: el entregable autorizado para esta sesión es este dictamen en el scratchpad y no había una decisión de producto nueva que registrar.
