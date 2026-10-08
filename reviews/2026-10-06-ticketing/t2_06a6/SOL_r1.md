# gpt-6.1-sol review, t2_06a6, round 1 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

All four scenarios were reproduced with in-memory backend stubs, without modifying files or starting processes.

**F1 — P2 — Capture still permits lazy-spawn.**  
`tools/dayz_mcp/server.py:7425` calls `runtime.lifecycle_status()`. In client mode, `server.py:1732` routes this through `_control_with_lazy_spawn`; `server.py:1532` invokes daemon recovery.

- Scenario: call `capture_screenshot({"frames":1})` while lifecycle status raises `ControlClientError("daemon_unavailable", request_stage="pre_request", http_bytes_sent=0)`.
- Wrong behavior: capture invokes `_ensure_daemon`. The reproduction recorded **one recovery call and two lifecycle reads**.
- Fix: use the bounded, non-spawning lifecycle reader for capture. This inherited path remains contrary to the explicit specification.

**F2 — P2 — A rejected restore erases the failed-camera warning.**  
`tools/dayz_mcp/server.py:7059` clears the attempt before dispatching `restore_gameplay`.

- Scenario: successful camera A → subsequent camera attempt raises `busy` → `restore_gameplay({"timeout_s":1.0})` also raises `busy`; lease remains held and run/generation unchanged → capture.
- Wrong output: before restore, metadata contains `camera_unverified_reason="busy"`; afterward, both warning and reason disappear although restore never executed successfully.
- Fix: retain the attempt when restore is rejected before changing gameplay.

**F3 — P2 — An unreadable caller state becomes confirmed absence.**  
`tools/dayz_mcp/lease_result_ttl.py:89`–`91` classifies every dictionary whose state is not `"active"` as `"absent"`.

- Scenario: session status returns `{"self":{}}`; no remembered camera failure; capture succeeds.
- Wrong output: `camera_unverified_reason="no_lease"` plus `camera_unverified`.
- Expected: reason `"unknown"`; the missing state does not establish absence.
- Fix: recognize explicit supported states and classify missing/unrecognized state as unknown.

**F4 — P2 — A lifecycle state transition invalidates an unchanged run/generation.**  
`tools/dayz_mcp/server.py:614` includes lifecycle `state` in the fingerprint; `server.py:687` clears the attempt whenever that fingerprint differs.

- Scenario: camera attempt raises `client_not_in_game` with context `(run-a, STARTING, gen-1, gen-1)`; capture initially warns. Change only state to `RUNNING`, keeping the caller’s lease, run ID and generations unchanged; capture again.
- Wrong output: `camera_unverified` and its reason disappear.
- Fix: distinguish run/generation identity changes from ordinary lifecycle state changes.

## SPEC COVERAGE

| Requirement | Status |
|---|---|
| Session-local last attempt, outcome/context, shared lock, restore/context invalidation | **Wrong:** storage and locking implemented; invalidation loses evidence in F2/F4. |
| One additive warning and reason for failed attempt or confirmed absence | **Wrong:** main scenario works; F2/F4 suppress required warnings and F3 invents confirmed absence. |
| Unreadable state → unknown; no inferred expiry; held lease does not certify pose | **Wrong:** no pose certification or invented expiry, but malformed state fails F3. |
| Capture available, no renewal, existing read control, no lazy-spawn | **Wrong:** no renewal added; F1 retains lazy-spawn. |
| Simulated tests covering required scenarios and preserving image/warnings | **Done:** requested cases exist and pass, with coverage gaps below. |

Python-only scope is satisfied; no PBO/reseal change appears in the diff.

## GATE GAP

The new fixture replaces `runtime.lifecycle_status` with an `AsyncMock` at `tools/tests/test_capture_camera_unverified.py:58`, bypassing the production lazy-spawn path. Tests also omit rejected restores, malformed `self.state`, and state-only lifecycle transitions. Consequently, all four findings survive the supplied gate.

Local verification: **12 tests passed**—the seven new tests plus `ProtocolTtlTest`.

## PREMISE

The original stale-camera scenario is valid. However, `REPORT.md:29` reports zero fast-tier failures, while the authoritative orchestrator output reports **two failures, zero new**. The supplied gate establishes no new detected failures, not an entirely green suite.

## NOT VERIFIED

- No in-game capture, actual daemon spawn, or deployed artifact validation.
- Full fast tier and suite ratchets were not rerun.
- The broader file-wait module encountered 13 temporary-directory errors under the read-only sandbox.
- No files were modified.

