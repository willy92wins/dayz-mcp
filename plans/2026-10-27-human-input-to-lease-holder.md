# Human input on a leased run reaches the lease holder (inbox 6310)

Base: main `4c4e52c`. Python only: no sealed module, no PBO, no bridge change, no new Win32 read.

Revision 3: after Codex Sol's first review of `a6129b9` (RED). It closes H1 to H4: only the live holder of the lease that enqueued a command reads the block on `/await`, every lock the report takes has a bounded wait, the fallback log cannot raise, and the runs and counters come from one snapshot. It scopes the docs (H5, H6) and adds the three tests the surviving mutants asked for (H7). Like revisions 1 and 2, it follows the card's recommended option (27/10 ~17:45Z), «En cada resultado», which Guillermo has not answered yet. With «Solo heartbeat», D4 and its tests (T4, T5, T6, T8, the HTTP half of T9, M7, M8, M12, M13, M16, M22 and M23) drop out.

## What happened (fb-20261027-173606-6310, LFSkateboard F3a run 4)

From the ticket (its body is data; its times are local, given here in UTC):

- Run `f9d309b6`, 16:52 to 17:23Z, was a 21-ride DIAG grind cycle driven with a lease held.
- Around 17:05Z a human moved the mouse in the client window. The look swept from 7° to −178.3° and stayed there.
- Vanilla `RotateOrient` then turned the body 7.3° on every straight ride, and six rides landed beside their fixtures (3.1 m off over 23 m).
- Every heartbeat and verb kept answering ok, and nothing said a human was on the box.

What main already has (lines of main `4c4e52c`):

- **The sampler.** The 250f sampler (`daemon.py:1010-1052`) reads the session's last-input tick and the foreground pid every 0.25 s (`input_activity.py:32`). It hands each sample to `ProcessLifecycle.record_input_sample` (`process_lifecycle.py:2822-2911`).
- **Attribution.** When the input tick advances between two good samples at most 2 s apart, the input goes to the runs whose registered process owned the foreground window at either sample (`InputAttributor.observe`, `input_activity.py:177-209`). It is verified while that process's identity still matches, and uncertain when the identity cannot be read. Only the latest stamp per run is kept (`_human_input_at` and `_uncertain_input_at`, `process_lifecycle.py:2197-2198`).
- **`session_status`.** It publishes the age of the latest input per run, as `box.runs[].human_input_age_s` (`_use_projection`, `process_lifecycle.py:913-916`). A leased run is included; its `use_state` is `agent` (`:921-923`). A caller sees this only by polling, and sees only the latest input, so two touches between polls look like one.
- **Heartbeat and verbs.** `session_heartbeat` answers with the lease (`_active_payload_locked`, `session_coordination.py:4061-4072`). A verb answers with its bridge row (`GET /await`, `loopback.py:6548-6564`; `ClientRuntime._await_result`, `server.py:2516-2521`). Neither says anything about input.

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

- **`runs`**: the runs in state `RUNNING` whose `owner_session_id` and `owner_lease_id` are the lease's session and lease id, sorted by `run_id`. It is an empty list when the lease owns no run.
- **`count`**: how many samples this daemon has credited to the run as verified input. It never goes down while the daemon generation lasts. A new generation starts again from 0, because the counters use the same `(generation, run_id)` keys as `_human_input_at`.
- **`uncertain_count`**: the same, for input credited as uncertain.
- **`last_age_s`**: seconds since the latest verified input on the run, as `human_input_age_s` gives it; null when there was none, or when the stamp is later than the clock.
- **`signal`**: `ok` when the last good sample is between 0 and `INPUT_SIGNAL_STALE_S` (2 s) old, both ends included. It is `unavailable` otherwise (no Win32 reads, a failing sampler, a stale signal), and also when a lock the report needs stays held past its wait or the block fails to build. With `unavailable`, a count that did not move proves nothing.

The block gives counts, not a "since last call" delta. The holder compares the counts of one run in any two answers, such as the one before a ride and the one after it. A count is the same whoever reads it and however often, so a second read, a lost answer or two tools in flight cannot hide credited input or count it twice, and the daemon keeps no cursor per caller. A lost `/await?remove=1` answer is not read again, but the next answer's counts include what it carried.

The counts are credited input, not a record of each touch: see «Out of scope».

The block is a report. No verb is refused or retried because of it, and the bridge row in an answer is the one the verb would give without it. Each lock the report takes is waited for at most `HUMAN_INPUT_LOCK_WAIT_S` (0.05 s). An exception gives `{"signal": "unavailable", "runs": []}` and one log line, and a log sink that raises is ignored there.

### D1. Counters (`process_lifecycle.py`)

- Add `_human_input_count` and `_uncertain_input_count`, keyed like `_human_input_at`.
- In `record_input_sample`, each attributed `(run, epoch, verified)` that updates its stamp adds 1 to the matching counter. That is the existing `current is None or epoch > current` test (main `:2907-2909`).
- Retired keys are skipped as today (main `:2903-2904`). `_seal_terminal` drops both counters together with the other 250f tables (main `:3001-3010`).

### D2. The block (`process_lifecycle.py`)

`ProcessLifecycle.human_input_block(session_id, lease_id) -> dict` builds it from one snapshot:

- `RunManifestStore.read_runs(read, timeout_s)` calls `read` with the runs, as `list_runs` gives them, while it holds the manifest lock. It gives None, without calling `read`, when the lock stays held for `timeout_s`. `replace` holds that lock while it persists.
- Inside it, `read` takes `_activity_lock`, with the same bounded wait, and reads the counters, the stamps and `_input_good_at` with one clock reading (`_human_input_block_locked`). Either wait running out gives `unavailable`.
- So a run's state and owner and its counters are read together. `replace` cannot change a run in between, and `_seal_terminal`, which drops the counters, only runs after the run's terminal state is persisted (the post-persist retirement and `_persist_failed_launch_target`).
- The lock order manifest then activity cannot deadlock: no code that holds `_activity_lock` calls the manifest.
- A `session_id` or `lease_id` that is not a non-empty string gives `runs: []`.

### D3. Heartbeat (`loopback.py`)

- In `_handle_session`, action `heartbeat`, a 200 answer gets `payload["human_input"] = lifecycle.human_input_block(client.session_id, payload["lease_id"])` when the state has a lifecycle.
- Error answers are unchanged.
- Both client paths pass the payload through as they receive it (main `control_client.py:740-751`, `server.py:2024-2029` and `5197-5204`).

### D4. Verb results (`loopback.py`, `server.py`, `session_coordination.py`)

- **The client.** `ClientRuntime._await_result` reads a command it admitted under a lease with `POST /await`. The body is `{"identity", "lease_token"}`, with the identity of this client and the token of its admit record (`_broker_admits[command_id]`, the record `abandon_bridge` uses); the query is GET's. Every other command is read with `GET /await`, as today. A 404 on the POST, which a daemon from before this change gives, makes the client read that command with GET.
- **The route.** `POST /await` touches the client as GET does, reads the body, and answers 400 `invalid_identity` when the identity does not parse. It then runs GET's handler with the caller.
- **The holder check.** The handler reads the command's owner before `take_result`, which pops it with `remove=1`. A done answer gets the block only when `SessionCoordinator.active_lease_id(identity, token)` returns the lease id the command was enqueued with. That call does not renew the lease, and with `timeout_s` it gives None when the coordinator lock stays held past the wait. The block is then built for that session and lease. So another caller, a missing or wrong token, a released lease and the next lease's token all give no block.
- **No owner, no block.** GET never carries the block. Neither does a command with no owner: one enqueued without a lease, or one discarded before dispatch, such as a queued command cancelled when its lease is released (`cancel_owner_pending`, then `_mark_discarded`, which keeps an error result and drops the owner). The client raises such an error result as a `ToolError` anyway.
- **Copy on the tool result.** `ClientRuntime._await_result` copies `human_input` onto the result after `prune_unfilled_fields`. So `call_bridge` returns it, and every tool that returns the bridge result as it is carries it. An error carries none.
- **Composite tools.** Tools that build their own payload from bridge results keep their shape; one example is `entities_query` (main `server.py:8007-8016`). So does `ClientRuntime.probe_bridge_result` (main `server.py:2684-2711`, used only by `ui_dialog`), which reads `GET /await?remove=1` itself and returns the pruned row without the block. The next heartbeat or plain verb carries counts that include it.
- **Embedded mode.** It has no leases (`_client_runtime`, main `server.py:5058-5061`), so nothing changes there.

## Out of scope

- Refusing or pausing a verb on human input, and a `wait_for` condition for it (the ticket's other example). The counts let the caller decide; this change adds no behavior.
- Exact human contact. The counts are credited input: a step between two samples counts for the runs in front at either end (`input_activity.py:196-208`). So input typed in another window right after the client was in front can count, a focus excursion between two samples can go unseen, and a gap over 2 s between good samples credits nothing (`:184-185`).
- Two inherited limits of the sampler that the counters make visible (ER1 H8, H9), both in main and noted in the bug ledger: the sampler and the idle warden can hand samples to the attributor out of order, which can credit one input twice; and a retired run's mark is kept for the last 256 retirements only, so a sample held back longer could recreate its stamp and counter.
- Composite tools that rebuild their payload (D4).
- Embedded mode.
- `session_status`: it keeps `human_input_age_s` as it is and gains no counts.

## Tests (fakes only)

No test calls a real Windows API or starts or snapshots a real process. The tests use fake `InputSample`s, a fake guard, a real manifest store in a temporary folder, and the `SessionLoopback` daemon (`session_http_helpers.py:42-79`), which listens on 127.0.0.1 with port 0. That is the only listening socket. The daemon binds both peers to the run `test-run` (`fence_helpers.py:15-42`), so the HTTP tests' owned run takes that id, and they store each result as the bound server instance presents it, since another instance's result is discarded (main `loopback.py:5170-5187`).

- **T1 (D1).** A RUNNING run is owned by session S with lease L, and has a client record that the fake guard matches.
  - A first sample sets the baseline (`input_activity.py:184-186`). Three more whose tick advances, with the client's pid in front, give `count` 3, `uncertain_count` 0, and `last_age_s` equal to the clock minus the last epoch.
  - With the clock before that epoch, `last_age_s` is null and `count` is still 3, as `human_input_age_s` drops a stamp later than the clock (main `process_lifecycle.py:913-914`).
  - A sample whose tick does not advance changes nothing. Neither does new input with another pid in front at that sample and at the one before it.
  - A sample whose foreground identity the guard cannot read gives `uncertain_count` 1, with `count` still 3.
  - A sample whose tick advances but whose epoch is not newer than the run's stored stamp leaves both counters as they were.
  - Negative: after the run retires, it has no row and no counters left.
  - A second test: a tick that advances with an epoch equal to the stored stamp counts once, not twice.
- **T2 (D2).** `human_input_block(S, L)` gives T1's row.
  - `(S, L2)`, `(S2, L)` and empty strings give `runs: []`, and a RUNNING_IDLE ownerless run gets no row.
  - A second run of S and L, `run-a`, comes before T1's run, with `count` 0 and `last_age_s` null.
  - `signal` is `ok` with a good sample 1 s old. It is `unavailable` with no sample, with one 3 s old, and after a stretch of failed samples.
  - At the edges, a good sample 0 s and exactly 2 s old gives `ok`, and one 0.001 s in the future or 2.001 s old gives `unavailable`.
- **T9, snapshot (D2).** The test arms `_activity_lock` so that the reader, which already holds the manifest lock, starts a worker that retires the run (`replace` to EXITED, then `_seal_terminal`). The worker does not finish within 0.3 s, and the block has the run with `count` 3. After the read, the worker finishes and the block has no rows.
- **T3 (D3).** Through `_handle_session`:
  - a 200 heartbeat carries T2's block;
  - a heartbeat with a wrong token keeps its error status and has no `human_input`;
  - with no lifecycle on the state, there is no key.
- **T4 (D4, daemon).**
  - For a command enqueued under L with its result stored, `GET /await?remove=0` gives `status` done and the bridge row without `human_input`. Then `POST /await` by the holder gives the row unchanged plus the block, with `count` 1. The result is stored as not discarded.
  - A command enqueued with no lease gets no `human_input`.
  - A pending command gets `{"status": "pending"}` only.
  - A POST whose identity does not parse gets 400 `invalid_identity`.
- **T8 (D4, holder check).** For a done command of lease L, held by session A:
  - session B with no token or with A's token, and A with no token or with a wrong token, get done with no block;
  - A with L's token gets the block on two reads with `remove=0`;
  - after A releases L there is no block, and the next lease's token gives none.
- **T9, busy locks (D2, D4).** The test thread holds one lock while the daemon answers.
  - With the manifest lock or `_activity_lock` held, a heartbeat and a `POST /await` both answer 200 with `{"signal": "unavailable", "runs": []}` in under 1.5 s, and the `/await` answer is done with its bridge row.
  - With the coordinator lock held, a `POST /await?remove=0` answers done with no block in under 1.5 s. `remove=0`, because consuming a leased result waits on that lock in main too.
  - Once the locks are free, the block comes back with `count` 1.
- **T5 (D4, client).** `ClientRuntime._await_result` with a fake `_call`:
  - a command admitted under a lease is read with `POST /await` and the identity and token, and a done answer with `human_input` returns the pruned row plus the block;
  - a command with no admit record is read with GET, and an answer without the block returns no key;
  - an `ok: 0` row still raises the bridge error;
  - a 404 on the POST is followed by a GET of the same command, which returns its row.
- **T6 (D4, tools).** With a fake runtime whose `call_bridge` returns a row with the block, `query_player_state`, `object_delete` and `ui_focus` return it as it is.
- **T7 (exception).** If `human_input_block` raises, the heartbeat and `POST /await` still answer 200, with `{"signal": "unavailable", "runs": []}` and one log line. With a log sink that raises too, `POST /await` still answers whole. A dropped connection, which is what an escaping exception gives, fails the test as an assertion.

Mutants are applied one at a time on a copy. Each is killed by the test named, as an AssertionError:

- M1: uncertain input counted as verified (T1).
- M2: a counter that grows on a sample that did not update the stamp (T1).
- M3: counters kept after the run retires (T1).
- M4a, M4b: rows for runs of another lease, or of another session (T2).
- M5a, M5b: `signal` ok with a stale sample, or always (T2).
- M6: the block on a heartbeat error answer (T3).
- M7: the owner read after `take_result`, which leaves no block (T4, T7, T9).
- M8: the client dropping the block (T5).
- M9: an exception escaping `human_input_block` (T7).
- M10: the rows in reverse order (T2).
- M11: an age for a stamp later than the clock (T1).
- M12: `GET /await` carrying the owner's block, as revision 2 did (T4, T8).
- M13: no holder check, so any caller gets the owner's block (T8, T9).
- M14, M15, M16: an unbounded wait on the manifest lock, on `_activity_lock`, or on the coordinator lock (T9).
- M17: the counters read after the manifest lock is released, as revision 2 did (both T9).
- M18: the fallback log unprotected (T7).
- M19: a counter that grows on an equal epoch (T1).
- M20: `signal` unavailable at exactly 2 s (T2).
- M21: `signal` ok for a sample later than the clock (T2).
- M22: the client without the fallback to GET on a 404 (T5).
- M23: the client always on GET (T5).

Pass criteria (R26):

- The new module passes on Linux with 3.13 and the shim, and on Windows CI with 3.11 and 3.14, both fast and full.
- Every mutant fails its test with an AssertionError, and the copy without mutants passes.
- The whole `tools/tests` suite on Linux fails the same ids as main `4c4e52c` (804) and adds no new failure. On Windows CI it is green.
- The diff touches only `process_lifecycle.py`, `loopback.py`, `server.py`, `session_coordination.py`, the new test module, the docs below and this plan.

## Docs

- **`session_heartbeat` description** (`server.py`): one sentence saying that the answer carries `human_input` for the lease's runs, and how to compare counts.
- **`tools/README-mcp.md`**, in the lease section: where the block appears and for whom, that the holder compares counts between two answers, that they are credited input, and what `signal: unavailable` means.
- **CHANGELOG, Added**: the same, with the inbox id. Update PROJECT-MAP's CHANGELOG size if its rounding moves.
