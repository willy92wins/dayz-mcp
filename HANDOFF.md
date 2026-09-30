# HANDOFF — DayZ-MCP

<!-- LIVE-STATE:START -->
# DayZ-MCP - Estado vivo - 2026-09-30 17:00 (Madrid; medido: main, PBO, daemon y caja)

- **main** = `8bd83dc` (#164). El árbol vivo está limpio y en esa cabeza; no hay PR abiertos. Se hace fast-forward de `origin/main` solo dentro de una promoción. Nunca `reset --hard`.
- **PBO vivo:** `E923B94B57B180F3AB99F417EBE9CBD8729E0B85E0A50571F2411CB34548D0DE` (338 377 B), construido desde `8bd83dc` con procedencia 17/17 exacta.
  - Copias y evidencia: `C:\Users\guill\DayZ_MCP_backups\p11-20260930\`.
  - La copia de la P8, `DayZ_MCP.pbo.bak_pre_p10_20260930` (`32505440…`), sigue siendo la vuelta atrás.
- **Daemon:** generación `57cd5458`, arrancado a las 11:13 sobre `8bd83dc`. Doctor ok. Caja libre a las 17:00; otras sesiones han lanzado corridas desde entonces.
- **Superficie:** 73 herramientas (+ `exec_enforce` opt-in). Tier rápido: `Ran 4791 tests / OK (skipped=450)`; la suite completa pasa en la CI (Python 3.11 y 3.14).
- **Hoy (2026-09-30), sesión autónoma**, todo fusionado con el APROBADO de un revisor de otra familia y la CI en verde:
  - #152-#158: 114e (Steam.dll), 4f50, df3a, ba11, d1d9 (`action_use` `door_index`), 5535 (`anim_timeline`), los P3.
  - **#159** `5b615a0`: `weapon_fire`/`weapon_raise` llegan al servidor (486e/0063).
  - **#160** `0f207ae`: DayZDiag registra `PluginInventoryDebug` (c4e1: el NULL pointer de la toma a manos).
  - **#161** `a2acb5d`: verbo nuevo `vehicle_door` (29c1).
  - **#162** `0d0b75e`: banderas de presencia `*_set` (8779).
  - **#163** `4471e1a`: la frase de puertas de edificio de `object_anim`/`object_doors` (d1d9).
  - **#164** `8bd83dc`: `world_spawn(lifetime_s)` (bd28/e0af).
  - Todo se probó en juego con un cliente stdio propio (`dzn-night/promo/p1*_ingame.py`); la evidencia está en `DayZ_MCP_backups\p11-20260930\ingame-*`.
- **Hallazgos que cambian lo que se daba por hecho:**
  - **Antes de #162**, un argumento opcional omitido llegaba al addon como 0: `JsonSerializer` no conserva los valores por defecto del constructor de `MCPArgs`. Así, `object_anim` leído escribía fase 0 (la «reversión» de df3a/29c1) y `world_weather_set` parcial ponía a cero el resto. Todo opcional nuevo lleva una bandera `<campo>_set`.
  - **Vehículos de `world_spawn`:** la economía central los borra cuando no hay jugadores a menos de `CleanupAvoidance`, 100 m (`globals.xml:4`). La causa es su `<lifetime>` de types.xml: 3 s en `CivilianSedan`. Con `lifetime_s` sobreviven.
- **Proceso:**
  - Un verbo nuevo o un cambio del contrato del cable se prueba en juego después de fusionar y promover con reinicio del daemon, porque el daemon valida los comandos con su propio `loopback.py`.
  - Receta de promoción: `C:\Users\guill\AppData\Local\Temp\dzn-night\promo\` (`p11_archive.sh`, `swap_pbo.ps1`, `restart_daemon_p8.ps1`, `p11_doctor.py`).
- **Puertas de modelos:** Codex sin cuota hasta el 2026-10-06 a las 13:16; Grok con el límite semanal al 0 %. Implementa Opus (subagente) y revisa Gemini 3.8 Flash por `agy` (`dzn-night/run_agy_review.sh`).
- **Abierto:**
  - f5e6/35c1 (validador): el Knowledge Pack tiene WIP de otra sesión.
  - 7695: dependencias externas; decide el dueño.
  - Del dueño: 250f (rescate del pase B), e1ae, 120f, a2d5, 6ed1/75e7 (resellado).
  - En HOLD: 9ab8 (heater), e7ef, GATES.
  - fd52/5363: el Bash tool de Claude Code falla en su envoltorio; hay que reportarlo a Anthropic.
- **Handoff completo:** `C:\Users\guill\ObsidianVault\AI\30_Sessions\2026-09-30-HANDOFF-dayz-mcp-p10.md`. Decisiones: D-93 en el decision-log del vault.
<!-- LIVE-STATE:END -->

---

## Árboles: `main` público vs leftovers locales

| Árbol | Qué es | Estado 2026-09-30 |
|---|---|---|
| `P:\DayZ_MCP` | Addon compilable (`$PBOPREFIX$=DayZ_MCP`). **Sin `.git`.** | **DESFASADO** (medido el 2026-09-26 contra `main`; no se ha vuelto a medir): 5 de los 13 ficheros versionados de `addon/` diferían. No empaquetar desde aquí (ficha `fb-20260925-233932-aa11`). |
| `P:\DayZ_MCP_dev` (=`...\DayZ Projects\DayZ_MCP_dev`) | Repo de producto (Python MCP, plans, reviews, este HANDOFF). `addon/` es la copia git del bridge. | Git `https://github.com/willy92wins/dayz-mcp.git`. HEAD = `main` = `origin/main` (`8bd83dc`). Los backups de sesión viven en `C:/Users/guill/DayZ_MCP_backups/`, fuera del repo. |
| `…\!Workshop\@DayZ_MCP\Addons\DayZ_MCP.pbo` | PBO desplegado | SHA-256 `E923B94B57B180F3AB99F417EBE9CBD8729E0B85E0A50571F2411CB34548D0DE` (2026-09-30, desde `8bd83dc`, procedencia 17/17). Se sustituye solo con DayZ parado, el lease tomado y `tools/dev/swap_pbo.ps1 -WantOld <sha vivo>`. |
| `C:\Users\guill\Repos\dayz-mcp` | Otro clone | Rancio. No usarlo como checkout. |

Empaquetar SOLO desde `DayZ_MCP_dev\addon`. `pack-addon.ps1 -Ref <sha> -PackOnly -Clear -Destination …` empaqueta el `addon/` de git en esa ref y mete en la raíz del PBO `mcp_build.json` (commit, árbol de `addon/` y hora UTC de la build; no está en git), así que un PBO dice desde qué commit se construyó. Después, `tools/dev/pbo_provenance.py <pbo> <repo> <ref>` compara fichero a fichero, exige que el `prefix` de la cabecera sea el de `addon/$PBOPREFIX$` y que esa marca lleve el commit de `<ref>`, y sale con un código distinto de 0 ante cualquier diferencia. `-Source` cambia el árbol empaquetado y no debe apuntar a `DayZ_MCP`.

Herramientas de desarrollo y promoción en `tools/dev/`: `setup_worktree.sh` crea un worktree con su propio `tools/.venv-mcp` y comprueba, desde un directorio neutro, que el import editable resuelve dentro de ese worktree y que ninguna junction ni symlink lleva a su venv (los marcadores de nube de OneDrive pasan) (2eb5; `--check <dir>` repite solo la comprobación); `pbo_provenance.py`; y `swap_pbo.ps1`, que sustituye el PBO vivo solo si el build y el vivo tienen los SHA-256 esperados y no corre ningún proceso de DayZ. Mantiene el vivo bloqueado desde su hash hasta el cambio, instala los bytes que comprobó con renombrados que nunca sobrescriben y deja el anterior al lado como `DayZ_MCP.pbo.swapped_out_<UTC>_<hash>`; un `-Backup` que sea el mismo fichero con otro nombre (hard link) se rechaza.

**Higiene:**
- `reviews/2026-09-04-reserva/` y `reviews/2026-09-07-reserva-cierre/` eran snapshots de árboles completos (5,8 y 5,4 GB). **No versionarlos ni «rescatarlos» nunca**; están en backups fuera del repo.
- El scratch de Cursor (`cursor-home/`) dentro de reviews rompe `git add` por MAX_PATH; está excluido por gitignore.
- El decision-log D-NN canónico vive en el vault Obsidian (`AI/10_Projects/DayZ_MCP/decisions/decision-log.md`, hasta D-93), NO en el repo.

## Cola aparcada (PARK / PARO)

Ninguna de estas fichas está en implementación.

| Marca | Fichas | Razón para no tocar |
|---|---|---|
| **PARK** | `0ab2`, `546d`, `a429`, lote sellado (`2edd-1`, `dae1-1`) | Día + params del dueño + audit R9 (`0ab2`); no son trabajo de noche. |
| **PARO** | `3fc1`, `1025` | Parados a propósito. **No reabrir sin ángulo nuevo.** |
| **LEAVE_UNTRACKED** | `d50e` (`fb-20260907-232253-d50e`) | Juicio Sol 12-sep. La auditoría original queda sin promocionar. |

## Superficie (no recontar a ciegas)

- README / architecture / `build_app`: **73 tools** (+ `exec_enforce` opt-in).
- Suite de tests: `tools/tests/test_*.py`.
  - Suite completa: `tools\.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t .` desde `tools\`; sin pytest.
  - Tier rápido: la misma orden con `DAYZ_MCP_FAST_TESTS=1`, unos 95 s y `Ran 4791 tests / OK (skipped=450)` el 2026-09-30.
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
