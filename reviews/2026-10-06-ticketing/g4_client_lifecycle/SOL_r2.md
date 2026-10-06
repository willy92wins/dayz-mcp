# gpt-6.1-sol review, g4_client_lifecycle, round 2 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

Round-one **F1 is fixed**. Source-extracted public orchestration admitted eligible extensions in immediate, positive-wait and queue modes with one execution, zero waits and zero stop calls. Fresh `mode="all"` remained `takeover_required`.

Round-one **F2’s iterator exception is fixed**: publication copies the table under its dedicated lock. A related lost-update defect remains.

**F3 — P2 — Overlapping status readers can erase an already observed client death.**

Location: [tools/dayz_mcp/process_lifecycle.py:2339](C:/Users/guill/dzmcp_gauntlet/g4_client_lifecycle/ws/tools/dayz_mcp/process_lifecycle.py:2339). The earlier observation lookup occurs at `:2383`; preservation uses that snapshot at `:2432–2446`.

Lookup and insertion are individually locked, but the observation update is not atomic. A delayed reader unconditionally replaces evidence published while its probe was running.

Executable scenario, reproduced through the actual `public_status()` implementation with in-memory manifest and guard boundaries:

1. Run `R` is `RUNNING_IDLE`, server alive, registered client PID 802.
2. Reader A reads an empty observation history and blocks inside its guard probe.
3. Reader B receives `{"error":"process_not_found","exit_code":4}` and publishes the client’s first observed death at `10:00:01`.
4. A resumes with `{"error":"identity_unavailable","exit_code":3}` and overwrites B’s observation.
5. Reader C confirms death at `10:00:02`.

Actual wrong output:

```text
B:             state=dead     first_observed_dead_at_utc=10:00:01
stored after A: state=unknown  first_observed_dead_at_utc=null
C:             state=dead     first_observed_dead_at_utc=10:00:02
```

The first death observation is permanently lost. The same stale merge can discard retained exact exit fields when a producer supplies them.

**Suggested fix:** keep probing outside the lock, then merge against the latest stored observation under the lock. Preserve first-dead and exact exit evidence, prevent older observations from replacing newer data, and return a detached merged result. Add a deterministic test using this interleaving.

## SPEC COVERAGE

“Done” means source-verified unless execution evidence is stated.

| Specification bullet | Coverage |
|---|---|
| **7055:** explicit client-extension admission | **Done**. |
| Client mode, supplied run ID, matching project, `RUNNING_IDLE`, original launcher | **Done**. |
| Exempt only the requested row | **Done**. |
| Apply distinction before queue/own-run refusals and waits | **Done**; positive-wait fail mode independently exercised. |
| Preserve another-run, foreign-DayZ and unknown-scan blockers | **Done**; unchanged since round one. |
| Preserve protected states and concurrent-transition gates | **Done statically** through existing downstream checks. |
| Existing extension validation, internal lease, adoption and replacement gates | **Done**, retained. |
| Full session ID remains authoritative in daemon | **Done**, unchanged. |
| Public prefix selects without authorizing process changes | **Done statically**. |
| Generic fresh-launch takeover behavior unchanged | **Done**; immediate fresh-launch refusal independently exercised. |
| Healthy client remains `client_already_polling` | **Done**, retained replacement gate. |
| Same run/server/world; client alone replaced | **Done statically**; runtime proof **missing**. |
| Requested admission tests | **Done partially**. Positive-wait coverage added; actual adoption-to-launch ownership races remain **missing**. |
| Sentinel-based in-game acceptance | **Missing**, deferred. |
| Python-only reattach; conditional launcher reseal | **Done**; bundled worker/transaction modules unchanged. |
| **9336:** preserve camera fields and result shape | **Done**. |
| Effective settle duration plus five-second deadline slack | **Done statically**, `MCPClientBridge.c:1804`. |
| Public/wire bounds `0..600`; zero retains three ticks | **Done**. |
| Validate before queueing/applying | **Done** in Python, wire schema and Enforce. |
| Document duration and caller timeout budget | **Done**. |
| Timeout leaves application/completion unknown | **Done**. |
| Update Python, `_camera_variant` and Enforce together | **Done**. |
| Ordinary/200/boundary/apply-failure/short-timeout runner tests | **Missing actual-runner coverage**; the Python model covers these cases. |
| PBO rebuild; no launcher reseal | Rebuild **missing**, deferred; launcher unchanged. |
| Menu, camera, graceful-close and retirement repros | **Missing**, deferred. |
| **9efc/aecb:** active client state vocabulary | **Done**. |
| Registered identity and observation timestamp | **Done**, but concurrent publication can regress the observation. |
| First observed-dead timestamp | **Wrong concurrently** — F3. |
| Nullable exact exit code/time from retained evidence | **Done sequentially; wrong concurrently** — F3. Shipping guard supplies neither field today. |
| Identity-aware observation; PID-reuse protection | **Done** for snapshot classification. |
| Preserve previous identity across reattach | **Done**, subject to bounded retention and F3. |
| Bounded records | **Done**, capped at 32. |
| Surviving server stays active; status makes no management calls | **Done** in the added observation path. |
| Requested diagnostic tests | **Done partially**; deterministic late-observation/evidence-preservation coverage **missing**. |
| Controlled camera-exit trials and separate distant-camera trial | **Missing**, deferred. |
| No clamp, automatic retry or claimed camera-exit fix | **Done**. |

## GATE GAP

The new concurrency test overlaps probes but returns equivalent replacement-client observations. It does not test conflicting observations or assert that previously published death evidence survives. F3 therefore escapes the reported gate.

The gate also cannot detect:

- Enforce compiler or engine behavior differences. The camera test runs a handwritten Python model.
- Real lease/adoption/worker-launch races. Successful identity preservation is supplied by the execution fixture.
- Deployment of an older PBO. Python accepts `settle_ticks=200`, but the older bridge retains its five-second deadline and can return `timeout` despite `timeout_s=40`. Application may already have occurred; no new capability/version signal distinguishes the behavior.

Static Enforce review found no new unsupported construct. Constants, bounds checks, existing method calls and deadline arithmetic follow patterns already present in the same file. This is compile-risk assessment, not compilation evidence.

## PREMISE

The supplied gate PASS is valid relative to its stated baseline; it does not establish in-game acceptance or eliminate F3.

The report’s initial CHANGES section still describes fail-mode waiting as unchanged. Its ROUND 2 FIXES section and current source correctly show the new admission behavior.

Attribution of client exits to `camera_set`, launch-menu behavior and healthy graceful-close timeouts remains unproven. Keeping those symptoms open is appropriate.

## NOT VERIFIED

- Enforce compilation, PBO build/deployment and all in-game scenarios.
- Real transport → lease → adoption → worker-launch recovery.
- Exact exit evidence from a production observer.
- Full regression suite independently.

The default Python failed import because `anyio` was absent. The project Python 3.11 venv imported successfully, but the full 18-test module encountered 16 fixture errors because this read-only sandbox cannot create temporary directories. The two tests requiring no temporary fixtures passed.

The admission check and F3 reproducer ran entirely in memory. No files were modified or DayZ processes contacted.

