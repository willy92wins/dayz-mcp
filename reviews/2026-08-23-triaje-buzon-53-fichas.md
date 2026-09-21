Auditoría de fichas contra HEAD: leo el snapshot con Python y verifico cada cita en el árbol, sin editar nada.El árbol tiene `addon/` presente; extraigo las fichas `[F-`/`[P-` y las que citan `path:line`.El regex mezclaba citas reales con ruido (`qwen3.5:27`). Afinaré el filtro y sacaré los cuerpos de F/P.Tengo las 18 fichas F/P. Ahora confirmo los cierres citados y leo el código en las líneas actuales.El workspace no es un repo git; verifico contra los ficheros de HEAD. Empiezo por F-01 a F-06 y el launcher.Hay 53 abiertas: las 18 F/P y 35 más. Saco los cuerpos de esas 35.Verifico las fichas abiertas que citan este repo: object_anim, extra_mods, inbox, launcher y tests.**6 de 18 F/P ya están cerradas en HEAD y nadie las ha marcado.** F-02, F-03, F-06 y F-11 cuadran con el cierre de `548e91f`. F-13 y la isla SO-01 (`134e`) también. F-12 **no**: `object_delete`/`notify_players` sí, el resto schemaless no.

Filtro: 53 abiertas (249 fichas − 196 `resolves`). Las 18 `[F-`/`[P-` siguen OPEN. `addon/` **sí está** en esta copia; las citas Enforce se leyeron. No corrí la suite, no hay `.git` aquí, no toqué MCP.

## Tabla

| id | ficha | veredicto | evidencia (HOY) | 1 línea |
|---|---|---|---|---|
| `fb-20260822-191204-9287` | [F-01] `same_path` no resuelve subst P:/C: | **VIVA** | `tools/dayz_mcp/native_process_snapshot.py:59-64` | Sigue siendo `normcase(normpath)`; `orphan_guard.py:332` lo usa para saltar el launcher. Cita **no** se movió. Módulo empaquetado. |
| `fb-20260822-191204-fa94` | [F-04] AddonBuilder hardcodeado en C++ | **VIVA** | `tools/native-launchers/dayz-test-v1/src/launcher.cpp:1010` y `:1200` | Literal Steam `C:\Program Files (x86)\...AddonBuilder.exe` intacto. Hace falta rebuild nativo. |
| `fb-20260822-191204-1b40` | [F-08] `ResolveOwnedCar` sin asiento/`IsOwner` | **VIVA** | `addon/scripts/5_Mission/MCPClientBridge.c:2133-2148` | Sigue siendo GetPlayer → GetCommand_Vehicle → Cast. `DispatchEngineSet` lo usa en `:789`. Cita **no** se movió. |
| `fb-20260822-191204-3cc4` | [F-09] `vehicle_control` copia el DIAG | **VIVA** | `addon/scripts/4_World/MCP_CarScript.c:620-660` | `engineReady=false` si rpm &lt; idle: no llega a `SetBrake`. `gear_shift` sigue solo en `product-spec.md:114`. |
| `fb-20260822-191204-dc90` | [F-14] bootstrap no pinnea pip | **VIVA** | `tools/install-mcp.ps1:325` | `& $VenvPython -m pip install --upgrade pip` sigue. |
| `fb-20260822-191204-46b3` | [P-01] URL con prefijo `http://127.0.0.1:` | **VIVA** | `addon/scripts/5_Mission/MCPBridge.c:174-181` y `MCPClientBridge.c:293-300` | `StringHasPrefix` lexical; `:2410-2422` sigue siendo `Substring`. |
| `fb-20260822-191204-2cc9` | [P-02] `pollHz` sin techo | **VIVA** | `MCPBridge.c:188-190` (copia) y `:122` (`1.0 / m_PollHz`) | `IsFiniteFloat` existe en `:2425` y **no** se aplica a `pollHz`. Igual en cliente `:307-309` / `:243`. |
| `fb-20260822-191204-039c` | [P-03] SDK hardcodeado | **VIVA** | `tools/build_native_launcher.py:837-841` | Lee `sdk["version"]` y fija `sdk_root = Path(r"C:\Program Files (x86)\Windows Kits\10")`. |
| `fb-20260822-191204-48bc` | [P-04] `Clear` no restaura freno | **VIVA** | `addon/scripts/4_World/MCP_CarScript.c:22-26` y `:608-611` | TTL llama `Clear()`; no hay `SetBrakesActivateWithoutDriver(true)`. |
| `fb-20260818-142116-6bef` | `object_anim` phase=1 no oculta | **VIVA** | `addon/scripts/5_Mission/MCPBridge.c:1221-1226` | Sigue `SetAnimationPhase`, no `Now`. El efecto visual in-game no se midió aquí. |
| `fb-20260822-193753-6271` | buzón &gt;4 KiB / ids débiles | **VIVA** | `tools/dayz_mcp/inbox.py:28-31`, `:59`, `:65`; `server.py:3721` | Comentario &lt;4 KiB vs body 8000; `token_hex(2)` sin unicidad; la tool sigue prometiendo "ids cannot collide". |
| `fb-20260822-201448-5272` | `extra_mods` tira `@DayZ_MCP` | **VIVA** | `tools/dayz_mcp/dayz_test_worker.py:204-210` | HEAD **concatena** base+`@mod`+extra (la tesis "reemplaza" no se sostiene). No inyecta ni avisa si falta el bridge. `dayz_test_worker.py` está en el bundle nativo. |
| `fb-20260822-192043-3819` | `wait_for` timeout opaco | **VIVA** | `tools/dayz_mcp/server.py:1888-1896` y `:3667-3672` | Hay `lookback_from="launch"` y `scanned`; el timeout **sigue** sin decir "patrón fuera de ventana" vs "patrón malo". |
| `fb-20260822-191204-6ce4` | [F-12] extras en schemaless y `object_delete` | **CITA_MOVIDA** | `loopback.py:670-671` (resto); `object_delete` cerrado en `:477-487` | **Desmiento el cierre total.** `object_delete`/`notify_players` sí (test `test_command_validation_coverage.py:129-141`). `engine_set`/`vehicle_control` siguen en `_SCHEMALESS_COMMANDS` (`:100-118`); el comentario `:97-98` lo deja como cambio aparte. |
| `fb-20260820-110945-8625` | `release_all_running_owners` sin test | **CITA_MOVIDA** | `tools/dayz_mcp/process_lifecycle.py:722` (era `:727`) | La función sigue; grep de tests: **cero** referencias. |
| `fb-20260819-153716-8491` | `inspect.getsource` vs OneDrive | **CITA_MOVIDA** | `tools/tests/test_playbook_tool.py:399` (era `:401`) | Sigue `inspect.getsource(tool.fn)`. |
| `fb-20260822-025926-bad7` | launcher tira stderr del juego | **CITA_MOVIDA** | `launcher.cpp:1305-1306` (la cita `:1277` hoy es `ActiveProcessLimit`) | `hStdError = null_error` (`NUL`). Rebuild nativo. |
| `fb-20260822-191204-aeda` | [F-05] Python 3.10 vs 3.11+/3.14 | **EN_CURSO** | `tools/pyproject.toml:8`; `host_config.py:12` (`tomllib`); `identity_migration.py:13` (`UTC`); `install-mcp.ps1:40` | Sigue en el árbol; `host_config.py` está en `_APP_PACKAGED_MODULES`. |
| `fb-20260822-191204-a660` | [F-07] flake PID a 20 ms | **EN_CURSO** | `tools/tests/test_bug046_startup_deadlock.py:746-781` | El scan global cada 20 ms sigue. |
| `fb-20260822-191204-4b70` | [F-10] `read_key` vs pinned | **EN_CURSO** | `tools/dayz_mcp/loopback.py:3145-3150` (era `:3095`) | Sigue `open()+read.strip()`. El bug está en `loopback.py` (no empaquetado); `pinned_keyfile.py` sí lo está. |
| `fb-20260822-191204-fb9f` | [F-02] `repairing` irrecuperable | **CERRADA** | `loopback.py:2951-2957` | Reanuda desde `repairing`. Cuadra con `548e91f`. |
| `fb-20260822-191204-ecbd` | [F-03] TCP ocioso sin techo | **CERRADA** | `loopback.py:159`, `:3161-3180`; test `test_loopback.py:1217` | `BoundedSemaphore(MAX_HTTP_WORKERS)`. Cuadra. |
| `fb-20260822-191204-3637` | [F-06] audit lento → `audit_failed` | **CERRADA** | `session_coordination.py:2292-2295`, `:2474-2476`; test `:1653` | Timeout ahora es `"pending"`, no `audit_failed`. Cuadra. |
| `fb-20260822-191204-2c76` | [F-11] `_results` sin tope | **CERRADA** | `loopback.py:146-149`, `:2019-2024` | `MAX_RESULTS = MAX_QUEUE * 4` y `_trim_results_locked`. No hay TTL temporal; hay techo. Cuadra el crecimiento ilimitado. |
| `fb-20260822-191204-49db` | [F-13] 9 tests no versionados | **CERRADA** | `tools/tests/` de HEAD | Los 9 nombres **no están** en esta copia trackeada. Un clone de HEAD no los corre. No vi el disco sucio del autor. |
| `fb-20260822-225038-134e` | BOM + exención HTTP (SO-01) | **CERRADA** | `tools/tests/test_sources_are_statically_analysable.py:44-55`; `security_runtime_audit.py:32` | `tools/mcp_client.py` / `mcp_server.py` / `run-fase*.ps1` **no existen**. `RUNTIME_HTTP_EXCLUSIONS` ya no está; hay allowlist por (path, fn, kind). SO-01 cuadra. |
| `fb-20260818-103447-51c1` | linter pre-PBO / tablas vanilla | **NO_VERIFICABLE** | — | Vive en `DayZ_Tooling`, no en este árbol. |
| `fb-20260818-113316-f8e7` | object_anim + teardown ajeno | **NO_VERIFICABLE** | — | El teardown por nombre no se demuestra leyendo; el phase=0/1 es la ficha `6bef`. |
| `fb-20260818-131115-cb15` | golden sin versión de celda | **NO_VERIFICABLE** | — | `atm_b3_cell.py` no está aquí. |
| `fb-20260818-152742-d49a` | flake cola de leases 202 vs 200 | **NO_VERIFICABLE** | — | Suite prohibida. `test_task7_final_authority_regressions.py` ya no está (era de F-13). |
| `fb-20260818-161011-c931` | `action_use` condition_failed y luego ejecuta | **NO_VERIFICABLE** | `MCPClientBridge.c:1701-1706` | HEAD sale en `Can()` false **sin** `PerformActionStart`. El retraso de ~10 s es in-game. |
| `fb-20260818-220336-2eb5` | copia %TEMP% no aísla el venv editable | **NO_VERIFICABLE** | — | El finder editable no está en el árbol; es el `.venv-mcp` de la máquina. |
| `fb-20260818-222404-7f9f` | `check_proxy_rotation.py` cp1252 | **NO_VERIFICABLE** | — | GunRacks, no este repo. |
| `fb-20260818-223252-5d14` | occlusion dummies WRX | **NO_VERIFICABLE** | — | Forza, no este repo. |
| `fb-20260818-232116-93d2` | CallLater no reencola | **NO_VERIFICABLE** | — | Motor Enforce; no hay `.c` de SUB_BRZ aquí. |
| `fb-20260818-232129-1233` | DayZDiag se congela al cargar | **NO_VERIFICABLE** | — | Proceso de juego. |
| `fb-20260819-000639-b88a` | falta captura `ui_tree` ATM | **NO_VERIFICABLE** | — | Petición in-game. |
| `fb-20260819-000957-a0a6` | ViewGeo / rotationFlags | **NO_VERIFICABLE** | — | Forza. |
| `fb-20260819-011327-d3c7` | scripts fuera del PBO | **NO_VERIFICABLE** | `dayz_test_worker.py:236` | Ya pasa `-filePatching`; no hay perfil "`.c` fuera del PBO". Es petición de pipeline. |
| `fb-20260819-014009-0a7c` | sondas layout right_ref/wrap | **NO_VERIFICABLE** | — | In-game. |
| `fb-20260819-024951-e307` | sesiones se pisan LIVE-STATE | **NO_VERIFICABLE** | — | Operación, no un `path:line` de este árbol. |
| `fb-20260819-110949-73dd` | ES-LAYOUT / PBOPREFIX | **NO_VERIFICABLE** | — | Linter en DayZ_Tooling. |
| `fb-20260819-123453-9359` | cliente muerto sin minidump | **NO_VERIFICABLE** | — | Crash in-game. |
| `fb-20260819-140207-ec0d` | election flake / idle watchdog | **NO_VERIFICABLE** | — | Misma familia que F-07 (EN_CURSO); la cita `:851` hoy es otro test. |
| `fb-20260819-224555-7802` | gate ODOL por strings | **NO_VERIFICABLE** | — | py3d / PBO, no este árbol. |
| `fb-20260820-004633-d541` | suite cuelga en crash_boundaries | **NO_VERIFICABLE** | `test_bug046_startup_deadlock.py:789` | El test sigue; el cuelgue bajo carga pide suite. Familia F-07. |
| `fb-20260821-001300-1178` | `packctl validate` árbol sucio | **NO_VERIFICABLE** | — | Knowledge Pack, no este repo. |
| `fb-20260821-112231-f1e2` | caras nuevas py3d invisibles | **NO_VERIFICABLE** | — | In-game / otro pipeline. |
| `fb-20260821-162508-00c4` | `session_acquire_wait` timeout con caja claimable | **NO_VERIFICABLE** | — | Carrera de heartbeat; pide daemon vivo. |
| `fb-20260821-232119-9486` | offline se apaga a los 3–5 min | **NO_VERIFICABLE** | `MCPClientBridge.c:323` (log de éxito; cita **no** se movió) | Pide juego. `launcher.cpp:1407-1424` mata hijos no-worker a ~20 s+5 s — no encaja con 177–290 s. |
| `fb-20260822-114910-7163` | CE purga fixtures | **NO_VERIFICABLE** | — | In-game / CE. |
| `fb-20260822-175553-fd10` | delegado headless ok sin fichero | **NO_VERIFICABLE** | — | Harness OpenCode, no este repo. |
| `fb-20260822-234216-5dfd` | Bash `unexpected EOF` línea 70 | **NO_VERIFICABLE** | — | Wrapper de Claude Code / profile del usuario. |

## Conteos

| veredicto | N |
|---|---|
| VIVA | 13 |
| CITA_MOVIDA | 4 |
| EN_CURSO | 3 |
| CERRADA | 6 |
| NO_VERIFICABLE | 27 |
| **total abiertas miradas** | **53** |

Cierres de `548e91f`: **confirmo F-02, F-03, F-06, F-11. Desmiento F-12 como cierre total.** SO-01/`134e`: confirmo.

## Top 3 VIVAS (impacto al clonar)

1. **F-01** — `QUICKSTART.md:16-19` manda `subst P:`. `same_path` no resuelve P:↔C:, así que el watchdog vigila el launcher del venv. Un clone que sigue el quickstart no reproduce la suite en C: (`README.md:228-230` promete que un clone solo está rojo por el flake F-07). Toca `native_process_snapshot.py`, que **sí** está en `_APP_PACKAGED_MODULES` (`native_bundle.py:65-81`).
2. **F-14** — el primer comando de un clone (`install-mcp.ps1:325`) sube pip a "latest". Dos clones del mismo commit no son el mismo toolchain. F-05 (el suelo 3.10/3.14) ya lo está mirando otra lane; este es el otro medio del bootstrap.
3. **6271** — el buzón que estamos triando viola su propia premisa de append atómico (`inbox.py:28` vs `:59`) y promete ids únicos (`server.py:3721`) con 16 bits de entropía. Las 18 F/P se archivaron **en el mismo segundo** (`fb-20260822-191204-*`). Un clone que use `pipeline_feedback` hereda esa garantía falsa.

## Qué no pude comprobar

- **Juego / DayZDiag:** F-08/F-09/P-01/P-02/P-04/`6bef`/`c931`/`9486`/`7163` — el código se leyó; el efecto físico no.
- **Suite:** F-07, `d49a`, `d541`, `ec0d` (prohibido).
- **Compile nativo:** F-04, P-03, `bad7` — el fuente está; el `.exe` instalado no se midió.
- **Bundle vs fuente:** `dayz_test_worker.py` y `native_process_snapshot.py` van en el pyz. HEAD fuente ≠ launcher ya instalado hasta rebuild.
- **Otros repos:** DayZ_Tooling, Forza, GunRacks, Knowledge Pack, OpenCode, wrapper bash.
- **Disco sucio del autor:** F-13 cerrado para un clone de HEAD; no vi untracked locales.
- **Git:** este workspace no es un repo; no pude `git show 548e91f`. Los cierres se confirman por el código, no por el hash.

Fichas mal planteadas en HEAD: **5272** (extra_mods no reemplaza; concatena). **F-12** (el commit cerró el subconjunto schemado y dejó escrito que schemaless es otro cambio). **f8e7.3** (teardown por nombre) no se sostiene solo con el árbol.

```json
{"status":"ok","summary":"De 53 abiertas: 13 VIVA, 4 CITA_MOVIDA, 3 EN_CURSO, 6 CERRADA (F-02/03/06/11/13 y SO-01/134e); F-12 no esta cerrada del todo.","counts":{"CERRADA":6,"VIVA":13,"CITA_MOVIDA":4,"NO_VERIFICABLE":27,"EN_CURSO":3},"top3_vivas":["fb-20260822-191204-9287","fb-20260822-191204-dc90","fb-20260822-193753-6271"],"verified":["tools/dayz_mcp/native_process_snapshot.py:59","tools/dayz_mcp/orphan_guard.py:332","tools/dayz_mcp/loopback.py:149","tools/dayz_mcp/loopback.py:477","tools/dayz_mcp/loopback.py:670","tools/dayz_mcp/loopback.py:2019","tools/dayz_mcp/loopback.py:2951","tools/dayz_mcp/loopback.py:3145","tools/dayz_mcp/loopback.py:3161","tools/dayz_mcp/session_coordination.py:2292","tools/dayz_mcp/session_coordination.py:2474","tools/tests/test_bug046_audit_fault_recovery.py:1653","tools/tests/test_loopback.py:1217","tools/tests/test_command_validation_coverage.py:129","tools/native-launchers/dayz-test-v1/src/launcher.cpp:1010","tools/native-launchers/dayz-test-v1/src/launcher.cpp:1200","tools/native-launchers/dayz-test-v1/src/launcher.cpp:1306","tools/pyproject.toml:8","tools/dayz_mcp/host_config.py:12","tools/dayz_mcp/identity_migration.py:13","tools/install-mcp.ps1:40","tools/install-mcp.ps1:325","addon/scripts/5_Mission/MCPClientBridge.c:2133","addon/scripts/5_Mission/MCPClientBridge.c:789","addon/scripts/5_Mission/MCPBridge.c:174","addon/scripts/5_Mission/MCPBridge.c:122","addon/scripts/5_Mission/MCPBridge.c:1223","addon/scripts/4_World/MCP_CarScript.c:22","addon/scripts/4_World/MCP_CarScript.c:634","tools/build_native_launcher.py:841","tools/dayz_mcp/inbox.py:28","tools/dayz_mcp/inbox.py:65","tools/dayz_mcp/server.py:3721","tools/dayz_mcp/server.py:1888","tools/dayz_mcp/process_lifecycle.py:722","tools/tests/test_playbook_tool.py:399","tools/dayz_mcp/dayz_test_worker.py:204","tools/tests/test_sources_are_statically_analysable.py:44","tools/dayz_mcp/security_runtime_audit.py:32","tools/dayz_mcp/native_bundle.py:65"],"not_verified":["efecto in-game F-08/F-09/P-01/P-02/P-04/6bef/c931/9486/7163","suite F-07/d49a/d541/ec0d","exe nativo vs fuente F-04/P-03/bad7/5272","untracked del autor para F-13","DayZ_Tooling/Forza/GunRacks/packctl/OpenCode/wrapper bash","commits 548e91f/a5f4bb9 (workspace sin .git)"]}
```