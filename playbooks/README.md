# Playbooks

A playbook is an executable checklist: an ordered list of observable
predicates, not instructions. The list is data (TOML). The runner is generic
and does not know any playbook by name. Each declared `on_fail` has a RED
fixture that proves the gate fires.

## Lifecycle

| From | To | Gate |
| --- | --- | --- |
| DRAFT | PROBED | Cold probe by two independent agents. |
| PROBED | CALIBRATED | Every gating threshold has measured data. |
| CALIBRATED | FROZEN | Green fixtures plus a content SHA of the playbook and its fixtures. |

`playbook_run` reports `certified: false` / `certified_reason: no_frozen_registry` until that SHA lives in a sidecar FROZEN registry (never inside the TOML).

A threshold without measured data stays `state = "uncalibrated"` and never
gates. A FAIL on that expect is downgraded to WARN and the verdict reason is
`uncalibrated_gate_downgraded`.

## Dictionary

| id | when to use | status | requires |
| --- | --- | --- | --- |
| place_safely | Before spawn or teleport to `(x, z)`: solid ground, clear vertical column, no players inside `clear_r`. **DRAFT** — the TOML declares `status = "DRAFT"` (the lifecycle still requires a cold probe by two independent agents), so a PASS does not certify a safe site. | DRAFT | `surface_query`, `scene_raycast`, `query_all_players`, `entities_query` |
| box_is_mine | Before a mutating verb: this session holds the lease, no unmanaged DayZDiag, `box.runs[0].run_id` is the run I intend to handle. | DRAFT | `session_status` |
| run_really_started | After `dayz_test_run` returns a `run_id`: that id is in `session_status.box.runs`, `state` is `RUNNING`, and `bridge_status.ready.reason` is `ready`. Complements `last_start_error` on the compact. | DRAFT | `session_status`, `bridge_status` |

## Running

From the repository root. The runner is stdlib Python (3.11+ for `tomllib`).

### Fixtures (CI)

A directory replays every `*.json` and exits 0 only when each file's
`expect_overall` / `expect_stopped_at` / `expect_reason` match the verdict.
A single file emits one verdict: exit 0 on `PASS` / `PASS_WITH_WARNINGS`, 1 on
`FAIL`.

```text
python playbooks/runner.py playbooks/place_safely.toml --fixtures playbooks/fixtures/place_safely
python playbooks/runner.py playbooks/place_safely.toml --fixtures playbooks/fixtures/place_safely/pass.json
```

### Live

`--live` starts a new MCP stdio client against the shared daemon (the
`CONFIG` block at the top of `runner.py`). The client may autospawn that
daemon if no listener is present. The `mcp` package is imported only on
this path. The MCP tool `playbook_run(name, params)` uses the same
runner in the current session/lease and does not launch DayZ;
`certified` is always false until the FROZEN sidecar registry exists.

With DayZ off, S1 fails with `tool_error` `version_blocked` in about
13-15 s and the process exits 1. That means no DayZ peers, not a
playbook bug.

```text
python playbooks/runner.py playbooks/place_safely.toml --live --param x=7512 --param z=7502
```

`--json-out FILE` writes the same JSON printed to stdout.

Exit codes: `0` pass, `1` fail, `2` schema or usage error. Schema validation
runs before any tool call. A schema error names the step and the field.

## Semantics

A `$ref` is the whole string: `$param` or `$Step.field` (dotted path).
The only arithmetic is one trailing `+ n` or `- n` on that string
(`"$S1.y + 30"`). Anything else that contains `$` is a schema error.

Operators: `eq`, `neq`, `lt`, `lte`, `gt`, `gte`, `finite`, `contains`,
`absent`, `near`, `min_dist_xz_gte`. Shipped playbooks use `eq`,
`finite`, `near`, `lte`, and `min_dist_xz_gte`; the rest exist and
nobody uses them. `eq` is Python `==`, so TOML `true` matches JSON `1`.

If a tool raises, that step is `FAIL` with reason `tool_error:<detail>`
and every later step is `SKIPPED`. That step's `on_fail` is not used.

Live payloads: if the result is an object that has a `result` object and
no top-level `ok`, the runner unwraps to that inner object. Fixture JSON
is not unwrapped.

`--fixtures` on an empty directory is overall `FAIL` /
`fixture_mismatch`. Zero files is not a pass. The same reason is used
when a file's `expect_*` fields do not match the verdict.

Lifecycle and lease tools (`dayz_test_run`, `dayz_test_stop`,
`exec_enforce`, `session_release`, `session_acquire`,
`session_acquire_wait`, `lease_acquire`, `session_wait`,
`session_cancel`, `session_heartbeat`, `playbook_run`) are denied by
the MCP adapter (`tools/dayz_mcp/playbook_tool.py`), not by this
runner. CLI `--live` does not apply that denylist.

There is no `requires_bridge` field. Live version gating is the MCP
client's `--require-version` (see `CONFIG` in `runner.py`).

## Adding a playbook

Submit through the `pipeline_feedback` mailbox with `kind=tool_contribution`.
Do not add a playbook by editing this tree ad hoc.

## Calibration

`near` may take `tol` (literal) or `tol_ref` (a `[[calibration]]` name).
Measured rows under `[[calibration.measured]]` are evidence, not runtime
inputs. The runner reads `threshold` and `state` only. No measured data means
the calibration stays uncalibrated and must not STOP a playbook.

`place_safely`'s `canopy_dy` is **calibrated** (2026-08-19): 9 clear sites measured against the
3 required, max observed delta 0.000244 (8 ULP of float32) versus 4.89 m for a real canopy. The
gate STOPS. The threshold 0.05 is deliberately untouched: with four orders of magnitude between
noise and obstruction, any value in a wide band behaves identically.

A DRAFT playbook with `state = "uncalibrated"` does not certify a clear
canopy. Do not spawn or teleport unless S2 is a clean PASS, not
`PASS_WITH_WARNINGS` from `uncalibrated_gate_downgraded`. S4 inspects
only the vertical column (`entities_query` radius 5) and players inside
`clear_r`; it does not detect lateral solids (a rock beside the point
does not fail the playbook).

After a clean PASS, spawn or teleport at `pos=[x, S1.y, z]` (Spawn
safely in [`../tools/README-mcp.md`](../tools/README-mcp.md)).
