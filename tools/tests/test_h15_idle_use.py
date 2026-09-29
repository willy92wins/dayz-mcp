"""H15 and invariant 5: idle use state, adoption protection, and the warden.

The published numbers are the code constants. A doc that drops H15, or that
quotes a cut the code no longer uses, fails here.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp.idle_warden import (
    COUNTDOWN_S,
    EXIT_WAIT_S,
    IDLE_WARDEN_SETTINGS_NAME,
    WARNING_RETRY_S,
    WARNING_SHOW_S,
)
from dayz_mcp.input_activity import SAMPLE_INTERVAL_S
from dayz_mcp.process_lifecycle import INPUT_SIGNAL_STALE_S, RUN_IDLE_CUT_S
from tests.lease_helpers import parse_dpf_table

_REPO = Path(__file__).resolve().parents[2]
_HEADING = "### H — Coordinación segura de sesiones de agentes"
_H15_CRITERION = (
    "Runs idle: estado de uso por actividad, protección y el vigilante"
)
_H15_STATE = (
    "[verify] offline ✓; pase A en verde; pase B vuelta 1 en verde "
    "(aviso visto, cierre `idle_timeout` `orderly`); falta el rescate "
    "moviendo el ratón durante el aviso; el vigilante sigue apagado"
)
_H13_CRITERION = "Diagnóstico seguro de lifecycle y runs compartidos"
_LEASE_CLEANUP = (
    "Cleanup is scoped to the owner and never kills DayZ or the daemon."
)


class H15SpecContractTests(unittest.TestCase):
    def test_h15_row_pins_the_shipped_contract(self) -> None:
        spec = (_REPO / "product-spec.md").read_text(encoding="utf-8")
        rows = parse_dpf_table(spec, _HEADING)
        self.assertEqual(rows["H15"]["criterion"], _H15_CRITERION)
        self.assertEqual(rows["H15"]["state"], _H15_STATE)
        self.assertIn("[verify]", rows["H15"]["state"])
        self.assertIn("falta el rescate", rows["H15"]["state"])
        verification = rows["H15"]["verification"]
        self.assertEqual(RUN_IDLE_CUT_S, 600.0)
        self.assertEqual(INPUT_SIGNAL_STALE_S, 2.0)
        self.assertEqual(SAMPLE_INTERVAL_S, 0.25)
        self.assertEqual(WARNING_SHOW_S, 60)
        self.assertEqual(COUNTDOWN_S, 60.0)
        self.assertEqual(EXIT_WAIT_S, 45.0)
        self.assertEqual(WARNING_RETRY_S, 300.0)
        self.assertEqual(IDLE_WARDEN_SETTINGS_NAME, "idle-warden.json")
        for phrase in (
            "`SAMPLE_INTERVAL_S` (0,25 s)",
            "`RUN_IDLE_CUT_S` (600 s)",
            "`INPUT_SIGNAL_STALE_S` (2 s)",
            "identity_unverified",
            "input_signal_unavailable",
            "launched_by",
            "server_reload",
            "_use_projection",
            "UNRECONCILED",
            "ProcessLifecycle.adopt_run",
            "run_protected",
            "retry_after_s",
            "box_protected",
            "takeover=true",
            IDLE_WARDEN_SETTINGS_NAME,
            '{"enabled": true}',
            "`WARNING_SHOW_S` (60)",
            "`COUNTDOWN_S` (60 s)",
            "`WARNING_RETRY_S` (300 s)",
            "client_gone",
            "WM_CLOSE",
            "`EXIT_WAIT_S` (45 s)",
            "run_not_reapable",
            "a22b",
            "stop_run",
            "idle_timeout",
            "orderly",
            "fallback_stop",
            "human_input_age_s",
            "10:00",
            "11:00",
            "test_input_activity.py",
            "test_adoption_protection.py",
            "test_idle_warden.py",
        ):
            self.assertIn(phrase, verification, phrase)
        self.assertEqual(rows["H13"]["criterion"], _H13_CRITERION)
        self.assertEqual(rows["H13"]["state"], "❓")
        self.assertIn(
            "Un `RUNNING_IDLE` sin dueño que no está `abandoned`",
            rows["H13"]["verification"],
        )
        self.assertIn(
            "250f PRs 1-3 y a22b — estado de uso, protección y vigilante",
            spec,
        )
        for token in ("067b6f4", "5631b5f", "28f76cd", "41f9dd0"):
            self.assertIn(token, spec, token)
        self.assertIn(
            "2026-09-29 (250f PR 2 — protección de `adopt_run`)",
            spec,
        )

    def test_invariant_5_keeps_lease_cleanup_and_names_the_warden(self) -> None:
        arch = (_REPO / "ARCHITECTURE-DECISIONS.md").read_text(encoding="utf-8")
        self.assertIn(_LEASE_CLEANUP, arch)
        self.assertIn("This sentence is lease cleanup.", arch)
        self.assertIn("tools/dayz_mcp/idle_warden.py", arch)
        self.assertIn("LEASE_GRACE_S", arch)
        self.assertIn("MAX_PREF_RENEWALS", arch)
        for phrase in (
            IDLE_WARDEN_SETTINGS_NAME,
            '{"enabled": true}',
            "`RUN_IDLE_CUT_S` (600 s)",
            "`WARNING_SHOW_S` 60",
            "`COUNTDOWN_S` (60 s)",
            "WM_CLOSE",
            "`EXIT_WAIT_S` (45 s)",
            "run_not_reapable",
            "stop_run",
            "client_gone",
            "agent owner",
            "retail quarantine",
            "D-81",
            "D-82",
            "D-19",
            "D-20",
        ):
            self.assertIn(phrase, arch, phrase)
        self.assertEqual(RUN_IDLE_CUT_S, 600.0)
        self.assertEqual(COUNTDOWN_S, 60.0)
        self.assertEqual(EXIT_WAIT_S, 45.0)
        self.assertEqual(WARNING_SHOW_S, 60)


if __name__ == "__main__":
    unittest.main()
