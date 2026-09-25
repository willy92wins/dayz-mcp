# HANDOFF â€” DayZ-MCP

<!-- LIVE-STATE:START -->
# DayZ-MCP - Estado vivo - 2026-09-25 (Madrid)

- **main** = `72f63549f2337764a73b34c6e6a935eed0697c3b` (#92 MERGED squash; lease recovery + stale launcher pin).
- **#92 MERGED** ids `0c27`+`b1a7` lease +`99db`; `e7ef` still deferred; **NO** `pipeline_resolve` from #92.
- Checkout: local clones may lag / dirty HANDOFF - do **not** reset --hard; ff when clean. Canonical tip is `72f6354`, not older #91 tip.
- Abierto: buzon triage deferred (no Sol launch this hop). 296b=#91 cerrado; #92 tip canonico.
- HOLD dueno: Workshop / push / tester - no tocar desde Orq.
<!-- LIVE-STATE:END -->



---

## Ãrboles: `main` pÃºblico vs leftovers locales

| Ãrbol | QuÃ© es | Estado 2026-09-12 |
|---|---|---|
| `P:\DayZ_MCP` | Addon compilable (`$PBOPREFIX$=DayZ_MCP`). **Sin `.git`.** | Bytes del bridge alineados con **`main`**. |
| `P:\DayZ_MCP_dev` | Repo de producto (Python MCP, plans, reviews, este HANDOFF). `addon/` es la copia git del bridge. | Git `https://github.com/willy92wins/dayz-mcp.git`. HEAD local = `main` (tracking `origin/main`, ff desde inbox `6de5d98` = PR #17). **Inbox no mergeada.** `HANDOFF.md` entra en este commit. Untracked leftovers (backups, dumps de reviews, `NEXT-SESSION-PROMPT.txt`) se conservan y no se publican. |
| `â€¦\!Workshop\@DayZ_MCP\Addons\DayZ_MCP.pbo` | PBO desplegado | SHA-256 `1E79BF80EADE48C98BB318A96C7F4ECF73F8782EC3AD3DDD08045B7751F98C9B` (2026-09-16, desde `1737dc5`) |
| `C:\Users\guill\Repos\dayz-mcp` | Otro clone | Rancio. No usarlo como checkout. Rama local `fichas/hands-watchdog` (**13c, no estÃ¡ en main**). |

Empaquetar desde `DayZ_MCP` o desde `DayZ_MCP_dev\addon` en este checkout pilla el bridge de `main`.

Las ramas de trabajo ya fusionadas se retiraron el 2026-09-16: el remoto se queda solo con `main`, y en local sobreviven las que sujeta un worktree vivo y tres con commits sin fusionar. NingÃºn commit se pierde â€”todos siguen alcanzables desde `main` y desde sus PRâ€”, y el registro con nombre y sha de cada una queda fuera del repo, junto a la evidencia de la ola. El vault sigue con los D1-1/D1-4 del 10-sep; no son un plan activo.

## Cola aparcada (PARK / PARO)

Ninguna de estas fichas estÃ¡ en implementaciÃ³n.

| Marca | Fichas | Por quÃ© no se tocan |
|---|---|---|
| **PARK** | `0ab2`, `546d`, `a429`, lote sellado (`2edd-1`, `dae1-1`) | DÃ­a + params del dueÃ±o + audit R9 (`0ab2`); no son trabajo de noche. |
| **PARO** | `3fc1`, `1025` | Parados a propÃ³sito. **No reabrir sin Ã¡ngulo nuevo.** |
| **LEAVE_UNTRACKED** | `d50e` (`fb-20260907-232253-d50e`) | Juicio Sol 12-sep. La auditorÃ­a original no se promociona. |
| **fuera de main** | `fichas/hands-watchdog` | Local, 13 commits, no mergear por inercia. |

El buzÃ³n quedÃ³ en **66** abiertas tras W2 (2026-09-12). Esa cifra **no** es un encargo para re-resolver 95. Desglose de las 66: 20 re-enrute (W4, aÃºn ABIERTAS), PARK/PARO intactos, residuales W9/W10. El plan de olas W1â€“WF **sÃ­** estÃ¡ en vuelo (W1 = esta rama). `GATES.md` raÃ­z del cierre integral 30-ago sigue con ROOT-* en `[ ]`: ledger no actualizado a v1.2.

## Planes

**Plan MCP en vuelo: olas W1â€“WF despachadas.**

- **W1 (esta rama):** documentaciÃ³n, tablas de cadencias, banners STALE **en raÃ­z** (cuatro `AUDITORIA_*.md` de agosto) y D1â€“D4.
- **W2:** C-resolve JSONL **hecho** 2026-09-12 (29 ids; 95â†’66). No repetir.
- **W3:** tests.
- **W4:** grill `050e`/`0d65` + re-enrute de 20 fichas ajenas (no es W2).
- **W5:** occupancy (needs grill).
- Mapa general: Context `docs/mcp-end-to-end-plan.md` (no estÃ¡ en este repo).
- `2026-09-10-tres-bloques-backlog.md` (v2.1): tramo jugable **cerrado**. El resto de su universo estÃ¡ aparcado o encauzado.
- Playbooks en repo: `place_safely`, `lease_spawn_prepare_trace`, `box_is_mine`, `run_really_started`.

El buzÃ³n no se sustituye por Issues GitHub. PR #26 es leftover `546d` (W1 no la toca). Linear no existe para este producto.

## Superficie (no recontar a ciegas)

README / architecture / `build_app`: **62 tools** (+ `exec_enforce` opt-in). `CLAUDE.md` local alineado a 62 (sigue untracked). `CHANGELOG` [Unreleased] vacÃ­o; la superficie vive en [1.2]. Group G en `product-spec.md` sigue â“ â€” parked, no es un plan abierto.

## Gotchas que siguen vigentes

- `MakeScreenshot` roto (T165276) â†’ window-grab.
- `SetHeader` solo Content-Type â†’ API-key en query string.
- `*_now` bloquea el tick.
- `SetTimeMultiplier(0)` congela la sim.
