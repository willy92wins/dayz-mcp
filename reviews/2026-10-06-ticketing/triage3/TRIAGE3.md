# Ticketing session, triage round 3 (2026-10-08): open DayZ-MCP inbox findings on main `ba07cca`

Scope: 102 unresolved inbox entries; 21 concern the MCP. The rest belong to other projects (mod knowledge,
the script validator, terrain and build scripts) or to the agent pipeline and harness, and are left to their
owners. Verification against the tree: gpt-6.1-sol, read-only, with the verbatim ticket bodies
(`SOL_TRIAGE3.md`). Orchestrator checks are marked as such.

| Item | Verdict | Inbox action |
|---|---|---|
| 39b8 named-instance profiles folders | fixed by #220, not deployed | resolve after the v11b deploy and its in-game check |
| 9376 + 250f abandoned runs block the box | handled: idle warden on since 2026-10-06 | resolve (evidence below) |
| 6ed1 CRLF copies in the sealed launcher | PARTLY_FIXED: 3 working copies left | renormalize in the v11b reseal, then resolve |
| bf5c + 8cf9 AddonBuilder `-addon` exposes sibling projects | OPEN_CONFIRMED, PR #195 | merged with main for v11b; resolve after the build acceptance |
| a97e binarize exit 2 on a Dokan source | NEEDS_REPRO (did not reproduce on 2026-10-01) | keep open |
| 713a, 66cc AddonBuilder materials and determinism | context for #195: the acceptance reads the ODOL | left to SimpleGroup |
| 9941 + 01d3 continuous actions; probe procedure | OPEN_CONFIRMED | keep; fix the probe procedure, run the trials |
| d1a8 hold mode (flag raise/lower) | DUPLICATE_OF 9941 (repeat case kept) | resolve as duplicate |
| 9ab8 generic in-game actions | PARTLY_FIXED: `action_use` is generic; continuous actions remain 9941 | keep |
| 86a3 look at a point / action cursor state | OPEN_CONFIRMED | keep (needs design) |
| c440 slow texture streaming after a teleport | OPEN_CONFIRMED (requests); focus as cause is a hypothesis | keep; document now |
| 75e7 + fade (f298) launch takes the user's focus | PARTLY_FIXED: capture and DayZ launch no longer activate; the native worker console remains a hypothesis | keep; passive focus probe in the v11b cycle |
| 33ed tests red only under the whole fast tier | PARTLY_FIXED: bug046 signal is atomic on main; the coordination test reads a snapshot before it is persisted | keep; fix the coordination test |
| ce72 provider keys in HKCU\Environment | NOT_IN_MCP for the host residual; MCP children use the whitelist | owner question |
| 7695 offline animation timeline | OWNER_DECISION | keep (no consumer for a verb yet) |
| 1d31 ESC and the pause menu | PARTLY_FIXED | keep; check in the v11b cycle |
| d490 black capture with the display asleep | NEEDS_REPRO | keep |

## 9376 + 250f evidence (orchestrator)
- `%LOCALAPPDATA%\DayZ_MCP\idle-warden.json` is `{"enabled": true}` since 2026-10-06 (owner decision,
  `../DECISIONS.md`).
- The daemon audit (`audit\events.jsonl`) records two orderly closes by the warden on 2026-10-07:
  `{"event":"idle_timeout","reason":"idle_timeout","decision":"orderly","run_id":"94556294-fa18-42ca-81fc-c78f0a4c3ee2",...,"timestamp_utc":"2026-10-07T14:23:14.822100Z"}`
  and the same for run `b4d19d6e-5aa1-4adb-8f24-961deb92e82b` at `2026-10-07T18:35:57.031900Z`.
- `session_status` on 2026-10-08 shows each run's `use_state` (`human`, `agent`), `human_input_age_s`, the
  time until it can be abandoned, and `launched_by` (250f requests A and D; 9376 request 3).
- Not built, by the owner's 2026-10-06 decision: `session_acquire_wait(adopt=false)` (9376 request 1).

## Outcome (2026-10-08, same session)
- Owner's round: A 33ed (#223) and B c440 (#224), Grok 4.7 implementing (the GX10 was full) and gpt-6.1-sol
  reviewing (A approved in r2, B in r1); C the 9941 probe procedure (corrected, READY, trials run: H1 supported);
  D the 86a3 design (`SPEC_86A3.md`; the aim-unit experiment ran in game: radians per pulse).
- Deployed with the v11b reseal: #220, #221, #222 and #195 (merged with main; Sol approved; CI 4/4). Results:
  `../ingame-v11b/RESULTS.md`.
- Resolved in the inbox: 9376, 250f, d1a8, ce72 (MCP part), 39b8, 6ed1, bf5c, 8cf9, 01d3.
- Filed: 2986 (1.30 close skips the logout wait: localized join line), 855c (r9c E2 not end to end on the
  lifecycle path), 222d (binarize builds pack no scripts), e92d (MSIX virtualization of %LOCALAPPDATA% for
  processes started from the Claude app), b58f (hold mechanism and aim units measured).
- Round 2, chosen by the owner in the session on 2026-10-08 after the deploy: A (855c + 2986, Python), B (hold
  mode for continuous actions, 9941, Enforce) and C (86a3 action cursor + look-at, Enforce); D (222d builds with
  scripts) left for a later reseal window.
