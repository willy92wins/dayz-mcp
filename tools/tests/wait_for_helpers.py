"""Clock shared by wait_for tests that must not depend on the Windows tick.

Lives here so a test module can use it without importing another test module.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


class _ExactWaitClock:
    """time.monotonic that moves only by the delay wait_for sleeps.

    On Windows before 3.13, monotonic ticks about every 15.6 ms and a sleep
    shorter than one tick can wake on the same reading, still before the
    deadline, so another probe starts legitimately. Adding exactly the delay
    the loop asked for makes the probe count and the timeout bound exact.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


def fixture_close_policy(run: dict) -> "SimpleNamespace":
    """The sealed-project authority for a fixture run: the root it names.

    The production admission (`launch_logs._recorded_anchor_in_sealed_project`
    via `dayz_test_tool._close_project_policy`) reads the approved-launcher
    registry, which the fast tier does not build. A test pins that loader to
    this stand-in with `patch.object`; the run row still carries its project
    identity, and the recorded anchor is validated against this policy's
    dev_root exactly as in production.
    """

    return SimpleNamespace(
        mod="ExampleMod",
        dev_root=str(Path(str(run.get("profiles"))).parent.parent),
    )


class PinnedClosePolicy:
    """Test mixin: pin the sealed-project admission for the test's runtime.

    ``_recorded_anchor_in_sealed_project`` resolves the loader through
    ``dayz_test_tool`` at call time, so one attribute patch covers every
    reader that consumes run rows.
    """

    def setUp(self) -> None:
        from unittest.mock import patch

        from dayz_mcp import dayz_test_tool

        pinned = patch.object(
            dayz_test_tool,
            "_close_project_policy",
            side_effect=fixture_close_policy,
        )
        pinned.start()
        self.addCleanup(pinned.stop)
        super().setUp()

