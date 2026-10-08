"""Capture tests must not write the real frame-state sidecar.

The capturing classes install isolate_capture_frame_state. This module runs
those tests with LOCALAPPDATA aimed at a sentinel, once with a dangerous
inherited DAYZ_MCP_FRAME_STATE_PATH and once with that variable absent, and
records every path _write_frame_state receives. Each path has to sit inside a
directory the isolation fixture (or a pre-existing per-test temporary
override) opened for that call.
"""
from __future__ import annotations

import importlib
import io
import os
import tempfile
import unittest
from unittest import mock

import mcp_capture
from tests._frame_state_isolation import OPEN_FRAME_STATE_DIRS

_CAPTURING = (
    "MCPCaptureTest",
    "CaptureDualCropSpaceTest",
    "AllBlackFrameReportTest",
    "CaptureFullresCropBehaviorTest",
)
_MODULES = (
    "test_mcp_capture.py",
    "test_tool_description_caveats.py",
)
_HEAD_IDENTITY = (
    "test_head_identity_scenario_keeps_frame_sha256_as_the_window_hash_in_both_spaces"
)


def _iter_tests(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_tests(item)
        else:
            yield item


def _tests_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _suite_from_modules() -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for class_name in _CAPTURING:
        module = "tests.test_mcp_capture"
        if class_name == "CaptureFullresCropBehaviorTest":
            module = "tests.test_tool_description_caveats"
        suite.addTests(loader.loadTestsFromName(f"{module}.{class_name}"))
    return suite


def _suite_from_discovery() -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    tools_dir = os.path.dirname(_tests_dir())
    for pattern in _MODULES:
        discovered = loader.discover(_tests_dir(), pattern=pattern, top_level_dir=tools_dir)
        for test in _iter_tests(discovered):
            if type(test).__name__ in _CAPTURING:
                suite.addTest(test)
    return suite


def _test_ids(suite: unittest.TestSuite) -> set[str]:
    return {test.id() for test in _iter_tests(suite)}


def _inside(path: str, root: str) -> bool:
    path = os.path.normcase(os.path.abspath(path))
    root = os.path.normcase(os.path.abspath(root))
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


class CaptureFrameStateIsolationTest(unittest.TestCase):
    def test_module_and_discovery_load_the_same_capturing_tests(self) -> None:
        by_module = _test_ids(_suite_from_modules())
        by_discovery = _test_ids(_suite_from_discovery())
        self.assertTrue(by_module)
        self.assertEqual(by_module, by_discovery)
        for class_name in _CAPTURING:
            self.assertTrue(any(class_name in test_id for test_id in by_module), class_name)

    def test_writer_stays_isolated_with_inherited_override(self) -> None:
        self._assert_writer_stays_isolated(inherited=True)

    def test_writer_stays_isolated_without_override(self) -> None:
        self._assert_writer_stays_isolated(inherited=False)

    def _assert_writer_stays_isolated(self, inherited: bool) -> None:
        case_type = importlib.import_module("tests.test_mcp_capture").CaptureDualCropSpaceTest
        sentinel = tempfile.TemporaryDirectory(prefix="frame_state_sentinel_")
        self.addCleanup(sentinel.cleanup)
        dangerous = os.path.join(sentinel.name, "inherited", mcp_capture.FRAME_STATE_FILENAME)
        previous = os.environ.get(mcp_capture.FRAME_STATE_ENV)
        local = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = sentinel.name
        if inherited:
            os.environ[mcp_capture.FRAME_STATE_ENV] = dangerous
        else:
            os.environ.pop(mcp_capture.FRAME_STATE_ENV, None)

        def restore_env() -> None:
            if local is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = local
            if previous is None:
                os.environ.pop(mcp_capture.FRAME_STATE_ENV, None)
            else:
                os.environ[mcp_capture.FRAME_STATE_ENV] = previous

        self.addCleanup(restore_env)

        recorded: list[str] = []
        violations: list[str] = []
        real_write = mcp_capture._write_frame_state

        def spy(path: str, state: dict) -> None:
            # Do not raise. _frame_stale_report catches every writer exception and
            # turns it into state_backend "unavailable", which would hide this guard.
            absolute = os.path.abspath(path)
            recorded.append(absolute)
            opened = [os.path.abspath(root) for root in OPEN_FRAME_STATE_DIRS]
            escaped = not any(_inside(absolute, root) for root in opened) or _inside(absolute, sentinel.name)
            if escaped:
                violations.append(absolute)
                return None
            return real_write(path, state)

        runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
        results: list[unittest.TextTestResult] = []
        with mock.patch.object(mcp_capture, "_write_frame_state", spy):
            for suite in (_suite_from_modules(), _suite_from_discovery()):
                results.append(runner.run(suite))
            self._run_head_identity(case_type)

        self.assertEqual([], violations, "writer targets left the isolated directory")
        self.assertTrue(recorded, "the capturing tests never reached the frame-state writer")
        for result in results:
            self.assertTrue(result.wasSuccessful(), self._format_result(result))
        self.assertFalse(os.path.exists(dangerous))
        fallback = os.path.join(sentinel.name, "DayZ_MCP", mcp_capture.FRAME_STATE_FILENAME)
        self.assertFalse(os.path.exists(fallback))

    def test_guard_fails_when_the_fixture_is_removed(self) -> None:
        """A swallowed writer assertion must not report success once isolation is gone."""
        capture = importlib.import_module("tests.test_mcp_capture")
        caveats = importlib.import_module("tests.test_tool_description_caveats")
        with mock.patch.object(capture, "isolate_capture_frame_state", lambda case: None), \
             mock.patch.object(caveats, "isolate_capture_frame_state", lambda case: None):
            for inherited in (True, False):
                with self.assertRaises(AssertionError):
                    self._assert_writer_stays_isolated(inherited=inherited)

    def _run_head_identity(self, case_type: type[unittest.TestCase]) -> None:
        """The spec scenario is @slow_test, so the fast tier skips it inside the suite."""
        case = case_type(_HEAD_IDENTITY)
        case.setUp()
        try:
            method = getattr(case, _HEAD_IDENTITY)
            raw = getattr(method, "__wrapped__", None)
            if raw is None:
                method()
            else:
                raw(case)
        finally:
            case.doCleanups()

    @staticmethod
    def _format_result(result: unittest.TextTestResult) -> str:
        lines = []
        for test, err in list(result.failures) + list(result.errors):
            lines.append(f"{test.id()}\n{err}")
        return "\n".join(lines) or "capturing tests failed"
