# Owner decisions, ticketing session 2026-10-06

Decided by the owner in the session, from the options of `TRIAGE.md` and `SOL_REVIEW.md`.

| Item | Decision | What follows |
|---|---|---|
| c561 (scriptless PBO passes the boot gate) | Opt-in project attestation | Spec, then implementation batch |
| 97de + 0cb3 (second MCP instance blocked by any dayz_mcp process) | Test whether independent instances work; if feasible, make it supported | Investigation, then design and implementation |
| 9376 + 250f (abandoned foreign runs block the box) | Enable the idle warden | Done 2026-10-06: `%LOCALAPPDATA%\DayZ_MCP\idle-warden.json` = `{"enabled": true}` (backup `idle-warden.json.bak-20261006-ticketing`); no code change |
| 584e + f0e3 (owner reads do not renew the lease) | Expose `lease_ttl_s` in results while a lease is held; reads keep not renewing | Implementation batch |
| af90 (wait_for players_* without a lease on another session's run) | Keep H3: pure reads need no lease | Closed as documented behaviour |
| 31d2 (preflight refused with takeover_required while another session holds the box) | Preflight without the box: validate what does not need it, answer ok with `box_busy` and `occupied_by_run_id` | Implementation batch (H11/H13 amendment) |
| f4de (no headless way to kill a living player) | Add `player_kill` | Implementation batch (Enforce, in-game cycle) |
| 983a (camera_set does not expose the applied FOV) | Echo `fov_applied` (null when fov=0) and document the measured default; no engine getter | Implementation batch |
| 9941 (continuous actions are started but never completed) | Investigate a real hold mechanism | Investigation with an in-game experiment |
| d17c (wait_for on mission $profile files; camera_set on a dead client waits the whole timeout) | Both: `client_process_gone` fast failure and `wait_for` `file_matches` | Implementation batches |
| 2837 (base_mods candidate PBO loads beside the live project mod) | Explicit `project_mod_override` | Implementation batch (sealed launcher: reseal) |
| 120f (bot verbs for 1.30) | Implement `bot_start` / `bot_stop`, fail-closed `bot_unavailable` on 1.29 | Implementation batch, verified on the 1.30 instance |
| 7055, 9336, 9efc + aecb, 449e, d490, a97e (need repro) | gpt-6.1-sol re-triage with the whole tree first; what still needs a repro goes into one shared in-game cycle | Review |
| Inbox closures | The orchestrator resolves 6084, 7672 and af90 now, and each ticket when its batch merges | Done for 6084, 7672, af90 |

Closure evidence:
- 6084: `SOL_REVIEW.md` ITEM 6084 (launch check through `evaluate_steam_session()` with the WMI provider, `dayz_test_tool.py:1994`, `steam_preflight.py:446-448`, `:168-181`; outside-app daemon spawn `server.py:1613-1620`, `daemon.py:1836-1839`). Fallback and embedded execution remain separate conditions.
- 7672: fixed by #194 (`6671fd2`, "publishing the bundle names the processes that hold the previous one and retries for a bounded time").
- af90: this table.

## Later decisions (same session)

| Item | Decision | What follows |
|---|---|---|
| Integration | One PR per batch, merged with CI 4/4 green and the gpt-6.1-sol approval; Enforce batches wait for the in-game cycle | Applied to every merge of the session |
| Local main's 3 debug-gate commits | A PR with those commits | Merged as #197 |
| 97de details | Own tools tree per instance; upgrade both daemons before concurrent acceptance; the default instance is stable DayZ, token `130` is 1.30 Experimental (its store kept) | Batch g5 (`specs/SPEC_97DE.md`) |
| g1 (584e + f0e3, d17c-b) | Full scope plus one more round, then consolidation | Approved in round 5: #207 |
| In-game window | Start when the c8c7 DZ-R9 fixes are approved; check the box and proceed only if nobody uses it | See the next row |
| Shared `@DayZ_MCP` PBO | The 1.30 instance loads the same `P:\Mods\@DayZ_MCP` and its daemon requires bridge "10", while the Enforce candidate is "11": wait for g5 and promote both instances together | In-game cycle postponed; candidate kept ready |
| 9941 | An opt-in probe batch now, for the next cycle | Batch g6 (`specs/SPEC_9941_PROBE.md`) |
| 130 instance (120f bots on 1.30) | Later, with g5 | |

DZ-R9 audit of c8c7 (#205): fixes in `b2_c8c7/r9/` of #205; the pre-existing storage recovery defect it found is
inbox `fb-20261006-145731-c5ac` (spec `specs/SPEC_STORAGE_RECOVERY.md`).
