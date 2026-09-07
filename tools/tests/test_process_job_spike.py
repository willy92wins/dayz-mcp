from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import importlib.util
import io
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "_session_coordination"
    / "process-job-spike.py"
)


def _load_module():
    if not MODULE_PATH.is_file():
        return None
    spec = importlib.util.spec_from_file_location("process_job_spike", MODULE_PATH)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


spike = _load_module()


class ProcessJobSpikePureHelpersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertIsNotNone(spike, "process-job-spike.py must exist")

    def test_close_targets_are_exact_current_job_member_intersection(self) -> None:
        self.assertEqual(
            spike.select_close_target_pids(
                window_owner_pids=[44, 11, 44, 99, 0],
                current_job_member_pids=[11, 44, 77],
            ),
            (11, 44),
        )
        self.assertEqual(
            spike.select_close_target_pids([44, 99], [11, 77]),
            (),
        )

    def test_identity_output_hashes_path_and_keeps_filetime(self) -> None:
        raw_path = r"C:\Private\Steam\DayZ\DayZ_x64.exe"
        redacted = spike.redact_identity(
            pid=1234,
            creation_filetime=133_999_111,
            image_path=raw_path,
            query_errors={},
        )
        self.assertEqual(redacted["pid"], 1234)
        self.assertEqual(redacted["creation_filetime"], 133_999_111)
        self.assertEqual(redacted["image_name"], "DayZ_x64.exe")
        self.assertEqual(
            redacted["image_path_sha256"],
            hashlib.sha256(raw_path.casefold().encode("utf-8")).hexdigest(),
        )
        self.assertNotIn(raw_path, repr(redacted))

    def test_incomplete_identity_exposes_only_structured_error_codes(self) -> None:
        redacted = spike.redact_identity(
            pid=42,
            creation_filetime=None,
            image_path=None,
            query_errors={"open_process": 5, "private_detail": r"C:\secret"},
        )
        self.assertEqual(
            redacted,
            {
                "pid": 42,
                "creation_filetime": None,
                "image_name": None,
                "image_path_sha256": None,
                "basic_identity_complete": False,
                "query_error_codes": {"open_process": 5},
            },
        )

    def test_non_negative_seconds_rejects_nan_infinity_and_negative(self) -> None:
        self.assertEqual(spike.non_negative_seconds("0"), 0.0)
        self.assertEqual(spike.non_negative_seconds("1.25"), 1.25)
        for raw in ("-0.01", "nan", "inf", "-inf"):
            with self.subTest(raw=raw):
                with self.assertRaises(Exception):
                    spike.non_negative_seconds(raw)

    def test_launch_flags_include_extended_startup_but_never_breakaway(self) -> None:
        self.assertEqual(
            spike.CREATE_FLAGS,
            spike.CREATE_SUSPENDED
            | spike.CREATE_UNICODE_ENVIRONMENT
            | spike.EXTENDED_STARTUPINFO_PRESENT,
        )
        self.assertEqual(spike.CREATE_FLAGS & spike.CREATE_BREAKAWAY_FROM_JOB, 0)
        self.assertTrue(math.isfinite(spike.DEFAULT_OBSERVE_SECONDS))

    def test_parent_job_preflight_refuses_nested_launch_shape(self) -> None:
        self.assertEqual(
            spike.parent_job_preflight(True),
            {"allowed": False, "reason": "parent_already_in_job"},
        )
        self.assertEqual(
            spike.parent_job_preflight(False),
            {"allowed": True, "reason": None},
        )

    def test_any_error_after_process_creation_requires_manual_cleanup(self) -> None:
        helper = getattr(spike, "manual_cleanup_after_error", lambda *_args: False)
        try:
            before_launch = helper(False, False)
            still_active = helper(True, False)
            confirmed_empty = helper(True, True)
        except TypeError:
            before_launch = still_active = confirmed_empty = False
        self.assertFalse(before_launch)
        self.assertTrue(still_active)
        self.assertFalse(confirmed_empty)

    def test_observation_signals_keep_named_opaque_containment_and_basic_identity_separate(self) -> None:
        helper = getattr(spike, "classify_observed_members", None)
        self.assertIsNotNone(helper)
        named = helper(
            10,
            [
                {
                    "pid": 10,
                    "image_name": "DayZ_BE.exe",
                    "basic_identity_complete": True,
                },
                {
                    "pid": 11,
                    "image_name": "DayZ_x64.exe",
                    "basic_identity_complete": True,
                },
            ],
        )
        self.assertEqual(
            named,
            {
                "named_child_seen": True,
                "opaque_descendant_seen": False,
                "basic_identity_complete": True,
            },
        )
        opaque = helper(
            10,
            [
                {
                    "pid": 10,
                    "image_name": "DayZ_BE.exe",
                    "basic_identity_complete": True,
                },
                {
                    "pid": 12,
                    "image_name": None,
                    "basic_identity_complete": False,
                },
            ],
        )
        self.assertEqual(
            opaque,
            {
                "named_child_seen": False,
                "opaque_descendant_seen": True,
                "basic_identity_complete": False,
            },
        )

    def test_exception_and_keyboard_interrupt_are_structured_but_system_exit_is_not_captured(self) -> None:
        helper = getattr(spike, "apply_caught_failure", None)
        self.assertIsNotNone(helper)
        result = {"status": "ERROR", "manual_cleanup_required": False}
        runtime_result, runtime_code = helper(
            dict(result), RuntimeError(r"private C:\path"), True, False
        )
        self.assertEqual(runtime_code, 5)
        self.assertEqual(runtime_result["status"], "UNEXPECTED_ERROR")
        self.assertEqual(runtime_result["error"], {"type": "RuntimeError"})
        self.assertTrue(runtime_result["manual_cleanup_required"])
        self.assertNotIn("private", json.dumps(runtime_result))

        interrupted_result, interrupted_code = helper(
            dict(result), KeyboardInterrupt(), True, False
        )
        self.assertEqual(interrupted_code, 130)
        self.assertEqual(interrupted_result["status"], "INTERRUPTED")
        self.assertTrue(interrupted_result["manual_cleanup_required"])

        with self.assertRaises(SystemExit):
            helper(dict(result), SystemExit(7), True, False)

    def test_atomic_json_writer_matches_stdout_and_leaves_no_temp_file(self) -> None:
        writer = getattr(spike, "emit_result_json", None)
        self.assertIsNotNone(writer)
        result = {"z": 1, "status": "PASS", "nested": {"a": True}}
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "result.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                serialized = writer(result, output_path)
            self.assertEqual(output_path.read_text(encoding="utf-8"), serialized)
            self.assertEqual(stdout.getvalue().strip(), serialized)
            self.assertEqual(list(Path(temp_dir).glob("*.tmp")), [])

    def test_process_context_reports_current_pid_session_and_parent_job(self) -> None:
        helper = getattr(spike, "current_process_context", None)
        self.assertIsNotNone(helper)

        class Kernel:
            def GetCurrentProcessId(self):
                return 321

            def ProcessIdToSessionId(self, process_id, session_ptr):
                self.process_id = process_id
                session_ptr._obj.value = 9
                return True

            def GetCurrentProcess(self):
                return 999

            def IsProcessInJob(self, _process, _job, in_job_ptr):
                in_job_ptr._obj.value = 1
                return True

        kernel = Kernel()
        context = helper(SimpleNamespace(kernel32=kernel))
        self.assertEqual(
            context,
            {
                "current_process_id": 321,
                "current_session_id": 9,
                "parent_in_any_job": True,
            },
        )
        self.assertEqual(kernel.process_id, 321)

    def test_preflight_only_never_creates_job_or_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "DayZ_BE.exe"
            executable.write_bytes(b"")
            args = argparse.Namespace(
                dayz_be=str(executable),
                observe_seconds=0.0,
                close_timeout=0.0,
                preflight_only=True,
            )
            with (
                mock.patch.object(spike, "WinApi", return_value=object()),
                mock.patch.object(
                    spike,
                    "current_process_context",
                    return_value={
                        "current_process_id": 321,
                        "current_session_id": 9,
                        "parent_in_any_job": False,
                    },
                ),
                mock.patch.object(
                    spike,
                    "create_named_job",
                    side_effect=AssertionError("must not create job"),
                ) as create_job,
            ):
                result, exit_code = spike.run_spike(args)
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["status"], "PREFLIGHT_OK")
            self.assertEqual(result["preflight"]["current_process_id"], 321)
            self.assertEqual(result["preflight"]["current_session_id"], 9)
            create_job.assert_not_called()

    def test_run_spike_structures_runtime_interrupts_after_atomic_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "DayZ_BE.exe"
            executable.write_bytes(b"")
            args = argparse.Namespace(
                dayz_be=str(executable),
                observe_seconds=0.0,
                close_timeout=0.0,
                preflight_only=False,
            )
            process = spike.PROCESS_INFORMATION()
            process.hProcess = 101
            process.hThread = 102
            process.dwProcessId = 444

            def run_with_failure(failure):
                with (
                    mock.patch.object(spike, "WinApi", return_value=object()),
                    mock.patch.object(
                        spike,
                        "current_process_context",
                        return_value={
                            "current_process_id": 321,
                            "current_session_id": 9,
                            "parent_in_any_job": False,
                        },
                    ),
                    mock.patch.object(spike, "create_named_job", return_value=77),
                    mock.patch.object(spike, "query_job_limit_flags", return_value=0),
                    mock.patch.object(
                        spike, "create_suspended_process", return_value=process
                    ),
                    mock.patch.object(
                        spike, "verify_membership_then_resume", return_value=True
                    ),
                    mock.patch.object(spike, "observe_members", side_effect=failure),
                    mock.patch.object(spike, "_close_handle"),
                ):
                    return spike.run_spike(args)

            result, exit_code = run_with_failure(RuntimeError(r"private C:\path"))
            self.assertEqual(exit_code, 5)
            self.assertEqual(result["status"], "UNEXPECTED_ERROR")
            self.assertEqual(result["error"], {"type": "RuntimeError"})
            self.assertTrue(result["manual_cleanup_required"])

            result, exit_code = run_with_failure(KeyboardInterrupt())
            self.assertEqual(exit_code, 130)
            self.assertEqual(result["status"], "INTERRUPTED")
            self.assertTrue(result["manual_cleanup_required"])

            with self.assertRaises(SystemExit):
                run_with_failure(SystemExit(7))

    def test_atomic_job_attribute_is_installed_before_create_process(self) -> None:
        class Kernel:
            def __init__(self, create_success=True):
                self.create_success = create_success
                self.init_calls = 0
                self.update_calls = []
                self.delete_calls = 0
                self.create_flags = None
                self.startup_cb = None

            def InitializeProcThreadAttributeList(self, buffer, count, flags, size_ptr):
                self.init_calls += 1
                self.asserted = (count, flags)
                if buffer is None:
                    size_ptr._obj.value = 128
                    return False
                return True

            def UpdateProcThreadAttribute(
                self, buffer, flags, attribute, value, value_size, previous, returned
            ):
                self.update_calls.append((flags, attribute, value_size, previous, returned))
                return True

            def CreateProcessW(
                self,
                application,
                command_line,
                process_attributes,
                thread_attributes,
                inherit_handles,
                creation_flags,
                environment,
                cwd,
                startup_ptr,
                process_ptr,
            ):
                self.create_flags = creation_flags
                self.startup_cb = startup_ptr._obj.StartupInfo.cb
                if not self.create_success:
                    return False
                process_ptr._obj.hProcess = 101
                process_ptr._obj.hThread = 102
                process_ptr._obj.dwProcessId = 444
                process_ptr._obj.dwThreadId = 445
                return True

            def DeleteProcThreadAttributeList(self, _buffer):
                self.delete_calls += 1

        kernel = Kernel()
        process_target = spike.PROCESS_INFORMATION()
        try:
            process = spike.create_suspended_process(
                SimpleNamespace(kernel32=kernel),
                Path(r"C:\DayZ\DayZ_BE.exe"),
                77,
                process_target,
            )
        except TypeError as exc:
            self.fail(f"atomic create signature unavailable: {exc}")
        self.assertEqual(process.dwProcessId, 444)
        self.assertEqual(kernel.init_calls, 2)
        self.assertEqual(kernel.asserted, (1, 0))
        self.assertEqual(
            kernel.update_calls,
            [(0, spike.PROC_THREAD_ATTRIBUTE_JOB_LIST, ctypes.sizeof(ctypes.c_void_p), None, None)],
        )
        self.assertEqual(kernel.create_flags, spike.CREATE_FLAGS)
        self.assertEqual(kernel.startup_cb, ctypes.sizeof(spike.STARTUPINFOEXW))
        self.assertEqual(kernel.delete_calls, 1)

        failing_kernel = Kernel(create_success=False)
        with self.assertRaises(spike.WinApiFailure):
            spike.create_suspended_process(
                SimpleNamespace(kernel32=failing_kernel),
                Path(r"C:\DayZ\DayZ_BE.exe"),
                77,
                spike.PROCESS_INFORMATION(),
            )
        self.assertEqual(failing_kernel.delete_calls, 1)

        class InterruptAfterNativeCreateKernel(Kernel):
            def CreateProcessW(self, *args):
                process_ptr = args[-1]
                process_ptr._obj.hProcess = 201
                process_ptr._obj.hThread = 202
                process_ptr._obj.dwProcessId = 544
                process_ptr._obj.dwThreadId = 545
                raise KeyboardInterrupt()

        interrupted_kernel = InterruptAfterNativeCreateKernel()
        interrupted_target = spike.PROCESS_INFORMATION()
        with self.assertRaises(KeyboardInterrupt):
            try:
                spike.create_suspended_process(
                    SimpleNamespace(kernel32=interrupted_kernel),
                    Path(r"C:\DayZ\DayZ_BE.exe"),
                    77,
                    interrupted_target,
                )
            except TypeError as exc:
                self.fail(f"caller-owned process information unavailable: {exc}")
        self.assertEqual(interrupted_target.hProcess, 201)
        self.assertEqual(interrupted_target.hThread, 202)
        self.assertEqual(interrupted_target.dwProcessId, 544)
        self.assertEqual(interrupted_kernel.delete_calls, 1)

    def test_run_spike_recovers_caller_owned_process_info_after_create_interrupt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "DayZ_BE.exe"
            executable.write_bytes(b"")
            args = argparse.Namespace(
                dayz_be=str(executable),
                observe_seconds=0.0,
                close_timeout=0.0,
                preflight_only=False,
            )

            def interrupt_after_create(_api, _executable, _job, process_target):
                process_target.hProcess = 301
                process_target.hThread = 302
                process_target.dwProcessId = 644
                process_target.dwThreadId = 645
                raise KeyboardInterrupt()

            with (
                mock.patch.object(spike, "WinApi", return_value=object()),
                mock.patch.object(
                    spike,
                    "current_process_context",
                    return_value={
                        "current_process_id": 321,
                        "current_session_id": 9,
                        "parent_in_any_job": False,
                    },
                ),
                mock.patch.object(spike, "create_named_job", return_value=77),
                mock.patch.object(spike, "query_job_limit_flags", return_value=0),
                mock.patch.object(
                    spike,
                    "create_suspended_process",
                    side_effect=interrupt_after_create,
                ),
                mock.patch.object(spike, "_close_handle") as close_handle,
            ):
                result, exit_code = spike.run_spike(args)

            self.assertEqual(exit_code, 130)
            self.assertEqual(result["status"], "INTERRUPTED")
            self.assertTrue(result["manual_cleanup_required"])
            self.assertEqual(result["launch"]["wrapper_pid"], 644)
            self.assertTrue(result["launch"]["created_suspended"])
            closed = [call.args[1] for call in close_handle.call_args_list]
            self.assertIn(301, closed)
            self.assertIn(302, closed)

    def test_membership_is_verified_before_resume_and_missing_member_never_resumes(self) -> None:
        helper = getattr(spike, "verify_membership_then_resume", None)
        self.assertIsNotNone(helper)

        class Kernel:
            def __init__(self):
                self.resume_calls = 0

            def ResumeThread(self, _thread):
                self.resume_calls += 1
                return 1

        process = spike.PROCESS_INFORMATION()
        process.hThread = 22
        process.dwProcessId = 44
        kernel = Kernel()
        with mock.patch.object(spike, "query_job_member_pids", return_value=(44, 55)):
            self.assertTrue(
                helper(SimpleNamespace(kernel32=kernel), 77, process)
            )
        self.assertEqual(kernel.resume_calls, 1)

        kernel = Kernel()
        with mock.patch.object(spike, "query_job_member_pids", return_value=(55,)):
            with self.assertRaises(spike.WinApiFailure):
                helper(SimpleNamespace(kernel32=kernel), 77, process)
        self.assertEqual(kernel.resume_calls, 0)

        class FailingResumeKernel(Kernel):
            def ResumeThread(self, _thread):
                self.resume_calls += 1
                return 0xFFFFFFFF

        kernel = FailingResumeKernel()
        verified = []
        with mock.patch.object(spike, "query_job_member_pids", return_value=(44,)):
            try:
                with self.assertRaises(spike.WinApiFailure):
                    helper(
                        SimpleNamespace(kernel32=kernel),
                        77,
                        process,
                        on_verified=lambda: verified.append(True),
                    )
            except TypeError as exc:
                self.fail(f"verified-state callback unavailable: {exc}")
        self.assertEqual(verified, [True])

    def test_source_has_no_forced_termination_primitive(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "TerminateProcess",
            "TerminateJobObject",
            "Stop-Process",
            "taskkill",
            "kill()",
            "AssignProcessToJobObject",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
