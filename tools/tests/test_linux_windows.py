"""The Linux window functions behind dayz_test_close."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import unittest
from unittest import mock

POSIX = os.name != "nt"
if POSIX:
    from dayz_mcp import linux_compat

    linux_compat.install()
    from dayz_mcp import input_activity, linux_windows, window_close

_TREE = {
    "type": "root",
    "nodes": [
        {
            "type": "output",
            "nodes": [
                {
                    "type": "workspace",
                    "floating_nodes": [],
                    "nodes": [
                        {"type": "con", "id": 7, "pid": 111, "visible": True, "nodes": []},
                        {"type": "con", "id": 8, "pid": 222, "visible": False, "nodes": []},
                    ],
                }
            ],
        }
    ],
}


@unittest.skipUnless(POSIX, "the Linux window functions")
class LinuxWindowsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.commands: list[list[str]] = []

        def run(command, **_kwargs):
            self.commands.append(command)
            stdout = json.dumps(_TREE) if "get_tree" in command else ""
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        for patcher in (
            mock.patch.object(linux_windows.subprocess, "run", run),
            mock.patch.object(linux_windows, "_sway_pids", return_value=[50]),
            mock.patch.object(linux_windows, "_server_pids", return_value=[900]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api = linux_windows.fns()

    def test_only_the_asked_pids_are_listed(self) -> None:
        listed = window_close.list_visible_top_level_windows((111, 900), fns=self.api)
        self.assertEqual(sorted(pid for _handle, pid in listed), [111, 900])

    def test_an_invisible_view_and_another_pid_are_not_listed(self) -> None:
        self.assertEqual(window_close.list_visible_top_level_windows((222,), fns=self.api), [])
        self.assertEqual(window_close.list_visible_top_level_windows((333,), fns=self.api), [])

    def test_closing_a_client_view_kills_exactly_that_con_in_its_session(self) -> None:
        [(handle, _pid)] = window_close.list_visible_top_level_windows((111,), fns=self.api)
        self.assertTrue(window_close.post_wm_close(handle, fns=self.api, expected_pid=111))
        command = self.commands[-1]
        self.assertEqual(command[-2:], ["[con_id=7]", "kill"])
        self.assertTrue(command[2].endswith(f"sway-ipc.{os.getuid()}.50.sock"))

    def test_a_view_now_owned_by_another_pid_is_not_closed(self) -> None:
        [(handle, _pid)] = window_close.list_visible_top_level_windows((111,), fns=self.api)
        self.assertFalse(window_close.post_wm_close(handle, fns=self.api, expected_pid=222))
        self.assertNotIn("kill", self.commands[-1])

    def test_closing_a_server_sends_sigterm_never_sigkill(self) -> None:
        [(handle, _pid)] = window_close.list_visible_top_level_windows((900,), fns=self.api)
        with mock.patch.object(linux_windows.os, "kill") as kill:
            self.assertTrue(window_close.post_wm_close(handle, fns=self.api, expected_pid=900))
        kill.assert_called_once_with(900, signal.SIGTERM)

    def test_any_other_message_is_refused(self) -> None:
        with mock.patch.object(linux_windows.os, "kill") as kill:
            self.assertFalse(self.api.post_message(1 << 62 | 900, 0x0012, 0, 0))
        kill.assert_not_called()

    def test_a_headless_host_reads_as_no_input_not_as_a_doubt(self) -> None:
        sample = input_activity.take_sample(linux_windows.input_fns(), 100.0)
        self.assertTrue(sample.ok)
        self.assertIsNone(sample.foreground_pid)

    def test_install_puts_these_functions_in_place(self) -> None:
        self.addCleanup(setattr, window_close, "_REAL", window_close._REAL)
        self.addCleanup(setattr, input_activity, "real_win32", input_activity.real_win32)
        linux_windows.install()
        self.assertIs(window_close.real_win32().enum_windows, linux_windows.enum_windows)
        self.assertIs(input_activity.real_win32, linux_windows.input_fns)


if __name__ == "__main__":
    unittest.main()
