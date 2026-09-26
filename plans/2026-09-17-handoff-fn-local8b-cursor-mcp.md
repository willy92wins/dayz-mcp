# HANDOFF → sesión Cursor proyecto DayZ MCP
Fecha: 2026-09-17 (Madrid) · De: Experto IA · Pedido Guillermo: cerrar y pasar a Cursor MCP

## Objetivo producto
MCP usable por modelos locales gratis (Flash-Next / ~8B) para que el producto pueda ser gratis (sin fricción de coste API).

## Qué se hizo hoy
1. Soak overnight 4× FN era **stubs**, no MCP real (0 tickets esperable).
2. Pack **local-8B-v1** (13 tools, ~8.5 KB) probado con FN.
3. Batería **FN piloto E2E** (29 tools, ~28 KB): FN lanzó DayZ solo (dayz_test_run), lease, world read/write, screenshot, tickets. Caja quedó RUNNING_IDLE (Guillermo cerró el agente).

## Prioridad de triaje (lote)
### P0 — rompe el loop de modelos pequeños
1. `fb-20260917-100411-5edf` — `ready.reason=server_poll_stale` falso negativo tras launch fresco (poll ages del peer muerto).
2. `fb-20260917-092908-5bde` / related — errores citan tools no expuestas / next_step muerto.
3. `fb-20260917-100554-d0e0` — `session_release` → `lease_invalid` tras expiry silencioso; sin next_step.
4. `fb-20260917-095637-8011` — `knowledge_find` manda a prepare cuando `can_prepare=false`.

### P1 — UX modelos locales / pack
5. `fb-20260917-092908-2ad1` — progressive disclosure (~56 KB catálogo completo).
6. `fb-20260917-092919-1bcb` — respuestas OK sin `next_step`.
7. `fb-20260917-092908-2492` / `fb-20260917-095637-f8e0` — ready/reason primero + threshold/age en stale.
8. `fb-20260917-092908-1765` / `fb-20260917-092908-baf9` — unificar not_ready + fail-fast envelope.
9. `fb-20260917-100525-f18d` — knowledge pack: install path + un solo error code.
10. `fb-20260917-092908-0505` — can_prepare=false pero prepare callable.

### P2 — ruido / semántica
11. `fb-20260917-092908-2e3f` / `fb-20260917-095653-26ac` — foreign_ports ruido + flag DayZ-relevance.
12. `fb-20260917-100436-b286` — object_inspect type vacío en validación.
13. `fb-20260917-100452-e5cb` — notify_players sent:1 con 0 players.

### Findings de contexto (no bloquean merge)
- `fb-20260917-092908-e951` — gauntlet 5/3 sin cliente (histórico).

## Pack propuesto local-8B (default para FN)
meta+session+pipeline+knowledge (+ dayz_test_* cuando haga falta caja). World/vehicle/ui en pack 2 tras lease.

## Artefactos
- `C:\Users\guill\AppData\Local\Temp\fn-mcp-box-full-20260917\` (FN_PILOT_SUMMARY, RECAP-FN-PILOT, transcripts)
- `C:\Users\guill\AppData\Local\Temp\fn-mcp-local8b-20260917\RECAP.md`
- `C:\Users\guill\AppData\Local\Temp\fn-mcp-gauntlet-20260917\TOOLS-CATALOG.md`
- Repo vivo: `P:\DayZ_MCP_dev` / OneDrive DayZ Projects\DayZ_MCP_dev

## Pedido a Cursor MCP
Triaje v2.2 por lote: clasificar P0–P2, proponer PR(s) o fichas de arreglo; no merge sin Auditor. Preferir fixes que mejoren el loop de modelos locales (ready honest, next_step, packs, knowledge install).

IDs piloto de hoy: fb-20260917-100411-5edf, fb-20260917-100436-b286, fb-20260917-100452-e5cb, fb-20260917-100525-f18d, fb-20260917-100554-d0e0
IDs mañana/pack: fb-20260917-092908-2492, fb-20260917-092908-1765, fb-20260917-092908-5bde, fb-20260917-092908-2e3f, fb-20260917-092908-0505, fb-20260917-092908-2ad1, fb-20260917-092908-baf9, fb-20260917-092908-e951, fb-20260917-092919-1bcb, fb-20260917-095637-8011, fb-20260917-095637-f8e0, fb-20260917-095653-26ac
