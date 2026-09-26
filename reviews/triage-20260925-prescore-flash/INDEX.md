# INDEX — mcp-triaje-prescore-flash-20260925

**Status:** COMPLETE (propose-only; NO dispatch)  
**Machine:** Willy `608a9767-5c78-4986-b0cd-762eb2f79a0b`  
**Finished:** 2026-09-25 10:57:51 Europe/Madrid (UTC+2)  
**run_id:** `20260925-prescore-flash`  
**Checkout:** `P:\DayZ_MCP_dev` @ `78cc50a` (= origin/main, #91)  
**BRIEF.sha256:** D24898F5D6BE353904D1CC9A01BCC2EDDD8C73E244FC00AD028500DA74907E89 (verified)  
**OT-SEAL:** YES  
**rule:** DONE means do not relaunch this job_id; next = Sol/Astra decide hop (new JobDir)

| id | kind | pid | out | state |
| --- | --- | ---: | --- | --- |
| prescore | script §3 | - | yes | COMPLETE |
| flash-propose | agy gemini-3.8-flash-low | - | yes | COMPLETE 43/43 |

## Artifacts (JobDir first)

| Path | What |
|---|---|
| `live-inbox.json` | live `read_inbox` dump (43; snap==live) |
| `prescore.json` | full PRESCORE + bodies |
| `prescore-compact.json` | Flash input |
| `propose.jsonl` | **43/43** Flash propose rows (+ senales attached) |
| `run_prescore.py` | mechanical scorer used |
| `agy-ws/` | Flash workspace (BRIEF + propose source) |
| `agy-propose2.raw.json` | agy meta (SUCCESS; attempt1 denied RunCommand; attempt2 file-tool OK) |
| `READY-FOR-SOL.md` | handoff |

## Optional mirror

`P:\DayZ_MCP_dev\reviews\triage-20260925-prescore-flash\` → `propose.jsonl`, `prescore.json`, `prescore-compact.json`, `FLASH-BRIEF.md`, `agy-meta.json`

## Coverage

- **43/43** propose rows; 0 skipped; 0 parse errors
- Every row: `senal_que_decide` ∈ {D1..D7}; `confianza=DUDOSA`
- clase tally (Flash): C3=17, X=16, C2=10
- disposicion tally: CAMBIO=16, X-REROUTE=13, EVIDENCIA=10, X-YA_RESUELTO=1, X-ATERRIZAR=1, X-DUPLICADO=1, DESCARTE=1

## Mutations

- **Zero** durable mailbox mutations (`feedback.jsonl` mtime unchanged 2026-09-25 01:55 Madrid)
- **Zero** `triage.jsonl` writes (still ABSENT)
- **Zero** edits to root `GATES.md` v9 / `gates\inbox-*`
- **Zero** `pipeline_resolve` / PR / Opus / Cursor CA
- dayz-mcp MCP temporarily disabled for agy tool-schema, **re-enabled** after

## agy notes

- attempt1: denied `RunCommand` (accept-edits); no file
- attempt2: file-tool write OK — 60s; in≈407k out≈8.6k; status SUCCESS; 0 denied
- model: `gemini-3.8-flash-low` via `agy` (never gemini npm)

## POST
### Hecho
- OT-SEAL YES; BRIEF.sha256 verified; checkout `78cc50a` confirmed
- Live inbox re-read 43/43 ≡ snapshot
- PRESCORE mecánico §3 over 43 (signals + `clase_mecanica` candidata only)
- Flash propose lote via `agy --model gemini-3.8-flash-low` → `propose.jsonl` 43/43
- INDEX + READY-FOR-SOL written; optional reviews mirror created
- G-CAL still not durable-green → **no fix dispatch**

### Blockers
1. G-CAL not durable-green (blocks despacho / pipeline_resolve / Opus implement)
2. `triage.jsonl` / TRIAGE-SCHEMA still absent (no lateral register append)
3. Flash row `296b`: clase=C3 + disposicion=X-YA_RESUELTO (inconsistent pairing — Sol should normalize to clase=X if accepting YA_RESUELTO)

### LL-candidatos
1. agy Flash under accept-edits may prefer RunCommand; brief must ban shell and require file tool (attempt1 fail → attempt2 OK)
2. agy + enabled dayz-mcp MCP can 400 on Gemini tool schemas — disable MCP for propose-only turns

### Skills canónicas
- (none)

### LIVE-STATE
- ready for **Sol/Astra decide**; G-CAL still not durable-green → **no fix dispatch**
- unresolved_total still **43**; tip `78cc50a`; propose artifact only

timeout_sec: agy attempt2 ~60s; prescore ~8s
0 Cursor CA. Grok not triador.
