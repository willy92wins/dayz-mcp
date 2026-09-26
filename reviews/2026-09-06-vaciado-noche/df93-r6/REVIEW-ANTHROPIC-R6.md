DICTAMEN: SEGURO INTEGRAR
Motivo: el producto cumple el contrato (a)/(b)/(c) medido en 41 casos ejecutados contra los dos árboles (rama (b) con CERO lecturas bajo tres instrumentos, rama (c) idéntica a a7ddbac por AST y por ejecución, hints exactos), el rojo-primero cae justo en los 9 tests del contrato nuevo y los 4 mutantes mueren; los hallazgos son uno MEDIA de hermeticidad en un test (no del producto) y dos BAJA de redacción fuera del write-set.

# Revisión cruzada ciega — lote df93, ronda 6 (revisor: familia Anthropic)

Rutas abreviadas: `<lote>` = `C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-df93-r6`; `<ws>` = `<lote>\ws`; `<rev>` = `<lote>\review-r6`; `<wt>` = `C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\e89adcdf-b230-4060-83b7-f733911e169a\scratchpad\wt-df93`; `<py>` = `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe` (Python 3.14.3).

Método: nada del STATE.md se da por bueno; cada cifra sale de un comando ejecutado por mí, con su salida literal (las salidas largas están completas en los ficheros de `<rev>` que se citan). `<ws>` y `<wt>` no se han modificado: las ejecuciones desde `<ws>\tools` llevaron `PYTHONDONTWRITEBYTECODE=1` y un censo por mtime posterior dio 0 ficheros nuevos (§0.4); los mutantes y el rojo-primero corrieron sobre copias bajo `<rev>` (§0.5).

## Hallazgos

### H1 — MEDIA — El test del chokepoint renombrado (T9) depende del estado del host, no de la construcción del caso

- `<ws>\tools\tests\test_vpp_preflight.py:881-891` (`test_a_server_request_with_unusable_admin_tools_never_takes_a_lease`), helper `_transaction` en `:860-879`, que llama a `execute_native_launcher_transaction` SIN seam de ficheros → la puerta lee el host real (`HostVppFiles`, `native_launcher_transaction.py:186-194`).
- Premisa del brief §3 T9: «la policy stub apunta a rutas que no existen en el host». En este host es inexacta a medias:

```
$ for p in /p/Suite /p/Suite/_server/serverDZ.cfg /p/Mods /p/Mods/@VPPAdminTools /p/Mods/@VPPAdminTools/meta.cpp; do [ -e "$p" ] && echo "EXISTS : $p" || echo "absent : $p"; done
absent : /p/Suite
absent : /p/Suite/_server/serverDZ.cfg
EXISTS : /p/Mods
EXISTS : /p/Mods/@VPPAdminTools
EXISTS : /p/Mods/@VPPAdminTools/meta.cpp
$ cmd //c subst
P:\: => C:\Users\guill\OneDrive\Documentos\DayZ Projects
```

- Veredicto real de la puerta para ese request en este host (`<rev>\q4_raised.py`, salida en `<rev>\q4-ws.jsonl`): la identidad se PRUEBA con el `meta.cpp` real y el rechazo descansa solo en el cfg ausente:

```
{"real_host_verdict_for_VPP_case": {"error_code": "vpp_preflight_failed", "missing": ["server_config"], "warnings": ["vpp_superadmins_absent", "vpp_credentials_absent"]}}
```

- Repro ejecutado (`<rev>\q4b_host_dependence.py`: parchea `HostVppFiles.read_text` para servir desde memoria un `P:\Suite` sano — cfg con `vppDisablePassword = 1`, SuperAdmins y credentials — sin crear ningún fichero; salida completa en `<rev>\q4b-host-dependence.txt`):

```
$ ( cd <ws>/tools && PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 "<py>" <rev>/q4b_host_dependence.py tests.test_vpp_preflight.VppPreflightChokepointTest.test_a_server_request_with_unusable_admin_tools_never_takes_a_lease )
test_a_server_request_with_unusable_admin_tools_never_takes_a_lease (...) ... FAIL
AssertionError: 'invalid_dayz_test_path_authority' != 'vpp_preflight_failed'
paths served from memory instead of the host: ['P:\\Suite\\_server\\serverDZ.cfg', 'P:\\Suite\\_server\\profiles\\VPPAdminTools\\Permissions\\SuperAdmins\\SuperAdmins.txt', 'P:\\Suite\\_server\\profiles\\VPPAdminTools\\Permissions\\credentials.txt']
verdict with a healthy P:\Suite in memory: RED

$ (control: el MISMO parche sobre el T9 viejo del baseline, tree-base)
OK
verdict with a healthy P:\Suite in memory: GREEN
```

- Qué debería pasar: el rechazo del chokepoint debería estar garantizado por construcción, como hace T8 (`:791-812`, policy sobre un `TemporaryDirectory`) — p. ej. `_transaction` aceptando una policy cuyo `dev_root`/`mod_roots` apunten a un temporal o a una raíz inexistente, de modo que `vpp_mod_folder` rechace con independencia del host. Hoy el test es verde en este host solo porque `P:\Suite` no existe; en un host donde exista con un cfg sano el test falla sin que el producto haya cambiado. No es un defecto del producto (la puerta decide bien en ambos escenarios), es hermeticidad del test; el T9 de a7ddbac no tenía esta dependencia (el token `vpp_mod_not_requested` salía del request).

### H2 — BAJA — Dos docstrings siguen describiendo la puerta como un rechazo incondicional (fuera del alcance que el brief dio al implementador)

- `<ws>\tools\dayz_mcp\native_launcher_transaction.py:221-224` (`mode_starts_server`): «An unknown name fails closed as a server start: the gate refuses rather than wave through a mode it cannot classify». Tras la ronda 6 eso solo es cierto si el request pide VPP; sin VPP, un modo desconocido que llegue a `evaluate_vpp_preflight` se avisa (medido en Q3, caso q3-2). El brief §2 ordenó no tocar las frases de «modos desconocidos», así que no es incumplimiento del implementador; queda para el receptor.
- `<ws>\tools\tests\test_vpp_preflight.py:10-13` (docstring del módulo, idéntico a a7ddbac): «These checks pin the replacement: a read-only refusal in native_launcher_transaction ... plus warnings for the seeding». Ahora el rechazo es condicional a lo pedido. No estaba en la lista de frases del brief.
- Qué debería pasar: una línea en cada sitio («refuses only when the request names an admin tool; otherwise warns»). Sin efecto en código.

### H3 — BAJA (observación, fuera del write-set) — El aviso nuevo es invisible en la ruta CLI y su hint no llega al llamador MCP en el caso de éxito

- `native_launcher_transaction.py:615`: `enforce_vpp_preflight(parsed.payload, semantic_policies)` descarta el `VppPreflightResult`; `grep -n "vpp\|Vpp" secure_launcher.py` no devuelve nada. En la ruta CLI, «se avisa» no es observable (ya pasaba con `vpp_superadmins_absent`/`vpp_credentials_absent` en a7ddbac). El contrato exige el call-site intacto, así que esto no es un hallazgo contra la entrega.
- Ruta MCP: `dayz_test_tool.py:1191-1192, 1304-1305, 1368-1369` propagan `vpp_warnings`; `remediation=vpp.hint` solo en el fallo (`:1273`). El texto de `VPP_ABSENT_HINT` no viaja en el éxito; el llamador ve el token `vpp_mod_not_requested` a secas (T10-bis lo fija así). Para el receptor: si se quiere que el hint del aviso llegue, es un cambio en `dayz_test_tool.py`.

Sin hallazgos CRITICA ni ALTA. Write-set respetado (§0.2); ningún aserto debilitado fuera de la lista permitida (Q5).

## 0. Entradas verificadas

### 0.1 Punto de partida y baselines byte-exactos

```
$ git -C <wt> rev-parse HEAD
a7ddbac2c455a0416a437a51c63f836c06d37270
$ git -C <wt> branch --show-current
work/df93-vpp-preflight
$ git -C <wt> status --porcelain -- tools/dayz_mcp/native_launcher_transaction.py tools/tests/test_vpp_preflight.py
(vacío: los dos ficheros del write-set están limpios en el worktree)
$ git -C <wt> ls-tree HEAD tools/dayz_mcp/native_launcher_transaction.py tools/tests/test_vpp_preflight.py tools/dayz_mcp/dayz_test_request.py
100644 blob f2d865e69eaad355af5b0ec526f76750a01d4bfe	tools/dayz_mcp/dayz_test_request.py
100644 blob b3cbd2c474219e4b5ccaf4b078480f1c9f90f291	tools/dayz_mcp/native_launcher_transaction.py
100644 blob 616aa3b1df26ca0a71134d9db525a953091ac659	tools/tests/test_vpp_preflight.py
```

Baselines extraídos con `git -C <wt> show HEAD:<ruta>` a `<rev>\baseline\...` y verificados contra los blobs (checksum, no mtime):

```
$ git hash-object <rev>/baseline/tools/dayz_mcp/native_launcher_transaction.py <rev>/baseline/tools/tests/test_vpp_preflight.py <rev>/baseline/tools/dayz_mcp/dayz_test_request.py
b3cbd2c474219e4b5ccaf4b078480f1c9f90f291
616aa3b1df26ca0a71134d9db525a953091ac659
f2d865e69eaad355af5b0ec526f76750a01d4bfe
```

### 0.2 La entrega: hashes, finales de línea y write-set

```
$ sha256sum <ws>/tools/dayz_mcp/native_launcher_transaction.py <ws>/tools/tests/test_vpp_preflight.py
3ca12ca20597a09de932cb147df7d509ffc8c49125923758905b70a79fe93c05  native_launcher_transaction.py   (715 líneas; baseline 700)
a3fe333dc0b7ae93b94accf3bef2a7b498876eb340b5f8097c315dd58565a2b5  test_vpp_preflight.py           (1535 líneas; baseline 1396)
$ grep -c $'\r' <ws>/tools/dayz_mcp/native_launcher_transaction.py ; grep -c $'\r' <ws>/tools/tests/test_vpp_preflight.py
0
0
```

(Coinciden con los sha256 que declara STATE.md; los baselines también tienen 0 CR.)

Write-set, medido por CONTENIDO contra el worktree, no por mtime:

```
$ diff -rq -x __pycache__ -x .pytest_cache <wt>/tools <ws>/tools
Only in <wt>/tools: .venv-mcp
Files <wt>/tools/dayz_mcp/native_launcher_transaction.py and <ws>/tools/dayz_mcp/native_launcher_transaction.py differ
Files <wt>/tools/tests/test_vpp_preflight.py and <ws>/tools/tests/test_vpp_preflight.py differ
$ (raíz de <ws>, fichero a fichero contra <wt>)
ONLY IN WS: BRIEF.txt
ONLY IN WS: STATE.md
```

Censo por mtime posterior a `run1/STARTED` (1788718107 = 2026-09-06 20:08:27 +0200): 58 ficheros, de los cuales 55 son `__pycache__/*.pyc` (bytecode generado por las ejecuciones de tests que el brief exige), más los 2 del write-set y `STATE.md`. Ningún otro fuente. `dayz_test_request.py` de `<ws>` es idéntico al baseline (`diff` vacío).

### 0.3 Los patches del receptor son los que yo obtengo

```
$ diff -u <rev>/baseline/.../native_launcher_transaction.py <ws>/.../native_launcher_transaction.py > <rev>/my-product.diff
$ diff <(tail -n +3 <rev>/my-product.diff) <(tail -n +3 <lote>/DIFF-R6-product.patch) && echo IDENTICAL_BODIES
IDENTICAL_BODIES
$ (ídem tests → <rev>/my-tests.diff)
IDENTICAL_BODIES
```

### 0.4 Ejecuciones desde `<ws>\tools` (PYTHONPATH=. , PYTHONDONTWRITEBYTECODE=1) y prueba de que `<ws>` no cambió

```
$ ( cd <ws>/tools && PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 PYTHONIOENCODING=utf-8 "<py>" -m unittest -v tests.test_vpp_preflight )   # <rev>/run-ws-vpp-verbose.txt
----------------------------------------------------------------------
Ran 64 tests in 2.081s

OK
$ ( cd <ws>/tools && ... -m unittest tests.test_native_launcher_transaction tests.test_dayz_test_tool tests.test_dayz_test_tool_modes tests.test_lifecycle_reconcile )   # <rev>/run-ws-regression.txt
.................................................................................................................................
----------------------------------------------------------------------
Ran 129 tests in 0.995s

OK
$ find <ws> -type f -newer <rev>/_marker_before_ws_runs | wc -l
0
```

Ningún método con «acceso denegado» en mi corrida.

### 0.5 Árboles de trabajo bajo `<rev>` (nunca `<ws>` ni `<wt>`)

- `tree-base/tools`: copia de `<ws>/tools` (sin `__pycache__`) con producto Y tests = a7ddbac (`git hash-object` → `b3cbd2c…`, `616aa3b…`).
- `tree-redfirst/tools`: producto = a7ddbac (`b3cbd2c…`), tests = entrega (`a3fe333…`).
- `tree-mut/tools`: producto y tests = entrega (`3ca12ca…`, `a3fe333…`).

Helpers propios del revisor (`<rev>\r6helpers.py`): copias de `_policy/_raw/_sealed/FakeFiles/_healthy/META_*` con las mismas formas que `test_vpp_preflight.py:37-133`, para que ambos árboles reciban entradas idénticas sin importar el fichero de tests.

## Q1 — Lecturas en la rama (b): CERO, medido con tres instrumentos

Script `<rev>\q1_reads.py`; salidas completas en `<rev>\q1-ws.jsonl` (entrega) y `<rev>\q1-base.jsonl` (baseline, control positivo). Instrumentos por caso: (1) `FakeFiles.reads` y `HostVppFiles.read_text` envuelto (lecturas pedidas al seam, falso o real); (2) contadores sobre `_read_or_absent`, `vpp_preflight_paths`, `_resolved_mod_path`, `_proves_vpp_identity` (globales del módulo parcheados); (3) `sys.addaudithook` capturando TODO evento de auditoría (`open`, `os.*`) mientras corre la puerta.

Entrega, los diez casos de la rama (b) — `mode="all"` y `"server"`, con y sin `preflight=True`, con `FakeFiles` vacío, con `FakeFiles` sano (los ficheros existen y deben quedar sin leer), con `base_mods` sin VPP, sin `extra_mods`, con policy cuyo `default_base_mods` trae VPP pero `no_base_mods=True`, y con `files=None` (seam real):

```
{"case": "b1 all/no VPP/launch/FakeFiles empty", ..., "result": {"error_code": null, "missing": [], "warnings": ["vpp_mod_not_requested"], "hint": "VPP_ABSENT_HINT"}, "fake_reads": [], "host_seam_reads": [], "helper_calls": {}, "audit_events": []}
{"case": "b2 all/no VPP/preflight=True/FakeFiles empty", ..., "result": {"error_code": null, "missing": [], "warnings": ["vpp_mod_not_requested"], "hint": "VPP_ABSENT_HINT"}, "fake_reads": [], "host_seam_reads": [], "helper_calls": {}, "audit_events": []}
{"case": "b3 server/no VPP/launch/FakeFiles empty", ..., "fake_reads": [], "host_seam_reads": [], "helper_calls": {}, "audit_events": []}
{"case": "b4 server/no VPP/preflight=True/FakeFiles empty", ..., "fake_reads": [], "host_seam_reads": [], "helper_calls": {}, "audit_events": []}
{"case": "b5 all/no VPP/healthy FakeFiles (files exist, must stay unread)", ..., "fake_reads": [], "host_seam_reads": [], "helper_calls": {}, "audit_events": []}
{"case": "b6 all/base_mods [@CF] + extra [@DayZ_MCP]", ..., "fake_reads": [], "host_seam_reads": [], "helper_calls": {}, "audit_events": []}
{"case": "b7 all/only @ExampleMod (no extra, no base)", ..., "fake_reads": [], "host_seam_reads": [], "helper_calls": {}, "audit_events": []}
{"case": "b8 all/policy default has VPP but no_base_mods=True", ..., "fake_reads": [], "host_seam_reads": [], "helper_calls": {}, "audit_events": []}
{"case": "b9 all/no VPP/files=None (real seam)", ..., "files": "None(real HostVppFiles)", "result": {"error_code": null, "missing": [], "warnings": ["vpp_mod_not_requested"], "hint": "VPP_ABSENT_HINT"}, "fake_reads": null, "host_seam_reads": [], "helper_calls": {}, "audit_events": []}
{"case": "b10 server/no VPP/preflight=True/files=None (real seam)", ..., "host_seam_reads": [], "helper_calls": {}, "audit_events": []}
```

Los diez devuelven `error_code=None, missing=[], warnings=["vpp_mod_not_requested"], hint=VPP_ABSENT_HINT` con las tres listas de lecturas vacías y sin llamada a ningún helper.

Controles positivos (el instrumento ve las lecturas cuando las hay):

```
entrega, rama (c) con FakeFiles sano:   "fake_reads": ["P:\\Mods\\@VPPAdminTools\\meta.cpp", "P:\\Suite\\_server\\serverDZ.cfg", "...\\SuperAdmins.txt", "...\\credentials.txt"], "helper_calls": {"_resolved_mod_path": 1, "_proves_vpp_identity": 1, "_read_or_absent": 4, "vpp_preflight_paths": 1}
entrega, rama (c) con seam real y raíz fantasma Z:\__r6_nonexistent__:   "host_seam_reads": [4 rutas], "audit_events": [["open", ["'Z:\\\\__r6_nonexistent__\\\\Mods\\\\@VPPAdminTools\\\\meta.cpp'", "'r'", "32896"]], ["open", [...serverDZ.cfg...]], ["open", [...SuperAdmins...]], ["open", [...credentials.txt...]]]
baseline a7ddbac, MISMO caso b1:   "result": {"error_code": "vpp_preflight_failed", "missing": ["vpp_mod_not_requested", "server_config"], ...}, "fake_reads": ["P:\\Suite\\_server\\serverDZ.cfg", "...\\SuperAdmins.txt", "...\\credentials.txt"], "helper_calls": {"vpp_preflight_paths": 1, "_read_or_absent": 3}
baseline a7ddbac, caso b9 (seam real):   "audit_events": [["open", ["'P:\\\\Suite\\\\_server\\\\serverDZ.cfg'", "'r'", "32896"]], ...]
```

Traza estática (`<ws>\tools\dayz_mcp\native_launcher_transaction.py`): antes del retorno de `:464-470` solo corren `mode_starts_server` (`:453`, resuelve en memoria contra `dayz_test_modes.MODE_RECORDS`), la construcción `HostVppFiles()` (`:457`, sin I/O: su único método es `read_text`), `effective_mod_entries` (`:461-463`, lecturas de dict) y `_is_vpp_candidate` (`ntpath.basename`, puro). `_resolved_mod_path` (`:471`), `vpp_preflight_paths` (`:490`) y `_read_or_absent` (`:491, :504, :509`) quedan todos después del `return`. No hay ningún otro camino: `if not requested:` es el único punto de decisión entre `:461` y `:471`.

## Q2 — Rama (c) inalterada: por texto, por AST y por ejecución

Texto ignorando espacios (`diff -w -u`, salida en `<rev>\my-product-w.diff`, 6 hunks): dentro de `evaluate_vpp_preflight` solo cambian el docstring y estas líneas:

```
     if not requested:
-        missing.append("vpp_mod_not_requested")
-    else:
+        return VppPreflightResult(
+            error_code=None,
+            missing=(),
+            warnings=("vpp_mod_not_requested",),
+            hint=VPP_ABSENT_HINT,
+        )
         resolved = [_resolved_mod_path(entry, policy) for entry in requested]
```

Por definición (`<rev>\q2_ast.py`, salida en `<rev>\q2-ast.txt`):

```
== IDENTICAL_SOURCE (26) ==   (entre ellas) _is_vpp_candidate, _live_assignments, _proves_vpp_identity, _read_or_absent, _resolved_mod_path, _skip_non_string_dead_ground, _vpp_password_is_disabled, effective_mod_entries, mode_starts_server, preflight_vpp_payload, preflight_vpp_request, select_request_policy, vpp_preflight_paths, HostVppFiles, HostVppFiles.read_text, VppPreflightResult, VppPreflightPaths
== SAME_AST (2) ==   enforce_vpp_preflight (base@557 -> new@570)   execute_native_launcher_transaction (base@575 -> new@589)   [solo docstring/comentario]
== DIFFERENT_AST (1) ==   evaluate_vpp_preflight (base@432 -> new@439)
== ONLY_BASE (0) ==   == ONLY_NEW (0) ==
== module-level constants ==   NEW VPP_ABSENT_HINT   CHANGED VPP_PREFLIGHT_HINT   (unchanged constants: 18)
```

Prueba exacta (`<rev>\q2_ast_transform.py`): al baseline se le aplica SOLO el retorno temprano (sustituir `missing.append(...)` por el `Return` de la entrega e izar el `else`) y se compara con la entrega:

```
delivery branch (b) body: if not requested:
        return VppPreflightResult(error_code=None, missing=(), warnings=('vpp_mod_not_requested',), hint=VPP_ABSENT_HINT)
baseline branch (b) body: missing.append('vpp_mod_not_requested')
baseline + early-return-only == delivery (AST, docstrings stripped): True
statements in function body: baseline(after hoist) = 16  delivery = 16
```

Por ejecución (`<rev>\q2_cases.py` + `<rev>\q2b_cases.py`, un proceso por árbol con su propio `PYTHONPATH`; comparación campo a campo con `<rev>\q2_compare.py`; salidas en `<rev>\q2-compare.txt` y `<rev>\q2b-compare.txt`). Los ocho casos que pide el brief más 22 adicionales de la rama (c), 3 de la rama (a) y 4 de la rama (b):

```
[SAME-TOKENS] c01 META_OTHER (foreign directory with the right name): error_code='vpp_preflight_failed' missing=['vpp_mod_identity'] warnings=[] hint=VPP_PREFLIGHT_HINT reads=4
[SAME-TOKENS] c02 folder absent (no meta.cpp): error_code='vpp_preflight_failed' missing=['vpp_mod_folder'] warnings=[] hint=VPP_PREFLIGHT_HINT reads=4
[SAME-TOKENS] c03 relative entry under several roots: error_code='vpp_preflight_failed' missing=['vpp_mod_root_ambiguous'] warnings=[] hint=VPP_PREFLIGHT_HINT reads=3
[SAME-TOKENS] c04 cfg without the key: error_code='vpp_preflight_failed' missing=['vpp_disable_password'] warnings=[] hint=VPP_PREFLIGHT_HINT reads=4
[SAME-TOKENS] c05 cfg key = 0: error_code='vpp_preflight_failed' missing=['vpp_disable_password'] warnings=[] hint=VPP_PREFLIGHT_HINT reads=4
[SAME-TOKENS] c06 cfg bigger than the read cap: error_code='vpp_preflight_failed' missing=['server_config_unverifiable'] warnings=[] hint=VPP_PREFLIGHT_HINT reads=4
[SAME-TOKENS] c07 superadmins absent: error_code=None missing=[] warnings=['vpp_superadmins_absent'] hint=VPP_PREFLIGHT_HINT reads=4
[SAME-TOKENS] c08 all healthy: error_code=None missing=[] warnings=[] hint=VPP_PREFLIGHT_HINT reads=4
[SAME-TOKENS] c09 cfg absent ... missing=['server_config']          [SAME-TOKENS] c10 cfg unreadable (OSError) ... missing=['server_config_unreadable']
[SAME-TOKENS] c11 meta.cpp unreadable (OSError) ... missing=['vpp_mod_folder']   [SAME-TOKENS] c12 credentials absent ... warnings=['vpp_credentials_absent']
[SAME-TOKENS] c13 two live assignments that disagree ... missing=['vpp_disable_password']
[SAME-TOKENS] c16 VPP in base_mods ... clean   [SAME-TOKENS] c17 VPP from policy default_base_mods ... clean   [SAME-TOKENS] c18 mode=server healthy ... clean   [SAME-TOKENS] c19 preflight=True healthy ... clean
[SAME-TOKENS] c20 everything wrong at once (token order): error_code='vpp_preflight_failed' missing=['vpp_mod_identity', 'vpp_disable_password'] warnings=['vpp_superadmins_absent', 'vpp_credentials_absent'] hint=VPP_PREFLIGHT_HINT reads=4
[SAME-TOKENS] c21 publishedid with a suffix ... missing=['vpp_mod_identity']   [SAME-TOKENS] c23 key only in a comment ... missing=['vpp_disable_password']
[SAME-TOKENS] c14b Workshop-id absolute path under the root, healthy ... clean reads=4   [SAME-TOKENS] c14c Workshop-id absolute path, meta.cpp elsewhere ... missing=['vpp_mod_folder', 'server_config']
[SAME-TOKENS] c15b absolute path under a multi-root policy ... clean   [SAME-TOKENS] c22b two candidates (folder=META_OTHER, workshop=META_VPP) ... clean reads=5
[SAME-TOKENS] c22c two candidates, both foreign ... missing=['vpp_mod_identity'] reads=5   [SAME-TOKENS] c24 meta.cpp with two publishedid assignments ... missing=['vpp_mod_identity']   [SAME-TOKENS] c25 cfg comments between tokens (ronda 5) ... clean
[SAME-TOKENS] a01 client with run_id / a02 offline / a03 client with VPP requested: error_code=None missing=[] warnings=[] hint=VPP_PREFLIGHT_HINT reads=0
[DIFFERENT  ] b01..b04 (all/server/preflight/no_base_mods, sin VPP): baseline error_code='vpp_preflight_failed' missing=['vpp_mod_not_requested'] hint=VPP_PREFLIGHT_HINT reads=3 cfg/SuperAdmins/credentials  →  entrega error_code=None missing=[] warnings=['vpp_mod_not_requested'] hint=VPP_ABSENT_HINT reads=[]
cases with identical decision fields: 29 of 34   (q2)   +   7 of 7   (q2b)
```

`SAME-TOKENS` = `error_code`, `missing`, `warnings`, `hint_kind` y la lista ORDENADA de rutas leídas son iguales en los dos árboles; el texto del hint difiere solo por el arranque reescrito. Los 5 `DIFFERENT` son los 4 casos de la rama (b) (el cambio de contrato) y q3-2 (Q3). Tres casos de mi primera tanda (c14/c15/c22, ruta absoluta `C:\Steam\...` fuera de los `mod_roots`) lanzaron `ValueError: invalid_dayz_test_request` en AMBOS árboles — la capa de request los rechaza antes de la puerta (`dayz_test_request.py:167-170, 139-150`) — y se repitieron en q2b con la forma que aceptan los tests existentes (`P:\Mods\1828439124`).

Constantes (`<rev>\q_hints.py`, salida en `<rev>\q-hints.txt`):

```
VPP_ABSENT_HINT in baseline: False
VPP_ABSENT_HINT == brief [EXACT]: True
contains '@VPPAdminTools': True
contains 'refuse' (case-insensitive): False
old PREFLIGHT_HINT opening: 'this mode starts a server and the admin tools are not usable:'
new PREFLIGHT_HINT opening: 'the requested admin tools are not usable:'
new opening == 'the requested admin tools are not usable:': True
rest of PREFLIGHT_HINT unchanged: True
VPP_PREFLIGHT_FAILED unchanged: True
```

Conjunto de literales de token (`grep -o '"vpp_[a-z_]*"\|"server_config[a-z_]*"' | sort | uniq -c`): los mismos 11 literales, una vez cada uno, en baseline y entrega (ningún token renombrado ni añadido).

## Q3 — Modo desconocido sin VPP: hueco teórico, no real

Cita de la capa de request, `<ws>\tools\dayz_mcp\dayz_test_request.py`:

```
276:    default_mode, request_mode_names = _request_mode_view()
277:    mode = value.get("mode", default_mode)
297:    if mode not in request_mode_names:
298:        _invalid()                                   # → ValueError("invalid_dayz_test_request"), :59-60
 67: def _request_mode_view():  ... except dayz_test_modes.ModeAuthorityError: _invalid()   # autoridad rota → también inválido, :67-74
```

Todos los callers de producción de la puerta parsean primero con esa función: `execute_native_launcher_transaction` (`native_launcher_transaction.py:606-608` → `:615`), `preflight_vpp_request` (`:564-567`, único uso en `dayz_test_tool.py:1256`), y el worker sellado vuelve a parsear el request (`dayz_test_worker.py:509`). El probe `tools/h9_native_probe.py:217` (fichero NO versionado en a7ddbac: `git show HEAD:tools/h9_native_probe.py` → «exists on disk, but not in 'HEAD'») también pasa `parsed.payload`. Un modo desconocido solo llega a `evaluate_vpp_preflight` con un payload construido a mano (tests) o si `MODE_RECORDS` cambiara entre el parse (`:606`) y la puerta (`:615`) dentro del mismo proceso, cosa que ningún código de producción hace.

Medido (q2, ambos árboles):

```
q3-1 raw request con mode='not-a-mode' (capa de request):   baseline y entrega → ValueError: invalid_dayz_test_request   (reads=[])
q3-2 evaluate_vpp_preflight directo, 'not-a-mode', sin VPP:  baseline → ModeAuthorityError: unknown mode (la vieja rama seguía hasta vpp_preflight_paths, :266)   |   entrega → error_code=None warnings=['vpp_mod_not_requested'] hint=VPP_ABSENT_HINT
q3-3 evaluate_vpp_preflight directo, 'not-a-mode', con VPP:  baseline y entrega → ModeAuthorityError: unknown mode   (reads=['P:\\Mods\\@VPPAdminTools\\meta.cpp'])
q3-4 mode_starts_server('not-a-mode'):                       True en ambos
```

Conclusión: en producción el nombre desconocido muere en `:297` antes de la puerta en las tres rutas; el único escenario en que la ronda 6 cambia algo (payload sin parsear + modo desconocido + sin VPP) pasaba en a7ddbac de una excepción `ModeAuthorityError` (ni siquiera el token limpio) a un aviso, y aguas abajo el worker sellado repite el parse (`dayz_test_worker.py:509`). Teórico. El test `VppPreflightInvariantTest.test_an_unknown_mode_is_treated_as_a_server_start` (`:987`) sigue byte a byte y verde. Residuo documental: H2.

## Q4 — Embudo y lease: `raised` impreso, no un `assertNotEqual` vacío

`<rev>\q4_raised.py` reproduce `_transaction` (`test_vpp_preflight.py:860-879`) imprimiendo `raised` (salidas en `<rev>\q4-ws.jsonl` y `<rev>\q4-base.jsonl`):

```
entrega:
{"case": "VPP requested, unusable on this host", "raised_type": "ValueError", "raised_str": "vpp_preflight_failed", "is_VPP_PREFLIGHT_FAILED": true, "acquire_calls": 0, "consumer_calls": 0}
{"case": "no VPP requested", "raised_type": "ValueError", "raised_str": "invalid_dayz_test_path_authority", "is_VPP_PREFLIGHT_FAILED": false, "acquire_calls": 0, "consumer_calls": 0}
{"case": "offline", "raised_type": "ValueError", "raised_str": "invalid_dayz_test_path_authority", "is_VPP_PREFLIGHT_FAILED": false, "acquire_calls": 0, "consumer_calls": 0}
{"case": "client", "raised_type": "ValueError", "raised_str": "invalid_dayz_test_path_authority", "is_VPP_PREFLIGHT_FAILED": false, "acquire_calls": 0, "consumer_calls": 0}
baseline:
{"case": "no VPP requested", "raised_type": "ValueError", "raised_str": "vpp_preflight_failed", "is_VPP_PREFLIGHT_FAILED": true, "acquire_calls": 0, "consumer_calls": 0}
```

El caso sin VPP en la entrega no devuelve `None`: pasa la puerta y muere en la acreditación de rutas de las policies stub (`invalid_dayz_test_path_authority`), exactamente donde mueren `offline` y `client` — «lo que falle después es de otro». `acquire_calls == 0` en todos porque la acreditación precede al lease. Los dos tests del chokepoint, verbose desde `<ws>`:

```
test_a_server_request_with_unusable_admin_tools_never_takes_a_lease (...) ... ok
test_a_server_request_without_admin_tools_gets_past_the_gate (...) ... ok
Ran 4 tests in 0.037s
OK
```

Pero el primero mide contra el host real, y aquí `P:\Mods\@VPPAdminTools\meta.cpp` existe: ver H1 (con un `P:\Suite` sano en memoria el test se pone rojo).

## Q5 — Tests: lista permitida, forma, recuento y rojo-primero

Comparación por método con `<rev>\q5_methods.py` (fuente exacta de cada método; salida en `<rev>\q5-methods.txt`):

```
module docstring identical: True
== UNCHANGED (84) ==  todos los helpers (_policy, _raw, _sealed, _healthy, FakeFiles.*, _tree_digest, _steam_ok, _Opened/_Bundle/_Runtime/_CountingControlClient), TODOS los métodos de VppPreflightInvariantTest (3), VppPreflightCallSiteTest (3 + _callers), VppPreflightRound4Test (4 + helpers), VppPreflightRound5Test (5 + helpers), y el resto de métodos no listados en el brief (DecisionTest 32, EnforcementTest 2, ChokepointTest 2 + _transaction, DayzTestRunVppGateTest.test_warnings_travel_with_a_successful_run)
== CHANGED (5) ==
   <module>._refused (base@1122 -> new@1261)                                                    [T10: stub, permitido]
   VppPreflightDecisionTest.test_a_preflight_request_fails_exactly_where_a_launch_would (404 -> 409)   [T4, permitido]
   VppPreflightDecisionTest.test_no_base_mods_drops_a_default_that_only_lived_in_the_policy (192 -> 197)   [T3, permitido]
   VppPreflightEnforcementTest.test_enforce_raises_the_declared_token (717 -> 751)              [T7, permitido]
   VppPreflightEnforcementTest.test_the_refusal_writes_nothing_to_the_workspace (740 -> 791)   [T8, permitido]
== REMOVED from baseline (4) ==  los 4 renombrados: ..._is_refused (153), ..._is_refused_the_same_way (160), ..._without_admin_tools_never_takes_a_lease (808), ..._without_admin_tools_never_reaches_the_launcher (1035)
== ADDED in delivery (10) ==  los 4 renombrados (153, 162, 881, 1118) + 6 nuevos: test_a_preflight_request_without_admin_tools_is_warned_like_a_launch (428), test_a_run_that_requests_no_admin_tools_reads_nothing (438), test_enforce_returns_a_warning_result_when_no_admin_tools_are_requested (766), test_the_warning_writes_nothing_to_the_workspace (814), test_a_server_request_without_admin_tools_gets_past_the_gate (893), test_the_absent_admin_tools_warning_travels_with_a_successful_run (1204)
== test_ methods per class, baseline: total 58 ==   == delivery: total 64 ==
```

Forma de cada cambio (comprobada contra `<lote>\DIFF-R6-tests.patch`, idéntico a mi diff): T1/T2 con los cinco asertos del brief (`error_code` None, `missing == ()`, `warnings == ("vpp_mod_not_requested",)`, `"@VPPAdminTools" in hint`, `hint == VPP_ABSENT_HINT`); T3 conserva nombre y caso `no_base_mods` y cambia solo las dos últimas líneas; T4 pasa a VPP pedido + cfg sin clave con `preflight=True` → `VPP_PREFLIGHT_FAILED` y `"vpp_disable_password" in missing`; T5 usa `FakeFiles().reads` (ya contaba); T6 no duplicado — el test citado existe: `test_a_foreign_directory_with_the_right_name_is_refused` (`:255-276`, VPP pedido en las dos formas + `META_OTHER` → `vpp_mod_identity`) y `VppPreflightRound4Test.test_meta_cpp_forms_that_do_not_prove_the_identity` (`:1331`); T7 pide VPP con `FakeFiles()` vacío; T8 pide VPP sobre el temporal vacío; T9 renombrado con VPP y asertos `acquire_calls == 0` / `consumer_calls == []` conservados; T10 renombrado, `extra_mods` con VPP, aserto `["vpp_disable_password"]`, stub `_refused` con `missing=("vpp_disable_password",)`; T10-bis copia de `test_warnings_travel_with_a_successful_run` con stub `warned`. Ningún aserto debilitado fuera de la lista.

Recuento y rojo-primero, ejecutados por mí:

```
$ ( cd <rev>/tree-base/tools && PYTHONPATH=. ... -m unittest tests.test_vpp_preflight )      # a7ddbac sobre a7ddbac
Ran 58 tests in 2.508s
OK
$ ( cd <rev>/tree-redfirst/tools && PYTHONPATH=. ... -m unittest -v tests.test_vpp_preflight )   # tests de la entrega sobre el PRODUCTO de a7ddbac; <rev>/q5-redfirst-verbose.txt
rc=1
ERROR: test_enforce_returns_a_warning_result_when_no_admin_tools_are_requested          [T7-bis]  ValueError: vpp_preflight_failed
ERROR: test_the_absent_admin_tools_warning_travels_with_a_successful_run                [T10-bis] AttributeError: module 'dayz_mcp.native_launcher_transaction' has no attribute 'VPP_ABSENT_HINT'
ERROR: test_the_warning_writes_nothing_to_the_workspace                                 [T8-bis]  ValueError: vpp_preflight_failed
FAIL: test_a_preflight_request_without_admin_tools_is_warned_like_a_launch              [T4-bis]  AssertionError: 'vpp_preflight_failed' is not None
FAIL: test_a_run_that_requests_no_admin_tools_reads_nothing                             [T5]      AssertionError: 'vpp_preflight_failed' is not None
FAIL: test_a_server_request_without_admin_tools_gets_past_the_gate                      [T9-bis]  AssertionError: 'vpp_preflight_failed' == 'vpp_preflight_failed'
FAIL: test_no_base_mods_drops_a_default_that_only_lived_in_the_policy                   [T3]      AssertionError: 'vpp_preflight_failed' is not None
FAIL: test_server_mode_without_the_admin_tools_is_warned_not_refused                    [T1]      AssertionError: 'vpp_preflight_failed' is not None
FAIL: test_server_only_mode_is_warned_the_same_way                                      [T2]      AssertionError: 'vpp_preflight_failed' is not None
Ran 64 tests in 2.323s
FAILED (failures=6, errors=3)
```

Exactamente los 9 del contrato nuevo (T1, T2, T3, T4-bis, T5, T7-bis, T8-bis, T9-bis, T10-bis) y ninguno del contrato viejo: T4, T7, T8, T9 y T10 (modificados/renombrados pero de la rama (c)) siguen verdes sobre el producto viejo, como deben.

## Q6 — Mutantes sobre `<rev>\tree-mut` (nunca `<ws>`): los cuatro mueren

`<rev>\q6_mutants.py` (ancla única, sha256 antes/después, restauración verificada; salidas por mutante en `<rev>\mutant-*.txt`, resumen en `<rev>\mutants-summary.json`):

```
a1 token de vuelta a missing (error_code None):      sha256 aplicado a17b89e5144542de98e5b177cb7cc17d7102cfcf2c87c3d5c77dfc3a29ac3064   Ran 64  FAILED (failures=6)
   rojos: T1, T2, T3, T4-bis, T5, T7-bis
a2 token de vuelta a missing CON error_code:          sha256 aplicado cfb400bbd3415a53b478d8311ca1450ca6d4061abe26bc9bfc2479734e8b8891   Ran 64  FAILED (failures=6, errors=2)
   rojos: T1, T2, T3, T4-bis, T5, T7-bis, T8-bis, T9-bis
b  leer el cfg en la rama (b) antes de devolver:      sha256 aplicado 620ce216700a64222390e75280be025f545b57e0e453f1307a9dc46edf85e97c   Ran 64  FAILED (failures=1)
   rojo: test_a_run_that_requests_no_admin_tools_reads_nothing (T5)
c  VPP_PREFLIGHT_HINT en vez de VPP_ABSENT_HINT:      sha256 aplicado 3b2a38ddf49fc2f94df28832e0fb2d2c81468e5ee4078eccabf42b5f1cfbd88d   Ran 64  FAILED (failures=2)
   rojos: test_server_mode_without_the_admin_tools_is_warned_not_refused (T1), test_server_only_mode_is_warned_the_same_way (T2)
sha256 restaurado tras cada mutante: 3ca12ca20597a09de932cb147df7d509ffc8c49125923758905b70a79fe93c05 (restored_ok: true ×4); <ws> intacto: 3ca12ca2…
```

Los hashes de mis mutantes a1 y c coinciden con los que STATE.md declara para sus (a) y (c) (`a17b89e5…`, `3b2a38dd…`): confirmación independiente de que aplicó esos mutantes; su (b) tiene otro hash (`45755465…`) porque lo escribió con otra forma textual del mismo mutante. Ningún mutante sobrevive: no hay hallazgo de cobertura.

## Q7 — Redacción

```
$ grep -n -i "refus\|reject\|no bypass\|without admin tools" <ws>/tools/dayz_mcp/native_launcher_transaction.py
181:    would be the ps1 again; the refusal is what the ficha asked for. is_dir went        [HostVppFiles: rechazar vs reparar; neutro respecto a la ausencia del mod]
221:    An unknown name fails closed as a server start: the gate refuses rather            [modos desconocidos: el brief manda no tocarla; ver H2]
223:    layer already rejected unknown names (dayz_test_request.py:297), so this
322:    everything after it; the gate refuses only if no live assignment was              [cfg: no cambia por diseño]
373:    Two live assignments that disagree are refused: this layer cannot rank them       [cfg: no cambia por diseño]
576:    """Refuse a server start whose requested admin tools are not usable.              [nuevo, correcto]
579:    warned and never refused: this launcher does not require a particular
580:    one. There is no bypass parameter on this route, and none is needed:              [idea del bypass reformulada, como pide el brief]
613:    # requested admin tools are unusable is refused on either; one that               [call-site, nuevo, correcto]
```

En el baseline el mismo grep daba además `112` («The replacement refuses instead of repairing»), `563` («Refuse a server start without usable admin tools»), `565-567` y `599` («would start without admin tools is refused»): todas reescritas. Ninguna frase del módulo entregado dice ya que la puerta rechaza cuando falta el mod; «without admin tools» no aparece. Las cinco FRASES REESCRITAS de STATE.md coinciden con el diff (§0.3). Comentarios nuevos en inglés, tono neutro, sin fechas ni primera persona (el «the part we saw» de `:499` ya estaba en a7ddbac `:486`, en la rama (c) intacta). Residuos documentales fuera del alcance del implementador: H2.

## Q8 — Forma del diff

- LF: 0 CR en los dos ficheros de `<ws>` (§0.2); mis diffs sin normalizar son idénticos a los patches del receptor (§0.3).
- Hunks del producto (6, todos del contrato): `@@ -109,10 +109,11` comentario de cabecera; `@@ -135,11 +136,17` arranque de `VPP_PREFLIGHT_HINT` + `VPP_ABSENT_HINT` nueva; `@@ -437,9 +444,11` docstring de `evaluate_vpp_preflight`; `@@ -453,26 +462,30` retorno temprano + desindentación del antiguo `else`; `@@ -560,11 +573,12` docstring de `enforce_vpp_preflight`; `@@ -595,8 +609,9` comentario del call-site.
- El único hunk que toca líneas fuera de «retorno temprano, constante, hints, docstrings/comentarios» es la desindentación del cuarto: está explicada (el `else:` queda muerto tras el `return`) y probada como puramente estructural — `diff -w` deja solo el `return` y la línea `else:`, y `baseline + early-return-only == delivery (AST): True` (Q2). Sin hunks fuera de contrato.

## Observaciones sin severidad

- `tools/h9_native_probe.py` no está en a7ddbac (untracked; idéntico en `<wt>` y `<ws>`). Compone `mode="server", no_base_mods=True` (`:129-135`) y llama a `enforce_vpp_preflight(parsed.payload, policies)` (`:217`); su comentario (`:210-216`) describe la implicación vieja («exactly a server start without admin tools»). Con la ronda 6 ese probe pasará la puerta con aviso, que es el contrato nuevo. Nada que cambiar en la entrega; que el receptor lo sepa.
- STATE.md: sus recuentos (58 → 64, 129 de regresión, 9 rojos con T10-bis por `AttributeError`, mutantes 6/1/2) coinciden con lo que yo medí; no he tomado ninguno de ellos como evidencia.

## LO QUE NO PUDE VERIFICAR

- Nada in-game ni con el bundle sellado: no lancé juego, daemon ni launcher; no comprobé que un arranque real de servidor sin `@VPPAdminTools` llegue al worker sellado (`app.pyz`) con el aviso y arranque. La ruta MCP solo está cubierta por los tests con `preflight_vpp_request` y el launcher parcheados (`DayzTestRunVppGateTest`), y la ruta CLI descarta el resultado (H3), así que no medí cómo (o si) un humano ve `vpp_mod_not_requested` en ninguna de las dos rutas.
- No corrí la suite completa (`discover`), por prohibición del brief; la regresión se limita a los 4 módulos indicados (129 OK).
- La fixture Win32 con «acceso denegado» que vio un revisor anterior no se manifestó aquí; no puedo afirmar que no aparezca en otro sandbox.
- No verifiqué el proceso del implementador (su rojo-primero, sus hashes de mutantes) más allá de reproducir resultados equivalentes; dos de sus tres hashes de mutante coinciden con los míos, el tercero difiere por forma textual.
- La existencia de `P:\Suite` en otros hosts (H1 depende de ella): solo medí este host y la simulación en memoria.
- Los seis policies «multi-root» reales del bundle (`request-policy.json`) no se examinaron: los casos multi-root usan policies sintéticas.
