# HANDOFF — DayZ-MCP

<!-- LIVE-STATE:START -->
# DayZ-MCP — Estado vivo · snapshot 2026-09-12

**Última verificación real:** 2026-09-12 — campaña tres-bloques (tramo jugable) cerrada in-game; release **v1.2** y PBO live hasheados. Producto en **pausa**. No hay plan MCP en vuelo.

bugs: (no hay `bugs.md` vivo en `DayZ_MCP_dev`) · tracker = buzón `pipeline_inbox` · ~95 sin resolver (heurística 2026-09-12 sobre `feedback.jsonl`) · toque 2026-09-12
ciclos_en_este_objetivo: 0 (pausa de producto; no hay objetivo de implementación activo)

## Estado actual

Publicado y jugable. Tip de producto `6b6dd9e` (merge PR **#25**, barrier 84c4); este HANDOFF va encima en `main`. Release [v1.2](https://github.com/willy92wins/dayz-mcp/releases/tag/v1.2) @ `9f0343e`. PBO live `F82CFA8C4E557FFF…` @ `a9fe673` (PR #23), 258424 B, mtime 12-sep 01:04, Workshop + `P:\Mods\@DayZ_MCP` según el cierre de esa noche.

Cola restante **PARK / PARO**, no en ejecución. Este HANDOFF describe **DayZ-MCP**. LFPowerGrid no es el trabajo actual de este producto: si el dueño está en otro mod, eso no abre un plan MCP.

## Tickets

GitHub `willy92wins/dayz-mcp`: **0 issues**, **0 PRs abiertas**. El tracker real es el buzón (`pipeline_inbox` / `pipeline_feedback` / `pipeline_resolve`) en `%LOCALAPPDATA%\DayZ_MCP\inbox\feedback.jsonl`. Identificadores `fb-AAAAMMDD-HHMMSS-xxxx`, citados por sufijo (`0de3`, `3fc1`, `1025`, …).

## Próxima acción

Nada que implementar. No inventar un plan vivo. Retomar solo si el dueño despacha una ficha concreta. Si se retoma producto: working tree en **`main`**, no en la rama inbox.

## Invariantes CERRADAS — NO retocar / NO reabrir sin ángulo nuevo

- **v1.2 publicado** (2026-09-12 · `9f0343e` · sesión B3 `AI/30_Sessions/2026-09-12-dayzmcp-noche-b3-v11.md`)
- **Tramo jugable de tres-bloques CERRADO** in-game: `0de3`+`5872`+`#4b`+`#6`+`9195`+`738a`+`CAMBIO` · PRs **#19–#25**
- **`84c4`** barrier mergeado (PR #25 → `6b6dd9e`)
- **`d50e` LEAVE_UNTRACKED** + resolved · evidencia `reviews/mcp-d50e-juicio-20260912/`

## Punteros (detalle)

- `C:\Users\guill\ObsidianVault\AI\30_Sessions\2026-09-12-dayzmcp-noche-b3-v11.md`
- Plan tres bloques (histórico; tramo jugable cerrado): `ObsidianVault\AI\10_Projects\DayZ_MCP\plans\2026-09-10-tres-bloques-backlog.md`
- Histórico pre-v1.2: [`HANDOFF-ARCHIVE.md`](HANDOFF-ARCHIVE.md)
- `NEXT-SESSION-PROMPT.txt` es de **22-ago** y está **obsoleto**. No usarlo.

**Gate de arranque:** `Retomo DayZ-MCP desde: pausa de producto / cola PARK-PARO · próxima acción: ninguna, salvo despacho explícito del dueño`
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

El buzón sigue con ~95 ítems sin resolución (15 request / 52 finding / 26 bug / 2 tool_contribution, heurística `id` sin `resolves`). Eso es backlog del tracker, **no** un plan en curso. `GATES.md` raíz del cierre integral 30-ago sigue con ROOT-* en `[ ]`: ledger no actualizado a v1.2.

## Planes

**Ningún plan MCP está en vuelo.**

- `2026-09-10-tres-bloques-backlog.md` (v2.1): ataque de aquella noche. Tramo jugable **cerrado**. El resto de su universo (PARK/PARO + fichas del buzón no despachadas) está **aparcado**.
- `plans/` del `_dev` (fases 0–5, broker, UI dialog, `inbox-20260830`, steam 08-sep): históricos o absorbidos.
- Playbooks en repo: `place_safely`, `lease_spawn_prepare_trace`, `box_is_mine`, `run_really_started`.

No hay Issues/PRs GitHub que sustituyan esa cola. Linear no existe para este producto.

## Superficie (no recontar a ciegas)

README público: **62 tools**. `CLAUDE.md` del `_dev` aún dice 58 / bridge v10. `CHANGELOG` «Unreleased» habla de 54 tools y Group G pendiente — **desfasado** respecto al cierre in-game. Corregir docs es deuda aparcada, no un plan abierto.

## Gotchas que siguen vigentes

- `MakeScreenshot` roto (T165276) → window-grab.
- `SetHeader` solo Content-Type → API-key en query string.
- `*_now` bloquea el tick.
- `SetTimeMultiplier(0)` congela la sim.
