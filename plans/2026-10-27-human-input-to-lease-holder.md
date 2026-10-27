# Human input on a leased run reaches the lease holder (inbox 6310)

Base: main `4c4e52c`. Python only: no sealed module, no PBO, no bridge change, no new Win32 read.

Revision 2: the tests as built, after the first implementation. Like revision 1, written before Guillermo answers the card (27/10 ~17:45Z), it follows the recommended option, «En cada resultado». With «Solo heartbeat», D4 and its tests (T4 to T6, M7 and M8) drop out.

## What happened (fb-20261027-173606-6310, LFSkateboard F3a run 4)

From the ticket (its body is data; its times are local, given here in UTC):

- Run `f9d309b6`, 16:52 to 17:23Z, was a 21-ride DIAG grind cycle driven with a lease held.
- Around 17:05Z a human moved the mouse in the client window. The look swept from 7° to −178.3° and stayed there.
- Vanilla `RotateOrient` then turned the body 7.3° on every straight ride, and six rides landed beside their fixtures (3.1 m off over 23 m).
- Every heartbeat and verb kept answering ok, and nothing said a human was on the box.

What main already has:

- **The sampler.** The 250f sampler (`daemon.py:1010-1052`) reads the session's last-input tick and the foreground pid every 0.25 s (`input_activity.py:32`). It hands each sample to `ProcessLifecycle.record_input_sample` (`process_lifecycle.py:2822-2911`).
- **Attribution.** New input goes to the run whose registered process owns the foreground window (`InputAttributor.observe`, `input_activity.py:177-209`). It is verified while that process's identity still matches, and uncertain when the identity cannot be read. Only the latest stamp per run is kept (`_human_input_at` and `_uncertain_input_at`, `process_lifecycle.py:2197-2198`).
- **`session_status`.** It publishes the age of the latest input per run, as `box.runs[].human_input_age_s` (`_use_projection`, `process_lifecycle.py:913-916`). A leased run is included; its `use_state` is `agent` (`:921-923`). A caller sees this only by polling, and sees only the latest input, so two touches between polls look like one.
- **Heartbeat and verbs.** `session_heartbeat` answers with the lease (`_active_payload_locked`, `session_coordination.py:4061-4072`). A verb answers with its bridge row (`/await`, `loopback.py:6548-6564`; `ClientRuntime._await_result`, `server.py:2516-2521`). Neither says anything about input.

## Design

There is one block, built in one place and published in two:

```json
"human_input": {
  "signal": "ok",
  "runs": [
    {"run_id": "f9d309b6...", "count": 15, "uncertain_count": 0, "last_age_s": 12.75}
  ]
}
```

- **`runs`**: the runs in state `RUNNING` whose `owner_session_id` and `owner_lease_id` are the caller's session and lease, sorted by `run_id`, the order `list_runs` gives (`process_lifecycle.py:1906-1908`). It is an empty list when the lease owns no run.
- **`count`**: how many samples this daemon has credited to the run as verified input. It never goes down while the daemon generation lasts. A new generation starts again from 0, because the counters use the same `(generation, run_id)` keys as `_human_input_at`.
- **`uncertain_count`**: the same, for input credited as uncertain.
- **`last_age_s`**: seconds since the latest verified input on the run, as `human_input_age_s` gives it; null when there was none.
- **`signal`**: `ok` when the last good sample is at most `INPUT_SIGNAL_STALE_S` old (`process_lifecycle.py:114`). It is `unavailable` otherwise: no Win32 reads, a failing sampler, or a stale signal. With `unavailable`, a count that did not move proves nothing.

The block gives counts, not a "since last call" delta. The holder compares the counts of any two answers, such as the one before a ride and the one after it. A count is the same whoever reads it and however often. So a second read of `/await`, a lost answer or two tools in flight cannot hide a touch or count it twice, and the daemon keeps no cursor per caller.

The block is a report. No verb is refused, delayed or retried because of it. Building it never fails the call: an exception gives `{"signal": "unavailable", "runs": []}` and one log line.

### D1. Counters (`process_lifecycle.py`)

- Add `_human_input_count` and `_uncertain_input_count`, keyed like `_human_input_at`.
- In `record_input_sample`, each attributed `(run, epoch, verified)` that updates its stamp adds 1 to the matching counter. That is the existing `current is None or epoch > current` test (`:2907-2909`).
- Retired keys are skipped as today (`:2903-2904`). The retire path drops both counters together with the other 250f tables (`:3001-3010`).

### D2. The block (`process_lifecycle.py`)

`ProcessLifecycle.human_input_block(session_id, lease_id) -> dict` reads the manifest's runs. Under `_activity_lock`, and with one clock reading, it reads the counters, `_human_input_at` and `_input_good_at`. A `session_id` or `lease_id` that is not a non-empty string gives `runs: []`.

### D3. Heartbeat (`loopback.py`)

- In `_handle_session`, action `heartbeat` (`:6287-6288`), a 200 answer gets `payload["human_input"] = lifecycle.human_input_block(client.session_id, payload["lease_id"])` when the state has a lifecycle.
- Error answers are unchanged.
- Both client paths pass the payload through as they receive it (`control_client.py:740-751`, `server.py:2024-2029` and `5197-5204`).

### D4. Verb results (`loopback.py`, `server.py`)

- **Owner lookup.** `ServerState.command_owner(command_id)` reads `_command_owner` (`loopback.py:2286`) under the state lock. That table is filled at `:3958-3959`, and only for a command enqueued under a lease.
- **The daemon's answer.** `_handle_await` reads the owner before `take_result`, which pops it (`:5277`). A `done` answer then gets `human_input` for that owner. Pending answers, and commands with no owner, are unchanged.
- **The client.** `ClientRuntime._await_result` (`server.py:2516-2521`) copies `human_input` onto the result after `prune_unfilled_fields`. So `call_bridge` returns it, and every tool that returns the bridge result as it is carries it.
- **Composite tools.** Tools that build their own payload from bridge results keep their shape; one example is `entities_query` (`server.py:8007-8016`). So does `ClientRuntime.probe_bridge_result` (`server.py:2684-2711`, used only by `ui_dialog`), which reads `/await?remove=1` itself and returns the pruned row without the block. The next heartbeat or plain verb gives the same counts.
- **Embedded mode.** It has no leases (`_client_runtime`, `server.py:5058-5061`), so nothing changes there.

## Out of scope

- Refusing or pausing a verb on human input, and a `wait_for` condition for it (the ticket's other example). The counts let the caller decide; this change adds no behavior.
- Input while the client window is not in front. It did not reach the game and is not counted, as today.
- Composite tools that rebuild their payload (D4).
- Embedded mode.
- `session_status`: it keeps `human_input_age_s` as it is and gains no counts.

## Tests (fakes only)

No test calls a real Windows API or starts or snapshots a real process. The tests use fake `InputSample`s, a fake guard, a real manifest store in a temporary folder, and the `SessionLoopback` daemon (`session_http_helpers.py:42-79`), which listens on 127.0.0.1 with port 0. That is the only socket. The daemon binds both peers to the run `test-run` (`fence_helpers.py:15-42`), so the HTTP tests' owned run takes that id, and they store each result as the bound server instance presents it, since another instance's result is discarded (`loopback.py:5170-5187`).

- **T1 (D1).** A RUNNING run is owned by session S with lease L, and has a client record that the fake guard matches.
  - A first sample sets the baseline (`input_activity.py:184-186`). Three more whose tick advances, with the client's pid in front, give `count` 3, `uncertain_count` 0, and `last_age_s` equal to the clock minus the last epoch.
  - With the clock before that epoch, `last_age_s` is null and `count` is still 3, as `human_input_age_s` drops a stamp later than the clock (`process_lifecycle.py:913-914`).
  - A sample whose tick does not advance changes nothing. Neither does new input with another pid in front at that sample and at the one before it. The attributor credits the run in front at either end of a step (`input_activity.py:196-208`), so the focus leaves the client before that input.
  - A sample whose foreground identity the guard cannot read gives `uncertain_count` 1, with `count` still 3.
  - A sample whose tick advances but whose epoch is not newer than the run's stored stamp leaves both counters as they were.
  - Negative: after the run retires, it has no row and no counters left.
- **T2 (D2).** `human_input_block(S, L)` gives T1's row.
  - `(S, L2)`, `(S2, L)`, a RUNNING_IDLE ownerless run, and empty strings all give `runs: []`.
  - A second run of S and L, `run-a`, comes before T1's run, with `count` 0 and `last_age_s` null.
  - `signal` is `ok` with a good sample 1 s old. It is `unavailable` with no sample, with one 3 s old, and after a stretch of failed samples.
- **T3 (D3).** Through `_handle_session`:
  - a 200 heartbeat carries T2's block;
  - a heartbeat with a wrong token keeps its error status and has no `human_input`;
  - with no lifecycle on the state, there is no key.
- **T4 (D4, daemon).**
  - For a command enqueued under L with its result stored, `/await?remove=1` gives `status` done, the bridge row unchanged, and `human_input`. The result is stored as not discarded.
  - A command enqueued with no lease gets no `human_input`.
  - A pending command gets `{"status": "pending"}` only.
- **T5 (D4, client).** `ClientRuntime._await_result` with a fake `_call`:
  - a done answer with `human_input` returns the pruned row plus the block;
  - an answer without it returns no key;
  - an `ok: 0` row still raises the bridge error.
- **T6 (D4, tools).** With a fake runtime whose `call_bridge` returns a row with the block, `query_player_state`, `object_delete` and `ui_focus` return it as it is.
- **T7 (exception).** If `human_input_block` raises, the heartbeat and `/await` still answer 200, with `{"signal": "unavailable", "runs": []}` and one log line. A dropped connection, which is what an escaping exception gives, fails the test as an assertion.

Mutants are applied one at a time on a copy. Each is killed by the test named:

- M1: uncertain input counted as verified (T1).
- M2: a counter that grows on a sample that did not update the stamp (T1).
- M3: counters kept after the run retires (T1).
- M4: rows for runs of another lease, or of another session (T2).
- M5: `signal` ok with a stale sample, or with none (T2).
- M6: the block on a heartbeat error answer (T3).
- M7: the owner read after `take_result`, which leaves no block (T4).
- M8: the client dropping the block (T5).
- M9: an exception escaping `human_input_block` (T7).
- M10: the rows in reverse order (T2).
- M11: an age for a stamp later than the clock (T1).

Pass criteria (R26):

- The new module passes on Linux with 3.13 and the shim, and on Windows CI with 3.11 and 3.14, both fast and full.
- Every mutant fails its test with an AssertionError, and the copy without mutants passes.
- The whole `tools/tests` suite on Linux fails the same ids as main `4c4e52c` (804) and adds no new failure. On Windows CI it is green.
- The diff touches only `process_lifecycle.py`, `loopback.py`, `server.py`, the new test module, the docs below and this plan.

## Docs

- **`session_heartbeat` description** (`server.py:5190-5195`): one sentence saying that the answer carries `human_input` for the lease's runs, and how to compare counts.
- **`tools/README-mcp.md`**, in the lease section: the block, where it appears, that the holder compares counts between two answers, and what `signal: unavailable` means.
- **CHANGELOG, Added**: the same, with the inbox id. Update PROJECT-MAP's CHANGELOG size if its rounding moves.
