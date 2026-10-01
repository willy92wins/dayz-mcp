"""inbox 7672 (fb-20260930-212510-7672): publishing over a bundle that is held.

build_native_launcher.py --offline --verify-reproducible failed in
_publish_bundle with a bare WinError 5 on os.replace(output, previous) while a
live MCP client had the previous bundle loaded: load_verified_bundle keeps a
FILE_SHARE_READ handle on every closure file until its session ends, and the
operator had to find that process by hand.

A rename Windows refuses as in use is now retried for a bounded time. Past
it, BundleInUseError names the processes the Restart Manager sees holding the
bundle's files, with the refusal as its cause. Nothing is stopped or
signalled. On every path the last valid bundle stays in output or .previous,
and the .incoming the publish made is removed.
"""

from __future__ import annotations

import ctypes
import errno
import math
import ntpath
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_native_launcher
from tests._tiers import slow_test

_HOLDERS = [
    {"pid": 4242, "image": "python.exe", "command_hint": "dayz_mcp --client"},
    {"pid": 31337, "image": "MsMpEng.exe", "command_hint": None},
]


class _Clock:
    """monotonic and sleep for _publish_bundle: sleeping advances the clock."""

    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _sharing_violation(source: object, destination: object) -> PermissionError:
    # What os.replace raises on a directory with an open file inside.
    return PermissionError(errno.EACCES, "Access is denied", str(source), 5, str(destination))


def _files(root: Path) -> list[str]:
    return sorted(str(path) for path in root.rglob("*") if path.is_file())


class _PublishFixture(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.staging = root / "staging" / "dayz-test-v1"
        self.staging.mkdir(parents=True)
        for name in ("app.pyz", "closure-manifest.json", "dayz-test-launcher.exe", "request-policy.json"):
            (self.staging / name).write_bytes(name.encode() + b" new")
        self.fingerprint = build_native_launcher._artifact_fingerprint(self.staging)
        self.output = root / "native-launchers" / "dayz-test-v1"
        (self.output / "runtime").mkdir(parents=True)
        (self.output / "app.pyz").write_bytes(b"old")
        (self.output / "runtime" / "python.exe").write_bytes(b"old exe")
        self.incoming = self.output.with_name("dayz-test-v1.incoming")
        self.previous = self.output.with_name("dayz-test-v1.previous")
        self.clock = _Clock()
        self.lookups: list[list[str]] = []
        self.renames: list[tuple[Path, Path]] = []
        self.refusals: dict[tuple[Path, Path], float] = {}
        real_replace = os.replace

        def replace(source: object, destination: object) -> None:
            pair = (Path(source), Path(destination))
            self.renames.append(pair)
            left = self.refusals.get(pair, 0)
            if left:
                self.refusals[pair] = left - 1
                raise _sharing_violation(source, destination)
            real_replace(source, destination)

        for patcher in (
            patch.object(build_native_launcher.os, "replace", replace),
            patch.object(build_native_launcher, "verify_bundle", lambda *_a, **_k: None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def refuse(self, source: Path, destination: Path, times: float = math.inf) -> None:
        self.refusals[(source, destination)] = times

    def holders(self, paths: list[str]) -> list[dict[str, object]]:
        self.lookups.append(list(paths))
        return [dict(item) for item in _HOLDERS]

    def publish(self, **overrides: object) -> None:
        options: dict[str, object] = {
            "find_holders": self.holders,
            "monotonic": self.clock.monotonic,
            "sleep": self.clock.sleep,
        }
        options.update(overrides)
        build_native_launcher._publish_bundle(self.staging, self.output, self.fingerprint, **options)

    def siblings(self) -> list[str]:
        return sorted(path.name for path in self.output.parent.iterdir())

    def assert_bundle(self, path: Path, marker: bytes) -> None:
        self.assertEqual((path / "app.pyz").read_bytes(), marker)


class FreeBundleTest(_PublishFixture):
    def test_a_free_bundle_is_swapped_by_the_same_two_renames(self) -> None:
        self.publish()

        self.assertEqual(self.renames, [(self.output, self.previous), (self.incoming, self.output)])
        self.assertEqual(self.clock.slept, [])
        self.assertEqual(self.lookups, [])
        self.assert_bundle(self.output, b"app.pyz new")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])

    def test_a_first_publish_renames_once(self) -> None:
        shutil.rmtree(self.output)

        self.publish()

        self.assertEqual(self.renames, [(self.incoming, self.output)])
        self.assert_bundle(self.output, b"app.pyz new")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])

    def test_another_error_is_raised_at_once_and_leaves_no_incoming(self) -> None:
        failure = OSError(errno.EXDEV, "The system cannot move the file to a different disk drive")
        recording = build_native_launcher.os.replace
        attempts: list[object] = []

        def replace(source: object, destination: object) -> None:
            if Path(source) == self.output:
                attempts.append(source)
                raise failure
            recording(source, destination)

        with patch.object(build_native_launcher.os, "replace", replace):
            with self.assertRaises(OSError) as caught:
                build_native_launcher._publish_bundle(self.staging, self.output, self.fingerprint)

        self.assertIs(caught.exception, failure)
        self.assertEqual(len(attempts), 1)
        self.assert_bundle(self.output, b"old")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])

    def test_an_unbounded_or_negative_budget_is_refused_before_anything_moves(self) -> None:
        for budget in (-1.0, math.inf, math.nan):
            with self.subTest(budget=budget):
                with self.assertRaisesRegex(ValueError, "rename_retry_budget"):
                    self.publish(retry_seconds=budget)
                self.assertEqual(self.renames, [])
                self.assert_bundle(self.output, b"old")
                self.assertEqual(self.siblings(), ["dayz-test-v1"])


class RefusalThatClearsTest(_PublishFixture):
    def test_a_holder_that_lets_go_within_the_budget_does_not_fail_the_publish(self) -> None:
        self.refuse(self.output, self.previous, times=3)

        self.publish()

        self.assertEqual(self.clock.slept, [0.05, 0.1, 0.2])
        self.assertEqual(self.lookups, [])
        self.assert_bundle(self.output, b"app.pyz new")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])

    def test_a_scan_of_the_incoming_copy_is_waited_out_too(self) -> None:
        self.refuse(self.incoming, self.output, times=2)

        self.publish()

        self.assertEqual(self.clock.slept, [0.05, 0.1])
        self.assert_bundle(self.output, b"app.pyz new")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])


class RefusalPastTheBudgetTest(_PublishFixture):
    def test_the_error_names_the_holders_and_keeps_the_refusal_as_its_cause(self) -> None:
        self.refuse(self.output, self.previous)
        held = _files(self.output)

        with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
            self.publish()

        error = caught.exception
        message = str(error)
        self.assertTrue(message.startswith("bundle_in_use"), message)
        self.assertIn("pid 4242 python.exe (dayz_mcp --client)", message)
        self.assertIn("pid 31337 MsMpEng.exe", message)
        self.assertIn("Close or reopen", message)
        self.assertEqual(error.holders, _HOLDERS)
        self.assertEqual((error.source, error.destination), (self.output, self.previous))
        # Code that caught the bare refusal before still catches this.
        self.assertIsInstance(error, PermissionError)
        self.assertIsInstance(error.__cause__, PermissionError)
        self.assertEqual(error.__cause__.winerror, 5)
        # Bounded: the waits add up to the budget, then one lookup of the bundle's files.
        self.assertAlmostEqual(sum(self.clock.slept), build_native_launcher._RENAME_RETRY_SECONDS)
        self.assertEqual(max(self.clock.slept), 1.0)
        self.assertEqual(self.lookups, [held])
        # The last valid bundle never moved.
        self.assert_bundle(self.output, b"old")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])

    def test_a_refused_incoming_rename_puts_the_previous_bundle_back(self) -> None:
        self.refuse(self.incoming, self.output)

        with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
            self.publish()

        self.assertEqual(caught.exception.source, self.incoming)
        self.assertEqual(len(self.lookups), 1)
        self.assertTrue(all(name.startswith(str(self.incoming)) for name in self.lookups[0]))
        self.assert_bundle(self.output, b"old")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])

    def test_when_the_restore_is_refused_too_previous_keeps_the_bundle(self) -> None:
        self.refuse(self.incoming, self.output)
        self.refuse(self.previous, self.output)

        with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
            self.publish()

        error = caught.exception
        self.assertEqual(error.source, self.incoming)
        self.assertEqual(error.__cause__.winerror, 5)
        self.assertIn(str(self.previous), "\n".join(getattr(error, "__notes__", [])))
        self.assert_bundle(self.previous, b"old")
        self.assertEqual(self.siblings(), ["dayz-test-v1.previous"])

        # The next publish moves it back before anything else.
        self.refusals.clear()
        self.publish()

        self.assert_bundle(self.output, b"app.pyz new")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])

    def test_a_refused_repair_of_an_interrupted_publish_leaves_previous_alone(self) -> None:
        self.output.rename(self.previous)
        self.refuse(self.previous, self.output)

        with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
            self.publish()

        self.assertEqual(caught.exception.source, self.previous)
        self.assert_bundle(self.previous, b"old")
        self.assertEqual(self.siblings(), ["dayz-test-v1.previous"])


class UnknownHoldersTest(_PublishFixture):
    def assert_unknown(self, error: BaseException, reason: str) -> None:
        message = str(error)
        self.assertTrue(message.startswith("bundle_in_use"), message)
        self.assertIn("holders unknown", message)
        self.assertIn(reason, message)
        self.assertIn("Close or reopen", message)
        # The refusal is still the cause: the diagnosis never masks it.
        self.assertIsInstance(error.__cause__, PermissionError)
        self.assertEqual(error.__cause__.winerror, 5)
        self.assert_bundle(self.output, b"old")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])

    def test_a_failing_lookup_degrades_to_unknown_holders(self) -> None:
        def broken(_paths: list[str]) -> list[dict[str, object]]:
            raise OSError("restart manager down")

        self.refuse(self.output, self.previous)
        with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
            self.publish(find_holders=broken)

        self.assertIsNone(caught.exception.holders)
        self.assert_unknown(caught.exception, "restart manager down")

    def test_a_lookup_that_names_nobody_is_unknown_too(self) -> None:
        self.refuse(self.output, self.previous)
        with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
            self.publish(find_holders=lambda _paths: [])

        self.assertEqual(caught.exception.holders, [])
        self.assert_unknown(caught.exception, "named no process")

    def test_a_malformed_holder_list_is_not_trusted(self) -> None:
        self.refuse(self.output, self.previous)
        with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
            self.publish(find_holders=lambda _paths: [{"pid": "4242", "image": "python.exe"}])

        self.assertIsNone(caught.exception.holders)
        self.assert_unknown(caught.exception, "malformed")

    def test_the_default_lookup_degrades_when_the_restart_manager_cannot_load(self) -> None:
        self.refuse(self.output, self.previous)
        with patch.object(
            build_native_launcher,
            "_restart_manager",
            side_effect=OSError("rstrtmgr.dll unavailable"),
        ):
            with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
                self.publish(find_holders=None)

        self.assertIsNone(caught.exception.holders)
        self.assert_unknown(caught.exception, "rstrtmgr.dll unavailable")


class InUseTest(unittest.TestCase):
    def test_only_access_denied_and_sharing_violations_count_as_in_use(self) -> None:
        class OtherOSError(OSError):
            pass

        cases = [
            (PermissionError(errno.EACCES, "Access is denied", "a", 5, "b"), True),
            (OSError(None, "The process cannot access the file", "a", 32), True),
            (OtherOSError(errno.EIO, "a subclass that still carries the code", "a", 32), True),
            (OSError(errno.EXDEV, "The system cannot move the file to a different disk drive"), False),
            (FileNotFoundError(errno.ENOENT, "missing"), False),
        ]
        for error, expected in cases:
            with self.subTest(error=error):
                self.assertIs(build_native_launcher._refused_in_use(error), expected)


@unittest.skipUnless(sys.platform == "win32", "OpenProcess is a Windows API")
class HolderNamingTest(unittest.TestCase):
    def test_a_process_that_did_not_start_when_the_holder_did_keeps_the_restart_manager_names(self) -> None:
        # This process did not start at FILETIME 1: a reused pid must not lend
        # its image or command line to the holder.
        holder = build_native_launcher._named_holder(os.getpid(), 1, "Python", "")

        self.assertEqual(holder, {"pid": os.getpid(), "image": "Python", "command_hint": None})

    def test_a_process_that_cannot_be_opened_keeps_the_restart_manager_names(self) -> None:
        holder = build_native_launcher._named_holder(0, 0, "Windows Search", "WSearch")

        self.assertEqual(holder, {"pid": 0, "image": "Windows Search", "command_hint": "service WSearch"})

    def test_without_any_name_the_image_is_unknown(self) -> None:
        holder = build_native_launcher._named_holder(0, 0, "", "")

        self.assertEqual(holder, {"pid": 0, "image": "unknown", "command_hint": None})


class CommandHintTest(unittest.TestCase):
    def test_the_hint_names_the_program_and_never_an_option_value(self) -> None:
        cases = [
            (
                [r"C:\venv\Scripts\python.exe", "-m", "dayz_mcp", "--client", "--port", "8765",
                 "--keyfile", r"C:\keys\daemon.key", "--client-platform", "codex"],
                "dayz_mcp --client",
            ),
            (["python.exe", "-m", "dayz_mcp", "--daemon", "--port", "8765"], "dayz_mcp --daemon"),
            (["python.exe", "-B", "-m", "unittest", "tests.test_x"], "unittest"),
            (["python.exe", r"C:\tools\build_native_launcher.py", "--offline"], "build_native_launcher.py"),
            (["python.exe", "-I", r"C:\bundle\app.pyz", "--token", "s3cret"], "app.pyz"),
            (["python.exe", "-c", "import os; os.getpid()"], None),
            (["python.exe", "-m", "not a module name", "--client"], None),
            ([r"C:\Windows\explorer.exe"], None),
            ([], None),
            (None, None),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertEqual(build_native_launcher._command_hint(argv), expected)


class RestartManagerBindingTest(unittest.TestCase):
    def test_the_structures_match_restartmanager_h(self) -> None:
        # RestartManager.h:87-102, Windows SDK 10.0.26100.0. Checked with cl.exe
        # static_asserts on 2026-10-01: sizeof(RM_PROCESS_INFO) == 668 at these offsets.
        info = build_native_launcher._RM_PROCESS_INFO
        self.assertEqual(ctypes.sizeof(build_native_launcher._RM_UNIQUE_PROCESS), 12)
        self.assertEqual(ctypes.sizeof(info), 668)
        self.assertEqual(
            [
                info.strAppName.offset,
                info.strServiceShortName.offset,
                info.ApplicationType.offset,
                info.AppStatus.offset,
                info.TSSessionId.offset,
                info.bRestartable.offset,
            ],
            [12, 524, 652, 656, 660, 664],
        )

    def test_the_session_is_ended_when_the_list_cannot_be_read(self) -> None:
        ended: list[int] = []

        class Library:
            def RmStartSession(self, session: object, flags: int, key: object) -> int:
                session._obj.value = 7  # type: ignore[attr-defined]
                return 0

            def RmRegisterResources(self, session: int, count: int, names: object, *_rest: object) -> int:
                return 0

            def RmGetList(self, *_args: object) -> int:
                return 87  # ERROR_INVALID_PARAMETER

            def RmEndSession(self, session: int) -> int:
                ended.append(session)
                return 0

        with patch.object(build_native_launcher, "_restart_manager", return_value=Library()):
            with self.assertRaises(OSError) as caught:
                build_native_launcher._restart_manager_holders([r"C:\bundle\app.pyz"])

        self.assertEqual(caught.exception.winerror, 87)
        self.assertEqual(ended, [7])

    def test_a_list_that_outgrows_the_first_buffer_is_read_again(self) -> None:
        capacities: list[int] = []

        class Library:
            def RmStartSession(self, session: object, flags: int, key: object) -> int:
                session._obj.value = 3  # type: ignore[attr-defined]
                return 0

            def RmRegisterResources(self, session: int, count: int, names: object, *_rest: object) -> int:
                self.registered = [names[index] for index in range(count)]  # type: ignore[index]
                return 0

            def RmGetList(self, session: int, needed: object, filled: object, infos: object, reasons: object) -> int:
                capacities.append(filled._obj.value)  # type: ignore[attr-defined]
                needed._obj.value = 20  # type: ignore[attr-defined]
                if filled._obj.value < 20:  # type: ignore[attr-defined]
                    return 234  # ERROR_MORE_DATA
                for index in range(20):
                    infos[index].Process.dwProcessId = 1000 + index  # type: ignore[index]
                    infos[index].strAppName = f"app{index}"  # type: ignore[index]
                filled._obj.value = 20  # type: ignore[attr-defined]
                return 0

            def RmEndSession(self, session: int) -> int:
                return 0

        library = Library()

        def named(pid: int, started: int, app_name: str, service: str) -> dict[str, object]:
            return {"pid": pid, "image": app_name, "command_hint": None}

        with (
            patch.object(build_native_launcher, "_restart_manager", return_value=library),
            patch.object(build_native_launcher, "_named_holder", named),
        ):
            holders = build_native_launcher._restart_manager_holders([r"C:\bundle\app.pyz", r"C:\bundle\x.exe"])

        self.assertEqual(library.registered, [r"C:\bundle\app.pyz", r"C:\bundle\x.exe"])
        self.assertEqual(capacities, [16, 20])
        self.assertEqual([item["pid"] for item in holders], list(range(1000, 1020)))
        self.assertEqual(holders[19]["image"], "app19")


@unittest.skipUnless(sys.platform == "win32", "the Restart Manager is a Windows API")
class RestartManagerOnThisHostTest(unittest.TestCase):
    """The real API, on a file this process holds open. Never another process."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.target = self.root / "held.bin"
        self.target.write_bytes(b"held")
        try:
            build_native_launcher._restart_manager_holders([str(self.target)])
        except OSError as error:
            self.skipTest(f"the Restart Manager cannot run on this host: {error}")

    def bundle(self) -> tuple[Path, Path, dict[str, str]]:
        staging = self.root / "staging" / "dayz-test-v1"
        staging.mkdir(parents=True)
        for name in ("app.pyz", "closure-manifest.json", "dayz-test-launcher.exe", "request-policy.json"):
            (staging / name).write_bytes(name.encode() + b" new")
        output = self.root / "native-launchers" / "dayz-test-v1"
        output.mkdir(parents=True)
        for name in ("app.pyz", "closure-manifest.json", "dayz-test-launcher.exe"):
            (output / name).write_bytes(b"old")
        patcher = patch.object(build_native_launcher, "verify_bundle", lambda *_a, **_k: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        return staging, output, build_native_launcher._artifact_fingerprint(staging)

    @slow_test
    def test_a_handle_this_process_keeps_open_is_named(self) -> None:
        import psutil

        me = psutil.Process(os.getpid())
        with self.target.open("rb"):
            holders = build_native_launcher._restart_manager_holders([str(self.target)])
        after = build_native_launcher._restart_manager_holders([str(self.target)])

        mine = [item for item in holders if item["pid"] == os.getpid()]
        self.assertEqual(len(mine), 1, holders)
        self.assertEqual(mine[0]["image"], ntpath.basename(me.exe()))
        self.assertEqual(mine[0]["command_hint"], build_native_launcher._command_hint(me.cmdline()))
        self.assertNotIn(os.getpid(), [item["pid"] for item in after])

    @slow_test
    def test_publishing_over_a_bundle_this_process_holds_names_it(self) -> None:
        from dayz_mcp.launcher_registry import _open_pinned_read

        staging, output, fingerprint = self.bundle()

        # The handles a live MCP client keeps: load_verified_bundle opens the
        # manifest and every closure file, and the opened launcher its exe,
        # through _open_pinned_read (GENERIC_READ, FILE_SHARE_READ only).
        with (
            _open_pinned_read(output / "closure-manifest.json"),
            _open_pinned_read(output / "dayz-test-launcher.exe"),
        ):
            with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
                build_native_launcher._publish_bundle(staging, output, fingerprint, retry_seconds=0.0)

        error = caught.exception
        self.assertIn(os.getpid(), [item["pid"] for item in error.holders])
        self.assertIn(f"pid {os.getpid()} ", str(error))
        self.assertIn(getattr(error.__cause__, "winerror", None), (5, 32))
        self.assertEqual((output / "app.pyz").read_bytes(), b"old")
        self.assertEqual(sorted(path.name for path in output.parent.iterdir()), ["dayz-test-v1"])

        # Released: the same publish goes through.
        build_native_launcher._publish_bundle(staging, output, fingerprint, retry_seconds=0.0)

        self.assertEqual((output / "app.pyz").read_bytes(), b"app.pyz new")
        self.assertEqual(sorted(path.name for path in output.parent.iterdir()), ["dayz-test-v1"])

    @slow_test
    def test_a_real_holder_that_lets_go_within_the_budget_does_not_fail_the_publish(self) -> None:
        staging, output, fingerprint = self.bundle()
        handle = (output / "app.pyz").open("rb")
        self.addCleanup(handle.close)
        refusals: list[OSError] = []
        real_replace = os.replace

        def replace(source: object, destination: object) -> None:
            try:
                real_replace(source, destination)
            except OSError as error:
                refusals.append(error)
                handle.close()  # the holder lets go while the publish waits
                raise

        with patch.object(build_native_launcher.os, "replace", replace):
            build_native_launcher._publish_bundle(staging, output, fingerprint, retry_seconds=10.0)

        self.assertEqual(len(refusals), 1, refusals)
        self.assertIsInstance(refusals[0], PermissionError)
        self.assertEqual((output / "app.pyz").read_bytes(), b"app.pyz new")
        self.assertEqual(sorted(path.name for path in output.parent.iterdir()), ["dayz-test-v1"])


if __name__ == "__main__":
    unittest.main()
