# HANDOFF — DayZ-MCP

<!-- LIVE-STATE:START -->
# DayZ-MCP - Estado vivo - 2026-11-04 (Madrid; LIVE y T130 promovidos a `3732333` y medidos entre las 14:27 y las 15:28)

- **main** = `3732333d` (#323). LIVE y T130 están en main: los dos detached en `3732333d` desde el 4/11, antes en `e18e4814`.
  - Antes del checkout ninguno tenía cambios locales en ficheros versionados.
  - Tras el checkout, los registros y los bundles de los dos lanzadores conservan sus hashes (58 ficheros cada uno).
- **Entraron 5 commits** (`e18e481..3732333`):
  - #319 y #320, solo documentación;
  - #321: arreglo del `poll error=7` (PBO y daemon). Con la clave de 43 caracteres que genera la instalación, que es la de LIVE y T130, la URL del poll del servidor cabe en 1024 caracteres. Una clave 41 caracteres más larga llevaría el caso saturado a 1025 (`tools/tests/test_poll_url_engine_limit.py`, docstring del módulo);
  - #322 (#318): en Windows, `stop()` libera los workers keep-alive ociosos;
  - #323 (#317): una sola espera bloqueante de `/session/wait` por ticket, pausa de cliente y nueva descripción de `session_wait`.
- **En el vault:** `AI/30_Sessions/2026-11-04-DayZ_MCP-promocion-0107c0c-plan.md` (plan, apunta a `3732333d`) y `AI/30_Sessions/2026-11-04-DayZ_MCP-flecos-317-318.md`; la promoción del 3/11 está en `AI/30_Sessions/2026-11-03-DayZ_MCP-promocion-live.md`.
- **Lanzadores `dayz-test-v1`, sin resellar el 4/11.** `e18e481..3732333` no toca el lanzador ni los 21 ficheros que fija `tools/packaged-modules.lock.json`, y los doctores no dan `NATIVE_BUNDLE_EXTERNAL_DRIFT`.
  - Siguen los resellados del 3/11, hechos tras la actualización de DayZ Tools de las 13:56 (build `26323527`): LIVE a las 14:26 y T130 a las 14:37, al promoverlo.
  - La actualización de las 18:54 (build `26405911`, el actual) no los ha desfasado.
  - Registros vigentes:
    - LIVE: `approved-launchers.json` `FC65EDF4…`, PE `F24523EA…`, `app.pyz` `6C5F5108…`, recibo `d1c49575…`;
    - T130: `approved-launchers.json` `A550CEAC…`, PE `B8D1CFCA…`, `app.pyz` `6C5F5108…`, recibo `84f5b804…`.
  - La política de T130 solo existe en la copia privada de la app de Claude (`%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\DayZ_MCP_130\launcher-policy.json`). Para construir T130 fuera de la app hay que pasar esa ruta con `--policy`.
  - `replace-dayz-test-v1 --expected-sha256` solo acepta hexadecimal en mayúsculas (`tools/dayz_mcp/launcher_registry_update.py:39`).
  - DayZ (221100) tiene una actualización pendiente en Steam (`StateFlags 6`). Al aplicarse cambiará `DayZDiag_x64.exe`, los dos lanzadores quedarán desfasados y habrá que resellarlos.
- **PBO vivo:** `6989BC95…` (740 105 B), construido desde `3732333d`.
  - `pbo_provenance.py`: PROVENANCE OK, 30 entradas, marker `3732333d`.
  - Está en `P:\Mods\@DayZ_MCP\addons\`. `P:\Mods` es un junction a `!Workshop`, así que es un solo fichero y lo cargan LIVE y T130.
  - El anterior, `B28CEC03…`, queda al lado como `DayZ_MCP.pbo.swapped_out_20261104T132847Z_B28CEC03434E0D4E`.
- **Daemons.** El dueño los paró el 4/11 y la primera llamada de un cliente los arrancó por WMI:
  - 8765: generación `bee69827…`; 8775: `f87e6b3b…`. Los dos con `daemon_modules.stale=[]`.
  - Los doctores (`--daemon-policy normal`) dan `ok=true` y solo INFO.
- **Clientes:** cambia la descripción de `session_wait`, así que la huella del registro de herramientas pasa a `202992bb…`. Un cliente que dé `stale_client` se recarga con `server_reload`, la herramienta del supervisor, sin reiniciar la sesión.
- **Pruebas en juego del 4/11 en LIVE** (DayZ_MCP, mode all):
  - **Bench de 30 iteraciones** (run `10f5712d`), p50:
    - `session_status` 116 ms, `world_time_get` 325 ms, `query_player_state` 321 ms y `camera_get` 316 ms; 0 errores.
    - Mismo orden de magnitud que el 2/11 con las PR de rendimiento (206-228 ms en la instancia aislada `perf`) y por debajo de la base anterior (545-558 ms).
    - Medido con otras sesiones corriendo tests en paralelo.
  - **#302** (run `2b822fea`), con el cliente sentado en un `CivilianSedan` (`vehicle_telemetry` `seated=1`):
    - `object_delete` y `scene_clear` devuelven `vehicle_client_seated` con `deleted=0`;
    - el cliente sigue vivo y haciendo poll;
    - 0 `ACCESS_VIOLATION`, 0 `poll error` y 0 `SCRIPT (E)` en los logs de servidor y cliente.
  - **Partida larga** (run `2f9a9697`, de 15:09 a 15:26):
    - durante 15 minutos, `world_time_get` y `camera_get` cada 20 s, `notify_players` y `bridge_status` cada minuto, y 8 `world_spawn` de una manzana sin persistencia;
    - las 15 lecturas de `bridge_status` dieron `ready`, con una edad máxima del poll de 0,49 s en el servidor y 1,01 s en el cliente, y 0 errores;
    - al final, el servidor llevaba `polls=1064`, `cmds=84`, `cmd_bytes=162792` y `busy_ms=22`;
    - 0 `poll error`, 0 `result post error`, 0 `SCRIPT (E)` y 0 `ACCESS_VIOLATION` en los logs de servidor y cliente.
  - **Run `cb026f6d`:** quedó huérfana por un fallo del script de prueba. Vivió 16 minutos con 0 `poll error` y el daemon la cerró por `idle_timeout`.
    - Sus 9 `SCRIPT (E)` son avisos de fuga al apagar («Leaked … script instance»).
    - Los de los callbacks del MCP salen en 71 de los 105 script logs del servidor de LIVE, ya desde el más antiguo que se conserva (19/10).
- **T130**, run `caacd5a2`, DayZ Exp `12~1.30.164289.70`:
  - los dos bridges en `ready` y `BOUND`, y capacidades `match` con el contrato `86e3c48a…`;
  - `capture_screenshot` correcta (`frame_stale=false`);
  - 0 `poll error`, 0 `SCRIPT (E)` y 0 `ACCESS_VIOLATION` en sus logs.
- **D1** no cambia: el godmode y el relleno siguen apagados por defecto, salvo con `profile=test`.
- **Abierto:**
  - PR #324: seguimiento P3 de #317 (P3-12 y P3-13);
  - tickets `fb-20261102-122105-e9c6` y `fb-20261102-122105-0afc`, en sesiones propias;
  - el aviso a Codex, que lo pega el dueño.
- **Sin medir hoy:** el buzón, el censo de instancias y de clientes, y los riesgos abiertos. El último censo completo es el LIVE-STATE del 18/10 (`git show 19dce57d:HANDOFF.md`).
<!-- LIVE-STATE:END -->

---

## Árboles: `main` público vs leftovers locales

| Árbol | Qué es | Estado 2026-10-18, 01:37 (Madrid) |
|---|---|---|
| `P:\DayZ_MCP` | Addon compilable (`$PBOPREFIX$=DayZ_MCP`). **Sin `.git`.** | **DESFASADO**: copia suelta de septiembre (sus ficheros más nuevos son del 15/09). De los 24 ficheros de `addon/` en `main`, 5 son iguales, 8 difieren y 11 faltan; tiene 13 que git no tiene (11 backups, nueve `.bak*` y dos `_bak*`, más `CLAUDE.md` y `gui/layouts/hot/mcp_hot.layout`). No empaquetar desde aquí (ficha `fb-20260925-233932-aa11`). |
| `P:\DayZ_MCP_dev` (=`...\DayZ Projects\DayZ_MCP_dev`), LIVE | Repo de producto (Python MCP, plans, reviews, este HANDOFF). `addon/` es la copia git del bridge. | Git `https://github.com/willy92wins/dayz-mcp.git`. HEAD detached en `b8c8d10` (v1.4), que es su `origin/main`: 4 commits por detrás de `main` (`dd619ff`) al medirlo. Su rama local `main` sigue en `3c27f33`. Sin cambios en ficheros versionados. Los backups de sesión viven en `C:/Users/guill/DayZ_MCP_backups/`, fuera del repo. |
| `C:\temp\DayZ_MCP_130`, T130 | Worktree del repo de LIVE para la instancia 130 (su gitdir está en `DayZ_MCP_dev\.git\worktrees\DayZ_MCP_130`). | HEAD detached en `d825aa2` (#235), 10 commits por detrás de `dd619ff`. Ve las mismas ramas que LIVE. Sin cambios en ficheros versionados. |
| `…\!Workshop\@DayZ_MCP\Addons\DayZ_MCP.pbo` | PBO desplegado. `P:\Mods`, el `mods_root` de las dos policies, es un junction a `!Workshop`, así que lo cargan las dos instancias. | SHA-256 `7078C2E4AC2231976D7AFD4A0B0EE677F9822C371F3FAA927B2EA356212E600A` (542 178 B), construido desde `b8c8d10` (v1.4) y puesto el 2026-10-17 a las 17:01; procedencia 24/24 más el marcador (01:38). El `addon/` de `b8c8d10` es el mismo árbol que el de `dd619ff`. Se sustituye solo con DayZ parado, el lease tomado y `tools/dev/swap_pbo.ps1 -WantOld <sha vivo>`. |
| `C:\Users\guill\Repos\dayz-mcp` | Otro clone | Rancio: `main` en `a41580f` (2026-09-26), 159 commits por detrás de `dd619ff`, sin commits propios y con `HANDOFF.md` modificado sin commit. No usarlo como checkout. |

Empaquetar SOLO desde `DayZ_MCP_dev\addon`. `pack-addon.ps1 -Ref <sha> -PackOnly -Clear -Destination …` empaqueta el `addon/` de git en esa ref y mete en la raíz del PBO `mcp_build.json` (commit, árbol de `addon/` y hora UTC de la build; no está en git), así que un PBO dice desde qué commit se construyó. Después, `tools/dev/pbo_provenance.py <pbo> <repo> <ref>` compara fichero a fichero, exige que el `prefix` de la cabecera sea el de `addon/$PBOPREFIX$` y que esa marca lleve el commit de `<ref>`, y sale con un código distinto de 0 ante cualquier diferencia. `-Source` cambia el árbol empaquetado y no debe apuntar a `DayZ_MCP`.

Herramientas de desarrollo y promoción en `tools/dev/`: `setup_worktree.sh` crea un worktree con su propio `tools/.venv-mcp` y comprueba, desde un directorio neutro, que el import editable resuelve dentro de ese worktree y que ninguna junction ni symlink lleva a su venv (los marcadores de nube de OneDrive pasan) (2eb5; `--check <dir>` repite solo la comprobación); `pbo_provenance.py`; y `swap_pbo.ps1`, que sustituye el PBO vivo solo si el build y el vivo tienen los SHA-256 esperados y no corre ningún proceso de DayZ. Mantiene el vivo bloqueado desde su hash hasta el cambio, instala los bytes que comprobó con renombrados que nunca sobrescriben y deja el anterior al lado como `DayZ_MCP.pbo.swapped_out_<UTC>_<hash>`; un `-Backup` que sea el mismo fichero con otro nombre (hard link) se rechaza.

**Higiene:**
- `reviews/2026-09-04-reserva/` y `reviews/2026-09-07-reserva-cierre/` eran snapshots de árboles completos (5,8 y 5,4 GB). **No versionarlos ni «rescatarlos» nunca**; están en backups fuera del repo.
- El scratch de Cursor (`cursor-home/`) dentro de reviews rompe `git add` por MAX_PATH; está excluido por gitignore.
- El decision-log D-NN canónico vive en el vault Obsidian (`AI/10_Projects/DayZ_MCP/decisions/decision-log.md`, hasta D-93), NO en el repo.

## Cola aparcada (PARK / PARO)

La cola antigua quedó vacía en la v1.3: 0ab2 (#31), 546d (#26), a429 (#29), 2edd-1 (#73), 3fc1 (#70), 1025 (#61, #99) y dae1 partes 1 y 2 (#185) están hechos, y d50e se cerró sin trabajo. Lo que queda fuera de la v1.3, y por qué:

| Marca | Fichas | Razón |
|---|---|---|
| **HOLD** | `9ab8` (heater), GATES | Decisión del dueño. |
| **FUERA, documentado** | `6084`, `ce72` | Decisión del dueño (D-94). |
| **v1.4** | `c8c7` (el aviso de reset de la rotación no llega a quien llama), `7f27` (escalares genéricos con su valor por defecto en todos los verbos) | Hallazgos de la P18a. |
| **Del dueño** | `bf5c`, `8cf9`, `a97e` (alcance del `-addon` de AddonBuilder, source en `P:`), `7672` (publicar el bundle con handles vivos), `fade`/`75e7` (el foco al arrancar; f298 está cerrada) | El arreglo toca el worker o el launcher sellados. |

## Superficie (no recontar a ciegas)

- README / architecture / `build_app`: **86 tools** (+ `exec_enforce` opt-in). Contadas el 2026-10-09 con `build_app`; coinciden una a una con la lista de `README.md:80`.
- `server.py` es una fachada desde #186: las definiciones de dominio viven en `tool_catalog.py`, `bridge_readiness.py`, `bridge_errors.py`, `tool_args.py`, `world_results.py`, `launch_logs.py` y `box_occupancy.py`, y `dayz_mcp.server` las reexporta. `build_app` sigue entero en `server.py`.
- Suite de tests: `tools/tests/test_*.py`.
  - Suite completa: `tools\.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t .` desde `tools\`; sin pytest.
  - Tier rápido: la misma orden con `DAYZ_MCP_FAST_TESTS=1`, unos 115 s y `Ran 5313 tests / OK (skipped=504)` el 2026-10-01.
- `CLAUDE.md`, `AGENTS.md`, `GATES.md` y `PROJECT-MAP.md` están versionados.

## Gotchas que siguen vigentes

- `MakeScreenshot` no escribe sin `-autotest` (T165276); con `-autotest` sí (probe 2026-11-03, `dayz-mcp-architecture.md` §11, sin integrar) → window-grab.
- `SetHeader` solo Content-Type → API-key en query string.
- `*_now` bloquea el tick.
- `SetTimeMultiplier(0)` congela la sim.
- **Argumentos opcionales hacia el addon:** un campo omitido en el JSON llega a `MCPArgs` como 0/false/vacío, no con el valor del constructor. Un opcional nuevo lleva su bandera `<campo>_set` y el addon mira la bandera (#162).
- **Fixtures de vehículo:** sin `world_spawn(lifetime_s)`, la economía central borra el vehículo entre 3 y 27 s después de que el jugador se aleje más de 100 m.
- **Sesiones de Claude Code:** la lista de herramientas se fija al arrancar (clase e7ef), así que un verbo o argumento nuevo solo se ve con un cliente stdio propio (patrón en `dzn-night/promo/p11c5_ingame.py`). El Bash tool falla en el envoltorio del arnés (fd52/5363): usar PowerShell con `& 'C:\Program Files\Git\bin\bash.exe' -lc '<script>'`.
- **Lanes claude headless:** SIEMPRE `--allowedTools` explícito (Read/Write/Edit/Glob/Grep/Bash(venv)+git+gh) y 1 lane = 1 git worktree (en el mismo cwd se pisan).
- **Suite:** usar el venv absoluto; `python -m unittest tools.tests.X` NO funciona; desde worktrees hace falta `PYTHONPATH=tools`.
<!-- Distill 2026-09-30 11:20 Madrid (sesión autónoma, Claude orquestador)
- #152-#164 fusionados con revisión de otra familia (Opus o Gemini) y CI en verde; P11a-P16 promovidas.
- Hallazgos: 8779 (opcionales omitidos llegan como 0; object_anim leído escribía) y la causa de e0af (CE: lifetime de types.xml + CleanupAvoidance 100 m).
- Evidencia en juego: C:\Users\guill\DayZ_MCP_backups\p11-20260930\ingame-*; scripts en C:\Users\guill\AppData\Local\Temp\dzn-night\promo\.
-->
