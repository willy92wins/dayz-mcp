# GCAL-VERDICT — 20260925-gcal

- **Verdict:** GREEN
- **When:** 2026-09-25 11:17:25 Europe/Madrid
- **Checkout:** `P:\DayZ_MCP_dev` @ `78cc50a`
- **Sheets:** 35 (`plans/inbox-20260830/NN-fb-*.md`); **OWNS set:** 22
- **Descensos:** 0 (EXPECT 0)
- **Ascensos:** 5 (reported, non-blocking)
- **Matches:** 17
- **Descensos list:** (empty)

## Evidence paths

| Path | Role |
|---|---|
| `gates/triage-20260925-gcal/GATES.md` | NEW triage lote ledger (G-CAL `[x]`, sealed) |
| `gates/triage-20260925-gcal/gcal-summary.json` | counts + tallies |
| `gates/triage-20260925-gcal/gcal-prescore.json` | per-sheet PRESCORE rows |
| `gates/triage-20260925-gcal/check-gcal.mjs` | gate oracle |
| JobDir `gate-check-approve.log` | `--approve` transcript |
| JobDir `gate-check-reverify.log` | `--reverify` transcript (RC=0 ALL MET) |
| JobDir `run_gcal_remeasure.py` | PRESCORE+oracle script |

## Gate-check

- Tool: `C:\Users\guill\.claude\skills\gates-ledger\scripts\gate-check.mjs` (not under repo `tools/`; skill path is the real one)
- `--approve` RC=0 → ALL MET
- `--reverify` RC=0 → ALL MET (1 met, reran: 1, previously met reverified: 1)
- Seal present on G-CAL EVIDENCE line

## Forbidden untouched

- root `GATES.md` v9: not edited
- `gates/inbox-*`: not edited
- no `pipeline_resolve`, no Opus product fixes, no Cursor CA, not a mailbox drain

## Notes

- Initial run had 2 false descensos (f201 + d366) from over-broad schema-path C4 floor and missing D3 on promote_effective_schema; extraction rules corrected (script-first), remeasure → 0 descensos.
- Ascensos (5) left as reported; do not block G-CAL.
