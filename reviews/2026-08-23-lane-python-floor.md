Cierro la contradicción de versión para que el floor, el instalador y la documentación digan lo mismo. Primero compruebo el floor real y quién más pinea Python.El encargo está claro: alinear floor, instalador y docs. Siguiente paso: leer las fuentes y quién más pinea Python.Hay un test de verdad de docs que ya vigila este desajuste. Lo leo junto al resto de pines de versión.El test de docs extrae el pin `py -X.Y` del instalador: si cambia la selección, hay que actualizarlo. Sigo con el AST del código y con `install_mcp.py`.El instalador Python no pinea 3.14; el watchdog de docs sí. Ahora mido el floor real: AST de producción, intérpretes en la máquina y `Requires-Python` de las dependencias.Floor confirmado: producción pide 3.11, no más. Hay un CPython 3.11 de uv para verificar imports. Mido el fallo en 3.10 y empiezo a escribir.3.11 importa `host_config` de verdad, no solo por el lenguaje. 3.10 sigue cayendo en `tomllib`. Escribo el alineado.El floor público queda en **3.11**. `pyproject.toml`, los dos instaladores y las docs de entrada dicen lo mismo; el código de producción no se tocó.

## Floor

Barrido AST de los `.py` de producción (tests y `vendor/` fuera). Seis sitios 3.11, ninguno 3.12+:

| sitio | rasgo |
|---|---|
| `tools/dayz_mcp/host_config.py:12` | `import tomllib` |
| `tools/dayz_mcp/identity_migration.py:13` | `from datetime import UTC` |
| `tools/dayz_mcp/instance_fence.py:11` | `from datetime import UTC` |
| `tools/dayz_mcp/native_process_guard.py:8` | `from datetime import UTC` |
| `tools/checks/fence_canary_probe.py:34` | `from datetime import UTC` |
| `playbooks/runner.py:11` | `import tomllib` |

El sexto es el runner de playbooks, también 3.11. No hay un sitio que suba el floor.

Dependencias instaladas: `mcp` y `Pillow` declaran `>=3.10`; `psutil` `>=3.6`. No empujan por encima de 3.11.

`py -3.11` no resuelve nada en este host (`No suitable Python runtime found`). `py -0p` sí lista `-V:Astral/CPython3.11.16`. Contra ese intérprete, con `PYTHONPATH=tools` (y `tools/checks` para el canary):

- `dayz_mcp.host_config` → ok
- `dayz_mcp.identity_migration` / `instance_fence` / `native_process_guard` → ok
- `fence_canary_probe` → ok

3.11 no es solo un hecho de lenguaje: esos cinco módulos importan. No hay 3.12 ni 3.13 instalados (`py -3.12` / `py -3.13` fallan igual que el `-3.11` corto).

## Decisiones

**1. Floor = 3.11.** Es el mínimo del stdlib que el árbol usa (`tomllib`, `datetime.UTC`). `>=3.10` era falso: pip aceptaba el paquete y el import se caía. Fijar 3.14 habría sido el pin de una máquina.

**2. Instalador: rango resuelto, fail-closed.** `py` no acepta rangos. `install-mcp.ps1` lee `py -0p`, se queda con el más nuevo `>= 3.11` (sirve también un tag Astral/uv, que `py -3.11` no ve) y comprueba la versión del exe. Si `py` no lista ninguno que cumpla, **no** cae a `python` sin pin: error explícito. Sin `py`, usa `python.exe` solo si su `sys.version_info` llega al floor; si no hay exe o es más viejo, error. `install_mcp.py` declara `MIN_PYTHON = (3, 11)` y se niega a importar `host_config` por debajo.

**3. Docs: el requisito, sin la contradicción.** README, QUICKSTART y `tools/README-mcp.md` dicen «Python 3.11 or newer». Los párrafos del desajuste 3.14 vs `>=3.10` ya no están. `playbooks/README.md` ya decía 3.11+; no se reescribió.

## Diff

| archivo | qué |
|---|---|
| `tools/pyproject.toml:8` | `requires-python = ">=3.11"` |
| `tools/install-mcp.ps1` | `$MinPythonVersion = [version]"3.11"`; `Resolve-HostPython` vía `py -0p`; adiós a `py -3.14` y al fallback ciego a `python` |
| `tools/install_mcp.py` | guard `MIN_PYTHON = (3, 11)` antes de importar `host_config` |
| `README.md`, `QUICKSTART.md`, `tools/README-mcp.md` | «3.11 or newer»; sin el split |
| `tools/tests/test_packaging_declarations.py` | el floor forma parte del gate de packaging |
| `tools/tests/test_docs_truth.py` | `InstallerPythonDocsTest` lee pyproject + `$MinPythonVersion`, no un `py -3.N` |
| `tools/tests/test_host_python_floor.py` | lockstep de las tres declaraciones + selector aislado |

`doctor.py`, `bootstrap_parent.py` y `p0s_gate.py` no pinea Python. `p0s_gate` importa `install_mcp`, así que hereda el guard. `loopback.py` / `daemon.py` / `server.py` / `test_bug046_startup_deadlock.py` no se tocaron.

## Demostración

**Import 3.10, antes y después** (`PYTHONPATH=tools`, `py -3.10 -c "import dayz_mcp.host_config"`). El error es el mismo; el código de producción no cambió:

```
File "tools\dayz_mcp\host_config.py", line 12, in <module>
    import tomllib
ModuleNotFoundError: No module named 'tomllib'
```

**El paquete ahora declara el floor.** `py -3.10 install_mcp.py`:

```
Python 3.11 or newer is required; this interpreter is 3.10
```

exit 2.

**El selector de `install-mcp.ps1` rechaza por debajo del floor** (función aislada, no el instalador entero): listing solo con 3.10/2.7 → `NONE` (eso es el throw de `Resolve-HostPython`). Listing 3.14 + 3.10 + Astral 3.11 → elige 3.14. Listing 3.10 + Astral 3.11 → elige el 3.11. `Resolve-HostPython` en esta máquina devuelve un intérprete `>= 3.11`.

## Gate

`tests.test_packaging_declarations` (4 tests, incluido `test_requires_python_is_the_language_floor`):

```
test_declared_packaging_targets_exist ... ok
test_dependencies_are_pinned_to_exact_versions ... ok
test_pyproject_declares_the_pinned_runtime_dependencies ... ok
test_requires_python_is_the_language_floor ... ok

----------------------------------------------------------------------
Ran 4 tests in 0.002s

OK
```

Acompañantes, no la suite entera:

- `tests.test_docs_truth` — 19 tests, 7 skipped, OK
- `tests.test_host_python_floor` — 10 tests, OK (1.583 s)

## Grep `3.14` / `3.10` que sigue vivo

| sitio | por qué queda |
|---|---|
| `tools/build_native_launcher.py`, `tools/dependency-lock.json`, `tools/tests/test_dependency_lock.py` | CPython **embebido** del native launcher, pin `3.14.3`. No es el Python del host. |
| `product-spec.md` (entrada fechada del grill Fase 4) | registro histórico («smoke-verificado … sobre Python 3.14.3 host»), no un requisito. |
| `tools/tests/test_docs_truth.py` | el watchdog **prohíbe** que las docs vuelvan a decir que 3.10 sirve. |

No queda `3.14` ni `>=3.10` como requisito de host en README, QUICKSTART, `tools/README-mcp.md`, `pyproject.toml` ni `install-mcp.ps1`.

No verificado: `pip install` sobre 3.10 (el pip de ese intérprete no llegó a evaluar `requires-python`; falló antes, setuptools viejo / `bdist_wheel`). Suite completa no corrida, como pedía el encargo.

```json
{"status":"ok","paths":["tools/pyproject.toml","tools/install-mcp.ps1","tools/install_mcp.py","README.md","QUICKSTART.md","tools/README-mcp.md","tools/tests/test_packaging_declarations.py","tools/tests/test_docs_truth.py","tools/tests/test_host_python_floor.py"],"summary":"Host Python floor is 3.11 across pyproject, both installers, and public docs.","floor_chosen":"3.11","verified":["AST production: tomllib+datetime.UTC only, plus playbooks/runner.py tomllib","3.10 import dayz_mcp.host_config still ModuleNotFoundError tomllib","3.11.16 imports host_config, UTC modules, fence_canary_probe","mcp/Pillow Requires-Python >=3.10; psutil >=3.6","packaging_declarations 4/4 OK","docs_truth 19 OK (7 skipped)","test_host_python_floor 10/10 OK","py -3.10 install_mcp.py exit 2","selector rejects 3.10-only py -0p listing"],"not_verified":["3.12/3.13 not installed","pip install on 3.10 did not reach requires-python (old setuptools)","full unittest suite not run","install-mcp.ps1 not run end-to-end (selector + Resolve-HostPython isolated)"]}
```