# DayZ MCP readiness targeted-status fix

**Status:** proposed from confirmed B6 runtime evidence  
**Objective:** prevent the private readiness probe from requesting the unbounded
global lifecycle history; query only the managed run already being evaluated.

## Evidence and scope

B6 returned `readiness_status_invalid` before Mission/client. The authoritative
receipt is:

`P:\LFPowerGrid_dev\_validation\server-footprint-a2-20260725\client-smoke\execution-b6-c6-instrumented-20260726.json`

SHA-256:
`2CF0598532679AD5BE9F7681D161594D037A4AF2065CBB693495F292A912DAAE`.

The observed manifest contains 439 exited runs and is 236,386 bytes. Readiness
currently sends global status (`dayz_test_readiness.py:129-134`), while the
broker accepts at most 65,536 bytes
(`native_broker_protocol.py:16`; generated `launcher.cpp:10,731-753`). The
broker converts an oversized child failure to the closed
`broker_child_failed` envelope (`launcher.cpp:1373-1397`); `_server_pid`
classifies that envelope as `readiness_status_invalid`
(`dayz_test_readiness.py:64-72`).

The server already implements exact-run filtering before its response is
serialized (`loopback.py:1327-1338`), its broker payload is explicitly tested
(`test_native_broker_protocol.py:50-62`), and the worker already uses the same
targeted status contract (`dayz_test_worker.py:403-413`).

### Product files

- Modify:
  `P:\DayZ_MCP_dev\tools\dayz_mcp\dayz_test_readiness.py`
- Modify:
  `P:\DayZ_MCP_dev\tools\tests\test_dayz_test_readiness.py`
- Regenerate as one indivisible unit:
  `P:\DayZ_MCP_dev\tools\native-launchers\dayz-test-v1`
- Transition only through the approved updater:
  `P:\DayZ_MCP_dev\tools\approved-launchers.json`
- Align post-registry expectations after the new PE is known:
  `P:\DayZ_MCP_dev\tools\tests\test_secure_launcher.py`
  and
  `P:\DayZ_MCP_dev\tools\tests\test_task9_launcher_migration.py`

No other source, schema, timeout, cadence, ownership, error taxonomy,
transport, lifecycle, LFPowerGrid artifact, PBO, config, process, or persistent
format is in scope.

## Frozen canonical preimages

| Path | SHA-256 |
|---|---|
| `dayz_test_readiness.py` | `62B81556FBCA40C45D14E297336DF30BF85734CA9467A8B93F82A44157B3C06C` |
| `test_dayz_test_readiness.py` | `F9081DB2B10FF4C98E432680D5F530F9FE1B7BF9D7E4824CA7FA399DEB9D1399` |
| `test_secure_launcher.py` | `E912AE775D28326C15010C69C630DF3CDEDA00C5F8C7C6669381CBE7C222AF55` |
| `test_task9_launcher_migration.py` | `97E53FD30803AF2BD69F68AF51921C29C166E9FD0C06290A6425E05F13838E95` |
| `approved-launchers.json` | `960EF6AE3AFE9DE13F01D0E327CE890CB1C8D1C1B4CAE0F69DD48819299CE8CF` |
| `app.pyz` | `B955247C097C81F6A6078D6139957F49D3B644950C729D0301F9EF5AFA398BB8` |
| `closure-manifest.json` | `E6DEAE6D291391D4073D098ACAA07E8A6A94D007ABF4398B69BA947EF112D14E` |
| `dayz-test-launcher.exe` | `17B43C6726CBDAAA866F08E0873E979CD96DD81B27EC084C2B94D581EF9825EF` |

## Exact change

### Test first

Strengthen
`test_waits_then_accepts_only_exact_server_pid_binding`
(`test_dayz_test_readiness.py:96-109`) so every recorded broker frame must have
the exact payload below:

[EXACT]

```python
{
    "command": "status",
    "launch_operation_id": None,
    "run_id": RUN_ID,
}
```

Run that one test before product code. Expected RED: current frames contain
`run_id=None`.

### Product

Replace only the final value in
`dayz_test_readiness.py:129-132`:

[EXACT]

```python
frame = native_broker_protocol.encode_request(
    native_broker_protocol.BrokerKind.LIFECYCLE_CLI,
    {"command": "status", "launch_operation_id": None, "run_id": run_id},
)
```

The local `run_id` has already passed `_valid_uuid4` at
`dayz_test_readiness.py:114-125`; no new validation or API is required.

## Phase 1 — isolated TDD and build

Isolation root:

`C:\tmp\dayz-mcp-readiness-targeted-status-20260726T232200Z`

1. Require the root absent. Copy the prior sanitized snapshot
   `C:\tmp\dayz-mcp-readiness-diagnostics-20260726T220925Z\tools`
   into the new root; overlay the four current canonical test/source files,
   current registry/lock/transition dependencies, and current 57-file bundle.
   Do not copy `.venv-mcp`, keyfiles, daemon/runtime state, credentials or
   LFPowerGrid artifacts.
2. Create a full tree manifest and a separate rollback directory with exact
   preimages. Record paths, sizes and SHA-256.
3. Apply only the test assertion. Run the exact focused test and require RED
   for `run_id=None`; fixture/setup errors do not count.
4. Apply the one-line product change. Require the focused test GREEN, then run
   the complete readiness/worker/app/tool focused set from the preceding
   instrumentation plan.
5. Build only inside the isolated root with:

[EXACT]

```powershell
python build_native_launcher.py --offline --verify-reproducible
```

   Use the project's read-only
   `P:\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe`.
   Require three-way reproducibility and direct bundle verification.
6. Record the new PE hash. In the isolated tree only, transition its registry
   through `launcher_registry_update rollback-last` followed by
   `install-dayz-test-v1 --expected-sha256 <actual predecessor registry SHA>`.
   Align the two isolated expected-PE fixtures to that new hash.
7. Run H11:

[EXACT]

```powershell
python -m unittest `
  tests.test_native_launcher_bundle `
  tests.test_native_launcher_backend `
  tests.test_secure_launcher `
  tests.test_task9_launcher_migration `
  tests.test_control_client `
  tests.test_native_launcher_transaction `
  tests.test_mcp_tools -v
```

   Require 130/130. Also run the focused readiness/worker/app/tool suite and
   require every test green.
8. Independent review must confirm the handwritten product diff is one line,
   the readiness test diff only tightens exact-run framing, generated output
   is reproducible, and no secret/error text or public/schema change exists.

## Phase 2 — canonical source compare-and-swap

Use a fresh MCP session.

1. `session_status`; stop on unauthorized or any non-clean state.
2. Acquire Lease A with purpose
   `dayz-mcp-readiness-targeted-status-source-test`.
3. Revalidate every frozen preimage and create durable non-overwriting
   backups under the isolation root.
4. Apply with `apply_patch` only:
   `dayz_test_readiness.py` and `test_dayz_test_readiness.py`.
5. Verify their postimages are byte-identical to the reviewed isolated
   postimages. Registry and generated bundle must remain at their frozen
   preimages.
6. Release immediately and require clean `session_status`.

No build, registry mutation or DayZ lifecycle occurs in Lease A.

## Phase 3 — candidate build and canonical bundle/registry CAS

1. Outside a lease, use an isolated copy whose two source/test postimages are
   byte-identical to canonical. Build the candidate into that isolated copy;
   never point the builder at the canonical bundle. Verify its full 57-file
   manifest and reproducibility receipts.
2. Open a fresh MCP session and acquire Lease B with purpose
   `dayz-mcp-readiness-targeted-status-bundle-registry`.
3. Revalidate canonical source postimages, frozen registry/bundle/fixture
   preimages, the isolated candidate and all rollback backups.
4. Copy the candidate to a unique same-parent stage, verify 57/57, and perform
   a same-parent rename CAS: canonical bundle to an owned rollback sibling,
   verified stage to canonical. Maintain heartbeat at no more than 60-second
   intervals.
5. Before registry transition, run direct bundle verification and H11 against
   the still-old registry/fixture expectation. Require 130/130.
6. Reconfirm authority. Use only the updater:
   `rollback-last`, record the predecessor registry SHA, then
   `install-dayz-test-v1 --expected-sha256 <predecessor SHA>`. Reopen and
   verify the registry/bundle.
7. Under the same authoritative lease, replace the previous PE SHA exactly
   once in each canonical PE fixture with the newly registered PE SHA. Verify
   each two-file postimage matches the reviewed isolated fixture.
8. Run post-registry focused tests and full H11. Require 2/2 fixture tests and
   130/130 H11. Registry, PE and source hashes must remain stable through the
   test run.
9. Delete only owned stage/rollback siblings after exact verification; retain
   durable rollback backups and receipts.
10. Release immediately; require clean `session_status`.

### Rollback state machine

While authority remains valid:

- Before bundle swap: restore only source/test preimages if the rollout is
  abandoned.
- After bundle swap but before registry mutation: CAS the verified old bundle
  back, then restore source/test preimages.
- After old registry entry is removed: restore old bundle and reinstall the
  old entry with the recorded predecessor SHA.
- After new entry is installed: updater `rollback-last`, restore the old
  bundle and both old fixture preimages, reinstall the old entry with the
  predecessor SHA, require the original registry SHA, then restore the two
  source/test preimages.

If authority is lost after any mutation, perform no blind reacquire or further
mutation. Record exact state and stop degraded.

## Phase 4 — fresh runtime discriminator

Only after independent post-rollout review:

1. Use a new MCP session; call `session_status` and `bridge_status`.
2. Revalidate LFPowerGrid candidate/stage/baseline/worktree and recreate the
   exact owned 646-byte Task 7 config under a short low-level lease.
3. Run B7 once with `dayz_test_run(mode="all")`; do not pre-acquire around the
   public tool.
4. If B7 succeeds through Mission/client, stop its exact `run_id` and run C7
   once. If B7 fails, preserve the new specific code and do not run C7.
5. Stop exact runs, remove only the owned config under a new lease, and finish
   with clean `session_status`/`bridge_status`.

No automatic second retry, GS, integration, publication or LFPowerGrid source
change is authorized by this plan.

## Exit criteria

- Focused test demonstrates RED before product and GREEN after.
- Handwritten production diff is exactly one value: `None` to validated
  `run_id`.
- Isolated and canonical focused suites are green.
- Reproducible bundle verifies 57/57.
- Pre- and post-registry H11 are each 130/130.
- Registry and both PE fixtures name the exact new PE.
- Independent review reports no material finding.
- Runtime B7 no longer returns `readiness_status_invalid` due the global
  history overflow.
- All leases/runs/config cleanup end terminal and clean.

## Commit and handoff

`P:\DayZ_MCP_dev` is not a Git repository; do not fabricate a commit. Seal
full manifests, logs and receipts under the isolation root, update the
DayZ_MCP research/bug ledger and LFPowerGrid Task 7 evidence, and preserve all
rollback backups. LFPowerGrid remains unintegrated.
