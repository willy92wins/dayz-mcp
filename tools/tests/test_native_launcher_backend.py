from __future__ import annotations

import ctypes
import asyncio
import importlib
import struct
import threading
import unittest
from pathlib import Path
from typing import Any

from dayz_mcp.dayz_tools_paths import addon_builder_exe

_ADDON_BUILDER_ANNOUNCE = addon_builder_exe().encode("utf-8")


def _handle_value(value: object) -> int:
    raw = getattr(value, "value", value)
    return int(raw or 0)


class _ImageAuthority:
    def __init__(
        self,
        approved: bool = True,
        child_approved: bool = True,
        helper_approved: bool = True,
    ) -> None:
        self.approved = approved
        self.child_approved = child_approved
        self.helper_approved = helper_approved
        self.calls: list[tuple[int, str]] = []
        self.child_calls: list[tuple[int, object]] = []
        self.helper_calls: list[int] = []

    def approve_debug_image(self, file_handle: int, *, event_kind: str) -> bool:
        self.calls.append((file_handle, event_kind))
        return self.approved

    def approve_announced_process(self, file_handle: int, announcement: object) -> bool:
        self.child_calls.append((file_handle, announcement))
        return self.child_approved

    def approve_addon_helper_process(self, file_handle: int) -> bool:
        self.helper_calls.append(file_handle)
        return self.helper_approved

    @property
    def debug_image_authority(self) -> "_ImageAuthority":
        return self


def _announcement_frame(
    *,
    kind: int = 1,
    path: bytes = b"runtime\\python.exe",
    sequence: int = 1,
) -> bytes:
    return struct.pack(
        "<4sBBHII32sQ16s",
        b"DZA1",
        1,
        kind,
        0,
        sequence,
        len(path),
        b"S" * 32,
        7,
        bytes(range(16)),
    ) + path


class _FakeKernel32:
    def __init__(self, *, fail_at: str | None = None) -> None:
        self.fail_at = fail_at
        self.events: list[str] = []
        self.closed: list[int] = []
        self.deleted_attribute_lists = 0
        self.next_duplicate = 1001
        self.job_list: tuple[int, ...] = ()
        self.handle_list: tuple[int, ...] = ()
        self.create_args: tuple[object, ...] | None = None
        self.limit_flags: int | None = None
        self.completion_port: int | None = None
        self.environment_snapshot = ""
        self.current_directory_exists = False
        self.debug_events: list[object] = []
        self.continues: list[tuple[int, int, int]] = []
        self.request_sizes: list[int] = []
        self.fail_request_write = False
        self.on_request_write: object | None = None
        self.on_request_write_blocked: object | None = None
        self.block_request_write = False
        self.request_write_release = threading.Event()
        self.pipe_bytes: dict[int, bytearray] = {}
        self.completion_events: list[tuple[bool, int, int, int]] = [
            (True, 6, 501, 703),
            (True, 4, 501, 0),
        ]

    def GetCurrentProcess(self) -> int:
        return -1

    def CreateJobObjectW(self, *_args: object) -> int:
        self.events.append("create_job")
        return 0 if self.fail_at == "create_job" else 501

    def SetInformationJobObject(
        self, job: object, info_class: int, pointer: object, _size: int
    ) -> bool:
        if info_class == 9:
            self.events.append("set_job_limits")
            limits = pointer._obj
            self.limit_flags = int(limits.BasicLimitInformation.LimitFlags)
            return self.fail_at != "set_job_limits"
        if info_class == 7:
            self.events.append("associate_completion_port")
            association = pointer._obj
            self.completion_port = _handle_value(association.CompletionPort)
            return self.fail_at != "associate_completion_port"
        raise AssertionError(f"unexpected job info class {info_class}")

    def CreateIoCompletionPort(self, *_args: object) -> int:
        self.events.append("create_completion_port")
        return 0 if self.fail_at == "create_completion_port" else 601

    def DuplicateHandle(
        self,
        _source_process: object,
        source: object,
        _target_process: object,
        target: object,
        _access: int,
        inherit: bool,
        options: int,
    ) -> bool:
        self.events.append(f"duplicate:{_handle_value(source)}")
        duplicate_index = self.next_duplicate - 1000
        if self.fail_at == f"duplicate_{duplicate_index}":
            return False
        self.assertions = (inherit, options)
        target._obj.value = self.next_duplicate
        self.next_duplicate += 1
        return True

    def InitializeProcThreadAttributeList(
        self, pointer: object, count: int, _flags: int, size: object
    ) -> bool:
        self.events.append("size_attributes" if pointer is None else "init_attributes")
        if pointer is None:
            size._obj.value = 256
            return False
        return self.fail_at != "init_attributes" and count == 2

    def UpdateProcThreadAttribute(
        self,
        _attribute_list: object,
        _flags: int,
        attribute: int,
        value: object,
        size: int,
        _previous: object,
        _return_size: object,
    ) -> bool:
        handle_type = ctypes.c_void_p
        count = int(size) // ctypes.sizeof(handle_type)
        array_type = handle_type * count
        values = tuple(
            _handle_value(item)
            for item in ctypes.cast(value, ctypes.POINTER(array_type)).contents
        )
        if attribute == 0x0002000D:
            self.events.append("attribute_job_list")
            self.job_list = values
            return self.fail_at != "attribute_job_list"
        if attribute == 0x00020002:
            self.events.append("attribute_handle_list")
            self.handle_list = values
            return self.fail_at != "attribute_handle_list"
        raise AssertionError(f"unexpected attribute {attribute:#x}")

    def DeleteProcThreadAttributeList(self, _pointer: object) -> None:
        self.events.append("delete_attributes")
        self.deleted_attribute_lists += 1

    def CreateProcessW(self, *args: object) -> bool:
        self.events.append("create_process")
        self.create_args = args
        self.environment_snapshot = "".join(args[6])
        self.current_directory_exists = Path(str(args[7])).is_dir()
        if self.fail_at == "create_process":
            return False
        process_information = args[9]._obj
        process_information.hProcess = 701
        process_information.hThread = 702
        process_information.dwProcessId = 703
        process_information.dwThreadId = 704
        return True

    def CloseHandle(self, handle: object) -> bool:
        value = _handle_value(handle)
        self.closed.append(value)
        self.events.append(f"close:{value}")
        return True

    def WaitForDebugEvent(self, *_args: object) -> object:
        if self.debug_events:
            return self.debug_events.pop(0)
        return None

    def ContinueDebugEvent(self, pid: int, tid: int, status: int) -> bool:
        self.continues.append((pid, tid, status))
        self.events.append(f"continue:{pid}:{tid}")
        return True

    def ResumeThread(self, _handle: object) -> int:
        self.events.append("resume")
        return 1

    def WriteFile(
        self,
        _handle: object,
        _buffer: object,
        size: int,
        written: object,
        _overlapped: object,
    ) -> bool:
        if self.block_request_write:
            self.events.append("write_request_blocked")
            if callable(self.on_request_write_blocked):
                self.on_request_write_blocked()
            if not self.request_write_release.wait(2.0):
                self.events.append("write_request_block_timeout")
                return False
            self.events.append("write_request_cancelled")
            return False
        if self.fail_request_write:
            self.events.append("write_request_failed")
            return False
        written._obj.value = size
        self.request_sizes.append(size)
        self.events.append("write_request")
        if callable(self.on_request_write):
            self.on_request_write()
        return True

    def CancelIoEx(self, handle: object, _overlapped: object) -> bool:
        self.events.append(f"cancel_io:{_handle_value(handle)}")
        self.request_write_release.set()
        return True

    def PeekNamedPipe(
        self,
        handle: object,
        _buffer: object,
        _size: int,
        _read: object,
        available: object,
        _left: object,
    ) -> bool:
        pending = self.pipe_bytes.get(_handle_value(handle))
        if pending is None:
            return False
        available._obj.value = len(pending)
        return True

    def ReadFile(
        self,
        handle: object,
        buffer: object,
        size: int,
        read: object,
        _overlapped: object,
    ) -> bool:
        pending = self.pipe_bytes.get(_handle_value(handle))
        if pending is None:
            raise AssertionError("ReadFile must not run without available bytes")
        chunk = bytes(pending[:size])
        del pending[:size]
        ctypes.memmove(buffer, chunk, len(chunk))
        read._obj.value = len(chunk)
        return True

    def GetQueuedCompletionStatus(self, *_args: object) -> tuple[bool, int, int, int]:
        event = self.completion_events.pop(0)
        self.events.append(
            "active_zero" if event[1] == 4 else f"new_process:{event[3]}"
        )
        return event


class _OpenedLauncher:
    def __init__(self, events: list[str], *, root_image_approved: bool = True) -> None:
        self.path = Path(r"C:\sealed\dayz-test-launcher.exe")
        self._events = events
        self._root_image_approved = root_image_approved

    def validate_native_pe(self) -> None:
        self._events.append("validate_pe")

    def approve_root_debug_image(self, file_handle: int) -> bool:
        self._events.append(f"approve_root:{file_handle}")
        return self._root_image_approved


class NativeLauncherBackendTests(unittest.TestCase):
    @staticmethod
    def _backend() -> Any:
        return importlib.import_module("dayz_mcp.native_launcher_backend")

    def test_single_create_call_has_atomic_job_exact_handles_and_closed_environment(self) -> None:
        backend = self._backend()
        fake = _FakeKernel32()
        opened = _OpenedLauncher(fake.events)
        handles = backend.LauncherInheritedHandles(11, 12, 13, 14, 15)

        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = backend._create_registered_launcher(
                opened,
                handles=handles,
                identity_json='{"platform":"codex"}',
                lease_token="lease-secret",
                daemon_policy_json="{}",
            )
        finally:
            backend._kernel32 = original

        self.assertEqual(fake.events[0], "validate_pe")
        self.assertLess(fake.events.index("attribute_job_list"), fake.events.index("create_process"))
        self.assertLess(fake.events.index("attribute_handle_list"), fake.events.index("create_process"))
        self.assertEqual(fake.job_list, (501,))
        self.assertEqual(fake.handle_list, (1001, 1002, 1003, 1004, 1005))
        self.assertEqual(fake.limit_flags, backend._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        self.assertEqual(fake.completion_port, 601)
        self.assertEqual(fake.assertions, (True, backend._DUPLICATE_SAME_ACCESS))

        assert fake.create_args is not None
        application, command, process_acl, thread_acl, inherit, flags, environment, cwd, startup_pointer, _process_pointer = fake.create_args
        self.assertEqual(application, str(opened.path))
        self.assertEqual(command.value, f'"{opened.path}"')
        self.assertIsNone(process_acl)
        self.assertIsNone(thread_acl)
        self.assertIs(inherit, True)
        self.assertEqual(flags, 0x00080401)
        self.assertIsInstance(cwd, str)
        private_directory = Path(cwd)
        self.assertTrue(private_directory.is_absolute())
        self.assertTrue(fake.current_directory_exists)
        self.assertEqual(created.private_directory, private_directory)
        startup = startup_pointer._obj
        self.assertEqual(startup.StartupInfo.cb, ctypes.sizeof(backend.STARTUPINFOEXW))
        self.assertEqual(startup.StartupInfo.dwFlags, backend._STARTF_USESTDHANDLES)
        self.assertEqual(_handle_value(startup.StartupInfo.hStdInput), 1001)
        self.assertEqual(_handle_value(startup.StartupInfo.hStdOutput), 1002)
        self.assertEqual(_handle_value(startup.StartupInfo.hStdError), 1003)
        environment_text = fake.environment_snapshot
        self.assertEqual(
            tuple(part for part in environment_text.split("\0") if part),
            (
                "DAYZ_MCP_CANCEL_HANDLE=1004",
                "DAYZ_MCP_WORKER_CANCEL_HANDLE=1005",
                'DAYZ_MCP_CLIENT_ID_JSON={"platform":"codex"}',
                "DAYZ_MCP_LEASE_TOKEN=lease-secret",
                "DAYZ_MCP_NORMAL_POLICY_JSON={}",
                f"USERPROFILE={Path.home().resolve(strict=True)}",
            ),
        )
        self.assertNotIn("lease-secret", command.value)
        self.assertTrue(all(character == "\0" for character in environment))
        self.assertEqual(fake.deleted_attribute_lists, 1)
        self.assertEqual(fake.closed[:5], [1005, 1004, 1003, 1002, 1001])

        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created.close_tree()
        finally:
            backend._kernel32 = original
        self.assertEqual(fake.closed[-4:], [501, 702, 701, 601])
        self.assertFalse(private_directory.exists())

    def test_invalid_handles_or_environment_fail_before_any_win32_mutation(self) -> None:
        backend = self._backend()
        invalid_cases = (
            ((11, 11, 13, 14, 15), "duplicate"),
            ((0, 12, 13, 14, 15), "zero"),
            ((True, 12, 13, 14, 15), "bool"),
        )
        for raw, label in invalid_cases:
            with self.subTest(label=label):
                fake = _FakeKernel32()
                opened = _OpenedLauncher(fake.events)
                with self.assertRaises(backend.NativeLauncherBackendError):
                    handles = backend.LauncherInheritedHandles(*raw)
                    original = backend._kernel32
                    backend._kernel32 = fake
                    try:
                        backend._create_registered_launcher(
                            opened,
                            handles=handles,
                            identity_json="identity",
                            lease_token="lease",
                            daemon_policy_json="{}",
                        )
                    finally:
                        backend._kernel32 = original
                self.assertEqual(fake.events, [])

        fake = _FakeKernel32()
        opened = _OpenedLauncher(fake.events)
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            with self.assertRaisesRegex(
                backend.NativeLauncherBackendError, "invalid_native_launcher_environment"
            ):
                backend._create_registered_launcher(
                    opened,
                    handles=backend.LauncherInheritedHandles(11, 12, 13, 14, 15),
                    identity_json="identity\0injected",
                    lease_token="lease",
                    daemon_policy_json="{}",
                )
        finally:
            backend._kernel32 = original
        self.assertEqual(fake.events, [])

    def test_each_pre_create_failure_closes_every_owned_handle(self) -> None:
        backend = self._backend()
        cases = (
            "set_job_limits",
            "create_completion_port",
            "associate_completion_port",
            "duplicate_3",
            "init_attributes",
            "attribute_job_list",
            "attribute_handle_list",
            "create_process",
        )
        for fail_at in cases:
            with self.subTest(fail_at=fail_at):
                fake = _FakeKernel32(fail_at=fail_at)
                opened = _OpenedLauncher(fake.events)
                original = backend._kernel32
                backend._kernel32 = fake
                try:
                    with self.assertRaisesRegex(
                        backend.NativeLauncherBackendError,
                        "native_launcher_create_failed",
                    ):
                        backend._create_registered_launcher(
                            opened,
                            handles=backend.LauncherInheritedHandles(11, 12, 13, 14, 15),
                            identity_json="identity",
                            lease_token="lease",
                            daemon_policy_json="{}",
                        )
                finally:
                    backend._kernel32 = original
                issued_duplicates = list(range(1001, fake.next_duplicate))
                for handle in issued_duplicates:
                    self.assertIn(handle, fake.closed)
                if "create_completion_port" in fake.events and fail_at != "create_completion_port":
                    self.assertIn(601, fake.closed)
                if "create_job" in fake.events:
                    self.assertIn(501, fake.closed)

    def test_source_has_one_direct_createprocess_call(self) -> None:
        backend = self._backend()
        source = Path(backend.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count("_kernel32.CreateProcessW("), 1)
        self.assertNotIn("subprocess", source)

    def test_root_debug_creation_relies_on_pre_user_mode_debug_event(self) -> None:
        backend = self._backend()
        self.assertEqual(
            backend.CREATE_FLAGS,
            backend.CREATE_UNICODE_ENVIRONMENT
            | backend.DEBUG_PROCESS
            | backend.EXTENDED_STARTUPINFO_PRESENT,
        )
        self.assertEqual(backend.CREATE_FLAGS & backend.CREATE_SUSPENDED, 0)

    def test_every_used_kernel32_function_has_an_explicit_ffi_signature(self) -> None:
        backend = self._backend()
        expected = {
            "CreatePipe": (4, backend.wintypes.BOOL),
            "WaitForDebugEvent": (2, backend.wintypes.BOOL),
            "ContinueDebugEvent": (3, backend.wintypes.BOOL),
            "WriteFile": (5, backend.wintypes.BOOL),
            "CancelIoEx": (2, backend.wintypes.BOOL),
            "ReadFile": (5, backend.wintypes.BOOL),
            "PeekNamedPipe": (6, backend.wintypes.BOOL),
            "GetQueuedCompletionStatus": (5, backend.wintypes.BOOL),
        }
        for name, (argument_count, result_type) in expected.items():
            with self.subTest(name=name):
                function = getattr(backend._kernel32, name)
                self.assertIsNotNone(function.argtypes)
                self.assertEqual(len(function.argtypes), argument_count)
                self.assertIs(function.restype, result_type)

    def test_runtime_pipe_creation_is_exact_and_cleans_partial_failure(self) -> None:
        backend = self._backend()

        class PipeKernel:
            def __init__(self, fail_at: int | None = None) -> None:
                self.fail_at = fail_at
                self.calls = 0
                self.next_handle = 100
                self.closed: list[int] = []

            def CreatePipe(self, read: object, write: object, _acl: object, _size: int) -> bool:
                self.calls += 1
                if self.fail_at == self.calls:
                    return False
                read._obj.value = self.next_handle
                write._obj.value = self.next_handle + 1
                self.next_handle += 2
                return True

            def CloseHandle(self, handle: object) -> bool:
                self.closed.append(_handle_value(handle))
                return True

        original = backend._kernel32
        try:
            successful = PipeKernel()
            backend._kernel32 = successful
            pipes = backend._create_runtime_pipes()
            self.assertEqual(
                pipes.child_handles().as_tuple(),
                (100, 103, 105, 106, 108),
            )
            self.assertEqual(pipes.parent_handles(), (101, 102, 104, 107, 109))
            pipes.close_cancel_writer()
            self.assertEqual(successful.closed, [107, 109])
            pipes.close()
            self.assertEqual(
                successful.closed,
                [107, 109, 108, 106, 105, 104, 103, 102, 101, 100],
            )

            for fail_at in range(1, 6):
                with self.subTest(fail_at=fail_at):
                    failing = PipeKernel(fail_at)
                    backend._kernel32 = failing
                    with self.assertRaisesRegex(
                        backend.NativeLauncherBackendError,
                        "native_launcher_pipe_failed",
                    ):
                        backend._create_runtime_pipes()
                    self.assertEqual(
                        failing.closed,
                        list(reversed(range(100, 100 + 2 * (fail_at - 1)))),
                    )
        finally:
            backend._kernel32 = original


class NativeDebugReducerWiringTests(unittest.TestCase):
    def test_backend_reuses_the_single_pure_debug_state_machine(self) -> None:
        backend = importlib.import_module("dayz_mcp.native_launcher_backend")
        debug = importlib.import_module("dayz_mcp.native_debug_state")
        self.assertIs(backend.NativeDebugEvent, debug.NativeDebugEvent)
        self.assertIs(backend.NativeDebugState, debug.NativeDebugState)


class NativeDebugOwnershipTests(unittest.TestCase):
    @staticmethod
    def _backend() -> Any:
        return importlib.import_module("dayz_mcp.native_launcher_backend")

    def _create(
        self,
        backend: Any,
        fake: _FakeKernel32,
        *,
        root_image_approved: bool = True,
    ) -> object:
        opened = _OpenedLauncher(
            fake.events,
            root_image_approved=root_image_approved,
        )
        return backend._create_registered_launcher(
            opened,
            handles=backend.LauncherInheritedHandles(11, 12, 13, 14, 15),
            identity_json='{"platform":"unknown"}',
            lease_token="lease-secret",
            daemon_policy_json="{}",
        )

    def test_request_and_debug_events_finish_before_active_zero_and_return(self) -> None:
        backend = self._backend()
        fake = _FakeKernel32()
        fake.pipe_bytes[23] = bytearray(_announcement_frame())
        fake.completion_events = [
            (True, 6, 501, 703),
            (True, 6, 501, 900),
            (True, 4, 501, 0),
        ]
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=703,
                tid=704,
                process_handle=801,
                thread_handle=802,
                file_handle=803,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=900,
                tid=901,
                process_handle=811,
                thread_handle=812,
                file_handle=813,
            ),
            backend.NativeDebugEvent(
                "EXCEPTION",
                pid=703,
                tid=704,
                exception_code=backend.EXCEPTION_BREAKPOINT,
                first_chance=True,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=900, tid=901, exit_code=0),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=0),
        ]
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = self._create(backend, fake)
            result = backend._supervise_created_launcher(
                created,
                canonical_request=b'{"mod":"Example","version":1}',
                runtime_pipes=backend.NativeRuntimePipes(11, 21, 22, 12, 23, 13, 14, 24, 15, 25),
                image_authority=_ImageAuthority(),
                cancel_signal=threading.Event(),
            )
        finally:
            backend._kernel32 = original
        self.assertEqual(result, 0)
        self.assertEqual(len(fake.continues), 5)
        self.assertEqual(fake.request_sizes, [29])
        self.assertIn(803, fake.closed)
        self.assertNotIn(801, fake.closed)
        self.assertNotIn(802, fake.closed)
        self.assertLess(fake.events.index("write_request"), fake.events.index("active_zero"))
        self.assertLess(fake.events.index("active_zero"), fake.events.index("close:501"))
        self.assertIn(501, fake.closed)

    def test_start_watchdog_times_out_when_root_create_event_is_absent(self) -> None:
        backend = self._backend()

        class NoRootKernel(_FakeKernel32):
            def WaitForDebugEvent(self, *_args: object) -> bool:
                ctypes.set_last_error(backend._ERROR_SEM_TIMEOUT)
                return False

        fake = NoRootKernel()
        fake.completion_events = [(True, 4, 501, 0)]
        original_kernel32 = backend._kernel32
        original_timeout = backend._LAUNCHER_START_SECONDS
        backend._kernel32 = fake
        backend._LAUNCHER_START_SECONDS = 0.0
        try:
            created = self._create(backend, fake)
            with self.assertRaisesRegex(
                backend.NativeLauncherBackendError,
                "native_launcher_start_timeout",
            ):
                backend._supervise_created_launcher(
                    created,
                    canonical_request=b"{}",
                    runtime_pipes=backend.NativeRuntimePipes(
                        11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                    ),
                    image_authority=_ImageAuthority(),
                    cancel_signal=threading.Event(),
                )
        finally:
            backend._LAUNCHER_START_SECONDS = original_timeout
            backend._kernel32 = original_kernel32
        self.assertEqual(fake.closed.count(501), 1)
        self.assertEqual(fake.continues, [])
        self.assertLess(fake.events.index("close:501"), fake.events.index("active_zero"))

    def test_root_exit_without_descendant_preserves_exit_code_immediately(self) -> None:
        backend = self._backend()
        clock = {"now": 0.0}
        waited: list[str | None] = []

        class RootOnlyKernel(_FakeKernel32):
            def WaitForDebugEvent(self, *_args: object) -> object:
                event = super().WaitForDebugEvent(*_args)
                waited.append(None if event is None else event.kind)
                if event is None:
                    ctypes.set_last_error(backend._ERROR_SEM_TIMEOUT)
                    return False
                return event

            def ContinueDebugEvent(self, pid: int, tid: int, status: int) -> bool:
                outcome = super().ContinueDebugEvent(pid, tid, status)
                if len(self.continues) == 1:
                    clock["now"] = 2.0
                return outcome

        fake = RootOnlyKernel()
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=703,
                tid=704,
                process_handle=801,
                thread_handle=802,
                file_handle=803,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=1),
        ]
        original_kernel32 = backend._kernel32
        original_monotonic = backend.time.monotonic
        original_timeout = backend._LAUNCHER_START_SECONDS
        backend._kernel32 = fake
        backend.time.monotonic = lambda: clock["now"]
        backend._LAUNCHER_START_SECONDS = 1.0
        try:
            created = self._create(backend, fake)
            with self.assertRaisesRegex(
                backend.NativeLauncherBackendError,
                "native_launcher_root_exit_before_descendant:1",
            ):
                backend._supervise_created_launcher(
                    created,
                    canonical_request=b"{}",
                    runtime_pipes=backend.NativeRuntimePipes(
                        11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                    ),
                    image_authority=_ImageAuthority(),
                    cancel_signal=threading.Event(),
                )
        finally:
            backend._LAUNCHER_START_SECONDS = original_timeout
            backend.time.monotonic = original_monotonic
            backend._kernel32 = original_kernel32
        self.assertEqual(fake.closed.count(501), 1)
        self.assertEqual(fake.continues, [(703, 704, backend.DBG_CONTINUE)] * 2)
        self.assertEqual(fake.events.count("resume"), 1)
        self.assertEqual(clock["now"], 2.0)
        self.assertEqual(waited, ["CREATE_PROCESS", "EXIT_PROCESS"])
        self.assertLess(
            fake.events.index("continue:703:704"),
            fake.events.index("close:501"),
        )

    def test_descendant_create_event_disarms_start_watchdog(self) -> None:
        backend = self._backend()
        clock = {"now": 0.0}

        class DescendantKernel(_FakeKernel32):
            def ContinueDebugEvent(self, pid: int, tid: int, status: int) -> bool:
                outcome = super().ContinueDebugEvent(pid, tid, status)
                if pid == 900:
                    clock["now"] = 2.0
                return outcome

        fake = DescendantKernel()
        fake.pipe_bytes[23] = bytearray(_announcement_frame())
        fake.completion_events = [
            (True, 6, 501, 703),
            (True, 6, 501, 900),
            (True, 4, 501, 0),
        ]
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=703,
                tid=704,
                process_handle=801,
                thread_handle=802,
                file_handle=803,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=900,
                tid=901,
                process_handle=811,
                thread_handle=812,
                file_handle=813,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=900, tid=901, exit_code=0),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=0),
        ]
        original_kernel32 = backend._kernel32
        original_monotonic = backend.time.monotonic
        original_timeout = backend._LAUNCHER_START_SECONDS
        backend._kernel32 = fake
        backend.time.monotonic = lambda: clock["now"]
        backend._LAUNCHER_START_SECONDS = 1.0
        try:
            created = self._create(backend, fake)
            result = backend._supervise_created_launcher(
                created,
                canonical_request=b"{}",
                runtime_pipes=backend.NativeRuntimePipes(
                    11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                ),
                image_authority=_ImageAuthority(),
                cancel_signal=threading.Event(),
            )
        finally:
            backend._LAUNCHER_START_SECONDS = original_timeout
            backend.time.monotonic = original_monotonic
            backend._kernel32 = original_kernel32
        self.assertEqual(result, 0)
        self.assertEqual(fake.closed.count(501), 1)
        self.assertEqual(len(fake.continues), 4)
        self.assertLess(
            fake.events.index("continue:900:901"),
            fake.events.index("close:501"),
        )

    def test_root_create_requires_pinned_hfile_identity_before_continue(self) -> None:
        backend = self._backend()
        fake = _FakeKernel32()
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=703,
                tid=704,
                process_handle=801,
                thread_handle=802,
                file_handle=803,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=1),
        ]
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = self._create(
                backend,
                fake,
                root_image_approved=False,
            )
            with self.assertRaisesRegex(
                backend.NativeLauncherBackendError,
                "native_debug_gate_rejected",
            ):
                backend._supervise_created_launcher(
                    created,
                    canonical_request=b"{}",
                    runtime_pipes=backend.NativeRuntimePipes(
                        11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                    ),
                    image_authority=_ImageAuthority(),
                    cancel_signal=threading.Event(),
                )
        finally:
            backend._kernel32 = original
        self.assertIn("approve_root:803", fake.events)
        self.assertLess(
            fake.events.index("close:501"),
            fake.events.index("continue:703:704"),
        )

    def test_rejected_descendant_closes_job_before_its_single_continue(self) -> None:
        backend = self._backend()
        fake = _FakeKernel32()
        fake.completion_events = [
            (True, 6, 501, 703),
            (True, 6, 501, 900),
            (True, 4, 501, 0),
        ]
        fake.debug_events = [
            backend.NativeDebugEvent("CREATE_PROCESS", pid=703, tid=704, process_handle=801, thread_handle=802, file_handle=803),
            backend.NativeDebugEvent("CREATE_PROCESS", pid=900, tid=901, process_handle=811, thread_handle=812, file_handle=813),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=900, tid=901, exit_code=1),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=1),
        ]
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = self._create(backend, fake)
            with self.assertRaisesRegex(
                backend.NativeLauncherBackendError, "native_debug_gate_rejected"
            ):
                backend._supervise_created_launcher(
                    created,
                    canonical_request=b"{}",
                    runtime_pipes=backend.NativeRuntimePipes(11, 21, 22, 12, 23, 13, 14, 24, 15, 25),
                    image_authority=_ImageAuthority(),
                    cancel_signal=threading.Event(),
                )
        finally:
            backend._kernel32 = original
        close_job = fake.events.index("close:501")
        child_continue = fake.events.index("continue:900:901")
        active_zero = fake.events.index("active_zero")
        self.assertLess(close_job, child_continue)
        self.assertLess(child_continue, active_zero)
        self.assertEqual(len(fake.continues), 4)
        self.assertTrue({803, 813}.issubset(fake.closed))
        self.assertTrue({801, 802, 811, 812}.isdisjoint(fake.closed))

    def test_announced_descendant_requires_exact_authority_before_continue(self) -> None:
        backend = self._backend()
        for approved in (True, False):
            with self.subTest(approved=approved):
                fake = _FakeKernel32()
                fake.pipe_bytes[23] = bytearray(_announcement_frame())
                fake.completion_events = [
                    (True, 6, 501, 703),
                    (True, 6, 501, 900),
                    (True, 4, 501, 0),
                ]
                fake.debug_events = [
                    backend.NativeDebugEvent("CREATE_PROCESS", pid=703, tid=704, process_handle=801, thread_handle=802, file_handle=803),
                    backend.NativeDebugEvent("CREATE_PROCESS", pid=900, tid=901, process_handle=811, thread_handle=812, file_handle=813),
                    backend.NativeDebugEvent("EXIT_PROCESS", pid=900, tid=901, exit_code=0),
                    backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=0),
                ]
                authority = _ImageAuthority(child_approved=approved)
                original = backend._kernel32
                backend._kernel32 = fake
                try:
                    created = self._create(backend, fake)
                    if approved:
                        self.assertEqual(
                            backend._supervise_created_launcher(
                                created,
                                canonical_request=b"{}",
                                runtime_pipes=backend.NativeRuntimePipes(11, 21, 22, 12, 23, 13, 14, 24, 15, 25),
                                image_authority=authority,
                                cancel_signal=threading.Event(),
                            ),
                            0,
                        )
                    else:
                        with self.assertRaisesRegex(
                            backend.NativeLauncherBackendError,
                            "native_debug_gate_rejected",
                        ):
                            backend._supervise_created_launcher(
                                created,
                                canonical_request=b"{}",
                                runtime_pipes=backend.NativeRuntimePipes(11, 21, 22, 12, 23, 13, 14, 24, 15, 25),
                                image_authority=authority,
                                cancel_signal=threading.Event(),
                            )
                finally:
                    backend._kernel32 = original
                self.assertEqual(len(authority.child_calls), 1)
                self.assertEqual(authority.child_calls[0][0], 813)
                announcement = authority.child_calls[0][1]
                self.assertEqual(announcement.sequence, 1)
                self.assertEqual(announcement.announced_path, r"runtime\python.exe")
                self.assertLess(
                    fake.events.index("new_process:900"),
                    fake.events.index("continue:900:901"),
                )
                if not approved:
                    self.assertLess(
                        fake.events.index("close:501"),
                        fake.events.index("continue:900:901"),
                    )

    def test_exact_helper_without_announcement_is_allowed_only_during_active_addon_builder(self) -> None:
        backend = self._backend()
        fake = _FakeKernel32()
        fake.pipe_bytes[23] = bytearray(
            _announcement_frame(
                kind=3,
                path=_ADDON_BUILDER_ANNOUNCE,
            )
        )
        fake.completion_events = [
            (True, 6, 501, 703),
            (True, 6, 501, 900),
            (True, 6, 501, 910),
            (True, 4, 501, 0),
        ]
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=703, tid=704,
                process_handle=801, thread_handle=802, file_handle=803,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=900, tid=901,
                process_handle=811, thread_handle=812, file_handle=813,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=910, tid=911,
                process_handle=821, thread_handle=822, file_handle=823,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=910, tid=911, exit_code=0),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=900, tid=901, exit_code=0),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=0),
        ]
        authority = _ImageAuthority()
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = self._create(backend, fake)
            result = backend._supervise_created_launcher(
                created,
                canonical_request=b"{}",
                runtime_pipes=backend.NativeRuntimePipes(
                    11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                ),
                image_authority=authority,
                cancel_signal=threading.Event(),
            )
        finally:
            backend._kernel32 = original
        self.assertEqual(result, 0)
        self.assertEqual(authority.helper_calls, [823])
        self.assertLess(
            fake.events.index("new_process:910"),
            fake.events.index("continue:910:911"),
        )

    def test_second_simultaneous_addon_helper_fails_before_continue(self) -> None:
        backend = self._backend()
        fake = _FakeKernel32()
        fake.pipe_bytes[23] = bytearray(
            _announcement_frame(
                kind=3,
                path=_ADDON_BUILDER_ANNOUNCE,
            )
        )
        fake.completion_events = [
            (True, 6, 501, 703),
            (True, 6, 501, 900),
            (True, 6, 501, 910),
            (True, 6, 501, 920),
            (True, 4, 501, 0),
        ]
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=703, tid=704,
                process_handle=801, thread_handle=802, file_handle=803,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=900, tid=901,
                process_handle=811, thread_handle=812, file_handle=813,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=910, tid=911,
                process_handle=821, thread_handle=822, file_handle=823,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=920, tid=921,
                process_handle=831, thread_handle=832, file_handle=833,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=920, tid=921, exit_code=1),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=910, tid=911, exit_code=1),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=900, tid=901, exit_code=1),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=1),
        ]
        authority = _ImageAuthority()
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = self._create(backend, fake)
            with self.assertRaisesRegex(
                backend.NativeLauncherBackendError,
                "native_debug_gate_rejected",
            ):
                backend._supervise_created_launcher(
                    created,
                    canonical_request=b"{}",
                    runtime_pipes=backend.NativeRuntimePipes(
                        11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                    ),
                    image_authority=authority,
                    cancel_signal=threading.Event(),
                )
        finally:
            backend._kernel32 = original
        self.assertEqual(authority.helper_calls, [823])
        self.assertLess(
            fake.events.index("close:501"),
            fake.events.index("continue:920:921"),
        )

    def test_addon_helper_after_builder_exit_fails_before_continue(self) -> None:
        backend = self._backend()
        fake = _FakeKernel32()
        fake.pipe_bytes[23] = bytearray(
            _announcement_frame(
                kind=3,
                path=_ADDON_BUILDER_ANNOUNCE,
            )
        )
        fake.completion_events = [
            (True, 6, 501, 703),
            (True, 6, 501, 900),
            (True, 6, 501, 910),
            (True, 4, 501, 0),
        ]
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=703, tid=704,
                process_handle=801, thread_handle=802, file_handle=803,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=900, tid=901,
                process_handle=811, thread_handle=812, file_handle=813,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=900, tid=901, exit_code=0),
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=910, tid=911,
                process_handle=821, thread_handle=822, file_handle=823,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=910, tid=911, exit_code=1),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=1),
        ]
        authority = _ImageAuthority()
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = self._create(backend, fake)
            with self.assertRaisesRegex(
                backend.NativeLauncherBackendError,
                "native_debug_gate_rejected",
            ):
                backend._supervise_created_launcher(
                    created,
                    canonical_request=b"{}",
                    runtime_pipes=backend.NativeRuntimePipes(
                        11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                    ),
                    image_authority=authority,
                    cancel_signal=threading.Event(),
                )
        finally:
            backend._kernel32 = original
        self.assertEqual(authority.helper_calls, [])
        self.assertLess(
            fake.events.index("close:501"),
            fake.events.index("continue:910:911"),
        )

    def test_addon_helper_lifetime_cap_fails_closed(self) -> None:
        backend = self._backend()
        self.assertEqual(backend._MAX_ADDON_HELPER_LAUNCHES, 64)
        fake = _FakeKernel32()
        fake.pipe_bytes[23] = bytearray(
            _announcement_frame(
                kind=3,
                path=_ADDON_BUILDER_ANNOUNCE,
            )
        )
        fake.completion_events = [
            (True, 6, 501, 703),
            (True, 6, 501, 900),
            (True, 6, 501, 910),
            (True, 6, 501, 920),
            (True, 4, 501, 0),
        ]
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=703, tid=704,
                process_handle=801, thread_handle=802, file_handle=803,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=900, tid=901,
                process_handle=811, thread_handle=812, file_handle=813,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=910, tid=911,
                process_handle=821, thread_handle=822, file_handle=823,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=910, tid=911, exit_code=0),
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=920, tid=921,
                process_handle=831, thread_handle=832, file_handle=833,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=920, tid=921, exit_code=1),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=900, tid=901, exit_code=1),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=1),
        ]
        authority = _ImageAuthority()
        original_kernel32 = backend._kernel32
        original_cap = backend._MAX_ADDON_HELPER_LAUNCHES
        backend._kernel32 = fake
        backend._MAX_ADDON_HELPER_LAUNCHES = 1
        try:
            created = self._create(backend, fake)
            with self.assertRaisesRegex(
                backend.NativeLauncherBackendError,
                "native_debug_gate_rejected",
            ):
                backend._supervise_created_launcher(
                    created,
                    canonical_request=b"{}",
                    runtime_pipes=backend.NativeRuntimePipes(
                        11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                    ),
                    image_authority=authority,
                    cancel_signal=threading.Event(),
                )
        finally:
            backend._MAX_ADDON_HELPER_LAUNCHES = original_cap
            backend._kernel32 = original_kernel32
        self.assertEqual(authority.helper_calls, [823])
        self.assertLess(
            fake.events.index("close:501"),
            fake.events.index("continue:920:921"),
        )

    def test_second_announced_addon_builder_fails_before_continue(self) -> None:
        backend = self._backend()
        fake = _FakeKernel32()
        addon_path = _ADDON_BUILDER_ANNOUNCE
        fake.pipe_bytes[23] = bytearray(
            _announcement_frame(kind=3, path=addon_path)
            + _announcement_frame(kind=3, path=addon_path, sequence=2)
        )
        fake.completion_events = [
            (True, 6, 501, 703),
            (True, 6, 501, 900),
            (True, 6, 501, 910),
            (True, 4, 501, 0),
        ]
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=703, tid=704,
                process_handle=801, thread_handle=802, file_handle=803,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=900, tid=901,
                process_handle=811, thread_handle=812, file_handle=813,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=910, tid=911,
                process_handle=821, thread_handle=822, file_handle=823,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=910, tid=911, exit_code=1),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=900, tid=901, exit_code=1),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=1),
        ]
        authority = _ImageAuthority()
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = self._create(backend, fake)
            with self.assertRaisesRegex(
                backend.NativeLauncherBackendError,
                "native_debug_gate_rejected",
            ):
                backend._supervise_created_launcher(
                    created,
                    canonical_request=b"{}",
                    runtime_pipes=backend.NativeRuntimePipes(
                        11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                    ),
                    image_authority=authority,
                    cancel_signal=threading.Event(),
                )
        finally:
            backend._kernel32 = original
        self.assertEqual(len(authority.child_calls), 1)
        self.assertLess(
            fake.events.index("close:501"),
            fake.events.index("continue:910:911"),
        )

    def test_unannounced_helper_without_active_addon_builder_fails_before_continue(self) -> None:
        backend = self._backend()
        fake = _FakeKernel32()
        fake.completion_events = [
            (True, 6, 501, 703),
            (True, 6, 501, 910),
            (True, 4, 501, 0),
        ]
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=703, tid=704,
                process_handle=801, thread_handle=802, file_handle=803,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=910, tid=911,
                process_handle=821, thread_handle=822, file_handle=823,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=910, tid=911, exit_code=1),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=1),
        ]
        authority = _ImageAuthority()
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = self._create(backend, fake)
            with self.assertRaisesRegex(
                backend.NativeLauncherBackendError,
                "native_debug_gate_rejected",
            ):
                backend._supervise_created_launcher(
                    created,
                    canonical_request=b"{}",
                    runtime_pipes=backend.NativeRuntimePipes(
                        11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                    ),
                    image_authority=authority,
                    cancel_signal=threading.Event(),
                )
        finally:
            backend._kernel32 = original
        self.assertEqual(authority.helper_calls, [])

    def test_partial_pending_announcement_blocks_addon_helper_fallback(self) -> None:
        backend = self._backend()
        fake = _FakeKernel32()
        addon_path = _ADDON_BUILDER_ANNOUNCE
        pending = _announcement_frame(sequence=2)[:20]
        fake.pipe_bytes[23] = bytearray(
            _announcement_frame(kind=3, path=addon_path) + pending
        )
        fake.completion_events = [
            (True, 6, 501, 703),
            (True, 6, 501, 900),
            (True, 6, 501, 910),
            (True, 4, 501, 0),
        ]
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=703, tid=704,
                process_handle=801, thread_handle=802, file_handle=803,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=900, tid=901,
                process_handle=811, thread_handle=812, file_handle=813,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=910, tid=911,
                process_handle=821, thread_handle=822, file_handle=823,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=910, tid=911, exit_code=1),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=900, tid=901, exit_code=1),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=1),
        ]
        authority = _ImageAuthority()
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = self._create(backend, fake)
            with self.assertRaisesRegex(
                backend.NativeLauncherBackendError,
                "native_debug_gate_rejected",
            ):
                backend._supervise_created_launcher(
                    created,
                    canonical_request=b"{}",
                    runtime_pipes=backend.NativeRuntimePipes(
                        11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                    ),
                    image_authority=authority,
                    cancel_signal=threading.Event(),
                )
        finally:
            backend._kernel32 = original
        self.assertEqual(authority.helper_calls, [])
        self.assertLess(
            fake.events.index("close:501"),
            fake.events.index("continue:910:911"),
        )
        self.assertLess(
            fake.events.index("close:501"),
            fake.events.index("continue:910:911"),
        )

    def test_descendant_job_pid_mismatch_is_rejected_before_continue(self) -> None:
        backend = self._backend()
        fake = _FakeKernel32()
        fake.pipe_bytes[23] = bytearray(_announcement_frame())
        fake.completion_events = [
            (True, 6, 501, 703),
            (True, 6, 501, 999),
            (True, 4, 501, 0),
        ]
        fake.debug_events = [
            backend.NativeDebugEvent("CREATE_PROCESS", pid=703, tid=704, process_handle=801, thread_handle=802, file_handle=803),
            backend.NativeDebugEvent("CREATE_PROCESS", pid=900, tid=901, process_handle=811, thread_handle=812, file_handle=813),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=900, tid=901, exit_code=1),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=1),
        ]
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = self._create(backend, fake)
            with self.assertRaisesRegex(
                backend.NativeLauncherBackendError,
                "native_debug_gate_rejected",
            ):
                backend._supervise_created_launcher(
                    created,
                    canonical_request=b"{}",
                    runtime_pipes=backend.NativeRuntimePipes(11, 21, 22, 12, 23, 13, 14, 24, 15, 25),
                    image_authority=_ImageAuthority(),
                    cancel_signal=threading.Event(),
                )
        finally:
            backend._kernel32 = original
        self.assertLess(
            fake.events.index("close:501"),
            fake.events.index("continue:900:901"),
        )

    def test_request_write_error_still_closes_job_drains_and_waits_active_zero(self) -> None:
        backend = self._backend()
        fake = _FakeKernel32()
        fake.fail_request_write = True
        fake.debug_events = [
            backend.NativeDebugEvent("CREATE_PROCESS", pid=703, tid=704, process_handle=801, thread_handle=802, file_handle=803),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=1),
        ]
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = self._create(backend, fake)
            with self.assertRaisesRegex(
                backend.NativeLauncherBackendError,
                "native_launcher_request_write_failed",
            ):
                backend._supervise_created_launcher(
                    created,
                    canonical_request=b"{}",
                    runtime_pipes=backend.NativeRuntimePipes(11, 21, 22, 12, 23, 13, 14, 24, 15, 25),
                    image_authority=_ImageAuthority(),
                    cancel_signal=threading.Event(),
                )
        finally:
            backend._kernel32 = original
        continue_indices = [
            index
            for index, event in enumerate(fake.events)
            if event == "continue:703:704"
        ]
        self.assertLess(fake.events.index("close:501"), continue_indices[-1])
        self.assertLess(continue_indices[-1], fake.events.index("active_zero"))
        self.assertEqual(len(fake.continues), 2)

    def test_cancellation_closes_liveness_before_job_without_writing_a_signal(self) -> None:
        backend = self._backend()
        fake = _FakeKernel32()
        fake.debug_events = [
            backend.NativeDebugEvent("CREATE_PROCESS", pid=703, tid=704, process_handle=801, thread_handle=802, file_handle=803),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=130),
        ]
        cancel = threading.Event()
        cancel.set()
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = self._create(backend, fake)
            result = backend._supervise_created_launcher(
                created,
                canonical_request=b"{}",
                runtime_pipes=backend.NativeRuntimePipes(11, 21, 22, 12, 23, 13, 14, 24, 15, 25),
                image_authority=_ImageAuthority(),
                cancel_signal=cancel,
            )
        finally:
            backend._kernel32 = original
        self.assertEqual(result, 130)
        self.assertEqual(fake.request_sizes, [])
        self.assertLess(fake.events.index("close:24"), fake.events.index("close:501"))

    def test_started_cancellation_allows_cooperative_exit_before_job_close(self) -> None:
        backend = self._backend()
        fake = _FakeKernel32()
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=703,
                tid=704,
                process_handle=801,
                thread_handle=802,
                file_handle=803,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=130),
        ]
        cancel = threading.Event()
        fake.on_request_write = cancel.set
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = self._create(backend, fake)
            result = backend._supervise_created_launcher(
                created,
                canonical_request=b"{}",
                runtime_pipes=backend.NativeRuntimePipes(
                    11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                ),
                image_authority=_ImageAuthority(),
                cancel_signal=cancel,
            )
        finally:
            backend._kernel32 = original
        self.assertEqual(result, 130)
        self.assertLess(fake.events.index("close:24"), fake.events.index("close:501"))
        root_continues = [
            index
            for index, event in enumerate(fake.events)
            if event == "continue:703:704"
        ]
        self.assertEqual(len(root_continues), 2)
        self.assertLess(root_continues[-1], fake.events.index("close:501"))

    def test_cancellation_without_exit_process_never_reports_clean_130(self) -> None:
        backend = self._backend()

        class NoExitKernel(_FakeKernel32):
            def WaitForDebugEvent(self, *_args: object) -> object:
                event = super().WaitForDebugEvent(*_args)
                if event is None:
                    ctypes.set_last_error(backend._ERROR_SEM_TIMEOUT)
                    return False
                return event

        fake = NoExitKernel()
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=703,
                tid=704,
                process_handle=801,
                thread_handle=802,
                file_handle=803,
            ),
        ]
        cancel = threading.Event()
        fake.on_request_write = cancel.set
        original_kernel32 = backend._kernel32
        original_cancel_grace = backend._CANCEL_GRACE_SECONDS
        original_debug_drain = backend._DEBUG_DRAIN_SECONDS
        backend._kernel32 = fake
        backend._CANCEL_GRACE_SECONDS = 0.0
        backend._DEBUG_DRAIN_SECONDS = 0.0
        try:
            created = self._create(backend, fake)
            with self.assertRaisesRegex(
                backend.NativeLauncherBackendError,
                "native_job_cleanup_incomplete",
            ):
                backend._supervise_created_launcher(
                    created,
                    canonical_request=b"{}",
                    runtime_pipes=backend.NativeRuntimePipes(
                        11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                    ),
                    image_authority=_ImageAuthority(),
                    cancel_signal=cancel,
                )
        finally:
            backend._DEBUG_DRAIN_SECONDS = original_debug_drain
            backend._CANCEL_GRACE_SECONDS = original_cancel_grace
            backend._kernel32 = original_kernel32
        self.assertIn("close:501", fake.events)
        self.assertNotIn("active_zero", fake.events)

    def test_started_cancellation_cancels_blocked_request_write_without_stalling_debug(self) -> None:
        backend = self._backend()
        fake = _FakeKernel32()
        fake.block_request_write = True
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=703,
                tid=704,
                process_handle=801,
                thread_handle=802,
                file_handle=803,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=130),
        ]
        cancel = threading.Event()
        fake.on_request_write_blocked = cancel.set
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = self._create(backend, fake)
            result = backend._supervise_created_launcher(
                created,
                canonical_request=b"{}",
                runtime_pipes=backend.NativeRuntimePipes(
                    11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                ),
                image_authority=_ImageAuthority(),
                cancel_signal=cancel,
            )
        finally:
            backend._kernel32 = original
        self.assertEqual(result, 130)
        self.assertIn("cancel_io:1006", fake.events)
        self.assertIn("write_request_cancelled", fake.events)
        self.assertLess(
            fake.events.index("continue:703:704"),
            fake.events.index("write_request_cancelled"),
        )

    def test_request_write_deadline_cancels_io_and_fails_closed(self) -> None:
        backend = self._backend()
        fake = _FakeKernel32()
        fake.block_request_write = True
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=703,
                tid=704,
                process_handle=801,
                thread_handle=802,
                file_handle=803,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=1),
        ]
        original_kernel32 = backend._kernel32
        original_deadline = backend._REQUEST_WRITE_SECONDS
        backend._kernel32 = fake
        backend._REQUEST_WRITE_SECONDS = 0.0
        try:
            created = self._create(backend, fake)
            with self.assertRaisesRegex(
                backend.NativeLauncherBackendError,
                "native_launcher_request_write_timeout",
            ):
                backend._supervise_created_launcher(
                    created,
                    canonical_request=b"{}",
                    runtime_pipes=backend.NativeRuntimePipes(
                        11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                    ),
                    image_authority=_ImageAuthority(),
                    cancel_signal=threading.Event(),
                )
        finally:
            backend._REQUEST_WRITE_SECONDS = original_deadline
            backend._kernel32 = original_kernel32
        self.assertIn("cancel_io:1006", fake.events)
        continue_indices = [
            index
            for index, event in enumerate(fake.events)
            if event == "continue:703:704"
        ]
        self.assertEqual(len(continue_indices), 2)
        self.assertLess(
            fake.events.index("close:501"),
            continue_indices[-1],
        )

    def test_load_dll_requires_handle_accreditation_before_continue(self) -> None:
        backend = self._backend()
        for approved in (True, False):
            with self.subTest(approved=approved):
                fake = _FakeKernel32()
                fake.pipe_bytes[23] = bytearray(_announcement_frame())
                fake.completion_events = [
                    (True, 6, 501, 703),
                    (True, 6, 501, 900),
                    (True, 4, 501, 0),
                ]
                fake.debug_events = [
                    backend.NativeDebugEvent("CREATE_PROCESS", pid=703, tid=704, process_handle=801, thread_handle=802, file_handle=803),
                    backend.NativeDebugEvent("CREATE_PROCESS", pid=900, tid=901, process_handle=811, thread_handle=812, file_handle=813),
                    backend.NativeDebugEvent("LOAD_DLL", pid=703, tid=704, file_handle=804),
                    backend.NativeDebugEvent("EXIT_PROCESS", pid=900, tid=901, exit_code=0),
                    backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=0),
                ]
                authority = _ImageAuthority(approved)
                original = backend._kernel32
                backend._kernel32 = fake
                try:
                    created = self._create(backend, fake)
                    if approved:
                        result = backend._supervise_created_launcher(
                            created,
                            canonical_request=b"{}",
                            runtime_pipes=backend.NativeRuntimePipes(11, 21, 22, 12, 23, 13, 14, 24, 15, 25),
                            image_authority=authority,
                            cancel_signal=threading.Event(),
                        )
                        self.assertEqual(result, 0)
                    else:
                        with self.assertRaisesRegex(
                            backend.NativeLauncherBackendError,
                            "native_debug_gate_rejected",
                        ):
                            backend._supervise_created_launcher(
                                created,
                                canonical_request=b"{}",
                                runtime_pipes=backend.NativeRuntimePipes(11, 21, 22, 12, 23, 13, 14, 24, 15, 25),
                                image_authority=authority,
                                cancel_signal=threading.Event(),
                            )
                finally:
                    backend._kernel32 = original
                self.assertEqual(authority.calls, [(804, "LOAD_DLL")])
                if not approved:
                    continue_indices = [
                        index
                        for index, item in enumerate(fake.events)
                        if item == "continue:703:704"
                    ]
                    self.assertLess(fake.events.index("close:501"), continue_indices[1])

    def test_gate_rejection_preserves_fine_code_kind_and_pid(self) -> None:
        """Gate rejections keep the stable code and surface the fine detail."""
        backend = self._backend()
        fake = _FakeKernel32()
        fake.completion_events = [
            (True, 6, 501, 703),
            (True, 6, 501, 900),
            (True, 4, 501, 0),
        ]
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=703,
                tid=704,
                process_handle=801,
                thread_handle=802,
                file_handle=803,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=900,
                tid=901,
                process_handle=811,
                thread_handle=812,
                file_handle=813,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=900, tid=901, exit_code=1),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=1),
        ]
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = self._create(backend, fake)
            with self.assertRaises(backend.NativeLauncherBackendError) as raised:
                backend._supervise_created_launcher(
                    created,
                    canonical_request=b"{}",
                    runtime_pipes=backend.NativeRuntimePipes(
                        11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                    ),
                    image_authority=_ImageAuthority(),
                    cancel_signal=threading.Event(),
                )
        finally:
            backend._kernel32 = original

        error = raised.exception
        message = str(error)
        self.assertTrue(
            message.startswith("native_debug_gate_rejected"),
            msg=f"stable code lost: {message!r}",
        )
        self.assertEqual(error.code, "native_debug_gate_rejected")
        self.assertIsNotNone(error.detail)
        self.assertIn("unapproved_debug_image", error.detail)
        self.assertIn("kind=CREATE_PROCESS", error.detail)
        self.assertIn("pid=900", error.detail)
        self.assertEqual(error.fine_code, "unapproved_debug_image")
        self.assertEqual(error.event_kind, "CREATE_PROCESS")
        self.assertEqual(error.pid, 900)
        # Fake kernel has no GetFinalPathNameByHandleW — path must be omitted, not invented.
        self.assertIsNone(error.image_path)
        self.assertNotIn("image=", error.detail)
        # assertRaisesRegex / re.search still match the stable code as a substring.
        self.assertRegex(message, "native_debug_gate_rejected")

    def test_failing_detail_capture_never_breaks_the_cleanup(self) -> None:
        """Observability must not alter control flow.

        The detail capture runs BEFORE close_job(). If it were allowed to raise,
        the job would never close and the process tree would be stranded -- far
        worse than losing a diagnostic string. Here the capture blows up and the
        caller must still see the plain stable code, not the RuntimeError.
        """
        backend = self._backend()
        fake = _FakeKernel32()
        fake.completion_events = [
            (True, 6, 501, 703),
            (True, 6, 501, 900),
            (True, 4, 501, 0),
        ]
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=703, tid=704,
                process_handle=801, thread_handle=802, file_handle=803,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS", pid=900, tid=901,
                process_handle=811, thread_handle=812, file_handle=813,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=900, tid=901, exit_code=1),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=1),
        ]

        def explode(_decision: object, _event: object) -> None:
            raise RuntimeError("detail capture blew up")

        original_kernel = backend._kernel32
        original_builder = backend._native_debug_gate_rejection
        backend._kernel32 = fake
        backend._native_debug_gate_rejection = explode
        try:
            created = self._create(backend, fake)
            with self.assertRaises(backend.NativeLauncherBackendError) as raised:
                backend._supervise_created_launcher(
                    created,
                    canonical_request=b"{}",
                    runtime_pipes=backend.NativeRuntimePipes(
                        11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                    ),
                    image_authority=_ImageAuthority(),
                    cancel_signal=threading.Event(),
                )
        finally:
            backend._kernel32 = original_kernel
            backend._native_debug_gate_rejection = original_builder

        # Reaching this assert at all proves the RuntimeError did not escape and
        # the supervisor ran to completion through close_job() and the drains.
        error = raised.exception
        self.assertEqual(str(error), "native_debug_gate_rejected")
        self.assertEqual(error.code, "native_debug_gate_rejected")
        self.assertIsNone(error.detail)

    def test_gate_rejection_includes_resolved_image_path_when_available(self) -> None:
        backend = self._backend()
        resolved = r"C:\Steam\steamapps\common\DayZ\SomeUnknown.dll"

        class PathResolvingKernel(_FakeKernel32):
            def GetFinalPathNameByHandleW(
                self,
                handle: object,
                buffer: object,
                size: int,
                _flags: int,
            ) -> int:
                value = int(getattr(handle, "value", handle) or 0)
                if value != 804:
                    return 0
                text = "\\\\?\\" + resolved
                if len(text) + 1 > size:
                    return 0
                for index, char in enumerate(text):
                    buffer[index] = char
                buffer[len(text)] = "\0"
                return len(text)

        fake = PathResolvingKernel()
        fake.pipe_bytes[23] = bytearray(_announcement_frame())
        fake.completion_events = [
            (True, 6, 501, 703),
            (True, 6, 501, 900),
            (True, 4, 501, 0),
        ]
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=703,
                tid=704,
                process_handle=801,
                thread_handle=802,
                file_handle=803,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=900,
                tid=901,
                process_handle=811,
                thread_handle=812,
                file_handle=813,
            ),
            backend.NativeDebugEvent(
                "LOAD_DLL", pid=703, tid=704, file_handle=804
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=900, tid=901, exit_code=0),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=0),
        ]
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = self._create(backend, fake)
            with self.assertRaises(backend.NativeLauncherBackendError) as raised:
                backend._supervise_created_launcher(
                    created,
                    canonical_request=b"{}",
                    runtime_pipes=backend.NativeRuntimePipes(
                        11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                    ),
                    image_authority=_ImageAuthority(approved=False),
                    cancel_signal=threading.Event(),
                )
        finally:
            backend._kernel32 = original

        error = raised.exception
        self.assertTrue(str(error).startswith("native_debug_gate_rejected"))
        self.assertEqual(error.code, "native_debug_gate_rejected")
        self.assertEqual(error.fine_code, "unapproved_debug_image")
        self.assertEqual(error.event_kind, "LOAD_DLL")
        self.assertEqual(error.pid, 703)
        self.assertEqual(error.image_path, resolved)
        self.assertIn(f"image={resolved}", error.detail)

    def test_successful_supervise_does_not_gain_gate_rejection_detail(self) -> None:
        """Negative control: a non-rejected run keeps plain success, no gate attrs."""
        backend = self._backend()
        fake = _FakeKernel32()
        fake.pipe_bytes[23] = bytearray(_announcement_frame())
        fake.completion_events = [
            (True, 6, 501, 703),
            (True, 6, 501, 900),
            (True, 4, 501, 0),
        ]
        fake.debug_events = [
            backend.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=703,
                tid=704,
                process_handle=801,
                thread_handle=802,
                file_handle=803,
            ),
            backend.NativeDebugEvent(
                "CREATE_PROCESS",
                pid=900,
                tid=901,
                process_handle=811,
                thread_handle=812,
                file_handle=813,
            ),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=900, tid=901, exit_code=0),
            backend.NativeDebugEvent("EXIT_PROCESS", pid=703, tid=704, exit_code=0),
        ]
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            created = self._create(backend, fake)
            result = backend._supervise_created_launcher(
                created,
                canonical_request=b"{}",
                runtime_pipes=backend.NativeRuntimePipes(
                    11, 21, 22, 12, 23, 13, 14, 24, 15, 25
                ),
                image_authority=_ImageAuthority(),
                cancel_signal=threading.Event(),
            )
        finally:
            backend._kernel32 = original
        self.assertEqual(result, 0)
        # Non-gate errors stay plain strings with no forced detail.
        plain = backend.NativeLauncherBackendError("native_debug_incomplete")
        self.assertEqual(str(plain), "native_debug_incomplete")
        self.assertEqual(plain.code, "native_debug_incomplete")
        self.assertIsNone(plain.detail)
        self.assertIsNone(plain.fine_code)
        self.assertIsNone(plain.event_kind)
        self.assertIsNone(plain.pid)
        self.assertIsNone(plain.image_path)

    def test_wait_debug_event_distinguishes_timeout_from_failure(self) -> None:
        backend = self._backend()

        class WaitKernel:
            def __init__(self, error: int) -> None:
                self.error = error

            def WaitForDebugEvent(self, *_args: object) -> bool:
                ctypes.set_last_error(self.error)
                return False

        original = backend._kernel32
        try:
            backend._kernel32 = WaitKernel(backend._ERROR_SEM_TIMEOUT)
            self.assertIsNone(backend._wait_debug_event())
            backend._kernel32 = WaitKernel(5)
            with self.assertRaisesRegex(
                backend.NativeLauncherBackendError, "native_debug_wait_failed"
            ):
                backend._wait_debug_event()
        finally:
            backend._kernel32 = original

    def test_output_drain_delivers_bounded_chunks_to_the_named_sink(self) -> None:
        backend = self._backend()

        class OutputKernel:
            def __init__(self) -> None:
                self.pending = bytearray(b"output-bytes")

            def PeekNamedPipe(
                self,
                _handle: object,
                _buffer: object,
                _size: int,
                _read: object,
                available: object,
                _remaining: object,
            ) -> bool:
                available._obj.value = len(self.pending)
                return True

            def ReadFile(
                self,
                _handle: object,
                buffer: object,
                size: int,
                read: object,
                _overlapped: object,
            ) -> bool:
                chunk = bytes(self.pending[:size])
                del self.pending[:size]
                ctypes.memmove(buffer, chunk, len(chunk))
                read._obj.value = len(chunk)
                return True

        delivered: list[tuple[str, bytes]] = []
        original = backend._kernel32
        backend._kernel32 = OutputKernel()
        try:
            backend._drain_output(
                22,
                channel="stdout",
                output_sink=lambda channel, chunk: delivered.append((channel, chunk)),
            )
        finally:
            backend._kernel32 = original
        self.assertEqual(delivered, [("stdout", b"output-bytes")])

    def test_active_zero_requires_the_exact_job_completion_key(self) -> None:
        backend = self._backend()

        class CompletionKernel:
            def __init__(self) -> None:
                self.events = [(True, 4, 999), (True, 4, 501)]
                self.calls = 0

            def GetQueuedCompletionStatus(self, *_args: object) -> tuple[bool, int, int]:
                self.calls += 1
                return self.events.pop(0)

        fake = CompletionKernel()
        original = backend._kernel32
        backend._kernel32 = fake
        try:
            self.assertTrue(
                backend._wait_active_zero(
                    601,
                    expected_job_handle=501,
                    deadline=backend.time.monotonic() + 1.0,
                )
            )
        finally:
            backend._kernel32 = original
        self.assertEqual(fake.calls, 2)


class NativeLauncherThreadJoinTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_runner_owns_all_runtime_handles_and_joins(self) -> None:
        backend = importlib.import_module("dayz_mcp.native_launcher_backend")
        closed: list[tuple[int, str]] = []

        class CloseOnlyKernel:
            def CloseHandle(self, handle: object) -> bool:
                closed.append((_handle_value(handle), threading.current_thread().name))
                return True

        original_kernel = backend._kernel32
        original_create = backend._create_registered_launcher
        original_supervise = backend._supervise_created_launcher
        original_pipes = backend._create_runtime_pipes
        worker_thread: list[str] = []

        def fake_create(*_args: object, **_kwargs: object) -> object:
            worker_thread.append(threading.current_thread().name)
            return object()

        def fake_supervise(*_args: object, **_kwargs: object) -> int:
            worker_thread.append(threading.current_thread().name)
            return 0

        def fake_pipes() -> object:
            return backend.NativeRuntimePipes(
                11, 21, 22, 12, 23, 13, 14, 24, 15, 25
            )

        backend._kernel32 = CloseOnlyKernel()
        backend._create_registered_launcher = fake_create
        backend._supervise_created_launcher = fake_supervise
        backend._create_runtime_pipes = fake_pipes
        try:
            result = await backend.launch_registered_native(
                object(),
                verified_bundle=_ImageAuthority(),
                identity_json="identity",
                lease_token="lease",
                daemon_policy_json="{}",
                canonical_request=b"{}",
                cancel_event=asyncio.Event(),
            )
        finally:
            backend._kernel32 = original_kernel
            backend._create_registered_launcher = original_create
            backend._supervise_created_launcher = original_supervise
            backend._create_runtime_pipes = original_pipes
        self.assertEqual(result, 0)
        self.assertEqual(worker_thread, ["dayz-native-launcher", "dayz-native-launcher"])
        self.assertEqual(
            [value for value, _thread in closed],
            [25, 15, 24, 14, 13, 23, 12, 22, 21, 11],
        )
        self.assertTrue(all(thread == "dayz-native-launcher" for _value, thread in closed))

    async def test_repeated_async_cancellation_signals_then_joins_native_thread(self) -> None:
        backend = importlib.import_module("dayz_mcp.native_launcher_backend")
        cancel_signal = threading.Event()
        thread_started = threading.Event()
        permit_finish = threading.Event()
        sequence: list[str] = []
        outcome = backend._NativeThreadOutcome()

        def native_thread() -> None:
            thread_started.set()
            sequence.append("create_and_wait_same_thread")
            cancel_signal.wait(2.0)
            sequence.extend(("job_closed", "active_zero", "debug_drained"))
            permit_finish.wait(2.0)
            sequence.append("thread_return")

        thread = threading.Thread(target=native_thread, name="test-native-launcher")
        thread.start()
        self.addCleanup(lambda: permit_finish.set())
        self.assertTrue(thread_started.wait(1.0))

        task = asyncio.create_task(
            backend._await_native_thread_completion(
                thread,
                cancel_signal=cancel_signal,
                outcome=outcome,
                external_cancel_event=asyncio.Event(),
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)

        self.assertTrue(cancel_signal.is_set())
        self.assertFalse(task.done())
        self.assertTrue(thread.is_alive())
        permit_finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertFalse(thread.is_alive())
        self.assertEqual(
            sequence,
            [
                "create_and_wait_same_thread",
                "job_closed",
                "active_zero",
                "debug_drained",
                "thread_return",
            ],
        )

    async def test_external_cancel_is_cooperative_and_thread_error_is_sanitized(self) -> None:
        backend = importlib.import_module("dayz_mcp.native_launcher_backend")
        cancel_signal = threading.Event()
        external_cancel = asyncio.Event()
        outcome = backend._NativeThreadOutcome()

        def native_thread() -> None:
            cancel_signal.wait(2.0)
            outcome.error = ValueError("sensitive native detail")

        thread = threading.Thread(target=native_thread, name="test-native-launcher")
        thread.start()
        external_cancel.set()
        with self.assertRaisesRegex(
            backend.NativeLauncherBackendError,
            "native_launcher_thread_failed",
        ) as raised:
            await backend._await_native_thread_completion(
                thread,
                cancel_signal=cancel_signal,
                outcome=outcome,
                external_cancel_event=external_cancel,
            )

        self.assertTrue(cancel_signal.is_set())
        self.assertFalse(thread.is_alive())
        self.assertNotIn("sensitive", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
