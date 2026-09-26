# Gates — triage G-CAL remesure (20260925-gcal)

Scope: durable G-CAL green (or documented FAIL) per plan v2.2 §3/§7.
Oracle: 35 hojas `plans/inbox-20260830/NN-fb-*.md`; EXPECT 0 descensos on 22 with OWNS.
Checkout: `P:\DayZ_MCP_dev` @ `78cc50a`.
Ledger: NEW triage lote only — NOT root GATES.md v9, NOT gates/inbox-*.
Script: JobDir `run_gcal_remeasure.py` → `gcal-summary.json` + `check-gcal.mjs`.
Measured pre-check: descensos=0 ascensos=5 matches=17 gcal_pass=true.

```gates
[x] G-CAL: PRESCORE vs 35 approved sheets yields 0 descensos on the 22 with OWNS
  CHECK: node check-gcal.mjs
  EXPECT: G-CAL PASS: 0 descensos on 22 OWNS
  EVIDENCE: exit=0; shell=C:\windows\system32\cmd.exe; cwd=P:\DayZ_MCP_dev\gates\triage-20260925-gcal; path=06e684565ca4/43 entries; output=G-CAL PASS: 0 descensos on 22 OWNS; seal=4a4393a80c0fb2388a0af0a6e12f30b02ecb6b2fbbc71211b0879afd697379ae
```
