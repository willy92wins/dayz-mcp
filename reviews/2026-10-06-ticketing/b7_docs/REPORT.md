# REPORT — Batch 7: Published contract clarification (4d7e, c879+769c, 1d31, 0eb8)

Status: COMPLETE — acceptance gate PASS

## CHANGES

All four items are documentation-only wording changes to published tool
descriptions. No parameter, result, filename or error-code change was made,
and no Enforce/PBO file was touched.

- `tools/dayz_mcp/server.py` — `capture_screenshot` description (ITEM 4d7e):
  the `save_fullres=True` sentence now names the saved file the
  **native-resolution effective_surface** — "the frame after client-area
  selection and cropping, before inline downscaling" — and states that
  "the effective_surface dimensions — not the whole window's — apply to the
  file" (read it in effective_surface coordinates). This matches the actual
  behavior at `tools/mcp_capture.py:1547-1558` (effective crop computation)
  and `:1600-1603` (`write_fullres(effective_native, ...)`), which the spec
  cites.
- `tools/dayz_mcp/server.py` — `player_teleport` description (ITEM c879+769c):
  appended a qualified caveat: the reply's `pos_real` is the server-side
  **position assignment (SetPosition)**; the assignment "does not attest that
  the client's physics have settled at that position or that a moving floor
  keeps carrying the player", with the reported moving-platform hover
  attributed as "(reported, not reproduced)". Documents the workaround
  `player_move(speed="walk", phase="hold", hold_s=1)` as **actual movement**
  ("every phase actually displaces"), restricted to "the local on-foot player
  only (a uid target is out of its reach)", and states "There is no settle
  operation" — no automatic settle was added (per spec decision; there is no
  zero-speed movement mode, `tools/dayz_mcp/player_move.py:40`).
- `tools/dayz_mcp/server.py` — `key_press` description (ITEM 1d31): keeps the
  mission-only contract and adds the game-level cross-link with the spec's
  exact route and parameter names:
  `input_trigger(kind="key", dik=1, entry="game", phase="click")` for
  `DayZGame.OnKeyPress/OnKeyRelease` (bridge: `MCPClientBridge.c:283-287`;
  `key_press` itself calls only mission `OnKeyPress` at
  `MCPClientBridge.c:1381`). Adds that "a delivered callback is not
  confirmation that the intended UI effect happened ... verify the result
  with ui_tree". No camera-distance guard, no crash-classification claim, and
  no claim that any menu closes (that remains unverified at runtime).
- `tools/dayz_mcp/server.py` — `WORLD_SPAWN_FLAGS_LINE` (ITEM 0eb8), which is
  interpolated into the published `world_spawn` description: appended that
  "Spawning an infected without ECE_INITAI does not establish a durable
  living visual fixture", attributes the delayed health-zero observation as
  "reported" with "the cause is unverified", recommends checking the entity's
  health (`telemetry_read object_at`, field `health01`) "immediately before
  judging it visually", and mentions the living-infected recipe `flags=3108`
  `(ECE_PLACE_ON_SURFACE|ECE_INITAI)` which "initializes the AI and does not
  guarantee survival". Accepted masks, the bad_flags contract and the
  nopersistency example are unchanged (pinned by
  `tools/tests/test_precondition_docs.py::_assert_world_spawn_copy`, which
  still passes; note it forbids the literal "not verified in game", so the
  wording uses "the cause is unverified").
- `CHANGELOG.md` — one bullet under the existing `## [Unreleased]` /
  `### Changed` heading summarizing the four clarification edits.

## TESTS ADDED

New module `tools/tests/test_tool_description_caveats.py` (12 tests). Every
wording test reads the PUBLISHED descriptions through
`effective_schema.resolve_effective_schemas` (build_app + list_tools, the
path a client sees), not by grepping server.py.

Wording (fail on unmodified code — verified by reverting the four edits and
re-running: exactly these 7 fail on HEAD, the other 5 pass on both):

- `CaptureScreenshotFullresWordingTest.test_fullres_is_described_as_the_effective_surface`
  — 4d7e: fullres_path described as the native-resolution effective_surface,
  after client-area selection and cropping, before inline downscaling, with
  effective (not whole-window) dimensions applying to the file.
- `PlayerTeleportSettlementWordingTest.test_assignment_is_distinguished_from_settled_physics`
  — c879+769c: assignment vs settled physics/moving-floor caveat, reported
  attribution, no settle operation.
- `PlayerTeleportSettlementWordingTest.test_movement_workaround_is_described_as_real_displacement`
  — c879+769c: the player_move walk/hold workaround as real displacement,
  local-player-only, no zero-speed hold.
- `KeyPressRouteWordingTest.test_game_level_route_is_input_trigger_with_game_entry`
  — 1d31: mission-handler-only + the input_trigger game route with correct
  parameter names (dik, not key).
- `KeyPressRouteWordingTest.test_delivery_is_not_confirmation_of_the_ui_effect`
  — 1d31: delivery ≠ UI effect, verify with ui_tree; asserts no menu-closure
  claim is published.
- `WorldSpawnInfectedWordingTest.test_ai_less_infected_is_qualified_as_reported_and_unverified`
  — 0eb8: no durable living fixture without ECE_INITAI; reported + unverified
  cause.
- `WorldSpawnInfectedWordingTest.test_health_check_and_living_recipe_are_named_without_guarantees`
  — 0eb8: health01 check before visual judgment; flags=3108 recipe without a
  survival guarantee.

Contract preservation and discovery path (pass on HEAD too, by design):

- `PublishedContractPreservedTest.test_player_teleport_keeps_its_parameters_and_adds_no_settle`
- `PublishedContractPreservedTest.test_key_press_parameter_is_dik_not_key`
- `PublishedContractPreservedTest.test_world_spawn_flags_masks_are_unchanged`
- `CaveatDiscoveryPathTest.test_caveated_tools_are_absent_from_the_compact_catalog`
  — pins that none of the four tools is in `tool_catalog._INITIAL_CATALOG_NAMES`,
  so `_compact_description` (which can drop a late sentence) never truncates
  them: the full description is what a client sees on every discovery path.

Behavioral, kept in its own class apart from the wording checks (spec:
"Keep behavioral crop/save checks separate from wording checks"):

- `CaptureFullresCropBehaviorTest.test_fullres_file_is_the_crop_and_window_coordinates_misread_it`
  — the 4d7e failure scenario measured on `mcp_capture.capture_dual` with
  `crop="0.2,0.0,0.8,1.0"`, `save_fullres=True` over a synthetic 100x80
  window (red chrome, green 60x60 client viewport, blue block visible only
  inside the crop): asserts the saved file is 36x60 at window rect (22,10),
  its bytes hash to `meta.fullres_file_sha256`, its origin pixel is the crop
  origin (green viewport) and not the window origin (red chrome), and the
  blue block lands at crop coordinates (12,15) — i.e. the file represents the
  crop, so whole-window coordinates select the wrong region. (The broader
  crop/save behavioral contract is already covered by
  `tests.test_mcp_capture.MCPCaptureTest.test_fullres_saves_the_native_effective_surface_not_the_chosen_frame`,
  which passes unchanged.)

## GATE OUTPUT

From the workspace root:
`"/mnt/c/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev/tools/.venv-mcp/Scripts/python.exe" gauntlet_gate.py tests.test_tool_description_caveats`

Last lines of the run, pasted literally:

```
--- tests.test_tool_description_caveats: rc=0
Ran 12 tests in 0.133s

OK
--- fast tier: ran=5599 baseline_ran=5587 failures=2 new=0
GAUNTLET_GATE: PASS
EXIT=0
```

The two fast-tier failures are exactly the two in `baseline_failures.txt`
(the non-git-export overlay test and the git-ignoring staging test); no new
failures. 5599 tests ran, 12 more than the 5587 baseline — the new module.

## DEVIATIONS

- Spec quotes the two call examples with double quotes
  (`input_trigger(kind="key", ...)` / `player_move(speed="walk", ...)`);
  the published descriptions use exactly those strings (double quotes inside
  the description text), matching the spec literally and the escaped-quote
  style the server instructions already use for call examples.
- The 0eb8 recipe mention writes the living-infected mask as
  `flags=3108 (ECE_PLACE_ON_SURFACE|ECE_INITAI)`; the server instructions and
  README-mcp.md append `|ECE_CREATEPHYSICS` to the same number 3108, which is
  redundant because `ECE_PLACE_ON_SURFACE` (1060) already contains
  `ECE_CREATEPHYSICS` (1024). I kept the minimal correct decomposition rather
  than copying the redundant parenthetical; the integer 3108 is identical.
- No existing test pins had to change: the edits keep every pinned substring
  (`test_precondition_docs._assert_world_spawn_copy`,
  `test_docs_truth.WorldSpawnNopersistFlagDocsTest`) and deliberately avoid
  its forbidden literal "not verified in game".

## NOT VERIFIED

- No DayZ processes were launched: the 1d31 runtime closure (does
  `input_trigger(kind="key", dik=1, entry="game", phase="click")` actually
  close the pause menu?) and the c879 hover observation remain unverified in
  game, exactly as the spec leaves them (deferred to an in-game experiment).
- The 0eb8 infected death cause (engine cleanup vs absence of AI) remains
  unverified; the description only attributes the observation as reported.
- The full-suite fast tier was run only through the acceptance gate (see
  GATE OUTPUT); no other environments were exercised.
- The editable install of the project venv maps `dayz_mcp`/`mcp_capture` to
  the OneDrive dev tree when the workspace's `tools/` dir is not first on
  sys.path; all test/gate invocations here run from `tools` (cwd first), so
  they exercise this workspace — verified by printing `dayz_mcp.server.__file__`.
