# gpt-6.1-sol review, g5r9_journal, round 3 (unedited)

VERDICT: APPROVED

## FINDINGS

No remaining P1/P2 finding or executable production failure identified.

**F1 — P3 — F4 regression uses a different interleaving.**  
Locations: `tools/tests/test_g5_r9_journal.py:412`, `:438`.

The test crashes alpha **before** suspending beta, then checks that alpha’s retry gets `registration_busy`. The requested scenario suspends beta first, then attempts alpha’s crashing transaction. The added test verifies journal preservation, and the separate zero-provider-call contention test covers exclusion, but the exact requested chronology is missing. This is a coverage finding; no production failure was demonstrated.

## SPEC COVERAGE

| Specification bullet | Status | Evidence in this tree |
|---|---|---|
| A1: completed original and interrupted restore states | **done, preserved** | `tools/dayz_mcp/host_config.py:1148`; file matches round 2. |
| A2: valid prepared `manifest.next`; prove unchanged files before discard | **done, preserved** | `tools/dayz_mcp/host_config.py:1192`; file matches round 2. |
| A3: journal before provider mutation; next-run recovery | **done** | `tools/install_mcp.py:1523`, `:1547`, `:1566`. |
| PowerShell failed-Codex-add rollback seam | **done, preserved** | `tools/install-mcp.ps1:60`, `:946`; unchanged from round 2. |
| F4: one lock before discovery, held through recovery, mutations, timeouts and cleanup | **done** | `tools/install_mcp.py:1477`, `:1489`, `:1506`, `:1586`, `:1590`. |
| Sibling lock file, no `FILE_SHARE_DELETE`, explicit owner token | **done** | `tools/install_mcp.py:1179`, `:1204`, `:1234`. |
| Bounded polling, default 120 seconds; busy contender reaches no providers/journals | **done** | `tools/install_mcp.py:1246`, `:1460`; test at `tools/tests/test_g5_r9_journal.py:528`. |
| F1: keyed registration journal; corruption-only owner mismatch | **done** | `tools/install_mcp.py:1505`, `:1512`; isolation regression at `tools/tests/test_g5_r9_journal.py:472`. |
| Shared host-config journal remains shared | **done** | `tools/dayz_mcp/host_config.py:911`; protected by the enclosing registration lock. |
| Regression 1: exact original F4 ordering | **missing** | Equivalent exclusion/preservation checks exist; see P3 above. |
| Regressions 2–4: alpha preservation/recovery, zero-call timeout, deletion resistance | **done** | `tools/tests/test_g5_r9_journal.py:472`, `:528`, `:573`. |
| PowerShell remains unserialised and documented as residual | **done** | `REPORT.md:53`; PowerShell source unchanged. |

## GATE GAP

The contention tests use threads rather than separate processes. They would not detect a lock implementation that worked only within one process. They also do not contend specifically during host-timeout writes or cleanup; those boundaries are covered by source inspection.

## PREMISE

The bounded scope and treatment of unchanged baseline failures are sound. The supplied orchestrator output reports **two failures, zero new**; `REPORT.md:32` and `:59` report zero failures. I used the supplied orchestrator measurement.

## NOT VERIFIED

- Filesystem regressions could not run locally: the read-only sandbox rejected temporary-directory writes.
- The pure restore-classifier test passed. The PowerShell rollback seam passed with process-local execution-policy bypass.
- Python 3.11, separate-process contention, and regression failures against round 2 were not independently rerun.
- No real registrations, installers, daemon, DayZ or MCP tools were run. No files modified or commit created.