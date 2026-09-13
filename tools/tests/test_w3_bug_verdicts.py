"""Versioned W3 verdicts for BUG-110 / 111 / 112 (W3-P2-02).

BUG-112 stays INCONCLUSO: the named stability suite is absent. Do not add a
center-pick tautology here.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import dayz_mcp
import mcp_capture
from tests._tree_identity import assert_same_checkout, checkout_root


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLAN_W3 = _REPO_ROOT / "docs" / "plan-w3.md"
_CAPTURE_PY = _TOOLS_DIR / "mcp_capture.py"
_STABILITY_SUITE = _TOOLS_DIR / "tests" / "test_capture_stability.py"


class Bug110CheckoutLoadTests(unittest.TestCase):
    def test_dayz_mcp_loads_from_this_checkout_not_site_packages(self) -> None:
        loaded = Path(dayz_mcp.__file__).resolve()
        assert_same_checkout(Path(__file__), loaded)
        loaded_s = os.path.normcase(str(loaded))
        self.assertNotIn(
            os.path.normcase("site-packages"),
            loaded_s,
            f"dayz_mcp loaded from site-packages: {loaded}",
        )
        self.assertEqual(checkout_root(loaded), checkout_root(Path(__file__)))


class Bug111DeadConstantTests(unittest.TestCase):
    def test_default_stability_threshold_absent_from_mcp_capture(self) -> None:
        source = _CAPTURE_PY.read_text(encoding="utf-8")
        self.assertNotIn("DEFAULT_STABILITY_THRESHOLD", source)
        self.assertFalse(hasattr(mcp_capture, "DEFAULT_STABILITY_THRESHOLD"))


class Bug112InconclusoTests(unittest.TestCase):
    def test_capture_stability_suite_is_absent(self) -> None:
        self.assertFalse(
            _STABILITY_SUITE.is_file(),
            "BUG-112 stays INCONCLUSO until test_capture_stability.py exists "
            "with a non-center minimum; do not invent a tautological suite",
        )

    def test_plan_w3_keeps_bug112_inconcluso(self) -> None:
        text = _PLAN_W3.read_text(encoding="utf-8")
        self.assertRegex(text, r"\*\*BUG-112\*\*\s*\|\s*\*\*INCONCLUSO\*\*")
        self.assertIsNone(re.search(r"\*\*BUG-112\*\*\s*\|\s*\*\*PASS\*\*", text))
        self.assertRegex(text, r"\*\*BUG-110\*\*\s*\|\s*\*\*PASS\*\*")
        self.assertRegex(text, r"\*\*BUG-111\*\*\s*\|\s*\*\*PASS\*\*")
