# gpt-6.1-sol review, g1_ttl_file, round 4 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F5 — P2 — The supervisor’s final ownership probe can still return a stale lease ID.**

Anchors: `tools/dayz_mcp/mcp_supervisor.py:628`, `:632`, `:641`; `tools/dayz_mcp/server.py:4363`; `tools/dayz_mcp/lease_result_ttl.py:205`.

The local probe snapshots `active_lease_id` into its result. Before delivering that result, the normal result decorator awaits another authoritative status observation. Releasing the lease during that await leaves the probe’s `local_lease_id` unchanged. The supervisor accepts that stale ID as its final ownership recheck.

**Executed scenario**, using Python 3.11, the real `build_app` protocol handlers and supervisor `_reply`, with in-memory control/transport substitutes:

1. Worker holds lease A.
2. Supervisor’s first local probe returns A.
3. Authoritative observation returns active self=A, owner=A, TTL=33.
4. Supervisor’s final local probe snapshots A.
5. During that probe’s result-decoration status await, release the worker’s local lease and return the previously observed status.
6. Complete `_reply(9, {"error":"synthetic"}, is_error=True)`.

Actual output:

```json
{
  "status_calls": 4,
  "actual_local_lease": null,
  "annotation": {"lease_ttl_s": 33.0},
  "visible": "lease_ttl_s=33.000"
}
```

Expected: unknown annotation, never numeric TTL after the detected ownership change.

**Suggested fix:** obtain ownership evidence together with the authoritative observation and recheck it at the final worker response boundary, after asynchronous result wrappers. Add a regression exercising the real wrapped internal probe; the current fake worker answers local probes immediately and misses this window.

## SPEC COVERAGE

Coverage is limited to the binding round-4 closure directive.

| Requirement | Status | Evidence |
|---|---|---|
| F3: bound every policy/profile lookup and final validation by the original deadline | **Done** | Lookups use `_thread_bounded`; final validation checks the deadline at `server.py:3137`. Executed reproduction returned `satisfied=false`, `timed_out=true`, elapsed 0.375s for a 0.3s deadline. |
| F3: cooperative file reads with bounded chunks, stop signal and own deadline | **Done** | `log_tail.py:30`, `server.py:2798`. |
| F3: reject late/cancelled results; bounded join; report surviving reader | **Done** | `server.py:2859`, `:2866`, `:2923`. In-memory deadline and cancellation checks both ended with the cooperative reader no longer alive. |
| F3: add cooperative blocking regression | **Done** | `test_lease_ttl_and_file_wait.py:831`. Its claimed failure against round 3 was not independently rerun. |
| F5: match authoritative ownership against current generation-local ownership, observed atomically and rechecked | **Wrong** | Three separate probes still leave the executed final-probe race above. |
| F5: mismatches, changes or missing evidence produce unknown | **Wrong** | Numeric TTL survives the ownership change in F5. |
| F5: correct the test that expected B’s TTL while locally holding A | **Done** | `test_lease_ttl_and_file_wait.py:950`; passed independently. |
| F5: add release-during-observation regression | **Done, incomplete coverage** | `test_lease_ttl_and_file_wait.py:979`; passed independently but bypasses worker result wrappers. |
| F10: recheck exact held lease after the last await and before success | **Done** | `server.py:3142`. Executed release during final lifecycle status raised `lease_expired`. |
| F10: add regression for that release window | **Done** | `test_lease_ttl_and_file_wait.py:903`. |
| Report maps F3/F5/F10 changes and tests | **Done** | `REPORT.md:65`. |

## GATE GAP

The supervisor tests use a fake worker that answers local ownership probes synchronously (`test_lease_ttl_and_file_wait.py:349`). Production probes pass through asynchronous TTL and freshness wrappers. Consequently, both new F5 tests pass while the wrapped-probe reproduction still emits stale numeric TTL.

## PREMISE

The round-4 scope is clear. The report’s assumption that three fresh probes establish current ownership is incorrect: freshness at the probe body does not establish freshness after its response wrappers finish.

## NOT VERIFIED

- The full focused module could not complete under the read-only sandbox: 13 tests errored because temporary directories could not be created. These are environment errors, not demonstrated implementation failures.
- Both selected F5 tests passed: **2 tests, OK**.
- F3 and F10 were additionally checked with in-memory reproductions.
- No independent round-3 regression run, full fast-tier rerun, live daemon, or in-game validation.
- Earlier specification requirements outside F3/F5/F10 were not reopened.
- No files were modified.

