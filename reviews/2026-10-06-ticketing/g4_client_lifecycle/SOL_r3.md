# gpt-6.1-sol review, g4_client_lifecycle, round 3 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F3 — P2 — A delayed death observation still replaces previously recorded death evidence.**

Location: [tools/dayz_mcp/process_lifecycle.py:816](C:/Users/guill/dzmcp_gauntlet/g4_client_lifecycle/r3/ws/tools/dayz_mcp/process_lifecycle.py:816), with the timestamp captured before probing at [line 2443](C:/Users/guill/dzmcp_gauntlet/g4_client_lifecycle/r3/ws/tools/dayz_mcp/process_lifecycle.py:2443).

The merge is atomic, but `min(stamps)` allows an older incoming observation to change the first-death timestamp already published by another reader. Clamping `observed_at_utc` afterward does not preserve that evidence.

**Executable scenario:** use the existing `_BlockedProbe` interleaving through actual `public_status()`, with an in-memory manifest containing `RUNNING_IDLE` run R, live server PID 801 and registered client PID 802.

1. A obtains timestamp `00:00:00.500Z`, then blocks inside the guard before observing the client.
2. The client exits at `00:00:00.900Z`.
3. B probes at `00:00:01.000Z` and receives:

   ```python
   {"error": "process_not_found", "exit_code": 4,
    "process_exit_code": 1,
    "exit_time_utc": "2026-08-01T00:00:00.900Z"}
   ```

4. A resumes with `{"error": "process_not_found", "exit_code": 4}`.
5. C confirms death at `00:00:02.000Z`.

**Reproduced wrong output:**

```text
B: first_observed_dead_at_utc = 00:00:01.000Z
A: first_observed_dead_at_utc = 00:00:00.500Z
C: first_observed_dead_at_utc = 00:00:00.500Z
retained exit_time_utc        = 00:00:00.900Z
```

The recorded first death changes permanently and precedes the exact exit time.

The same merge also preserves exact exit fields only when incoming fields are null ([line 818](C:/Users/guill/dzmcp_gauntlet/g4_client_lifecycle/r3/ws/tools/dayz_mcp/process_lifecycle.py:818)). In the same harness, supplying older non-null values `process_exit_code=2`, `exit_time_utc=00:00:00.400Z` replaces B’s recorded `(1, 00:00:00.900Z)`. This violates the directive’s retention rule; conflicting producer evidence should not silently overwrite recorded fields.

**Suggested correction:** retain an existing first-death timestamp and recorded exact exit fields, filling only missing evidence. Apply observation ordering to the merged evidence, not merely its displayed timestamp. Add the delayed-`dead` interleaving regression.

F1 and F2 remain closed; no regression in their protected paths was found.

## SPEC COVERAGE

“Carried forward” means unchanged from the previous implementation within this bounded review.

| Round-3 requirement | Status |
|---|---|
| Guard probe outside observation lock | **Done** |
| Merge against latest stored observation under lock | **Done** |
| Prevent weaker observations replacing stronger state | **Done** |
| Prevent older observations replacing newer evidence | **Wrong** — F3 |
| Preserve first observed death timestamp | **Wrong** — delayed death changes it |
| Preserve recorded exact exit fields | **Wrong** — only null incoming fields preserve them |
| Return detached merged result | **Done** |
| Deterministic A-unknown/B-dead/C-confirm regression | **Done** |
| Regression fails on previous implementation | **Done** — independently reproduced |
| Leave F1/F2 fixes intact | **Done** |
| Add `## ROUND 3 FIXES` to report | **Done** |

| Original specification | Status |
|---|---|
| Explicit client-extension eligibility: mode, run, project, idle state and launcher | **Done, carried forward** |
| Exempt only requested row from takeover | **Done, carried forward** |
| Apply exemption before queue/own-run refusals | **Done, carried forward** |
| Preserve other managed-run, foreign-process, unknown-scan and transition blockers | **Done, carried forward** |
| Keep existing extension, lease, adoption and replacement gates | **Done, carried forward** |
| Full-session daemon authorization; prefix selects only | **Done, carried forward** |
| Generic fresh-launch takeover behavior unchanged | **Done, carried forward** |
| Healthy client remains `client_already_polling` | **Done, carried forward** |
| Client-extension regression coverage | **Done, carried forward** |
| Same server identity/world state after replacement | **Missing runtime verification** |
| Camera wire/result shape preserved | **Done, carried forward** |
| Deadline covers settle plus five seconds | **Done statically** |
| Public, wire and Enforce bounds `0..600`; zero means three ticks | **Done statically** |
| Validate before queue/apply | **Done statically** |
| Document settle duration and caller timeout budget | **Done** |
| Timeout leaves completion unknown | **Done** |
| Camera runner-transition tests | **Done as Python simulation; Enforce execution missing** |
| Active client state, identity and observation timestamp | **Done, carried forward** |
| Preserve first death and exact exit evidence | **Wrong** — F3 |
| PID reuse isolation, previous-client retention and bounded records | **Done, carried forward** |
| Status does not manage processes | **Done in exercised scenarios** |
| Diagnostics regressions | **Done, with delayed-death gap** |
| Composable UI/close/camera and paired SecretRock/distant-camera repros | **Missing runtime verification** |

## GATE GAP

Both new regressions use a delayed **unknown** result. They miss a delayed **dead** result and conflicting non-null exit evidence.

The camera test implements a Python approximation using source constants; it cannot establish Enforce compilation, actual scheduling or transport behavior. An older PBO still uses the five-second camera deadline and can time out a 200-tick request despite a longer caller timeout.

## PREMISE

The reported gate measures offline compatibility, not in-game acceptance. Static inspection found no new Enforce language construct beyond patterns already present in the same file.

F3’s requested unknown-result interleaving is fixed. That narrower success does not satisfy the directive’s complete evidence-preservation requirement.

## NOT VERIFIED

- Standard targeted command was attempted: **20 tests, 18 fixture errors** because the read-only sandbox cannot create temporary directories.
- With only the disk-backed fixture replaced by an in-memory boundary, both new regression bodies passed on this tree and failed on the previous implementation. The additional F3 scenarios above were reproduced through actual `public_status()`.
- No Enforce compilation, PBO build, DayZ process management or in-game testing occurred.
- Server, loopback, Enforce, worker and extension-validation files were byte-identical to the previous workspace. No files were modified.