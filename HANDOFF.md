# HANDOFF — DayZ-MCP

<!-- LIVE-STATE:START -->
# DayZ-MCP - Estado vivo - 2026-10-08 22:05 (Madrid; medido: main, árboles vivos, launcher, PBO y procesos)

- **main** = `4233351` (#226).
  - Los árboles vivos (LIVE `DayZ_MCP_dev` en `main` y T130 `C:\temp\DayZ_MCP_130`, detached) siguen en `46833d7` (despliegue v11b del 2026-10-08).
  - Pendiente de la próxima promoción:
    - #223 y #224 (test y descripciones; nada sellado);
    - #226 (855c: worker sellado). Necesita resellar y pasar `tools/dev/storage_recovery_joint_gate.py`.
  - Se hace fast-forward solo dentro de una promoción; nunca `reset --hard`.
- **Launcher `dayz-test-v1`, resellado el 2026-10-08:**
  - PE de LIVE `E1788375…` y de T130 `44372199…`; mismo `app.pyz` `8B932082…`.
  - Backups y vuelta atrás: `C:\Users\guill\DayZ_MCP_backups\v11b-20261008\` (`steps.log`).
- **PBO vivo:** `CC616EC7…` (puente "11").
- **Instancias:** default (1.29, `:8765`, `dayz-mcp`) y `130` (1.30 Exp, `:8775`, `dayz-mcp-130`).
  - Al cierre, el orquestador registró sus harnesses cerrados y ningún proceso propio pendiente. El censo global de las 22:02 y la reconexión de otras sesiones no están acreditados en esta evidencia.
- **Verificado en juego el 2026-10-08:** `reviews/2026-10-06-ticketing/ingame-v11b/RESULTS.md`.
- **Riesgos abiertos:**
  - 130: `dayz_test_close` no espera el logout porque el RPT de la 1.30 escribe la conexión en español (`fb-20261008-141137-2986`). El arreglo quedó aparcado: `reviews/2026-10-06-ticketing/round2/2986_PARKED.md`.
  - El rechazo de storage sin `storage_recovery_reason` (855c) está arreglado en main, pero no desplegado.
  - `dayz_test_run(build=true)` con binarize no empaqueta scripts (`fb-20261008-141215-222d`).
  - Virtualización MSIX de `%LOCALAPPDATA%` para procesos lanzados desde la app de Claude (`fb-20261008-141157-e92d`).
- **Ronda 2 (resultado):** `reviews/2026-10-06-ticketing/round2/ROUND2.md`.
  - 855c: mergeado.
  - 2986: aparcado.
  - B `action_hold` (9941), C `action_cursor`/`player_look_at` (86a3) y la infraestructura del broker: parados tras su tope de rondas. El trabajo se conserva en `C:\Users\guill\dzmcp_gauntlet\` (`r2b_9941\ws`, `r2c_86a3\ws`, `r2i_broker\ws`).
- **Siguiente sesión (decisión del dueño, 2026-10-08):**
  1. gpt-6.1-sol recorta la infraestructura del broker a lo mínimo: abandono por id y token de binding completo, sin retención ni atribución de recibos.
  2. Grok la implementa y Sol la revisa.
  3. B y C, una ronda final encima.
  4. PBO "12" y ciclo in-game.
  5. Ventana de resellado para 855c (y lo que entre: 222d, foco de f298).
- **Detalle:**
  - Notas: `C:\Users\guill\dzmcp_gauntlet\SESSION-STATE.md`.
  - Handoff: `C:\Users\guill\ObsidianVault\AI\30_Sessions\2026-10-08-HANDOFF-dayz-mcp-ticketing.md`.
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

- README / architecture / `build_app`: **75 tools** (+ `exec_enforce` opt-in).
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
