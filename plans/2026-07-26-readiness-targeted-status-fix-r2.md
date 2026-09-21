# DayZ MCP readiness targeted-status fix — R2

**Status:** revised after independent plan review; implementation is blocked
until a second independent review returns PASS with no material finding.

**Objective:** prevent the private readiness probe from requesting the
unbounded global lifecycle history. Query only the already-managed run being
evaluated and preserve infrastructure-error classification when the loopback
targeting layer receives an error envelope.

## 1. DPF trace and authority

This plan serves `P:\DayZ_MCP_dev\product-spec.md:120-138`:

- Phases 1–3 trace to Intent H and H11: first-class managed test execution must
  remain bounded, fail-closed, exact-run-owned and auditable.
- Phase 4 is the H11 runtime discriminator needed to resume the already
  authorized LFPowerGrid A2 Task 7 contract. It does not alter LFPowerGrid.

The user authorized autonomous safe continuation and Task 7 A2. This plan does
not authorize GS before a valid B7/C7 client pair, integration, publication,
baseline replacement, LFPowerGrid source/PBO edits, daemon restart, direct
process termination, secret access, or deletion of evidence/worktrees.

## 2. Confirmed cause

The authoritative B6 receipt is:

`P:\LFPowerGrid_dev\_validation\server-footprint-a2-20260725\client-smoke\execution-b6-c6-instrumented-20260726.json`

SHA-256:
`2CF0598532679AD5BE9F7681D161594D037A4AF2065CBB693495F292A912DAAE`.

The runtime manifest had 439 exited runs, 236,386 bytes, SHA-256
`2C050697F4B991F2FBFB4195073AB0A1DEA23728314EEB1A1CBFEC9D9B65B8D7`.
Readiness sends global status at
`tools/dayz_mcp/dayz_test_readiness.py:129-134`. The native frame cap is 65,536
bytes (`tools/dayz_mcp/native_broker_protocol.py:16` and generated
`tools/native-launchers/dayz-test-v1/src/launcher.cpp:10,731-753`).
The broker maps an oversized child response to `broker_child_failed`
(`launcher.cpp:1373-1397`), which `_server_pid` currently maps to
`readiness_status_invalid` (`dayz_test_readiness.py:64-72`).

The complete targeted path already exists:

- targeted lifecycle frames round-trip:
  `tools/tests/test_native_broker_protocol.py:44-62`;
- the private child forwards a non-null `run_id`:
  `tools/native-launchers/dayz-test-v1/src/app_main.py:268-296`;
- loopback filters before serialization:
  `tools/dayz_mcp/loopback.py:1327-1338`;
- the worker already uses exact-run status:
  `tools/dayz_mcp/dayz_test_worker.py:403-413`.

One edge remains: loopback copies an upstream status error and then adds
`runs=[]`. Without an envelope guard, readiness can misclassify that
infrastructure failure as `readiness_run_not_running`. Error taxonomy must
remain fail-closed and stable.

## 3. Exact scope

### Product source

- Modify only:
  `P:\DayZ_MCP_dev\tools\dayz_mcp\dayz_test_readiness.py`.

### Test source

- Modify:
  `P:\DayZ_MCP_dev\tools\tests\test_dayz_test_readiness.py`.
- Modify:
  `P:\DayZ_MCP_dev\tools\tests\test_dayz_test_app.py`.
- Modify:
  `P:\DayZ_MCP_dev\tools\tests\test_lifecycle_http.py`.
- Execute unchanged:
  `P:\DayZ_MCP_dev\tools\tests\test_native_broker_protocol.py`.

### Generated/registry unit

- Regenerate as one indivisible unit:
  `P:\DayZ_MCP_dev\tools\native-launchers\dayz-test-v1`.
- Transition only with:
  `P:\DayZ_MCP_dev\tools\dayz_mcp\launcher_registry_update.py`.
- Update the exact PE expectation once in:
  `P:\DayZ_MCP_dev\tools\tests\test_secure_launcher.py`;
  `P:\DayZ_MCP_dev\tools\tests\test_task9_launcher_migration.py`.
- Transition:
  `P:\DayZ_MCP_dev\tools\approved-launchers.json`.

No other source, schema, timeout, cadence, public result field, ownership
rule, lifecycle rule, persistent format, LFPowerGrid artifact, config, or
process is in scope.

## 4. Frozen canonical preimages

| Path | Bytes | SHA-256 |
|---|---:|---|
| `tools/dayz_mcp/dayz_test_readiness.py` | 5518 | `62B81556FBCA40C45D14E297336DF30BF85734CA9467A8B93F82A44157B3C06C` |
| `tools/tests/test_dayz_test_readiness.py` | 11476 | `F9081DB2B10FF4C98E432680D5F530F9FE1B7BF9D7E4824CA7FA399DEB9D1399` |
| `tools/tests/test_dayz_test_app.py` | 4594 | `008FE53989BB4953FA9FB98A6026B3B7EDF2518E9C94D49C35172911F26E116E` |
| `tools/tests/test_lifecycle_http.py` | 16975 | `3AB766A4C30F29D21E791D4233FABD57E1F575A2172D5B9CCD4475E766483D61` |
| `tools/tests/test_native_broker_protocol.py` | 5729 | `9402804DA6691E0839AFC58DB6124707311786C897B84A3B6914DCE708FF595A` |
| `tools/tests/test_secure_launcher.py` | 26481 | `E912AE775D28326C15010C69C630DF3CDEDA00C5F8C7C6669381CBE7C222AF55` |
| `tools/tests/test_task9_launcher_migration.py` | 13281 | `97E53FD30803AF2BD69F68AF51921C29C166E9FD0C06290A6425E05F13838E95` |
| `tools/build_native_launcher.py` | 49409 | `0169C2E942990F4DBF746717C7A1E121434EBACE68F5AFC3FE8446A48F5FC813` |
| `tools/dependency-lock.json` | 6272 | `7CC703836187D686D3940AA040B9AD728DDD792538BCABECAB524F679C2789F4` |
| `tools/dayz_mcp/launcher_registry_update.py` | 15922 | `818F649716EE8ED5543CE0CCA8FCA242B86FE251DD7EE7CC90B433FA55405705` |
| `tools/dayz_mcp/registry_lock.py` | 4017 | `A488390D69A376B9C0B9AC9EA922D1C7714F485D7D824E7EA7349A4F9396E00A` |
| `tools/dayz_mcp/launcher_registry.py` | 15992 | `8A614C0423E1C8C992A4A386994CFFD381870FCC27F536EBDAE4D31C574A6191` |
| `tools/dayz_mcp/native_bundle.py` | 33457 | `BF7589833907B3242F79062C1B5E0D5A1D16EE5E7BA50AD7A98F0413C3A54D10` |
| `tools/approved-launchers.lock` | 35 | `7C33FD5DA8BCBF37B0D0E72510BED035BA5AC1684E2DB11CF785D554EB232AE4` |
| `tools/approved-launchers.json` | 485 | `960EF6AE3AFE9DE13F01D0E327CE890CB1C8D1C1B4CAE0F69DD48819299CE8CF` |

The active current transition is
`approved-launchers.receipts\61b7dbf7-0dde-4ad3-a76b-d7a39cf6aefa`:

| Receipt | SHA-256 |
|---|---|
| `from-registry.json` | `330B04E8D7AB06E7EE850326C1CAE180F119ED21486745DC0EC9BAAE203C653B` |
| `prepared.json` | `843F73654CCC937DA88AE5F222ED5103D9B15C39E8A4D8E261718556B664E934` |
| `committed.json` | `537202D62EAA23CC2910A23D01EE34327662C9EF7B8A42CA1EBDC77C0EC8F7F8` |

`rolled-back.json` is absent for that transaction.

### Full bundle closure

The canonical bundle contains exactly 57 files. Freeze the complete sorted
manifest as UTF-8 lines:

`relative/path<TAB>byte_count<TAB>uppercase_sha256<LF>`

sorted by case-folded relative path. Its current receipt is:

- file count: `57`;
- manifest byte count: `5308`;
- manifest SHA-256:
  `33C46603EF42A9660287AB97F900AD173543C3A226A2FC852BD4BE4F0DEDEE28`.

The current key bundle files are:

| File | SHA-256 |
|---|---|
| `app.pyz` | `B955247C097C81F6A6078D6139957F49D3B644950C729D0301F9EF5AFA398BB8` |
| `build-contract.json` | `A575A35231E1E1127A598B347176BEA802BB01F81F998C7B422C8CC13B22D04E` |
| `closure-manifest.json` | `E6DEAE6D291391D4073D098ACAA07E8A6A94D007ABF4398B69BA947EF112D14E` |
| `dayz-test-launcher.exe` | `17B43C6726CBDAAA866F08E0873E979CD96DD81B27EC084C2B94D581EF9825EF` |
| `reproducibility.json` | `A3B71B652EE543DEC2979D840930549D634B298F916F0D99181282FC85C09277` |
| `request-policy.json` | `BD7C8463F89C5D2EA6C9F7D1A416CF74983CE1AC4949FFA661C5E08FDAA49EAF` |
| `worker-runtime.json` | `96BA258ED42018E3C3ED8AE80E8525FBFEC6103CCA9638DCC58653600586C113` |
| `src/app_main.py` | `F3B1C6BB0D9C6C1DF520B7803965EFF31718D8DC673C29A44B639D1C08D483AD` |
| `src/launcher.cpp` | `D0C9927815C15CF33B33F2999A983D572DB6156ECB737EDAE2A540E29CC57A0D` |

The closure manifest has 54 `kind=bundle` entries. Its exact sorted
`path<TAB>size<TAB>sha256<LF>` digest is
`0C3A57A2F459333A9ECC429B7161C1CB69A2C2A5FAE4212CB56AF160A1E1370B`.
Its 29 `kind=external` entries are also hard preconditions; their exact sorted
digest under the same format is
`AB64CAEBFD089EEE496B06FC3948DAE0EF227610DC5D25FE8327928E606ADC2B`.
Every external path, size, hash and file identity in the closure manifest must
revalidate before each build.

`P:\DayZ_MCP_dev\tools\native-launchers\dayz-test-v1.previous` must remain
absent before and after every build/swap.

## 5. Viability tests and exact product change

Baseline module counts, captured separately before edits:

| Module | Current | Required after |
|---|---:|---:|
| `tests.test_dayz_test_readiness` | 10 | 10 |
| `tests.test_dayz_test_app` | 3 | 4 |
| `tests.test_lifecycle_http` | 6 | 7 |
| `tests.test_native_broker_protocol` | 4 | 4 |

The existing four-module readiness/worker/app/tool gate is 48 tests. Adding the
two separately exercised chain modules gives 58 before and 60 after.
H11 remains its own unchanged 130-test gate.

### 5.1 Test first

Strengthen
`test_waits_then_accepts_only_exact_server_pid_binding`
(`test_dayz_test_readiness.py:96-109`) so every recorded broker request equals:

[EXACT]

```python
{
    "command": "status",
    "launch_operation_id": None,
    "run_id": RUN_ID,
}
```

Add to the existing malformed-status table, without changing test count:

[EXACT]

```python
({"error": "broker_child_failed", "runs": []}, "readiness_status_invalid"),
({"ok": False, "runs": []}, "readiness_status_invalid"),
```

Before product code, run only the two affected readiness test methods. Both
must fail for the expected `run_id=None` / wrong taxonomy, not for imports,
fixtures or environment.

Add a test in `test_dayz_test_app.py` that feeds a canonical targeted status
frame to `_lifecycle_main`, replaces the transport with a recorder, and proves
the request body contains the exact `run_id`, identity and fixture lease while
the two environment variables have already been removed. It performs no
network I/O and uses no real credential.

Add a test in `test_lifecycle_http.py` whose fake status contains two runs and
whose POST body requests one exact run. Require the response to contain only
that run and retain non-run status fields. This exercises
`loopback.py:1327-1338`.

Run the unchanged targeted-status round-trip test in
`test_native_broker_protocol.py:44-62`.

### 5.2 Product

Immediately after the top-level dict check in `_server_pid`
(`dayz_test_readiness.py:64-72`), add:

[EXACT]

```python
if response.get("error") is not None or response.get("ok") is False:
    return None, "readiness_status_invalid"
```

Replace only the final value in the readiness frame at
`dayz_test_readiness.py:129-132`:

[EXACT]

```python
frame = native_broker_protocol.encode_request(
    native_broker_protocol.BrokerKind.LIFECYCLE_CLI,
    {"command": "status", "launch_operation_id": None, "run_id": run_id},
)
```

The local `run_id` has already passed `_valid_uuid4` at
`dayz_test_readiness.py:114-125`. No new API, field, timeout, retry or public
error code is introduced.

## 6. Phase 1 — isolated TDD, build and rollback drill

Isolation root:

`C:\tmp\dayz-mcp-readiness-targeted-status-20260726T232200Z`

1. Require the root absent. Copy the prior sanitized snapshot
   `C:\tmp\dayz-mcp-readiness-diagnostics-20260726T220925Z\tools` to the same
   relative layout and overlay every frozen current file and the exact 57-file
   bundle. Never copy `.venv-mcp`, daemon/runtime state, keyfiles, credentials,
   lease material or LFPowerGrid artifacts.
2. Write complete preimage and rollback manifests under the isolation root.
   Record paths, sizes, SHA-256 and absence assertions. Durable preimages are
   never overwritten.
3. Apply only the readiness test changes. Run the two exact RED methods and
   retain command, stdout/stderr and exit code.
4. Add the app/HTTP chain tests. They should already pass because the
   downstream targeting path exists.
5. Apply only the two localized product changes in §5.2. Require the two RED
   tests GREEN.
6. Run each four baseline-count module separately and require `10/10`,
   `4/4`, `7/7`, `4/4`. Then run:

[EXACT]

```powershell
python -m unittest `
  tests.test_dayz_test_readiness `
  tests.test_dayz_test_worker `
  tests.test_dayz_test_app `
  tests.test_dayz_test_tool `
  tests.test_lifecycle_http `
  tests.test_native_broker_protocol -v
```

   Require 60/60.
7. Build only in the isolated root:

[EXACT]

```powershell
python build_native_launcher.py --offline --verify-reproducible
```

   Use the read-only interpreter
   `P:\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe`. Require three-way
   reproducibility, direct bundle verification, exactly 57 files, fresh full
   manifest/closure/external digests and `.previous` absent.
8. Bootstrap isolated registry history; never copy canonical receipts into an
   operational isolated receipts directory:
   - initialize the isolated registry from the exact bytes of active
     `61b7dbf7...\from-registry.json` (SHA
     `330B04E8D7AB06E7EE850326C1CAE180F119ED21486745DC0EC9BAAE203C653B`);
   - use the updater's path-parameterized `_install_transition` verified by
     `tests/test_launcher_registry_update.py:43-58` to install the old copied
     bundle and create an identity-bound isolated receipt;
   - verify old isolated registry, entry and bundle;
   - invoke `_rollback_transition` on those isolated paths and require exact
     predecessor bytes/hash;
   - install the new isolated bundle from that predecessor SHA and verify the
     new identity-bound receipt.
9. In the isolated tree only, replace the old expected PE hash exactly once in
   each PE fixture with the new PE hash. Run H11:

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

   Require 130/130. Re-run the 60-test focused gate.
10. Independent review must report no material finding and confirm:
    - only one production file changed;
    - exact-run targeting and error-envelope fail-closed guard match §5.2;
    - tests cover protocol → child → HTTP filter → readiness;
    - isolated receipts are identity-bound to the isolated registry;
    - build reproducibility and 57-file closure are exact;
    - no secret, raw exception, public/schema, timeout or ownership change.

## 7. Phase 2 — canonical source/test compare-and-swap

Use a fresh MCP agent/session.

### Maintenance-window precondition

Before the first canonical `dayz_mcp/*.py` edit, the root coordinator must
announce and seal a source-maintenance window:

- enumerate all known live collaborator agents;
- notify every live agent that can use DayZ MCP/tools and require it to remain
  idle for the source CAS;
- wait for acknowledgement or completed/idle state and record the agent
  snapshot plus UTC window start;
- run no concurrent DayZ gate, lifecycle action, registry/build rollout or
  other canonical source edit until Lease A is released.

`session_status` does not prove that idle MCP clients are absent. Therefore,
all pre-window MCP clients are treated as disposable: a client that fails
closed after the source edit is not reused or restarted, and Phases 3–4 use
fresh tasks/sessions. If the known-agent drain cannot be established, Phase 2
does not begin.

1. Call `session_status`. Stop on `unauthorized`, owner/queue/fault/cleanup
   residue, pending command, retail quarantine or active run.
2. Without a lease, revalidate all frozen
   source/test/builder/updater/registry/bundle preimages and create
   non-overwriting durable backups under the isolation root. While source and
   old bundle are still mutually consistent, run the current focused gate
   58/58 and H11 130/130 and seal both transcripts.
3. Call `session_status` again, then acquire Lease A with purpose
   `dayz-mcp-readiness-targeted-status-source-test`. Immediately rehash the
   four mutation targets and require their frozen preimages.
4. Apply with `apply_patch` only the reviewed postimages of:
   - `dayz_test_readiness.py`;
   - `test_dayz_test_readiness.py`;
   - `test_dayz_test_app.py`;
   - `test_lifecycle_http.py`.
5. Require every canonical postimage byte-identical to the reviewed isolated
   postimage. Registry, bundle and PE fixtures remain at frozen preimages.
   Reconfirm Lease A and release immediately; no test suite runs while the
   lease is held.
6. Require clean `session_status`, then run the new source-focused gate 60/60
   without a lease. Do not run bundle-closure H11 during this intentional
   source/new → bundle/old interval.

If Lease A authority is lost after a source/test mutation, do not blindly
reacquire and do not restore under a new identity. Seal exact current hashes
as `UNRECONCILED`, wait for natural reconciliation, then use a fresh agent and
fresh `session_status` to determine authoritative state. Phase 3 remains
blocked until the state is reconciled and independently reviewed.

If any patch, postimage or rehash gate fails after the first mutation while
the same Lease A is still authoritative, roll back under that lease:

- for each of the four targets, leave an exact frozen preimage untouched;
- restore an exact owned reviewed postimage to its frozen preimage using the
  inverse `apply_patch`;
- never overwrite a third/unknown hash; classify it as a CAS collision and
  `UNRECONCILED`;
- require all four frozen preimage hashes plus unchanged registry/bundle
  hashes before releasing;
- release immediately and require clean `session_status`.

If the lease ceases to be authoritative at any point in that branch, stop all
restoration and use the authority-loss branch above.

No build, registry mutation or DayZ lifecycle occurs in Lease A.

## 8. Phase 3 — canonical bundle/registry CAS

1. Outside a lease, create a fresh isolated candidate from canonical
   source/test postimages, revalidate every build input and run Phase 1 build,
   60/60, isolated registry drill and H11 130/130 again. Seal full manifests.
2. Without a lease, run canonical focused 60/60. Verify
   the still-old registered bundle directly against the frozen old 57-file
   tree, old closure/external manifests, old registry entry and old PE
   fixtures. Do not run bundle-closure H11 while canonical source is new and
   the registered bundle is intentionally still old; the last internally
   consistent old-state H11 is the sealed 130/130 run from Phase 2.2.
3. Use a fresh MCP agent/session. Require clean `session_status`, then acquire
   Lease B with purpose
   `dayz-mcp-readiness-targeted-status-bundle-registry`.
4. Immediately revalidate canonical source postimages, frozen old
   registry/bundle/fixture preimages, active receipt identity, candidate,
   backups, full 57-file old and new manifests and `.previous` absence.
5. Copy the reviewed candidate to a unique same-parent stage, verify 57/57,
   then use a same-parent rename CAS:
   - canonical old bundle → owned rollback sibling;
   - reviewed stage → canonical bundle.
   Maintain lease heartbeat at intervals below 60 seconds.
6. Run direct bundle verification only. Do not run registry-dependent H11
   while bundle, registry and fixture expectations are intentionally in the
   short transitional state.
7. Reconfirm authority. Use only the canonical updater:
   - `rollback-last`;
   - require predecessor registry SHA
     `330B04E8D7AB06E7EE850326C1CAE180F119ED21486745DC0EC9BAAE203C653B`;
   - `install-dayz-test-v1 --expected-sha256 <that predecessor SHA>`;
   - reopen and verify registry identity, root and exact new PE.
8. Under the same authoritative lease, replace the old PE SHA exactly once in
   each canonical PE fixture with the registered new PE SHA. Require both
   postimages byte-identical to reviewed isolated fixtures.
9. Run the two PE fixture tests, focused 60/60 and H11 130/130. Registry, PE,
   source and bundle hashes must remain stable across the run.
10. Delete only the owned temporary stage/rollback siblings after exact
    verification. Preserve durable rollback backups and receipts.
11. Release immediately and require clean `session_status`.

### Rollback state machine

While the same lease authority remains valid:

- before bundle swap: restore only source/test preimages if abandoning the
  rollout;
- after bundle swap but before registry rollback: CAS old bundle back, then
  restore source/test preimages;
- after registry rollback to predecessor: restore old bundle, reinstall its
  entry from the predecessor SHA, restore old fixture/source/test preimages;
- after new entry installation: updater `rollback-last`, restore old bundle
  and old PE fixtures, reinstall old entry from predecessor SHA, require the
  original registry SHA, then restore source/test preimages.

Every rollback ends with direct bundle verification, focused baseline counts,
H11 130/130 and clean session state.

If authority is lost after any mutation, perform no blind reacquire and no
further mutation. Seal exact hashes/identities as `UNRECONCILED`, wait for
natural reconciliation and require a fresh session plus independent review.

## 9. Phase 4 — fresh B7/C7 runtime discriminator

Only after an independent post-rollout review returns PASS:

1. Use a fresh MCP agent/session; call `session_status` and `bridge_status`.
   Require no owner, queue, fault, cleanup residue, pending command, retail
   quarantine or active run.
2. Revalidate:
   - candidate and stage: 112,111,767 bytes, SHA
     `3205A1D881A5E5484E53A49F75CFABD2D343807ED6F187624B528B6AE1B34605`;
   - baseline: 112,121,685 bytes, SHA
     `143B8309405AC9A1B97A5B4E006F7E95483E9E7EBCBC18852E5A147221BCC150`;
   - A2 worktree HEAD
     `f83a9a8bd2967753f13864d5c42baabf37dd6a8c`, clean;
   - temporary config absent.
3. Create a new B7/C7 contract before any mutation. It must cite immutable
   parent contract
   `retry-b6-c6-instrumented-contract-20260726.json`, SHA
   `6E656B0343E790C5C038AAEA716A4AE97D6C68E825DCD3165A9DBAB4E911072C`,
   and the final targeted-status rollout receipt/hash. Seal and record the new
   contract SHA-256 before config creation.
4. Under a short low-level lease, recreate only the exact owned 646-byte
   `serverDZ.cfg`, SHA
   `486B68D5EE89FBFDF7F2F1E5C91C27758760AF46AA3059B509DA18F577855335`;
   release immediately and require clean state.
5. Run B and C preflights with the exact requests below except
   `preflight=true`; require `succeeded`, `run_id=null`,
   `cleanup_degraded=false`.
6. Run B7 exactly once through public `dayz_test_run`, without a pre-acquired
   lease:

[EXACT]

```json
{
  "project": "Utopia_PC",
  "mode": "all",
  "mission": "chernarus",
  "extra_mods": ["@LFPowerGrid"],
  "build": false,
  "clean": false,
  "pack_only": false,
  "no_file_patching": false,
  "port": 2302,
  "preflight": false
}
```

7. B7 is valid only if server and client reach Mission, server contains
   `MissionServer OnInit`, compile errors are zero, physical mod order and
   artifact hashes are exact, and the exact returned `run_id` stops terminally
   through `dayz_test_stop` with no cleanup degradation.
8. Only after valid, cleanly stopped B7, run C7 once:

[EXACT]

```json
{
  "project": "Utopia_PC",
  "mode": "all",
  "mission": "chernarus",
  "extra_mods": ["@LFPowerGrid_A2"],
  "build": false,
  "clean": false,
  "pack_only": false,
  "no_file_patching": false,
  "port": 2302,
  "preflight": false
}
```

9. Apply the same Mission/OnInit/compile/order/hash/stop gates to C7. Client
   Game/World/Mission file and class counts must be identical B7↔C7.
10. Failure branch:
    - non-null `run_id`: stop only that exact run;
    - null `run_id` and `cleanup_degraded=false`: do not call stop;
    - any cleanup degradation: record and stop the sequence;
    - invalid B7: do not run C7;
    - no automatic retry and no direct process/daemon action.
11. Under a fresh short lease, remove only the owned exact config after
    ownership/hash proof. End with clean `session_status` and `bridge_status`.

No GS begins unless B7 and C7 both satisfy every gate and their evidence is
independently reviewed. No integration/publication is authorized.

## 10. Exit criteria

- Plan R2 has independent PASS before implementation.
- RED proves both global `run_id=None` and error-envelope misclassification.
- GREEN proves exact-run targeting and fail-closed taxonomy.
- Protocol → child → HTTP filter → readiness chain is covered.
- Isolated rollback uses newly generated identity-bound receipts.
- Isolated and canonical focused gates are 60/60.
- Every H11 run is internally consistent and 130/130.
- Reproducible bundle verifies exactly 57 files and all external identities.
- Registry and both PE fixtures name the exact new PE.
- Independent post-rollout review has no material finding.
- B7 no longer fails from the global-history overflow.
- Every lease/run/config cleanup ends terminal and clean.

## 11. Commit and durable handoff

`P:\DayZ_MCP_dev` is not a Git repository; do not fabricate a commit. Seal
manifests, RED/GREEN logs, build/reproducibility receipts, registry drill,
canonical CAS receipts, rollback material and runtime evidence under the
isolation root. Update DayZ_MCP research/bug ledger and LFPowerGrid Task 7
notes. LFPowerGrid remains unintegrated.
