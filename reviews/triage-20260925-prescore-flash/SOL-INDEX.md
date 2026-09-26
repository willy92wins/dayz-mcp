# INDEX - mcp-triaje-sol-decide-20260925

**Status:** COMPLETE (decide-only; NO dispatch / NO resolve)  
**Machine:** Willy `608a9767-5c78-4986-b0cd-762eb2f79a0b`  
**Finished:** 2026-09-25 11:09:09 Europe/Madrid (UTC+2)  
**run_id:** `20260925-sol-decide`  
**Checkout:** `P:\DayZ_MCP_dev` @ `78cc50a` (verified pre-launch; Sol also saw OneDrive copy @ `78cc50a`)  
**BRIEF.sha256:** `0B4F48AF3BE0AFC67C5118DBB9EADB4EF4FE0173A8D4DFFA4426E8186F01E996` (verified = BRIEF.md)  
**OT-SEAL:** YES  
**Lane:** `gpt-6-sol` x1 via `codex exec` / `run-codex-review.ps1` (EXIT=DONE; ~147k tokens)  
**rule:** DONE means do not relaunch this job_id; next = Orq GO under G-CAL constraints (see READY-NEXT.md)

| id | kind | pid | out | state |
| --- | --- | ---: | --- | --- |
| sol-decide | codex gpt-6-sol | 50936 | yes | COMPLETE 43/43 |

## Artifacts (JobDir first)

| Path | What |
|---|---|
| `decide.jsonl` | **43/43** authoritative decide rows (+ vs_flash ACCEPT\|OVERRIDE) |
| `RESUMEN.md` | counts by clase; X-YA_RESUELTO / C3 lists; note 296b |
| `lanes\codex\PROMPT.md` | batch decide prompt |
| `lanes\codex\REVIEW.md` | DECIDE_OK + override table |
| `lanes\codex\out.md` | short coverage |
| `lanes\codex\EXIT` | DONE |
| `inputs\propose.jsonl` | Flash 43/43 (inherited) |
| `inputs\prescore-compact.json` | PRESCORE senales |
| `READY-NEXT.md` | G-CAL still blocks fix/resolve |

## Optional mirror

`P:\DayZ_MCP_dev\reviews\triage-20260925-prescore-flash\decide.jsonl` (+ `RESUMEN-decide.md`)

## Coverage / tally

- **43/43** decide rows; 0 skipped; ids match Flash propose set
- `vs_flash`: **33 ACCEPT / 10 OVERRIDE**
- `confianza=DUDOSA` on 43/43 (G-CAL red)
- clase: X=25, C1=0, C2=3, C3=15, C4=0
- disposicion: X-REROUTE=20, CAMBIO=13, EVIDENCIA=5, X-YA_RESUELTO=2, X-ATERRIZAR=1, X-DUPLICADO=1, DESCARTE=1
- 296b: OVERRIDE C3/X-YA_RESUELTO -> **X/X-YA_RESUELTO** (normalized; mailbox still open)
- 678b: OVERRIDE C3/CAMBIO -> X/X-YA_RESUELTO (candidate via #90/#91; not closed)

## Mutations

- **Zero** durable mailbox mutations
- **Zero** `triage.jsonl` writes
- **Zero** edits to root `GATES.md` v9 / `gates\inbox-*`
- **Zero** `pipeline_resolve` / PR / Opus / Cursor CA
- Write-set = JobDir artifacts + optional reviews mirror only

## POST
### Hecho
- OT-SEAL YES; BRIEF.sha256 verified; checkout `78cc50a` confirmed on Willy
- Flash propose 43/43 ingested; PRESCORE/READY-FOR-SOL read
- `gpt-6-sol` decide via `run-codex-review.ps1` / `codex exec` -> `decide.jsonl` 43/43 + `RESUMEN.md`
- INDEX + READY-NEXT written; optional reviews mirror created
- G-CAL still not durable-green => **no fix / Opus / pipeline_resolve**

### Blockers
1. G-CAL not durable-green (blocks despacho / pipeline_resolve / Opus implement)
2. `triage.jsonl` / TRIAGE-SCHEMA still absent (no lateral register append)
3. Mailbox still open for resolve-candidates `296b` / `678b` until owner GO after G-CAL

### LL-candidatos
1. Sol used `senal_que_decide=D1` on several X-REROUTE where D1=0 (key present, value zero); Flash preferred D7/D5. Under DUDOSA + G-CAL red this is non-blocking; future decide prompt may require truthy signal value.
2. Codex ephemeral cwd saw OneDrive DayZ_MCP_dev but not `P:` mount; HEAD still `78cc50a`.

### Skills canonicas
- (none)

### LIVE-STATE
- decide artifact complete; **G-CAL still blocks** fix/resolve/Opus
- unresolved_total still **43**; tip `78cc50a`; next = Orq may remeasure G-CAL / owner GO resolve 296b (see READY-NEXT)

timeout_sec: codex ~6 min (11:00:19 -> 11:06:40 Madrid)  
0 Cursor CA. Grok packaging only (not triador).