# READY-FOR-SOL — decide hop next

**From:** mcp-triaje-prescore-flash-20260925 / run_id `20260925-prescore-flash`  
**When:** 2026-09-25 10:57:51 Europe/Madrid  
**Checkout frozen:** `78cc50a`

## Handoff sentence
Ready for Sol/Astra decide; G-CAL still not durable-green → no fix dispatch.

## What you inherit
| Artifact | Path |
|---|---|
| Propose JSONL (43/43) | `C:\Users\guill\AppData\Local\Temp\orq-dispatch\mcp-triaje-prescore-flash-20260925\propose.jsonl` |
| Mirror | `P:\DayZ_MCP_dev\reviews\triage-20260925-prescore-flash\propose.jsonl` |
| PRESCORE full | JobDir `prescore.json` / reviews mirror |
| Live inbox | JobDir `live-inbox.json` |
| Plan | `plans\2026-09-03-sistema-triaje-y-tratamiento-del-buzon.md` v2.2 |

## Lane
- Proponente: `agy` × `gemini-3.8-flash-low` (done; propose-only; NOT despacho authority)
- Decisor: prefer `gpt-6-sol` if catalog has it, else `gpt-5.6-sol` (plan WORKS)
- Grok: **out** of triaje
- Implement/Opus/PR: **forbidden** until G-CAL durable-green + Sol decide + Orq GO

## Constraints for decide
- Validate each `senal_que_decide` against PRESCORE `senales` for that id
- `confianza` stays DUDOSA while G-CAL red (even if Sol is FIRME-on-class, do not despachar)
- Do **not** `pipeline_resolve`, do **not** edit `GATES.md` / `gates\inbox-*`, do **not** append `triage.jsonl` until TRIAGE-SCHEMA GO
- Notable: `296b` proposed X-YA_RESUELTO (PR #91) but mailbox still open — resolve is a **separate** Orq GO after decide, not this artifact

## Coverage
43/43 rows. Skipped: none. agy failures: attempt1 RunCommand-denied (no output); attempt2 SUCCESS.
