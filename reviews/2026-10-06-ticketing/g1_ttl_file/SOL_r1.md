# gpt-6.1-sol review, g1_ttl_file, round 1 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

Paths below are relative to the reviewed tree.

**F1 — P2 — Production lifecycle rows cannot satisfy file waits.**  
`tools/dayz_mcp/server.py:2524`, `tools/dayz_mcp/server.py:2540`

`_one_owned_run` uses `_caller_owns_run`, which expects `owner_session`. Production lifecycle status supplies `owner_session_id`: `process_lifecycle.py:2083`, `process_lifecycle.py:6997`.

**Repro:** pass `dataclasses.asdict(RunRecord(...))` for a valid caller-owned `STARTING` run to `_one_owned_run([row], caller_session, owner_lease_id)`. **Measured output:** `no_active_run`. Consequently, `wait_for(condition="file_matches", profile_file="mission.log", pattern="READY")` rejects an owned production run.

There is a second mismatch in role resolution: production `RunRecord` has one `profiles` field, and `ProcessRecord` has no `profiles` field (`process_lifecycle.py:1333`, `:1375`). The new tests invent `profiles_by_role`. For a dual-role run whose recorded profile is `_server/profiles`, requesting `role="client"` produces `profile_unresolved`.

**Fix:** consume the actual lifecycle schema and resolve role profiles through verified mode/project policy.

**F2 — P2 — File containment checks a different handle from the subsequent read.**  
`tools/dayz_mcp/server.py:2615`, `tools/dayz_mcp/server.py:2650`, `tools/dayz_mcp/server.py:2801`, `tools/dayz_mcp/server.py:2816`

The validated handle is closed. `_marker_rewound` and `_new_log_lines` subsequently reopen the pathname (`launch_logs.py:214`, `log_tail.py:158`).

**Executable scenario:** using the existing file-wait fixture, create `profiles/results/mission.log` and an outside directory containing `mission.log` with `OUTSIDE-SECRET\n`. Call:

```json
{"condition":"file_matches","profile_file":"results/mission.log","pattern":"OUTSIDE-SECRET","lookback_lines":50}
```

Install a deterministic hook immediately before `_marker_rewound` that replaces `profiles/results` with a junction to the outside directory, then invokes the original reader. The earlier containment check passes; the reopened reader consumes the outside file and can return `satisfied=true, observed="OUTSIDE-SECRET"`.

**Fix:** validate and read the same opened handle. This Windows swap scenario was traced from source, not executed here.

**F3 — P2 — Deadline and cancellation guarantees are violated.**  
`tools/dayz_mcp/server.py:2674`, `tools/dayz_mcp/server.py:2728`, `tools/dayz_mcp/server.py:2764`, `tools/dayz_mcp/server.py:2816`

Heartbeat and lifecycle observations are awaited without the remaining deadline. File operations run synchronously on the event loop while holding `tool_lock`. A match is accepted without checking whether reading exceeded the deadline.

**Measured reproductions using the existing `_Runtime` fixture:**

- With containment returning “missing,” a heartbeat taking 200 ms and `timeout_s=0.05` produced a timeout response after **209 ms**.
- With `_new_log_lines` blocking for 200 ms and returning `["ready"]`, the same deadline produced **`satisfied=true, timed_out=false, elapsed_s≈0.201`**. Cancellation scheduled after 10 ms had not executed when the result returned.

**Fix:** bound every poll stage by the remaining deadline, offload blocking file work, and reject matches completed after deadline exhaustion.

**F4 — P2 — Supervisor TTL observation can launch a daemon.**  
`tools/dayz_mcp/mcp_supervisor.py:542`

The reserved observation invokes the ordinary `session_status` tool. That tool reaches `ClientRuntime.session_status` (`server.py:3997`), which uses `_control_with_lazy_spawn` (`server.py:1541`, `:1393`).

**Repro:** synthesize a supervisor error while its worker remains available but the daemon is unavailable. Make the worker’s control status return:

```python
ControlClientError(
    "daemon_unavailable", request_stage="pre_request", http_bytes_sent=0
)
```

The supervisor’s decoration request invokes `_ensure_daemon`. **Measured with a mocked launcher:** one launch call.

**Fix:** use a dedicated bounded worker observation that calls the non-launching `ControlClient.session_status` path.

**F5 — P2 — Supervisor observation failure silently drops the required unknown annotation.**  
`tools/dayz_mcp/mcp_supervisor.py:557`, `tools/dayz_mcp/mcp_supervisor.py:563`, `tools/dayz_mcp/mcp_supervisor.py:583`

**Repro:** use the new `_Worker` fixture with an unchanged active lease. First call `_reply` with a successful status response; then suppress status responses and call `_reply` again with `lease_status_timeout_s=0.02`.

**Measured output:** the first result contains `_meta.lease_ttl_s=33.0`; the second contains no TTL metadata or visible TTL block. Required output is `lease_ttl_s:null` and `lease_ttl_status:"unknown"` when the worker still holds that local lease.

**Fix:** preserve enough generation-local ownership evidence to distinguish failed observation from confirmed absence.

**F6 — P2 — Confirmed absence of the caller’s lease is reported as unknown when another owner exists.**  
`tools/dayz_mcp/lease_result_ttl.py:60`

**Repro:**

```python
classify_status({
    "self": {"state": "none", "position": None},
    "owner": {"state": "active", "lease_id": "B", "expires_in_s": 120},
}, "A")
```

**Measured output:**

```json
{"lease_ttl_s":null,"lease_ttl_status":"unknown"}
```

The observation succeeded and confirms that this caller holds no active lease. Both fields must be absent. This occurs naturally with a stale local lease ID after another session becomes owner.

**Fix:** distinguish an authoritative inactive caller from observation failure, independently of another owner’s presence.

**F7 — P2 — Profile selection accepts roots outside approved project policy.**  
`tools/dayz_mcp/server.py:2561`, `tools/dayz_mcp/server.py:2627`

**Repro:** call `_profiles_dir_for_role` with:

```python
{"profiles": r"C:\unapproved\_server\profiles"}
```

and `role="server"`. **Measured output:** that unapproved path is accepted. If the accepted run fixture names an existing directory there, its relative files become readable.

The implementation checks directory names, then anchors containment to whatever root resolves. It never checks the sealed project/mode policy required by the specification. Existing policy helpers are at `dayz_test_tool.py:2624`, `:2650`, `:2668`.

**Fix:** establish the authorized role root from verified policy before checking relative-file containment.

**F8 — P2 — An unreadable file bypasses scan diagnostics.**  
`tools/dayz_mcp/server.py:2589`, `tools/dayz_mcp/server.py:2733`, `tools/dayz_mcp/server.py:2769`

**Executable scenario:** hold `mission.log` open on Windows with sharing disabled, then call `file_matches` against it. `_final_opened_path` raises `LogTailError("log_unavailable")`; neither containment call catches it.

**Measured equivalent:** inject that exception at containment. It escapes immediately, with no `scanned` diagnostics or normal wait envelope; the initial failure occurs before any heartbeat.

**Fix:** retain unreadability in bounded scan diagnostics while preserving containment failures as explicit rejection.

**F9 — P3 — Required contract documentation is incomplete.**  
`product-spec.md:68`, `product-spec.md:138`, `product-spec.md:139`, `CHANGELOG.md:19`

C4/H3/H4 remain unchanged. The changelog does not state that nonrenewing owner reads require the updated daemon. This is a documentation gap without an executable failure scenario.

## SPEC COVERAGE

### 584e+f0e3

| Specification bullet | Status |
|---|---|
| Remove owner-read renewal; retain auditing and routing | **done** |
| Preserve lease-free reads and invalid/expired optional-token handling | **done** by source inspection |
| Preserve mutation and explicit-heartbeat renewal | **done** |
| Player/entity probes remain nonrenewing reads | **done** |
| Update renewal contract and `wait_for` description | **done** |
| Update H3/H4 and changelog contract | **missing** required spec edits and deployment caveat |
| Final boundary covers dictionaries, images/lists, aliases, errors and catalogs | **done** for exercised worker paths |
| Finite, nonnegative owned TTL | **done** |
| No active caller lease means fields absent | **wrong**, F6 |
| Failed observation with local lease means null/unknown | **done** in worker; **wrong** in supervisor, F5 |
| `_meta`, visible text and dictionary JSON annotation; preserve content/errors | **done** for exercised paths |
| Non-launching bounded status observation; match/recheck local lease ID | **done** in worker; **wrong** in supervisor, F4 |
| Reuse available valid status observations | **missing** |
| Advisory TTL includes operation pin and grants no authority | **done** |
| Supervisor reload and synthesized errors satisfy the same contract | **wrong**, F4–F5 |
| Mixed deployment does not claim unconditional new semantics | **done** in tool description; **missing** changelog caveat |
| Release omits TTL; acquire describes new lease | **done** structurally; real client lifecycle **not verified** |
| No launcher reseal, PBO or persistent-format change | **done** |
| Required tests | **done** as named tests; production schema, observation failures and timing coverage **missing** |

### d17c-b

| Specification bullet | Status |
|---|---|
| Enum, optional `profile_file`, typed role | **done** |
| Require pattern, relative path, active lease and exact owned/adopted run | **wrong** in production, F1 |
| Resolve registered role profile through verified project/mode policy | **wrong**, F1/F7 |
| Reject absolute/drive/UNC/traversal/ADS/device paths | **done** by source inspection |
| Validate parents and containment of the actual consumed file | **wrong**, F2 |
| Missing file may be awaited | **done** in fixtures |
| Bounded complete lines, substring matching, cursor/lookback semantics | **done** in fixtures |
| Reject launch lookback; bind marker to selected file | **done** |
| Return cursor and document preexisting-content risk | **done** |
| Heartbeat exact lease every poll, including missing files | **done** in fixtures |
| Revalidate lease/run; abort on loss without acquisition/adoption | **done** in fixtures |
| Bound heartbeat, resolution and reading by original deadline | **wrong**, F3 |
| Offload blocking work; sleeps outside lock; cancellation leaves no polling task | Sleeps/task cleanup **done**; blocking work/cancellation **wrong**, F3 |
| Reject intervals exceeding renewable window | **done** for fixed `SESSION_TTL_S`; configured-window behavior **not verified** |
| Preserve result envelope; add role/file/cursor | **done** on normal paths |
| Absence/no match times out; unreadability remains in scan diagnostics | Timeout **done**; unreadability **wrong**, F8 |
| Preserve other conditions except nonrenewing reads | **done** by diff inspection |
| Update C4/H3, description and changelog | Description/changelog **done**; C4/H3 **missing** |
| Optional-argument compatibility; no daemon command/PBO/reseal/format change | **done** |
| Required tests and extended edge cases | **done** as fixture tests; production, race, slow-I/O and failure coverage **missing** |

## GATE GAP

The passing gate can miss these defects because:

- File fixtures invent `owner_session`, `profiles_by_role` and role profiles absent from production lifecycle records.
- Static escape tests do not swap a reparse point between validation and reading.
- Heartbeats are immediate and files are fast; cancellation is tested during sleep.
- Supervisor status always succeeds with a canned matching lease.
- Expired status is tested with no owner, omitting the stale-local-ID/other-owner case.
- The file-wait tests do not compare the selected root against sealed project policy or exercise an unreadable first open.

The reported flaky test’s cause remains unisolated.

## PREMISE

The report cites an inherited edit allowlist to justify skipping `product-spec.md`. The supplied review specification explicitly requires those edits; no waiver is present here.

Its claim that the changelog states the updated-daemon requirement is also unsupported by the added entry.

The injected October 1 release state is historical context, not evidence for this batch’s reviewed baseline.

## NOT VERIFIED

I ran, from this tree’s `tools/`, using:

`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe`

```text
-B -m unittest
tests.test_lease_ttl_and_file_wait.OwnerReadDoesNotRenewTest
tests.test_lease_ttl_and_file_wait.ProtocolTtlTest
tests.test_lease_ttl_and_file_wait.SupervisorTtlTest

Ran 7 tests — OK
```

Additional non-writing probes reproduced F1, F3–F8 as described. F2’s Windows junction swap was not executed.

The file-test class creates files and junctions, so I did not run it under the read-only restriction. I did not rerun the full fast tier, contact a daemon, launch DayZ, or perform either later live check.

No files, commits, shared memory or live DayZ state were changed.

