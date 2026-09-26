# Censo de invocacion de tests ? L4

Resultado: el defecto exacto estaba en DOS imports de UN fichero. No se encontro otro import absoluto `tools.*` en el AST de los 194 ficheros Python censados. Tras el cambio no queda ninguno. No se ejecuto discovery ni la suite completa para este censo.

## Metodo y cobertura

Comando reproducible desde la raiz:

```powershell
./tools/.venv-mcp/Scripts/python.exe -B ./reviews/suite-invocation-2026-09-08/census.py
```

`census.py` recorre `tools/tests/**/*.py`, parsea AST, incluye imports dentro de funciones y clases, y guarda ruta, linea, sentencia, bytes y SHA-256 en `census-data.json`. Las preimagenes del fichero editado y `GATES.md` se conservan aparte. Se uso ademas `rg` con `^\s*(from tools\.|import tools\.)` para incluir sangria.

| Dimension | Medida |
|---|---:|
| Ficheros Python recursivos | 194 |
| Modulos `tools/tests/test_*.py` | 187 |
| Entradas de import en AST | 1892 |
| Errores de parseo | 0 |
| Imports `tools.*` antes / despues | 2 / 0 |
| Imports con convencion `tests` despues | 136 |
| Referencias textuales a `sys.path` | 147 lineas / 69 ficheros |
| Rutas derivadas de `__file__` | 119 lineas / 100 ficheros |
| `os.getcwd()`, `Path.cwd()` u `os.chdir()` | 1 linea / 1 fichero |
| Candidatos textuales de import dinamico | 196 lineas / 34 ficheros |
| Candidatos textuales de ruta literal relativa | 23 lineas / 14 ficheros |

Los dos ultimos contadores son candidatos lexicos, no errores: incluyen codigo incrustado, comentarios, `path.open('a')` y `opener.open(URL)`. No se atribuye dependencia de cwd por su sola coincidencia. El AST de sentencias import no ejecuta strings ni demuestra todos los caminos dinamicos.

## Hallazgos concretos

- `tools/tests/test_lote2_t2_steam.py:6`: `from tools.tests.test_steam_preflight ...` dependia de la raiz en `sys.path`. Se sustituyo por `from tests.test_steam_preflight ...`.
- `tools/tests/test_lote2_t2_steam.py:80`: el import indentado `from tools.tests import test_dayz_test_tool as fixtures` tenia el mismo defecto. Se sustituyo por `from tests import test_dayz_test_tool as fixtures`. Corregir solo la linea 6 dejaba fallando el quinto test sin `PYTHONPATH`. El grep original del brief no miraba imports indentados.
- `tools/tests/test_steam_preflight.py:10` usa `dayz_mcp.*`, coherente con `tools/` como raiz de importacion. Otros imports locales censados: `_broker`, `_session_coordination`, `build_native_launcher`, `checks`, `g0_abba_gate`, `h9_native_probe`, `install_mcp`, `mcp_capture`, `p0s_gate`, `p0s_test_runner` y `vehicle_trace_artifact`. Sus modulos/directorios existen bajo `tools/`; no se encontro otro import de la familia invertida `tools.tests.*` ni import bare `test_*`.
- `tools/tests/test_000_path.py:7-9` YA prepara `sys.path` para discovery. `tools/tests/__init__.py` esta vacio. Esto es infraestructura preexistente; no equivale a fijar interpreter, cwd, namespace y argumentos de cada invocacion. El modulo bootstrap se empleo como negativo real de cero tests del runner.
- `tools/tests/test_g0_abba_verdict.py:151-155`: unico `Path.cwd()`. Construye una cadena sintetica para verificar que `_public_cell` expone solo el nombre del sidecar. No abre ese path ni lo usa para encontrar el repo; no es el defecto del encargo. Sus imports en lineas 10-15 siguen la convencion `tools/`.
- `tools/tests/_addon_paths.py:15,25-28`: `Path('scripts')` se une a candidatos basados en ancestros de `__file__`; no se resuelve contra cwd. `test_install_mcp.py:1032` usa `Path('unknown.exe')` como fallback nominal, no como raiz del repo.
- `Path(__file__).resolve().parents[1]` apunta a `tools/` en los tests directos; `parents[2]` apunta al repo. Ejemplos: `test_box_occupancy.py:13`, `test_bug046_lease_queue_liveness.py:11`, `test_task9_protocol_docs.py:8`. Estas expresiones dependen del layout del fichero, no del cwd. La fixture `fixtures/dayz_mcp/sitecustomize.py:15` usa `parents[3]` por su profundidad. No se cambiaron.
- No hay `pytest.ini`, `setup.cfg`, `conftest.py` ni `tox.ini` en raiz, `tools/` o `tools/tests/`. `tools/pyproject.toml:1-21` declara build/paquete/dependencias, sin configuracion de tests. `GATES.md` anterior no mencionaba unittest ni pytest.
- Matiz a ?no hay nada?: existe `tools/p0s_test_runner.py:282-291,315-332`, un runner especial con allowlist explicita y deny-launch guard, que prohibe discovery implicito. No fija la invocacion general solicitada ni es un runner `.ps1`; se conserva intacto.

## Doble import observado y consecuencias

`identity-before.log` ejecuta solo los dos modulos del gate, con PYTHONPATH raiz. Preimporta `tests.test_dayz_test_tool` como haria discovery al visitar ese fichero, sin ejecutar sus tests. Se observaron `tests.test_steam_preflight` / `tools.tests.test_steam_preflight` y `tests.test_dayz_test_tool` / `tools.tests.test_dayz_test_tool` simultaneamente: misma fuente, objetos de modulo y clases distintos, y estado de modulo no compartido. Es evidencia dinamica focal; no afirma haber observado una corrida completa historica.

Las clases `_MutableSteamProvider` y `_FakeRemediationHost` guardan su estado en instancias (`test_steam_preflight.py:282-349`), por lo que no se demostro una asercion incorrecta debida al alias. `_Runtime` y los context managers tambien se construyen por instancia (`test_dayz_test_tool.py:674-726`). Los dos aliases de fixtures compartian el modulo/funciones `dayz_mcp` de produccion: el doble import no crea dos Steam reales ni acredita por si solo tests ejecutados dos veces. Si se parchea un global o se compara identidad de clase por el alias opuesto, la modificacion no se comparte; esto se aislo con un atributo centinela en memoria. Ademas, `_PROJECTION_CASES` contiene dicts/listas creados al importar (`test_dayz_test_tool.py:1836-1881`), por lo que la doble evaluacion recrea esos datos; no se observo da?o por ello en el gate.

Despues, `identity-after.log` muestra ambos `alias_present=False`, `tools.tests module keys: []`, 22 tests y exit 0. `gate.log` acredita A/B con los mismos 22 identificadores, no solo igual cardinalidad.

## Limites

No se garantiza cualquier linea arbitraria de `python -m unittest` desde la raiz o una carpeta externa: los imports `tests.*` siguen requiriendo `tools/` en el contexto correcto. El runner soporta esos cwd fijando explicitamente ese contexto. No se anadio otro bootstrap a cada test ni se altero logica o infraestructura de otras lanes.

FUERA DE MI ALCANCE: resolver otras familias de dependencias del layout, cambiar `test_docs_truth.py`, `test_daemon_spawn_branch.py` o produccion; verificacion dinamica de los restantes modulos y de los imports generados. La restriccion del brief impide la suite completa; 3085 sigue siendo expectativa del receptor, no una medicion de esta lane.

El censo es una instantanea de 2026-09-08T03:12:49.791963+02:00. Al cierre cambiaron respecto a sus hashes `tools/tests/test_db05_preflight_diagnostics.py` y `tools/tests/test_provenance_gate.py`; esta lane no los escribio. Los recuentos de la tabla describen la instantanea conservada, no un arbol congelado de las otras lanes.
