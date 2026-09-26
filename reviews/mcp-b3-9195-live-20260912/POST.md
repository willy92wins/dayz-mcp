## POST
### Hecho
- Prefer-agy lane probed: gemini-3.8-flash-low listed; agy --model -p MCP permission denied headless → drove via Python/MCP client on Willy.
- Protocol: dayz_test_run(@DayZ_MCP,build=false) only if bridge not ready → bridge_ready → session_acquire_wait → wait_for players → world_spawn flags matrix → session_release.
- Adopted live DayZDiag (pids ~41220/64332); no force-kill; no PBO rebuild; no 3fc1; no Sol; no merge.
- PBO live SHA expected F82CFA8C… (Workshop+P:\Mods); tip a9fe673; ficha fb-20260910-010648-9195.
- Allowed: flags=0 and/or ECE_PLACE_ON_SURFACE(=1060) — must not be bad_flags (spawn OK or engine err unrelated to flags).
- Excluded: ECE_KEEPHEIGHT=524288, ECE_NOLIFETIME=4194304, combo 4718592 — expect bad_flags per main mask (mcp-b2-9195 / PR#15).

### LL
- agy headless cannot call MCP without permissions.allow mcp(...) or --dangerously-skip-permissions.
- world_spawn Python preflight mirrors IsAllowedSpawnFlags; KEEPHEIGHT/NOLIFETIME stay excluded on main without Enforce change.
- Holding lease blocks dayz_test_run (session_transition_conflict) — acquire after bridge_ready.

### Skills canonicas
- dayz-mcp local tools (session_*, dayz_test_run, wait_for, query_*, world_spawn)

### LIVE-STATE
- RC=PASS. DayZ left per session_release (no force-kill). Workshop/Mods untouched this hop. run_id=da74540f-1bae-4a7f-8169-eeca964d8c3c.
