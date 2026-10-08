# gpt-6.1-sol review, g5r9_selection, round 2 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P2 — Previous F2 remains partially open: public capture results can still suppress selection errors.**

Location: [tools/mcp_capture.py:1497](C:/Users/guill/dzmcp_gauntlet/g5r9_selection/ws/tools/mcp_capture.py:1497), also `:1593`; validation is bypassed by the early return at `:896`.

**Executable scenario, verified with an in-memory backend probe:**

Reset the instance context, set `sys.argv = ["mcp", "--instance", "NOTVALID"]`, and provide an 8×8 RGB frame with pixels `(20,20,20)` and this successful backend payload:

```python
{
    "ok": True,
    "window": {},
    "client": {"left": 0, "top": 0, "width": 8, "height": 8},
    "clientStats": {"meanBrightness": 20, "nonBlackRatio": 1.0},
}
```

With no `cmdline_match`:

- `capture_screenshot(frames=1, scale=20)` returns an image without `selection_error`.
- `capture_dual(frames=1, scale=20, save_fullres=False)` returns `{inline, fullres_path, meta}` without a selection failure.

The unaccredited-window branch returns before selector validation. Both entrypoints consequently accept the invalid selector.

There is another loss on the accredited path: change `window` to `{"pid": 123}` and `clientStats` to `None`. Selector validation detects `invalid_instance_token`, but both public entrypoints return only `{isError: True, error: "frame_client_area_unverified"}`, discarding that selection error.

**Suggested fix:** validate selection independently of window accreditation and backend error handling, then consistently return the structured selection failure. Add regressions exercising the real `grab_stable_frame` flow.

## SPEC COVERAGE

| Specification bullet | Status |
|---|---|
| M2: shared entry validation before effects | **Done** |
| M3: server/client/embedded reject conflicting environment | **Done** |
| M4: doctor accepts validated selectors and selects instance | **Done** |
| M5: capture reports selection errors and prevents persistence | **Wrong** — persistence guards work for the original scenarios, but error reporting remains incomplete |
| A4: fallback audit uses actual daemon instance | **Done** — intercepted writer received `DayZ_MCP_r9inst` and the event |
| A5: common per-instance exec audit name | **Done** |
| One regression per specification bullet | **Done** — all six are present |

Previous-round status: **F1 fixed; F3 fixed; F2 partially fixed.** Both original full-resolution scenarios now return `invalid_instance_token` with zero `write_fullres` calls.

## GATE GAP

The F2 test injects an image containing a prebuilt selection-error report. It does not exercise the unaccredited-window early return or a client-statistics rejection that discards the detected error. Both defects survive the supplied green gate.

## PREMISE

“Each must fail on this tree” contradicts the intended regression criterion: tests should fail on the **unmodified base** and pass on the modified tree. I interpreted it accordingly.

The injected release LIVE-STATE is outside this batch’s review scope.

## NOT VERIFIED

- Locally ran four non-writing regressions on Python **3.11.16**: **4 passed**; all four failed against `base_g5`.
- Full nine-test module and fast tier were not rerun because they require filesystem writes; their reported results remain orchestrator evidence.
- No real capture backend, live daemon, in-game validation, or durable fallback audit write. Local probes intercepted persistence.
- No files modified.

