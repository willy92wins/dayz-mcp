# gpt-6.1-sol review, g5r9_selection, round 1 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P2 — Explicit sidecar paths bypass selector validation.**  
[tools/mcp_capture.py:577](C:/Users/guill/dzmcp_gauntlet/g5r9_selection/ws/tools/mcp_capture.py:577)

Executable scenario:

- `sys.argv = ["mcp", "--instance", "NOTVALID"]`
- `DAYZ_MCP_FRAME_STATE_PATH=C:\r9-virtual\DayZ_MCP\capture-frame-state.json`
- Call `_frame_stale_report("window-key", "window", "ab", {})` with a writable destination.

`frame_state_path()` returns the override before calling `_bound_capture_token()`. The report reaches `_write_frame_state()` against the default instance’s sidecar and returns `state_backend="sidecar"`, `state_error=None`, without `selection_error`.

**Verified:** a read-only probe intercepted that exact write call. Validate the selector before accepting an override.

**F2 — P2 — Public capture results do not consistently propagate selection errors or prevent persistence.**  
[tools/mcp_capture.py:938](C:/Users/guill/dzmcp_gauntlet/g5r9_selection/ws/tools/mcp_capture.py:938), with consumers at `tools/mcp_capture.py:1481` and `tools/mcp_capture.py:1633`.

Executable scenario: invalid argv as above, no sidecar override, and a successful nonblack capture backend.

- `capture_screenshot(frames=1, scale=20)` returns only `type`, `mimeType`, and `data`. The detected `invalid_instance_token` disappears from the result.
- `capture_dual(frames=1, scale=20, save_fullres=True, save_dir=r"C:\r9-out")` reports the selection error in metadata **and still calls `write_fullres()`**.
- With `save_fullres=True` and no explicit destination, it instead raises an uncaught `InstanceSelectionError` while resolving the output directory.

**Verified:** synthetic backend probes exercised the public functions; persistence was intercepted. Propagate a structured selection failure through both public entrypoints and stop persistence when it occurs.

**F3 — P2 — Standalone capture now requires the DayZ-MCP package.**  
[tools/mcp_capture.py:556](C:/Users/guill/dzmcp_gauntlet/g5r9_selection/ws/tools/mcp_capture.py:556), repeated in the exception handler at `tools/mcp_capture.py:931`.

Executable scenario: load `mcp_capture.py` in an environment with Pillow available but `dayz_mcp` unavailable, omit the selector and sidecar override, then call `_frame_stale_report("key", "window", "ab", {})`.

The unconditional import raises `ModuleNotFoundError`; the exception handler repeats that import and lets the exception escape. This breaks the standalone behavior explicitly described at `tools/mcp_capture.py:574`.

**Verified:** isolated imports showed the base helper returning `None` for the omitted token, while this tree’s helper and report raised `ModuleNotFoundError`. Preserve package-absence handling while continuing to propagate actual selection errors.

## SPEC COVERAGE

| Bullet | Status | Evidence |
|---|---|---|
| M2: shared entry validation before effects | done | Both entrypoints validate first and disable abbreviations. Regression passed locally. |
| M3: environment conflicts rejected across modes | done | `server.py:7333` invokes the shared rejection function. Regression passed locally. |
| M4: doctor accepts and diagnoses selected instance | done | Binding precedes policy loading; real sources select registration and runtime root by token. Regression passed locally. |
| M5: selection error reported, no fallback/write | **wrong** | F1 and F2. |
| A4: fallback audit row uses daemon instance | done | Bound token replaces undefined `config`; daemon binds its configuration before execution. Durable-row test was sandbox-blocked locally. |
| A5: common exec-enforce audit filename | done | Both writers use the shared helper. Regression passed locally. |
| One regression per bullet | done | Six tests exist, but M5 coverage is insufficient. |

## GATE GAP

The M5 test at `tools/tests/test_g5_r9_selection.py:123` explicitly removes the path override and exercises only `_frame_stale_report()`. It misses override writes, public-result propagation, full-resolution persistence, and standalone package absence.

The doctor test mocks `execute()`, so it establishes binding rather than end-to-end diagnostic behavior.

## PREMISE

“Each must fail on this tree” must mean the **unmodified baseline**; requiring failure on the implementation would contradict the acceptance gate.

Four reverse checks against `base_g5` failed as expected. However, A5 fails immediately because its newly introduced helper cannot be imported, rather than demonstrating the original filename mismatch.

The injected release LIVE-STATE is unrelated to this review’s modified tree and was not used as current evidence.

## NOT VERIFIED

- Project-venv run: **four tests passed; A4 and M5 errored because the read-only sandbox denies temporary-directory creation**. These are environment limitations, not implementation failures.
- No local fast-tier rerun or Python 3.11 rerun; those results are supplied orchestrator evidence.
- No real capture, live daemon, durable audit write, or in-game verification.
- Failure probes intercepted writes; no files were modified and no commit was created.

