# READY-NEXT - after Sol decide 20260925

**From:** mcp-triaje-sol-decide-20260925 / run_id `20260925-sol-decide`  
**When:** 2026-09-25 11:09:09 Europe/Madrid  
**Checkout frozen:** `78cc50a`

## Hard gate (unchanged)
**G-CAL still NOT durable-green** => blocks:
- Opus / implement / fix dispatch
- `pipeline_resolve`
- closing mailbox rows (including `296b` / `678b`)
- append to `triage.jsonl` without TRIAGE-SCHEMA GO
- edits to `GATES.md` v9 / `gates\inbox-*`

Decide classes are authoritative **as classes only**; `confianza=DUDOSA` on all 43.

## What Orq may do after (allowed, still no fix)

1. **Remeasure G-CAL** on the 35 oracle sheets (plan v2.2) until durable-green.
2. **Owner GO** to resolve `fb-20260924-012921-296b` (and optionally `678b`) **only after** G-CAL green + explicit Orq GO — PR #91 @ `78cc50a` is already merged; mailbox still open; resolve is a separate hop.
3. Consume `decide.jsonl` / `RESUMEN.md` for planning: C3 CAMBIO/EVIDENCIA lists are candidates, not tickets.
4. Optional: TRIAGE-SCHEMA GO before any `triage.jsonl` lateral register write.
5. Do **not** spawn Opus/fix lanes from this decide alone.

## Pointers
- Decide: `C:\Users\guill\AppData\Local\Temp\orq-dispatch\mcp-triaje-sol-decide-20260925\decide.jsonl`
- RESUMEN: `...\mcp-triaje-sol-decide-20260925\RESUMEN.md`
- Mirror: `P:\DayZ_MCP_dev\reviews\triage-20260925-prescore-flash\decide.jsonl`
- Prior Flash: `...\mcp-triaje-prescore-flash-20260925\` (CLOSED 43/43)

## Coverage snapshot
43/43; OVERRIDE vs Flash = **10**; X-YA_RESUELTO candidates = `296b`, `678b` (not resolved).