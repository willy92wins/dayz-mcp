# r9c: the r9b DZ-R9 step-6 findings (mission storage rotation recovery)

## Origin
r9b's step-6 re-audit (`../r9b_storage/r9/R9-TABLE.md`) found nine items in the rotation recovery, most of them already on main before r9b:
- M1 (P1): a case-variant journal name is ignored.
- M2 (P2): a non-directory `storage_1` counts as absent.
- M3 (P2): replay misses a crash-reseal.
- D1 (P2): retained abort journals block every retry of a launch.
- D2 (P3): a marker that is a directory raises an exception.
- D3 (P3): hostile or oversized JSON is not contained.
- E2 (P3): the caller is not told the precise refusal.
- E1 and E3 (P3): there is no operator procedure.

gpt-6.1-sol wrote the binding fix specification (`../r9b_storage/r9/FIX_SPEC.md`). r9b's sixth round (Grok) delivered the eight production changes but not the required tests. The owner then decided (2026-10-07) to merge r9b's approved round 4 (#221) and move these findings to this separate batch, starting from those production changes (`SPEC_R9C.md`).

## Loop (`loop/`)
The rounds are numbered 2 to 4: the batch was seeded on main with r9b's round-6 production changes, and its first round ran as round 2 to keep that tree.

All rounds ran a fresh Grok 4.7 session, and gpt-6.1-sol reviewed each one with in-memory mutations. Production did not change in any round; the reviewer confirmed every positive control.

| Round | Work | Gate | Review |
|---|---|---|---|
| 2 | Regression tests, oracle extensions and the E2 envelope migration | Pass | CHANGES_REQUIRED (2 P2) |
| 3 | The closure equivalence and the lifecycle replay measurement | Pass | CHANGES_REQUIRED (4 P2, test strength) |
| 4 | Artifact kinds in both deduplication keys, real same-caller deaths, corrupted M/K fed to recovery, E2 through the real worker start and launcher serialization | Pass | **APPROVED** |

Round 4 was the orchestrator's last round, granted at the third review. Each new test fails against the mutation that showed its gap. Only optional P3 coverage notes remain.

## Not done here (deployment)
- **Launcher reseal:** `dayz_test_storage.py`, `dayz_test_worker.py` and the launcher's `app_main.py` changed. One reseal covers g5fix (#220), r9b (#221) and this batch.
- **Real disk and game:** NTFS behaviour and an in-game refusal path.
