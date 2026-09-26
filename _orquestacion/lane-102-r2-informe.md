# Informe lane 102-r2 (Claude) — issue #102, deuda C.4: shims standalone

Rama: `feature/claude-102-lane-claude` · estado: **COMPLETO** (C.4 cerrada; ver C.1 para lo que el shim destapa)

### Bloque A - Archivos creados/modificados

Solo tests. **Cero produccion**, cero asertos tocados.

- `tools/tests/test_arg_contract_hash.py` 13 (`import sys`), 17-20 (cabecera `_TOOLS_DIR` identica a `test_mcp_tools.py:20-23`), antes de `from dayz_mcp import ...`.
- `tools/tests/test_playbook_reload.py` 20-23 (cabecera `_TOOLS_DIR`; `sys` ya estaba importado), justo antes de `from dayz_mcp import ...` (tras los imports de `mcp`, que no dependen de `tools/`).
- `_orquestacion/lane-102-r2-informe.md` — este informe.

### Bloque B - Resultado de los tests / verificacion

Python: `tools/.venv-mcp` del checkout principal; `discover -s tools/tests -p "<mod>.py"`.

| corrida | resultado literal |
|---|---|
| test_arg_contract_hash.py, `PYTHONPATH=tools` | `Ran 18 tests in 0.001s` / `OK` |
| test_arg_contract_hash.py, **sin PYTHONPATH** (`env -u PYTHONPATH`) | `Ran 18 tests in 0.001s` / `OK` |
| test_playbook_reload.py, `PYTHONPATH=tools` | `Ran 22 tests in 3.572s` / `FAILED (errors=1)` (ver C.1) |
| test_playbook_reload.py, **sin PYTHONPATH** | `Ran 22 tests in 3.519s` / `FAILED (errors=1)` (el mismo, ver C.1) |
| test_client_mode.py, `PYTHONPATH=tools` | `Ran 53 tests in 7.663s` / `OK` |

El `ModuleNotFoundError: No module named 'tests'` desaparece en los dos modulos, con y sin PYTHONPATH.

Suite COMPLETA (`-p "test_*.py"`, `PYTHONPATH=tools`), dos corridas:
```
Run 1: Ran 4173 tests in 338.323s  FAILED (failures=30, errors=5, skipped=58)
Run 2: Ran 4173 tests in 315.874s  FAILED (failures=29, errors=5, skipped=58)
```
Run 2 es identica a la baseline de lane 102 (29F/5E/58s), con los mismos 29F+5E listados en `lane-102-claude-informe.md` Bloque B. El fallo extra de Run 1 fue un flake (ver C.2).

### Bloque C - Hallazgos durante implementacion

1. **`test_playbook_reload` no queda 22/22 OK, y el brief lo esperaba.** Con el shim el modulo importa y 21/22 pasan. `test_wire_requires_exact_explicit_module` lanza `KeyError: 'playbook_reload'` en `tools["playbook_reload"].inputSchema` (linea 153). Es un fallo preexistente y no depende del import: es el mismo 1E de `test_playbook_reload` que la suite completa ya contaba en la baseline (familia #103, "playbook_reload(1E)"). Causa: `build_app` en modo `client` sin lease sustituye `list_tools` por `_compact_initial_catalog` (`server.py:708-710`, `7463-7474`), y `playbook_reload` no esta en `_INITIAL_CATALOG_NAMES`. Las llamadas `tools/call` funcionan, pero `list_tools()` no lo expone. Decision conservadora: no lo toco (el brief pide cero cambios de asertos y cero produccion; decidir si el catalogo compacto debe incluirlo o si el test debe leer el catalogo completo es cosa de #103).
2. **Flake de timing en `test_client_mode.test_version_blocked_world_read_surfaces_not_ready`** (solo en suite completa Run 1: `FAIL`). El test sondea con un deadline fijo de 1.0 s (`test_client_mode.py:706`) y es sensible a la carga. Pasa standalone (53/53), pasa con todos los modulos importados (`-k`), y pasa en Run 2. No tiene relacion con el shim: el shim solo anade `tools/` a `sys.path` si falta. Candidato a subir el deadline en otra lane.
3. Con `PYTHONPATH=tools` (relativo), `str(_TOOLS_DIR)` (absoluto) no coincide con esa entrada y el shim la inserta duplicada. Es inocuo y es el mismo comportamiento que ya tienen sus hermanos.

### Bloque D - Handoff para la revision (Sol)

- **Estado:** C.4 cerrada: los dos modulos importan standalone con y sin PYTHONPATH. `test_arg_contract_hash` 18/18 OK. `test_playbook_reload` 21/22, con el residual preexistente de C.1 (#103). Suite completa sin regresion: 29F/5E/58s, igual que la baseline.
- **Que revisar:** que las dos cabeceras sean byte a byte el patron de `test_mcp_tools.py:20-23` y que el diff solo anada lineas.
- **Deuda conocida:** C.1 (`playbook_reload` fuera del catalogo compacto pre-lease, #103), C.2 (deadline de 1.0 s flaky en test_client_mode). Siguen abiertas la C.1 y la C.3 de lane 102.
- Push normal a `feature/claude-102-lane-claude`, sin force, sin PR, sin tocar `main`.
