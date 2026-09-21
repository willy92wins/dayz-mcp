# DayZ Test Readiness Reason Instrumentation Plan

**Status:** APPROVED FOR ISOLATED TDD. Canonical source, bundle, registry and
runtime remain gated by the rollout steps below.

**Date:** 2026-07-26

**Goal:** Preserve a stable, closed reason for every terminal
`dayz_test_run(mode="all")` readiness failure, using the existing
`error_code` field without changing the public/DZW1 shape or version. The
closed `error_code` allowlist deliberately expands.

## 1. Product trace and scope

- DPF H11 requires typed first-class test lifecycle, bounded compact results,
  exact-run cleanup and no PID, argv, arbitrary path, token or exception text
  in the public result
  (`P:\DayZ_MCP_dev\product-spec.md:138`).
- DPF H10 requires fail-closed control and no disclosure before accreditation
  (`P:\DayZ_MCP_dev\product-spec.md:137`). This change does not touch transport
  or accreditation, but its diagnostics must preserve those confidentiality
  properties.
- The implemented H11 contract has exactly nine public result keys and five
  DZW1 terminal keys
  (`P:\DayZ_MCP_dev\plans\2026-07-22-dayz-test-mcp-tools-plan.md:80-149`;
  `P:\DayZ_MCP_dev\tools\dayz_mcp\dayz_test_tool.py:14-17,328-346`).
- The current cause is collapsed at
  `wait_for_owned_udp(...) -> bool`
  (`P:\DayZ_MCP_dev\tools\dayz_mcp\dayz_test_readiness.py:66-119`) and then
  converted unconditionally to `readiness_failed`
  (`P:\DayZ_MCP_dev\tools\dayz_mcp\dayz_test_worker.py:574-598`).

This phase is therefore a localized H11 diagnostic correction, not a new
feature or protocol.

### In scope

Product source:

1. `tools/dayz_mcp/dayz_test_readiness.py`
2. `tools/dayz_mcp/dayz_test_worker.py`
3. `tools/native-launchers/dayz-test-v1/src/app_main.py`

Tests:

1. `tools/tests/test_dayz_test_readiness.py`
2. `tools/tests/test_dayz_test_worker.py`
3. `tools/tests/test_dayz_test_app.py`
4. `tools/tests/test_dayz_test_tool.py`

Generated artifacts, only after all source gates pass:

1. The complete generated unit
   `tools/native-launchers/dayz-test-v1/`, including `src/`, `runtime/`,
   `vendor/`, `app.pyz`, `request-policy.json`, `worker-runtime.json`,
   `build-contract.json`, `closure-manifest.json`,
   `dayz-test-launcher.exe` and `reproducibility.json`.
2. `tools/approved-launchers.json`, only through
   `dayz_mcp.launcher_registry_update`
3. Builder-produced receipts/manifests required by the existing H11 workflow.

The bundle directory is one generated replacement unit, not a list of
independently restorable files. Its pre/post tree manifests contain every
relative file path, byte count and SHA-256, and freeze the absence of sibling
`dayz-test-v1.previous`.

### Explicitly out of scope

- No DZW2, sixth terminal key, tenth public key or registry/bundle format
  version. The only contract expansion is the closed `error_code` enum.
- No change to `dayz_test_tool.py`, `launcher.cpp`, daemon transport, lease,
  queue, ownership, stop, timeout, polling cadence, port or lifecycle rules.
- No exception message, PID, argv, path, request body, identity, token or
  environment data in any result or receipt.
- No direct process kill, daemon restart, manual registry edit or client hot
  reload claim.
- No LFPowerGrid source, candidate, baseline, integration or publication
  change.

## 2. Closed internal contract

The existing verified public signatures and schemas remain unchanged.

[DESIGN] Add only this internal value contract in
`dayz_test_readiness.py`:

```python
READINESS_ERROR_CODES: frozenset[str]

@dataclass(frozen=True, slots=True)
class ReadinessResult:
    ready: bool
    error_code: str | None

async def wait_for_owned_udp(
    broker: _Broker,
    run_id: str,
    port: int,
    timeout_s: int | float,
    *,
    psutil_module: object,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> ReadinessResult
```

`ReadinessResult` is fail-closed:

- success is exactly `ready=True, error_code=None`;
- failure is exactly `ready=False` with one member of
  `READINESS_ERROR_CODES`;
- any other constructor combination raises the stable internal
  `ValueError("invalid_readiness_result")`.

The eight new codes are final:

1. `readiness_status_invalid`
2. `readiness_run_not_running`
3. `readiness_server_process_invalid`
4. `readiness_server_pid_dead`
5. `readiness_udp_snapshot_invalid`
6. `readiness_udp_foreign_owner`
7. `readiness_probe_failed`
8. `readiness_timeout`

`readiness_failed` remains in `WORKER_ERROR_CODES` solely as the legacy
fallback and rollback-compatible code. It is not emitted by the new production
readiness probe.

### Compatibility matrix

| Bundle | Parser/allowlist | Result |
|---|---|---|
| old | old | baseline behavior; `readiness_failed` accepted |
| old | new | compatible because legacy `readiness_failed` remains accepted |
| new | new | all eight typed readiness codes accepted |
| new | old | fails closed as terminal-invalid; never claim compatibility or hot reload |

The rollout therefore updates/tests the parser source before installing the
new bundle, forbids use by old live clients, and requires a genuinely fresh
client after registration.

### Exact causal mapping

| Condition | Result |
|---|---|
| Broker response is not a dict, or `runs` is not a list | `readiness_status_invalid` |
| Exact `run_id` is absent, duplicated, or not `RUNNING` | `readiness_run_not_running` |
| `processes` is not a list, there is not exactly one server process, or its PID is invalid | `readiness_server_process_invalid` |
| The exact server PID no longer exists | `readiness_server_pid_dead` |
| `psutil.net_connections(kind="udp")` does not return a list | `readiness_udp_snapshot_invalid` |
| A positive owner PID different from the exact server PID owns the requested port | `readiness_udp_foreign_owner` |
| Broker/psutil/probe code raises an exception other than `CancelledError` | `readiness_probe_failed` |
| Deadline expires with no binding or only unknown/ambiguous owner data | `readiness_timeout` |
| Requested port is bound and every observed owner equals the exact server PID | success |
| Any awaited operation raises `asyncio.CancelledError` | re-raise unchanged |

Unknown owner values continue to wait. The loop deadline, two-second maximum
sleep and validation limits remain byte-for-byte semantically unchanged.

### Worker propagation

[DESIGN] Change the verified internal callback annotation at
`dayz_test_worker.py:454-465` to:

```python
readiness_probe: Callable[
    [str, int, int],
    Awaitable[dayz_test_readiness.ReadinessResult],
] | None = None
```

The worker must:

1. retain `readiness_failed` when no probe is supplied or a callback violates
   the internal result type;
2. raise `DayzTestWorkerError(result.error_code)` for a valid failure result;
3. preserve the existing `created_run_id` cleanup and
   `cleanup_degraded` behavior;
4. preserve cancellation as `operation_cancelled` after exact-run cleanup;
5. build `WORKER_ERROR_CODES` from the existing legacy set plus
   `READINESS_ERROR_CODES`, without duplicating the new string literals.

[DESIGN] Change only the callback return annotation at
`app_main.py:226-231`:

```python
async def readiness(
    run_id: str,
    port: int,
    timeout_s: int,
) -> dayz_test_readiness.ReadinessResult:
```

The existing terminal serializer at `app_main.py:321-351` remains unchanged.

## 3. Viability tests — RED before product code

All test edits happen first in the isolated snapshot. The initial focused run
must fail for the expected missing result contract/codes, not for import,
environment or fixture mistakes.

### Readiness fixtures

1. Exact owned UDP binding returns `ReadinessResult(True, None)`.
2. Malformed top-level status returns `readiness_status_invalid`.
3. Missing, duplicated and non-running exact run each return
   `readiness_run_not_running`.
4. Malformed process list, zero/multiple server roles and invalid PID each
   return `readiness_server_process_invalid`.
5. Dead exact PID returns `readiness_server_pid_dead`.
6. Non-list UDP snapshot returns `readiness_udp_snapshot_invalid`.
7. Positive foreign owner returns `readiness_udp_foreign_owner`.
8. No binding and transient unknown owner remain retryable and finish as
   `readiness_timeout`.
9. Exceptions from initial/loop `monotonic`, request encoding, broker,
   `pid_exists`, `net_connections` and sleep return only
   `readiness_probe_failed`; their message text is absent from serialized
   outputs.
10. `CancelledError` from broker invocation and sleep propagates unchanged.
11. `ReadinessResult` rejects `ready=1`, `ready=None`, failure without a code,
    success with a code, unknown code and non-string code, all with
    `invalid_readiness_result`.
12. Existing strict input validation remains `invalid_dayz_test_readiness`.

### Worker fixtures

1. Every member of `READINESS_ERROR_CODES` is present in
   `WORKER_ERROR_CODES`; legacy `readiness_failed` remains present.
2. A typed readiness failure is propagated as its exact code.
3. The newly created run is still stopped and the terminal run ID is cleared
   after confirmed cleanup.
4. Failed cleanup retains only the exact new run ID and marks
   `cleanup_degraded=True`.
5. Readiness cancellation stops only the new run and becomes
   `operation_cancelled`.
6. Missing or malformed internal callback result fails closed as legacy
   `readiness_failed`.
7. Success still starts server, waits, then extends the same run with client.

### Terminal and public-schema fixtures

1. DZW1 success and failure payloads retain exactly:
   `cleanup_degraded,error_code,exit_code,ok,run_id`.
2. Each new readiness code survives the existing DZW1 `error_code` field.
3. Unknown codes and raw exception text are rejected/sanitized as before.
4. The compact MCP result retains exactly nine keys.
5. A new readiness code reaches the existing public `error_code` key without
   any additional field.
6. Source/bundle assertions reject `DZW2`, a sixth terminal key, a tenth public
   key and `str(error)` serialization.

## 4. Isolated non-Git workspace

`P:\DayZ_MCP_dev` is not an operational Git worktree; this plan must not
initialize or repair Git. Create one exclusive directory:

`C:\tmp\dayz-mcp-readiness-diagnostics-<UTC timestamp>`

Copy into the same relative layout only:

- `tools/dayz_mcp/`
- `tools/tests/`
- `tools/native-launchers/dayz-test-v1/`
- `tools/build_native_launcher.py`
- `tools/dependency-lock.json`
- `tools/pyproject.toml`
- `tools/schemas/`, `tools/fixtures/` and `tools/vendor/`
- The four read-only dependencies required by
  `tests.test_task9_launcher_migration`:
  `tools/approved-launchers.json`, `tools/approved-launchers.lock`,
  `tools/README-mcp.md`,
  `.superpowers/sdd/legacy-task9-launcher-migration-pyc.b64.txt` and
  `.superpowers/sdd/legacy-secure-launcher-prefx2-pyc.b64.txt`.

Never copy or read `tools/.dayz_mcp.key`, lease tokens, identities, receipts
from unrelated runs, `.venv-mcp`, `spike0`, `.codex_i*` or rolled-back launcher
directories.

Before edits, create a canonical JSON manifest of every copied file with
relative path, byte count and uppercase SHA-256. Rehash the seven planned
source/test targets against the research baseline. Abort on any mismatch.

Tests use host Python 3.14 with
`workdir=C:\tmp\dayz-mcp-readiness-diagnostics-<timestamp>\tools`:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest tests.test_dayz_test_readiness tests.test_dayz_test_worker tests.test_dayz_test_app tests.test_dayz_test_tool -v
```

The pre-edit canonical baseline is 37 tests, all passing. The snapshot must
reproduce that result before RED edits.

## 5. TDD execution sequence

1. Create and hash the isolated snapshot.
2. Reproduce the 37-test green baseline.
3. Edit only the four test files with `apply_patch`.
4. Run the new focused cases and retain the RED transcript plus exit code.
   Abort if failures are unrelated to the absent typed contract.
5. Edit only the three product files with `apply_patch`.
6. Run the four focused modules until green.
7. From `workdir=<snapshot>\tools`, run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest `
  tests.test_dayz_test_readiness `
  tests.test_dayz_test_worker `
  tests.test_dayz_test_app `
  tests.test_dayz_test_tool `
  tests.test_task9_launcher_migration -v
```

8. Rebuild the generated bundle only inside the snapshot:

```powershell
python build_native_launcher.py --offline --verify-reproducible
```

   Preserve pre/post full-tree manifests and require the three-way
   reproducibility receipt before continuing. This never targets the canonical
   bundle.
9. From the same `workdir=<snapshot>\tools`, run the H11
   security/regression modules from the existing plan. Use the project's
   read-only `.venv-mcp\Scripts\python.exe` when a module imports FastMCP; the
   cwd remains the snapshot so imports under test resolve to snapshot source:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest `
  tests.test_native_launcher_bundle `
  tests.test_native_launcher_backend `
  tests.test_secure_launcher `
  tests.test_task9_launcher_migration `
  tests.test_control_client `
  tests.test_native_launcher_transaction `
  tests.test_mcp_tools -v
```

10. Review the seven-file staged diff independently. Reject any unrelated line,
   duplicated code list, schema growth, secret/error-text exposure, timeout or
   ownership change.

The isolated phase does not call DayZ MCP, launch DayZ, write the canonical
registry or replace canonical generated artifacts.

## 6. Canonical rollout with rollback and CAS

Canonical rollout requires a fresh MCP client/session because live clients do
not hot-reload source or allowlists. The 120-second lease TTL is never assumed
to cover the whole rollout.

### 6.1 Original-state rollback unit

Before the first canonical edit, freeze under the isolated root:

- the seven source/test preimages;
- the complete recursive `dayz-test-v1/` tree and a canonical tree manifest;
- absence of sibling `dayz-test-v1.previous`;
- `approved-launchers.json`, its SHA-256 and the currently registered
  `dayz-test-v1` bundle/PE hashes;
- the receipt-directory listing/identities needed to identify transitions,
  without copying secret material.

The original complete bundle tree is the rollback unit. Never restore selected
generated members.

### 6.2 Lease A — source/test CAS only

1. From a fresh client, call `session_status`. Abort on `unauthorized`, unsafe
   foreign ownership, pending mutating commands or a non-terminal managed run.
2. Acquire Lease A through `session_acquire_wait`.
3. Rehash every canonical target and the original bundle/registry unit. Any
   preimage drift aborts without write.
4. Apply only the independently reviewed seven-file patch with `apply_patch`.
   Rehash and compare to the approved staged postimages.
5. Create the explicit `prebuild-tree` manifest. It must equal the original
   full-tree manifest at every path except
   `src/app_main.py`, whose hash must equal its approved seven-file postimage.
   No generated member may otherwise drift.
6. Release Lease A immediately and call `session_status`.
7. From `workdir=P:\DayZ_MCP_dev\tools`, run the 53-test focused source gate
   plus Task 9 read-only, with no lease held. Do not run closure/native-bundle
   gates against this deliberate prebuild state: `src/app_main.py` is the
   approved postimage while `app.pyz`, manifest, PE and build contract still
   represent the original bundle. Full H11/native gates run against the
   isolated candidate in §6.3 and again after the canonical swap in §6.4.
8. If a gate fails, acquire a new rollback lease, restore only seven-file
   postimages that still match this rollout, release and verify
   `session_status`. Never overwrite concurrent drift.

### 6.3 Isolated reproducible build — no lease, no canonical bundle mutation

After Lease A is released and canonical source tests pass, build to a new
output under the exclusive `C:\tmp` snapshot. Do not use the canonical bundle
as builder output:

```powershell
python tools/build_native_launcher.py `
  --output C:\tmp\dayz-mcp-readiness-diagnostics-<timestamp>\canonical-candidate\dayz-test-v1 `
  --offline `
  --verify-reproducible
```

The builder reads the approved canonical source postimages but replaces only
the isolated output. It must complete its three-way reproducibility check and
verify source/bundle byte equality. Create a recursive candidate-tree manifest
and compare it to the independently built snapshot candidate; all deterministic
bundle members and fingerprints must agree.

Require:

- every candidate file is accounted for;
- no candidate sibling `.previous` exists;
- bundled source hashes equal canonical source;
- three-way reproducibility is true;
- `verify_bundle(candidate)` and the staged native bundle suites pass;
- canonical `dayz-test-v1/` still equals `prebuild-tree`;
- canonical registry still equals its original preimage.

Failure here restores source under a newly acquired rollback lease; it never
touches the canonical bundle or registry.

### 6.4 Lease B — short bundle CAS and registry transition

1. Acquire Lease B immediately before copying the prepared candidate into the
   canonical parent.
2. Reverify the seven postimages, canonical `prebuild-tree`, registry preimage
   and absence of `dayz-test-v1.previous`.
3. Call `session_heartbeat`, then copy the isolated candidate to one unique
   sibling staging directory under
   `P:\DayZ_MCP_dev\tools\native-launchers\`. Verify its resolved absolute path
   remains under that parent and its recursive tree manifest equals the
   candidate manifest.
4. Call `session_heartbeat` again. Immediately before swap, reverify the
   canonical `prebuild-tree` and registry preimage.
5. Perform one bounded same-parent CAS sequence:
   - rename canonical `dayz-test-v1` to one unique owned rollback sibling;
   - rename the verified staging sibling to canonical `dayz-test-v1`;
   - if the second rename fails while Lease B is still authoritative, restore
     the owned rollback sibling immediately;
   - verify canonical equals the candidate-tree manifest and the rollback
     sibling equals `prebuild-tree`.
6. Call `session_heartbeat`. A failure before the swap aborts with canonical
   state unchanged. A failure after the swap stops all further mutation and
   closes as degraded; it never triggers a blind acquire or unauthorized
   rollback.
7. Run the short canonical native bundle gates and call heartbeat again.
   Require:
   - every file is accounted for;
   - `dayz-test-v1.previous` is absent;
   - bundled source hashes equal canonical source;
   - three-way reproducibility is true.
8. Reconfirm Lease B authority immediately before the registry mutation.
   The updater has no dry-run command. Invoke its fail-closed
   `rollback-last` directly; it must identify exactly one committed
   predecessor transition or abort without guessing.
9. Record the predecessor registry SHA returned by `rollback-last`. Then,
   from `workdir=P:\DayZ_MCP_dev\tools`, install:

```powershell
$registryHash = (Get-FileHash -Algorithm SHA256 approved-launchers.json).Hash
python -m dayz_mcp.launcher_registry_update install-dayz-test-v1 --expected-sha256 $registryHash
```

The install requires a committed receipt, exact reopen/verification of the new
bundle and a recorded registry postimage.

10. Copy the owned rollback sibling's already verified bytes into the
    host-direct rollback snapshot if they are not already present. Only after
    that copy rehashes to `prebuild-tree`, remove the exact owned sibling under
    the same verified native-launchers parent. The original full-tree snapshot
    remains durable.
11. Release Lease B immediately and call `session_status`. Record a clean
    session/ticket/command state.

### 6.5 Exact rollback state machine

If a failure occurs while Lease B remains authoritative, rollback immediately
under that same Lease B and maintain heartbeat. Never request a second lease
while Lease B is active. Acquire a fresh rollback lease only after the prior
lease was confirmed released and `session_status` is clean. If release or
authority is ambiguous, stop degraded without another acquire or mutation.
Every rollback restores only when current postimages match this rollout.

1. **Failure before canonical bundle CAS:** restore the seven source/test
   targets by CAS; registry and original bundle must already match.
2. **Failure after canonical bundle CAS but before withdrawing the old registry
   entry:** restore the complete original bundle tree only if the current tree
   equals the owned candidate-tree manifest; verify the original registry hash;
   then restore remaining source/test targets by CAS.
3. **Failure after the old entry was withdrawn but before the new entry was
   installed:** restore the complete original bundle tree by tree-CAS, then
   run `install-dayz-test-v1 --expected-sha256 <predecessor-registry-sha>` to
   reinstall the old bundle entry.
4. **Failure after the new entry was installed:** run `rollback-last` for the
   new transition, require the exact predecessor registry SHA, restore the
   complete original bundle tree by tree-CAS, then run
   `install-dayz-test-v1 --expected-sha256 <predecessor-registry-sha>` to
   reinstall the old entry.
5. Reopen/verify the old registered bundle, require final
   `approved-launchers.json` SHA-256 to equal the original preimage, and retain
   every committed/rolled-back receipt.
6. Restore remaining source/tests only where the current hash equals this
   rollout's own postimage. The full-bundle restore may already have restored
   `src/app_main.py`; verify rather than overwrite it again.
7. Release and call `session_status`.

No manual registry write is permitted. If updater rollback/install cannot
produce the required committed receipt or any tree/file CAS mismatches, stop
with degraded rollback evidence rather than overwrite unknown state. Loss of
lease authority after the canonical swap is also a degraded terminal
condition; no automatic reacquire/rollback is authorized.

## 7. Fresh-client diagnostic smoke

After canonical rollout, start a genuinely new Codex/MCP task. Do not reuse an
old authenticated client.

1. `session_status`.
2. Freeze LFPowerGrid candidate, stage, baseline, config and worktree hashes.
3. Acquire through the managed queue.
4. Execute the exact approved `dayz_test_run` preflight, then one controlled
   `mode="all"` B attempt.
5. If a run ID is returned, retain it exactly and stop only that run with
   `dayz_test_stop(run_id)`.
6. Release and immediately call `session_status`.
7. The result is valid diagnostic evidence only if it carries one closed
   readiness code, has no secret/private fields, cleanup is accounted for and
   all frozen LFPowerGrid invariants still match.
8. Use the typed cause to correct or adjudicate the environment. Do not alter
   timeouts or retry blindly.
9. Run C and GS only when B passes the existing Task 7 contract and the
   pre-existing user authorization remains applicable.

## 8. Abort conditions

Abort without further mutation on:

- any canonical/staged preimage drift;
- unauthorized MCP status;
- inability to acquire the lease normally;
- RED failures caused by fixture/environment mistakes;
- public/DZW1 shape or version growth beyond the approved `error_code`
  allowlist expansion;
- exception/private-data exposure;
- timeout, cadence, ownership, stop or transport changes;
- non-reproducible build;
- registry updater non-committed receipt or failed reopen;
- rollback target hash not matching this rollout's own postimage;
- any LFPowerGrid candidate/baseline/worktree hash drift.

## 9. Receipts and closeout

Retain:

- research and plan hashes;
- canonical and snapshot manifests;
- baseline, RED, focused-green, H11-regression and native-bundle transcripts;
- seven-file diff and independent review;
- rollback manifest;
- three-way build/reproducibility hashes;
- registry updater committed receipt;
- fresh-client MCP result, exact run ID/stop receipt and final
  `session_status`;
- LFPowerGrid invariant hashes before and after.

Update DayZ_MCP BUG-055 with the instrumented cause only after a valid
fresh-client result. Update the LFPowerGrid execution notes with the Task 7
outcome. Do not claim the LFPowerGrid goal complete until the existing client
and GS gates are valid and the capacity objective is actually proven.

## 10. R22/R26 review

- **DPF trace:** every product line is limited to H11 failure fidelity and
  continues to serve the H-group intent of safe, owner-scoped lifecycle.
- **Simpler path:** reusing and deliberately expanding the existing
  `error_code` allowlist is strictly smaller and safer than DZW2 or a new
  public field.
- **API risk:** all touched signatures and serializers were verified at the
  cited source lines; no external API is invented.
- **Validation completeness:** positive, negative, cancellation, cleanup,
  schema, confidentiality, reproducibility, registry and real-smoke gates are
  defined before implementation.
- **Rollback:** legacy `readiness_failed` remains accepted; rollback is
  receipt-backed, ordered and compare-matching.
- **Residual unknown:** the actual B5 cause remains unknown until the
  fresh-client instrumented smoke. The plan does not promote that unknown to a
  fact.

No architecture, persistence/network format, product intent or LFPowerGrid
scope change is introduced by this plan.
