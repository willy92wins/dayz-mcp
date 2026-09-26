# HANDOFF — DayZ-MCP

<!-- LIVE-STATE:START -->
# DayZ-MCP - Estado vivo - 2026-09-26 noche (Madrid)

- **main** = `f1b7b85` (higiene: untrack sessions crudos inbox-plan-v6; working tree LIMPIO; solo rama `main` local y remota).
- **Gauntlet 26-sep cerrado al 100%** (issues **#102/#103/#104/#105 CLOSED** con veredicto Sol y arbitraje publicados):
  - #102 gate caps/ach + fixture GamePeer (`48fe322`) + deuda C.4 shims standalone (`5829ce8`) — APPROVE.
  - #103 disclosure vs contrato via helper `active_lease_token` (`03873ed`) — APPROVE.
  - #104 triaje deriva residual ficha a ficha (`31620b5`); ficha 15 (a429 capa 2) = duplicado de #102 (verificado en integración).
  - #105 bug037 fix PRODUCCIÓN: `call_bridge` acota probe de liveness por deadline (`da22937`, server.py:2284) — APPROVE; repro 0.4/2.0 OK.
- **Integración**: merges `--no-ff` #103→#102→#104→#105 (`cc02221`→`db6c430`) + docs `93af08c`. **Suite final: 4174 tests en 372s — 5F/0E/10S** (baseline roto era 56F/5E). Los 5 fallos restantes son PREEXISTENTES (telemetry ×3, session_e2e release, mcp_tools description — verificados contra `ut-fails-4174.txt`).
- **Limpieza repo (26/27-sep)**: 11 ramas locales + 8 remotas borradas (solo `main`); worktrees Temp purgados; commits de higiene `1901c59`/`1ee9315`/`f1b7b85`; untracked 461→0; ~25 GB de chatarra movida a `C:/Users/guill/DayZ_MCP_backups/` (bak, _fase*, _poc, _s0, snapshots reserva, evidence, cursor scratch). `.gitignore` nuevo anti-bak/scratch/logs-crudos.
- Prior: #100 `8e6d0ad` tool-count SSOT 62→63; #98 `1785212` run_not_owned/session_acquire_wait / 9ccc; #97 `515fb94` camera_set RestoreGameplay / 9957; #95 `e03282b` foreign_ports / 1432; #94 `9af5cbf` arg-contract / 0878; #92 `72f6354` wave1.
- Checkout: ff `origin/main` when clean; do **not** reset --hard. Canonical tip is `f1b7b85`.
- Abierto: **deuda técnica documentada** (`_orquestacion/gauntlet-r2-CIERRE.md`): 3 probes de liveness sin acotar (~server.py:2331/2432/2486), C.1 (rediseño test e2e), GamePeer extra sin censar en `tools/_session_coordination/e2e_agent_sessions.py:183`. Issue #93 (instalación Windows) sigue OPEN. 0 PRs abiertos.
- HOLD dueño: Workshop re-pack / heater / GATES / e7ef - no tocar desde Orq sin OT/GO. Ticket `fb-20260924-235528-0878`: su fix está en main (PR #94); el issue de GitHub #102 que lo cubría está CLOSED.
- **PENDIENTE DUEÑO**: decisión de orquestación (asistente GLM vs Qwen Flash-Next para el slot GX10) tras prueba 26-sep superada; fase 2 MCP in-game cuando la caja esté libre.
<!-- LIVE-STATE:END -->

---

## Árboles: `main` público vs leftovers locales

| Árbol | Qué es | Estado 2026-09-26 |
|---|---|---|
| `P:\DayZ_MCP` | Addon compilable (`$PBOPREFIX$=DayZ_MCP`). **Sin `.git`.** | **DESFASADO (medido 2026-09-26 contra `main`):** 5 de los 13 ficheros versionados de `addon/` difieren (`MCPBridge.c`, `MCPClientBridge.c`, `MCPDialogController.c`, `MCPMessages.c`, `MissionGameplay.c`) (lleva `vehicle_drive`/`drive_probe_client`, retirados en `5892014`) con la misma version `"10"`. No empaquetar desde aqui (ficha `fb-20260925-233932-aa11`). |
| `P:\DayZ_MCP_dev` (=`...\DayZ Projects\DayZ_MCP_dev`) | Repo de producto (Python MCP, plans, reviews, este HANDOFF). `addon/` es la copia git del bridge. | Git `https://github.com/willy92wins/dayz-mcp.git`. HEAD = `main` = `origin/main` (`f1b7b85`). **Limpieza 26-sep: única rama `main`; sin untracked.** Los backups de sesión viven en `C:/Users/guill/DayZ_MCP_backups/` (fuera del repo). |
| `…\!Workshop\@DayZ_MCP\Addons\DayZ_MCP.pbo` | PBO desplegado | SHA-256 `1E79BF80EADE48C98BB318A96C7F4ECF73F8782EC3AD3DDD08045B7751F98C9B` (2026-09-16, desde `1737dc5`) — **desfasado vs main (merges 26-sep: caps/ach gate, disclosure fix, bug037); re-pack bajo HOLD del dueño.** |
| `C:\Users\guill\Repos\dayz-mcp` | Otro clone | Rancio. No usarlo como checkout. |

Empaquetar SOLO desde `DayZ_MCP_dev\addon` (`pack-addon.ps1` empaqueta por defecto el `addon/` de git en `-Ref`, `HEAD` si se omite; `-Source` cambia el arbol empaquetado y no debe apuntar a `DayZ_MCP`). `DayZ_MCP` no esta alineado con `main`.

**Higiene 26-sep**: `reviews/2026-09-04-reserva/` y `reviews/2026-09-07-reserva-cierre/` eran snapshots de árboles completos (5,8 y 5,4 GB) — **nunca versionar ni "rescatar"**; están en backups fuera del repo. El scratch de Cursor (`cursor-home/`) dentro de reviews rompe `git add` por MAX_PATH: excluido por gitignore. El decision-log D-NN canónico vive en el vault Obsidian (`AI/10_Projects/DayZ_MCP/decisions/decision-log.md`, hasta D-64), NO en el repo.

## Cola aparcada (PARK / PARO)

Ninguna de estas fichas está en implementación.

| Marca | Fichas | Razón para no tocar |
|---|---|---|
| **PARK** | `0ab2`, `546d`, `a429`, lote sellado (`2edd-1`, `dae1-1`) | Día + params del dueño + audit R9 (`0ab2`); no son trabajo de noche. |
| **PARO** | `3fc1`, `1025` | Parados a propósito. **No reabrir sin ángulo nuevo.** |
| **LEAVE_UNTRACKED** | `d50e` (`fb-20260907-232253-d50e`) | Juicio Sol 12-sep. La auditoría original queda sin promocionar. |

Nota 26-sep: la ficha `a429` capa 2 quedó resuelta como duplicado del fix de #102 (verificado en integración). El buzon historico (`plans/inbox-20260830/`) está versionado en el repo desde la higiene 26-sep.

## Superficie (no recontar a ciegas)

README / architecture / `build_app`: **63 tools** (+ `exec_enforce` opt-in) — SSOT `8e6d0ad` (#100). Suite de tests: `tools/tests/test_*.py` con `tools\.venv-mcp\Scripts\python.exe -m unittest discover -s tools/tests -p "test_*.py"` (4174 tests, ~6 min; sin pytest). Estado 26-sep: 5F/0E/10S (los 5 preexistentes). `CLAUDE.md`/`AGENTS.md`/`GATES.md`/`PROJECT-MAP.md` versionados desde `1901c59`.

## Gotchas que siguen vigentes

- `MakeScreenshot` roto (T165276) → window-grab.
- `SetHeader` solo Content-Type → API-key en query string.
- `*_now` bloquea el tick.
- `SetTimeMultiplier(0)` congela la sim.
- Lanes claude headless: SIEMPRE `--allowedTools` explícito (Read/Write/Edit/Glob/Grep/Bash(venv)+git+gh) y 1 lane = 1 git worktree (en el mismo cwd se pisan). Sol revisor: `codex.cmd exec -s read-only --skip-git-repo-check -m gpt-5.6-sol` (sandbox sin tmp: briefs dentro del cwd; done marker "tokens used" en stderr).
- Suite: usar venv absoluto; `python -m unittest tools.tests.X` NO funciona; `PYTHONPATH=tools` necesario desde worktrees.
<!-- Asistente distill 2026-09-26 noche Madrid
- Gauntlet r2 completo: 3 tickets iniciales + #105 iterados hasta OK (mandato "no pares"); integracion en main verificada con suite 4174 (5F/0E/10S vs baseline 56F/5E); issues cerrados con veredicto; commit+push.
- Limpieza integral: ramas (solo main), worktrees, 25 GB chatarra -> DayZ_MCP_backups, gitignore anti-bak, docs vivos versionados (1901c59/1ee9315/f1b7b85).
- Veredictos y cierres: _orquestacion/gauntlet-2026-09-26-RESUMEN.md, _orquestacion/gauntlet-r2-CIERRE.md, _orquestacion/veredictos/.
- LIVE-STATE header rewritten 2026-09-26 noche to tip f1b7b85 (higiene); origin/main fetched = f1b7b85.
-->
