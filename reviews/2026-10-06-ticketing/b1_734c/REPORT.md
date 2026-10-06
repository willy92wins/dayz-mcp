# REPORT — batch b1_734c — supervisor answers host requests when the worker transport dies

## CHANGES

All code changes are in `tools/dayz_mcp/mcp_supervisor.py` (Python only, no PBO/reseal), plus one
line in `CHANGELOG.md` under `## [Unreleased]` / `### Fixed`.

- `_Generation` — new per-generation bookkeeping: `failed` (terminal transport failure, decided
  under the supervisor's `_state` lock), `failure_reason` (`stdout_eof` / `write_failed`),
  `detached` (request ids the failure path already answered, consulted by the pump to swallow a
  late real response), `retired` (set before a planned retirement closes stdin, so the EOF that
  follows is read as the expected end and not as a failure).
- `_fail_generation` (new) — idempotent: sets `failed` and atomically detaches every pending
  request (`inflight.clear()` + `detached`) under one `_state` hold, wakes the drain/heartbeat/
  replay waiters, then answers each detached host request exactly once. Whichever racer arrives
  first (EOF in the pump, a failed write) does the work; later callers observe the flag and
  return.
- `_worker_died_payload` / `_answer_worker_died` (new) — the terminal answer per host request:
  failed `tools/call`s get the tool-shaped `isError:true` result with payload
  `{"error": "worker_died", "generation": N, "reason": ..., "completion": "unknown"}`; every
  other request method gets a JSON-RPC error response with code `-32603` and the same data as
  `error.data` (a `tools/call` result would not be a valid answer to `initialize`/`tools/list`).
  Completion is stated as unknown and nothing retries the call automatically, because its effects
  may already have reached the daemon.
- `_send_worker` — refuses writes to an already-failed generation up front (no more bytes into a
  pipe nobody reads); a write failure (`OSError`/`ValueError`) now marks the generation failed
  (`write_failed`) instead of only logging, which detaches and answers its pending requests.
- `_pump_worker` — (1) a response whose id is in `detached` is swallowed, so a real response that
  lost the race against a transport failure cannot produce a second terminal response; (2) EOF on
  stdout outside a planned retirement is no longer merely logged: it calls `_fail_generation`
  with `stdout_eof`, answering everything still pending on that generation.
- `_retire` — flags `retired` under `_state` before closing stdin, so a clean recycle's EOF is
  not misread as a transport failure.
- `_heartbeat` — refuses a failed generation; a failed send reports `worker_died` instead of
  `send_failed`; the wait loop distinguishes "detached by a transport death" (`worker_died`) from
  a real answer (`answered`) and from `timeout`. EOF is never heartbeat success.
- `_replay_handshake` — refuses a failed generation; the wait loop treats a detached replay id as
  failure (`initialize replay lost to a dead worker transport`), never as success; the send of the
  recorded `notifications/initialized` line is now honored, so a replacement that dies during the
  replay fails the reload.
- `handle_host_line` (admission) — a request offered to a failed generation is refused and
  answered from the supervisor with the `worker_died` payload (tool-shaped or JSON-RPC error by
  method), and is never forwarded, even when the launcher still accepts writes; notifications to
  a dead generation are dropped rather than written into the dead pipe.
- `recycle` — flow unchanged, which is what makes the reload path work: on a generation whose
  transport already died, the drain completes trivially (nothing is pending any more), the
  heartbeat reports `worker_died`, the dead generation is retired, a replacement is spawned and
  the handshake is replayed into it. A live worker whose stdout stays open and whose calls cannot
  drain still gets the `drain_timeout` refusal (spec bullet retained).
- Module docstring — documents the terminal-failure contract and the no-automatic-retry rule.

## TESTS ADDED

All in `tools/tests/test_mcp_supervisor.py`, new class `WorkerDeathTest` (10 tests):

- `test_stdout_eof_answers_a_pending_call_with_worker_died` — the ticket's reproduction: a call is
  in flight when worker stdout EOFs while `poll()` is still `None` and stdin is still writable;
  the call must get `isError` + `worker_died` + generation + `completion: "unknown"`.
- `test_admission_after_eof_is_refused_not_forwarded_into_the_dead_pipe` — a request admitted
  after EOF is answered with `worker_died` and is NOT forwarded (the launcher still accepts
  writes); a notification after EOF is dropped, not fed to the dead pipe.
- `test_eof_then_reload_replaces_the_dead_generation` — the ticket's executable scenario end to
  end: call 5 in flight at EOF, call 6 admitted after EOF, then `server_reload`: both calls
  answered `worker_died`, reload answers `recycled` with generation 2 and the replacement receives
  the replayed `initialize`.
- `test_a_failed_write_answers_the_request_and_marks_the_generation_dead` — a broken stdin write
  answers the request (`reason: write_failed`) and the generation stays refused with no further
  write attempts.
- `test_a_response_racing_the_failure_never_doubles_the_terminal_answer` — a real response pushed
  just before a write failure: whichever racer wins, the host ends up with exactly one terminal
  response per request id.
- `test_a_non_tool_request_gets_a_json_rpc_error_when_the_worker_dies` — `tools/list` gets a
  JSON-RPC `-32603` error (not a `tools/call` result) with the same diagnostic data.
- `test_initialize_that_loses_its_worker_gets_a_json_rpc_error` — same for `initialize`, the
  method a tool-shaped result could never answer correctly.
- `test_a_heartbeat_to_a_dead_transport_is_reported_not_answered` — a reload whose outgoing
  worker's stdin is broken reports `lease_heartbeat: "worker_died"` (was `send_failed`) and still
  brings up the replacement.
- `test_replacement_eof_during_replay_fails_the_reload_and_refuses_later_calls` — a replacement
  whose stdout EOFs during the initialize replay fails the reload with `initialize_replay_failed`,
  and later calls to that dead replacement are answered `worker_died` (generation 2), never
  forwarded into it.
- `test_a_live_worker_drain_timeout_does_not_fail_the_generation` (slow tier) — pins the retained
  behaviour: a live worker with an open stdout that cannot drain keeps the `drain_timeout`
  refusal and keeps serving afterwards (a fresh call is admitted and relayed verbatim).

Round 2 additions (all in `WorkerDeathTest`):

- `test_a_reserved_failure_keeps_itself_off_the_host_wire` (F1) — a broken stdin write
  while the reload's heartbeat is in flight: the reload still reports
  `lease_heartbeat: "worker_died"` and recycles, and no message with a reserved id ever
  reaches the host.
- `test_a_replay_that_loses_its_transport_fails_the_reload_off_the_host_wire` (F1) — a
  replacement that EOFs while the replayed initialize is unanswered: the reload answers
  `initialize_replay_failed` and no response or `-32603` error with `REPLAY_ID` reaches
  the host.
- `test_reload_does_not_announce_success_when_the_replacement_dies_mid_replay` (F2) — a
  replacement that answers the replay but dies while the initialized notification is in
  flight (write accepted, death marked concurrently): the reload answers
  `initialize_replay_failed`, not `recycled`, and the next call gets `worker_died` for
  generation 2.

Verified against the round-1 code before fixing: the three new tests fail there
(F1 twice via the reserved-id assertion, F2 via `recycled` with `isError: false`) and
all 27 round-1 tests still pass; after the fixes all 30 pass.

Which fail on the unmodified code: 9 of the 10 — all except
`test_a_live_worker_drain_timeout_does_not_fail_the_generation`, which pins behaviour that already
existed. Verified empirically, not only by reading the old source: the original
`mcp_supervisor.py` was reconstructed (byte-identical line count and markers, no `git` used),
written to the OS temp directory, and the new test class was run against it with the project
venv — the 9 tests above fail there, e.g. `test_stdout_eof_answers_a_pending_call_with_worker_died`
ends with `AssertionError: host never saw a matching message: []`, i.e. zero host-response bytes
for a request the launcher accepted, exactly the ticket's reproduction. Against the fixed module
all 27 tests of the module pass.

## GATE OUTPUT

Command (from the workspace root, project venv):
`"C:\Users\guill\OneDrive\...\python.exe" -B gauntlet_gate.py tests.test_mcp_supervisor` — exit code 0.
The gate's complete console output is below (it is a compact summary; fewer than 8 lines exist, all pasted literally):

```
--- tests.test_mcp_supervisor: rc=0
Ran 30 tests in 0.190s

OK (skipped=3)
--- fast tier: ran=5600 baseline_ran=5587 failures=2 new=0
GAUNTLET_GATE: PASS
```

Notes: `ran=5600` is the 5587-test baseline plus the 13 tests now in the supervisor
module (10 from round 1, 3 added for the round-2 findings); the 2 fast-tier failures
are the pre-existing ones already listed in `baseline_failures.txt` (`new=0`). The
`(skipped=3)` in the named-module pass are the slow-tier tests (the 2 pre-existing
`@slow_test` ones plus `test_a_live_worker_drain_timeout_does_not_fail_the_generation`);
the gate runs the fast tier, and the slow tests were executed in a full-module run
without the fast-tier switch and pass (`Ran 30 tests in 1.711s OK`).

## DEVIATIONS

- The failure payload carries one extra diagnostic field beyond the three the specification
  names: `reason` (`stdout_eof` or `write_failed`). The specified `error`, `generation` and
  explicit `completion: "unknown"` are all present and asserted.
- The heartbeat outcome for a dead transport is the string `worker_died`; the specification says
  only "reserved requests receive internal failure outcomes". The previous string for a failed
  send was `send_failed`; nothing pinned it.
- No existing test needed weakening or re-pinning: all pins the old tests hold (verbatim relay,
  reserved-id refusal, `drain_timeout`, `server_recycling`, `killing tree` log) still hold.

## NOT VERIFIED

- The behaviour is verified against the test file's fake worker doubles, not against a real MCP
  client and a real worker process over OS pipes; the supervisor's production wiring in
  `server.py` was not executed end to end (unchanged).
- The EOF-while-draining and EOF-during-heartbeat interleavings are covered at the fake level;
  their real-pipe timing was not exercised.
- The full gate was run once, from this workspace, with the project venv (command in the brief);
  single test modules were used during iteration.


## ROUND 2 FIXES

- **F1 (P2) — failures of reserved requests were sent to the host.** `_fail_generation`
  in `tools/dayz_mcp/mcp_supervisor.py` answered every detached id, including
  `HEARTBEAT_ID` and `REPLAY_ID`, so a transport death while the supervisor's own
  heartbeat or replay was in flight produced an unsolicited host response (an
  `isError` result, or a `-32603` error, carrying a reserved id). Now
  `_fail_generation` splits the detached pending list: reserved ids keep their failure
  purely internal — they stay in `detached`, which is what the heartbeat and replay
  waiters read, so they still observe the death — and only host-request ids go to
  `_answer_worker_died`. The log line reports detached and answered counts separately,
  and the module docstring states the rule.
- **F2 (P2) — reload announced success when the replacement had already failed during
  the replay.** `_replay_handshake` resolved its outcome from `_send_worker`'s return,
  and `_send_worker` returns `True` after a `flush()` that accepted the bytes without
  noticing a concurrent death. Now, after the `notifications/initialized` send, the
  replay outcome is resolved under `_state`: if the replacement's generation is
  terminally failed at that point, the handshake fails and the reload answers
  `initialize_replay_failed` instead of a `recycled` success the very next call would
  contradict with `worker_died`.

Both findings are pinned by new tests (listed below); each failed on the round-1 code
and passes after the fix. No round-1 change or test was altered.

