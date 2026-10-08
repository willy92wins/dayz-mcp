# gpt-6.1-sol review, g1_ttl_file, round 2 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

Findings below use current-tree line numbers. Reproductions used in-memory fixtures and mocks; no files were modified.

**F3 — P2 — Deadline and cancellation fixes remain incomplete.**  
`tools/dayz_mcp/server.py:2859`, `:2862`, `:2863`, `:2913`, `:2918`

Policy resolution and initial filesystem checks still run synchronously outside the deadline wrapper. Cancellation of the `to_thread` await also leaves the running file operation behind.

**Measured scenarios:**

- Call `file_matches` with `pattern="READY"`, `profile_file="mission.log"`, `timeout_s=0.05`; make `_profiles_dir_for_role` take 200 ms. Result: timeout after **200 ms**, zero heartbeats, and a concurrent task scheduled for 10 ms had not run.
- Block `_read_contained_handle`, cancel the wait after that reader starts, then await cancellation. Result: **the reader is still running after cancellation returns**.

Heartbeat/lifecycle awaits and rejection of late matches are fixed. Remaining policy/path work needs bounded execution and an explicit cleanup strategy for outstanding file work.

**F5 — P2 — Supervisor ownership bookkeeping still violates the annotation contract.**  
`tools/dayz_mcp/mcp_supervisor.py:545`, `:546`, `:580`, `:583`, `:594`

The original “successful observation, then timeout” scenario is fixed. Two related cases remain:

1. Worker holds lease A, but `confirmed_lease_id` is initially `None`. Suppress its first status response and call `_reply(..., is_error=True)` with a 20 ms observation budget. **Measured result:** original error text only; no TTL metadata or visible block. Required: null TTL and `"unknown"`.
2. Cache lease A, then return a successful authoritative status with `self=active B`, `owner=B`, `expires_in_s=17`. **Measured annotation:** null/unknown, even though B was successfully observed. The cache becomes B only after classifying the response.

A previously observed lease ID is not the worker’s current local lease ID. Obtain current generation-local ownership evidence and distinguish successful ownership changes from observation failures.

**F8 — P2 — Unreadability after opening still escapes scan diagnostics.**  
`tools/dayz_mcp/server.py:2687`, `:2704`, `:2717`, `:2930`

Open failures now produce unreadable scan diagnostics; I verified that correction. Failures during handle validation or reading remain uncaught.

**Measured scenario:** use an existing readable file and make `_marker_rewound_handle` raise `OSError("read unavailable")`. Call:

```json
{"condition":"file_matches","profile_file":"REPORT.md","pattern":"READY","timeout_s":0.2}
```

Result: **`OSError: read unavailable` escapes after one heartbeat**, without the normal wait envelope or `scanned`. Injecting `LogTailError("log_unavailable")` at `_final_handle_path` likewise escapes.

Convert unreadability throughout the opened-handle operation into scan diagnostics; retain containment violations as explicit rejection.

**F9 — P3 — Required product-contract updates remain missing.**  
`product-spec.md:68`, `:138`, `:139`

C4/H3/H4 still omit the new file-wait, nonrenewing-read, and result-TTL contracts. The mixed-deployment changelog warning is fixed at `CHANGELOG.md:19`.

This is a documentation finding without an executable failure scenario. It does not independently block approval; the contradictory implementation scope is noted under PREMISE.

**F10 — P2 — Lease release during the offloaded read can still return success.**  
`tools/dayz_mcp/server.py:2915`, `:2918`, `:2956`

After releasing `tool_lock`, the code awaits the reader and accepts its match without rechecking lease or run identity.

**Measured scenario:** begin an owned file wait; make the reader take 50 ms and return a complete `"READY"` line. After 10 ms, a concurrent task obtains `tool_lock` and releases the local lease.

**Wrong output:**

```json
{"satisfied":true,"observed":"READY"}
```

The local lease is `None` when this result returns. Required behavior is to abort with the named lease error. Revalidate ownership after asynchronous reading before accepting its result.

**F11 — P2 — A late supervisor observation can answer a subsequent observation.**  
`tools/dayz_mcp/mcp_supervisor.py:555`, `:572`, `:577`, `:323`

Every observation reuses `STATUS_ID`. Timeout removes its inflight entry, but late responses remain eligible to complete a later request with that same ID.

**Measured sequence:**

1. Observation 1 times out.
2. Caller releases its lease.
3. Start observation 2.
4. Deliver observation 1’s delayed response: active lease A, TTL `33`.
5. Deliver observation 2’s actual response later: caller state `"none"`.

**Wrong output from observation 2:**

```json
{"lease_ttl_s":33.0}
```

Required: no TTL fields. Use independently correlated observation IDs and discard responses belonging to expired observations.

**F12 — P2 — Policy resolution is not bound to the selected run’s launched roles.**  
`tools/dayz_mcp/server.py:2569`, `:2570`, `:2574`

The resolver selects from global mode roots and project policy without checking the selected run’s registered roles or recorded profile.

**Measured state:** one owned `RUNNING` run records server profiles and contains only a server process. Approved project policy also contains client profiles. Calling `_profiles_dir_for_role(run, "client")` returns:

```text
C:\approved\A\_client\profiles
```

There is no client role in that run. Consequently, a default-lookback wait can consume matching content from an unused client profile and report success for the server-only run.

Bind profile selection to the registered run’s actual launch configuration and reject unavailable roles.

**F13 — P2 — The supported `local8b` pack removes the supervisor observer.**  
`tools/dayz_mcp/server.py:7863`, `tools/dayz_mcp/tool_pack.py:51`

The internal observer is registered before `apply_tool_pack`, whose closed allowlist removes it.

**Measured input:** build with `ServerConfig(tool_pack="local8b", ...)`, then call `__dayz_mcp_lease_ttl_status__`.

**Wrong output:**

```text
isError=True
Unknown tool: __dayz_mcp_lease_ttl_status__
```

`parse_tool_status` rejects that error result, so supervisor-generated responses cannot obtain the required authoritative status in this configuration. Preserve internal transport operations independently of public tool-pack filtering.

## SPEC COVERAGE

Previous findings: **F1, F2, F4, F6 and F7’s arbitrary-root acceptance are addressed. F3, F5 and F8 are partially addressed. F9 remains.** F12 identifies a separate run-binding defect in the revised profile resolver.

### ITEM 584e+f0e3

| Specification requirement | Status |
|---|---|
| Preserve owner-read identification, auditing and routing; remove expiry/stamp renewal | **done** |
| Preserve lease-free reads and invalid/expired optional-token behavior | **done** |
| Preserve mutation and explicit-heartbeat renewal | **done** |
| Update renewal contract and wait description; player/entity probes remain nonrenewing reads | **done** |
| Update H3/H4 | **missing** — F9 |
| Install final result decorator alongside catalog/freshness wrappers | **done** |
| Cover dictionaries, images, lists, aliases and errors; preserve existing content/metadata | **done** in worker tests |
| Active observed lease gets finite nonnegative TTL | **wrong** on supervisor paths — F5/F11/F13 |
| No active caller lease means fields absent | **wrong** under delayed supervisor responses — F11 |
| Failed observation with a local lease means null/unknown | **wrong** before supervisor confirmation — F5 |
| Advisory TTL includes operation pin and confers no authorization | **done** |
| Publish metadata, visible text, dictionary structured JSON and corresponding JSON text | **done** when annotation is supplied |
| Nonlaunching bounded observation; caller/owner matching and local-ID recheck | **done** in worker decorator; **wrong** in supervisor bookkeeping/correlation |
| No acquire, heartbeat, reconciliation or daemon launch solely for decoration | **done** by inspected observation paths |
| Reuse available valid observations where possible | **missing** — observer results undergo another decorator observation |
| Supervisor reload and synthesized errors satisfy the same contract | **wrong** — F5/F11/F13 |
| Document updated-daemon requirement for nonrenewing reads | **done** |
| No reseal, PBO or persistent-format change | **done** |
| Release omits TTL; acquire describes newly held lease | **done** in inspected paths/synthetic tests; real client transition not exercised |
| Required protocol, expiry, declining-TTL, replacement, unknown/pin and catalog tests | **done**, with supervisor cases incomplete |
| Later live TTL-boundary check | **missing**, explicitly deferred |

### ITEM d17c-b

| Specification requirement | Status |
|---|---|
| Condition/enum extension and optional `profile_file`/`role` arguments | **done** |
| Nonempty pattern, relative path, local lease and exactly one owned/adopted run | **done** |
| Resolve selected role from verified policy and the registered run | **wrong** — F12 |
| Reject absolute, drive, UNC, traversal, ADS, device and reparse escapes | **done** by inspection; live swap not exercised |
| Validate and read the same opened handle | **done** — F2 |
| Validate existing parents for missing files; recheck eventual handle | **done** |
| Bounded complete-line, substring, cursor and lookback behavior | **done** by inspection |
| Reject launch lookback; marker identifies selected file; return cursor | **done** |
| Document preexisting-content risk and boundary-marker workflow | **done** |
| Heartbeat exact lease on each poll, including missing-file polls | **done** |
| Lease/run loss aborts; no implicit acquire/adopt or unowned continuation | **wrong** during offloaded reads — F10 |
| Bound heartbeat, policy/path work and reading by original deadline | **wrong** — F3 |
| Sleeps outside lock; blocking file reads offloaded | **done** |
| Cancellation leaves no outstanding file work | **wrong** — F3 |
| Reject poll interval exceeding renewable window | **done** |
| Preserve result envelope and add role, filename, cursor | **done** on normal paths |
| Missing/no-match timeout; unreadability visible in scans | **wrong** for post-open failures — F8 |
| Other wait conditions unchanged except nonrenewing reads | **done** by diff inspection |
| Update description/changelog and C4/H3 | Description/changelog **done**; C4/H3 **missing** |
| Optional-argument compatibility; no daemon command/PBO/reseal/format change | **done** |
| Required file-wait tests | **done**, but production-policy and adverse-I/O coverage remain inadequate |
| Later in-game delayed-marker wait exceeding 120 seconds | **missing**, explicitly deferred |

## GATE GAP

The reported PASS does not exercise the failures above:

- File fixtures replace all three policy helpers, masking actual policy/run binding.
- Cancellation is tested during normal polling; the assertion only checks that heartbeat count stops increasing.
- Supervisor timeout testing first establishes a successful ownership cache.
- No test delivers an expired observation response during a subsequent observation.
- Catalog tests use the full tool pack, leaving `local8b` observer removal unseen.
- No post-open unreadability or release-during-read case is asserted.

I independently ran **10 tests** from `OwnerReadDoesNotRenewTest`, `ProtocolTtlTest` and `SupervisorTtlTest` in the project venv: **all passed**. The additional reproductions nevertheless produced the failures reported above. The orchestrator-designated flaky test is not counted as a blocking finding.

## PREMISE

The implementer’s brief contradicts the specification: `brief_r1.md:161` permits only `tools/dayz_mcp/`, `tools/tests/` and `CHANGELOG.md`, while the spec requires editing `product-spec.md`. Round 2 also requests only blocking fixes. F9 therefore needs an orchestration scope correction.

The supplied diff additionally contains an empty `tools/approved-launchers.lock` outside that allowlist; its provenance should be accounted for.

## NOT VERIFIED

- Full fast tier and filesystem-writing `FileMatchesTest` fixtures were not rerun under the read-only constraint; their supplied results remain orchestrator evidence.
- Real sealed-launcher policy resolution, live daemon/supervisor generations, and Windows junction swaps.
- In-game TTL expiry, mixed deployment, or a mission-written marker during a wait exceeding 120 seconds.
- No files, shared memory, processes or deployed artifacts were changed.

