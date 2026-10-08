# MCPHoldProbe — in-game procedure (ticket 9941, H1)

The mod logs only after it deletes a marker file. With no marker it is vanilla and writes nothing. If the delete fails it runs that one start as vanilla, writes nothing, and the attempt is inconclusive. Run **control**, then **suppress**, then **cancel** in multiplayer with a dedicated server and client. Single-player/offline is excluded from this unchanged-PBO cycle because repeated `PerformActionStart` calls bypass the input-end bookkeeping reset. One trial is one `ActionDeployObject` on a `FenceKit`. Kits carry `ActionTogglePlaceObject` and `ActionDeployObject` (`4_world/entities/itembase/kitbase.c:150-151`); `ActionPlaceObject` belongs to `Container_Base` and other placeables, not to kits, so the v11 procedure (which named it) could not arm a trial (inbox 01d3).

## Where the orchestrator writes the marker

`$profile:` is the client process `-profiles=` directory for this run (the host folder that already contains the client RPT and `dayz_mcp.json`, typically `<mod>_dev\_client\profiles` when the run was launched by `dayz-test.ps1`). Write an empty file there:

| Trial | Host file (no extension) |
| --- | --- |
| control | `mcp_hold_probe_control` |
| suppress | `mcp_hold_probe_suppress` |
| cancel | `mcp_hold_probe_cancel` |

The mod deletes that file on the `PerformActionStart` that arms the trial and appends `mcp_hold_probe.jsonl` in the same directory. If more than one marker is present, priority is control, then suppress, then cancel. A marker is left untouched while a trial is already active. If this trial's start line never appears, delete the marker on the host before any later action so a failed delete cannot arm the next start.

Tools are the existing DayZ-MCP client tools only. Do not hold a key.

## `wait_for` `file_matches`

This wait needs the DayZ-MCP build that publishes `file_matches` (main `a72277c`, which includes PR #207). In that tree `WAIT_FOR_CONDITIONS` contains `"file_matches"` at `tools/dayz_mcp/server.py:374`, and the timeout helper calls `_wait_for_response(condition="file_matches", ...)` at `:2939`. The tool reads one file relative to the role's profile directory and renews the lease on each poll. `log_matches` does not read this JSONL and does not replace this wait.

Exact call, after the jsonl for this trial has been deleted on the host:

- `condition` = `"file_matches"`
- `role` = `"client"`. This cycle requires the multiplayer topology specified above.
- `profile_file` = `"mcp_hold_probe.jsonl"` (relative path; no drive, no `..`)
- `pattern` = a plain substring, not a regex. Use `"\"event\":\"start\""` for the start line and `"\"event\":\"restored\""` for the restore line.
- `lookback_lines` = `200` (the default). It can match text already in the file, which is why the jsonl is deleted before the marker is written. Gate on `satisfied`, not `ok`. The 200-line lookback can miss an earlier start after many tick lines; inspect the complete fresh JSONL before treating a wait timeout as missing lifecycle evidence.

Fallback if that build is not the one serving the session: open `mcp_hold_probe.jsonl` from the host path of this run's client profile directory (the same folder as the marker) and search the new lines for the same substrings. Do not use `log_matches` as a stand-in.

## One trial

1. With all markers absent and no action or inventory operation pending, give a fresh `FenceKit` using `inventory_give(classname="FenceKit", dest="hands")`. Record its receipt and object_id; confirm it is held by the local client player.
2. Stand erect, with unbroken legs, on open flat ground, looking ahead and slightly down (the hologram follows the camera ray, `4_world/classes/hologram.c:1157-1162`). With no existing hologram, call `action_use(action="ActionTogglePlaceObject", target="hands", classname="FenceKit")` once. Confirm the resulting hologram and valid placement indication with `capture_screenshot`. `setup_failed` alone proves neither success nor failure; an instant local toggle can finish before the bridge checks for a running action. Keep all markers absent during preparation.
3. Preserve the preceding attempt's evidence before clearing JSONL. Query the fixed placement area with `limit=128`; require reliable, untruncated results. Resolve each existing Fence using `object_resolve(type="Fence", pos=<row position>, radius=<isolating radius>)`. Record IDs and positions; ambiguity blocks the trial. Then delete `mcp_hold_probe.jsonl` on the host if it exists.
4. Write only the marker for this trial.
5. `action_use` once with `action` = `ActionDeployObject` and `target` = `"hands"`. That single start is the trial.
6. `wait_for` as above for `"event":"start"`.
7. `wait_for` as above for `"event":"restored"` (the mod also stops the trial at 30 s).
8. Repeat the same query and resolution after closure and bounded server observation. Attribute only a new Fence in the recorded placement area, with no competing actor or action. Preserve JSONL, dispatch receipt, screenshots, kit ID and before/after results. Then, with markers absent, delete only trial-created Fences and any surviving trial kit using existing `object_delete`; verify removal. Successful deploy consumes the kit. Confirm no hologram remains; toggle it off only if present. Restore the baseline before preparing another trial.

While a start is only pending, `running_action` is legitimately `null` (`GetRunningAction()` reads `m_CurrentActionData`, and a single-player start is still `m_PendingActionData`). Judge the start by `accepted` on the `start` line, not by `running_action`.

Never retry blindly. A remaining marker can mean pre-dispatch rejection or failed deletion; remove it before any other action and inspect the dispatch receipt. Read the full JSONL on the host if waits miss evidence. `restored` restores only the flag. After timeout or unresolved lifecycle, stop this cycle. Retry only after demonstrated closure, evidence preservation, cleanup and complete preparation.

## What each JSON line means

One object per line:

| Field | Meaning |
| --- | --- |
| `event` | `start` (marker deleted, after `super`, and after `EndActionInput` only when that start was accepted), `tick` (one `Update` while the trial is active, index 0..1999), `ended` (the stored `ActionData` left `m_PendingActionData` and `m_CurrentActionData`, including a pending that vanished without ever being current, or `OnActionEnd` on that data; also the immediate close of a start that was not accepted), `timeout` (30 s from the start `GetTime()`), `restored` (`SetIgnoreAutomaticInputEnd` put the previous `m_IgnoreAutoInputEnd` back; once per trial) |
| `accepted` | On the `start` line, `true` only when `super.PerformActionStart` left a new `ActionData` (neither the pending nor the current pointer captured before `super`) whose `m_Action` is the action that was started. `false` when setup was rejected or the manager was already busy with an earlier execution, including an earlier execution of the same type. The same value is repeated on later lines of that attempt. |
| `mode` | `control`, `suppress`, or `cancel` |
| `tick` | Tick index on `tick`, otherwise `null` |
| `time_ms` | `GetGame().GetTime()` (mission ms), or `null` |
| `running_action` | `ClassName()` of `GetRunningAction()`, or `null` |
| `is_trial_action` | `true` only when `GetRunningAction()` is the same shared `ActionBase` passed into the consumed `PerformActionStart`. It is a type match. It is not the identity of this execution. |
| `state` | `ActionData.m_State` while `m_CurrentActionData` exists. On `ended`, if that data was already cleared, the value captured in `OnActionEnd` before the clear; if the trial data disappeared without `OnActionEnd`, `null`. Names: 0 none, 2 processing, 4 finished, 5 cancel, 7 start, 12 initialize, 14 pending, 15 accepted, 16 rejected (`3_game/constants.c`) |
| `ignore_auto_input_end` | Live `m_IgnoreAutoInputEnd` |
| `already_placed` | `PlaceObjectActionData.m_AlreadyPlaced` when the logged data is that type, else `null` |

The lifecycle comparison is the `ActionData` pointer stored only when it is new after `super`: it must differ from both `m_PendingActionData` and `m_CurrentActionData` captured before `super`, and its `m_Action` must be the action passed into that `PerformActionStart`. That is `accepted` `true`. An earlier execution of the same type is not adopted, and `EndActionInput` is not called on it. A rejected setup or a manager already busy logs `start` with `accepted` `false`, then `ended`, then `restored`, and the flag is restored at once. `is_trial_action` is not this identity. Vanilla keeps one shared `ActionBase` per type and allocates a new `ActionData` per execution.

`ended` then `restored` is one close. `OnActionEnd` writes `ended` while the trial data is still current, then calls `super`, then restores. A close that happens inside `super.PerformActionStart` (before the `start` line) is logged after `start`, in the order `start`, `ended`, `restored`. A pending that vanishes without `OnActionEnd` logs `ended` with `state` `null`.

For `ActionDeployObject` the client sets `m_AlreadyPlaced` too, in `OnFinishProgressClient` (`4_world/classes/useractionscomponent/actions/continuous/deployactions/actiondeployobject.c:150-162`), so a client line with `already_placed` `true` says the client finished the progress. Placement evidence is still a new `Fence` id (`FenceKit` creates a `Fence` on placement, `4_world/entities/itembase/fencekit.c:27`), not this field and not "a Fence exists somewhere".

`ActionDeployObject`'s callback runs `CAContinuousTime(m_MainItem.GetDeployTime())` (`4_world/classes/useractionscomponent/actions/continuous/deployactions/actiondeploybase.c:12`), which is `UATimeSpent.DEFAULT_DEPLOY`, 5 s (`4_world/classes/useractionscomponent/actions/actionconstants.c:38`; `ItemBase.GetDeployTime`, `4_world/entities/itembase.c:4386-4389`). The action has to stay active about 5 s with no input held, which is what H1 tests: the control arm is expected to end before the progress completes.

`state` `null` is an unobserved terminal state. It is unknown. It is not evidence that the action failed to finish and not evidence of cancel.

## Reading H1

Hypothesis: `SetIgnoreAutomaticInputEnd(true)` immediately before the single `PerformActionStart` lets the continuous action reach its normal completion with no input held.

Classify H1 only from uncontaminated, attributable trials. Require all three starts accepted, matching mode/action, initial ignore flag false for control and true for suppress/cancel, complete lifecycle evidence, and restoration to false.

**Supported:** suppress completes normal progress (`already_placed=true`, finished lifecycle) and produces an attributable baseline-new Fence; valid control and cancel terminate without completing progress or producing a Fence.

**Refuted:** suppress reaches execution, then demonstrably cancels before completing progress with `already_placed=false`, despite suppression remaining enabled and no independent interruption or placement failure. Confirm no attributable Fence after bounded server observation.

**Inconclusive:** every other result, including server rejection, vanished pending data without execution evidence, timeout, missing observations, client completion without server placement, or a Fence in control/cancel.
