# gpt-6.1-sol review, g1_ttl_file, round 5 (unedited)

VERDICT: APPROVED

## FINDINGS

No P1/P2 findings or new implementation defects within the round 5 scope. **F5 is closed.**

The final local lease snapshot occurs after the status await, with no subsequent await before returning (`tools/dayz_mcp/lease_result_ttl.py:220–226`). Both result wrappers bypass the internal probes (`lease_result_ttl.py:280`, `server_freshness.py:253`).

## SPEC COVERAGE

| Round 5 requirement | Status | Evidence |
|---|---|---|
| Both internal probes bypass asynchronous result wrappers | done | Independently exercised both real protocol handlers; freshness work was forbidden and neither result was decorated. |
| Local ownership snapshot is the final synchronous response step | done | `tools/dayz_mcp/lease_result_ttl.py:226` |
| Probes remain hidden | done | `tools/dayz_mcp/server.py:8069–8073`; verified through `app.list_tools()`. |
| Release race returns null/unknown, never numeric TTL | done | Regression at `tools/tests/test_lease_ttl_and_file_wait.py:1081`, through real `build_app` handlers and supervisor `_reply`. |
| Regression fails on previous tree | done | Executed against round 4: fails at line 1200 because `lease_ttl_status` is absent. Passes on round 5. |
| Preserve F3, F10 and other implementation | done | Only two production files changed; `server.py`, `log_tail.py`, `launch_logs.py`, and `mcp_supervisor.py` are byte-for-byte unchanged. |
| Add `ROUND 5 FIXES` report section | done | `REPORT.md:108` |

The write-free protocol/supervisor subset passed: **11 tests, OK**.

## GATE GAP

The new regression alone does not guard the freshness bypass. An in-memory mutation disabling only that bypass still passed the regression. A future post-snapshot freshness await could therefore escape it. The current bypass was independently verified.

The test uses substituted control/transport objects rather than a real worker process and daemon.

## PREMISE

`REPORT.md:115` contains stale wording: the actual `product-spec.md` already includes the C4/H3/H4 updates at lines 68, 138 and 139. This does not affect F5 closure.

## NOT VERIFIED

- The full module could not pass locally: **13 setup errors** occurred because the read-only sandbox cannot create temporary directories. The supplied orchestrator gate remains separate evidence.
- No live daemon, real stdio worker, mixed deployment, or in-game checks.
- Earlier specification behavior was preserved, not comprehensively re-audited.
- No files modified or commit created.

