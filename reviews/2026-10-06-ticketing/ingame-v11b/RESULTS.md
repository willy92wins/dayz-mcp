# v11b deploy and in-game cycle, 2026-10-08

Deployed: main `46833d7` = g5fix (#220), r9b (#221), r9c (#222), AddonBuilder stage (#195). No Enforce change since
`686b15f`, so the shared PBO stays `CC616EC7…` (bridge "11"). Procedure: quiescence (14 client chains of other
sessions terminated with the owner's permission, the default daemon stopped; the 130 daemon was already down),
backups, LIVE fast-forward + CRLF renormalization (6ed1) + reseal, T130 checkout + reseal (POL130), daemons up,
doctors, in-game. Raw evidence: `C:\Users\guill\dzmcp_gauntlet\ingame\evidence_v11b\` (not in the repo: some
files carry the local Steam id); step log and backups: `C:\Users\guill\DayZ_MCP_backups\v11b-20261008\`.

## Reseal
| Tree | Launcher PE sha256 | app.pyz sha256 | Registry | Notes |
|---|---|---|---|---|
| LIVE | `E1788375…` (was `B633162A…`) | `8B932082…` | replace rc=0, check_native_launcher_registry rc=0 | `win32_fileinfo.py` inside app.pyz has 0 CR |
| T130 | `44372199…` | `8B932082…` (same code) | replace rc=0, check rc=0 | policy POL130 |

The renormalization first used `git checkout-index --force`, which skips a file whose stat data still matches the
index (mtime unchanged); removing the copy before `checkout-index` rewrote the three files as LF.
Doctors: 130 clean; default only the pre-existing `RUN_PREPRUNE_BACKUP_SLOTS_EXHAUSTED`.

## Instance 130 (DayZ 1.30 Exp), g5fix / 39b8
| Check | Result |
|---|---|
| `profiles-130` folders renamed aside, first launch recreates them | PASS (both roles, run `4d100b67`) |
| `artifacts_paths` | PASS: `_server\profiles-130`, `_client\profiles-130` |
| `logs_since`, `wait_for(log_matches)` read the named folders | PASS (files listed under `profiles-130`; positive control `Player connect enabled` satisfied) |
| `wait_for(file_matches, role=client)` | PASS (a marker file in `_client\profiles-130`) |
| `dayz_test_close` with a player connected | graceful=true, termination line in both roles, exit metrics valid; **`logout_players` empty** |

The empty `logout_players` is a separate defect: the 1.30 Experimental server RPT writes the join line localized
(`El jugador Dev (id=… pos=<…>) se ha conectado.`), while the parser matches only
`Player "…" (…) is connected` (`tools/dayz_mcp/dayz_test_tool.py:3312-3316`). On 1.29 (default, same day) the close
waited 17.1 s and reported `logout_players=[Dev, logout_finished=true]`.

## Default instance (DayZ 1.29), r9b / r9c storage
| Check | Result |
|---|---|
| Modset change rotates `storage_1` | PASS: `storage_rotated=true`, backup `storage_1.modset-20261008-154124-795a0433` (old seal `795a0433…`) and again after the refusal check (`…-160010-fab720e2`) |
| A planted case-variant journal refuses before any mutation | PASS for the refusal (`error_code=storage_recovery_required`, no run, no process; daemon audit `lifecycle_storage_recovery_required` reason `journal_name_invalid`) |
| The caller gets `storage_recovery_reason` and `remediation` (r9c E2) | **FAIL**: both null on this path. The refusal happens in the daemon lifecycle (`process_lifecycle.py`), which sets the pair on its settled result (`:4289-4297`), but it does not reach the tool result. |
| After removing the plant, the next launch | PASS (run `82cb609d`, rotated) |

## AddonBuilder stage (#195, bf5c / 8cf9)
- SimpleGroup, `dayz_test_run(build=true)`: built from `…\Temp\dayz-mcp-native-29paid5w\dayz-mcp-build-kkver6gp\SimpleGroup` (AddonBuilder log), 20 s with the launch.
  - The ODOL gate of inbox 713a passes. G2: tangent space n_st == vertices in all 5 LOD0. G3: every `dz\` material complete (VS=102, 13 stages).
  - `T1_FlagKit.p3d` is byte-identical to the deployed PBO. G6/G7 fail identically on the deployed PBO (control): those gates predate the current T3 model.
  - The private folder was gone after the run. The deployed PBO was restored, sha256 `2C3EAC14…` = original.
- LFHeli_OH1: built through the stage (ResultCode 0, 80 s). The closure (`\LFHeli\` references) is not semantically verified. The deployed PBO was restored, sha256 `194FD1A5…` = original.
- Pre-existing, not from #195: a `dayz_test_run(build=true)` PBO packs only config, models, textures and rvmats.
  - SimpleGroup: 22 entries against 67 in the project's own build. No `scripts\`, `gui\layouts`, `inputs.xml` or `stringtable.csv`, so such a run tests the mod without its scripts (c561 family).
  - LFHeli_OH1: prefix `LFHeli_OH1` against `LFHeli` in the deployed PBO.
- Observations: 16 + 2 `Error: 1816 Cannot run binMake.exe` lines in the staged builds; 5 empty `dayz-mcp-native-*` folders from earlier days left in TEMP.

## 9941 hold probe (corrected procedure, gpt-6.1-sol READY)
FenceKit + `ActionDeployObject` (5 s deploy), multiplayer, one fresh kit per trial, hologram confirmed by capture.

| Arm | Start | Lifecycle | `already_placed` | New Fence |
|---|---|---|---|---|
| control (vanilla) | accepted, ignore flag false | cancel (state 5) after ~1.9 s | false | none |
| suppress (`SetIgnoreAutomaticInputEnd(true)` before the start) | accepted, ignore flag true | processing ~6.7 s, then finished (state 4) | true (21 ticks) | yes, 2.5 m away (resolved and deleted) |
| cancel (suppress + `EndActionInput`) | accepted, ignore flag true | cancel after ~1.7 s | false | none |

All three restored the flag to false. **H1 supported**: setting `SetIgnoreAutomaticInputEnd(true)` right before one `PerformActionStart` lets a continuous action complete with no input held, which is the mechanism inbox 9941 / d1a8 / 9ab8 need.

## 86a3 aim units (experiment from SPEC_86A3)
`weapon_aim` with a weapon in hands (it refuses `no_weapon_in_hands` otherwise), player camera read with `camera_get`:

| Pulse (`ONE_FRAME`) | View change |
|---|---|
| dx +0.01 / −0.01 / +0.02 / +0.2 | yaw +0.5730° / −0.5730° / +1.1459° / +11.4592° (right is positive) |
| dy +0.01 / −0.01 / +0.02 | pitch +0.5730° / −0.5730° / +1.1459° (up is positive) |
| 0, 0 | no change |

`OverrideAimChangeX/Y` with `ONE_FRAME` is radians per pulse, applied exactly once and proportionally. The view kept its pitch after the weapon left the hands.

## Other
- 1d31: inconclusive. No pause menu after this launch (`ui_tree` no_menu); ESC by `key_press` or `input_trigger` opened none either.
- f298 / 75e7 passive focus probe (250 ms sampling, 15:36-15:56):
  - DayZDiag took the foreground at each launch: server about 5 s, client 1-4 s.
  - `WindowsTerminal.exe` held it for one sample at 15:39:01 and at 15:54:01, not attributed.
