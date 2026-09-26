# Revisión lote C — ronda 3 — fb-20260829-221423-b2c4

Rama observada: `work/inbox-20260830-modules`.

HEAD observado: `adc1c22f0b890f2132439541d6943ffed430c7d8`.

Intérprete usado en todas las ejecuciones Python:
`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe`.

Directorio de trabajo Python: `DayZ_MCP_dev\tools`, con `PYTHONPATH=.` y
`PYTHONDONTWRITEBYTECODE=1`.

## VEREDICTO

**BLOQUEANTES=0.** B-02 queda cerrado por control negativo ejecutado: retirar el gate exterior por
verbo pone rojo `test_verb_gate_decides_not_the_payload`. El backlog de lista blanca también queda
pineado: sustituir la allowlist por iteración de todas las claves pone rojo
`test_echo_is_an_allowlist_not_an_iteration`. Las 11 pruebas focales y los 196 tests de regresión
solicitados salen verdes; hay 3 skips ambientales declarados. Producción no cambió respecto a la
ronda 2: `tools/dayz_mcp/server.py` conserva exactamente SHA-256
`CA4E442FAB43F5D38318661123370F62A5061BC5DC623C3460BC7975515FC01B`.

## R-01 — mutantes (a) y (d), con restauración manual

No usé `git checkout`, `git restore` ni reset. Apliqué y revertí cada mutación manualmente mediante
parche sobre `tools/dayz_mcp/server.py`; después de cada una calculé el SHA-256 y, al final, repetí
la aplicabilidad reversa de `DIFF-C3.patch`.

### Mutante (a): quitar el gate por verbo

Eliminé temporalmente el `if cmd not in _UI_ECHO_VERBS: return ""` real de
`tools/dayz_mcp/server.py:670-671`. El fixture discriminante lleva escalares no vacíos y un eco
permitido no vacío en `tools/tests/test_ui_error_diagnostics.py:52-59`; el test recorre
`world_spawn`, `player_teleport`, `key_press` y `None` en `:91-99`.

Parche aplicado [EXACT]:

```diff
-    if cmd not in _UI_ECHO_VERBS:
-        return ""
     parts: list[str] = []
```

Comando [EXACT]:

```powershell
$env:PYTHONPATH='.'
$env:PYTHONDONTWRITEBYTECODE='1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' `
  -m unittest `
  tests.test_ui_error_diagnostics.BridgeErrorDiagnosticsTest.test_verb_gate_decides_not_the_payload `
  -v
```

Salida observada:

```text
MUTANT_A_SHA=43393FA00DD51DD464412F0E6B52B86AEFA68AD8C44A17B43FC9DF57D2003CC5
[world_spawn] ... FAIL
[player_teleport] ... FAIL
[key_press] ... FAIL
[None] ... FAIL
AssertionError: "timeout; requested_path='A' matched_path='Root/A'" != 'timeout'
Ran 1 test in 0.001s
FAILED (failures=4)
MUTANT_A_EXIT=1
```

Restauré a mano las dos líneas. Evidencia inmediata:

```text
AFTER_MUTANT_A_RESTORE_SHA256
CA4E442FAB43F5D38318661123370F62A5061BC5DC623C3460BC7975515FC01B
```

**Resultado (a): ROJO, mutante eliminado por el test exigido.**

### Mutante (d): iterar todas las claves del eco

Sustituí temporalmente la iteración de `_UI_ECHO_KEYS` de
`tools/dayz_mcp/server.py:680-683` por iteración de `echo`. El test usa `future_key="x"` y
`requested_text="t"`, ambos no vacíos, en
`tools/tests/test_ui_error_diagnostics.py:120-132`.

Parche aplicado [EXACT]:

```diff
-            f"{key}={echo[key]!r}" for key in _UI_ECHO_KEYS if echo.get(key) not in (None, "")
+            f"{key}={echo[key]!r}" for key in echo if echo.get(key) not in (None, "")
```

Comando [EXACT]:

```powershell
$env:PYTHONPATH='.'
$env:PYTHONDONTWRITEBYTECODE='1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' `
  -m unittest `
  tests.test_ui_error_diagnostics.BridgeErrorDiagnosticsTest.test_echo_is_an_allowlist_not_an_iteration `
  -v
```

Salida observada:

```text
MUTANT_D_SHA=51EAE140CEC87040583B7A7678211D9EBC56BEA285A6FB20C92C5D3D334B463F
test_echo_is_an_allowlist_not_an_iteration ... FAIL
actual:   not_handled; handler='H' user_id=1 clicked=False; requested_path='A' future_key='x' requested_text='t'
esperado: not_handled; handler='H' user_id=1 clicked=False; requested_path='A'
Ran 1 test in 0.001s
FAILED (failures=1)
MUTANT_D_EXIT=1
```

Restauré a mano la iteración por `_UI_ECHO_KEYS`. Evidencia inmediata:

```text
AFTER_MUTANT_D_RESTORE_SHA256
CA4E442FAB43F5D38318661123370F62A5061BC5DC623C3460BC7975515FC01B
REVERSE_CHECK_AFTER_MUTANTS_EXIT=0
```

**Resultado (d): ROJO, mutante eliminado por el test exigido.**

## R-02 — focal de 11 tests y regresión de doce módulos

Comando focal [EXACT]:

```powershell
$env:PYTHONPATH='.'
$env:PYTHONDONTWRITEBYTECODE='1'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' `
  -m unittest tests.test_ui_error_diagnostics tests.test_lote_b_products -v
```

Salida observada:

```text
Ran 11 tests in 0.783s
OK
FOCAL_EXIT=0
```

Los once casos, incluidos los dos tests objetivo de R-01 y el wire negativo
`test_world_spawn_with_a_loaded_payload_stays_bare`, salieron `ok`.

Comando de regresión [EXACT]:

```powershell
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' `
  -m unittest `
  tests.test_d09_d10_spawn_timeout_object_id `
  tests.test_boundary_values_are_pinned `
  tests.test_client_runtime_control_composition `
  tests.test_wait_for tests.test_mcp_tools `
  tests.test_weak_agent_consumer_ux tests.test_docs_truth `
  tests.test_lote_v_products tests.test_tool_registry_fingerprint `
  tests.test_ui_enforce_contract tests.test_bad_args_messages `
  tests.test_ui_click_scriptview -v
```

Salida observada:

```text
Ran 196 tests in 18.837s
OK (skipped=3)
REGRESSION_EXIT=0
```

Los tres skips son las mismas condiciones ambientales explícitas de la ronda 2:

- `SparseAddonDocsTest.test_quickstart_discloses_sparse_addon`: addon presente o no sparse-excluded.
- `SparseAddonDocsTest.test_readme_discloses_sparse_addon`: addon presente o no sparse-excluded.
- `VanillaSymbolCitationsDocsTest.test_symbol_line_citations_resolve`: no está `P:\scripts`.

**Resultado R-02: PASS — 207 tests ejecutados (11 + 196), 204 `ok` y 3 `skipped`; cero fallos.**

## R-03 — identidad del parche y producción sin cambios

La rama y HEAD observados fueron:

```text
work/inbox-20260830-modules
adc1c22f0b890f2132439541d6943ffed430c7d8
HEAD_EXIT=0
```

Ejecuté literalmente `git diff --stat` con `safe.directory` limitado a esta invocación. Salida:

```text
tools/native-launchers/dayz-test-v1/src/app_main.py: Permission denied
tools/native-launchers/dayz-test-v1/src/launcher.cpp: Permission denied
 tools/dayz_mcp/loopback.py            | 189 ++++++++++++++++++--
 tools/dayz_mcp/process_lifecycle.py   | 194 ++++++++++++++-------
 tools/dayz_mcp/server.py              |  68 +++++++-
 tools/tests/test_daemon.py            | 317 ++++++++++++++++++++++++++++++++++
 tools/tests/test_loopback.py          |  57 ++++++
 tools/tests/test_process_lifecycle.py | 209 +++++++++++++++++++++-
 tools/tests/test_session_e2e.py       |  41 ++++-
 7 files changed, 982 insertions(+), 93 deletions(-)
GIT_DIFF_STAT_EXIT=0
```

Ese stat global incluye seis ficheros trackeados ajenos al lote C y no enumera los dos tests
untracked. No los toqué. El estado limitado a los tres paths entregados fue:

```text
 M tools/dayz_mcp/server.py
?? tools/tests/test_lote_b_products.py
?? tools/tests/test_ui_error_diagnostics.py
TARGET_STATUS_EXIT=0
```

Comando de identidad solicitado [EXACT]:

```powershell
git -c safe.directory='C:/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev' `
  -C 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev' `
  apply --reverse --check `
  'C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\review-C\DIFF-C3.patch'
```

Salida observada tanto antes como después de los mutantes:

```text
REVERSE_CHECK_EXIT=0
REVERSE_CHECK_AFTER_MUTANTS_EXIT=0
FINAL_REVERSE_CHECK_EXIT=0
```

Además comparé la identidad Git de cada fichero vivo con el blob postimagen declarado por el
parche; los tres coinciden exactamente:

```text
PATCH index 9e48a28..c752f80  -> server.py|c752f80d3c3d844e3fb8239939ac3282b949fe85
PATCH index 0000000..090baa0  -> test_ui_error_diagnostics.py|090baa0695983eb472d8212fae31b877ec02a2d3
PATCH index 0000000..ecfd372  -> test_lote_b_products.py|ecfd372661131662c2b22c7ddcdbae9660124369
```

Hashes SHA-256 finales:

```text
server.py|CA4E442FAB43F5D38318661123370F62A5061BC5DC623C3460BC7975515FC01B
test_ui_error_diagnostics.py|ECFDC98B715787C5068E0DB8E0D0897197A8E88102E80179EBC1D71E2DAAD342
test_lote_b_products.py|C452B5B96AF59228B02B38966AA35F8F5D3B5AA4FD93671D1E15CD51BED7CDC3
```

`server.py` y `test_lote_b_products.py` conservan los hashes observados en ronda 2; solo cambió el
test de diagnósticos. Dentro del alcance de tres ficheros, el árbol vivo es byte-idéntico a la
postimagen de `DIFF-C3.patch`. **Resultado R-03: PASS.**

## BLOQUEANTES

Ninguno. Los dos mutantes obligatorios tienen repro ejecutado y salen rojos; las puertas de
producto solicitadas permanecen verdes tras restaurar producción.

## BACKLOG

- Cerrado en esta ronda: la exclusión de claves futuras y de `requested_text` queda pineada con
  valores no vacíos, y el mutante que itera todas las claves sale rojo.
- Arrastre no bloqueante de ronda 2, no modificado por `DIFF-C3.patch`: sigue sin existir una
  regresión automatizada específica que ejercite `_await_result` y `probe_bridge_result` de
  `ClientRuntime` con la forma plana. No forma parte de R-01–R-03 y no lo elevé a bloqueante.
- No apareció backlog nuevo dentro del alcance de esta ronda.

## LO QUE NO PUDE VERIFICAR

- No ejecuté DayZ, daemon, procesos de lifecycle ni red, por frontera expresa. Las sesiones MCP de
  los tests fueron exclusivamente en memoria.
- No ejecuté la suite completa del repositorio: ejecuté exactamente los 14 módulos solicitados
  (dos focales y doce de regresión). Tres tests quedaron skipped por sus condiciones de entorno,
  detalladas en R-02.
- `git diff --stat` no pudo leer dos ficheros bajo `tools/native-launchers/dayz-test-v1/src/` por
  `Permission denied`. Esto impide certificar el contenido íntegro de esos paths ajenos, pero no la
  identidad de los tres ficheros C3: sus blobs casan con el patch y el reverse-check sale 0.
- No puedo afirmar que el worktree global contenga únicamente `DIFF-C3.patch`: el stat demuestra
  seis ficheros trackeados adicionales fuera del lote. Sí puedo afirmar, con hashes de blob y
  reverse-check, que los tres paths del lote son exactamente la postimagen entregada.
- No actualicé memoria del vault: esta ronda no añade API, decisión ni mecanismo durable; el único
  resultado nuevo es el cierre de cobertura y queda conservado en este dictamen solicitado.
