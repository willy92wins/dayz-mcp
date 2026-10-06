## CHANGES

- `tools/tests/_frame_state_isolation.py:isolate_capture_frame_state` — per-test fixture. Replaces `DAYZ_MCP_FRAME_STATE_PATH` with a file inside a `TemporaryDirectory` (does not `setdefault`), restores the environment and deletes the directory via `addCleanup`. `note_frame_state_dir` records a directory the writer is allowed to use.
- `tools/tests/test_mcp_capture.py:MCPCaptureTest.setUp`, `CaptureDualCropSpaceTest.setUp`, `AllBlackFrameReportTest.setUp` — each capturing test installs that fixture. `AllBlackFrameReportTest.test_an_all_black_frame_reports_whether_the_frame_was_advancing` still creates its own `frame_state_` directory and env override, and notes that directory so the guard accepts it.
- `tools/tests/test_tool_description_caveats.py:CaptureFullresCropBehaviorTest.setUp` — same fixture. The existing `save_dir` temporary directory is unchanged.
- `tools/tests/test_capture_frame_state_isolation.py` — regression guard. Loads the four capturing classes by module name and by unittest discovery, asserts the same test ids, then runs both suites with `LOCALAPPDATA` pointed at a sentinel. One case inherits a `DAYZ_MCP_FRAME_STATE_PATH` inside that sentinel; the other removes the variable. Every `_write_frame_state` path must sit in a directory the fixture noted and must not sit under the sentinel. The head-identity scenario is invoked through its unwrapped body because `@slow_test` skips it on the fast tier.
- `CHANGELOG.md` — one Fixed line under Unreleased (inbox 4313).
- Production `frame_state_path` fallback is unchanged. No `addon/` edit, no sealed-module edit, no bridge version bump, no product-spec row (the spec names no C*/H*/D* row).

## TESTS ADDED

- `CaptureFrameStateIsolationTest.test_module_and_discovery_load_the_same_capturing_tests`
- `CaptureFrameStateIsolationTest.test_writer_stays_isolated_with_inherited_override` — fails on unmodified code: an inherited `DAYZ_MCP_FRAME_STATE_PATH` is the path `frame_state_path` returns, so the writer target is the sentinel file instead of a per-test temporary directory.
- `CaptureFrameStateIsolationTest.test_writer_stays_isolated_without_override` — fails on unmodified code: with the variable absent, `frame_state_path` uses `%LOCALAPPDATA%\DayZ_MCP\capture-frame-state.json`, which the sentinel makes visible.
- `CaptureFrameStateIsolationTest.test_guard_fails_when_the_fixture_is_removed` — with the fixture replaced by a no-op, both isolation checks raise `AssertionError` because the writer targets are recorded as violations after the calls return.

No existing assertion pin was changed.

## ROUND 2 FIXES

- F1 — `test_capture_frame_state_isolation.py` spy no longer asserts inside `_write_frame_state`. It records an escaping path, skips that write, and `_assert_writer_stays_isolated` asserts the violation list is empty only after the capturing calls return. `test_guard_fails_when_the_fixture_is_removed` disables `isolate_capture_frame_state` on both capturing modules and expects `AssertionError` for the inherited override and for the fallback.

## GATE OUTPUT

(pending)

## DEVIATIONS

- The reusable fixture lives in `tools/tests/_frame_state_isolation.py`, not in the new test module. `tests/test_suite_structure.py` forbids one `test_*` module importing another. The new tests still live in `tools/tests/test_capture_frame_state_isolation.py`.
- The head-identity method is `@slow_test`. The guard runs the discovered suites (fast tier skips that method) and then calls the unwrapped method so the spec scenario still hits the writer.
- The guard loads `CaptureDualCropSpaceTest` with `importlib.import_module`. A static `from tests.test_mcp_capture import ...` is a new cross-test import, which `tests.test_suite_structure` rejects.

## NOT VERIFIED

- The guard was not executed against a tree that lacks the fixture (the brief forbids running the unmodified tree). The failure mode above follows from `mcp_capture.frame_state_path` (`tools/mcp_capture.py` lines 557-561) and from the absence of an override in the capturing classes before this change.
- No in-game capture. The production fallback path was not exercised against a real profile, on purpose.
