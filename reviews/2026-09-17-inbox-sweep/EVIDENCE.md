# Barrido del buzón del pipeline — 2026-09-17

Árbol: `DayZ_MCP_dev`, main **1737dc5** (`origin/main` va 2 commits por delante: `2aaef6e`/`3f6a46f`,
solo `HANDOFF.md`, sin código). Entradas sin resolver al empezar: **32** (`unresolved_total=32`,
`count_total=611`), no 25.

Suite invocada siempre como `./tools/run-tests.ps1 <modulo>` con módulo explícito. Exit 5 = suite
vacía = fallo del comando. No se lanzó DayZ (0 procesos vivos durante todo el barrido), no se tocó
el lease, no se usó P:\ ni AddonBuilder.

## Límites reales de `pipeline_resolve` (leídos del inputSchema publicado)

- `resolution`: 1..2000 caracteres.
- `evidence_ref`: 1..240, **ruta y nada más**, relativa a `DayZ_MCP_dev`, empezando por
  `reviews | gates | reports | research`, segmentos `[A-Za-z0-9._-]`. Sin prefijo de repo, sin
  comentario, sin paréntesis, sin commit, sin `#ancla`.
- **No hay campo `title`** en `pipeline_resolve`. El tope de 120 es de `pipeline_feedback.title`
  (y `body` 1..8000, `project` 0..64). El rechazo por «título >120» venía de ahí.

## Resueltas (9)

| id | veredicto | prueba |
|---|---|---|
| fb-20260915-144512-7b66 | no reproduce | `tests.test_dependency_lock` 8 OK exit 0; 0 hits de `.ps1\|powershell\|pwsh` en `tools/dayz_mcp/pack_only.py` (cadena retirada en 9623c11) |
| fb-20260915-144519-2f98 | no reproduce | 0 procesos DayZ vivos + `tests.test_fb_ba70` 5 OK exit 0 (0,006 s); hermético desde 871324c (PR #71) |
| fb-20260915-014746-d85b | implementado | `server.py:4060` `_lease_renewal_contract`, colgado de `:4251` `:4307` `:4340` `:6445`; descripción servida hoy lo dice literal |
| fb-20260913-013217-2223 | implementado (1,2,5 de 5) | `on_busy="queue"` + `queue_offer` + `box.queue_position`; `tests.test_fb_2223_box_queue_offer` 28 OK exit 0 |
| fb-20260911-214400-8604 | implementado | `dayz_test_close` (`dayz_test_tool.py:2239`), espera `--- Termination successfully completed ---` por rol; `tests.test_fb_8604_orderly_close` 31 OK exit 0 |
| fb-20260908-202102-2edd | implementado (petición 2) | `dayz_test_tool.py:1944-1946` `stop_method=forced_kill` + `exit_metrics_valid=False`; descripción de `dayz_test_stop` lo declara; `tests.test_b2_2edd_dae1` 5 OK exit 0 |
| fb-20260828-211445-3bb4 | implementado | `key_press` vivo (DIK → `Mission.OnKeyPress`); `MCPClientBridge.c:180,279,783`, `MissionGameplay.c:38`; `tests.test_fb_3bb4_esc_and_drive_retire` 7 OK exit 0 |
| fb-20260909-161553-dce1 | implementado | `action_use(target=world\|hands\|self)` vía `action_use_target`; `MCPClientBridge.c:2002-2051`, `server.py:6357`; `tests.test_fb_3fc1_action_use_target` 13 OK exit 0 |
| fb-20260911-214427-7ef2 | implementado (la sugerencia) | generación cableada (`dayz_test_tool.py:1684-1687,1733-1735`) + `session_status.runs_retired_recently` (`server.py:4364`); `tests.test_fb_7ef2` 9 OK exit 0 |

## Siguen reproduciendo — verificado sin entorno (6)

| id | prueba de que sigue |
|---|---|
| fb-20260915-151528-6ed1 | `git ls-files --eol -- tools` → **8** con `w/crlf` (no 9; `tools/deploy-addon.ps1` ya no está). Siguen `win32_fileinfo.py` y `launcher.cpp`, los dos del bundle sellado |
| fb-20260915-143332-00bb | `server.py:5241-5243`: `player_teleport` llama `vehicle_telemetry` al cliente **sin comprobar antes** que el peer sondea; sin cliente se va al timeout y el error nombra otro verbo |
| fb-20260915-104637-1004 | `server.py:5987` (`vehicle_get_in_client`) y `:5222`/`:5251` siguen diciendo «tear down with object_delete» sin el aviso; `object_delete` no tiene guarda de ocupante (solo acepta `object_id`) |
| fb-20260914-194728-e4be | `runtime_state.py:1249-1253`: el comentario del propio código dice que la ventana sigue abierta; el borrado sigue siendo `os.unlink(directory / member)` por ruta |
| fb-20260913-143454-8bc6 | `object_delete` sigue aceptando solo `object_id`; ni borrado por pos+type ni la nota de que un `object_id` no sobrevive a su run |
| fb-20260915-014753-ba11 | `canary_fence.py:165-171`: `taskkill` y acto seguido `_intruder_still_running`, sin espera ni reintento |

## Arregladas a medias — queda trabajo nombrado (4)

- **fb-20260910-105607-1025**: petición 2 hecha (`packaged-modules.lock.json` + `tests.test_fb_1025_packaged_modules_lock` 8 OK). Petición 1a **no**: `README-mcp.md:126-131` sigue documentando build → `rollback-last` → `install`, o sea la ventana 2 sigue existiendo por diseño.
- **fb-20260904-200821-dae1**: dae1-3 (A4) hecho — `dayz_test_tool.py:1204-1212` expande el ancla de servidor a `all` y recoge `_client\profiles`. dae1-1 (carrera reaper/adopt) y dae1-2 (A1-F7) siguen.
- **fb-20260915-103604-63c9**: `9623c11` (PR #62) reescribe `pack-addon.ps1`: ahora empaqueta desde un *stage* verificado (`pack-addon.ps1:430` `$Source = $stageFull`) y `tests.test_pack_addon_staging` (14 OK) corre el **script real** con `-StageOnly` y comprueba que `CLAUDE.md`, `.bak_*`, `_bak_*` y una junction quedan fuera. Falta el único paso que necesita AddonBuilder: empaquetar una vez y contar las entradas del PBO.
- **fb-20260909-110445-1f21**: `prepare()` ya espera el marcador de arranque de Steam en su propio `console_log.txt` (`steam_launch_guard.py:93-122`, `steam_preflight.py:181-223`) y `57a02b1` pasa `steam_pid_repair_reason`/`steam_restart_fallback` al resultado. El propio docstring dice que «Steamworks initialization by the actual game remains the acceptance test»: falta la corrida.

## No verificables sin entorno — se dejan abiertas (13)

`fb-20260915-132202-2143`, `fb-20260915-113100-160c`, `fb-20260915-111038-75e7`,
`fb-20260915-105311-fade`, `fb-20260915-014724-b0d9`, `fb-20260915-010502-2084`,
`fb-20260914-231736-52c3`, `fb-20260910-102449-f47b`, `fb-20260907-184749-3fc1`,
`fb-20260907-133851-f298`, y las tres ajenas a este árbol: `fb-20260915-133831-c586` (arnés de
Claude Code), `fb-20260915-133820-7b41` (agy/Gemini), `fb-20260915-133817-dfca` (Cursor Ultra,
ventana abierta hasta el 2026-09-30).

Notas sobre ellas:
- **f298 / 75e7 / fade / 160c**: `d6ddf95` (PR #59) lanza DayZ con `STARTF_USESHOWWINDOW` +
  `SW_SHOWNOACTIVATE` (`process_lifecycle.py:1421-1425`) y la captura con `CREATE_NO_WINDOW`
  (`mcp_capture.py:939-942`); `tests.test_fb_f298_launch_focus` 3 OK. **No toca `launcher.cpp`**.
  Si Enfusion se trae el foco por su cuenta, el arreglo no basta: hace falta una sonda en el
  próximo arranque.
- **b0d9 / f47b**: `179d260` (PR #65) quita el «Retry restore_gameplay» y hace que la captura
  nombre el render congelado; el congelado en sí sigue sin arreglo.
- **3fc1**: la petición (sostener la continua: `hold_s`/`action_complete`) **no** está; la
  descripción de `action_use` lo dice: «The tool does not sustain continuous-action input».
