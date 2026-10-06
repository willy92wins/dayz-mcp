# Ticketing session 2026-10-06: triage of open DayZ-MCP inbox findings

Scope: the 45-odd unresolved inbox entries that concern the MCP (of 118 unresolved in total),
grouped into 33 items. First pass: GLM-5.3-Flash-EXL3 on the GX10, three lanes, grep excerpts of
origin/main `6671fd2`. Second pass: gpt-6.1-sol with the whole tree, on the 17 items that had a
fix or a closure proposal (`SOL_REVIEW.md`). Owner decisions: `DECISIONS.md`.

| lane | item | first-pass verdict | title |
|---|---|---|---|
| A_lifecycle | [2837](triage/2837.md) | OWNER_DECISION | dayz_test_run: base_mods no sustituye al mod del proyecto; un PBO candidato se carga junto al vivo |
| A_lifecycle | [31d2](triage/31d2.md) | OWNER_DECISION | dayz_test_run preflight=true is refused with takeover_required while another session's run holds the box |
| A_lifecycle | [52c3](triage/52c3.md) | NOT_IN_MCP | DayZDiag rejects an orderly-saved storage_1 when the next boot comes after a gap |
| A_lifecycle | [584e+f0e3](triage/584e_f0e3.md) | OWNER_DECISION | Las lecturas del dueño (logs, capturas) no renuevan el lease: caducó durante una tanda de lecturas / Para fb-2 |
| A_lifecycle | [7055](triage/7055.md) | NEEDS_REPRO | dayz_test_run mode=client cannot reattach a client to the caller's own run after the client died |
| A_lifecycle | [734c](triage/734c.md) | OPEN_CONFIRMED | Supervised client hangs forever when the worker interpreter dies; server_reload refuses (drain_timeout) |
| A_lifecycle | [9336](triage/9336.md) | NEEDS_REPRO | Capture tandem: client reattach needs takeover, client starts in pause menu, close times out (Baltic 04-10) |
| A_lifecycle | [9376+250f](triage/9376_250f.md) | OWNER_DECISION | Ownerless foreign run blocks the box all night, and session_acquire_wait would auto-adopt it once abandoned /  |
| A_lifecycle | [97de+0cb3](triage/97de_0cb3.md) | OPEN_CONFIRMED | Second DayZ-MCP instance (1.30 Exp copy) cannot start its daemon while any dayz_mcp process runs / Quiescence  |
| A_lifecycle | [9efc+aecb](triage/9efc_aecb.md) | NEEDS_REPRO | Client died silently after camera_set; reattach refused (takeover_required / session_transition_conflict) / ca |
| A_lifecycle | [af90](triage/af90.md) | OWNER_DECISION | wait_for players_at_least succeeded without a lease while another session owned the running box |
| A_lifecycle | [c561](triage/c561.md) | OPEN_CONFIRMED | Boot gate PASS with a mod PBO that has no scripts: six SimpleGroup merges validated vacuously |
| B_verbs | [0eb8](triage/0eb8.md) | NOT_IN_MCP | Infected spawned without ECE_INITAI (flags 8389668) read health01 0 about 12 min later, vanilla too, no log li |
| B_verbs | [1d31](triage/1d31.md) | LIKELY_FIXED | key_press ESC does not close the in-game pause menu after launch; ui_click continuebtn does |
| B_verbs | [449e](triage/449e.md) | NEEDS_REPRO | scene_raycast: radius 0 is treated as 0.05 and pos is the sphere centre, not the surface point |
| B_verbs | [4d7e](triage/4d7e.md) | OPEN_CONFIRMED | capture_screenshot save_fullres writes the CROPPED region at native size, not the whole window: say so in the  |
| B_verbs | [6d21](triage/6d21.md) | OPEN_CONFIRMED | action_use door_index: the door component scan stops at 512, so doors on buildings with more components are no |
| B_verbs | [983a](triage/983a.md) | OWNER_DECISION | camera_set/camera_get: expose the applied FOV (default measured 68.5 deg vertical) |
| B_verbs | [9941](triage/9941.md) | OWNER_DECISION | action_use starts a continuous hold action (ActionPlaceObject) but never completes it; no way to place a kit |
| B_verbs | [aa01](triage/aa01.md) | OPEN_CONFIRMED | No verb removes a worn item from the player; inventory_attach returns object_id 0, so object_delete can't eith |
| B_verbs | [c879+769c](triage/c879_769c.md) | OPEN_CONFIRMED | player_teleport leaves the client player unsimulated until a move: it hovers and never links to a moving floor |
| B_verbs | [d17c](triage/d17c.md) | OWNER_DECISION | wait_for sobre logs $profile de la misión + error claro de camera_set cuando el cliente ya no existe |
| B_verbs | [d490](triage/d490.md) | NEEDS_REPRO | capture_screenshot all black with the display asleep (session unlocked); pre-launch desktop probe passed |
| B_verbs | [f4de](triage/f4de.md) | OWNER_DECISION | No headless way to kill a living player: player_respawn is a no-op even with godmode off; add a kill option |
| B_verbs | [fde3](triage/fde3.md) | OPEN_CONFIRMED | action_use: synthetic target (component -1) cannot trigger component-gated building actions |
| C_build_infra | [120f](triage/120f.md) | OWNER_DECISION | 1.30: server-side dummy bots work — proposal bot_dummy_spawn / bot_start / bot_stop verbs |
| C_build_infra | [6084](triage/6084.md) | LIKELY_FIXED | Steam not running: la app ve una copia congelada de Steam\ActiveProcess; WMI lee el real (medido) |
| C_build_infra | [7f27](triage/7f27.md) | OPEN_CONFIRMED | a412, segunda parte: los escalares genéricos de MCPResult siguen viajando con su valor por defecto en todos lo |
| C_build_infra | [a97e](triage/a97e.md) | NEEDS_REPRO | binarize -silent de AB muere exit 2 instantaneo con source en espejo Dokan P: (ReparsePoint) |
| C_build_infra | [bb6d](triage/bb6d.md) | OPEN_CONFIRMED | Test sensible a la carga: test_fb_c9ca... da native_launcher_request_writer_stuck en un tier rápido lento |
| C_build_infra | [bf5c+8cf9](triage/bf5c_8cf9.md) | OPEN_CONFIRMED | binarize con -addon=%TEMP% convierte fixtures de test de Temp en parches globales: rompe todo build desde el 2 |
| C_build_infra | [c8c7](triage/c8c7.md) | OPEN_CONFIRMED | H11: el aviso de reset por rotación de storage_1 solo queda en la auditoría; dayz_test_run no lo devuelve |
| C_build_infra | [dbe0](triage/dbe0.md) | OPEN_CONFIRMED | dayz_test_worker: el predicado de assets (Path.rglob) recorre bucles de junctions dentro del source |
