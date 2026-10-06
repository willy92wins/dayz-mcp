# REPORT — inbox 5aa8 frame cap visibility

## CHANGES

- `tools/mcp_capture.py:FRAME_LIMIT` / `grab_stable_frame`: the grab stays `max(1, min(requested, 5))`. Evidence gains `requested_frames`, `effective_frames`, `frame_limit=5`, and `limit_reason="frame_limit"` only when the request is above 5. Not a backend error.
- `tools/mcp_capture.py:capture_dual`: published `meta` gets warning `frames_capped` when that evidence says the grab was cut. Fields travel in `meta.frame_stale_detail`.
- `tools/mcp_capture.py:capture_screenshot`: the legacy entry publishes the same detail and the same warning on `meta`.
- `tools/dayz_mcp/server.py:capture_screenshot`: tool description states the cap of 5 and the warning.
- `CHANGELOG.md`: one line under Unreleased / Changed.

## TESTS ADDED

- `tools/tests/test_capture_frame_limit.py`
- `test_twelve_frames_grab_five_and_warn` — fails on unmodified code: 12 requests issued 5 backend calls and the evidence had no `requested_frames` and no `frames_capped` warning.
- `test_four_frames_are_not_capped`
- `test_legacy_entry_publishes_the_same_cap`
- `test_frame_selection_is_unchanged_by_the_cap`
- `test_tool_description_states_the_frame_limit` — the description pin is new; no existing pin had to be rewritten.

## GATE OUTPUT

```
--- tests.test_suite_structure (whole-suite ratchets): rc=0
--- tests.test_capture_frame_limit: rc=0
Ran 5 tests in 2.301s

OK
--- tests.test_capture_frame_limit on Python 3.11: rc=0 OK
--- fast tier: ran=5828 baseline_ran=5587 failures=0 new=0
GAUNTLET_GATE: PASS
```

## DEVIATIONS

- The binding spec names `tools/mcp_capture.py`. That file lives at `tools/mcp_capture.py`, outside `tools/dayz_mcp/`. Edited there because that is the implementation. `product-spec.md` was not edited: the spec names no C*/H*/D* row.

## NOT VERIFIED

- In-game capture. No DayZ process was started.
- The gate printed 7 lines; all of them are pasted above. `ran=5828` is the fast-tier count the gate reported.
