# Joint v11 promotion and in-game cycle (default instance + instance 130), v3 2026-10-07

v2 folded in gpt-6.1-sol's review of v1 (`r_promo/SOL_v1.md`: 5 P1, 3 P2, missing steps); v3 its closure review of v2
(`r_promo/SOL_v2.md`: 3 P2: sibling stage/destination, doctors after the daemons start, rollback only of what ran). Owner decisions it
implements: each instance from its own tools tree; both daemons upgraded before the concurrent acceptance;
default = stable DayZ 1.29, token `130` = DayZ 1.30 Experimental keeping `%LOCALAPPDATA%\DayZ_MCP_130`; batch E in v11.

## Names
- LIVE = `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev` (default, port 8765, `dayz-mcp`).
- T130 = `C:\temp\DayZ_MCP_130` (instance 130, port 8775, `dayz-mcp-130`). Today a 506de5d checkout with local
  patches (root and `E:\SteamLibrary\steamapps\common\DayZ Exp` hardcoded), its venv and keyfile.
- DEV130 = `C:\temp\DayZ_MCP_130_dev` (the 130 projects' dev tree, referenced by its launcher policy; kept).
- POL130 = `%LOCALAPPDATA%\DayZ_MCP_130\launcher-policy.json`; POLDEF = `%LOCALAPPDATA%\DayZ_MCP\launcher-policy.json`.
- CAND = branch `ingame/v11-<date>` = origin/main (Python batches and g5 merged) + batch E (Enforce, merged after
  this cycle). CAND_SHA its commit.
- PBO_DIR = `P:\Mods\@DayZ_MCP\Addons` (shared by both instances). STAGE_SRC = `P:\mcp_v11_stage\source` and
  STAGE_OUT = `P:\mcp_v11_stage\out\Addons` (siblings: pack-addon refuses a destination inside its stage root).
- BACKUPS = `C:\Users\guill\DayZ_MCP_backups\v11-<date>\`. Every sub-step writes its result there (`steps.log`).

## 0. Quiescence for the WHOLE promotion (stop if any fails)
- Owner go-ahead. `session_status` read once on each instance (it can lazy-spawn a daemon: read, then quiesce).
- The owner closes every MCP client of BOTH instances (`dayz-mcp` and `dayz-mcp-130`): other Claude/Codex
  sessions, supervisors, temporary clients; this orchestrating session stops calling `dayz-mcp` tools (a call
  respawns the daemon). They stay closed until step 6.
- Stop both `--daemon` listeners (8765, 8775) by PID; check the ports are free and no `dayz_mcp` writer process
  remains (process list by image and argv). No DayZ process.
- Only now back up (step 1).

## 1. Backups (sha256 and entry counts into steps.log)
- Archives of `%LOCALAPPDATA%\DayZ_MCP` and `%LOCALAPPDATA%\DayZ_MCP_130` (without dependency caches);
  `~/.claude.json` and `~/.codex/config.toml` (and the exact `dayz-mcp-130` registration entries, copied out);
  PBO_DIR's PBO; LIVE and T130 `tools\approved-launchers.json`, POLDEF, POL130, the launcher bundles' publish dirs.
- LIVE HEAD into a backup branch.

## 2. Retire the legacy 130 tree
- Rename T130 to `C:\temp\DayZ_MCP_130.legacy-<date>` (fails if a legacy process still runs from it: go back to
  step 0). Never deleted in this cycle. Renaming does not move the store: both versions use the same root.

## 3. New 130 tree
- `git -C LIVE worktree add --detach C:\temp\DayZ_MCP_130 <CAND_SHA>`; copy the legacy keyfile to
  `T130\tools\.dayz_mcp.key` (the installer reuses an existing key).
- Python >= 3.11 for the venv; pin the CLIs first (`install_mcp.py --pin-clis`, as the README install).
- Register WITH the selector from the start (never an unselected README install: it would update the default
  knowledge pack), keeping the current options:
  `python tools\install_mcp.py --register --instance 130 --game-path "E:\SteamLibrary\steamapps\common\DayZ Exp" --port 8775 --idle-timeout-seconds 3600 --claude-no-progressive-disclosure`
  The runs backup gate runs inside `--register` before the registrations change; the existing receipt
  (`source_absent=true`) is reused (no new `runs.pre-v2.json`). Record each sub-step (venv, gate, registration,
  knowledge pack): a later knowledge-pack failure can follow a committed registration.
- Launcher for T130 (a new worktree has no `approved-launchers.json`, it is git-ignored), from `T130\tools` with
  its venv python: `build_native_launcher.py --offline --verify-reproducible --policy <POL130 expanded>`, then
  `python -m dayz_mcp.launcher_registry_update bootstrap`, then `install-dayz-test-v1 --expected-sha256 <hash
  printed by the previous step>`. Keep copies of the policy and the reseal evidence (the builder deletes
  `.previous` after publishing).

## 4. Default tree
- LIVE: checkout CAND (backup branch kept). Reseal: `build_native_launcher.py --offline --verify-reproducible`
  (POLDEF), then verify `approved-launchers.json` holds the `dayz-test-v1` entry and run
  `replace-dayz-test-v1 --expected-sha256 <UPPERCASE sha256 of approved-launchers.json>`.
- Both trees resolve the same absolute shared root (`DAYZ_MCP_SHARED_ROOT` unset in both, so
  `%LOCALAPPDATA%\DayZ_MCP_shared`): box admission depends on it.

## 5. PBOs
- `pack-addon.ps1 -Ref <CAND_SHA> -StageRoot STAGE_SRC -Destination STAGE_OUT` (never the live folder);
  provenance `tools\dev\pbo_provenance.py <STAGE_OUT\DayZ_MCP.pbo> <LIVE> <CAND_SHA>`.
- `tools\dev\swap_pbo.ps1 -Build STAGE_OUT\DayZ_MCP.pbo -WantNew <new sha> -WantOld <old sha> -Destination
  PBO_DIR`; record the retired file's name (rollback swaps the hashes back).
- PROBE: pack `C:\Users\guill\dzmcp_gauntlet\probe_9941\ws\MCPHoldProbe` to `P:\Mods\_probe\@MCPHoldProbe\Addons`.

## 6. Daemon health (no game yet)
- Clients reopened by this session only: one stdio harness per instance; `session_status` on each (the daemons
  start lazily). Then the doctors, each with its tree's venv python: LIVE
  `python -m dayz_mcp.doctor --daemon-policy normal --json`; T130 `python -m dayz_mcp.doctor --instance 130
  --daemon-policy normal --json` (cwd does not select). The bridge version is read from a game poll, so v11 and
  the new capabilities are checked in step 7.

## 7. In-game checklist (sequential cycles, one box)
- Default (1.29): `bridge_status` "11" + capabilities (object_resolve, player_kill, bot verbs, component);
  6d21/fde3 (door above 511 and `action_use_component` on a building with many components); batch E (object_resolve
  0/1/2 objects, small radius, repeat -> same id, delete only that object, bad_args/object_not_found/
  ambiguous_object; telemetry_read lifetime vs a native read; inventory_give -> hands_take of the id;
  vehicle_telemetry.direction vs vehicle_trace while turning); 06a6 (camera_unverified after a lease expiry);
  9941 probe trials (PROBE in extra_mods, PROCEDURE.md); c8c7 rotation report on v11.
- 130 (1.30 Exp): `bridge_status` "11"; 120f bot_start/bot_stop on a world_spawn dummy.
- g5 step 7: both daemons up together with separate state, ports and registrations; a run on one instance blocks
  a launch on the other (box admission); a second writer for one root refused (exit 75 / state_root_writer_busy);
  stores checked by manifest, backups (`runs.json.bak-preprune*`: EXITED rows are pruned on activation) and
  evidence, not by literal equality.

## 8. Rollback (from any step)
- First: close games through the lifecycle guard, release leases, close every client, stop and verify both
  daemons (as step 0). Keep a copy of the post-promotion state before restoring anything.
- Undo ONLY the mutations `steps.log` records as done, each after checking the current state: swap the PBO back
  only if the new hash is installed (if the old one is still there, skip); replace the registry only if its
  content differs; checkout, worktree removal and legacy rename only if they happened.
- PBO: swap back (hashes reversed). LIVE: checkout the backup branch; reseal from it (replace).
- T130: remove the worktree; rename the legacy tree back; restore the `dayz-mcp-130` registration entries saved in
  step 1 exactly (the legacy installer only manages `dayz-mcp`); restore its launcher files and POL130.
- Stores: restore an archive only if the post-promotion state is broken, after keeping a copy of it.
