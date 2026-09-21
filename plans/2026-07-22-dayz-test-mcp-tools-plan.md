# DayZ Test MCP Tools Implementation Plan

**Status:** IMPLEMENTED — offline gates and controlled MERCEDES launch/stop smoke passed 2026-07-23. Utopia preflight passed but its DayZDiag process crashed during launch; the tool failed closed and left queue/session clean.

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:executing-plans` to implement this plan task-by-task. The adversarial contract gate must be green before editing product code.

**Goal:** Expose the sealed `dayz-test-v1` launcher through two typed, request-bound MCP tools that queue until completion and never require the caller to handle a lease token.

**Architecture:** Add a small MCP adapter over the existing secure launcher transaction. It builds the canonical request from a sealed project policy, captures only the bounded terminal JSON, and reuses the current FIFO/heartbeat/release path. Stop resolves `run_id → @mod → sealed project`, then the native worker adopts the exact idle run before stopping it.

**Tech Stack:** Python 3.14, FastMCP, existing `ControlClient`, native MSVC launcher bundle, `unittest`/`pytest`.

## Global Constraints

- Product criterion: `product-spec.md` H11.
- No daemon/job engine, subprocess wrapper, second queue protocol or background job ID.
- No caller-provided executable, `dev_root`, source path, arbitrary path, PID, argv, token or arbitrary environment. Public mission accepts only aliases; public mod lists accept only relative one-segment identifiers. Absolute defaults may come only from the sealed policy.
- The lease token remains private to `ControlClient → native backend → approved lifecycle child`; it never enters MCP arguments/results, public request bytes or logs.
- Calls remain open while queued and executing. No finite public queue timeout is added.
- If the MCP session already owns a ticket, operation or lease, both tools fail before enqueue with `session_busy`.
- A successful launch releases the operation lease and leaves the run `RUNNING_IDLE`; a later stop reacquires, adopts and stops only the exact `run_id`.
- No persistence/network schema changes. The lifecycle manifest and launcher registry formats remain v1.
- Rebuilding the sealed bundle is required because worker/app bytes change. Registry replacement uses the existing rollback/install CAS workflow and retains its receipt for recovery.
- No incidental refactor. Every changed line must trace to H11, terminal error fidelity, or the localized idle-stop defect.

---

## Approved public contract

### Tool 1: `dayz_test_run`

[DESIGN] New typed interface (signature; implementation is defined by Tasks 1 and 4):

```python
async def dayz_test_run(
    project: str,
    mode: str,
    mission: str = "chernarus",
    build: bool = False,
    clean: bool = False,
    pack_only: bool = False,
    preflight: bool = False,
    run_id: str | None = None,
    extra_mods: list[str] | None = None,
    base_mods: list[str] | None = None,
    server_mods: list[str] | None = None,
    no_base_mods: bool = False,
    no_file_patching: bool = False,
    port: int = 2302,
    width: int = 1920,
    height: int = 1080,
    player_name: str = "Dev",
    server_wait_s: int = 60,
    ctx: Context | None = None,
) -> dict[str, Any]
```

- `project` is an exact selector from the sealed policy: currently `Utopia_PC`, `LF_VStorage` or `MERCEDES_AMGLF`.
- `mode` is required and must be `offline`, `server`, `client` or `all`.
- `mission` is restricted by the MCP adapter to `chernarus`, `livonia` or `sakhal`; unlike the lower-level CLI schema, this tool never accepts a mission path.
- Caller-supplied `extra_mods`, `base_mods` and `server_mods` entries must be relative, normalized, one-segment identifiers with no drive, slash, backslash, `.` or `..`. Existing `dayz_test_request.parse_dayz_test_request` remains the final authority after these stricter MCP-only checks.
- `base_mods=None` means policy defaults. `no_base_mods=True` is the explicit empty-base path.
- `run_id` is accepted only where the current schema permits extension (`client`, or `offline` where applicable). Before enqueue, lifecycle status must contain exactly that `run_id` in `RUNNING_IDLE` with `run.mod == "@" + selected_policy.mod`; `adopt` under lease revalidates the state/identity race.

### Tool 2: `dayz_test_stop`

[DESIGN] New typed interface (signature; implementation is defined by Tasks 1 and 4):

```python
async def dayz_test_stop(
    run_id: str,
    ctx: Context | None = None,
) -> dict[str, Any]
```

- No project, PID or path argument.
- Read lifecycle status, find one exact run, map `run.mod == "@" + sealed_policy.mod`, then build a canonical `mode="offline", kill=True` request.
- The authoritative mutation remains `adopt(run_id) → stop(run_id)` under the newly acquired lease. A status/adopt race fails closed.

### Public result

[DESIGN] Both tools return exactly these fields after an accepted operation:

```python
{
    "status": "succeeded" | "failed",
    "project": str,
    "mode": "offline" | "server" | "client" | "all" | "stop",
    "run_id": str | None,
    "phase": "validating" | "queued" | "executing" | "finalizing" | "completed",
    "elapsed_s": float,
    "artifacts_paths": list[str],
    "error_code": str | None,
    "cleanup_degraded": bool,
}
```

- Validation/session ownership errors are `ToolError` codes before enqueue.
- Native worker failures return `status="failed"`, `phase="executing"` and a closed `error_code`; stdout/stderr and exception text are never returned.
- If cleanup of a newly created run is uncertain, `cleanup_degraded=True` and `run_id` identifies the run needing inspection. After confirmed cleanup, failure may return `run_id=None`.
- Cancellation may prevent delivery of a result to the caller; its invariant is bounded cleanup, exact ticket cancellation and no hidden lease.
- `artifacts_paths` contains profile directories derived from the sealed `dev_root` and requested mode, never caller paths or file contents.

### Progress

[DESIGN] FastMCP progress messages use four stable stages:

```text
validating
queued (position N)
executing
finalizing
```

The existing `ControlClient.session_acquire_wait` supplies the FIFO position. The adapter reports the other boundaries; no detailed build subprocess stream is surfaced.

### Native terminal

[DESIGN] The internal terminal remains canonical JSON, capped by the existing PE at 4096 bytes, and has exactly:

```python
{
    "cleanup_degraded": bool,
    "error_code": str | None,
    "exit_code": int,
    "ok": bool,
    "run_id": str | None,
}
```

The MCP parser rejects duplicate keys, non-canonical encoding, extra/missing fields, invalid UUIDs, invalid error-code syntax, inconsistent `ok/exit_code`, oversize output and any stderr output.

[DESIGN] Worker terminal error codes are exactly:

```text
build_failed
build_source_unavailable
internal_failure
operation_cancelled
readiness_failed
request_integrity_failed
run_not_adoptable
run_stop_failed
runtime_policy_invalid
worker_failed
worker_identity_failed
```

Success invariant: `ok=True`, `exit_code=0`, `error_code=None`, `cleanup_degraded=False`, and `run_id` is UUIDv4 or null (preflight). Failure invariant: `ok=False`, `exit_code` is 1..255 and `error_code` is in the allowlist. `cleanup_degraded=True` is valid only on failure with a UUIDv4 `run_id`; after confirmed cleanup a failed terminal has `run_id=None`.

---

## File map

- Create `tools/dayz_mcp/dayz_test_tool.py` — typed-request adapter, policy selection, terminal parser, compact result and stop resolution.
- Create `tools/tests/test_dayz_test_tool.py` — adapter contract and negative/security fixtures.
- Create `tools/tests/test_dayz_test_app.py` — closed native terminal schema and exception sanitization.
- Modify `tools/dayz_mcp/server.py` — register the two MCP tools and expose lifecycle status through `ClientRuntime`.
- Modify `tools/dayz_mcp/control_client.py` — add authenticated read-only `lifecycle_status()` and immediate enqueue progress.
- Modify `tools/dayz_mcp/native_launcher_transaction.py` — pass the existing queue progress callback through; no queue semantics change.
- Modify `tools/dayz_mcp/secure_launcher.py` — extract one async verified-launch function shared by CLI and MCP; preserve CLI behavior and redaction.
- Modify `tools/dayz_mcp/dayz_test_worker.py` — adopt before kill and propagate sanitized cleanup outcome.
- Modify `tools/native-launchers/dayz-test-v1/src/app_main.py` — emit the closed terminal schema for success/failure/cancel.
- Modify focused tests: `test_control_client.py`, `test_native_launcher_transaction.py`, `test_secure_launcher.py`, `test_dayz_test_worker.py`, `test_mcp_tools.py`.
- Rebuild generated bundle files under `tools/native-launchers/dayz-test-v1/`, then update `tools/approved-launchers.json` via the existing registry updater.
- Modify `tools/README-mcp.md` — document the first-class tools and remove the implication that callers must launch through a shell harness.

---

## Viability tests — must exist before implementation

1. Tool schemas expose both names and contain no `lease_token`, path, PID, argv or environment argument.
2. Invalid project/mode/cross-field request, mission path, absolute/multi-segment public mod, mismatched/non-idle extension `run_id`, and a locally busy session fail before enqueue/native call. The read-only lifecycle lookup for `run_id` is permitted before this rejection.
3. A queued operation reports its position, stays open, receives the grant and leaves no ticket/lease after completion. A concurrent manual session call on the same `ClientRuntime` cannot observe or release the automatic lease.
4. Cancellation before grant cancels only its operation; cancellation after grant completes protected release.
5. Run success parses a canonical terminal, returns the exact `run_id`, and reports expected profile directories.
6. Stop resolves `@mod`, then worker calls `adopt` followed by `stop`; unknown, foreign, non-idle, identity-mismatched or raced runs are never terminated.
7. Build/readiness/start/ack failures stop only the newly created run. Failed cleanup sets terminal/public `cleanup_degraded` and retains its `run_id`.
8. Token/identity split across output chunks is redacted before capture; terminal parser rejects secrets, malformed/duplicate/oversize/non-canonical data and non-empty stderr.
9. Existing CLI tests and native launcher transaction suites remain green.
10. Reproducible bundle verification passes; registry opens the rebuilt launcher and validates its sealed bundle.
11. Controlled real smoke: invoke `dayz_test_run(project="Utopia_PC", mode="offline", preflight=True)` through FastMCP, then one offline launch followed by `dayz_test_stop(run_id)`. End with `session_status`: no own lease, ticket or pending commands.

---

### Task 1: Adapter contract and failing tests

**Files:** create `tools/tests/test_dayz_test_tool.py`; create `tools/dayz_mcp/dayz_test_tool.py` only after red tests.

**Interfaces:** produces `build_run_request`, `parse_worker_terminal`, `execute_dayz_test_run` and `execute_dayz_test_stop`; consumes sealed policies and a runtime implementing session/lifecycle methods. `build_run_request` applies the MCP-only alias/relative-mod restrictions before delegating to the existing parser. Run execution resolves and binds any supplied `run_id` to `RUNNING_IDLE` and `"@" + project` before enqueue.

- [ ] Write tests for policy selection/defaults, mission-path and absolute/multi-segment mod rejection, cross-field delegation, project/run binding, exact result keys, exact `@mod` stop mapping, session-busy rejection and all terminal allowlist/invariant negatives from viability tests 1, 2, 5, 6 and 8.
- [ ] Run `python -m unittest tests.test_dayz_test_tool -v` from `tools`; expected failure: module/functions absent.
- [ ] Implement the minimal adapter. It must not read the policy JSON directly; it receives `verified_bundle.sealed_policies` from the opened, verified bundle.
- [ ] Run `python -m unittest tests.test_dayz_test_tool -v`; expected: PASS.

### Task 2: Shared secure transaction and lifecycle status

**Files:** modify `control_client.py`, `native_launcher_transaction.py`, `secure_launcher.py`; tests `test_control_client.py`, `test_native_launcher_transaction.py`, `test_secure_launcher.py`.

**Interfaces:** add `ControlClient.lifecycle_status() -> dict[str, object]`; add optional queue progress passthrough to `execute_native_launcher_transaction`; add one async secure-launch function accepting an already opened launcher, verified bundle, control runtime and sanitized output sink.

- [ ] Add red tests for `/lifecycle/status`, immediate initial queue-position progress, progress passthrough, async shared-launch success, split-secret redaction and unchanged CLI output/exit behavior.
- [ ] Run the three focused modules; expected new tests FAIL and old tests PASS.
- [ ] Implement only those interfaces. The shared launch function must keep secret redaction inside `secure_launcher.py`; the MCP adapter never receives raw token-bearing output.
- [ ] Run `python -m unittest tests.test_control_client tests.test_native_launcher_transaction tests.test_secure_launcher -v`; expected: PASS.

### Task 3: Correct idle stop and closed terminal outcome

**Files:** modify `dayz_test_worker.py`, `src/app_main.py`; create `test_dayz_test_app.py`; run `test_native_launcher_bundle.py` after rebuild.

**Interfaces:** `DayzTestWorkerError` carries only a closed code, optional run ID and cleanup-degraded boolean. `_stop_best_effort` reports success/failure. Kill performs `adopt → stop`.

- [ ] Change the permissive kill test to require `adopt, stop`; add rejection, cancellation and cleanup-degraded fixtures.
- [ ] Add app terminal tests for exact canonical success/failure schema and generic exception sanitization.
- [ ] Run focused tests; expected new tests FAIL against direct-stop/generic terminal behavior.
- [ ] Implement the minimal worker and terminal changes. Never serialize exception messages other than validated internal error codes.
- [ ] Run `python -m unittest tests.test_dayz_test_worker tests.test_dayz_test_app tests.test_task9_launcher_migration -v`; expected: PASS before rebuild-sensitive gates.

### Task 4: FastMCP surface, bundle, registry and end-to-end gates

**Files:** modify `server.py`, `test_mcp_tools.py`, `README-mcp.md`; rebuild sealed bundle; update launcher registry.

- [ ] Add tool-surface/local-validation tests and a mocked FastMCP run/stop flow. Assert neither tool has a token/path/PID/argv argument.
- [ ] Run `python -m unittest tests.test_mcp_tools -v`; expected new tests FAIL.
- [ ] Register both tools only through `_client_runtime()`, report the four progress stages and translate adapter contract errors to closed `ToolError` codes. Hold the existing `runtime.tool_lock` for the whole automatic operation and acquire it in the public `session_*` handlers so a concurrent same-runtime call cannot observe/release its lease; local busy state still fails before enqueue.
- [ ] Run `python -m unittest tests.test_mcp_tools tests.test_dayz_test_tool -v`; expected: PASS.
- [ ] Rebuild with `python tools/build_native_launcher.py --offline --verify-reproducible`; expected: reproducibility verification PASS.
- [ ] Run native focused suites: `python -m unittest tests.test_native_launcher_bundle tests.test_native_launcher_backend tests.test_secure_launcher tests.test_task9_launcher_migration -v`; expected: PASS.
- [ ] Replace the installed `dayz-test-v1` registry entry from `tools`: run `python -m dayz_mcp.launcher_registry_update rollback-last`; then `$registryHash = (Get-FileHash -Algorithm SHA256 approved-launchers.json).Hash` and `python -m dayz_mcp.launcher_registry_update install-dayz-test-v1 --expected-sha256 $registryHash`; expected: committed receipt and verified open.
- [ ] Run all queue/lifecycle/tool regression suites used by BUG-046/H9 plus the new tests; expected: zero failures.
- [ ] Execute the controlled real preflight, offline launch and exact stop. Verify final redacted `session_status` is clean and no managed run remains active.
- [ ] Update durable memory, bug ledger and session handoff. Do not claim a git commit: this project directory is not a git worktree.

---

## Final review gates

- Every changed product line traces to H11 or the direct-stop degradation.
- No full stdout/stderr, secret, request JSON, PID or argv enters MCP output.
- No active/ticket state can self-deadlock the operation.
- No cancellation path grants blindly, leaks ownership or stops a foreign process.
- No bundle/registry format changed; rollback remains the existing receipt-backed transition.
- `session_status` final: own lease none, own ticket none, pending commands zero.

## Closeout evidence — 2026-07-23

- Consolidated regression: 263 tests, `OK` (23.840 s).
- Reproducible installed bundle: PE `9A787B3F534D60BD22F087AF76912751243694C272E163AD1F66C552CF638918`; registry contains only `dayz-test-v1`.
- Real smoke on the final bundle: MERCEDES `dayz_test_run` created run `2218cca5-8935-422f-8960-5ef9ee30bdea`; `dayz_test_stop` returned the same run and both calls completed without degraded cleanup.
- Utopia residual: preflight passed; DayZDiag then produced a minidump and exited. This is a process crash, not a lease/queue stall; the MCP returned a closed failure and cleaned its operation.
