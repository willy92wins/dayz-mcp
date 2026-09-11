"""Offline behavior tests; STEAMFASTPATH_SOURCE can select the saved BEFORE."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch


_source = os.environ.get("STEAMFASTPATH_SOURCE")
if _source:
    _loader = importlib.machinery.SourceFileLoader("_steamfastpath_under_test", _source)
    _spec = importlib.util.spec_from_loader(_loader.name, _loader)
    sp = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = sp
    _loader.exec_module(sp)
else:
    from dayz_mcp import steam_preflight as sp


STEAM_EXE = r"C:\Steam\steam.exe"


class MemoryProvider:
    def __init__(self):
        self.pid = 99
        self.active_user = 7
        self.pids = (41,)
        self.existing = {41}
        self.images = {41: STEAM_EXE}
        self.events = []
        self.read_count = 0
        self.on_read = None
        self.on_enumerate = None
        self.exists_error = None
        self.image_error = None

    def read_active_process(self):
        self.read_count += 1
        if self.on_read:
            self.on_read(self.read_count)
        self.events.append(("read", self.pid))
        return sp.SteamActiveProcessSnapshot(self.pid, self.active_user)

    def steam_process_pids(self):
        if self.on_enumerate:
            self.on_enumerate()
        self.events.append(("enumerate", self.pids))
        if isinstance(self.pids, Exception):
            raise self.pids
        return self.pids

    def steam_startup_complete(self, pid: int) -> bool:
        # These existing fixtures model a fully initialized Steam client.
        return True

    def process_exists(self, pid):
        self.events.append(("exists", pid))
        if self.exists_error:
            raise self.exists_error
        return pid in self.existing

    def process_image_path(self, pid):
        self.events.append(("image", pid))
        if self.image_error:
            raise self.image_error
        return self.images.get(pid)


class MemoryHost:
    def __init__(self, provider):
        self.provider = provider
        self.writes = []
        self.write_attempts = []
        self.invocations = []
        self.write_error = None
        self.write_effect = True
        self.after_write = None
        self.shutdown_error = None
        self.silent_errors = 0
        self.now = 0.0

    def write_active_process_pid(self, pid):
        self.write_attempts.append(pid)
        if self.write_error:
            raise self.write_error
        self.provider.events.append(("write", pid))
        self.writes.append(pid)
        if self.write_effect:
            self.provider.pid = pid
        if self.after_write:
            self.after_write()

    def steam_executable(self):
        return STEAM_EXE

    def invoke_steam(self, executable, extra_args):
        self.invocations.append((executable, extra_args))
        if extra_args == ("-shutdown",):
            if self.shutdown_error:
                raise self.shutdown_error
            self.provider.pids = ()
            self.provider.existing.clear()
        elif extra_args == ("-silent",):
            if self.silent_errors:
                self.silent_errors -= 1
                raise OSError("fake relaunch failure")
            self.provider.pid = 41
            self.provider.active_user = 7
            self.provider.pids = (41,)
            self.provider.existing = {41}
            self.provider.images = {41: STEAM_EXE}
            self.provider.exists_error = self.provider.image_error = None
            self.provider.on_read = self.provider.on_enumerate = None

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class SteamFastPathTests(unittest.TestCase):
    def setUp(self):
        self.provider = MemoryProvider()
        self.host = MemoryHost(self.provider)
        self.popen = self.enterContext(patch.object(
            sp.subprocess, "Popen", side_effect=AssertionError("Real launch forbidden")
        ))
        self.enterContext(patch.object(
            sp.WindowsSteamPreflightProvider, "read_active_process",
            side_effect=AssertionError("Real registry forbidden"),
        ))

    def tearDown(self):
        self.popen.assert_not_called()

    def run_repair(self):
        return sp.remediate_stale_steam_session(self.provider, self.host)

    def assert_fallback(self, result, reason, invocations=None):
        """invocations defaults to the full cycle; pass it when Steam was already down."""
        self.assertTrue(result.steam_restart_fallback)
        self.assertEqual(result.steam_pid_repair_reason, reason)
        self.assertEqual([args for _, args in self.host.invocations],
                         [("-shutdown",), ("-silent",)]
                         if invocations is None else list(invocations))

    def test_repairs_only_pid_without_restarting_and_is_idempotent(self):
        result = self.run_repair()
        self.assertEqual(self.host.writes, [41])
        self.assertEqual(self.host.invocations, [])
        self.assertIsNone(result.error_code)
        self.assertEqual(result.steam_registered_pid, 41)
        self.assertEqual(result.steam_previous_registered_pid, 99)
        self.assertEqual(result.steam_pid_repair_target_pid, 41)
        self.assertEqual(result.steam_pid_repair_reason, "applied")
        self.assertFalse(result.steam_restart_fallback)
        self.assertIsNone(result.steam_remediation_reason)
        self.assertEqual(self.provider.active_user, 7)
        write_index = self.provider.events.index(("write", 41))
        self.assertIn(("exists", 41), self.provider.events[:write_index])
        self.assertIn(("image", 41), self.provider.events[:write_index])
        again = self.run_repair()
        self.assertEqual(self.host.writes, [41])
        self.assertEqual(self.host.invocations, [])
        self.assertEqual(again.steam_pid_repair_reason, "already_correct")
        self.assertEqual(again.steam_previous_registered_pid, 41)

    def test_pid_already_correct_does_not_write_or_restart(self):
        self.provider.pid = 41
        result = self.run_repair()
        self.assertEqual(self.host.writes, [])
        self.assertEqual(self.host.invocations, [])
        self.assertIsNone(result.error_code)
        self.assertEqual(result.steam_pid_repair_reason, "already_correct")

    def test_dead_candidate_is_never_written(self):
        self.provider.existing.clear()
        result = self.run_repair()
        self.assertEqual(self.host.write_attempts, [])
        self.assert_fallback(result, "target_not_running")

    def test_exists_probe_failure_is_never_written(self):
        self.provider.exists_error = PermissionError("private probe detail")
        result = self.run_repair()
        self.assertEqual(self.host.write_attempts, [])
        self.assert_fallback(result, "target_probe_failed")
        self.assertEqual(result.steam_pid_repair_error, "PermissionError")
        self.assertNotIn("private probe detail", repr(result))

    def test_image_probe_failure_is_never_written(self):
        self.provider.image_error = OSError("private path")
        result = self.run_repair()
        self.assertEqual(self.host.write_attempts, [])
        self.assert_fallback(result, "target_probe_failed")

    def test_wrong_or_unreadable_image_is_never_written(self):
        for image in (r"C:\Steam\steamwebhelper.exe", None, b"steam.exe", 41):
            with self.subTest(image=image):
                self.provider = MemoryProvider()
                self.host = MemoryHost(self.provider)
                self.provider.images[41] = image
                result = self.run_repair()
                self.assertEqual(self.host.write_attempts, [])
                self.assert_fallback(result, "target_not_steam")

    def test_multiple_live_steam_processes_are_never_guessed(self):
        self.provider.pids = (41, 42)
        self.provider.existing.add(42)
        self.provider.images[42] = STEAM_EXE
        result = self.run_repair()
        self.assertEqual(self.host.write_attempts, [])
        self.assert_fallback(result, "multiple_steam_processes")

    def test_no_live_steam_process_falls_back(self):
        self.provider.pids = ()
        self.provider.existing.clear()
        result = self.run_repair()
        self.assertEqual(self.host.write_attempts, [])
        # No live Steam to close, so the cycle starts straight at the relaunch.
        self.assert_fallback(result, "no_steam_process", [("-silent",)])

    def test_bad_process_list_is_never_used_for_writing(self):
        for pids in (PermissionError("snapshot"), (True,), ("41",), (0,), (2**32,)):
            with self.subTest(pids=pids):
                self.provider = MemoryProvider()
                self.host = MemoryHost(self.provider)
                self.provider.pids = pids
                result = self.run_repair()
                self.assertEqual(self.host.write_attempts, [])
                self.assertTrue(result.steam_restart_fallback)
                self.assertIn(result.steam_pid_repair_reason,
                              ("process_list_unreadable", "target_pid_invalid"))

    def test_missing_active_user_does_not_write(self):
        for value in (0, None, True, "7", -1):
            with self.subTest(active_user=value):
                self.provider = MemoryProvider()
                self.host = MemoryHost(self.provider)
                self.provider.active_user = value
                result = self.run_repair()
                self.assertEqual(self.host.write_attempts, [])
                # Live steam.exe + invalid ActiveUser: abort, never -silent race.
                self.assertEqual(result.steam_pid_repair_reason, "active_user_invalid")
                self.assertFalse(result.steam_restart_fallback)
                self.assertEqual(result.steam_remediation_reason, "active_user_invalid_live_steam")
                self.assertEqual(self.host.invocations, [])
                self.assertEqual(result.error_code, sp.STEAM_SESSION_STALE)

    def test_missing_active_user_without_live_steam_still_restarts(self):
        self.provider.active_user = 0
        self.provider.pids = ()
        self.provider.existing.clear()
        result = self.run_repair()
        self.assertEqual(self.host.write_attempts, [])
        self.assert_fallback(result, "active_user_invalid", [("-silent",)])

    def test_malformed_registered_pid_is_not_repaired_as_a_dword(self):
        for pid in (True, "99", -1, None, 2**32):
            with self.subTest(pid=pid):
                self.provider = MemoryProvider()
                self.host = MemoryHost(self.provider)
                self.provider.pid = pid
                result = self.run_repair()
                self.assertEqual(self.host.write_attempts, [])
                self.assert_fallback(result, "registered_pid_invalid")

    def test_registry_unreadable_or_unstable_does_not_write(self):
        for changing in (False, True):
            with self.subTest(changing=changing):
                self.provider = MemoryProvider()
                self.host = MemoryHost(self.provider)
                def read(count):
                    if changing:
                        self.provider.pid = 99 + count
                    else:
                        raise PermissionError("registry denied")
                self.provider.on_read = read
                result = self.run_repair()
                self.assertEqual(self.host.write_attempts, [])
                self.assert_fallback(result, "snapshot_unreadable_or_unstable")
                self.assertIsNone(result.steam_previous_registered_pid)

    def test_registry_changes_before_write_does_not_get_overwritten(self):
        def read(count):
            if count == 3:
                self.provider.pid = 77
        self.provider.on_read = read
        result = self.run_repair()
        self.assertEqual(self.host.write_attempts, [])
        self.assert_fallback(result, "registry_changed")
        self.assertEqual(result.steam_previous_registered_pid, 99)

    def test_process_list_changes_before_write_does_not_get_guessed(self):
        calls = 0
        def enumerate_pids():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.provider.pids = (41, 42)
        self.provider.on_enumerate = enumerate_pids
        result = self.run_repair()
        self.assertEqual(self.host.write_attempts, [])
        self.assert_fallback(result, "process_list_changed")

    def test_failed_write_preserves_cause_and_uses_old_cycle(self):
        self.host.write_error = PermissionError("private registry detail")
        result = self.run_repair()
        self.assertEqual(self.host.write_attempts, [41])
        self.assertEqual(self.host.writes, [])
        self.assert_fallback(result, "write_failed")
        self.assertEqual(result.steam_pid_repair_error, "PermissionError")
        self.assertEqual(result.steam_previous_registered_pid, 99)
        self.assertIsNone(result.error_code)
        self.assertNotIn("private registry detail", repr(result))

    def test_write_without_effect_is_not_reported_as_success(self):
        self.host.write_effect = False
        result = self.run_repair()
        self.assertEqual(self.host.writes, [41])
        self.assert_fallback(result, "verification_failed")

    def test_target_dying_after_write_uses_fallback(self):
        self.host.after_write = self.provider.existing.clear
        result = self.run_repair()
        self.assert_fallback(result, "verification_failed")

    def test_active_user_changing_after_write_uses_fallback(self):
        self.host.after_write = lambda: setattr(self.provider, "active_user", 8)
        result = self.run_repair()
        self.assert_fallback(result, "verification_failed")

    def test_legacy_host_without_writer_still_runs_old_cycle(self):
        self.host.write_active_process_pid = None
        result = self.run_repair()
        self.assertEqual(self.host.write_attempts, [])
        self.assert_fallback(result, "writer_unavailable")
        self.assertIsNone(result.error_code)

    def test_fallback_failure_keeps_old_reason_and_new_write_diagnostic(self):
        self.host.write_error = PermissionError()
        self.host.shutdown_error = OSError()
        result = self.run_repair()
        self.assertEqual(result.error_code, sp.STEAM_SESSION_STALE)
        self.assertEqual(result.steam_remediation_reason, "shutdown_failed")
        self.assertEqual(result.steam_pid_repair_reason, "write_failed")
        self.assertTrue(result.steam_restart_fallback)
        self.assertEqual(self.host.invocations, [(STEAM_EXE, ("-shutdown",))])

    def test_failed_relaunch_retains_retry_and_steam_left_down(self):
        self.host.write_error = PermissionError()
        self.host.silent_errors = 2
        result = self.run_repair()
        self.assertTrue(result.steam_left_down)
        self.assertEqual(result.steam_remediation_reason, "relaunch_failed")
        self.assertEqual(result.steam_pid_repair_reason, "write_failed")
        self.assertEqual([args for _, args in self.host.invocations],
                         [("-shutdown",), ("-silent",), ("-silent",)])

    def test_target_is_rechecked_immediately_before_writing(self):
        for defect, reason in (("dead", "target_not_running"), ("image", "target_not_steam")):
            with self.subTest(defect=defect):
                self.provider = MemoryProvider()
                self.host = MemoryHost(self.provider)
                def read(count):
                    if count == 4:
                        if defect == "dead":
                            self.provider.existing.clear()
                        else:
                            self.provider.images[41] = r"C:\Steam\foreign.exe"
                self.provider.on_read = read
                result = self.run_repair()
                self.assertEqual(self.host.write_attempts, [])
                self.assert_fallback(result, reason)

    def test_active_user_changes_before_write_do_not_get_overwritten(self):
        def read(count):
            if count == 3:
                self.provider.active_user = 8
        self.provider.on_read = read
        result = self.run_repair()
        self.assertEqual(self.host.write_attempts, [])
        self.assert_fallback(result, "registry_changed")

    def test_zero_registered_pid_and_case_insensitive_image_are_repaired(self):
        self.provider.pid = 0
        self.provider.images[41] = r"C:\Steam\StEaM.ExE"
        result = self.run_repair()
        self.assertEqual(self.host.writes, [41])
        self.assertEqual(self.host.invocations, [])
        self.assertEqual(result.steam_previous_registered_pid, 0)
        self.assertEqual(result.steam_pid_repair_reason, "applied")
        self.assertIsNone(result.error_code)

    def test_healthy_legacy_host_retains_its_restart_only_contract(self):
        self.provider.pid = 41
        self.host.write_active_process_pid = None
        result = self.run_repair()
        self.assertEqual(self.host.write_attempts, [])
        self.assert_fallback(result, "writer_unavailable")
        self.assertIsNone(result.error_code)

    def test_windows_writer_open_error_is_offline_and_retains_fallback(self):
        registry = SimpleNamespace(HKEY_CURRENT_USER=0x80000001,
            KEY_SET_VALUE=2, REG_DWORD=4, OpenKey=Mock(side_effect=PermissionError()),
            SetValueEx=Mock())
        self.host.write_active_process_pid = sp.WindowsSteamRemediationHost().write_active_process_pid
        with patch.dict(sys.modules, {"winreg": registry}):
            result = self.run_repair()
        registry.SetValueEx.assert_not_called()
        self.assert_fallback(result, "write_failed")
        self.assertEqual(result.steam_pid_repair_error, "PermissionError")

    def test_windows_host_writes_only_pid_as_dword_through_fake_winreg(self):
        context = MagicMock()
        key = context.__enter__.return_value
        registry = SimpleNamespace(HKEY_CURRENT_USER=0x80000001,
            KEY_SET_VALUE=2, REG_DWORD=4, OpenKey=Mock(return_value=context),
            SetValueEx=Mock(side_effect=lambda *args: setattr(self.provider, "pid", args[-1])))
        with patch.dict(sys.modules, {"winreg": registry}):
            result = sp.remediate_stale_steam_session(
                self.provider, sp.WindowsSteamRemediationHost())
        self.assertIsNone(result.error_code)
        registry.OpenKey.assert_called_once_with(0x80000001,
            r"Software\Valve\Steam\ActiveProcess", 0, 2)
        registry.SetValueEx.assert_called_once_with(key, "pid", 0, 4, 41)
        context.__exit__.assert_called_once()
        self.assertEqual(self.provider.active_user, 7)

    def test_windows_writer_rejects_invalid_dword_before_opening_key(self):
        registry = SimpleNamespace(OpenKey=Mock(), SetValueEx=Mock())
        with patch.dict(sys.modules, {"winreg": registry}):
            for pid in (True, 0, -1, "41", 2**32):
                with self.subTest(pid=pid), self.assertRaises(ValueError):
                    sp.WindowsSteamRemediationHost().write_active_process_pid(pid)
        registry.OpenKey.assert_not_called()
        registry.SetValueEx.assert_not_called()


class RestartShutdownSkipTests(unittest.TestCase):
    """-shutdown against an already-down Steam starts one only to kill it."""

    def setUp(self):
        self.provider = MemoryProvider()
        self.host = MemoryHost(self.provider)
        self.popen = self.enterContext(patch.object(
            sp.subprocess, "Popen", side_effect=AssertionError("Real launch forbidden")
        ))
        self.enterContext(patch.object(
            sp.WindowsSteamPreflightProvider, "read_active_process",
            side_effect=AssertionError("Real registry forbidden"),
        ))

    def tearDown(self):
        self.popen.assert_not_called()

    def run_repair(self):
        return sp.remediate_stale_steam_session(self.provider, self.host)

    def test_no_shutdown_is_sent_when_steam_is_already_down(self):
        self.provider.pids = ()
        self.provider.existing.clear()
        result = self.run_repair()
        self.assertEqual(self.host.invocations, [(STEAM_EXE, ("-silent",))])
        self.assertIsNone(result.error_code)
        self.assertTrue(result.steam_restart_fallback)
        self.assertEqual(result.steam_pid_repair_reason, "no_steam_process")

    def test_shutdown_is_still_sent_when_a_steam_is_running(self):
        self.provider.pids = (41, 42)
        self.provider.existing = {41, 42}
        result = self.run_repair()
        self.assertEqual(
            self.host.invocations,
            [(STEAM_EXE, ("-shutdown",)), (STEAM_EXE, ("-silent",))],
        )
        self.assertIsNone(result.error_code)
        self.assertEqual(result.steam_pid_repair_reason, "multiple_steam_processes")

    def test_unreadable_process_list_still_takes_the_shutdown_cycle(self):
        def unreadable():
            raise OSError("process list unreadable")

        self.provider.on_enumerate = unreadable
        result = self.run_repair()
        self.assertEqual(self.host.invocations[0], (STEAM_EXE, ("-shutdown",)))
        self.assertEqual(result.error_code, sp.STEAM_SESSION_STALE)
        self.assertEqual(result.steam_remediation_reason, "process_list_unreadable")


if __name__ == "__main__":
    unittest.main()
