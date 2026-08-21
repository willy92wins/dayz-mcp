"""`is_pid_alive` must never answer "dead" out of ignorance.

Its own docstring states the rule -- *indeterminate access errors read as alive
so the reclaim discriminator stays fail-closed (never kills on ambiguity)* --
and nothing was checking it: found by mutation on 2026-08-20, flipping the
answer left the whole suite green.

Why it is worth a test of its own: this predicate feeds the reclaim path. A pid
that cannot be opened because of permissions is not evidence of death, it is
absence of evidence. Reading it as dead is how a healthy process belonging to
somebody else gets reclaimed -- the worst failure this project has.

The Windows branch is the one that runs here, so it is the one asserted; the
POSIX branch is asserted through the same public entry point where it is
reachable.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import orphan_guard  # noqa: E402


@unittest.skipUnless(orphan_guard._IS_WINDOWS, "the Windows branch is the one in use here")
class LivenessIsFailClosedTest(unittest.TestCase):
    """OpenProcess failing tells us why it failed; only one reason means 'gone'."""

    def _answer_when_open_fails_with(self, last_error: int) -> bool:
        with patch.object(orphan_guard._k32, "OpenProcess", return_value=0), patch.object(
            orphan_guard.ctypes, "get_last_error", return_value=last_error
        ):
            return orphan_guard.is_pid_alive(4242)

    def test_invalid_parameter_is_the_only_proof_of_death(self) -> None:
        # ERROR_INVALID_PARAMETER means the kernel has no such pid. That is
        # evidence, and the only evidence this function accepts.
        self.assertFalse(
            self._answer_when_open_fails_with(orphan_guard._ERROR_INVALID_PARAMETER)
        )

    def test_access_denied_reads_as_alive(self) -> None:
        # 5 = ERROR_ACCESS_DENIED. The process exists and belongs to someone we
        # cannot inspect -- the exact case that must never be reclaimed.
        self.assertTrue(self._answer_when_open_fails_with(5))

    def test_any_other_failure_reads_as_alive(self) -> None:
        # Anything unclassified is ambiguity, and ambiguity means alive.
        for code in (0, 1, 6, 87 + 1, 1450):
            with self.subTest(last_error=code):
                self.assertTrue(self._answer_when_open_fails_with(code))

    def test_pid_zero_is_not_a_process(self) -> None:
        self.assertFalse(orphan_guard.is_pid_alive(0))

    def test_a_live_handle_that_has_not_signalled_is_alive(self) -> None:
        # A process object signals when it exits; not signalled means running.
        with patch.object(orphan_guard._k32, "OpenProcess", return_value=1234), patch.object(
            orphan_guard._k32, "WaitForSingleObject", return_value=orphan_guard._WAIT_OBJECT_0 + 1
        ), patch.object(orphan_guard._k32, "CloseHandle", return_value=1):
            self.assertTrue(orphan_guard.is_pid_alive(4242))

    def test_a_signalled_handle_is_dead(self) -> None:
        with patch.object(orphan_guard._k32, "OpenProcess", return_value=1234), patch.object(
            orphan_guard._k32, "WaitForSingleObject", return_value=orphan_guard._WAIT_OBJECT_0
        ), patch.object(orphan_guard._k32, "CloseHandle", return_value=1):
            self.assertFalse(orphan_guard.is_pid_alive(4242))


if __name__ == "__main__":
    unittest.main()
