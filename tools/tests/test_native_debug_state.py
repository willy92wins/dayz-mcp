from __future__ import annotations

import importlib
import unittest


class NativeDebugStateTests(unittest.TestCase):
    @staticmethod
    def _module():
        return importlib.import_module("dayz_mcp.native_debug_state")

    @staticmethod
    def _complete(state, decision, *, current_thread_id: int = 77) -> None:
        if decision.close_handles:
            state.acknowledge_closed_handles(decision.close_handles)
        state.complete_continue(current_thread_id=current_thread_id, succeeded=True)

    def test_happy_event_matrix_retires_every_owned_handle(self) -> None:
        debug = self._module()
        state = debug.NativeDebugState(creator_thread_id=77)

        create = state.begin_event(
            debug.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=10,
                tid=11,
                file_handle=101,
                process_handle=201,
                thread_handle=301,
                image_approved=True,
            ),
            current_thread_id=77,
        )
        self.assertEqual(create.continue_status, debug.DBG_CONTINUE)
        self.assertEqual(create.close_handles, (101,))
        self.assertFalse(create.close_job_first)
        self._complete(state, create)

        breakpoint = state.begin_event(
            debug.NativeDebugEvent(
                "EXCEPTION",
                pid=10,
                tid=11,
                exception_code=debug.EXCEPTION_BREAKPOINT,
                first_chance=True,
            ),
            current_thread_id=77,
        )
        self.assertEqual(breakpoint.continue_status, debug.DBG_CONTINUE)
        self._complete(state, breakpoint)

        repeated_breakpoint = state.begin_event(
            debug.NativeDebugEvent(
                "EXCEPTION",
                pid=10,
                tid=11,
                exception_code=debug.EXCEPTION_BREAKPOINT,
                first_chance=True,
            ),
            current_thread_id=77,
        )
        self.assertEqual(
            repeated_breakpoint.continue_status,
            debug.DBG_EXCEPTION_NOT_HANDLED,
        )
        self._complete(state, repeated_breakpoint)

        load = state.begin_event(
            debug.NativeDebugEvent(
                "LOAD_DLL", pid=10, tid=11, file_handle=102, image_approved=True
            ),
            current_thread_id=77,
        )
        self.assertEqual(load.close_handles, (102,))
        self._complete(state, load)

        create_thread = state.begin_event(
            debug.NativeDebugEvent(
                "CREATE_THREAD", pid=10, tid=12, thread_handle=302
            ),
            current_thread_id=77,
        )
        self._complete(state, create_thread)
        self.assertEqual(state.open_handle_count, 3)

        for kind in ("OUTPUT_DEBUG_STRING", "UNLOAD_DLL"):
            decision = state.begin_event(
                debug.NativeDebugEvent(kind, pid=10, tid=11),
                current_thread_id=77,
            )
            self._complete(state, decision)

        exit_thread = state.begin_event(
            debug.NativeDebugEvent("EXIT_THREAD", pid=10, tid=12),
            current_thread_id=77,
        )
        self._complete(state, exit_thread)
        self.assertEqual(state.open_handle_count, 2)

        exit_process = state.begin_event(
            debug.NativeDebugEvent("EXIT_PROCESS", pid=10, tid=11),
            current_thread_id=77,
        )
        self._complete(state, exit_process)
        self.assertEqual(state.open_handle_count, 0)
        self.assertTrue(state.active_zero)
        self.assertFalse(state.failed)

    def test_exactly_one_continue_and_creator_thread_are_enforced(self) -> None:
        debug = self._module()
        state = debug.NativeDebugState(creator_thread_id=77)
        event = debug.NativeDebugEvent(
            "CREATE_PROCESS",
            pid=10,
            tid=11,
            process_handle=201,
            thread_handle=301,
            image_approved=True,
        )
        with self.assertRaisesRegex(debug.NativeDebugStateError, "debug_thread_mismatch"):
            state.begin_event(event, current_thread_id=78)

        decision = state.begin_event(event, current_thread_id=77)
        with self.assertRaisesRegex(debug.NativeDebugStateError, "debug_event_pending"):
            state.begin_event(event, current_thread_id=77)
        self._complete(state, decision)
        with self.assertRaisesRegex(debug.NativeDebugStateError, "debug_event_not_pending"):
            state.complete_continue(current_thread_id=77, succeeded=True)

    def test_manual_handles_must_close_before_continue(self) -> None:
        debug = self._module()
        state = debug.NativeDebugState(creator_thread_id=77)
        decision = state.begin_event(
            debug.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=10,
                tid=11,
                file_handle=101,
                process_handle=201,
                thread_handle=301,
                image_approved=True,
            ),
            current_thread_id=77,
        )
        with self.assertRaisesRegex(debug.NativeDebugStateError, "debug_handle_not_closed"):
            state.complete_continue(current_thread_id=77, succeeded=True)
        state.acknowledge_closed_handles(decision.close_handles)
        state.complete_continue(current_thread_id=77, succeeded=True)

    def test_exception_rip_and_unapproved_image_fail_closed_then_drain(self) -> None:
        debug = self._module()
        cases = (
            debug.NativeDebugEvent(
                "EXCEPTION",
                pid=10,
                tid=11,
                exception_code=0xC0000005,
                first_chance=False,
            ),
            debug.NativeDebugEvent("RIP_EVENT", pid=10, tid=11),
            debug.NativeDebugEvent(
                "LOAD_DLL", pid=10, tid=11, file_handle=102, image_approved=False
            ),
        )
        for failing_event in cases:
            with self.subTest(kind=failing_event.kind):
                state = debug.NativeDebugState(creator_thread_id=77)
                create = state.begin_event(
                    debug.NativeDebugEvent(
                        "CREATE_PROCESS",
                        pid=10,
                        tid=11,
                        process_handle=201,
                        thread_handle=301,
                        image_approved=True,
                    ),
                    current_thread_id=77,
                )
                self._complete(state, create)

                decision = state.begin_event(failing_event, current_thread_id=77)
                self.assertTrue(decision.close_job_first)
                self.assertIsNotNone(decision.failure_code)
                self._complete(state, decision)
                self.assertTrue(state.failed)

                exit_process = state.begin_event(
                    debug.NativeDebugEvent("EXIT_PROCESS", pid=10, tid=11),
                    current_thread_id=77,
                )
                self.assertFalse(exit_process.close_job_first)
                self._complete(state, exit_process)
                self.assertEqual(state.open_handle_count, 0)

    def test_first_chance_non_breakpoint_is_not_handled_without_failing(self) -> None:
        debug = self._module()
        state = debug.NativeDebugState(creator_thread_id=77)
        create = state.begin_event(
            debug.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=10,
                tid=11,
                process_handle=201,
                thread_handle=301,
                image_approved=True,
            ),
            current_thread_id=77,
        )
        self._complete(state, create)
        decision = state.begin_event(
            debug.NativeDebugEvent(
                "EXCEPTION",
                pid=10,
                tid=11,
                exception_code=0xC0000005,
                first_chance=True,
            ),
            current_thread_id=77,
        )
        self.assertEqual(decision.continue_status, debug.DBG_EXCEPTION_NOT_HANDLED)
        self.assertFalse(decision.close_job_first)
        self._complete(state, decision)
        self.assertFalse(state.failed)

    def test_malformed_duplicate_or_unknown_event_fails_closed(self) -> None:
        debug = self._module()
        malformed = (
            debug.NativeDebugEvent("UNKNOWN", pid=1, tid=2),
            debug.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=0,
                tid=2,
                process_handle=3,
                thread_handle=4,
                image_approved=True,
            ),
            debug.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=1,
                tid=2,
                process_handle=3,
                thread_handle=3,
                image_approved=True,
            ),
        )
        for event in malformed:
            with self.subTest(event=event):
                state = debug.NativeDebugState(creator_thread_id=77)
                decision = state.begin_event(event, current_thread_id=77)
                self.assertTrue(decision.close_job_first)
                self.assertEqual(decision.failure_code, "malformed_debug_event")
                self._complete(state, decision)


if __name__ == "__main__":
    unittest.main()
