# gpt-6.1-sol review, g2_kill_bots, round 2 (unedited)

VERDICT: APPROVED

## FINDINGS

The previous **P2 F1 is fixed**: cleanup stops are registered atomically, survive the run fence, and receive the narrowly scoped delivery authorization at `tools/dayz_mcp/loopback.py:2724`, `:3326`, and `:3786`. I reproduced delivery after release with the run fenced.

The previous **P2 F2 is fixed**: admission checks announcement age and bound instance before allocating an ID; retirement invalidates that instance’s census. Evidence: `tools/dayz_mcp/loopback.py:2676`, `:2696`, `:2799`, and `:1986`. The ageing and replacement-binding test passes.

**F3 — P3 — Bridge refusals of cleanup stops are silently discarded.**

Locations: `tools/dayz_mcp/loopback.py:2724`, `:3618`.

The new cleanup stops use fire-and-forget result handling. Although daemon-side discards now retain a negative result, a negative response **received from the bridge** is removed without reporting cleanup degradation.

Executable Python negative-response scenario:

1. Acquire a lease; enqueue and deliver movement on object `9`.
2. Store the successful start response.
3. Release the lease; poll and receive cleanup `bot_stop`, ID `2`.
4. Inject the accredited bridge response:
   ```json
   {"id":2,"ok":false,"error":"bot_unavailable"}
   ```
5. Read the result and session status.

Observed:

```text
store_result: (200, {"ok":true,"id":2,"ok_value":false})
take_result(2): None
session cleanup_degraded: []
```

This is a diagnostic gap, not a reproduced failure of the server TTL. Suggested fix: retain and expose failed cleanup acknowledgements through status or audit, without treating successful enqueue as confirmed cessation.

No remaining P1/P2 finding was established within the round 2 scope.

## SPEC COVERAGE

“Done” means present in the implementation and checked statically, unless execution is explicitly stated. The three Enforce diff sections are unchanged from round 1; their reviewed coverage is carried forward.

**f4de — `player_kill`**

| Specification requirement | Status |
|---|---|
| Required nonempty opaque UID; no first-player fallback | **Done** — `server.py:4938`; `MCPBridge.c:1564`. |
| Public timeout/default; server routing with exactly `{"uid": …}` | **Done** — `server.py:4946`; focused wire test passes. |
| Closed public and wire schemas; reject misspelled/unknown arguments | **Done** — focused schema tests pass. |
| Reuse `MCPArgs.uid`; required result fields | **Done** — `MCPMessages.c:875`. |
| Dispatch beside heal/godmode | **Done**. |
| Validate arguments before resolving | **Done** — `MCPBridge.c:1558`. |
| Resolve exact player; require identity and matching UID | **Done** — `MCPBridge.c:1572`, `:1586`, `:1593`. |
| Refuse already-dead player before mutation | **Done** — `MCPBridge.c:1599`. |
| Record health, alive state and damage permission | **Done** — `MCPBridge.c:1606`. |
| Release body godmode through the existing mechanism | **Done** — `MCPBridge.c:1612`. |
| Invoke `SetHealth(0)` without fabricated death callbacks | **Done** — `MCPBridge.c:1613`. |
| Read back authoritative death before success | **Done**, with the previously documented `<= 0` predicate instead of exact equality — `MCPBridge.c:1616`. |
| Preserve remembered godmode; restore damage permission if kill fails | **Done** statically — `MCPBridge.c:1620`. |
| Keep client respawn separate; do not respawn automatically | **Done**. |
| Required handler errors and public error propagation | **Done** statically. |
| Command, closed schema, tool mapping and Enforce capability registration | **Done** — contract/census tests pass. |
| Combined argument hash and synchronized version bump | **Done** — `e5a0ed288dbae72f`, version `"11"`. |
| Fresh accredited capability/hash admission before queue/ID allocation | **Done** — previous F2 fixed. |
| Repeat capability/hash check against the bound delivery peer | **Done** — `loopback.py:3304`; delivery-refusal test passes. |
| Lease authorization before admission and commit | **Done** — existing authorization/commit paths retained. |
| Old version, missing verb, missing/wrong hash and stale census refusal | **Done** — focused tests pass; independent old-version, missing-verb and wrong-hash probes refused all three verbs with queue depth `0` and next ID `1`. |
| No/expired lease, foreign run and retired-binding checks | **Done** in focused tests. |
| Exact target, unknown UID, already-dead and godmode/failure behavior tests | **Missing behavioral execution** — source assertions cover portions of these requirements. |
| Timeout-after-dispatch test | **Missing** from the added tests. |
| Death/death-screen, unrelated-player isolation, repeated kill, replacement body within 40 seconds and preserved godmode | **Missing** — deferred in-game acceptance. |

**120f — `bot_start` / `bot_stop`**

| Specification requirement | Status |
|---|---|
| Public signatures/defaults and exact wire arguments | **Done** — `server.py:4971`, `:4990`, `:5004`. |
| Positive strict object IDs; finite `0 < ttl_s <= 30` | **Done** — public/wire validation and tests. |
| Add `bot_ttl_s`; reuse object ID/action | **Done** — `MCPMessages.c:194`. |
| Registry-only living `PlayerBase`, without connected identity | **Done** statically. |
| Ten exact action mappings; reject raw integers, stop-current and C2H | **Done** statically; malformed-action tests pass. |
| Nested three-macro exclusion of bot-dependent symbols | **Done** as source checks. |
| Cross-version signatures; no addon-defined engine macros | **Done** statically. |
| Unsupported branch validates shape, then refuses before initialization | **Done** statically — `MCPBridge.c:1637`, `:1643`, `:1673`, `:1679`. |
| Same contract advertised on both versions | **Done**. |
| Initialize once, independently of action acceptance; require `m_Bot` | **Done** statically. |
| Submit vanilla events through `ProcessEvent`; report accepted start only | **Done** statically. |
| Track object/originating command; reject duplicate active start | **Done** statically. |
| Track active lease ownership | **Wrong/incomplete**, unchanged from round 1: daemon tracking records admitted starts without reconciling rejected starts or cessation. |
| Explicit stop, TTL, deletion, death and shutdown hooks | **Done** statically. |
| Owner cleanup with independent server TTL | **Done** at daemon delivery level — previous F1 fixed; bridge-failure diagnostics remain F3. |
| Lease-expiry cleanup | **Done** in an additional Python reproduction with a fresh census: expiry delivered `bot_stop`. |
| Initialized-idle stop is idempotent | **Done** statically; the existing branch also accepts never-initialized idle dummies. |
| Required results/errors; closed schemas, hash gates and lease policy | **Done** statically and through focused Python tests. |
| Offline allowlist, malformed arguments and older-PBO checks | **Done**. |
| Unsupported compile-branch checks | **Done as source checks**; actual compilation is missing. |
| Registry/type/identity, duplicate start, rejected transition, idempotence and TTL behavior tests | **Missing behavioral execution**. |
| 1.29 refusal without initialization or compile errors | **Missing** — deferred runtime acceptance. |
| 1.30 movement, explicit/TTL cessation, lease-release cleanup and deletion refusal | **Missing in-game acceptance**; daemon release delivery is independently verified. |
| Independent effect checks for every shipping action, otherwise restrict allowlist | **Missing** — still a shipping acceptance requirement. |

## GATE GAP

The gate cannot detect:

- Enforce compiler rejection of either macro branch.
- Incorrect engine death, godmode restoration or respawn behavior.
- Bot FSM acceptance without the expected effect, or ineffective stop/TTL cessation.
- Cleanup acknowledgements silently discarded under F3.
- Runtime ownership errors hidden by source-only lifecycle assertions.

I independently ran **19 focused tests plus 91 argument/hash/capability tests: all passed**, using the project venv. Additional read-only Python probes exercised fenced cleanup delivery, expiry cleanup and compatibility refusals.

## PREMISE

The supplied gate is a **baseline-relative PASS**, retaining two baseline failures; it is not a clean full-suite result.

Compilation and in-game acceptance are explicitly deferred. Approval here does not establish readiness to ship all ten bot actions.

`REPORT.md:7` and `:62` are outdated: round 2 centralizes cleanup, and lease expiry can now enqueue and deliver bot stops. I reproduced that behavior.

## NOT VERIFIED

- Actual Enforce compilation on 1.29 or 1.30 and current macro availability.
- Authoritative death, client death screen, respawn body identity and godmode persistence.
- Bot effects, movement cessation and runtime TTL enforcement.
- Real older-PBO traffic.
- Full production lifecycle integration with a real manifest.
- The complete fast tier independently rerun.

No files were modified and no commit was made.

