# Mechanical re-check: DayZ-MCP mission storage rotation recovery (r9b), DZ-R9 step 6

## Task
The change in `_audit_context/R9B.diff` (context: `_audit_context/CONTEXT.md`, design:
`_audit_context/SPEC_R9B_IMPL.md`) implements your state machine of the rotation transaction. You approved it in
round 4 of its review. This is not another review of the design: run the deterministic pre-checks of a rigorous
data audit over the code as it is now in this directory, list what each finds, and stop.

## The checks (grep and table walks; cite `path:line` for every row)
1. Path-naming matrix: every helper and literal that builds a name or path of the world (`storage_1`), the reserved
   backups, the canonical and reserved markers, the journal and its completed copy. Rows: artefact x producer,
   recovery, cleanup, reader. Any artefact named two different ways, or built outside its helper, is a finding.
2. Cleanup symmetry: for every file or directory the producer or the recovery creates, the place that removes,
   renames or retains it, on success, on refusal and after each crash cut. An artefact that nothing ever retires and
   that a later run reads as live is a finding.
3. Entry points: every caller of the rotation and recovery functions (`prepare_storage` and anything else that
   reads or writes these artefacts, including `process_lifecycle.py` and `dayz_test_tool.py`), and whether each
   goes through the validation-before-mutation gate.
4. Flag and phase lifecycle: every phase value and flag the journal carries, where each is written, read and
   cleared, and whether the state stores (journal, markers, world, backups, run record, observation log) move
   together.
5. State-machine matrix: the reachable states the code accepts versus the states your design admits; any accepted
   state outside the design, or a design state with no code path, is a finding.

## Answer
First line exactly one of `VERDICT: CLEAN` or `VERDICT: FINDINGS`. Then `## MECHANICAL` with one table per
check, then `## FINDINGS` (id M1..., P0-P3, `path:line`, an executable scenario), then `## NOT VERIFIED`.
Read-only. Do not start DayZ, daemons or MCP tools, and do not touch `%LOCALAPPDATA%`. Answer in English.
