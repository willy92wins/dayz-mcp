# Mechanical re-check: DayZ-MCP named-instance profile folders (g5fix), DZ-R9 step 6

## Task
The change in `_audit_context/G5FIX.diff` (context `_audit_context/CONTEXT.md`, your specification
`_audit_context/SPEC_G5FIX.md`, approved by you in round 3) is in this directory. This is not another review: run the
deterministic pre-checks of a rigorous data audit over the code as it is now, list what each finds, and stop.

## The checks (grep and table walks; cite `path:line` for every row)
1. Path-naming matrix: every place that builds or recognises a role profile folder (`profiles`, `profiles-<token>`)
   or a path under it (RPT, script log, `dayz_mcp.json`, dumps, VPP files). Rows: consumer x how it derives the folder
   (helper, recorded anchor, literal). A consumer that still builds the folder on its own, or two consumers that can
   disagree for the same run, is a finding.
2. Entry points: every caller that resolves a role folder of an existing run, and whether each goes through the
   recorded-anchor validation before reading or acting.
3. Cleanup symmetry: what the provisioning creates (the leaf, `dayz_mcp.json`, the per-launch instance id), and what
   retires or rewrites it on success, refusal, crash and the next launch.
4. Token authority: every source a token can come from in this diff; any source other than the sealed request (worker,
   VPP) or the bound context (daemon) is a finding.

## Answer
First line exactly one of `VERDICT: CLEAN` or `VERDICT: FINDINGS`. Then `## MECHANICAL` with one table per check, then
`## FINDINGS` (id M1..., P0-P3, `path:line`, an executable scenario), then `## NOT VERIFIED`. Read-only. Do not start
DayZ, daemons or MCP tools; do not touch `%LOCALAPPDATA%`. Answer in English.
