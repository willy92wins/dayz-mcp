# fb-20260909-190503-ba0e — playbook sequence (PROPOSAL)

- ticket: `fb-20260909-190503-ba0e`
- author: Auditor (Grok Bot)
- status: **PROPOSED** pending Guillermo OK + Codex review
- do not merge, do not resolve the inbox ticket, do not edit GATES.md / Obsidian

## What

New DRAFT playbook `lease_spawn_prepare_trace` under `playbooks/` (TOML + fixtures +
dictionary row). It encodes the known path **lease → spawn → prepare → trace** as four
ordered observable gates. `playbook_run(name="lease_spawn_prepare_trace")` discovers it
by glob; `runner.py` / `playbook_tool.py` are unchanged.

STOP reasons name the skipped stage:

| step | stage | tool | STOP reason |
| --- | --- | --- | --- |
| S1 | lease | `session_status` | `lease_not_held` |
| S2 | spawn | `object_inspect` | `spawn_missing` |
| S3 | prepare | `vehicle_telemetry` | `fixture_not_ready` |
| S4 | trace-ready | `vehicle_telemetry` | `trace_not_ready` |

Mutating / lifecycle verbs stay **outside** the playbook (denied or not observational):
`session_acquire_wait` → `world_spawn` → `vehicle_prepare_fixture` → `vehicle_trace`.
A PASS does not start a trace.

## Why

Agent friction is sequence and preconditions, not tool-menu discovery. Weak agents
already see `playbook_run` and the leaf checklists (`box_is_mine`, `place_safely`,
`run_really_started`). They still skip or reorder lease / spawn / prepare / trace.
One named path with one STOP reason per stage is the cheapest way to make the order
a known, fixture-tested checklist.

## Out of scope (this proposal)

- No `runner.py` / adapter wiring (dictionary is `playbooks/*.toml`).
- No in-game / Enforce / PBO / Workbench / Steam / filePatching.
- No Vaciado / Reserva tickets, no live Claude write-sets.
- No invented `triage.jsonl` / leases.
- Does not replace `box_is_mine` or `place_safely`.
