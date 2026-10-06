# gpt-6.1-sol review, g1_ttl_file, round 3 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F3 — P2 — Deadline and cancellation guarantees remain incomplete.**  
`tools/dayz_mcp/server.py:2993`, `:3046`, `:2777`, `:2803`, `:2807`

Initial policy resolution is now offloaded, but both subsequent `_profiles_dir_for_role(...)` calls remain synchronous. The final call can cross the deadline and still return success.

**Executed reproduction:** call:

```json
{"condition":"file_matches","pattern":"READY","profile_file":"mission.log","timeout_s":0.3,"poll_interval_s":0.5}
```

Use an owned run, make the initial policy lookup immediate, make subsequent policy lookups sleep 200 ms, and make the reader return a complete matching line.

**Wrong output:**

```json
{"elapsed_s":0.407,"satisfied":true,"timed_out":false,"observed":"READY"}
```

Cancellation also leaves blocked work behind. `_stop_thread` injects `SystemExit`, but that does not interrupt a thread blocked in `threading.Event.wait()`. `_thread_bounded` returns after its timeout or bounded join without confirming termination.

**Executed reproductions:** block the worker on an event, then either exhaust a 50 ms deadline or cancel its await.

```text
timeout:      reader_alive_at_return=True
cancellation: reader_alive_at_return=True, elapsed_s=1.015
```

The new regression uses a Python loop sleeping for 10 ms, which permits the injected exception to execute; it does not cover blocking waits.

**Fix:** bound every policy recheck, reject success after final validation exceeds the deadline, and implement cleanup that actually terminates and joins outstanding work.

---

**F5 — P2 — Supervisor observation still publishes TTL after local ownership changes.**  
`tools/dayz_mcp/mcp_supervisor.py:602`, `:610`, `:616`, `:626`

The supervisor obtains local ownership before requesting status, then deliberately classifies status with:

```python
annotation = classify_status(status, None)
```

It neither matches the status against current local ownership nor rechecks that ownership after observation.

**Executed reproduction:**

1. Worker holds lease A.
2. Supervisor receives local lease A.
3. Hold the corresponding status response.
4. Release the worker’s local lease.
5. Deliver that request’s previously observed authoritative status: `self=active A`, `owner=A`, TTL `33`.
6. Complete `_reply(..., is_error=True)`.

**Wrong output:**

```json
{"actual_local_lease":null,"annotation":{"lease_ttl_s":33.0}}
```

The response contains numeric TTL metadata and visible TTL text despite the intervening release. Unique observation IDs fix F11’s cross-request confusion, but do not fix this race within one observation.

The new F5 regression also changes authoritative status to B without updating the fake worker’s local lease A, then expects B’s TTL. That assertion rewards skipping the required ownership match.

**Fix:** obtain authoritative status together with current generation-local ownership evidence, match their lease IDs, and recheck local ownership before annotation. A stale cache must not establish ownership.

---

**F10 — P2 — The final ownership check still has an asynchronous release window.**  
`tools/dayz_mcp/server.py:3037`, `:3040`, `:3064`

The local lease check occurs **before**:

```python
owned_runs = await _bound(_lifecycle_runs(runtime), deadline)
```

There is no local lease recheck after that await.

**Executed reproduction:** start an owned file wait and return a complete `"READY"` line. During the final lifecycle-status await, release the local lease while returning the run snapshot obtained before that release.

**Wrong output:**

```json
{"satisfied":true,"observed":"READY","local_lease":null}
```

The new regression releases during the file read, which the added pre-status check catches. It does not release during the subsequent status await.

**Fix:** recheck the exact held lease after the final asynchronous validation and immediately before accepting success.

## SPEC COVERAGE

“Done” below describes inspected implementation and available evidence; it does not imply live validation.

### 584e + f0e3

| Requirement | Status |
|---|---|
| Owner reads retain identification/auditing and stop renewing expiry | **Done** |
| Lease-free reads and optional invalid/expired-token handling remain | **Done** |
| Mutations and explicit heartbeats retain renewal | **Done** |
| Player/entity probes remain nonrenewing reads; file waits explicitly heartbeat | **Done** |
| Renewal contract, wait descriptions, H3/H4 and changelog updated | **Done** |
| Final `CallToolResult` decorator installed after catalog registration and before freshness | **Done** |
| Dictionary, alias, image/list and error results annotated without losing their original content | **Done** in executed protocol tests |
| `_meta`, visible text, structured dictionary and corresponding JSON text annotations | **Done** |
| Finite, nonnegative authoritative TTL, including operation pin | **Done** |
| Absent TTL without caller lease; null/unknown on failed observation with local ownership | **Done** in worker decorator; **wrong** on supervisor surface overall |
| Non-launching, bounded status observation | **Done** in worker path |
| Match authoritative self/owner/local IDs and recheck local ownership | **Done** in worker decorator; **wrong** in supervisor — F5 |
| No acquire, heartbeat, reconcile or daemon spawn solely for annotation | **Done** in inspected observer paths |
| Reuse an already available valid status observation | **Missing**: the decorator performs another status request |
| `server_reload` and synthesized errors satisfy the same contract | **Wrong** — F5 |
| Mixed-deployment warning and additive compatibility documented | **Done** |
| No PBO, reseal or persistent-format change | **Done** in inspected changes |
| Release omits TTL; acquire reports newly held lease | **Done** in mocked coverage; real client transitions not verified |

All five required named 584e tests and the unknown/expiry/pin/release/catalog cases are present. The supervisor ownership regression remains inadequate.

### d17c-b

| Requirement | Status |
|---|---|
| Condition, optional `profile_file`, and typed role extension | **Done** |
| Nonempty pattern, local lease, and exactly one owned/adopted active run | **Done** for initial validation |
| Policy-derived profile bound to launched role; unavailable role rejected | **Done** for F12’s reported scenario |
| Bounded relative path; traversal, absolute/ADS/device/reparse escape rejection | **Done** in source |
| Existing-parent validation and containment of the actual opened handle | **Done** in source |
| Complete-line substring matching, bounded lookback, cursor and scan semantics | **Done** |
| Reject launch lookback; marker identifies selected file; return file cursor | **Done** |
| Document possible matches against preexisting content | **Done** |
| Heartbeat exact held lease before every poll, including missing files | **Done** |
| Abort on ownership loss without implicit acquisition/adoption | **Wrong** at final acceptance — F10 |
| Original deadline bounds heartbeat, policy and file work; reject late matches | **Wrong** — F3 |
| Blocking work offloaded; cancellation leaves no detached polling work | **Wrong** — F3 |
| Sleeps outside `tool_lock`; reject intervals longer than lease window | **Done** |
| Normal result envelope plus role, filename and cursor | **Done** |
| Missing/no-match timeout and unreadability scan diagnostics | **Done**; F8 reproduced as fixed |
| Other wait conditions preserved apart from authorized renewal change | **Done** in inspected diff |
| C4/H3, tool description, changelog and compatibility updates | **Done** |

All six required named file-wait tests and the additional edge-case tests are present. F3 and F10 regressions cover narrower failure windows than their specifications require.

Previous-round disposition: **F8, F9, F11, F12 and F13 fixed for their reported scenarios; F3, F5 and F10 remain blocking.**

## GATE GAP

The passing gate does not establish these lifecycle guarantees:

- F3’s cancellation fixture permits Python exception delivery; a blocking wait survives cancellation.
- F3 tests slow initial policy resolution, but not synchronous policy rechecks or late final success.
- F10 releases during reading, but not during final asynchronous validation.
- Supervisor fixtures answer internal tools directly, bypassing actual worker decorators and transport timing.
- F5’s new ownership-change assertion accepts a status lease that differs from the worker’s local lease.
- File-policy tests mock the sealed-policy helpers, so they do not prove production launcher/profile integration.

## PREMISE

The owner’s full-scope instruction is clear and applicable.

The supplied `DIFF.patch` omits changes present in the current tree: both `product-spec.md` and `PROJECT-MAP.md` differ from `base2`. I reviewed the live documentation directly.

`REPORT.md` retains obsolete deviations claiming that product-contract updates and initial unknown handling were omitted. Its round-3 section and current source supersede those statements.

## NOT VERIFIED

- Full module execution here: attempted **25 tests**, but **11 errored during setup** because the read-only sandbox provides no writable temporary directory. These are environment failures.
- Separately executed **14 tests passed**, covering coordinator, protocol, supervisor and round-3 non-filesystem cases.
- Additional read-only, in-memory reproductions confirmed F3, F5 and F10; opened-file fault injection confirmed F8’s corrections.
- No live daemon, in-game TTL-boundary test, mission-file wait exceeding 120 seconds, mixed-deployment test, or real sealed-launcher integration.
- No files modified and no commit created.