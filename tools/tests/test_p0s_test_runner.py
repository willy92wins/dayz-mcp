from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from p0s_test_runner import (
    WINDOWS_FFI_LAUNCH_PREFIXES,
    DenyLaunchGuard,
    LaunchDenied,
    LaunchSurface,
    default_launch_surfaces,
    run_named_tests,
)


class DenyLaunchGuardTest(unittest.TestCase):
    def test_fake_surface_is_blocked_before_call_and_recorded_without_arguments(self) -> None:
        calls: list[tuple[object, ...]] = []

        def fake_launch(*args: object, **kwargs: object) -> None:
            calls.append(args + tuple(kwargs.values()))

        owner = SimpleNamespace(launch=fake_launch)
        surface = LaunchSurface(owner=owner, attribute="launch", label="fake.launch")

        with DenyLaunchGuard((surface,), include_windows_ffi=False) as guard:
            with self.assertRaises(LaunchDenied) as raised:
                owner.launch("--token=must-not-leak", password="must-not-leak-either")

        self.assertEqual(calls, [])
        self.assertEqual(guard.intercept_count, 1)
        self.assertEqual(guard.attempts, ("fake.launch",))
        self.assertIn("fake.launch", str(raised.exception))
        self.assertNotIn("must-not-leak", str(raised.exception))

    def test_surface_is_restored_after_context_exit(self) -> None:
        def fake_launch() -> str:
            return "original"

        owner = SimpleNamespace(launch=fake_launch)
        surface = LaunchSurface(owner=owner, attribute="launch", label="fake.launch")

        with DenyLaunchGuard((surface,), include_windows_ffi=False):
            self.assertIsNot(owner.launch, fake_launch)

        self.assertIs(owner.launch, fake_launch)
        self.assertEqual(owner.launch(), "original")

    def test_partial_install_failure_rolls_back_already_patched_surfaces(self) -> None:
        def fake_launch() -> None:
            return None

        owner = SimpleNamespace(launch=fake_launch)
        missing_owner = SimpleNamespace()
        guard = DenyLaunchGuard(
            (
                LaunchSurface(owner=owner, attribute="launch", label="fake.launch"),
                LaunchSurface(
                    owner=missing_owner,
                    attribute="missing",
                    label="fake.missing",
                ),
            ),
            include_windows_ffi=False,
        )

        with self.assertRaises(AttributeError):
            guard.install()

        self.assertIs(owner.launch, fake_launch)
        self.assertFalse(guard.installed)

    def test_fake_windows_ffi_launch_is_blocked_but_safe_call_delegates(self) -> None:
        launch_calls: list[str] = []
        safe_calls: list[str] = []

        def create_process(value: str) -> None:
            launch_calls.append(value)

        def safe_query(value: str) -> str:
            safe_calls.append(value)
            return "safe-result"

        library = SimpleNamespace(
            CreateProcessW=create_process,
            QueryFullProcessImageNameW=safe_query,
        )

        with DenyLaunchGuard((), include_windows_ffi=False) as guard:
            protected = guard.wrap_windows_ffi_library(library, "fake.kernel32")
            with self.assertRaises(LaunchDenied):
                protected.CreateProcessW("must-not-run")
            result = protected.QueryFullProcessImageNameW("safe")

        self.assertEqual(launch_calls, [])
        self.assertEqual(safe_calls, ["safe"])
        self.assertEqual(result, "safe-result")
        self.assertEqual(guard.attempts, ("fake.kernel32.CreateProcessW",))

    def test_default_catalog_covers_required_python_launch_surfaces(self) -> None:
        labels = {surface.label for surface in default_launch_surfaces()}
        required = {
            "subprocess.Popen",
            "subprocess.run",
            "subprocess.call",
            "subprocess.check_call",
            "subprocess.check_output",
            "asyncio.create_subprocess_exec",
            "asyncio.create_subprocess_shell",
            "os.system",
            "os.popen",
            "multiprocessing.BaseProcess.start",
        }
        if hasattr(os, "startfile"):
            required.add("os.startfile")

        for attribute in dir(os):
            if attribute.startswith("spawn") and callable(getattr(os, attribute)):
                required.add(f"os.{attribute}")

        self.assertLessEqual(required, labels)

    def test_windows_ffi_prefix_catalog_covers_both_launch_families(self) -> None:
        self.assertEqual(
            WINDOWS_FFI_LAUNCH_PREFIXES,
            ("createprocess", "shellexecute"),
        )

    def test_runner_installs_guard_before_load_and_rejects_caught_fake_attempt(self) -> None:
        def fake_launch(_secret: str) -> None:
            self.fail("fake launch target must not be called")

        owner = SimpleNamespace(launch=fake_launch)
        surface = LaunchSurface(owner=owner, attribute="launch", label="fake.launch")
        guard = DenyLaunchGuard((surface,), include_windows_ffi=False)
        original = owner.launch
        secret = "must-not-appear-in-summary"
        test_case = self

        class FakeLoader:
            def loadTestsFromNames(self, names: list[str]) -> unittest.TestSuite:
                test_case.assertEqual(names, ["fake.test"])
                test_case.assertIsNot(owner.launch, original)

                def catch_denial() -> None:
                    with test_case.assertRaises(LaunchDenied):
                        owner.launch(secret)

                return unittest.TestSuite((unittest.FunctionTestCase(catch_denial),))

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = run_named_tests(
                ("fake.test",),
                guard_factory=lambda: guard,
                loader=FakeLoader(),
                stream=stderr,
            )

        summary = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(summary["status"], "launch_denied")
        self.assertEqual(summary["intercept_count"], 1)
        self.assertEqual(summary["attempts"], ["fake.launch"])
        self.assertNotIn(secret, stdout.getvalue())
        self.assertIs(owner.launch, original)


if __name__ == "__main__":
    unittest.main()
