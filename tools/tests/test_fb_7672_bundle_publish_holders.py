"""inbox 7672 (fb-20260930-212510-7672): publishing over a bundle that is held.

build_native_launcher.py --offline --verify-reproducible failed in
_publish_bundle with a bare WinError 5 on os.replace(output, previous) while a
live MCP client had the previous bundle loaded: load_verified_bundle keeps a
FILE_SHARE_READ handle on every closure file until its session ends, and the
operator had to find that process by hand.

A step Windows refuses as in use (a rename, or removing a leftover copy) is
now retried for a bounded time. Past it, BundleInUseError names the tree, the
refusal and the processes the Restart Manager sees holding its files, with the
refusal as its cause. Nothing is stopped or signalled. On every path the last
valid bundle stays in output or .previous; a refused second rename puts it back
at once, so every wait happens with output in place. If that put-back is
refused too, the publish stops at once with no wait at all: the bundle stays in
.previous, the error says so, and the next publish moves it back first. A copy
that cannot be removed is reported on the error, and the next publish removes
it first.
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
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import build_native_launcher
from tests._tiers import slow_test

_HOLDERS = [
    {"pid": 4242, "image": "python.exe", "command_hint": "dayz_mcp --client"},
    {"pid": 31337, "image": "MsMpEng.exe", "command_hint": None},
]
_HINTS = (None, "dayz_mcp --client", "dayz_mcp --daemon", "dayz_mcp --embedded")


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


def _held_file(tree: Path) -> OSError:
    # What shutil.rmtree raises when a file inside is open without FILE_SHARE_DELETE.
    return PermissionError(errno.EACCES, "The file is in use", str(tree / "app.pyz"), 32)


def _not_empty_yet(tree: Path) -> OSError:
    # A deleted file another process still has open keeps its directory non-empty.
    return OSError(errno.ENOTEMPTY, "The directory is not empty", str(tree), 145)


def _files(root: Path) -> list[str]:
    return sorted(str(path) for path in root.rglob("*") if path.is_file())


def _notes(error: BaseException) -> str:
    return "\n".join(getattr(error, "__notes__", []))


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
        # pair -> (renames still let through, refusals after them)
        self.refusals: dict[tuple[Path, Path], tuple[int, float]] = {}
        self.removal_refusals: dict[Path, float] = {}
        self.removal_error = _held_file
        # (output exists, .previous exists) at every wait.
        self.states: list[tuple[bool, bool]] = []
        # In order: ("rename", pair, refused) and ("sleep", output exists, .previous exists).
        self.events: list[tuple[object, ...]] = []
        real_replace = os.replace
        real_rmtree = shutil.rmtree

        def replace(source: object, destination: object) -> None:
            pair = (Path(source), Path(destination))
            through, refusals = self.refusals.get(pair, (0, 0))
            refused = not through and refusals > 0
            if through:
                self.refusals[pair] = (through - 1, refusals)
            elif refused:
                self.refusals[pair] = (0, refusals - 1)
            self.renames.append(pair)
            self.events.append(("rename", pair, refused))
            if refused:
                raise _sharing_violation(source, destination)
            real_replace(source, destination)

        def rmtree(path: object, *args: object, **kwargs: object) -> None:
            # A held tree: each removal that raises is one refusal; ignoring the
            # errors leaves the tree as it is and does not make the holder let go.
            tree = Path(path)  # type: ignore[arg-type]
            left = self.removal_refusals.get(tree, 0)
            if left:
                if kwargs.get("ignore_errors") or (args and args[0] is True):
                    return
                self.removal_refusals[tree] = left - 1
                raise self.removal_error(tree)
            real_rmtree(path, *args, **kwargs)  # type: ignore[arg-type]

        for patcher in (
            patch.object(build_native_launcher.os, "replace", replace),
            patch.object(build_native_launcher.shutil, "rmtree", rmtree),
            patch.object(build_native_launcher, "verify_bundle", lambda *_a, **_k: None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def refuse(self, source: Path, destination: Path, times: float = math.inf, after: int = 0) -> None:
        self.refusals[(source, destination)] = (after, times)

    def refuse_removal(self, tree: Path, times: float = math.inf) -> None:
        self.removal_refusals[tree] = times

    def holders(self, paths: list[str]) -> list[dict[str, object]]:
        self.lookups.append(list(paths))
        return [dict(item) for item in _HOLDERS]

    def sleep(self, seconds: float) -> None:
        state = (self.output.exists(), self.previous.exists())
        self.states.append(state)
        self.events.append(("sleep", *state))
        self.clock.sleep(seconds)

    def publish(self, **overrides: object) -> None:
        options: dict[str, object] = {
            "find_holders": self.holders,
            "monotonic": self.clock.monotonic,
            "sleep": self.sleep,
        }
        options.update(overrides)
        fingerprint = options.pop("fingerprint", self.fingerprint)
        build_native_launcher._publish_bundle(self.staging, self.output, fingerprint, **options)

    def siblings(self) -> list[str]:
        return sorted(path.name for path in self.output.parent.iterdir())

    def assert_bundle(self, path: Path, marker: bytes) -> None:
        self.assertEqual((path / "app.pyz").read_bytes(), marker)

    def assert_refusal(self, error: BaseException, winerror: int) -> None:
        # The refusal is the cause, and its codes are the error's own.
        self.assertIsInstance(error, PermissionError)
        self.assertIsInstance(error.__cause__, OSError)
        self.assertEqual(error.__cause__.winerror, winerror)
        self.assertEqual(error.winerror, winerror)  # type: ignore[attr-defined]
        self.assertEqual(error.errno, error.__cause__.errno)  # type: ignore[attr-defined]


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
        self.assertEqual(self.states, [(True, False)] * 3)
        self.assertEqual(self.lookups, [])
        self.assert_bundle(self.output, b"app.pyz new")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])

    def test_a_refused_second_rename_puts_output_back_before_it_waits(self) -> None:
        # Review round 1, F2: a reader meanwhile must find the last bundle, never none.
        self.refuse(self.incoming, self.output, times=2)

        self.publish()

        self.assertEqual(self.states, [(True, False)] * 2)
        self.assertEqual(self.clock.slept, [0.05, 0.1])
        swap = [(self.output, self.previous), (self.incoming, self.output)]
        put_back = [(self.previous, self.output)]
        self.assertEqual(self.renames, (swap + put_back) * 2 + swap)
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
        self.assertIn(f"renaming {self.output} to dayz-test-v1.previous", message)
        self.assertIn("pid 4242 python.exe (dayz_mcp --client)", message)
        self.assertIn("pid 31337 MsMpEng.exe", message)
        self.assertIn("Close or reopen", message)
        self.assertEqual(error.holders, _HOLDERS)
        self.assertEqual(error.path, self.output)
        self.assert_refusal(error, 5)
        # Bounded: the waits add up to the budget, then one lookup of the refused tree.
        self.assertAlmostEqual(sum(self.clock.slept), build_native_launcher._RENAME_RETRY_SECONDS)
        self.assertEqual(max(self.clock.slept), 1.0)
        self.assertEqual(self.lookups, [held])
        # The last valid bundle never moved.
        self.assert_bundle(self.output, b"old")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])

    def test_a_second_rename_refused_past_the_budget_waits_with_output_in_place(self) -> None:
        self.refuse(self.incoming, self.output)
        held = _files(self.staging)

        with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
            self.publish()

        self.assertTrue(self.states)
        self.assertEqual(set(self.states), {(True, False)})
        # One budget for the whole swap, not one per rename.
        self.assertAlmostEqual(sum(self.clock.slept), build_native_launcher._RENAME_RETRY_SECONDS)
        error = caught.exception
        self.assertEqual(error.path, self.incoming)
        self.assertIn(f"renaming {self.incoming} to dayz-test-v1", str(error))
        self.assert_refusal(error, 5)
        self.assertEqual(self.lookups, [[name.replace(str(self.staging), str(self.incoming)) for name in held]])
        self.assert_bundle(self.output, b"old")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])

    def test_a_refused_put_back_stops_at_once_and_no_wait_follows_it(self) -> None:
        # Review round 2, F1: no sleep may happen while output is missing. Two
        # put-backs go through, each wait comes with output in place; the third
        # is refused, tried once, and nothing waits after it.
        self.refuse(self.incoming, self.output)
        self.refuse(self.previous, self.output, after=2)

        with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
            self.publish()

        put_backs = [
            index for index, event in enumerate(self.events) if event[:2] == ("rename", (self.previous, self.output))
        ]
        self.assertEqual([self.events[index][2] for index in put_backs], [False, False, True])
        self.assertEqual([event for event in self.events if event[0] == "sleep"], [("sleep", True, False)] * 2)
        self.assertNotIn("sleep", [event[0] for event in self.events[put_backs[-1]:]])
        # The refusal that started it is the error; the put-back refusal is a note.
        error = caught.exception
        self.assertEqual(error.path, self.incoming)
        self.assert_refusal(error, 5)
        notes = _notes(error)
        self.assertIn(f"putting the last valid bundle back was refused: renaming {self.previous} to dayz-test-v1", notes)
        self.assertIn("WinError 5", notes)
        self.assertIn("pid 4242 python.exe (dayz_mcp --client)", notes)
        self.assertIn(f"the last valid bundle is in {self.previous}", notes)
        self.assertIn("the next publish moves it back first", notes)
        self.assertEqual(self.lookups[-1], _files(self.previous))
        self.assertFalse(self.output.exists())
        self.assert_bundle(self.previous, b"old")
        self.assertEqual(self.siblings(), ["dayz-test-v1.previous"])

        # Released: the next publish moves it back before anything else.
        self.refusals.clear()
        self.publish()

        self.assert_bundle(self.output, b"app.pyz new")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])

    def test_a_refused_put_back_leaves_a_held_copy_without_waiting_for_it(self) -> None:
        self.refuse(self.incoming, self.output)
        self.refuse(self.previous, self.output)
        self.refuse_removal(self.incoming)

        with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
            self.publish()

        self.assertEqual(self.clock.slept, [])
        self.assertEqual(self.renames.count((self.previous, self.output)), 1)
        notes = _notes(caught.exception)
        self.assertIn(f"the copy in {self.incoming} could not be removed", notes)
        self.assertIn("WinError 32", notes)
        self.assertIn("the next publish removes it first", notes)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.siblings(), ["dayz-test-v1.incoming", "dayz-test-v1.previous"])

        # Released: the next publish puts the bundle back, removes the copy and publishes.
        self.refusals.clear()
        self.removal_refusals.clear()
        self.publish()

        self.assert_bundle(self.output, b"app.pyz new")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])

    def test_a_refused_repair_of_an_interrupted_publish_leaves_previous_alone(self) -> None:
        self.output.rename(self.previous)
        self.refuse(self.previous, self.output)

        with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
            self.publish()

        self.assertEqual(caught.exception.path, self.previous)
        self.assert_bundle(self.previous, b"old")
        self.assertEqual(self.siblings(), ["dayz-test-v1.previous"])


class SwapFailureTest(_PublishFixture):
    """The second rename failing for another reason still puts the bundle back."""

    def fail_second_rename(self, failure: BaseException) -> None:
        recording = build_native_launcher.os.replace

        def replace(source: object, destination: object) -> None:
            if (Path(source), Path(destination)) == (self.incoming, self.output):
                raise failure
            recording(source, destination)

        patcher = patch.object(build_native_launcher.os, "replace", replace)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_interrupt_between_the_two_renames_puts_the_bundle_back(self) -> None:
        self.fail_second_rename(KeyboardInterrupt())

        with self.assertRaises(KeyboardInterrupt):
            self.publish()

        self.assertEqual(self.clock.slept, [])
        self.assertEqual(self.renames[-1], (self.previous, self.output))
        self.assert_bundle(self.output, b"old")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])

    def test_another_error_on_the_second_rename_is_raised_unchanged_after_the_put_back(self) -> None:
        failure = OSError(errno.EIO, "The request could not be performed because of an I/O device error")
        self.fail_second_rename(failure)

        with self.assertRaises(OSError) as caught:
            self.publish()

        self.assertIs(caught.exception, failure)
        self.assertEqual(self.clock.slept, [])
        self.assertEqual(self.lookups, [])
        self.assert_bundle(self.output, b"old")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])


class LeftoverCopyTest(_PublishFixture):
    """Review round 1, F3: a copy that cannot be removed is never hidden."""

    def test_a_copy_held_for_a_moment_is_removed_once_it_is_free(self) -> None:
        self.refuse_removal(self.incoming, times=2)

        with self.assertRaisesRegex(ValueError, "final_copy_mismatch"):
            self.publish(fingerprint=dict(self.fingerprint, app_pyz_sha256="0" * 64))

        self.assertEqual(self.clock.slept, [0.05, 0.1])
        self.assert_bundle(self.output, b"old")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])

    def test_a_copy_that_stays_is_named_on_the_error_it_follows(self) -> None:
        self.refuse_removal(self.incoming)

        with self.assertRaisesRegex(ValueError, "final_copy_mismatch") as caught:
            self.publish(fingerprint=dict(self.fingerprint, app_pyz_sha256="0" * 64))

        notes = _notes(caught.exception)
        self.assertIn(f"removing {self.incoming}", notes)
        self.assertIn("WinError 32", notes)
        self.assertIn("pid 4242 python.exe (dayz_mcp --client)", notes)
        self.assertIn("the next publish removes it first", notes)
        self.assertEqual(self.lookups, [_files(self.incoming)])
        self.assert_bundle(self.output, b"old")

    def test_a_copy_that_stays_after_a_refused_swap_keeps_the_refusal_first(self) -> None:
        self.refuse(self.output, self.previous)
        self.refuse_removal(self.incoming)

        with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
            self.publish()

        error = caught.exception
        self.assertEqual(error.path, self.output)
        self.assert_refusal(error, 5)
        self.assertIn(f"removing {self.incoming}", _notes(error))
        self.assertIn("WinError 32", _notes(error))
        self.assert_bundle(self.output, b"old")

    def test_a_stale_copy_that_stays_stops_the_next_publish_naming_it(self) -> None:
        for stale in (self.incoming, self.previous):
            with self.subTest(stale=stale.name):
                stale.mkdir()
                (stale / "app.pyz").write_bytes(b"stale")
                self.refuse_removal(stale)
                self.lookups.clear()

                with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
                    self.publish()

                error = caught.exception
                self.assertEqual(error.path, stale)
                self.assertIn(f"removing {stale}", str(error))
                self.assertIn("pid 4242 python.exe (dayz_mcp --client)", str(error))
                self.assert_refusal(error, 32)
                self.assertEqual(self.lookups, [[str(stale / "app.pyz")]])
                self.assert_bundle(stale, b"stale")
                self.assert_bundle(self.output, b"old")
                self.removal_refusals.clear()
                shutil.rmtree(stale)

    def test_a_previous_bundle_that_stays_after_the_swap_is_reported_as_published(self) -> None:
        self.refuse_removal(self.previous)

        with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
            self.publish()

        error = caught.exception
        self.assertEqual(error.path, self.previous)
        self.assert_refusal(error, 32)
        self.assertIn(f"already published in {self.output}", _notes(error))
        self.assert_bundle(self.output, b"app.pyz new")

    def test_a_directory_that_is_not_empty_yet_is_waited_out_like_a_held_file(self) -> None:
        self.incoming.mkdir()
        (self.incoming / "app.pyz").write_bytes(b"stale")
        self.removal_error = _not_empty_yet
        self.refuse_removal(self.incoming, times=2)

        self.publish()

        self.assertEqual(self.clock.slept, [0.05, 0.1])
        self.assert_bundle(self.output, b"app.pyz new")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])


class UnknownHoldersTest(_PublishFixture):
    def assert_unknown(self, error: BaseException, reason: str) -> None:
        message = str(error)
        self.assertTrue(message.startswith("bundle_in_use"), message)
        self.assertIn("holders unknown", message)
        self.assertIn(reason, message)
        self.assertIn("Close or reopen", message)
        # The refusal is still the cause: the diagnosis never masks it.
        self.assert_refusal(error, 5)
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
            (_not_empty_yet(Path("a")), False),
        ]
        for error, expected in cases:
            with self.subTest(error=error):
                self.assertIs(build_native_launcher._refused_in_use(error), expected)


@unittest.skipUnless(sys.platform == "win32", "OpenProcess is a Windows API")
class HolderNamingTest(unittest.TestCase):
    def test_a_process_that_did_not_start_when_the_holder_did_is_not_named(self) -> None:
        # This process did not start at FILETIME 1: a reused pid must not lend
        # its image or command line to the holder.
        holder = build_native_launcher._named_holder(os.getpid(), 1, "")

        self.assertEqual(holder, {"pid": os.getpid(), "image": "unknown", "command_hint": None})

    def test_a_service_that_cannot_be_opened_keeps_its_service_name(self) -> None:
        holder = build_native_launcher._named_holder(0, 0, "WSearch")

        self.assertEqual(holder, {"pid": 0, "image": "unknown", "command_hint": "service WSearch"})

    def test_without_any_name_the_image_is_unknown(self) -> None:
        holder = build_native_launcher._named_holder(0, 0, "")

        self.assertEqual(holder, {"pid": 0, "image": "unknown", "command_hint": None})

    def test_a_foreign_command_line_never_reaches_the_error(self) -> None:
        # Review round 1, F1: python.exe -W secreto_token.py worker.py is a valid
        # command line whose option value looks like a script.
        argv = [r"C:\Python314\python.exe", "-W", "secreto_token.py", "worker.py"]
        with patch.object(
            build_native_launcher,
            "_image_and_argv",
            return_value=(r"C:\Python314\python.exe", argv),
        ):
            holder = build_native_launcher._named_holder(4242, 5, "")
        error = build_native_launcher.BundleInUseError(
            "renaming a to b", Path("a"), 10.0, [holder], None, _sharing_violation("a", "b")
        )

        self.assertEqual(holder, {"pid": 4242, "image": "python.exe", "command_hint": None})
        self.assertNotIn("secreto", str(error))


class CommandHintTest(unittest.TestCase):
    def test_only_a_dayz_mcp_invocation_gets_a_hint(self) -> None:
        cases = [
            (
                [r"C:\venv\Scripts\python.exe", "-m", "dayz_mcp", "--client", "--port", "8765",
                 "--keyfile", r"C:\keys\token.py", "--client-platform", "codex"],
                "dayz_mcp --client",
            ),
            (["python.exe", "-m", "dayz_mcp", "--daemon", "--port", "8765"], "dayz_mcp --daemon"),
            (["python.exe", "-B", "-I", "-m", "dayz_mcp", "--embedded"], "dayz_mcp --embedded"),
            (["python.exe", "-Wignore", "-X", "utf8", "-m", "dayz_mcp", "--client"], "dayz_mcp --client"),
            (["python.exe", "-m", "dayz_mcp"], None),
            (["python.exe", "-m", "dayz_mcp", "--client", "--daemon"], None),
            (["python.exe", "-m", "dayz_mcp", "--client", "--client"], None),
            (["python.exe", "-m", "dayz_mcp.server", "--client"], None),
            (["python.exe", "-mdayz_mcp", "--client"], None),
            (["python.exe", "-B", "-m", "unittest", "tests.test_x"], None),
            (["python.exe", r"C:\tools\build_native_launcher.py", "--offline"], None),
            (["python.exe", "-I", "-B", "-S", r"C:\bundle\app.pyz", "--lifecycle-child"], None),
            ([r"C:\Windows\explorer.exe"], None),
            ([], None),
            (None, None),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertEqual(build_native_launcher._command_hint(argv), expected)

    def test_an_option_value_that_looks_like_a_script_is_never_taken_for_one(self) -> None:
        # Review round 1, F1. Each value is skipped as the option's value: it is
        # neither shown nor allowed to stand for the module switch.
        cases = [
            (["python.exe", "-W", "secreto_token.py", "worker.py"], None),
            (["python.exe", "-X", "secreto.pyz", "-m", "dayz_mcp", "--client"], "dayz_mcp --client"),
            (["python.exe", "--check-hash-based-pycs", "secreto.py", "-m", "dayz_mcp", "--daemon"],
             "dayz_mcp --daemon"),
            (["python.exe", "-W", "-m", "dayz_mcp", "--client"], None),
            (["python.exe", "worker.py", "-m", "dayz_mcp", "--client"], None),
            (["python.exe", "-c", "pass", "-m", "dayz_mcp", "--client"], None),
            (["python.exe", "-W"], None),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                hint = build_native_launcher._command_hint(argv)
                self.assertEqual(hint, expected)
                self.assertIn(hint, _HINTS)


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
                    infos[index].strServiceShortName = f"svc{index}"  # type: ignore[index]
                filled._obj.value = 20  # type: ignore[attr-defined]
                return 0

            def RmEndSession(self, session: int) -> int:
                return 0

        library = Library()

        def named(pid: int, started: int, service: str) -> dict[str, object]:
            return {"pid": pid, "image": "python.exe", "command_hint": service}

        with (
            patch.object(build_native_launcher, "_restart_manager", return_value=library),
            patch.object(build_native_launcher, "_named_holder", named),
        ):
            holders = build_native_launcher._restart_manager_holders([r"C:\bundle\app.pyz", r"C:\bundle\x.exe"])

        self.assertEqual(library.registered, [r"C:\bundle\app.pyz", r"C:\bundle\x.exe"])
        self.assertEqual(capacities, [16, 20])
        self.assertEqual([item["pid"] for item in holders], list(range(1000, 1020)))
        self.assertEqual(holders[19]["command_hint"], "svc19")


def _real_bundle(root: Path) -> tuple[Path, Path, dict[str, str]]:
    staging = root / "staging" / "dayz-test-v1"
    staging.mkdir(parents=True)
    for name in ("app.pyz", "closure-manifest.json", "dayz-test-launcher.exe", "request-policy.json"):
        (staging / name).write_bytes(name.encode() + b" new")
    output = root / "native-launchers" / "dayz-test-v1"
    output.mkdir(parents=True)
    for name in ("app.pyz", "closure-manifest.json", "dayz-test-launcher.exe"):
        (output / name).write_bytes(b"old")
    return staging, output, build_native_launcher._artifact_fingerprint(staging)


@unittest.skipUnless(sys.platform == "win32", "a real sharing violation is Windows behaviour")
class HeldIncomingCopyTest(unittest.TestCase):
    """Review rounds 1 and 2: real handles on files inside .incoming and .previous.

    The holder lookup is injected, so this runs where the Restart Manager does not.
    """

    def setUp(self) -> None:
        from dayz_mcp.launcher_registry import _open_pinned_read

        self.open_pinned = _open_pinned_read
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.staging, self.output, self.fingerprint = _real_bundle(Path(directory.name))
        self.incoming = self.output.with_name("dayz-test-v1.incoming")
        self.previous = self.output.with_name("dayz-test-v1.previous")
        self.hold = True
        self.handles: list[object] = []
        self.addCleanup(self.release)
        self.lookups: list[list[str]] = []
        self.states: list[tuple[bool, bool]] = []

        def verify(path: Path, **_kwargs: object) -> None:
            # Held the way a live MCP client holds a bundle: FILE_SHARE_READ only.
            if self.hold and path.name.endswith(".incoming") and not self.handles:
                self.handles.append(_open_pinned_read(path / "app.pyz"))

        patcher = patch.object(build_native_launcher, "verify_bundle", verify)
        patcher.start()
        self.addCleanup(patcher.stop)

    def hold_previous_before_the_second_rename(self) -> None:
        # A reader that opens the old bundle under .previous after output moved
        # there, before the refused .incoming -> output rename (the reviewer's case).
        real_replace = os.replace

        def replace(source: object, destination: object) -> None:
            if (
                self.hold
                and (Path(source), Path(destination)) == (self.incoming, self.output)
                and len(self.handles) == 1
            ):
                self.handles.append(self.open_pinned(self.previous / "app.pyz"))
            real_replace(source, destination)

        patcher = patch.object(build_native_launcher.os, "replace", replace)
        patcher.start()
        self.addCleanup(patcher.stop)

    def release(self) -> None:
        self.hold = False
        while self.handles:
            self.handles.pop().close()  # type: ignore[attr-defined]

    def holders(self, paths: list[str]) -> list[dict[str, object]]:
        self.lookups.append(list(paths))
        return []

    def sleep(self, seconds: float) -> None:
        self.states.append((self.output.exists(), self.previous.exists()))
        time.sleep(seconds)

    def publish(self) -> None:
        build_native_launcher._publish_bundle(
            self.staging,
            self.output,
            self.fingerprint,
            retry_seconds=0.3,
            find_holders=self.holders,
            sleep=self.sleep,
        )

    def siblings(self) -> list[str]:
        return sorted(path.name for path in self.output.parent.iterdir())

    @slow_test
    def test_waits_for_a_held_incoming_copy_happen_with_output_in_place(self) -> None:
        with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
            self.publish()

        self.assertTrue(self.states)
        self.assertEqual(set(self.states), {(True, False)})
        self.assertEqual(caught.exception.path, self.incoming)
        self.assertEqual(caught.exception.__cause__.winerror, 5)
        self.assertEqual((self.output / "app.pyz").read_bytes(), b"old")
        self.assertFalse(self.previous.exists())

    @slow_test
    def test_a_held_leftover_is_reported_now_and_named_by_the_next_publish(self) -> None:
        with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
            self.publish()

        held = str(self.incoming / "app.pyz")
        notes = _notes(caught.exception)
        self.assertIn(f"removing {self.incoming}", notes)
        self.assertIn("WinError 32", notes)
        self.assertIn([held], self.lookups)
        self.assertEqual(_files(self.incoming), [held])

        # Still held: the next publish stops at the leftover and names it.
        self.lookups.clear()
        with self.assertRaises(build_native_launcher.BundleInUseError) as again:
            self.publish()

        self.assertEqual(again.exception.path, self.incoming)
        self.assertIn(f"removing {self.incoming}", str(again.exception))
        self.assertEqual(again.exception.winerror, 32)
        self.assertEqual(self.lookups, [[held]])
        self.assertEqual((self.output / "app.pyz").read_bytes(), b"old")

        # Released: the next publish removes it first and goes through.
        self.release()
        self.publish()

        self.assertEqual((self.output / "app.pyz").read_bytes(), b"app.pyz new")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])

    def test_a_refused_put_back_stops_without_a_wait_and_the_next_publish_puts_it_back(self) -> None:
        # Fast-tier on purpose: with nothing waited for, it takes a few tens of ms.
        self.hold_previous_before_the_second_rename()

        with self.assertRaises(build_native_launcher.BundleInUseError) as caught:
            self.publish()

        # Nothing waited: output was missing from the refused put-back on.
        self.assertEqual(self.states, [])
        error = caught.exception
        self.assertEqual(error.path, self.incoming)
        self.assertEqual(error.__cause__.winerror, 5)
        notes = _notes(error)
        self.assertIn(f"putting the last valid bundle back was refused: renaming {self.previous}", notes)
        self.assertIn("WinError 5", notes)
        self.assertIn("the next publish moves it back first", notes)
        self.assertIn(f"the copy in {self.incoming} could not be removed", notes)
        self.assertIn(_files(self.previous), self.lookups)
        self.assertFalse(self.output.exists())
        self.assertEqual((self.previous / "app.pyz").read_bytes(), b"old")

        # Released: the next publish puts the bundle back first, then publishes.
        self.release()
        self.publish()

        self.assertEqual((self.output / "app.pyz").read_bytes(), b"app.pyz new")
        self.assertEqual(self.siblings(), ["dayz-test-v1"])


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
        patcher = patch.object(build_native_launcher, "verify_bundle", lambda *_a, **_k: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        return _real_bundle(self.root)

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
