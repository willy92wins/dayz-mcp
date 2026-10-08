# REPORT — batch n1_983a: `camera_set` echoes `fov_applied`

## CHANGES

- `tools/dayz_mcp/server.py` — `camera_set` tool description (`@app.tool(description=...)` above `async def camera_set`, lines ~5644-5662): documents the new field and its scope. Added: a successful apply echoes the value the setter path received as top-level `fov_applied` in radians (`fov=0` reports `null`); the echo is "not a native readback and does not guarantee the observed optical projection"; the engine default is "approximately 68.5 degrees vertical at 1920×1080 (owner-provided measurement, run 8834df0e, 2026-10-04)"; `fov=0` keeps the current FOV and "does not guarantee resetting to that measured default, the singleton free camera included". The pre-existing sentences (no `Camera.IsInterpolationComplete` / `GetCurrentFOV` settle, `camera_get.view` observer) are retained.
- `tools/dayz_mcp/server.py` — `camera_set` body (after the `runtime.call_bridge("camera_set", ...)` await, lines ~5709-5723): the awaited bridge result is now kept and, before returning, gets the top-level `fov_applied` key added only when the result is a success with a legible camera observation: top-level `ok` truthy (already guaranteed by `Runtime.call_bridge`/`wait_for_result`, checked defensively) and nested `result["camera"]` a dict with truthy `ok`. Positive validated FOV echoes that exact value in radians (`fov_value`, the value stored at `args["fov"]` and submitted to the setter path); zero stores `None` (JSON `null`). A refused apply (`ok=0` → `_bridge_error`), a timeout (`wait_for_result` ToolError), or an illegible camera observation (`camera.ok=0`, e.g. `camera_unavailable_*` / `camera_illegible_player_transform`) therefore never report an applied FOV. The comment block cites the no-getter rationale (MCPClientBridge.c:4773-4776, GetCurrentFOV froze the render) and the setter paths (MCPClientBridge.c:5417/:5461). Request schema, nested legacy `camera` object, bridge arguments (and thus the argument contract hash) and `camera_get` are untouched; the field is added after pruning (`result_prune.prune_unfilled_fields` runs inside `call_bridge` and passes unknown keys through, result_prune.py:158), so nothing else reads or strips it.
- `CHANGELOG.md` — one line under `## [Unreleased]` → `### Changed` describing the echo, the null-for-zero rule, the no-readback scope and the owner-measured default context.
- `product-spec.md` — D1 row (line 76) extended: the echo contract (`fov_applied` = radians submitted to the setter path, `null` for `fov=0`, no engine getter), the owner-measured ~68.5° vertical at 1920×1080 default as context only (run `8834df0e`, 2026-10-04), and the status cell notes the echo is accredited offline with the in-game re-verification pending. Only the D1 row changed.

## TESTS ADDED

New module `tools/tests/test_camera_set_fov_applied.py` (the acceptance gate requires tests in `tests.test_camera_set_fov_applied`; see DEVIATIONS for the spec's `test_mcp_tools.py` mention):

- `test_983a_camera_set_echoes_positive_fov` — parameterized over `orient`, `lookat`, the `look_at` alias, `matrix`, `free`+`look_at` and `free`+`cam_orientation`; asserts the result equals the validated radians exactly, that `fov_applied` sits at the top level (never inside the nested `camera`), that the nested camera object carries no FOV key, and that the bridge still receives the same `fov` argument.
- `test_983a_zero_fov_is_explicit_null` — explicit `fov: 0.0` and the defaulted zero; asserts key presence, `is None`, JSON round-trip `null` (wire shape), and that the wire still carries `fov=0`.
- `test_983a_failed_camera_set_has_no_applied_claim` — three subTests: a bridge refusal raises a `ToolError` built by the real `_bridge_error` and its `camera_create_failed` code survives unchanged (no `fov_applied` anywhere); a timeout `ToolError` propagates the same way; a successful wire answer with an illegible camera observation (`camera.ok=0`, `camera_unavailable_no_scripted_camera`) is returned without the field.
- `test_983a_description_scopes_measured_default` — reads the registered description source (literals re-joined the way Python concatenates them) and pins: units (`radians`), resolution (`1920×1080`), run (`8834df0e`), date (`2026-10-04`), owner attribution, `not a native readback`, `does not guarantee the observed optical projection`, the zero-does-not-reset clause, and the retained no-getter qualification (`no Camera.IsInterpolationComplete / GetCurrentFOV`).

Fails-on-base check: with the `server.py` change temporarily reverted (both edits reverse-applied in place), the module fails 9 assertions — all six echo subTests, both zero-null subTests, and the description pins (`fov_applied` and the new wording absent on base). `test_983a_failed_camera_set_has_no_applied_claim` passes on base trivially: it pins the absence of a claim, which base satisfies. With the change applied the module is green (4 tests, ~0.5 s, fast-tier safe). No existing test was weakened, deleted, or needed a pin update: the `camera_set` description had no prior pin, and the source pins on the `camera_get` / `restore_gameplay` descriptions (`test_b3_4b_camera_contract.py`) and the native-getter guards (`test_camera_native_crash.py`) are untouched and pass.

## GATE OUTPUT

(last 8 lines of the final gate run are pasted below)

```
(pending)
```

## DEVIATIONS

- The specification's test section names `tools/tests/test_mcp_tools.py` as the location for the new tests; the acceptance gate requires a module named `tests.test_camera_set_fov_applied` ("Put new tests for this batch in the named module(s)"). I followed the gate and created `tools/tests/test_camera_set_fov_applied.py` with the four specified test names.
- The brief's boundary list allows edits only under `tools/dayz_mcp/`, `tools/tests/` and `CHANGELOG.md`, but specification item 3 says "Update D1 and changelog" and D1 lives in the root `product-spec.md`. I edited only the D1 row there and report it here; every other edit stays inside the listed paths (plus this REPORT.md, the requested deliverable).
- The new tests stub `runtime.call_bridge` instead of driving the `FakePeer` socket harness, and carry no `@slow_test`: the gate only ever runs the required module and the fast-tier discovery, both with `DAYZ_MCP_FAST_TESTS=1`, so a slow-marked test would be skipped and never executed by the gate. Stubbing keeps the module under a second, socket-free and load-safe; the failure subTest still raises through the production `_bridge_error` formatting. The harness-level integration (`FakePeer` → `call_bridge` → prune) for camera_set results is covered by existing slow-tier tests, which still pass.
- The echo additionally requires the nested camera observation to be legible (`camera.ok` truthy) on top of the top-level `ok` that `call_bridge` already guarantees — this implements the spec sentence "A failed apply, timeout, or illegible camera observation must not report an applied FOV", since the bridge reports `ok=1` with `camera.ok=0` when the apply succeeded but the shared snapshot is illegible.

## NOT VERIFIED

- The later in-game check from the specification (set a positive FOV, set zero, confirm the replies and a live render, then `restore_gameplay`) needs a live DayZ client and was not run in this environment. Per the spec, recalibrating the 68.5° measurement was not required and was not attempted.
- No PBO rebuild or daemon round-trip was exercised (the spec requires none: the wire is unchanged; the field is added Python-side after the bridge result arrives).
- The 68.5° vertical default at 1920×1080 is reproduced as documentation exactly as supplied (owner-provided measurement, run 8834df0e, 2026-10-04); its optical correctness is not verifiable offline.
