## CHANGES

- `tools/dayz_mcp/server.py:execute_wait_for` (`_probe_timeout_at_deadline`): a recognised probe timeout (`timeout waiting for`) at or after the global deadline returns `_wait_for_response` with `ok=true`, `satisfied=false`, `timed_out=true`, `last_error=probe_timeout` for `players_at_least`, `players_at_most` and `entity_state`. Before the deadline the previous probe error is kept (players abort text; entity re-raises the original message). Ownership, version, authentication and ambiguous-response errors do not match the prefix and are never rewritten.
- `tools/tests/test_runloss_diagnostics.py:RunlossWaitBudgetTest.test_probe_that_exhausts_global_budget_still_reports_timeout`: pin updated from the `wait_for timed out` tool error to the structured result. The early-abort pins are unchanged.
- `CHANGELOG.md` Unreleased / Fixed: one line for inbox 9983.

## TESTS ADDED

- `tools/tests/test_wait_for_probe_deadline.py`
  - `ProbeDeadlineTest.test_last_probe_at_global_deadline_returns_json` — fails on unmodified code: the players path raises `ToolError("wait_for timed out … reason=probe_timeout")` and the entity path raises `ToolError("timeout waiting for telemetry_read …")` instead of the JSON result.
  - `ProbeDeadlineTest.test_probe_timeout_before_deadline_stays_an_error`
  - `ProbeDeadlineTest.test_non_probe_errors_stay_errors_after_the_deadline`
  - `ProbeDeadlineTest.test_unrelated_exception_after_deadline_is_not_swallowed`

## GATE OUTPUT

```
--- tests.test_suite_structure (whole-suite ratchets): rc=0
--- tests.test_wait_for_probe_deadline: rc=0
Ran 4 tests in 0.070s

OK
--- tests.test_wait_for_probe_deadline on Python 3.11: rc=0 OK
--- fast tier: ran=5827 baseline_ran=5587 failures=0 new=0
GAUNTLET_GATE: PASS
```

## DEVIATIONS

None. `product-spec.md` was not edited: the specification names no C*/H*/D* row. The historical literal `daemon_request_deadline_exceeded` is not claimed as reproduced.

## NOT VERIFIED

No in-game probe. `daemon_request_deadline_exceeded` was not traced to a build or endpoint, so that historical literal is not claimed as reproduced. `tools/packaged-modules.lock.json` and `addon/` were not modified; the gate checked the fast tier (5827 tests, 0 new failures) and the new module on the project venv, including Python 3.11.
