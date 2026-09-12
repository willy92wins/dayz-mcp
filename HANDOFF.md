# HANDOFF — DayZ-MCP

<!-- LIVE-STATE:START -->
# DayZ-MCP — Estado vivo · snapshot 2026-09-12

**Última verificación real:** 2026-09-12 — plan de olas W1–WF despachado. Mapa: Context `docs/mcp-end-to-end-plan.md` (no está en este repo). Esta rama cierra W1 (docs), incluido W1-P1-01 (banners STALE en raíz).
Publicado v1.2 `9f0343e`. PBO live `F82CFA8C4E557FFF…` @ `a9fe673` (PR #23), 258424 B, mtime 12-sep 01:04, Workshop + `P:\Mods\@DayZ_MCP`. Tip `main` = `edc7bb3` (0ab2) encima de `80ed00b` (a429).

bugs: (no hay `bugs.md` vivo en `DayZ_MCP_dev`) · tracker = buzón `pipeline_inbox` · **66** abiertas (W2 2026-09-12: 29 `resolves`, baseline 95→66). No recontar ni re-resolver esas 29. · toque 2026-09-12
ciclos_en_este_objetivo: 2 (W1 docs + W1-P1-01)

## Estado actual

Plan de olas W1–WF despachado en vuelo. Publicado y jugable v1.2 `9f0343e`. Tip de producto `main` = `edc7bb3` (0ab2) encima de `80ed00b` (a429). PBO live `F82CFA8C4E557FFF…` @ `a9fe673` (PR #23).

PARK leftover (no reimplementar): `a429` y `0ab2` ya están en `main`; `546d` = PR #26 abierta (esta ola NO la toca, no mergea).
PARO `3fc1` y `1025` siguen PARO. No reabrir sin ángulo nuevo.
LFPowerGrid no es el trabajo actual de este producto.

## Tickets

GitHub `willy92wins/dayz-mcp`: PR #26 abierta (`546d`, W1 no la edita). El tracker real es el buzón (`pipeline_inbox` / `pipeline_feedback` / `pipeline_resolve`) en `%LOCALAPPDATA%\DayZ_MCP\inbox\feedback.jsonl`. Identificadores `fb-AAAAMMDD-HHMMSS-xxxx`, citados por sufijo (`0de3`, `3fc1`, `1025`, …).

## Próxima acción

W2 C-resolve **ya está hecho** (2026-09-12, 29 ids, 95→66). **No** relanzar W2 ni re-resolver el lote C. Tras aterrizar W1: W3 tests; W4 grill + re-enrute de las 20 aún abiertas (otro escritor JSONL, no es un segundo W2). Occupancy = W5 (needs grill). No in-game en W1.

## Invariantes CERRADAS — NO retocar / NO reabrir sin ángulo nuevo

- **v1.2 publicado** (2026-09-12 · `9f0343e` · sesión B3 `AI/30_Sessions/2026-09-12-dayzmcp-noche-b3-v11.md`)
- **Tramo jugable de tres-bloques CERRADO** in-game: `0de3`+`5872`+`#4b`+`#6`+`9195`+`738a`+`CAMBIO` · PRs **#19–#25**
- **`84c4`** barrier mergeado (PR #25 → `6b6dd9e`)
- **`d50e` LEAVE_UNTRACKED** + resolved · evidencia `reviews/mcp-d50e-juicio-20260912/`

## Punteros (detalle)

- Mapa de olas W1–WF: Context `C:\cursor\stores\bc-79bb298c-84d6-46ba-865a-2bf73355a742\docs\mcp-end-to-end-plan.md` (no está en este repo)
- `C:\Users\guill\ObsidianVault\AI\30_Sessions\2026-09-12-dayzmcp-noche-b3-v11.md`
- Plan tres bloques (histórico; tramo jugable cerrado): `ObsidianVault\AI\10_Projects\DayZ_MCP\plans\2026-09-10-tres-bloques-backlog.md`
- Histórico pre-v1.2: [`HANDOFF-ARCHIVE.md`](HANDOFF-ARCHIVE.md)
- `NEXT-SESSION-PROMPT.txt` es de **22-ago** y está **obsoleto**. No usarlo.

**Gate de arranque:** `Retomo DayZ-MCP desde: plan de olas W1–WF en vuelo · W2 C-resolve ya aplicado (95→66, no re-resolver) · próxima acción: aterrizar W1 (banners STALE raíz) luego W3/W4; occupancy=W5`
<!-- LIVE-STATE:END -->

---

## Árboles: `main` público vs leftovers locales

| Árbol | Qué es | Estado 2026-09-12 |
|---|---|---|
| `P:\DayZ_MCP` | Addon compilable (`$PBOPREFIX$=DayZ_MCP`). **Sin `.git`.** | Bytes del bridge alineados con **`main`**. |
| `P:\DayZ_MCP_dev` | Repo de producto (Python MCP, plans, reviews, este HANDOFF). `addon/` es la copia git del bridge. | Git `https://github.com/willy92wins/dayz-mcp.git`. HEAD local = `main` (tracking `origin/main`, ff desde inbox `6de5d98` = PR #17). **Inbox no mergeada.** `HANDOFF.md` entra en este commit. Untracked leftovers (backups, dumps de reviews, `NEXT-SESSION-PROMPT.txt`) se conservan y no se publican. |
| `P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo` | PBO desplegado | SHA-256 `F82CFA8C4E557FFF0041661EC106DB6CF0ACA96C664515518DCC8459511E92AE` |
| `C:\Users\guill\Repos\dayz-mcp` | Otro clone | Rancio. No usarlo como checkout. Rama local `fichas/hands-watchdog` (**13c, no está en main**). |

Empaquetar desde `DayZ_MCP` o desde `DayZ_MCP_dev\addon` en este checkout pilla el bridge de `main`.

La rama `work/inbox-20260830-modules` @ `6de5d98` sigue en local/remoto, 22 commits detrás; no usarla como checkout. El vault sigue con los D1-1/D1-4 del 10-sep; no son un plan activo.

## Cola aparcada (PARK / PARO)

Ninguna de estas fichas está en implementación.

| Marca | Fichas | Por qué no se tocan |
|---|---|---|
| **PARK** | `0ab2`, `546d`, `a429`, lote sellado (`2edd-1`, `dae1-1`) | Día + params del dueño + audit R9 (`0ab2`); no son trabajo de noche. |
| **PARO** | `3fc1`, `1025` | Parados a propósito. **No reabrir sin ángulo nuevo.** |
| **LEAVE_UNTRACKED** | `d50e` (`fb-20260907-232253-d50e`) | Juicio Sol 12-sep. La auditoría original no se promociona. |
| **fuera de main** | `fichas/hands-watchdog` | Local, 13 commits, no mergear por inercia. |

El buzón quedó en **66** abiertas tras W2 (2026-09-12). Esa cifra **no** es un encargo para re-resolver 95. Desglose de las 66: 20 re-enrute (W4, aún ABIERTAS), PARK/PARO intactos, residuales W9/W10. El plan de olas W1–WF **sí** está en vuelo (W1 = esta rama). `GATES.md` raíz del cierre integral 30-ago sigue con ROOT-* en `[ ]`: ledger no actualizado a v1.2.

## Planes

**Plan MCP en vuelo: olas W1–WF despachadas.**

- **W1 (esta rama):** documentación, tablas de cadencias, banners STALE **en raíz** (cuatro `AUDITORIA_*.md` de agosto) y D1–D4.
- **W2:** C-resolve JSONL **hecho** 2026-09-12 (29 ids; 95→66). No repetir.
- **W3:** tests.
- **W4:** grill `050e`/`0d65` + re-enrute de 20 fichas ajenas (no es W2).
- **W5:** occupancy (needs grill).
- Mapa general: Context `docs/mcp-end-to-end-plan.md` (no está en este repo).
- `2026-09-10-tres-bloques-backlog.md` (v2.1): tramo jugable **cerrado**. El resto de su universo está aparcado o encauzado.
- Playbooks en repo: `place_safely`, `lease_spawn_prepare_trace`, `box_is_mine`, `run_really_started`.

El buzón no se sustituye por Issues GitHub. PR #26 es leftover `546d` (W1 no la toca). Linear no existe para este producto.

## Superficie (no recontar a ciegas)

README / architecture / `build_app`: **62 tools** (+ `exec_enforce` opt-in). `CLAUDE.md` local alineado a 62 (sigue untracked). `CHANGELOG` [Unreleased] vacío; la superficie vive en [1.2]. Group G en `product-spec.md` sigue ❓ — parked, no es un plan abierto.

## Gotchas que siguen vigentes

- `MakeScreenshot` roto (T165276) → window-grab.
- `SetHeader` solo Content-Type → API-key en query string.
- `*_now` bloquea el tick.
- `SetTimeMultiplier(0)` congela la sim.
