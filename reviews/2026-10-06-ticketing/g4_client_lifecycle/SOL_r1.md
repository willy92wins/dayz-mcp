# gpt-6.1-sol review, g4_client_lifecycle, round 1 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P2 — Eligible client extensions still fail when a positive box-wait budget is supplied.**  
[tools/dayz_mcp/server.py:3973](C:/Users/guill/dzmcp_gauntlet/g4_client_lifecycle/ws/tools/dayz_mcp/server.py:3973)

Executable scenario: caller is the original launcher, lease released, run `R` is `RUNNING_IDLE`, project matches, server alive, client confirmed dead, scans known, no other occupant. Call:

```text
dayz_test_run(project="ExampleMod", mode="client", run_id="R",
              takeover=false, on_busy="fail", wait_for_box_s=0.02)
```

Only queue mode skips the box wait. This request waits for the surviving server’s occupied box to become free, then returns `status="failed", error_code="takeover_required"` without reaching extension execution.

A source-extracted orchestration reproducer with fake transport boundaries produced:

```text
eligible=True
status=failed
error_code=takeover_required
extension_calls=0
```

**Fix:** apply eligible-extension admission before the wait in both admission modes. Preserve the blockers and downstream lease/adoption/replacement checks. Add a public-orchestration test with `on_busy="fail"` and a positive budget.

**F2 — P2 — Concurrent status collection can invalidate the diagnostics iterator and break status responses.**  
[tools/dayz_mcp/process_lifecycle.py:2442](C:/Users/guill/dzmcp_gauntlet/g4_client_lifecycle/ws/tools/dayz_mcp/process_lifecycle.py:2442), mutation at `:2318`.

Executable interleaving:

1. Status reader A begins iterating `_client_role_observations.items()`.
2. Reader B observes another client identity and inserts it through `_remember_client_observation`.
3. Reader A resumes iteration.

Wrong output:

```text
RuntimeError: dictionary changed size during iteration
```

I reproduced this using the exact extracted methods and two threads. During reattach, the readers can hold snapshots containing the old and replacement client identities. The daemon uses threaded HTTP handlers; neither `status()` nor `public_status()` serializes this new table’s access. The exception can escape `/lifecycle/status` or the daemon `/status` provider.

**Fix:** synchronize observation lookup, insertion, pruning and publication. Publish a detached snapshot under a dedicated lock; keep process probing outside the critical section. Test overlapping status reads during identity replacement.

## SPEC COVERAGE

“Done” below means verified statically unless runtime evidence is explicitly stated.

| Specification bullet | Coverage |
|---|---|
| **7055:** explicit client-extension admission path | **Done**, for immediate and queue paths. |
| Eligible mode, supplied run ID, matching project, `RUNNING_IDLE`, original launcher | **Done** in `_explicit_client_extension_row`. |
| Exempt only the requested row | **Done**; exemption requires the takeover target to equal `run_id`. |
| Distinguish extensions before queue/own-run refusals | **Done** for queue; **wrong** for positive-wait immediate admission — F1. |
| Preserve other managed runs, foreign DayZ and unknown-scan blockers | **Done** in the exemption checks. |
| Preserve protected state and concurrent-transition checks | **Done statically** through existing downstream authorization and state validation. |
| Existing validation, internal lease acquisition, adoption and replacement gates | **Done**; those paths remain unchanged. |
| Full session ID remains authoritative in daemon | **Done**; `_client_is_launcher` and adoption protection remain unchanged. |
| Public prefix selects but does not authorize | **Done statically**; full authorization is downstream. |
| Generic fresh-launch takeover classification unchanged | **Done**. |
| Healthy polling client remains `client_already_polling` | **Done** through existing replacement validation. |
| Same run/server/world survives; only client replaced | **Done statically**; **missing runtime proof**. The new fixture supplies the successful identities itself. |
| Requested admission tests | **Done partially**. Positive-wait admission and real adoption-to-launch ownership races are **missing**. Prefix collision tests check the private authorization predicate separately. |
| Sentinel-based in-game acceptance | **Missing**, appropriately deferred. |
| Python-only reattach fix; reseal conditional on bundled changes | **Done**; worker/transaction modules were not changed. |
| **9336:** preserve camera wire fields and result shape | **Done**. |
| Deadline covers effective settle plus five seconds | **Done statically**, at `MCPClientBridge.c:1804`. |
| Public/wire bounds `0..600`; zero means three ticks | **Done**. |
| Reject invalid values before queueing/applying | **Done** in Python, wire schema and Enforce dispatch validation. |
| Document tick duration and caller timeout budget | **Done**. |
| Timeout leaves completion/application unknown | **Done**; no success or non-application inference was added. |
| Update Python, `_camera_variant` and Enforce together | **Done**. |
| Ordinary/200/boundary/apply-failure/short-timeout runner tests | **Missing actual-runner coverage**. These cases execute a handwritten Python model plus a source-string assertion. |
| PBO rebuild | **Missing**, deferred to the in-game cycle. |
| Menu, camera comparison, graceful-close and retirement repros | **Missing**, deferred; no unsupported fixes were introduced. |
| **9efc/aecb:** active client state vocabulary | **Done**. |
| Registered identity and observation timestamp | **Done**. |
| First observed-dead timestamp distinguished from exact exit time | **Done sequentially**; concurrent publication is **wrong** — F2. |
| Nullable exact exit code/time from retained observation | **Done conditionally**; the shipping guard currently supplies neither field. |
| Identity-aware observation and PID-reuse protection | **Done** for current snapshot classification. |
| Preserve previous identity across reattach | **Done sequentially**; synchronization is **missing**. |
| Bounded records | **Done**, capped at 32. |
| Leave surviving server active; status performs no process management | **Done** in the new methods. |
| Requested diagnostic tests | **Done partially**: dead client/live server, unknown, PID reuse, reattach, restart and bound. Explicit late-observation and concurrent-publication tests are **missing**. |
| Controlled camera-exit repros | **Missing**, deferred. |
| No coordinate clamp, automatic retry or claimed camera-exit fix | **Done**. |

## GATE GAP

The supplied gate misses both blocking defects:

- Admission tests cover `("fail", 0.0)` and queue mode, excluding positive-wait immediate admission.
- Diagnostics tests are sequential, so dictionary insertion/pruning races remain invisible.

The gate also cannot prove Enforce compilation or execution. The camera test models the intended deadline independently of actual Enforce dispatch, application and reporting. The successful reattach fixture does not execute daemon adoption or worker launch; it returns the expected server identity and replacement PID.

Against an **older PBO**, Python accepts `settle_ticks=200`, but that PBO retains the five-second job deadline and can return `timeout` before settling completes—even with `timeout_s=40`. The camera may already have been applied. No new capability/version signal distinguishes this behavior.

## PREMISE

The supplied offline PASS establishes regression results relative to the stated baseline, not engine compilation or in-game acceptance.

There is still no demonstrated mechanism proving that `camera_set` causes client process exits. Launch-menu behavior and healthy graceful-close timeout remain `NEEDS_REPRO`. Keeping those symptoms open is correct.

The older-PBO limitation is a deployment gap; preserving bridge version `"10"` because a test pins it does not establish behavioral compatibility.

## NOT VERIFIED

- Enforce compilation, PBO build/deployment and all in-game acceptance scenarios.
- Real transport → lease → adoption → worker-launch recovery.
- Exact process exit evidence from a producer retaining that identity.
- Full regression suite independently.

I attempted `python -B -m unittest tests.test_client_lifecycle_7055_9336_9efc` from `tools/`; import failed because the available Python lacks `anyio`. The orchestrator’s reported 17-test pass remains external evidence. Both failure reproducers used source-extracted code and standard-library fakes, without managing DayZ processes.

No files were modified.