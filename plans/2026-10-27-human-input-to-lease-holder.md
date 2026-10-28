# Human input on a leased run reaches the lease holder (inbox 6310)

Base: main `9713894`; revisions 1 to 4 were drawn on `4c4e52c`. Python only: no sealed module, no PBO, no bridge change, no new Win32 read.

Revision 6: after Codex Sol's fourth review, of `2326538` (revision 5), GREEN with four test issues and one plan issue (r4 H1 to H5). T7 joins the diagnostic thread that wrote a line before it waits for the next one, since the sink's event comes before that thread frees its slot (r4 H1). T7b counts the HTTP worker slots and waits after each answer until every slot is back, so the next request finds a free worker and every handler has started its diagnostic before the test joins those threads. With the sink still held, it checks that the workers are back and that the sink runs on the diagnostic thread, and after the release that the test released the sink, not its own bound (r4 H2 and H4). A new T7c makes the first diagnostic thread fail to start, and checks that the slot comes back and the next line is written (r4 H3). Two new mutants go with them: M38 writes the line on the HTTP worker once it has the slot, and M39 keeps the slot when the thread fails to start. The base and the suite's comparison are `9713894`, the main this was tested on (r4 H5). The 2 s bound of the shared HTTP helper and the tests' 10, 30 and 60 s bounds stay: they keep a stuck run from hanging, and a runner paused past one fails the test (r4 H4).

Revision 5: after Codex Sol's third review, of `19e79ab` (revision 4), which found four issues (r3 H1 to H4). The failure line is logged off the HTTP worker: at most one diagnostic thread waits on the sink, and the line is dropped if that slot is taken or the server has no semaphore (`Handler._log_human_input_block_failed`, r3 H1). The docs follow the code: a tool that returns the bridge row, including `entities_query`, carries the block; `wait_for` and `ui_dialog` do not (r3 H2). A new T8 records that a pin past the TTL still names the lease until `effective_expiry` (r3 H3). Calls that wait while a lock is held use a 30 s bound, the unit T9 lock is released when the test sets done or after 30 s, and the T7 calls in front of the stuck sink wait 10 s against its 60 s (r3 H4). r1's H8 and H9 stay out of scope (see «Out of scope»). Like revisions 1 to 4, this follows the card's recommended option (27/10 ~17:45Z), «En cada resultado», which Guillermo has not answered yet. With «Solo heartbeat», D4 and what tests it drop out: T4, T5, T6, T8, the `/await` halves of T7, T7b and T9, and M7, M8, M12, M13, M16, M16b, M16c, M22, M23, M25, M26, M27, M29, M31, M35, M36 and M37.

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
- **`signal`**: `ok` when the last good sample is between 0 and `INPUT_SIGNAL_STALE_S` (2 s) old, both ends included. It is `unavailable` otherwise (no Win32 reads, a failing sampler, a stale signal), and also when another thread holds a lock the report needs, or when the block fails to build. With `unavailable`, a count that did not move proves nothing.

The block gives counts, not a "since last call" delta. The holder compares the counts of one run in any two answers, such as the one before a ride and the one after it. A count is the same whoever reads it and however often, so a second read, a lost answer or two tools in flight cannot hide credited input or count it twice, and the daemon keeps no cursor per caller. A lost `/await?remove=1` answer is not read again, but the next answer's counts include what it carried.

The counts are credited input, not a record of each touch: see «Out of scope».

The block is a report. No verb is refused or retried because of it, and the bridge row in an answer is the one the verb would give without it. The report waits for no lock: it tries each lock it takes once (`blocking=False`), and one that another thread holds gives `unavailable` at once. On `/await?remove=1` the result is already consumed when the report is built, so a wait there could make the answer miss the client's deadline and lose the result. A lock held only briefly, such as `_activity_lock` while the sampler records a sample every 0.25 s, can still make one answer `unavailable`; the counts are cumulative, so the next answer shows what that one missed. After the result is taken, the report adds the clone of the runs and the build, and no lock wait; main's own consumption of a leased result still waits for the coordinator lock to finish the operation (`finish_operation_exact`), as before. An exception gives `{"signal": "unavailable", "runs": []}` and one log line, which is written after the answer: the answer has a Content-Length and the handler writes it unbuffered, so a sink that raises or blocks cannot hold it back.

### D1. Counters (`process_lifecycle.py`)

- Add `_human_input_count` and `_uncertain_input_count`, keyed like `_human_input_at`.
- In `record_input_sample`, each attributed `(run, epoch, verified)` that updates its stamp adds 1 to the matching counter. That is the existing `current is None or epoch > current` test (main `:2907-2909`).
- Retired keys are skipped as today (main `:2903-2904`). `_seal_terminal` drops both counters together with the other 250f tables (main `:3001-3010`).

### D2. The block (`process_lifecycle.py`)

`ProcessLifecycle.human_input_block(session_id, lease_id) -> dict` builds it from one snapshot:

- `RunManifestStore.read_runs(read)` calls `read` with the runs, as `list_runs` gives them, while it holds the manifest lock. It gives None, without calling `read`, when another thread holds that lock: it does not wait for it. `replace` holds that lock while it persists.
- Inside it, `read` tries `_activity_lock` the same way, then reads the clock once and, with it, the counters, the stamps and `_input_good_at` (`_human_input_block_locked`). Either lock held by another thread gives `unavailable`. The clock is read with both locks held, so the sample's age is measured at the moment the counters are read (r2 H5).
- So a run's state and owner and its counters are read together. `replace` cannot change a run in between, and `_seal_terminal`, which drops the counters, only runs after the run's terminal state is persisted (the post-persist retirement and `_persist_failed_launch_target`).
- The lock order manifest then activity cannot deadlock: no code that holds `_activity_lock` calls the manifest.
- A `session_id` or `lease_id` that is not a non-empty string gives `runs: []`.

### D3. Heartbeat (`loopback.py`)

- In `_handle_session`, action `heartbeat`, a 200 answer gets `payload["human_input"] = lifecycle.human_input_block(client.session_id, payload["lease_id"])` when the state has a lifecycle.
- Error answers are unchanged.
- Both client paths pass the payload through as they receive it (main `control_client.py:740-751`, `server.py:2024-2029` and `5197-5204`).
- If building the block fails, the answer still goes out with `{"signal": "unavailable", "runs": []}`, and the failure line is logged by `Handler._log_human_input_block_failed` after `_json`. That call takes the sink as `_log` does, tries a one-slot semaphore without waiting, and starts a daemon thread named `mcp-human-input-diagnostic` only if the slot is free. If the slot is taken, or the server has no semaphore (a plain `ThreadingHTTPServer`), the line is dropped: no wait, no retry, no queue. The thread swallows a sink exception and releases the slot in `finally`; a failed `start` releases it and drops the line. The SESSION log on heartbeat still waits on the sink in the worker.

### D4. Verb results (`loopback.py`, `server.py`, `session_coordination.py`)

- **The client.** `ClientRuntime._await_result` reads a command it admitted under a lease with `POST /await`. The body is `{"identity", "lease_token"}`, with the identity of this client and the token of its admit record (`_broker_admits[command_id]`, the record `abandon_bridge` uses); the query is GET's. Every other command is read with `GET /await`, as today. A 404 on the POST, which a daemon from before this change gives, makes the client read that command with GET.
- **The route.** `POST /await` touches the client as GET does, reads the body, and answers 400 `invalid_identity` when `ClientIdentity.from_payload` raises `ValueError` or `TypeError` (a platform that is a list or an object cannot be hashed, r2 H4). It then runs GET's handler with the caller.
- **The holder check.** The handler reads the command's owner in the lock section that takes the result (`ServerState.take_result_with_owner`), which pops both with `remove=1`; `GET /await` still calls `take_result`. A done answer gets the block only when `SessionCoordinator.active_lease_id(identity, token, blocking=False)` returns the lease id the command was enqueued with. That call does not renew the lease and runs no cleanup. It gives None when another thread holds the coordinator lock, and for a lease whose effective expiry has passed (`>=`, the test `_expire_due` uses to end it, r2 H3). The block is then built for that session and lease. So another caller, a missing or wrong token, a released or expired lease and the next lease's token all give no block.
- **No owner, no block.** GET never carries the block. Neither does a command with no owner: one enqueued without a lease, or one discarded before dispatch, such as a queued command cancelled when its lease is released (`cancel_owner_pending`, then `_mark_discarded`, which keeps an error result and drops the owner). The client raises such an error result as a `ToolError` anyway.
- **Copy on the tool result.** `ClientRuntime._await_result` copies `human_input` onto the result after `prune_unfilled_fields`. So `call_bridge` returns it, and every tool that returns the bridge result as it is carries it. A tool error carries none: the client raises an `ok: 0` row before it copies the block. Over raw HTTP, a done answer to the holder carries the block whatever its row says, `ok: 0` included.
- **Composite tools.** The block travels on the row `call_bridge` returns. A tool's answer carries it when that answer is that row or a copy of it, as-is or with fields added or changed; a tool that makes several calls carries the block of the call whose row it returns. `notify_players` and `player_teleport` return the row of their last call; `entities_query` returns the row of its first call, passed through `_normalize_entities_cargo` and `_annotate_entities_reliability`, which return the same dict; `restore_gameplay` and `loadout_apply` copy the row with `dict()`. A tool that builds a new answer does not carry it: `wait_for` (`_wait_for_response`) and `ui_dialog` (which reads with GET via `probe_bridge_result`).
- **Failure line on `/await`.** Same diagnostic as D3: after `_json`, `Handler._log_human_input_block_failed`, so the worker does not wait on the sink.
- **Embedded mode.** It has no leases (`_client_runtime`, main `server.py:5058-5061`), so nothing changes there.

## Out of scope

- Refusing or pausing a verb on human input, and a `wait_for` condition for it (the ticket's other example). The counts let the caller decide; this change adds no behavior.
- Exact human contact. The counts are credited input: a step between two samples counts for the runs in front at either end (`input_activity.py:196-208`). So input typed in another window right after the client was in front can count, a focus excursion between two samples can go unseen, and a gap over 2 s between good samples credits nothing (`:184-185`).
- Two inherited limits of the sampler that the counters make visible (ER1 H8, H9), both in main and noted in the bug ledger: the sampler and the idle warden can hand samples to the attributor out of order, which can credit one input twice; and a retired run's mark is kept for the last 256 retirements only, so a sample held back longer could recreate its stamp and counter.
- Tools that build a new answer, such as `wait_for` and `ui_dialog` (D4).
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
- **T2, clock (D2).** The last good sample is at 1000 and the production clock reads 1001.99. Read once, the signal is `ok`. When the clock moves to 1002.03 as the report takes `_activity_lock`, the signal is `unavailable`, and the run keeps its row.
- **T9, snapshot (D2).** The test arms `_activity_lock` so that the reader, which already holds the manifest lock, starts a worker that retires the run (`replace` to EXITED, then `_seal_terminal`). A probe on the manifest lock records the worker's first try at it, without waiting, and the test waits for that try: it must find the lock held. The block has the run with `count` 3. After the read, the worker finishes and the block has no rows (r2 H7).
- **T9, no waits (D2, D4).** Stand-ins for the manifest lock, `_activity_lock` and the coordinator lock record how each acquire asks: the report asks each once with `blocking=False` and no timeout. With either of the first two held by another thread, the block is `{"signal": "unavailable", "runs": []}` (r2 H8).
- **T3 (D3).** Through `_handle_session`:
  - a 200 heartbeat carries T2's block;
  - a heartbeat with a wrong token keeps its error status and has no `human_input`;
  - with no lifecycle on the state, there is no key.
- **T4 (D4, daemon).**
  - For a command enqueued under L with its result stored, `GET /await?remove=0` gives `status` done and the bridge row without `human_input`. Then `POST /await` by the holder gives the row unchanged plus the block, with `count` 1. The result is stored as not discarded.
  - A command enqueued with no lease gets no `human_input`.
  - A pending command gets `{"status": "pending"}` only.
  - A POST whose identity does not parse gets 400 `invalid_identity`, and so does one whose identity is `{}` or whose platform is `[]` or `{}`.
- **T8 (D4, holder check).** For a done command of lease L, held by session A:
  - session B with no token or with A's token, and A with no token or with a wrong token, get done with no block;
  - A with L's token gets the block on two reads with `remove=0`;
  - after A releases L there is no block, and the next lease's token gives none;
  - a second HTTP test: once L's effective expiry has passed, with L still active because nothing has run `_expire_due`, A with L's token gets done with no block, and L is still the active lease.
- **T8, expiry (D4, unit).** `active_lease_id` names the lease 0.001 s before its effective expiry and names none at it, with `blocking` true or false; the lease is neither renewed nor ended.
- **T9, busy locks (D2, D4).** The test thread holds one lock while the daemon answers. HTTP calls that wait while the lock is held use a 30 s timeout; the unit holder thread releases the lock when the test sets `done`, in a `finally` after the read returns, or after 30 s. Safety joins use 30 s and then assert the thread is dead.
  - With the manifest lock or `_activity_lock` held, a heartbeat and a `POST /await` both answer 200 with `{"signal": "unavailable", "runs": []}`, and the `/await` answer is done with its bridge row.
  - With the coordinator lock held, a `POST /await?remove=0` answers done with no block. `remove=0`, because consuming a leased result waits on that lock in main too.
  - A handler that waited for the lock would not answer within the client's 30 s timeout, which fails the test as an assertion. A spy shows the handler asks the coordinator with `blocking=False`.
  - Once the locks are free, the block comes back with `count` 1.
- **T5 (D4, client).** `ClientRuntime._await_result` with a fake `_call`:
  - a command admitted under a lease is read with `POST /await` and the identity and token, and a done answer with `human_input` returns the pruned row plus the block;
  - a command with no admit record is read with GET, and an answer without the block returns no key;
  - an `ok: 0` row still raises the bridge error;
  - a 404 on the POST is followed by a GET of the same command, which returns its row.
- **T6 (D4, tools).** With a fake runtime whose `call_bridge` returns a row with the block, `query_player_state`, `object_delete` and `ui_focus` return it as it is. `entities_query` returns the block of its `entities_query` call, not of `query_all_players`. `notify_players` with a non-empty players list returns the block of its `notify_players` call. `wait_for` `players_at_least` with value 0, satisfied on the first probe, has no `human_input`.
- **T7 (exception).** If `human_input_block` raises, the heartbeat and `POST /await` still answer 200, with `{"signal": "unavailable", "runs": []}`, and one log line follows each answer. With a sink that blocks on that line, and then with one that raises on it, both answers still come, the `/await` one with its consumed row whole. A dropped connection, which is what an escaping exception gives, and a request that times out both fail the test as an assertion. The raise is swallowed by the diagnostic thread, so the test checks that the sink received the line. The stuck sink holds the line up to 60 s and the calls in front of it wait 10 s, so a line written before the answer fails the test as a timeout; after each join the thread is dead. Before it waits for the next line, the test joins the diagnostic thread that wrote the last one: the sink's event comes before that thread frees the slot.
- **T7b (exception, workers).** The same construction failure as T7. `_http_workers` is a counting `BoundedSemaphore(3)` (`CountingWorkers`) for the whole test, set before its first request. After each answer the test waits until every slot is back, since an answer can arrive before its worker ends. For heartbeat and `POST /await?remove=0` of a kept result, separately: with the sink held on the failure line, four failing requests answer 200 with the fallback block and their workers come back, then `GET /status` answers too. The sink was entered once, on the `mcp-human-input-diagnostic` thread, and still holds the line. After `release`, the sink returned because the test released it, and after joining `mcp-human-input-diagnostic` threads one more failing request writes the line again, on that thread. With a sink that raises, after joining its thread the next failing request writes the line too.
- **T7c (exception, start).** With `threading.Thread` patched so that the first `mcp-human-input-diagnostic` thread fails to start, `Handler._log_human_input_block_failed` gives the slot back (a `blocking=False` acquire gets it) and the next call writes the line.
- **T8, pin (D4, unit).** `acquire` then, at the same instant `c0`, `authorize` of a command that needs a lease with `operation_timeout_s=180`: `expires_at` is `c0 + 120` and the pending deadline is `c0 + 180`. At `c0 + 130`, `active_lease_id` names the lease with `blocking` true or false. At `c0 + 180` and `c0 + 180.5` it names none. `_active` is still that lease.

Mutants are applied one at a time on a copy. Each is killed by the test named, as an AssertionError:

- M1: uncertain input counted as verified (T1).
- M2: a counter that grows on a sample that did not update the stamp (T1).
- M3: counters kept after the run retires (T1).
- M4a, M4b: rows for runs of another lease, or of another session (T2).
- M5a, M5b: `signal` ok with a stale sample, or always (T2).
- M6: the block on a heartbeat error answer (T3).
- M7: the owner read after the result is taken, in another lock section, which leaves no block with `remove=1` (T4, T7, T9).
- M8: the client dropping the block (T5).
- M9: an exception escaping `human_input_block` (T7).
- M10: the rows in reverse order (T2).
- M11: an age for a stamp later than the clock (T1).
- M12: `GET /await` carrying the owner's block, as revision 2 did (T4, T8).
- M13: no holder check, so any caller gets the owner's block (T8, T9).
- M14, M15: a wait with no limit for the manifest lock or for `_activity_lock` (T9, no waits and busy locks); M14b, M15b: the same waits bounded at 0.05 s, as revision 3 did (T9, no waits).
- M16: the handler asking the coordinator lock to wait (T9, busy locks); M16b: `active_lease_id` waiting for it up to 0.05 s, as revision 3 did (T9, no waits); M16c: `active_lease_id` ignoring `blocking` (T9, no waits and busy locks).
- M17: the counters read after the manifest lock is released, as revision 2 did (the three T9 tests).
- M18, retired in revision 4: the failure line now follows the answer, so the guard it removed is gone.
- M19: a counter that grows on an equal epoch (T1).
- M20: `signal` unavailable at exactly 2 s (T2).
- M21: `signal` ok for a sample later than the clock (T2).
- M22: the client without the fallback to GET on a 404 (T5).
- M23: the client always on GET (T5).
- M24: the clock read before the locks are taken, as revision 3 did (T2, clock).
- M25, M26: an expired lease still naming its holder, or still naming it at exactly its effective expiry (T8).
- M27: only `ValueError` caught on `POST /await` (T4).
- M28, M29: the failure line written with `self._log` before the answer, on the heartbeat or on `/await` (T7).
- M30: the heartbeat failure line written with `self._log` on the HTTP worker (T7b, heartbeat: that worker is not back while the sink holds the line).
- M31: the `/await` failure line written with `self._log` on the HTTP worker (T7b, `/await`: the same).
- M32: no diagnostic semaphore, so one thread per line (T7b, sink entered once while held).
- M33: the diagnostic semaphore not released after the sink (T7b, the next failing request writes the line again).
- M34: the diagnostic semaphore acquired with `blocking=True` (T7b: the second failing request's worker waits for the slot and is not back).
- M35: `entities_query` dropping `human_input` from its answer (T6).
- M36: `entities_query` taking the block of `query_all_players` (T6).
- M37: `active_lease_id` comparing against `lease.expires_at` instead of `effective_expiry()` (T8, pin, at `c0 + 130`).
- M38: the line written on the HTTP worker once it has the slot (T7b: that worker is not back while the sink holds the line).
- M39: a diagnostic thread that fails to start keeping the slot (T7c).

Pass criteria (R26):

- The new module passes on Linux with 3.13 and the shim, and on Windows CI with 3.11 and 3.14, both fast and full.
- Every mutant fails its test with an AssertionError, and the copy without mutants passes.
- The whole `tools/tests` suite on Linux fails the same ids as main `9713894` (804) and adds no new failure. On Windows CI it is green.
- The diff touches only `process_lifecycle.py`, `loopback.py`, `server.py`, `session_coordination.py`, the new test module, the docs below and this plan.

## Docs

- **`session_heartbeat` description** (`server.py`): one sentence saying that the answer carries `human_input` for the lease's runs, and how to compare counts.
- **`tools/README-mcp.md`**, in the lease section: where the block appears and for whom, that the holder compares counts between two answers, that they are credited input, and what `signal: unavailable` means. Step 3 also opens with a renewal sentence, from main, that said the players_* and entity_state probes renew the lease; they are pure reads and do not, as the tool descriptions say (`_lease_renewal_contract`, `server.py`), so the sentence now says that (r2 H6).
- **CHANGELOG, Added**: the same, with the inbox id. Update PROJECT-MAP's CHANGELOG size if its rounding moves.
