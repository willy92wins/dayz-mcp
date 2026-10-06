# REPORT — batch b6_bb6d (request-writer ownership test determinism)

## CHANGES

All changes are in `tools/tests/test_native_launcher_backend.py`; production code and the PBO
are untouched.

- `tools/tests/test_native_launcher_backend.py:_SynchronousRequestWriter` (new module-level
  double, placed with the other test doubles): a request-writer double that reports completion
  synchronously. `done` is always True, `error` is None, `join` returns True without waiting,
  `cancel` is counted. It exists because `finish_request_writer` (production
  `native_launcher_backend.py:1350-1354`) joins the real `_PublicRequestWriter` thread twice
  with `_DEBUG_DRAIN_SECONDS` as the timeout; the ownership test sets that constant to 0.0, so
  both joins return before the thread (started at `:978-985`) has necessarily been scheduled,
  and the run fails with `native_launcher_request_writer_stuck`.

- `tools/tests/test_native_launcher_backend.py:NativeDebugOwnershipTests.test_fb_c9ca_missing_zero_empty_handles_returns_root_exit`
  (modified, the ownership test from the ticket): injects a recording subclass of
  `_SynchronousRequestWriter` for the duration of the `backend._supervise_created_launcher`
  call and restores `backend._PublicRequestWriter`, `backend._DEBUG_DRAIN_SECONDS` and
  `backend._kernel32` in `finally`. Keeps `_DEBUG_DRAIN_SECONDS = 0.0` and the exact
  CREATE/EXIT missing-zero event sequence, per the specification (no widening of the shared
  drain constant). The test now also verifies handle cleanup explicitly: job handle 501 is
  closed, debug file handles 803 and 813 are closed, no `active_zero` completion event was
  posted, and the writer received the canonical request `b"{}"`.

- `tools/tests/test_native_launcher_backend.py:NativeDebugOwnershipTests.test_zero_drain_joins_report_stuck_when_writer_thread_lags`
  (new regression test): the executable failure scenario of specification item 3, made
  deterministic. It runs the same CREATE/EXIT event sequence with `_DEBUG_DRAIN_SECONDS = 0.0`
  and patches in a subclass of the REAL `_PublicRequestWriter` whose `_run` parks on a
  `threading.Event` gate until the test releases it. The writer thread therefore cannot
  complete before both zero-time joins, and the test asserts the supervise raises
  `native_launcher_request_writer_stuck` instead of returning root exit 0. The gate is
  released and the writer thread joined in `finally`, so the parked daemon thread never leaks
  and the test never blocks.

## TESTS ADDED

- `test_zero_drain_joins_report_stuck_when_writer_thread_lags` (new): pins the race mechanism
  with a real, gated writer thread. Deterministic: the gate is released only after both joins
  have already run, so the outcome cannot depend on timing.
- `test_fb_c9ca_missing_zero_empty_handles_returns_root_exit` (modified): same scenario as
  before, now with the synchronous writer double.

Which one fails on the unmodified code: the ownership test
`test_fb_c9ca_missing_zero_empty_handles_returns_root_exit` — on the unmodified test code it
starts the real `_PublicRequestWriter` thread while `_DEBUG_DRAIN_SECONDS` is 0.0, and the two
zero-time joins in `finish_request_writer` can both return before that thread completes
(a thread scheduling race, lost under load). The run then fails with
`native_launcher_request_writer_stuck` instead of returning 0. The new
`test_zero_drain_joins_report_stuck_when_writer_thread_lags` reproduces exactly that failure
mode deterministically by holding the real writer thread back past both joins. Because the fix
is test-only, no added test fails against the unchanged production code; production behavior
is unchanged and the stuck outcome is pinned by the new test. The dedicated real-writer
cancellation/timeout scenarios (`test_started_cancellation_cancels_blocked_request_write_without_stalling_debug`,
`test_request_write_deadline_cancels_io_and_fails_closed`) are untouched and still exercise
the real writer.

## GATE OUTPUT
(pending — filled after the gate run)

## DEVIATIONS

- None against the implementer spec. Of the two allowed options the synchronous writer double
  was used for the ownership test; explicit completion coordination (a gated real writer) is
  used by the new regression test instead, where the delay is the point.

## NOT VERIFIED

- The historical flake was not reproduced in a live loaded run (specification item 3 notes the
  same); the mechanism is confirmed by source reading and pinned deterministically by
  `test_zero_drain_joins_report_stuck_when_writer_thread_lags`.
- The fixed ownership test cannot prove the absence of the race on the unmodified code by
  passing once; the proof is structural (no writer thread exists in that test anymore).
