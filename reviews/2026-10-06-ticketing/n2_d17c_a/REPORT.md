# REPORT — batch n2_d17c_a (client_process_gone admission gate)

## CHANGES

- `tools/dayz_mcp/process_lifecycle.py` — `ProcessLifecycle.classify_registered_client_liveness`
  (new read-only method, placed after `_partition_registered_processes`):
  classifies the client/offline records (`_PLAYER_ROLES`) of one exact
  registered run through `_classify_registered_process`, the full native
  identity snapshot — never through the `_client_liveness` PID census, which
  stays untouched for `use_state`. Verdicts: any `owned` record → `alive`; all
  records `gone`/`foreign` → `dead`; any `unknown` → `unknown`; a run with no
  client/offline record → `none` (no client process, not a guessed death);
  unreadable manifest entry, missing run, unreadable process list, bad run id
  or a client-shaped record that is not a `ProcessRecord` → `unknown`. It takes
  no `_operation_lock`, terminates nothing, reaps nothing, relaunches nothing.
  Round 2 (F1): it accepts an optional `destination` (pid, creation_time_utc)
  — the identity the caller is about to publish to — and a `dead` verdict only
  stands when the classified snapshot actually observed that identity; a
  pinned destination absent from the snapshot (reattach confirmed against the
  station before its manifest record is published) reads `unknown` instead of
  inheriting the superseded client's death.

- `tools/dayz_mcp/loopback.py`:
  - `_CLIENT_PROCESS_GONE_HINT`, `_CLIENT_GATE_VERDICTS` (module constants):
    the bounded hint (names `session_status` and the `dayz_test_run
    mode=client` reattach path, ≤240 chars) and the verdict whitelist (a
    foreign shape can never read as a death).
  - `ServerState._enqueue_command`: admission for `peer=="client"` (non-
    internal) now resolves the fence once under `ServerState._lock`, probes
    the registered client identity OUTSIDE the lock, re-resolves the fence
    and revalidates the destination pin under the lock, and only then
    publishes or refuses. A destination whose pin (instance, run_id, epoch,
    pid, creation time) changed while the probe ran discards the death
    verdict and follows the existing path. Refusal: HTTP 409
    `{"error": "client_process_gone", "run_id": ..., "hint": ...}` before any
    id is assigned, before the lease commit and before publication; the
    coordinated reservation is aborted with that reason.
  - `ServerState._admit_enqueue_locked` (new): stopping/fence/queue_full
    resolution plus the gate-pin capture, called under the lock. Existing
    error precedence is preserved: `enqueue_cancelled`, fence refusals
    (`run_not_owned`, `binding_retired`, `run_state_unavailable`,
    `instance_peer_collision`, ...), `queue_full` — then the gate.
  - `ServerState._client_gate_pin_locked` (new): identity pin of the exact
    bound client destination, or None (legacy/unbound queues and non-client
    peers are never gated; `internal=True` — the H5 fire-and-forget
    `vehicle_release` cleanup — is never gated).
  - `ServerState._registered_client_verdict` (new): calls the lifecycle
    helper with the lock released, passing the pin's (pid, creation_time) as
    the `destination`; an older lifecycle without the helper (round-1
    signature), a failed call, a pin whose identity cannot be tied to the
    probe, or a foreign verdict is `unknown` (fail-open to today's
    admission), never a guessed death.
  - Round 2 (F1): the gate call site passes the pinned destination identity
    to the probe, so the death evidence is bound both to the destination pin
    (instance/epoch/pid/creation-time revalidation) and to the registered
    identities the snapshot actually observed.
  - `ServerState._publish_enqueue_locked` (new): the verbatim publication
    block (id assignment, lease commit, owner mapping, append, seal,
    activity epoch) extracted so both the single-lock path and the
    probe-then-revalidate path share it.

- `tools/dayz_mcp/bridge_errors.py` — `_REMOTE_ERROR_CODES`: added
  `client_process_gone` (with a comment: process absence/identity loss, not a
  crash diagnosis), so `ToolError` translation keeps the named token and its
  hint (`client_process_gone: <hint>`) on both the embedded and HTTP client
  runtimes; it is not a `_PUBLISHED_NOT_READY_CODES` reason and is never
  rewritten to `game_not_ready:reason=client_not_polling` or `remote_error`.

- `CHANGELOG.md` — one line under `[Unreleased] / Fixed`.

- `tools/tests/test_client_process_gone.py` — new module (29 tests), see below.

No queue, termination, reap, relaunch or ownership side effect was added;
`capture_screenshot` (host capture) is untouched and keeps its own window
errors; no wire argument, PBO, reseal or manifest migration.

## TESTS ADDED

Module `tools/tests/test_client_process_gone.py`:

- `test_d17ca_dead_client_refused_before_command_publication` — registered
  absent client; immediate 409 token with run_id and bounded hint; queue,
  `_command_owner` and `_next_id` unchanged; poll delivers nothing; the gate
  probed the registered identity; `terminate_calls`/launcher calls empty.
- `test_d17ca_refusal_aborts_the_lease_reservation` — coordinated mutation
  (`player_move` + lease): refusal aborts the reservation, no owner mapping,
  queue empty.
- `test_d17ca_all_client_verbs_use_dead_process_gate` — parameterizes the
  whole `CLIENT_COMMANDS` census (30 verbs, each self-validated against
  `validate_command_args`) through the gate; includes `ui_dialog`'s args.
- `test_d17ca_ui_dialog_enqueue_probe_path_refuses_at_once` — drives
  `server.execute_ui_dialog` over a real `server.Runtime`: the named refusal
  comes from the enqueue before any result probe (ToolError text carries the
  token and hint).
- `test_d17ca_unknown_client_identity_allows_existing_admission` — guard
  answers `identity_unavailable`: the command enqueues as today.
- `test_d17ca_failing_guard_observation_passes_through` — a raising guard is
  doubt, not a death.
- `test_d17ca_lifecycle_without_the_helper_keeps_today_s_admission` — an older
  lifecycle object without the helper keeps the existing admission.
- `test_d17ca_run_without_client_record_does_not_guess_death` — a run with no
  client/offline record has no client process: the gate stays open.
- `test_d17ca_reused_pid_is_not_a_live_client` — complete mismatching
  identity (reused pid) is foreign → refused.
- `test_d17ca_binding_change_discards_death_observation` — the probe itself
  rebinds the destination; the verdict is discarded and the command follows
  the normal path on the new binding; the probe ran with the loopback lock
  released.
- `test_d17ca_confirmed_replacement_before_published_record_admits` (F1) —
  the reattach window: the replacement binding is already confirmed (pinned
  pid 9102) while the manifest still lists only the superseded client (pid
  9101, complete mismatching identity); the command is admitted, not
  refused, and the guard only ever probed the registered old identity.
- `test_d17ca_replacement_publishing_mid_probe_discards_stale_death` (F1) —
  the reviewer's race: destination pinned to the replacement, the
  replacement's manifest record published while the probe holds the stale
  clone; the stale death is discarded, the command follows the normal path,
  and the registered liveness afterwards reads `alive`.
- `test_d17ca_helper_death_requires_the_pinned_identity_observed` (F1) —
  helper contract: `destination` observed in the snapshot → `dead` stands;
  destination absent from the snapshot → `unknown`.
- `test_d17ca_probe_never_holds_the_lifecycle_operation_lock` — the lifecycle
  `_operation_lock` is not held during the probe.
- `test_d17ca_ownership_error_precedes_the_gate` — `RUNNING_IDLE` →
  `run_not_owned` wins over the gate.
- `test_d17ca_retired_binding_error_precedes_the_gate` — retired client
  binding → `binding_retired` wins over the gate.
- `test_d17ca_internal_cleanup_enqueue_skips_the_gate` — H5 cleanup
  `vehicle_release` still enqueues with the client gone.
- `test_d17ca_alive_client_still_admits` — owned identity → normal admission.
- `test_d17ca_dead_server_does_not_gate_server_peer` — dead server record:
  `peer="server"` admits while the same box refuses the client verb.
- Helper contract (`classify_registered_client_liveness`): alive-on-any-match,
  dead-on-all-gone/foreign, unknown-on-any-unreadable, none-without-player-
  records, unknown for missing run / bad run id / failing manifest read /
  unreadable run shape; no terminate calls.
- `test_d17ca_public_error_keeps_named_token` — the token is whitelisted, not
  a ready reason; the hint travels; oversized/missing hint keeps the bare
  token.
- `test_d17ca_http_refusal_carries_409_and_run_id` — HTTP `/enqueue` answers
  409 with the token, run_id and hint.

On the unmodified tree (measured by reverting the source files and running
the module): every gate-detecting test fails or errors —
`test_d17ca_dead_client_refused_before_command_publication` gets 200 instead
of 409 (the command would have waited the whole timeout on a dead client);
the census, reused-pid, binding-change, ui_dialog-path, HTTP-refusal and
public-token tests fail for the same missing gate/whitelist; the helper
contract tests fail with AttributeError. Re-measured in round 2 against the
round-1 gate (destination-less probe): the three F1 tests fail — both
replacement-race tests get 409 instead of 200 (the live replacement would
inherit the superseded client's death) and the helper destination contract
errors on the missing parameter — while the 26 pre-existing tests still pass
unchanged. The pass-through controls
(unknown identity, no helper, no client record, alive client, ownership and
retired-binding precedence, internal cleanup) pass on both trees by design.

## ROUND 2 FIXES

- **F1 (P2) — a live replacement can inherit the old client's death verdict:
  fixed.**
  - `classify_registered_client_liveness` now takes `destination` (the (pid,
    creation_time_utc) the caller is about to publish to) and answers
    `unknown` for a `dead` classification whose snapshot did not observe the
    pinned identity. The reattach path confirms the station binding before
    its manifest record is published, so a clone taken inside that window
    lists only the superseded client; that death is no longer inherited, and
    the live replacement keeps the existing admission. Death evidence is now
    tied to both halves the reviewer asked for: the registered identities
    actually probed (observed inside the helper) and the pinned destination
    (passed from the gate pin).
  - `ServerState._registered_client_verdict` passes the pin's (pid,
    creation_time) as `destination`; a pin without a tieable identity, an
    older round-1-signature helper (TypeError), or any failed probe stays
    `unknown` — fail-open, no guessed death. Native probing stays outside
    `ServerState._lock` (unchanged from round 1, still asserted by tests).
  - The gate call site passes `(gate_pin[3], gate_pin[4])`; the
    probe-then-revalidate flow, the pin equality check, and every other
    round-1 behaviour are unchanged.
  - New regressions: `test_d17ca_confirmed_replacement_before_published_record_admits`
    (static window: replacement binding confirmed, record not yet published),
    `test_d17ca_replacement_publishing_mid_probe_discards_stale_death` (the
    reviewer's deterministic race with the pin unchanged: the record is
    published mid-probe, the stale death is discarded, liveness afterwards
    reads `alive`), and `test_d17ca_helper_death_requires_the_pinned_identity_observed`
    (helper contract). All three fail on the round-1 gate (measured: 409
    instead of 200, and a TypeError on the missing parameter) and pass with
    the fix; the 26 pre-existing tests pass on both trees.
  - Fixture realism fix in `_gate_state`: `install_bound_peer` now receives
    the record's own `creation_time_utc`, mirroring production `confirm`,
    which copies the record identity onto the binding; the round-1 default
    stamp made the pinned identity differ from the registered record the
    helper observes.
- **F2 (P3) — H6/H13 contract documentation: unchanged (boundary).**
  product-spec.md sits outside the brief's edit boundary (`tools/dayz_mcp/`,
  `tools/tests/`, `CHANGELOG.md` only), and this round's instruction is to
  fix the blocking P1/P2 findings only. The refusal is documented once in
  CHANGELOG.md (Unreleased/Fixed, extended this round with the
  destination-tie clause) and in the code (`_CLIENT_PROCESS_GONE_HINT`, the
  helper docstring, the gate comment). See DEVIATIONS.

## GATE OUTPUT
(pending)

## DEVIATIONS

- product-spec.md H6/H13 rows were NOT updated: the brief's boundary list
  allows edits only under `tools/dayz_mcp/`, `tools/tests/` and
  `CHANGELOG.md`, and product-spec.md sits outside them. The refusal is
  documented once in CHANGELOG.md (Unreleased/Fixed) and in the code comments
  (`_CLIENT_PROCESS_GONE_HINT`, the helper docstring, the gate comment).

## NOT VERIFIED

- The later live check (owner-authorized client closure, then `camera_set`
  refusing at once while the registered run still exists; a live loading
  client keeping its prior behavior) needs a real box and was not run here.
- The whole-suite non-fast tier (DAYZ_MCP_FAST_TESTS unset) was not run; the
  gate and single modules ran with the fast tier.
