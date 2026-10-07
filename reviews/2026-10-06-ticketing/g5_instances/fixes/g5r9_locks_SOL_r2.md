# gpt-6.1-sol review, g5r9_locks, round 2 (unedited)

VERDICT: APPROVED

## FINDINGS

No blocking findings. Both previous findings are fixed:

- **Previous F1:** The daemon stamps its timeout into the sealed request at `tools/dayz_mcp/dayz_test_tool.py:464`; the worker consumes it at `tools/dayz_mcp/dayz_test_worker.py:838`. A read-only probe preserved `0.3` through canonicalization with the environment variable removed, then returned `build_busy` after **0.31 s**.
- **Previous F2:** Acquisition yields between polls at `tools/dayz_mcp/server_cli.py:331`, releases resources through exception cleanup and `finally`, and checks cancellation before dispatch at `tools/dayz_mcp/dayz_test_worker.py:841`. Mocked partial-acquisition probes handled event cancellation and task cancellation within **0.061 s**, leaving no held descriptors and emitting **zero broker frames**.

## SPEC COVERAGE

| Requirement | Status | Evidence |
|---|---|---|
| A6: all five lock kinds refuse delete sharing | **done** | Common opener uses read/write sharing only: `server_cli.py:133`. Used by root writer, admission, election, migration and build locks. |
| A6: retained owner object, identity comparison | **done** | `instance_context.py:177`, `:185`, `:227`. |
| A6: release leases before temporary-root cleanup; deletion regression | **done** | Reviewed teardown changes; regression at `tests/test_g5_r9_locks.py:110`. Real filesystem result supplied by orchestrator. |
| A7: refuse relative shared roots by name | **done** | `server_cli.py:163`; admission delegates at `box_admission.py:26`. Read-only probes rejected relative, drive-relative and rooted-without-drive paths. |
| B2: configurable bounded wait using `LK_NBLCK`; named worker timeout | **done** | `server_cli.py:90`, `:216`, `:328`; `dayz_test_worker.py:855`. |
| B2: regenerated sealed-module lock; 15-second holder and short-limit regressions | **done** | Hash check returned `packaged modules lock ok`; orchestrator reports nine passing tests on both Python versions. |
| B3: settled receipt still scans current writer classifier; clients and accredited other roots remain allowed | **done** | `identity_migration.py:1598`; corresponding regression cases reviewed. |

Paths above are under `tools/`.

## GATE GAP

The new transport regression invokes the worker directly with a manually stripped environment; it does not execute the sealed native launcher. A launcher-specific transport or cancellation regression could therefore escape.

The tests also do not prove **real Win32 lock release after partial acquisition**, with the first resource acquired and the second contested. My mocked probes verify that cleanup path, but cannot establish OS behavior.

## PREMISE

No substantive problem. `REPORT.md` still says gate output is pending; the supplied orchestrator measurements supersede that text. The inherited release LIVE-STATE was not used as evidence for this batch.

## NOT VERIFIED

- Local `python -B -m unittest tests.test_g5_r9_locks` encountered **eight setup errors** because the sandbox provides no writable temporary directory. This is an execution limitation, not an implementation failure.
- Local request-parser suite: **17 tests passed**.
- No independent full-suite, Python 3.11, rebuilt-launcher, live-daemon or in-game run.
- No files modified; no commit or persisted handoff created.

