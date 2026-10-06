# Triage bb6d

Tickets: fb-20261001-003342-bb6d

Title: Test sensible a la carga: test_fb_c9ca... da native_launcher_request_writer_stuck en un tier rápido lento

First pass by GLM-5.3-Flash-EXL3 on the GX10 (API direct, grep excerpts of origin/main 6671fd2, no whole-tree access). Raw model output below, unedited.

<!-- ticket bb6d | model GLM-5.3-Flash-EXL3 | finish stop | 373s | usage {"prompt_tokens": 40902, "completion_tokens": 3899, "total_tokens": 44801, "prompt_tokens_details": {"cached_tokens": 0}, "completion_tokens_details": {"reasoning_tokens": 2672}} | grep ['native_launcher_request_writer_stuck', '_supervise_created_launcher', 'TICKET', 'native_launcher_backend', 'NativeLauncherBackendError', 'Evidencia', 'worktree', 'changelog', 'cargada', 'determinista', 'cambio', 'discover'] -->

## VERDICT
OPEN_CONFIRMED — the supervision code still decides "writer stuck" from two fixed real-clock join windows with no injection point, so the load-sensitive flake mechanism the ticket describes is still present on main.

## EVIDENCE
- `tools/dayz_mcp/native_launcher_backend.py:1264` — `start_deadline = time.monotonic() + _LAUNCHER_START_SECONDS`: the supervisor reads the real clock directly; there is no injectable clock or deadline parameter.
- `tools/dayz_mcp/native_launcher_backend.py:1344-1356` — `finish_request_writer`: if the writer is not done it is cancelled, then `join(_DEBUG_DRAIN_SECONDS)` (line 1350), and on a second failed `join(_DEBUG_DRAIN_SECONDS)` (line 1353) it sets `failure = "native_launcher_request_writer_stuck"` (line 1354). The verdict is purely "thread did not finish within 2× a fixed constant under the real scheduler".
- `tools/dayz_mcp/native_launcher_backend.py:1356` — after the joins, `check_request_writer(time.monotonic())` again uses the real clock.
- `tools/dayz_mcp/native_launcher_backend.py:1385-1392` — the main loop polls `time.monotonic()` and re-checks the writer each iteration; all timing derives from wall-clock constants, none from an injected source.
- `tools/dayz_mcp/native_launcher_backend.py:1761-1768` — the only shown call site passes no timing overrides, confirming no injection hook exists today.
- NOT SHOWN: the raise site at `native_launcher_backend.py:1653` (where the failure string becomes `NativeLauncherBackendError`); the values of `_DEBUG_DRAIN_SECONDS` and `_LAUNCHER_START_SECONDS`; `_PublicRequestWriter` (`cancel`/`join` semantics, whether cancel interrupts a blocked write); the test `NativeDebugOwnershipTests.test_fb_c9ca_missing_zero_empty_handles_returns_root_exit` in `tests/test_native_launcher_backend.py`; the evidence log `dzn-night/logs/gate-changelog-022505.log.fast.err`.

## ROOT CAUSE
Under a loaded machine the request-writer thread does not observe cancel/finish within the two fixed `_DEBUG_DRAIN_SECONDS` join windows, so the supervisor labels a slow-but-alive writer as `native_launcher_request_writer_stuck` and fails the launch. It is a timing-margin defect (real clock, hard-coded constants, no injection), not a logic error — hence 3/3 passes unloaded and the ERROR only in the 196 s loaded pass.

## PROPOSED FIX
Recommended (smallest, test-only): in `tests/test_native_launcher_backend.py`, for `test_fb_c9ca_missing_zero_empty_handles_returns_root_exit` (or the test class setUp), widen the drain margin via `unittest.mock.patch.object(native_launcher_backend, "_DEBUG_DRAIN_SECONDS", <larger value>)` — the constant is read at call time inside `finish_request_writer` (lines 1350/1353), so patching the module attribute is sufficient; no production change, no signature change, callers at line 1761 untouched. Before: under load the test errors `NativeLauncherBackendError: native_launcher_request_writer_stuck`; after: the joins wait the widened window, the writer finishes, and the test reaches its root-exit assertion.

Alternative (if injection is wanted in production): add a keyword-only `drain_seconds: float | None = None` to `_supervise_created_launcher`, defaulting to `_DEBUG_DRAIN_SECONDS`, used at lines 1350/1353, and thread it through `launch_registered_native`'s worker (line 1761) as an optional pass-through; default behaviour and error contract (`native_launcher_request_writer_stuck` when the writer truly never finishes) are unchanged.

## TEST
Unit test: monkeypatch the module's request-writer used by `_supervise_created_launcher` with a fake whose `join(timeout)` returns `False` for timeouts ≤ the default `_DEBUG_DRAIN_SECONDS` but `True` for a larger injected window, and assert the supervision does not produce `native_launcher_request_writer_stuck` when the larger window is used. Before the fix (no injection / default constant) the fake exceeds the window and the failure string is set — the test fails; after either fix it passes. (The fake's insertion point — the `_PublicRequestWriter` construction site — is NOT SHOWN, so the exact patch target must be confirmed against the full file.) A cheaper in-repo check: run the fast tier under artificial CPU load; it errored before, passes after.

## SIZE / RISK
S. Main risk: masking a genuinely stuck writer — a larger drain window delays (does not remove) the fail-closed `native_launcher_request_writer_stuck` verdict for a writer that is truly blocked, so the widened margin should stay bounded and only in the test (or default-preserving in production).

## NOT VERIFIED
- The value of `_DEBUG_DRAIN_SECONDS` / `_LAUNCHER_START_SECONDS`, so I cannot say how much margin is needed.
- The raise site at line 1653 and how `failure` maps to the reported error.
- `_PublicRequestWriter.cancel()` semantics — whether cancel interrupts a blocked write or only sets a flag the writer polls (this decides whether the writer was slow vs. genuinely stuck in the failing run).
- The test body and what "missing/zero/empty handles" sets up (whether the scenario makes the write legitimately block).
- The evidence log `gate-changelog-022505.log.fast.err` and whether the loaded pass had other contention (e.g., parallel suites) that shrank effective CPU.
- Where `finish_request_writer` is invoked in the supervision loop (only its definition is shown).