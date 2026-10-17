# HANDOFF — DayZ-MCP

<!-- LIVE-STATE:START -->
# DayZ-MCP - Estado vivo - 2026-10-17 (Madrid; árboles vivos, launcher, PBO, procesos e instancias medidos a las 17:55; main tras el merge de #244)

- **main** = `659191e` (#244).
  - Desde el LIVE-STATE anterior (main `9f3e7de`, 9/10) entraron:
    - #232 (docs de las ventanas de 222d);
    - #233 (9608: `hands_take` desde cualquier attachment del mundo, y `hands_attach`; a383: la ola bug046 ya no falla al azar);
    - #234 (2986 y 0093: `dayz_test_close` espera el logout en 1.30 por la línea de conexión del script log);
    - #235 y #237 (ace5 y 1d4d: el gate de procedencia acepta el `enabled = true` que escribe la app de Codex, también en `dayz-mcp-130`);
    - #238 (11b4: el bundle nativo nombra un binario externo cambiado o borrado);
    - #239 (`steam_app_view_stale`: la app guarda una copia congelada del registro de Steam);
    - #240 (c9aa: doctor renueva un launcher desviado con `replace-dayz-test-v1`);
    - #241 (d9c8: la puerta de `storage_recovery_joint_gate` manda la ruta de la misión y nunca planta en una misión alias);
    - #242 (la release v1.4);
    - #243 (`docs/RELEASE.md` juega una partida con el PBO de la release antes del tag);
    - #244 (`server_extra_args` y `client_extra_args` en la policy del host; es el #236 de @kinkurono-dev).
  - Los árboles vivos no siguen a main. LIVE (`DayZ_MCP_dev`) está detached en `b8c8d10` (v1.4) y T130 (`C:\temp\DayZ_MCP_130`) detached en `d825aa2`, sin cambios en ficheros versionados. Sin seguimiento: `tools/native-launchers/dayz-test-v1/addonbuilder-include.lst` en los dos y `reviews/2026-10-13-9608/` en LIVE. Su rama local `main` sigue en `3c27f33`.
  - Se mueven solo dentro de una promoción, con resellado; nunca `reset --hard`. Los extras de #244 tocan el worker sellado y el lock: no cambian nada en un árbol hasta que se reselle en ese commit.
- **Release v1.4** (2026-10-17): la primera verificada en DayZ 1.30. La partida `334956a8-55cc-4398-81ab-367bfa83636c` corrió en 1.30.164.078 antes del tag. Evidencia: `C:\Users\guill\DayZ_MCP_backups\release-v1.4\EVIDENCE.md`.
  - Known issue: `action_use` y `action_hold` no comprueban `CanBeStarted()`. El arreglo cambia el PBO y lo revisa el dueño.
- **Launcher `dayz-test-v1`:** LIVE se reselló el 16/10 en la 1.30 y T130 el 17/10.
  - LIVE: PE `3E7FF1A7…`, `approved-launchers.json` `913AAABE…`. T130: PE `D432700C…`, `approved-launchers.json` `682113CD…`. `app.pyz` `E258B0BF…` en los dos.
  - En los dos, `check_native_launcher_registry.py` da ok con la procedencia anclada (23 transiciones en LIVE, 8 en T130), y `write_packaged_modules_lock.py --check` también.
  - Cada actualización de Steam de DayZ o de DayZ Tools cambia hashes que el launcher fija y obliga a resellar (11b4).
- **PBO vivo:** `7078C2E4…` (542 178 B, el de la v1.4) en `P:\Mods\@DayZ_MCP\Addons\`. `P:\Mods`, el `mods_root` de las dos policies, es un junction a `!Workshop`, así que default y 130 cargan el mismo PBO. El anterior sigue al lado como `DayZ_MCP.pbo.swapped_out_20261017T150136Z_D35364034BAE6392`.
- **Instancias:**
  - default: `C:\Program Files (x86)\Steam\steamapps\common\DayZ`, DayZDiag 1.30.164.078 (estable desde el 15/10), `:8765`, `dayz-mcp`.
  - `130`: `E:\SteamLibrary\steamapps\common\DayZ Exp`, DayZDiag 1.30.164.200, `:8775`, `dayz-mcp-130`.
  - `P:\scripts` es la 1.30; la 1.29 quedó en `P:\scripts_1.29_backup_20261016`. `P:` es un subst que se pierde al reiniciar, y sin él no se resella ni se construye el PBO.
  - Censo de las 17:55: ningún proceso DayZ del MCP (había una partida normal del dueño, con BattlEye). El daemon de 8765 (pid 50984) corría desde las 17:00:38; 8775 no tenía daemon. Clientes: 20 procesos en 6 cadenas en 8765, y 16 en 4 en 8775.
- **Buzón** (`C:\Users\guill\AppData\Local\DayZ_MCP\inbox\feedback.jsonl`), contado a las 23:25: 916 entradas y 869 resoluciones. Hay 100 abiertas, 14 de ellas nuestras: 8 del MCP y 6 del Pack.
  - Cerradas el 17/10 con su evidencia: bedf (T130 resellado), c9aa (#240), 11b4 (#238; las rutas desviadas siguen solo en doctor, porque el error del bundle no las lleva al wire), 2986 y 0093 (#234), ace5, 9d9b y 1d4d (#235, #237), d9c8 (#241), y del Pack ca47, c852, 25af, 5c98 y 35c1 (Pack #113) y 818e (Pack #117).
  - Abiertas el 17/10, del Pack:
    - e6d6 (O1: el validador tarda un tiempo cuadrático en una sentencia larga de argumentos de plantilla; solo con entrada adversarial);
    - dada (`ES-EMPTY-IFDEF` falla dos bloques vacíos que trae la 1.30: la partida de la v1.4 ejecutó sin caerse el de `DIAG_DEVELOPER`, y el de `ENABLE_LOGGING` no quedó medido, porque necesita `-doScriptLogs=1`);
    - 6b76 (con la vanilla 1.30, `ES-SYNCVAR-CONTRACT` acepta un override sin parámetros de `RegisterConstructionSyncVariables` en una clase que no deriva de `EntityAI`; es el límite que documenta el Pack #117).
    - 818e se abrió y se cerró el mismo día, con el Pack #117.
  - Siguen abiertas, nuestras: 204e, 5880, 8b1c, e92d, a97e, 75e7, fade y fb98; del Pack, a59b, 7ce1, 7b9e, e6d6, dada y 6b76.
- **Riesgos abiertos:**
  - «unable to locate running instance of steam» al lanzar desde la app: la copia congelada del registro de Steam (#239, Pack #113). Falta que el dueño pase el script de limpieza de Steam con Claude cerrado.
  - `action_use(ActionTogglePlaceObject)` no activa la colocación (`204e`).
  - El cliente se queda en el menú de pausa hasta `ui_click continuebtn` (`5880`).
  - Virtualización MSIX de `%LOCALAPPDATA%` (`e92d`).
  - Ningún verbo lee la cantidad de un ítem sin cambiarla (`fb98`).
  - `dayz-harness-apis.md` sigue anclado en el source de la 1.29.
  - El Pack sigue declarando la 1.29 como build de anclaje (`sources/source-map.json`: 1.29.0.163451), como se decidió en el Pack #117; su control vanilla ya pasa en la 1.30.164078.
- **Siguiente:**
  1. `CanBeStarted()` en `action_use` y `action_hold` (cambia el PBO; lo revisa el dueño).
  2. Reanclar `dayz-harness-apis.md` en la 1.30.
  3. Pack: medir en un mod de prueba la regla del bloque vacío (dada); siguen abiertas O1 (e6d6) y 6b76.
  4. Los extras de #244 llegan a LIVE y T130 solo con un resellado, que decide el dueño.
  5. Las fichas de diseño (8b1c, el foco; fb98; 9ab8) se hablan con el dueño antes de tocarlas.
  6. La tabla de árboles de debajo de este bloque es del 01/10 (PBO `4F3DD79D…`): ponerla al día.
- **Detalle:** la evidencia de la v1.4 está en `C:\Users\guill\DayZ_MCP_backups\release-v1.4\`, y las revisiones de Codex de esta ventana en `C:\Users\guill\dzmcp_gauntlet\reviews\`.
<!-- LIVE-STATE:END -->

---

## Árboles: `main` público vs leftovers locales

| Árbol | Qué es | Estado 2026-10-01 |
|---|---|---|
| `P:\DayZ_MCP` | Addon compilable (`$PBOPREFIX$=DayZ_MCP`). **Sin `.git`.** | **DESFASADO** (medido el 2026-09-26 contra `main`; no se ha vuelto a medir): 5 de los 13 ficheros versionados de `addon/` diferían. No empaquetar desde aquí (ficha `fb-20260925-233932-aa11`). |
| `P:\DayZ_MCP_dev` (=`...\DayZ Projects\DayZ_MCP_dev`) | Repo de producto (Python MCP, plans, reviews, este HANDOFF). `addon/` es la copia git del bridge. | Git `https://github.com/willy92wins/dayz-mcp.git`. HEAD = `main` = `origin/main` (`68e4c94`). Los backups de sesión viven en `C:/Users/guill/DayZ_MCP_backups/`, fuera del repo. |
| `…\!Workshop\@DayZ_MCP\Addons\DayZ_MCP.pbo` | PBO desplegado | SHA-256 `4F3DD79D2B2CAF2AD6A82269F3F3ECD0DCAC9AE6FCED33C86CAEF470401972DD` (2026-10-01, desde `68e4c94`, procedencia 17/17 más el marcador). Se sustituye solo con DayZ parado, el lease tomado y `tools/dev/swap_pbo.ps1 -WantOld <sha vivo>`. |
| `C:\Users\guill\Repos\dayz-mcp` | Otro clone | Rancio. No usarlo como checkout. |

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

- `MakeScreenshot` roto (T165276) → window-grab.
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
