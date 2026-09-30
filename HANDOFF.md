# HANDOFF — DayZ-MCP

<!-- LIVE-STATE:START -->
# DayZ-MCP - Estado vivo - 2026-10-01 02:40 (Madrid; medido: main, PBO, daemon y caja)

- **main** = `68e4c94` (#187), más el PR de release de la v1.3 (CHANGELOG, versión `1.3`, spec y este HANDOFF). El árbol vivo está limpio en `68e4c94`. Se hace fast-forward de `origin/main` solo dentro de una promoción. Nunca `reset --hard`.
- **PBO vivo:** `4F3DD79D2B2CAF2AD6A82269F3F3ECD0DCAC9AE6FCED33C86CAEF470401972DD` (377 176 B), construido desde `68e4c94`. Procedencia: 17 entradas exactas más el marcador `mcp_build.json` de ese commit.
  - Evidencia: `C:\Users\guill\DayZ_MCP_backups\v13-20260930\p18c\`.
  - Vuelta atrás: el PBO anterior queda al lado como `DayZ_MCP.pbo.swapped_out_20261001T001139Z_1882389D8372B948`, y está también en `v13-20260930\p18\pbo-3b31cbe`.
- **Daemon:** generación `1cc37140`, arrancado a las 02:12 sobre `68e4c94`. Doctor ok. Caja libre a las 02:30.
- **Superficie:** 75 herramientas (+ `exec_enforce` opt-in). Tier rápido: `Ran 5313 tests / OK (skipped=504)`; la suite completa pasa en la CI (Python 3.11 y 3.14).
- **v1.3 (2026-09-30/10-01, sesión autónoma):** fusionados #166-#187, todos con el APROBADO de un revisor de otra familia y la CI 4/4 sobre la cabeza revisada. El detalle está en `CHANGELOG.md`.
- **Verificado en juego**, con un cliente stdio propio (P17, P18a y P18a'):
  - f47b, e1ae (`input_trigger`), 3cc4/48bc, 4ed0, 6211, 89c9, bad7;
  - `world_time_get`, el prune de #183, lcrecover;
  - G1-G4, H11 (`mission_roots` y la rotación de `storage_1`), 7163, 9486, c673;
  - 20be (`ui_click mode="complete"`).
  - H9 pasa con 2 sesiones Claude y 2 Codex reales.
  - Evidencia en el vault: `research/2026-09-30-v13/` (`p17/`, `p18/`, `h9/`).
- **Pendiente de la release:**
  - la ventana del dueño (P18b): resellar el launcher (6ed1, más la decisión de f298), rescatar el pase B con el vigilante permanente (250f) y c261;
  - después, con el sí del dueño: la etiqueta `v1.3`, los assets de `docs/RELEASE.md`, y comentar y cerrar #93.
- **Hallazgos abiertos:**
  - c8c7 (el aviso de reset de la rotación no llega a quien llama) y 7f27 (escalares genéricos en todos los verbos), para la v1.4;
  - bf5c, 8cf9, a97e y 7672 (alcance de AddonBuilder y publicación del bundle con handles vivos): decide el dueño.
- **Puertas de modelos:** implementa Opus (subagente) y revisan Codex gpt-6-sol (`dzn-night/run_review.sh`) o Gemini 3.8 Flash por `agy` (`dzn-night/run_agy_review.sh`).
- **Arnés:** el Bash tool de Claude Code falla en su envoltorio en esta sesión (fd52/5363). Se usa PowerShell con Git Bash y scripts `.sh` en fichero.
- **Handoff completo:** `C:\Users\guill\ObsidianVault\AI\30_Sessions\2026-10-01-HANDOFF-dayz-mcp-v13.md`. Decisiones: D-94 a D-96 en el decision-log del vault.
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
| **Del dueño** | `bf5c`, `8cf9`, `a97e` (alcance del `-addon` de AddonBuilder, source en `P:`), `7672` (publicar el bundle con handles vivos), f298 (el foco al arrancar) | El arreglo toca el worker o el launcher sellados. |

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
