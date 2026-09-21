Voy a auditar en solo lectura: primero cargo las reglas de informe y el workspace, luego abro cada candidato en vez de fiarme de un grep.El workspace ya está claro: voy a inventariar las comprobaciones de existencia y las rutas que asumen el venv editable, y abrir cada candidato.El grep se cortó; voy a inventariar con Python y abrir primero los sitios que ya hablan de sparse checkout, `addon/` y el venv editable.`_addon_paths.py` y `test_docs_truth.py` ya hablan de sparse checkout; ahora cruzo cada existencia contra `git ls-files` para no mezclar disco con índice.Sparse checkout confirmado. Ahora saco el inventario de skips/existencia sobre rutas del repo y lo cruzo con el índice de git.HANDOFF y PROJECT-MAP no están trackeados; `addon/` sí. Siguiente paso: localizar skips y lecturas de `addon/`, `README` y `playbooks/` que traten el disco como si fuera un clon.Voy a medir en caliente qué resuelve `addon_root()`, de dónde importa el venv `dayz_mcp` y si el sibling `DayZ_MCP` coincide con el blob de git.Hay **2 defectos Q1** de verdad (el resolver de `addon/` y el assert de `include.lst`) y **un puñado Q2** que asumen el install editable. El resto de las 274 comprobaciones de existencia son artefactos o runtime. Un clon que corre el instalador sigue en verde; el daño está en sparse/copia + venv prestado.

## Método

Inventario AST de `exists` / `is_file` / `is_dir` / `os.path.exists` / `lexists` / `Test-Path` en los 255 paths trackeados (excluido `tools/checks/check_readme_cites.py`): **274 sitios**. Abrí todos los `skipTest`, todo lo que habla de clon/sparse/`addon/`, los ficheros de más hits, y todas las rutas `__file__` / `.venv-mcp` / `sys.executable` de runtime. El resto se clasificó por categoría (tmp, venv, Kits, `P:\`, `%LOCALAPPDATA%`, launcher construido), no línea a línea.

Medido con el Python del venv de desarrollo, `cwd` = esta copia:

- `dayz_mcp.__file__` → `...\DayZ_MCP_dev\tools\dayz_mcp\__init__.py` (no la copia)
- `daemon_runtime_cwd()` → `...\DayZ_MCP_dev\tools`
- `PLAYBOOKS_DIR` → `...\DayZ_MCP_dev\playbooks`
- `addon_root()` desde el árbol original → `...\DayZ Projects\DayZ_MCP` (sibling)
- `addon_root()` desde esta copia → `...\ws_audit\addon`
- `HEAD:addon/scripts/5_Mission/MCPBridge.c` sha256 = sibling = copia (`66d68a4e…`)
- `HEAD:addon/include.lst` sha256 = sibling (`37ae3ff9…`)
- Sparse original: `/*` + `!/addon/`; `addon/` no está en disco; 13 ficheros trackeados

---

## Tabla

| path:line | P | veredicto | dev / clon / copia | 1 línea |
|---|---|---|---|---|
| `tools/tests/_addon_paths.py:30` | 1 | **DEFECTO** | sibling `DayZ_MCP` / `addon/` del clon / `addon/` de la copia (o `FileNotFoundError` si no hay ni sibling ni carpeta) | `is_file()` sobre `scripts/5_Mission/MCPBridge.c` (trackeado, sparse-excluido) decide el árbol que 15 tests tratan como lo que se publica; no pregunta a git; en dev sustituye el blob por el sibling |
| `tools/tests/test_addon_tree_has_no_write_artifacts.py:122` | 1 | **DEFECTO** | `include.lst` del sibling / del `addon/` del clon / de la copia | El mensaje dice `addon/include.lst is missing` (lo que empaqueta un clon) pero el `is_file` mira lo que devolvió `addon_root()`, no `HEAD:addon/include.lst` |
| `tools/tests/test_docs_truth.py:104` | 1 | DUDOSO | subconjunto en disco (incluye `CLAUDE.md` interno) / sin `CLAUDE.md` / igual que clon | El helper documenta «un clon tiene un subconjunto» y lo implementa con `is_file()`; hoy los nombres no son sparse, así que no miente, pero el mecanismo es el de `check_readme_cites` antes del `git show` |
| `tools/tests/test_docs_truth.py:337` | 1 | DUDOSO | corre (checker en disco) / corre / corre aquí; skip solo si falta el checker | `check_readme_cites.py` **está trackeado**; skip si no hay disco = agujero en copia parcial, no una afirmación sobre el clon |
| `tools/tests/test_dayz_test_value_error_codes.py:69` | 1 | DUDOSO | todos los módulos presentes / igual / igual salvo copia a medias | `if not path.is_file(): continue` sobre `.py` trackeados de `dayz_mcp/`; ausencia = no se audita, no fallo |
| `tools/dayz_mcp/daemon_contract.py:12` | 2 | **DEFECTO** | `...\DayZ_MCP_dev\tools` / `...\<clon>\tools` si el venv es suyo / **sigue siendo DayZ_MCP_dev\tools** con este venv | `daemon_runtime_cwd()` sale de `__file__` del paquete importado, no del `cwd`; medido: copia ≠ cwd del daemon |
| `tools/dayz_mcp/playbook_tool.py:23` | 2 | **DEFECTO** | playbooks del checkout original / del clon (editable) / **playbooks del original** con este venv; wheel → `Lib/playbooks` inexistente | `PLAYBOOKS_DIR = Path(__file__).parents[2] / "playbooks"`; `is_file`/`is_dir` luego (L67, L85, L162) contestan sobre **ese** árbol |
| `playbooks/runner.py:24` | 2 | **DEFECTO** | `tools/.venv-mcp\Scripts\python.exe` junto a este `runner.py` / igual tras el instalador / igual, apuntando al árbol **desde el que se cargó** `runner.py` | Nombre de venv escrito a mano + `cwd: str(_TOOLS)` (L39); misma familia que `daemon.py:194`, otro fichero |
| `tools/dayz_mcp/dayz_test_tool.py:331` | 2 | **DEFECTO** | lee `native-launchers/dayz-test-v1/request-policy.json` junto al paquete (gitignore, artefacto de build) / un clon sin construir el launcher → `launcher_policy_invalid` / lee el json **del árbol importado** | Runtime asume layout editable + bundle ya generado al lado de `dayz_mcp` |
| `tools/mcp_capture.py:314` | 2 | **DEFECTO** | `mcp-grab.ps1` hermano de `__file__` (trackeado) / igual en árbol fuente / script del **original** (`MAPPING` también pincha `mcp_capture`); wheel → missing | `os.path.exists(GRAB_SCRIPT)` con `GRAB_SCRIPT` anclado a `__file__` (L86); py-module, no viaja en un wheel |
| `tools/dayz_mcp/doctor.py:245` | 2 | **DEFECTO** | escanea `...\DayZ Projects` / el padre del clon / el padre del paquete importado (`DayZ Projects` con este venv) | `scan_roots=(Path(__file__).parents[3],)` para `rglob("dayz-test.ps1")`; no es el árbol en ejecución |
| `tools/dayz_mcp/launcher_registry.py:25` | 2 | CORRECTO en Q1, **DEFECTO** en Q2 | `approved-launchers.json` local (gitignore) / un clon no lo tiene hasta instalar / el json **del paquete importado** | Artefacto bien gitignorado; la ruta sale de `__file__`, así que un venv prestado acredita el registro de otro árbol |
| `tools/dayz_mcp/server.py:704` | 2 | CORRECTO en Q1, **DEFECTO** en Q2 | `tools/_audit/exec_enforce.jsonl` (gitignore) / igual / `_audit` del **original** | Default de auditoría junto al paquete, no junto al `cwd` |
| `tools/tests/test_daemon_contract.py:89` | 2 | **DEFECTO** | pasa (import = tests) / pasa con venv propio / **falla** si el import es el original y `__file__` del test es la copia | Fija `daemon_runtime_cwd() == Path(__file__).parents[1]`; es el supuesto hecho test |
| `tools/tests/test_bug046_startup_deadlock.py:726` | 2 | **DEFECTO** | `sys.executable` + `cwd=tools` del mismo árbol / igual / python del venv original + `cwd` de la copia | El hijo arranca con intérprete prestado y cwd de la copia; es el patrón n5 (no es un `exists`) |
| `tools/tests/test_client_credential_rotation_e2e.py:170` | 2 | mixto | venv local o skip / skip hasta instalar / skip (esta copia no tiene `.venv-mcp`) **y** `cwd=daemon_runtime_cwd()` (L182) del original | El skip del venv es Q1 CORRECTO; el cwd del spawn sigue al paquete importado |
| `tools/tests/_bundle_paths.py:26` | 1 | CORRECTO | skip si no hay `.exe` / skip en clon fresco / skip | Artefacto de build; el comentario nombra el problema author-vs-clon y no usa el directorio (que sí viaja) |
| `tools/tests/test_docs_truth.py:232` | 1 | CORRECTO | predicate True → exige «sparse» en docs / skip «public clone» / skip (hay `addon/`, no hay `.git`) | Mide **este** checkout (`sparse-checkout` + disco); no afirma que git no tenga `addon/` |
| `tools/tests/test_docs_truth.py:363` | 1 | CORRECTO | corre (`HANDOFF.md` interno) / skip / skip | `HANDOFF.md` no está trackeado (medido) |
| `tools/tests/test_install_mcp.py:1232` | 1 | CORRECTO | corre (`tools/publish/` interno) / skip / skip | `publish/` no está trackeado |
| `tools/tests/test_visual_gate_resolution.py:39` | 1 | CORRECTO | corre si existe el PNG / skip / skip | `fase3-evidence-subject.png` no está trackeado |
| `tools/tests/test_addon_tree_has_no_write_artifacts.py:96` | 1 | CORRECTO | skip o corre según `publish/` / skip / skip | Artefacto interno, no el `addon/` trackeado |
| `tools/pack-addon.ps1:69` | 1 | CORRECTO | `P:\DayZ_MCP\include.lst` / igual si montan P:\ / n/a | Empaqueta el work drive, no concluye el contenido del clon |
| `tools/install_mcp.py:965` | 1 | CORRECTO | crea/reusa `.venv-mcp` / igual / n/a en esta copia | Artefacto; L981 hace `pip install -e` **a propósito** (un clon con este instalador queda coherente) |
| `tools/native-launchers/dayz-test-v1/src/app_main.py:147` | 2 | CORRECTO | `sys.executable.parent.parent` = raíz del bundle nativo | Layout del launcher embebido, no del venv editable |

Consumidores de `addon_root()` (heredan el DEFECTO de `:30`, no tienen su propio `exists` sobre la ruta trackeada):  
`test_batch6.py`, `test_instance_fence.py`, `test_inventory_give.py`, `test_object_anim.py`, `test_object_inspect.py`, `test_player_teleport.py`, `test_poll_key_reload_contract.py`, `test_restore_gameplay_contract.py`, `test_result_prune.py`, `test_surface_query.py`, `test_task9_spawn_phase_markers.py` (comentario en L19: «The Enforce source is NOT under version control» — **falso** desde que `addon/` está trackeado), `test_ui_reload_layout.py`, `test_vehicle_prepare_fixture.py`, `test_vehicle_trace_contract.py`, `test_world_spawn_ground_contract.py`.

`daemon.py:188-194` no se re-lista (archivado).

---

## Conteos

| | n |
|---|---|
| Sitios de existencia revisados (AST, trackeado, sin `check_readme_cites.py`) | 274 |
| **DEFECTO Q1** | **2** |
| **DUDOSO Q1** | **3** |
| **CORRECTO Q1** | **269** (por categoría en los de alto volumen: `identity_migration`, `build_native_launcher`, `host_config`, `relock_toolchain`, tests de tmp/journal/venv) |
| **DEFECTO Q2** (asunción editable / `__file__` ≠ árbol en ejecución), además de `daemon.py:194` | **8** sitios de runtime + 2 tests que la clavan |

---

## Qué arreglaría primero (daño a quien clona)

Un clon que corre `install-mcp` y se queda con **su** `.venv-mcp` editable **sigue verde**. El daño no es «el clon no arranca»; es el mismo patrón n5 (copia o sparse + venv prestado) y un tool de runtime que exige un artefacto de build.

1. **`daemon_contract.py:12` + spawns con `sys.executable` y `cwd` del test** (`test_bug046_startup_deadlock.py:726`, `test_client_credential_rotation_e2e.py:182`). Es el mecanismo n5 medido: el hijo importa un árbol y acredita otro `cwd`. Un agente que copia el repo y usa el Python de desarrollo lo vuelve a pagar.
2. **`playbook_tool.py:23` y `playbooks/runner.py:24`**. El adapter carga playbooks y el python del venv desde `__file__` del paquete/runner, no desde el árbol en ejecución. Wheel o venv prestado → diccionario vacío o `python.exe` que no existe (mismo contrato de nombre que `daemon.py:194`).
3. **`_addon_paths.py:30`**. Un clon completo no lo nota (`addon/` está en disco). En el árbol de desarrollo (sparse) los 15 tests de Enforce leen el sibling, no `HEAD:addon/`. Hoy el contenido coincide (hash medido); el día que diverga, el clon recibe una cosa y la suite del autor habrá gateado otra. `test_task9_spawn_phase_markers.py` todavía dice que el bridge no está versionado.
4. **`dayz_test_tool.py:331`**. Tras clonar e instalar, `list_project_names` falla con `launcher_policy_invalid` hasta `build_native_launcher.py` — el json está gitignorado. No es sparse; es un tool de usuario que no distingue «no construido» de «corrupto».

---

## Defensa genérica (no escrita)

Un helper al estilo de `check_readme_cites._tracked_lines`: si la pregunta es «¿lo recibe un clon?», disco primero y si falta `git show HEAD:<rel>` (o `ls-files`). Para Q2, un test de arranque que falle si `Path(dayz_mcp.__file__).resolve().parents[1]` no es el `tools/` del árbol desde el que se lanzó la suite.

---

## Qué no pude comprobar

- No corrí la suite ni un unittest (solo snippets).
- No abrí las 269 líneas CORRECTO una a una; los bloques grandes (`identity_migration` 30, `build_native_launcher` 19, etc.) son runtime/artefacto por lectura de muestra.
- Solo comparé hash de `MCPBridge.c` e `include.lst` sibling vs git; el resto de `addon/` puede divergir.
- No instalé un wheel ni medi el ganador PathFinder-vs-editable con `cwd=copia/tools` (sí con `cwd=copia` raíz: gana el editable).
- No re-verifiqué CRLF vs LF; fuera de alcance.

```json
{"status":"ok","summary":"274 exists() en trackeado: 2 DEFECTO Q1 (addon_root sin git), 3 DUDOSO, 269 CORRECTO; Q2: cwd/playbooks/venv siguen __file__ del editable, medido contra esta copia.","counts":{"DEFECTO":2,"CORRECTO":269,"DUDOSO":3},"top_fixes":["tools/dayz_mcp/daemon_contract.py:12 daemon_runtime_cwd desde __file__ (n5)","tools/dayz_mcp/playbook_tool.py:23 PLAYBOOKS_DIR + playbooks/runner.py:24 .venv-mcp","tools/tests/_addon_paths.py:30 is_file de MCPBridge.c trackeado/sparse","tools/dayz_mcp/dayz_test_tool.py:331 request-policy.json artefacto junto al paquete"],"verified":["HEAD 0225cee sparse !/addon/ 13 addon trackeados ausentes en disco orig","venv import dayz_mcp y daemon_runtime_cwd y PLAYBOOKS_DIR apuntan a DayZ_MCP_dev con cwd=ws_audit","addon_root orig=sibling DayZ_MCP; copia=ws_audit/addon","sha256 MCPBridge.c e include.lst iguales git=sibling=copia","CLAUDE.md HANDOFF.md PROJECT-MAP.md tools/publish/ no trackeados","install_mcp.py:981 pip install -e"],"not_verified":["suite no ejecutada","269 CORRECTO por categoria no linea a linea","resto de addon/ vs sibling","wheel install","PathFinder vs editable con cwd=copia/tools"]}
```