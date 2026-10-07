# gpt-6.1-sol review, g5r9_locks, round 1 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P2 — Configured build-lock timeout does not reach the production worker.**

Anchors: `tools/dayz_mcp/server_cli.py:95`, `tools/dayz_mcp/native_launcher_backend.py:544`, `tools/native-launchers/dayz-test-v1/src/launcher.cpp:600`.

The timeout is read from `DAYZ_MCP_BUILD_LOCK_WAIT_S`, but both environment construction stages omit that variable.

**Failure scenario:** Set `DAYZ_MCP_BUILD_LOCK_WAIT_S=0.3` in the daemon environment, hold the requested target/temp build locks for 15 seconds, and submit a sealed `build=true` request. The worker uses **900 seconds**, waits for release, and proceeds instead of returning `build_busy` at approximately 0.3 seconds.

A read-only probe confirmed that the generated launcher environment omits the variable and the private-worker environment produces `build_lock_wait_s() == 900.0`.

**Suggested fix:** Carry the validated limit through an accredited configuration channel into the sealed worker. Test the production transport boundary.

**F2 — P2 — The extended synchronous wait prevents cooperative cancellation.**

Anchors: `tools/dayz_mcp/server_cli.py:204`, `tools/dayz_mcp/dayz_test_worker.py:809`, `tools/native-launchers/dayz-test-v1/src/app_main.py:256`.

The async worker enters synchronous polling containing `time.sleep()`. Its event loop cannot process the cancellation watcher’s queued callback during that wait.

**Failure scenario:** Hold the shared build locks for 60 seconds, start a `build=true` worker, and queue `cancel_event.set` through `loop.call_soon_threadsafe` after 0.1 seconds. Cancellation remains unprocessed throughout contention. In production, this exceeds the native backend’s 25-second cancellation grace and requires forced job termination (`tools/dayz_mcp/native_launcher_backend.py:1381`, `:1395`).

A read-only probe using the actual polling loop with mocked filesystem/locking operations queued cancellation at **0.051 seconds**. At **0.358 seconds**, the worker dispatched `ADDON_BUILDER` with cancellation still unseen. With the immediate fixture broker, it returned success before the queued callback ran. This proves worker event-loop starvation; it does not establish that the native launcher permits a build after cancellation.

**Suggested fix:** Make acquisition cancellation-aware without blocking the event loop, release acquired resources on cancellation, and check cancellation before dispatching AddonBuilder.

## SPEC COVERAGE

| Specification bullet | Status | Evidence |
|---|---|---|
| **A6:** Delete sharing excluded for every named lock; retained owner token; leases released before cleanup | **done** | Shared opener at `server_cli.py:128` uses sharing mask `0x3`. All five lock kinds use it. `instance_context.py:177`, `:185`, `:227` retain and compare owner objects. Cleanup changes release leases. |
| **A7:** Relative shared-root override rejected by name | **done** | `server_cli.py:158` raises `relative_shared_root`; admission delegates to it. Read-only probes confirmed both paths. |
| **B2:** Bounded configurable polling, named timeout error, regenerated module lock, regressions | **wrong** | Default 900-second polling, `LK_NBLCK`, `build_busy` mapping, and hashes are implemented. Production configurability fails under F1; cancellation is unsafe under F2. |
| **B3:** Settled receipt still scans with the current classifier | **done** | `identity_migration.py:1598` invokes `_blocking_pids` before returning. Daemon startup supplies the selected token at `daemon.py:262`. Fake-process probes classified a legacy daemon as blocking and a valid client as nonblocking. |

## GATE GAP

The new build tests invoke the worker directly inside a patched environment (`tools/tests/test_g5_r9_locks.py:248`, `:276`). They bypass the sealed launcher’s environment filtering and never cancel during lock contention, so both blockers escape the gate.

Additional gaps include cross-process contention, timeout while acquiring the second resource, and deterministic owner-retention verification. The owner regression relies on allocation churn rather than explicitly proving the original owner remains alive.

## PREMISE

The requested specification is coherent. The supplied gate supersedes `REPORT.md:29`, which still says the gate is pending. Its two baseline failures are not attributed to this batch.

The blanket claim at `server_cli.py:110` that CRT `os.open` shares delete is not established by the supplied evidence. Microsoft documents delete-sharing behavior specifically for `_O_TEMPORARY`, which the base lock flags omit. The explicit root-writer sharing defect remains valid. [Microsoft CRT documentation](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/open-wopen?view=msvc-170)

## NOT VERIFIED

- Did not rerun filesystem-writing unittest modules: this sandbox permits reads only. The seven-test and fast-tier results are orchestrator-supplied.
- Ran read-only configuration, cancellation, relative-root, and classifier probes.
- Ran `python -B write_packaged_modules_lock.py --check`: **packaged modules lock ok**.
- No actual AddonBuilder, sealed-launcher execution, live daemon, or in-game validation.
- No files modified.

