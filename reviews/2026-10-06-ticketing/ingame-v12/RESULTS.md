# v12 window, 2026-10-09: bridge "12" and the reseal that deploys 855c

Owner go-ahead (2026-10-09): the orchestrator closes the other sessions' MCP clients only when no game runs, and
never touches a game. Step log and backups: `C:\Users\guill\DayZ_MCP_backups\v12-20261009\` (`steps.log`).

## Deploy
- **Quiescence:** 13:21-13:31, ten minutes with no DayZ process. Both instances had no owner, no queue and a free box. 13 client chains were closed and both daemons stopped. The orchestrator's own 222d batch was paused, because its Grok process respawned MCP clients.
- **Trees:** LIVE and T130 were checked out detached at `cb0fdee`, the PR #229 head at the time, and both resealed. 855c's sealed worker (#226) and the broker infrastructure (#228) went live with this reseal. Three tracked files in LIVE had a stale index stat from their CRLF era (content equal to HEAD); they were re-staged with the same blobs before the checkout.
- **PBO swaps** (built from the branch with provenance checked, the previous PBO kept beside the live one):
  - `CC616EC7` (bridge 11) → `940BE8D1` (`cb0fdee`): did not compile;
  - → `64FA0FCD` (`2f7f11c`);
  - → `BC6B6FDD` (`42e47bd`);
  - → `177471A7` (`944a358`, final).
- **Doctors:** both report only `RUN_PREPRUNE_BACKUP_SLOTS_EXHAUSTED`, the same single FAIL as the v11 promotion.

## Defects found in game and fixed (PR #229)
1. **1.29 compile error.** `mcpclientbridge.c(6544): Types 'IngameHud' and 'Mission' are not related` (86a3 cast the Mission to IngameHud). The offline linter did not report it. Fixed in `2f7f11c`.
2. **Look-at never converged.** `player_look_at` ended with termination `deadline` on every target: the view overshot and oscillated about 14° off. The camera shows an aim pulse a frame or more late, so a full-error pulse every frame overcorrects. Fixed in `42e47bd`: a gain of 0.2 per frame.
3. **1.30 compile error.** `mcp_actioncursor.c(60): Overloaded function 'Update' not compatible`. Vanilla `ActionTargetsCursor.Update()` became `Update(bool fullUpdate = true)` in 1.30. Fixed in `944a358`: the override selects its signature with `#ifdef DAYZ_1_29`.

## Accepted
- **Bridge:** `12~1.29.163709` and `12~1.30.164014.27` on both peers. The capabilities match: the client announces `action_hold`, `action_hold_cancel`, `action_cursor`, `player_look_at` and `player_look_at_release`; the server announces `action_cursor_ids`. `/status` carries the full binding tokens (#228).
- **855c** (manual joint check, 1.29). The formal `tools/dev/storage_recovery_joint_gate.py` refused, because project DayZ_MCP has no `mission_aliases` in the sealed policy.
  - Setup: an invalid journal `STORAGE_1.modset.rotation.0123…json` planted in the chernarus mission.
  - Result: `dayz_test_run` returned `failed` / `storage_recovery_required` / `storage_recovery_reason=journal_name_invalid` with the canonical remediation, and `run_id=null`.
  - Audit: exactly one `lifecycle_start`, one `lifecycle_storage_recovery_required`, and one `lifecycle_start_outcome` (same session and run). No `launch_identity_conflict`.
  - The plant was moved to the backups.
- **9941 `action_hold`** (1.29). Every hold below ended with `flag_restored` and `cleanup_complete`.
  - Drink, 10 s: `timeout` at 10.55 s.
  - Timeout, 1 s: 1.575 s.
  - Repeat, 5 s: 5.545 s.
  - Apple (`ActionEatFruit`): `finished` / `natural` at 10.64 s.
  - Cancellation through the broker: the harness sent MCP `notifications/cancelled` 2.5 s into a 30 s drink hold; "Request cancelled" came back in 56 ms. The client then received the released `action_hold_cancel` (id 47) right after the hold (id 46), and both results were posted.
- **8308** (a drink or eat action now runs while held).
  - 1.29: the apple ran to a natural finish, which this component reaches only once the item's quantity is spent (`cacontinuousquantityrepeat.c:44-46`, `:66-68`). The drink ran to its deadline. Both report `completed_cycles=0`, because vanilla 1.29 calls `OnCompletePogress` only when the item runs out (`:56-59` vs `:66-68`).
  - 1.30: the drink timed out at 8.57 s with `completed_cycles=5`, because 1.30 calls `OnCompletePogress` after each repeat.
  - Not read: the bottle quantity and the water stat. No verb reads them (inbox fb98).
- **86a3 `player_look_at` / `action_cursor`** (after the gain fix).
  - 1.29 convergence:
    - ground at 3 m: 29.1° → 0.47° (16 ticks, 0.53 s);
    - far ahead: 29.7° → 0.80°;
    - 90° right: 90.0° → 0.34° (33 ticks);
    - crossing ±180°: 90.4° → 0.23°;
    - after the final PBO: 0.33°.
  - 1.30 convergence: 52.7° → 0.69° (24 ticks).
  - `action_cursor` on a spawned Apple: the sample is fresh and coherent, the target is the exact object (Apple, id 65), the primary slot is `ActionTakeItem`, the row widgets are visible and `item_flag_icon` is hidden. With an empty scene the cursor is hidden.
  - Cancelling a 180° look-at through MCP 0.318 s in: `player_look_at_release` (id 71) was delivered 12 ticks after the command, and the look-at (id 70) ended `ok=0` (not converged).

## Inconclusive or not run
- **9941, FenceKit + `ActionDeployObject` hold.** The placement toggle through `action_use(ActionTogglePlaceObject)` never enabled placement (`setup_failed` every time; the deploy reported `condition_failed`). A pause menu open after the launch was found and closed (`ui_click continuebtn`), with no change.
- **9941:** death, player change and shutdown during a hold were not run.
- **86a3:** the SimpleGroup protected-vehicle icon needs SimpleGroup loaded, and script-camera/freelook rejection was not run.
- After every launch on 1.29 the client showed the in-game pause menu until `ui_click continuebtn` (see 1d31).
