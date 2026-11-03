# HANDOFF — DayZ-MCP

<!-- LIVE-STATE:START -->
# DayZ-MCP - Estado vivo - 2026-11-03 (Madrid; árbol LIVE, launcher, PBO, daemon y partida de prueba medidos entre las 12:49 y las 12:59, tras promover LIVE a `e18e481`)

- **main** = `9d1df76` (#319, solo documentación). LIVE no sigue a main: está en `e18e481`, y de `e18e481` a `9d1df76` solo cambia documentación.
- **LIVE** (`DayZ_MCP_dev`) detached en `e18e4814f` desde el 3/11; antes, `83b569b9`. Antes del checkout no había cambios locales en ficheros versionados. El registro y los artefactos generados del lanzador salieron del checkout con los mismos hashes.
  - Entraron 27 commits (`83b569b..e18e481`): #263, #266-#268, #285, #286, #289-#297, #299-#309, #311-#314 y #316. Entre ellos, las PR de rendimiento (#303, #305-#308) y el B1 del godmode (#289, #293, #299).
  - Plan y registro de la promoción en el vault: `AI/30_Sessions/2026-11-03-DayZ_MCP-promocion-live-plan.md` y `AI/30_Sessions/2026-11-03-DayZ_MCP-promocion-live.md`.
- **Launcher `dayz-test-v1` de LIVE**, resellado el 3/11 a las 12:49-12:50 fuera de la app (por WMI):
  - PE `7DFF6CE9…`, `app.pyz` `6C5F5108…`, `approved-launchers.json` `1A11C996…` (antes `2F569AA5…`), recibo `c09d02a4…`;
  - `reproducibility.json` con `reproducible=true` (clean-1, clean-2, offline);
  - el doctor da `NATIVE_BUNDLE_SOURCE_PINS_OK` y `NATIVE_BUNDLE_EXTERNALS_OK` (23).
  - `replace-dayz-test-v1 --expected-sha256` solo acepta hexadecimal en mayúsculas (`_HEX`, `tools/dayz_mcp/launcher_registry_update.py:39`). Con el hash en minúsculas que da `sha256sum` falla con el genérico `invalid_launcher_registry_update`, no con `launcher_registry_cas_mismatch`.
- **PBO vivo:** `B28CEC03…` (739 867 B), construido desde `e18e481` (`pbo_provenance.py`: PROVENANCE OK, 30 entradas), en `P:\Mods\@DayZ_MCP\addons\`.
  - `P:\Mods` es un junction a `!Workshop`: es un solo fichero, y T130 también lo carga.
  - El anterior queda al lado como `DayZ_MCP.pbo.swapped_out_20261103T115430Z_E4EBBA78DB16C225`.
- **Daemon de 8765:** generación `4333fb6d…`, con `daemon_modules.stale=[]`. Lo arrancó por WMI, a las 12:25:53, la primera llamada de un cliente, cuando el daemon anterior ya no estaba activo. No consta cómo terminó el anterior.
  - Si no hay daemon, la primera llamada de cualquier cliente lo arranca desde el árbol vivo. Con sesiones abiertas, pararlo no lo deja parado.
  - El doctor (`--daemon-policy normal`) da `DAEMON_STATUS_OK` y un FAIL que ya venía de antes, `RUN_PREPRUNE_BACKUP_SLOTS_EXHAUSTED`: los 10 huecos de `runs.json.bak-preprune` están llenos desde el 27/10, así que los runs retirados no se podan. Liberarlos lo decide el dueño.
- **Partida de prueba** `d56ffd3f-17be-4782-84a3-da72f6c3ab89` (DayZ_MCP, mode all, 3/11 12:55):
  - los dos bridges `ready`, capacidades `match`, versión `12~1.30.164200.56`;
  - 0 `SCRIPT (E)` en los RPT y los script logs de servidor y cliente;
  - `capture_screenshot` correcto, y `wait_for(players_at_least, 1)` cumplido al segundo intento;
  - godmode encendido al conectar (`profile=test`).
  - El primer `wait_for` abortó con `probe_timeout`: mientras entraba el primer jugador tras el reset del storage, el servidor dejó de hacer poll (`server_poll_stale`; un `bridge_status` midió 20,5 s desde su último poll).
  - La partida se paró con `dayz_test_stop` (`forced_kill`).
- **D1, aceptado por el dueño el 3/11:** el godmode y el relleno de agua y energía están apagados por defecto, salvo en los runs del arnés (`profile=test`), que los encienden (`CHANGELOG.md`, entrada de B1).
- **T130** (`C:\temp\DayZ_MCP_130`, 8775) sigue detached en `d825aa2` y sin tocar, pero carga el PBO nuevo por el junction. No se ha probado con él.
- **Sin medir hoy:** buzón, censo de instancias y de clientes, y riesgos abiertos. El último censo completo es el LIVE-STATE del 18/10 (`git show 19dce57d:HANDOFF.md`).
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
