"""Fifth round of review fixes for the launch focus guard (inbox 5c8e, review of d097706).

Oracle frozen before the fix round. A window that vanishes during its show
request must not cut short the restore of another window of the same
give-back: only windows still owned by the pid recorded at their minimize
count. No test touches the desktop or starts a thread.
"""
from __future__ import annotations

import unittest

from dayz_mcp.launch_focus import FocusFns, FocusGuard
from tests.launch_focus_helpers import (
    CLIENT,
    CLIENT_PID,
    SERVER,
    SERVER_PID,
    FakeClock,
    FakeDesktop,
)


class VanishedWindowTest(unittest.TestCase):
    def test_a_window_that_vanishes_during_its_show_does_not_cut_short_another_restore(
        self,
    ) -> None:
        desk = FakeDesktop()
        clock = FakeClock()
        desk.restores = False  # each restore lands later, below
        due: dict[int, float] = {}

        def on_show(hwnd: int) -> None:
            if hwnd == CLIENT:
                del desk.pids[CLIENT]  # the client exits during its show request
                desk.show_answers.insert(0, False)
            else:
                due[hwnd] = clock.now + 0.5  # the server's restore completes 0.5 s later

        desk.on_show = on_show
        fns = desk.fns()

        def is_iconic(hwnd: int) -> bool:
            for window, at in list(due.items()):
                if clock.now >= at:
                    desk.minimized.discard(window)
                    del due[window]
            return fns.is_iconic(hwnd)

        guard = FocusGuard(
            fns=FocusFns(
                foreground=fns.foreground,
                window_pid=fns.window_pid,
                is_window=fns.is_window,
                is_iconic=is_iconic,
                minimize=fns.minimize,
                show_no_activate=fns.show_no_activate,
                idle_s=fns.idle_s,
                mouse_down=fns.mouse_down,
                switch_keys_down=fns.switch_keys_down,
                cursor_window=fns.cursor_window,
            ),
            clock=clock,
            sleep=clock.sleep,
            settle_s=1.0,
        )
        guard.add_pids([CLIENT_PID, SERVER_PID])
        clock.now += 0.025
        guard.step()  # the person's window is in front: it is the one to give back to
        desk.steal(SERVER)
        desk.steal(CLIENT)  # Z order: CLIENT, SERVER, the person's window, ...
        start = clock.now + 0.025
        clock.now += 0.025
        guard.step()
        self.assertEqual(desk.minimizes(), [CLIENT, SERVER])
        # The give-back waited for the server's restore instead of judging it early.
        self.assertGreaterEqual(clock.now - start, 0.5 - 1e-6)
        self.assertNotIn(SERVER, desk.minimized)
        summary = guard.summary()
        self.assertEqual(summary["restore_failed"], 0)
        self.assertTrue(guard.armed)
        self.assertTrue(guard.wait_idle(0))
        desk.steal(SERVER)  # a later activation is still handled
        clock.now += 0.025
        guard.step()
        self.assertEqual(desk.minimizes(), [CLIENT, SERVER, SERVER])


if __name__ == "__main__":
    unittest.main()
