# gpt-6.1-sol review, g2_kill_bots, round 1 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P2 — Lease-release bot cleanup never reaches the bridge.**

Locations: `tools/dayz_mcp/loopback.py:2684`, `:2471`, `:3283`, `:4366`.

`enqueue_bot_stops_for_lease()` queues `bot_stop` with `internal=True`, which gives it no command owner. Delivery rejects ownerless mutations as `authority_missing`; its cleanup exception covers only `vehicle_release`.

**Reproduced scenario:**

1. Acquire a lease.
2. Enqueue and deliver `bot_start(object_id=9, action="PLAYER_BOT_RANDOMIZE_MOVEMENT", ttl_s=30.0)`.
3. Release the lease successfully.
4. Execute the release handler’s `enqueue_bot_stops_for_lease(lease_id)`.
5. Poll with the accredited server instance, current capabilities and correct hash.

Observed:

```text
cleanup queued: [{"id":2,"cmd":"bot_stop","args":{"object_id":9}}]
cleanup delivery: {"commands":[],"bind":"BOUND"}
cleanup result: {"id":2,"ok":false,"error":"authority_missing"}
```

The server receives no stop event. An accepted bot action therefore remains controlled until its TTL or another cleanup trigger. Release reports no cleanup degradation.

**Suggested fix:** perform bot cleanup through the existing fenced owner-cleanup path, authorize its specifically registered cleanup commands at delivery, and report enqueue/delivery failures. Add a test that polls after release. Do not broadly exempt internal mutations from authorization.

**F2 — P2 — Capability admission accepts indefinitely stale announcements.**

Locations: `tools/dayz_mcp/loopback.py:2663`, `:2760`, `:3860`.

The capability view checks daemon generation, commands and hash. It checks neither announcement age nor the originating bound instance. `_record_poll_caps_locked()` stores no announcement timestamp or instance.

**Reproduced scenario:** receive an accredited `11~1.30.0` announcement at time `0`, advance the state clock to `1000` without another poll, acquire a valid lease, then enqueue:

```json
{"cmd":"player_kill","args":{"uid":"76561198000000001"}}
```

With the real version classifier and lease coordinator enabled, observed:

```text
(200, {"id":1,"peer":"server","cmd":"player_kill"})
queue depth: 1
next_id: 2
```

The specification requires a fresh announcement and refusal without queue growth or ID consumption. Delivery still rechecks capabilities; this reproduction proves the admission defect, not delivery of an unsupported command to an older PBO.

**Suggested fix:** associate each accredited announcement with its instance and time, require freshness against the selected binding before allocating an ID, and invalidate announcements when that binding changes. Test same-generation ageing and binding replacement.

## SPEC COVERAGE

“Done” below means implemented and inspected statically, except where execution is explicitly stated.

### f4de — `player_kill`

| Specification requirement | Status |
|---|---|
| Required nonempty UID; no first-player fallback | **Done** — `server.py:4938`; `MCPBridge.c:1564`. |
| Public timeout parameter; exact UID-only server wire arguments | **Done** — `server.py:4940`, `:4944`. |
| Closed public schema and closed wire schema | **Done** — registration and validation present; focused tests pass. |
| Reuse `MCPArgs.uid`; required result fields | **Done** — `MCPMessages.c:879`. |
| Dispatch beside heal/godmode | **Done** — `MCPBridge.c:550`. |
| Validate before resolution | **Done** — `MCPBridge.c:1558`. |
| Resolve exact UID; require and compare identity | **Done** — `MCPBridge.c:1572`, `:1586`, `:1593`. |
| Refuse an already-dead body before mutation | **Done** — `MCPBridge.c:1599`. |
| Record health, alive state and damage permission | **Done** — `MCPBridge.c:1606`. |
| Release body godmode through `ReleaseBody` | **Done** — `MCPBridge.c:1612`. |
| Call vanilla `SetHealth(0)`; no fabricated death callbacks | **Done** — `MCPBridge.c:1613`. Vanilla definitions were re-read. |
| Read health/alive back; success only after death | **Done**, with a minor predicate deviation: `health_after <= 0`, rather than exactly zero, at `MCPBridge.c:1616`. |
| Preserve remembered godmode policy; restore damage permission on failure | **Done** — no policy write; restoration at `MCPBridge.c:1620`. |
| No automatic respawn; retain separate client respawn | **Done**. |
| Required handler error codes; public `ToolError` propagation | **Done** statically. |
| Command, tool, dispatch and capability census parity | **Done** — focused census tests pass. |
| Combined argument hash and synchronized version bump | **Done** — hash `e5a0ed288dbae72f`, bridge version `"11"`. |
| Fresh accredited admission before queueing or ID allocation | **Wrong** — F2. |
| Capability/hash recheck at delivery | **Done** — `loopback.py:3258`; reduced-capability delivery test passes. |
| Lease authorization before admission and commit | **Done** — existing authorization and commit paths retained. |
| Old version, missing verb, missing/wrong hash: refuse without queue/ID growth | **Done** — independently exercised through accredited polling for all three new verbs. |
| Stale-announcement rejection | **Wrong** — tests cover stale daemon generation only. |
| No/expired lease, foreign run, retired binding tests | **Done** in focused tests. |
| Exact target and unknown UID behavior tests | **Missing behavioral execution** — source assertions only. |
| Already-dead, godmode on/off and unsuccessful-kill behavior tests | **Missing behavioral execution** — textual assertions do not exercise these states. |
| Timeout after dispatch test | **Missing** from the added tests. |
| In-game death/death-screen, unrelated-player isolation, repeated kill, replacement body within 40 seconds, preserved godmode | **Missing** — explicitly deferred to the later in-game cycle. |

### 120f — `bot_start` / `bot_stop`

| Specification requirement | Status |
|---|---|
| Public signatures, defaults and exact wire argument names | **Done** — `server.py:4971`, `:5004`. |
| Positive strict object IDs; finite `0 < ttl_s <= 30` | **Done** — public and wire validation present. |
| Add `MCPArgs.bot_ttl_s`; reuse object ID/action | **Done**. |
| Registry-only, living `PlayerBase`, no connected identity | **Done** — `MCP_BotControl.c:169`, `:175`, `:180`, `:185`. |
| Ten exact action mappings; reject integer actions, stop-current and C2H | **Done** statically; mappings match the inspected V30 transitions. |
| Nested `DAYZ_1_30` / `ROBOCLIENT` / `INPUT_OVERRIDE` guards | **Done** — bot-dependent symbols remain inside the guards. |
| Cross-version adapter signatures; no addon-defined engine macros | **Done**. |
| Unsupported branch validates then returns `bot_unavailable` before initialization | **Done** statically — `MCPBridge.c:1637`. |
| Advertise both commands on both versions | **Done** — unconditional capability list. |
| Initialize once, independently of transition acceptance | **Done** — `MCP_BotControl.c:321`; initialization is recorded before submitting the action. |
| Require non-null `m_Bot`; use vanilla events and `ProcessEvent` | **Done** — signatures and event constructors were re-read in V30. |
| `started:true` only for an accepted transition | **Done** — `MCP_BotControl.c:351`. |
| Track object/originating command; reject duplicate active start | **Done** — `MCP_BotControl.c:189`, `:207`. |
| Track lease ownership for active controls | **Wrong/incomplete** — daemon ownership is recorded at enqueue, without reconciliation on rejection or cessation; functional release cleanup fails under F1. |
| Explicit stop, TTL, deletion, death and mission shutdown | **Done** statically — controller and bridge hooks are present. |
| Owner cleanup with server TTL as independent bound | **Wrong** — F1. Expiry/admin cleanup also has no bot-stop integration. |
| Idempotent stop for initialized idle dummy | **Done** statically — `MCP_BotControl.c:248`. The branch also accepts never-initialized dummies. |
| Required result fields and handler errors | **Done** statically. |
| Closed schemas, capability/hash gates and lease policy | **Done**, except admission freshness under F2. |
| Offline malformed arguments, allowlist and older-PBO checks | **Done**. |
| Unsupported compile-branch checks | **Done as source checks**; actual compilation remains missing. |
| Registry/type/identity, duplicate start, rejected transition, idempotence and TTL/ownership behavior tests | **Missing behavioral execution** — added tests mainly assert source strings. |
| 1.29 refusal without initialization or compile errors | **Missing** — deferred runtime acceptance. |
| 1.30 movement, explicit cessation, TTL cessation, lease-release cleanup and deletion refusal | **Missing** — deferred runtime acceptance; lease-release delivery already fails offline. |
| Independent effect checks for every shipping action, otherwise restrict allowlist | **Missing** — all ten remain enabled without those checks. |

## GATE GAP

The gate passes despite F1 and F2:

- The cleanup test checks that `bot_stop` enters the queue, but never polls it.
- The stale-census test changes daemon generation; it does not age an announcement within the same generation.
- Bot lifecycle tests look for tokens such as `"ttl"`, `"death"` and `"bot_busy"` rather than execute the controller.
- Kill tests cannot detect ineffective death, incorrect godmode restoration, or timeout-after-dispatch behavior.
- Neither the linter nor Python census tests compile either Enforce branch.

I independently ran **17 focused tests: OK**, plus **122 contract/census tests: OK, one skipped**, using the project venv.

## PREMISE

The supplied gate is a baseline-relative PASS with two existing failures, not a clean full-suite result.

The current brief explicitly defers compilation and in-game acceptance. Their absence alone is not a blocking finding in this review. It also does not waive the later requirement to validate each shipping bot action.

No definite Enforce compile error was identified statically. Guarded API definitions match V30, but the new adapter and its integration have no compilation evidence.

## NOT VERIFIED

- Actual Enforce compilation on 1.29 and 1.30.
- Current engine macro availability.
- Death, death-screen arrival, respawn body identity and godmode persistence.
- Bot FSM effects, movement cessation and runtime TTL enforcement.
- Real older-PBO traffic.
- The complete fast tier was not rerun.

No implementation edits or commit were made.

