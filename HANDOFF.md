# HANDOFF â€” DayZ-MCP

<!-- LIVE-STATE:START -->
# DayZ-MCP - Estado vivo - 2026-09-26 (Madrid)

- **main** = `e03282b30e17a9e1ce2130561d193291eb72ab77` (#95 MERGED squash; foreign_ports DayZ-related default / 1432).
- **#95 MERGED** tip pre-squash `0515ad9` → main `e03282b`; Auditor LIMPIO. Ticket `fb-20260917-092743-1432` stays **OPEN**.
- Prior: #94 `9af5cbf` arg-contract hash fail-closed / 0878 detectability; #92 `72f6354` wave1 resolve `0c27`+`b1a7`+`99db` CLOSED; `e7ef` deferred.
- Checkout: ff `origin/main` when clean; do **not** reset --hard. Canonical tip is `e03282b`.
- Abierto: triaje buzón continúa (FN/Flash/script; **NO Sol** en tickets). 0 PRs abiertos.
- HOLD dueño: Workshop re-pack / heater / GATES / e7ef / 0878 — no tocar desde Orq sin OT/GO. Ticket `fb-20260924-235528-0878` stays **OPEN**.
<!-- LIVE-STATE:END -->



---

## Ãrboles: `main` pÃºblico vs leftovers locales

| Ãrbol | QuÃ© es | Estado 2026-09-12 |
|---|---|---|
| `P:\DayZ_MCP` | Addon compilable (`$PBOPREFIX$=DayZ_MCP`). **Sin `.git`.** | **DESFASADO (medido 2026-09-26 contra `main` `1785212`):** 5 de los 13 ficheros versionados de `addon/` difieren (`MCPBridge.c`, `MCPClientBridge.c`, `MCPDialogController.c`, `MCPMessages.c`, `MissionGameplay.c`) (lleva `vehicle_drive`/`drive_probe_client`, retirados en `5892014`) con la misma version `"10"`. No empaquetar desde aqui (ficha `fb-20260925-233932-aa11`). |
| `P:\DayZ_MCP_dev` | Repo de producto (Python MCP, plans, reviews, este HANDOFF). `addon/` es la copia git del bridge. | Git `https://github.com/willy92wins/dayz-mcp.git`. HEAD local = `main` (tracking `origin/main`, ff desde inbox `6de5d98` = PR #17). **Inbox no mergeada.** `HANDOFF.md` entra en este commit. Untracked leftovers (backups, dumps de reviews, `NEXT-SESSION-PROMPT.txt`) se conservan y no se publican. |
| `â€¦\!Workshop\@DayZ_MCP\Addons\DayZ_MCP.pbo` | PBO desplegado | SHA-256 `1E79BF80EADE48C98BB318A96C7F4ECF73F8782EC3AD3DDD08045B7751F98C9B` (2026-09-16, desde `1737dc5`) |
| `C:\Users\guill\Repos\dayz-mcp` | Otro clone | Rancio. No usarlo como checkout. Rama local `fichas/hands-watchdog` (**13c, no estÃ¡ en main**). |

Empaquetar SOLO desde `DayZ_MCP_dev\addon` (`pack-addon.ps1` empaqueta por defecto el `addon/` de git en `-Ref`, `HEAD` si no se pasa; `-Source` cambia el arbol empaquetado y no debe apuntar a `DayZ_MCP`). `DayZ_MCP` no esta alineado con `main` (corregido 2026-09-25; ver la fila de arriba y `reviews/2026-09-25-vista-alto-nivel/INFORME.md` seccion 2.2 C1).

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

README / architecture / `build_app`: **63 tools** (+ `exec_enforce` opt-in; `build_app` offline sobre `57269d2`, 2026-09-25). `CLAUDE.md` local alineado a 63 (corregido 2026-09-25; sigue untracked). `CHANGELOG` [Unreleased] vacÃ­o; la superficie vive en [1.2]. Group G en `product-spec.md` sigue â“ â€” parked, no es un plan abierto.

## Gotchas que siguen vigentes

- `MakeScreenshot` roto (T165276) â†’ window-grab.
- `SetHeader` solo Content-Type â†’ API-key en query string.
- `*_now` bloquea el tick.
- `SetTimeMultiplier(0)` congela la sim.
<!-- Asistente distill 2026-09-25 ~20:50 Madrid
- RP noche: real `main` tip `72f6354` (PR #92 MERGED). LIVE-STATE header rewritten 2026-09-25 noche to tip `72f6354` (#92 MERGED). Local checkout may still lag.
- RP note: local checkout still at `78cc50a` (dirty HANDOFF; no ff). LIVE-STATE header rewritten 2026-09-25 noche to tip `72f6354`; origin/main fetched = `72f6354`.
-->