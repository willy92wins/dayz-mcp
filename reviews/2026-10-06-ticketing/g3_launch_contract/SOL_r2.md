# gpt-6.1-sol review, g3_launch_contract, round 2 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

Paths below refer to this modified tree. Finding IDs retain the previous round’s numbering.

**F3 — P2 — Preflight can accredit a different directory from the effective runtime path.**  
`tools/dayz_mcp/request_path_authority.py:584`, `:477`; `tools/dayz_mcp/dayz_test_tool.py:2275`; `tools/dayz_mcp/dayz_test_worker.py:283`

- **State/input:** Sealed policy permits `P:\Mods` and `C:\candidate`; runtime `mods_root="P:\Mods"`. `C:\candidate\@Missing` exists, but `P:\Mods\@Missing` does not. Other required directories exist. Call `project="ExampleMod", mode="server", preflight=true, base_mods=["@Missing"], extra_mods=["@DayZ_MCP"]`.
- **Wrong output:** Preflight returns `status="succeeded", error_code=null`. Accreditation finds the directory under the alternative root, while effective composition selects `P:\Mods\@Missing`.
- **Evidence:** A probe using the real sealed-policy type and accreditation traversal, with filesystem observations mocked, reproduced that success and recorded accreditation of `C:\candidate\@Missing`.
- **Fix:** Resolve effective absolute paths using the selected runtime first, then accredit those exact paths. The original absent-root scenario is addressed, but F3 remains incomplete.

**F4 — P2 — The runtime accessor still admits runtime policies the worker rejects.**  
`tools/dayz_mcp/native_bundle.py:1083`, `:1105`; `tools/dayz_mcp/dayz_test_tool.py:2317`, `:2338`; `tools/dayz_mcp/dayz_test_worker.py:222`

- **State/input:** Start with a matching runtime project containing valid ordinary path fields, but replace `mission_aliases` with `{"chernarus":"relative-missing"}`. Call server preflight with `mission="chernarus"`.
- **Wrong output:** Host preflight returns `status="succeeded", error_code=null`; the worker rejects the same runtime with `runtime_policy_invalid`.
- **Evidence:** Reproduced using the actual `VerifiedNativeBundle.validated_worker_runtime` method. It checks that aliases form a dictionary, but does not enforce the worker’s required aliases or absolute-path constraints. Bundle loading verifies the runtime bytes and canonical JSON, not these semantic constraints.
- **Fix:** Share complete runtime validation between the accessor and worker. Missing-accessor and no-matching-project failures now propagate correctly; the remaining validation gap still permits false success.

**F7 — P2 — Production occupancy sampling still misses foreign DayZ processes.**  
`tools/dayz_mcp/dayz_test_tool.py:2068`, `:2085`, `:2092`

- **State/input:** No managed runs exist, but a foreign DayZ process occupies the box. Production `/lifecycle/status` returns its normal managed-run payload with `"runs":[]`.
- **Wrong output:** `_sample_box_occupancy()` returns `(False, None)`, making preflight report `box_busy=false`.
- **Evidence:** Reproduced with the production status shape. `tools/dayz_mcp/process_lifecycle.py:7270` does not supply the foreign-process or scan fields the new sampler checks. Those observations belong to the separate box observer.
- An unknown-scan fixture also returns `(True, None)` rather than preserving uncertainty as `null`.
- **Fix:** Use an authoritative read-only box observation and distinguish known occupancy from an unavailable observation. STARTING/STOPPING managed-run handling is corrected, but the observation source is not.

**F9 — P2 — Artifact aggregation still allows missing evidence to mask unverifiable evidence.**  
`tools/dayz_mcp/dayz_test_attestation.py:289`, `:295`, `:300`

- **State/input:** Declare two artifacts in this order: an encoded/compressed PBO, then an absent PBO.
- **Wrong output:** Rows contain `unverifiable` and `failed`, but aggregate status becomes `failed`, producing `project_attestation_artifact_missing`.
- **Evidence:** Reproduced through `verify_artifacts()` using bounded in-memory payload observations.
- **Fix:** Preserve `unverifiable` precedence throughout artifact aggregation. The previous initialization-rule scenario is fixed in both rule orders; its equivalent artifact case remains wrong.

**F10 — P3 — Strict policy typing remains unfixed.**  
`tools/dayz_mcp/dayz_test_attestation.py:86`, `:120`, `:151`

- **Inputs/wrong outputs:** `{"version":true,"artifacts":[],"initialization":[]}` and the equivalent `version=1.0` policy are accepted. An artifact with `entries=[{}]`, or an initialization rule with `source={}`, raises `TypeError` instead of the closed policy-validation error.
- **Evidence:** All four cases reproduced.
- **Fix:** Check types before equality, hashing, and membership operations.

**F11 — P3 — Required acceptance-contract documentation remains missing.**  
`product-spec.md:146`, `:148`

H11/H13 remain unchanged. The tool description and changelog do not satisfy the separately requested acceptance-contract updates. No executable failure scenario applies to this documentation finding.

**F12 — P3 — Fixing F5 removed the required original-directory rejection reason.**  
`tools/dayz_mcp/dayz_test_request.py:488`; `tools/dayz_mcp/dayz_test_worker.py:310`

- **Input:** Original directory is `P:\Mods\@ExampleMod`. Call server preflight with `project_mod_override=true`, `base_mods=["P:\Mods\@ExampleMod"]`, and `extra_mods=["@DayZ_MCP"]`.
- **Wrong output:** `error_code="runtime_policy_invalid"` instead of the specified `bad_dayz_test_request:project_mod_override_includes_original`.
- **Evidence:** Public adapter probe reproduced the wrong code. The existing request suite now also fails `test_the_vocabulary_is_closed_and_every_declared_reason_is_used`; all 17 tests pass in `base3`.
- **Fix:** Retain normalized runtime-path comparison while restoring the specified public rejection translation.

The original scenarios for **F1, F2, F5, F6, and F8 are corrected**. F9’s original initialization scenario is also corrected. These closures are based on source inspection and focused probes, not live DayZ validation.

## SPEC COVERAGE

“Done” means implemented and inspected offline; it does not imply live acceptance.

### c561 — Attestation

| Specification requirement | Status |
|---|---|
| Optional policy; absent policy disables attestation | done |
| Carry policy through request, builder, sealed loader, and bootstrap; package helper | done |
| Version 1, requirement limits, IDs, relative paths, explicit entries, initialization sources, bounded timeout | done |
| Reject unknown keys, duplicate IDs, traversal, and absolute paths | done |
| Strictly reject invalid types through closed errors | **wrong — F10** |
| Apply initialization rules by requested role; other roles are `not_applicable` | done |
| Disabled launch behavior and terminal compatibility | done |
| Check deployed effective artifacts after build and before start | done |
| Candidate override excludes live-folder artifact evidence | done |
| Include loaded server-only mods for server/all requests | done — F6 |
| Zero/multiple artifact matches fail closed; exact entries and SHA256 | done |
| Plain-PBO parsing and bounded archive reads; hash parsed bytes | done — F8 |
| Unsupported encoding stays a distinct unverifiable result | **wrong for mixed failures — F9** |
| Capture declared profile files and incomplete-line boundaries | done — F1 |
| Capture each role immediately before its own start | done — F2 |
| Observe new logs and post-boundary complete lines; avoid newest-old-log selection | done for inspected cases |
| Poll inside admitted worker transaction with existing heartbeat ownership | done by source |
| Missing markers fail at deadline; unreadable/truncated initialization is unverifiable | done — original F9 |
| Initialization failure cleans newly created runs; reattach preserves server | done by source |
| Report affected supplied run and preserve degraded cleanup independently | done by source |
| Bounded reports, three new errors, worker/error/terminal/public projection | done |
| Preflight initialization pending; future-build artifacts pending | done when runtime validation succeeds |
| Legacy terminal support, bounded extension, arbitrary-key rejection, 4096-byte consumer limit | done |
| Tool description and changelog distinguish launch, attestation, and feature acceptance | done |
| H11/H13 acceptance contract | **missing — F11** |
| Existing callers remain opt-out; no silent unsupported-component downgrade | done by source |
| Helper/source packaging for combined reseal | done |
| Actual reseal and coordinated server/launcher deployment | missing; integration work remains |
| No bridge or persistence-format migration; existing recovery remains authoritative | done |
| Five named regression tests and disabled/encoding/unreadable/truncation/legacy controls | done as test additions |
| Dedicated cancellation and round-2 regression controls | missing |
| Disposable live positive/negative attestation and final-session check | missing |

### 31d2 — Preflight

| Specification requirement | Status |
|---|---|
| Early branch before FIFO, takeover, and idle-session reconciliation | done |
| Held lease preserved without release or renewal by this branch | done in inspected adapter path |
| Shared pure preflight assessment | done, but incomplete validation |
| Closed request grammar and mode/run-ID matrix | done |
| Verify launcher, bundle, policy, and source seals without executing launcher | done by source |
| Validate worker runtime consistently with worker | **wrong — F4** |
| Accredit sealed roots, source, absolute missions, and implicit project directory | done for straightforward paths |
| Accredit the actual effective mod directories | **wrong — F3** |
| Resolve mission aliases against selected runtime | **wrong for invalid runtime aliases — F4** |
| Source/build-policy constraints without asset traversal/build | done |
| VPP requirements | done |
| Read-only Steam gate; remediation flag does not repair | done |
| Preserve applicable desktop gate | done |
| No `_execute_request` or secure launcher execution | done |
| Compact envelope and `preflight=true` | done |
| Advisory occupancy and exact managed occupant when known | **wrong — F7** |
| Preserve supplied reattach ID; mint no run ID | done |
| Updated skipped checks, including pending initialization; Steam no longer skipped | done |
| Busy occupancy cannot mask validation failures | done for implemented validation |
| Validate queue/takeover argument types; never queue or evict during preflight | done by source |
| Description and changelog disclaim free-box/future-launch assurance | done |
| H11/H13 | **missing — F11** |
| Additive caller fields, real Steam refusals, no storage rotation/protocol migration | done |
| Named preflight regression tests | done as additions; production observations insufficient |
| Combined reseal and live occupied-box continuity check | missing |

### 2837 — Project override

| Specification requirement | Status |
|---|---|
| Strict Boolean public parameter and forwarding | done |
| Closed v1/v2 forms; v1 excludes new key; default calls emit historical v1 | done |
| Historical canonical v1 reparsing, including implicit source | done |
| Explicit nonempty base/extra candidate beyond bridge | done |
| Omit only implicit project; preserve caller order and server-mod semantics | done |
| Reject original directory by normalized runtime path, allowing distinct same-basename candidates | done — F5 |
| Original-directory refusal uses specified public reason | **wrong — F12** |
| Reject candidate build/clean before side effects | done |
| Worker and VPP effective composition agree | done |
| Preflight checks directories that reach argv | **wrong — F3** |
| Bridge presence/defaulting uses effective composition; excluded bridge project cannot satisfy presence | done |
| Preserve explicit-extra bridge rule and default reporting | done |
| Null implicit project role; candidate retains original roles; default storage hash preserved | done |
| Override changes seal through existing recoverable rotation; marker/journal schemas unchanged | done by source |
| Preflight/reattach do not rotate; candidate-byte changes remain outside path-seal assurance | done by source |
| Logical project identity, labels, stop lookup, and extension matching retained | done |
| `project_mod_replaced` on valid override results, including preflight | done |
| Three closed reasons and established public translation | **wrong for includes-original — F12** |
| Description/changelog; no automatic prefix detection | done |
| H11/H13 | **missing — F11** |
| Default compatibility, old-parser v2 refusal, opaque daemon seal/argv compatibility | done by source |
| Rollback uses seal mismatch and existing rotation rather than relabeling | done by source |
| Named override tests and negative controls | done as additions |
| Combined reseal and live candidate argv/rotation checks | missing |

## GATE GAP

The supplied orchestrator gate block is empty. `REPORT.md` reports an interrupted round-2 gate, not a completed PASS.

The tests can miss these defects because:

- Preflight fixtures bypass production path accreditation with `SimpleNamespace` policies.
- Occupancy fixtures supply managed runs directly; they do not establish what production `/lifecycle/status` observes.
- Host/worker agreement tests exercise a build-basename refusal, not complete runtime-policy agreement.
- Mixed artifact failures do not test aggregation in both orders.
- Round-2 stale-profile, per-role timing, and filesystem-root mismatch scenarios lack dedicated regressions.

The original-directory reason regression is already visible to an existing test; a completed gate should expose it.

Measured here:

| Command | Result |
|---|---|
| Gauntlet Python 3.11: `python -B -m unittest tests.test_launch_contract_c561_31d2_2837` | 21 run: 14 passed, 7 fixture-creation errors under read-only sandbox |
| Same environment: `python -B -m unittest tests.test_dayz_test_request` | 17 run: 16 passed, 1 failure |
| Same request suite in `base3` | 17/17 passed |
| Focused in-memory probes | Reproduced findings and verified the stated closures |

## PREMISE

The batch grouping is reasonable. The supplied brief does not establish the report’s claimed exclusion of `product-spec.md`; H11/H13 updates remain explicit requirements. If a separate integrator owns those edits, that dependency must be completed before acceptance.

The injected live state does not attest this tree or its launcher. Memory was used only as a caution about synthetic test coverage; all findings use freshly inspected source and current probes.

## NOT VERIFIED

- No files modified; no review artifact written.
- No DayZ, daemon, lifecycle endpoint, or MCP session contacted.
- No completed full gate or unrestricted execution of filesystem-dependent tests.
- No installed launcher reseal, deployed hashes, actual foreign-box observation, in-game initialization, argv, or storage rotation.
- Probe filesystem/runtime fixtures were synthetic; Windows filesystem behavior and races were not exercised live.

