# Fix specification: DZ-R9 step-5 loop-back for the storage rotation recovery (r9b)

## Task
The current directory is the r9b tree you approved in round 4 (rotation recovery after your state machine,
`_audit_context/SPEC_R9B_IMPL.md`). The DZ-R9 step-6 re-audit found the defects below. Each was verified: M1 and M2
by the orchestrator with a run, the rest by an independent gpt-6.1-sol session given only the bare claims
(`../VERIFY.md` is not in this tree; its verdicts are summarised here). Write the binding specification of ONE fix
round (the minimum verified patch: no redesign of what is not listed), with a regression test for each item that
fails on this tree and passes after. You may run Python on in-memory or temporary-directory scenarios.

## Verified defects (severity assigned by the orchestrator)
1. M1, P1. `_active_journals` (tools/dayz_mcp/dayz_test_storage.py:1010) filters with a case-sensitive
   `name.startswith(JOURNAL_PREFIX)`. Verified: `listdir` returning `JOURNAL_PREFIX.upper() + "1"*32 + JOURNAL_SUFFIX`
   gives `([], False)`; the canonical name is found. Consequence (mechanical re-check): with S2 (W absent, D holding
   O, M holding the original bytes E, K absent, a valid schema-2 journal under an upper-cased name), prepare_storage
   takes seal_only and replaces E with X; E is never preserved. The reserved-name rule already refuses Win32
   aliases (case, trailing periods or spaces) for the storage, marker and backup names; the journal family must
   follow it.
2. M2, P2. Ordinary classification equates a non-directory `storage_1` with absence (`:1154`, `:227`). Verified with
   a temporary directory: a regular FILE named storage_1, no marker -> launch_allowed=True, reason storage_absent, a
   marker published, the file left in place (control: a directory rotates, reason seal_changed).
3. M3 (C1), P2. After a recovery reseals A to X (completed journal keeps new_seal=A, marker published with X,
   `:943-945`), `_pending_completed_rotation(mission, X)` skips it (process_lifecycle.py:165-168), so a retried
   launch for X records storage_rotated=False although the reset happened (verified by scenario).
4. D1 (C2), P2. A recovery that aborts a prepared journal re-rotates with `derived_txid(txid)` (`:1151`, `:1099`).
   If that rotation is killed before the world moves, the next recovery aborts it and reuses the same derived id,
   whose completed journal exists: `backup_name_collision` (`:1059-1062`); later retries without an active journal
   use the original id, whose completed journal also exists. Persistent refusal for that launch identity (verified:
   three retries).
5. D2 (C3), P3. seal_only with a DIRECTORY at the marker name: `_publish_marker`'s `os.replace` raises (`:582-583`,
   `:599-604`, `:1169-1170`), mapped to storage_rotate_failed on every launch (verified); the rotate branch already
   answers `marker_type_unsupported` for the same object (`:1039-1041`).
6. D3 (C4), P3. `_parse_json` (`:285-291`) does not catch RecursionError: a 60,001-byte nested array in a journal
   or a marker makes prepare_storage raise RecursionError on every launch (verified on Python 3.14.3). The read
   before the 64 KiB cap is unbounded (`:280-282`).
7. E2 (C6), P3. Every recovery refusal reaches the dayz_test_run caller only as `storage_recovery_required`; the
   precise reason (journal_state_impossible, journal_unreadable, journal_name_invalid, journal_ambiguous, ...) is
   only in the daemon audit row, and the worker drops the lifecycle hint before the public result (verified).
8. E1 + E3 (C5, C7), P3. No shipped text tells the operator how to get out of a refusal: an operator who renames,
   copies or deletes the backup after a crash in storage_moved or marker_published gets journal_state_impossible on
   every launch until the layout is restored (verified: restoring it recovers); some stray
   `storage_1.modset.rotation.*` names (for example a `.json.bak` copy) block every launch with
   journal_name_invalid until removed (verified for .json.bak; `...garbage.completed.json` is ignored).
Not in scope: M4 (journal names assembled in several places: an improvement, no defect) and your round-4 P3s on the
test oracle unless a fix above needs them.

## What to answer
1. First line exactly `SPEC: READY` or `SPEC: NEEDS_OWNER`.
2. `## IMPLEMENTATION SPEC`: per item, the change (files, functions, behaviour), its regression test (state, call,
   expected result, and that it fails on this tree), and how the model-checking oracle must extend to cover it
   (for M3 and D1 especially: the derived-transaction id rule and the completed-journal record that replay reads).
   Mark the data-critical parts. Say whether `tools/packaged-modules.lock.json` must be regenerated.
3. `## OWNER QUESTIONS` (max 2), only if a choice is the owner's.
4. `## NOT VERIFIED`.
Read-only. Do not start DayZ, daemons or MCP tools; do not touch `%LOCALAPPDATA%` or real missions. Answer in English.
