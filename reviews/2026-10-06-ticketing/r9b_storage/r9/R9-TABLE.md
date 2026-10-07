# r9b (storage rotation recovery): DZ-R9 step-6 re-audit table, 2026-10-07

Change: branch `claude/fix-c5ac-storage-rotation-recovery` (rotation recovery after gpt-6.1-sol's state machine,
approved in review round 4). Step 6 = mechanical re-check plus the two most-changed angles.

- Mechanical re-check: gpt-6.1-sol, `MECHANICAL.md` (M1-M4).
- Angle D, persistence and atomic flow on a real disk: GLM-5.3-Flash on the GX10, `D/run/ws/AUDIT.md` (D1-D3).
- Angle E, recovery paths, refusals and the operator: GLM-5.3-Flash, `E/run/ws/AUDIT.md` (E1-E3).
- Independent verifier (step 3b): a fresh gpt-6.1-sol session given the bare claims only, `VERIFY.md` (C1-C7).
- Orchestrator self-sample: M1 and M2, run (`verify_m2.py` and a mocked listing); 2 of 10, both real.

| ID | Severity | Title | Location | Found by | Verified by | Defect / improvement | Status |
|---|---|---|---|---|---|---|---|
| M1 | P1 | Case-variant journal name ignored; seal_only can overwrite the original marker bytes | dayz_test_storage.py:1010 | mechanical | orchestrator (run) | defect | fix round |
| M2 | P2 | A non-directory storage_1 is classified as absent and the launch is allowed | :1154, :227 | mechanical | orchestrator (temp dir) | defect | fix round |
| M3 / C1 | P2 | Replay misses a rotation completed with a reseal A to X; retry records storage_rotated=False | :943-945; process_lifecycle.py:165-168 | mechanical | verifier (scenario) | defect | fix round |
| D1 / C2 | P2 | A second recovery generation reuses the derived txid: persistent backup_name_collision | :1151, :1099, :1059-1062 | auditor D (real dirs) | verifier (3 retries) | defect | fix round |
| D2 / C3 | P3 | seal_only onto a directory at the marker name raises; storage_rotate_failed every launch | :582-583, :599-604 | auditor D | verifier | defect | fix round |
| D3 / C4 | P3 | RecursionError from a nested 60 KB journal or marker escapes prepare_storage | :285-291 | auditor D | verifier (Python 3.14.3) | defect | fix round |
| E2 / C6 | P3 | The caller only sees storage_recovery_required; the precise reason and the hint stay in the daemon | process_lifecycle.py:3329-3341 | auditor E | verifier (partly) | defect (operability) | fix round |
| E1+E3 / C5+C7 | P3 | No shipped remedy text for refusals after a manual change or a stray rotation.* file | :651-657, :1014-1015 | auditor E | verifier (partly: reverting recovers; only some names block) | defect (documentation) | fix round |
| M4 | P3 | Journal names assembled in several places | several | mechanical | - | improvement | not acted |

Refuted parts: C5 "journal removal is necessary" (restoring the layout recovers); C6 "the public hint mispromises"
(the worker drops the hint); C7 "any non-grammar name blocks" (only some do).
