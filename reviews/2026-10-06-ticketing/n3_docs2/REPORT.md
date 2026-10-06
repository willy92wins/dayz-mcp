# REPORT — batch n3_docs2 (documentation only)

## CHANGES

### tools/dayz_mcp/server.py — `scene_raycast` tool description (ITEM 449e)
- What: extended the published description (was 2 short sentences) with four
  statements required by the specification:
  1. With `method='rvproxy'`, a requested `radius=0` currently runs with an
     effective radius of 0.05 m (the bridge initializes the radius at 0.05 and
     replaces it only when the requested value is positive); positive values
     pass through.
  2. `pos` is the engine-returned position copied without contact-point
     reconstruction; no universal sphere-centre or contact-point semantics are
     established (vanilla names it a collision position).
  3. With `method='bullet'`, the implementation performs a raycast and does not
     use radius.
  4. A reported floor experiment suggested a sweep-centre offset for that
     geometry; that interpretation is not promised for all surfaces, and engine
     measurements are required before any `contact_pos` addition.
- Why: the spec's [DESIGN] documentation fix for 449e. Verified against the
  sources before writing: `addon/scripts/5_Mission/MCPBridge.c`
  `ValidateRaycastArgs` (validation.radius initialized to 0.05, replaced only
  when `args.radius > 0.0`), `PopulateRaycastRVProxy` (radius forwarded into
  `RaycastRVParams`, `rayHit.pos` copied unchanged) and `PopulateRaycastBullet`
  (`DayZPhysics.RayCastBullet` call takes no radius). Behavior and result shape
  untouched; no PBO or reseal (documentation strings only, no Enforce edit).
- The first-pass universal contact formula was NOT implemented, per spec.

### tools/dayz_mcp/server.py — `capture_screenshot` tool description (ITEM d490)
- What: added one sentence right after the existing preflight sentence:
  "The preflight is a point-in-time check of current desktop accessibility and
  brightness: it neither keeps the display awake nor guarantees later client
  captures (a display that enters power-save after the probe can still produce
  frame_client_all_black)."
- Why: the spec's [DESIGN] clarification for d490 — the probe
  (`tools/mcp_capture.py` `probe_desktop_brightness` via
  `PIL.ImageGrab.grab()`, admitted by mean-brightness/non-black-ratio
  thresholds, invoked from `execute_dayz_test_run` before launch) checks
  lock/brightness at that moment only. No power-state API was added and no
  automatic power-policy change was made (spec: none is ready from present
  evidence). No synthetic input, no `SetThreadExecutionState` claim.

### CHANGELOG.md
- Added a short `### Changed` section under the existing `## [Unreleased]`
  heading with one line per item (449e radius semantics wording; d490 preflight
  clarification).

## TESTS ADDED

New module `tools/tests/test_tool_description_caveats_2.py` (all wording checks
read the PUBLISHED metadata through `effective_schema.resolve_effective_schemas`;
behavioral checks are kept in their own test class):

Wording (fail on the unmodified code because the sentences do not exist there):
- `test_scene_raycast_documents_radius_zero_substitution` — pins the radius=0 →
  effective 0.05 m statement and "positive values pass through". FAILS on
  unmodified code: the description has no radius statement at all.
- `test_scene_raycast_documents_pos_as_engine_returned` — pins engine-returned
  pos / no contact-point reconstruction / no universal sphere-centre semantics.
  FAILS on unmodified code for the same reason.
- `test_scene_raycast_documents_bullet_ignores_radius` — pins the bullet branch
  sentence. FAILS on unmodified code.
- `test_scene_raycast_limits_the_floor_experiment_interpretation` — pins the
  sweep-centre-offset caveat ("not promised for all surfaces"). FAILS on
  unmodified code.
- `test_capture_screenshot_limits_the_desktop_preflight_promises` — pins the
  d490 point-in-time / not-awake / no-guarantee sentence. FAILS on unmodified
  code (the preflight sentence exists there but makes no such statement).

Discovery-path regression (pass both before and after by design; they guard the
truncation discipline now that the description exceeds the 120-char compact
limit):
- `test_published_radius_default_is_0_05` — published `radius` default.
- `test_compact_catalog_cut_of_scene_raycast_is_sentence_aligned` — scene_raycast
  rides the pre-lease compact catalog (`_INITIAL_CATALOG_NAMES`); the cut made by
  `tool_catalog._compact_description` must end at a sentence boundary with the
  ` …` marker, be a prefix of the full description, and never truncate a caveat
  mid-sentence or mid-value.
- `test_compact_cut_stays_sentence_aligned_for_capture_screenshot` — same
  sentence-alignment property for the helper output.

Behavioral regression (kept separate from wording; pass on unmodified code —
they pin that the documentation change moved no behavior):
- `test_radius_zero_is_accepted_and_forwarded_unchanged` — with
  `runtime.call_bridge` mocked, `radius=0.0` is accepted, forwarded to the
  bridge unchanged (args["radius"] == 0.0, method default rvproxy, peer
  "server"), and the bridge dict returns through the tool with its shape intact.
- `test_negative_radius_is_still_rejected_before_the_bridge` — `radius=-0.01`
  still raises ToolError ("non-negative") before the bridge is called.

No existing test was weakened, deleted, or re-pinned: no pre-existing test
pinned the strings that changed (grep for the old sentences hits only the
source itself and `test_prerun_desktop_gate.py`, which pins substrings that are
still present verbatim).

## GATE OUTPUT

(pending — will hold the last 8 lines of the gauntlet_gate.py run)

## DEVIATIONS

(pending)

## NOT VERIFIED

(pending)
