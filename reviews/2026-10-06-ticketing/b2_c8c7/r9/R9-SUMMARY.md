# DZ-R9 rigorous data audit: c8c7 (PR #205), 2026-10-06

Scope: the storage_1 rotation fact reported to the caller (run record, durable observation log, lifecycle
status, public tool result) and the rotation journals/backups it reads.

## Steps
1. Mechanical pre-checks (orchestrator): writer/reader census of `storage_rotated`/`storage_backup`/
   `storage_reset_notice`, the observation list and the rotation journals. Mechanical finding M1 (reject-all
   loader for `storage_observations`).
2. Angle auditors (GLM-5.3-Flash on the GX10, fresh contexts): A = persistence, recovery, input bounds;
   B = state machine and cross-actor correlation. Reports: `A/AUDIT.md`, `B/AUDIT.md`.
3. Adversarial verification: independent verifier gpt-6.1-sol on a clean tree, claims only, two verdicts
   each (`V/VERIFY.md`: C1-C8; `V/VERIFY2.md`: D1-D5). Orchestrator re-read of every cited line used in the
   fix specification (self-sample > 20%).
4. Implementer-grade cross-actor pass (GLM, fresh context): `C/AUDIT.md`.
5. Fixes: batch r9fix_c8c7 (spec `R9_C8C7.md`; GLM round 1, Grok consolidation round 2; gpt-6.1-sol review).
6. Re-audit subset: pending (after the storage-recovery batch).
7. In-game: pending (in-game cycle of 2026-10-06).

## Deduped table
| ID | Sev | Title | File:Lines | Found by | Verified by | Kind | Status |
|---|---|---|---|---|---|---|---|
| F1 | P2 | Refused storage classification recorded as a measured `false` (row, observation log, public result) | process_lifecycle.py:3024-3026, :3055-3058; dayz_test_storage.py:349-352 | A1, B1, C2 | V C1-C4 (CONFIRMED/HOLDS) | defect | fixed in r9fix |
| F2 | P2 | One malformed or over-bound `storage_observations` entry invalidates the whole runs.json | process_lifecycle.py:228-262 | M1, A (severity note), C4 | V C6 HOLDS (C7's "empty manifest" consequence does not hold as stated) | defect | fixed in r9fix (tolerant per entry, duplicates dropped, newest 32) |
| F3 | P3 | Reattach and stop republish the creation-time rotation, against the per-call contract | dayz_test_tool.py:1805-1813 with :1544-1545 | B2 | V C8 HOLDS | defect | fixed in r9fix (calls on an existing run report null) |
| S1 | P1 | Crashed-rotation recovery publishes the journal's seal and skips classification for the current seal: later same-seal launch reuses a foreign-modset world | dayz_test_storage.py:493-500, :639-644, :222-223 | C1 | V D1-D3 (CONFIRMED/HOLDS) | defect, pre-existing on main | batch storage-recovery; inbox fb-20261006-145731-c5ac |
| S2 | P3 | Durable observation log cannot bind an attempt (no `launch_operation_id`) | dayz_test_tool.py:1982-1984, :1911-1917; process_lifecycle.py:1819-1840 | C3 | V D4 HOLDS | defect | batch storage-recovery |
| S3 | P3 | Active journal backup names not checked as plain names: marker can be moved outside the mission | dayz_test_storage.py:406-409, :445-446, :453-456 | C5 | V D5 HOLDS | defect, pre-existing | batch storage-recovery |
| B-1 | P3 | `_persist_locked` rollback when both the checkpoint and the rollback write fail: memory and disk can disagree until restart | process_lifecycle.py:1803-1813 | A2 | V C5 DOES NOT HOLD as stated (the rollback write can land before it raises) | backlog | two independent I/O failures needed |
| B-2 | P3 | An unreadable completed journal is skipped, so a retry measures `false` where an earlier attempt rotated | process_lifecycle.py:138-194 | A (note) | not submitted | backlog | design question (unknown vs false) |
