# INDEX — mcp-gcal-remeasure-20260925

**Status:** COMPLETE  
**Machine:** Willy `608a9767-5c78-4986-b0cd-762eb2f79a0b`  
**Finished:** 2026-09-25 11:17:25 Europe/Madrid  
**run_id:** `20260925-gcal`  
**Checkout:** `P:\DayZ_MCP_dev` @ `78cc50a`  
**BRIEF.sha256:** 5800BF49B92BB94263752F1C149C4B98BC4433CE148238752DB04F6F85A0E2C8 (verified)  
**OT-SEAL:** YES  
**Verdict:** GREEN — descensos=0 on 22 OWNS; gate-check --reverify RC=0

| id | kind | out | state |
| --- | --- | --- | --- |
| gcal-prescore | script | gcal-summary.json / gcal-prescore.json | COMPLETE 0 descensos |
| gates-ledger | write | gates/triage-20260925-gcal/GATES.md | COMPLETE |
| gate-check --approve | skill gate-check.mjs | gate-check-approve.log | RC=0 ALL MET |
| gate-check --reverify | skill gate-check.mjs | gate-check-reverify.log | RC=0 ALL MET |
| verdict | md | GCAL-VERDICT.md | GREEN |

## Artifacts

### JobDir
`C:\Users\guill\AppData\Local\Temp\orq-dispatch\mcp-gcal-remeasure-20260925\`

- `run_gcal_remeasure.py`, `gcal-summary.json`, `gcal-prescore.json`
- `check-gcal.mjs`, `GATES.md` (copy), `GCAL-VERDICT.md`
- `gate-check-approve.log`, `gate-check-reverify.log`
- `INDEX.md` (this file)

### Repo (write-set)
- `gates/triage-20260925-gcal/**` (NEW triage lote ledger + oracle)
- optional mirror `reviews/triage-20260925-gcal/**`

## Mutations

- **Zero** edits to root `GATES.md` v9
- **Zero** edits to `gates/inbox-*`
- **Zero** `pipeline_resolve` / mailbox / Opus product / Cursor CA

## POST
### Hecho
- OT-SEAL YES; BRIEF.sha256 verified; checkout `78cc50a` on `P:\DayZ_MCP_dev`
- PRESCORE vs 35 `NN-fb-*.md`; OWNS set 22; **0 descensos** (5 ascensos reported)
- NEW ledger `gates/triage-20260925-gcal/GATES.md` with G-CAL box + EXPECT
- `gate-check.mjs --approve` then `--reverify` → RC=0 ALL MET (sealed)
- `GCAL-VERDICT.md` = **GREEN**

### Blockers
- none for G-CAL durability; dispatch still needs Orq GO (Sol decide already closed)

### LL-candidatos
1. Bare substring schema-in-OWNS-path over-fired C4 floor (f201); tighten to promote/CAS/jsonl/inbox or explicit bytes-ajenos cues
2. D3 must treat `promote_effective_schema` as durable-data (d366 plan C4 example)

### Skills canónicas
- `gates-ledger` / `gate-check.mjs` (path under `~/.claude/skills/gates-ledger/scripts/`, not repo `tools/`)

### LIVE-STATE
- G-CAL **durable GREEN** via triage lote ledger + reverify seal
- tip `78cc50a`; unresolved mailbox still 43 (unchanged this hop)
- Flash glue: **not used**; Opus harness: **not used** (gate-check found at skill path)

timeout_sec: prescore ~3s; approve+reverify ~2s
0 Cursor CA. Script-first.
