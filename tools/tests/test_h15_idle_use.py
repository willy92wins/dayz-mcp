"""H15 and invariant 5: idle use state, adoption protection, and the warden.

The published numbers are the code constants. A doc that drops H15, or that
quotes a cut the code no longer uses, fails here.
"""

from __future__ import annotations

import hashlib
import re
import sys
import tempfile
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
_INVARIANT_5_START = "5. **One box, one lease.**"
_INVARIANT_5_END = "6. **Only the sealed"
# A published constant is one backtick name plus its number, once.
# `WARNING_SHOW_S` 60 has no parentheses; the others use `(N s)` or `(N)`.
_QUOTED_CONSTANT = re.compile(
    r"`([A-Z][A-Z0-9_]*)`\s*"
    r"(?:\(\s*(\d+(?:[.,]\d+)?)(?:\s*s)?\s*\)|(\d+(?:[.,]\d+)?))"
)
# Sentence-bounded. `[^.]` stops before the 120 s TTL and the 90 s grace,
# and before `WARNING_RETRY_S` (300 s), which is the next sentence.
_CUT_DURATION = re.compile(
    r"(?i)(?:(?:\bcorte\b|RUN_IDLE_CUT_S|\bcut\b)"
    r"[^.]{0,80}?(\d+(?:[.,]\d+)?)\s*s\b"
    r"|(\d+(?:[.,]\d+)?)\s*s\b[^.]{0,40}?"
    r"(?:\bcorte\b|\bcut\b|RUN_IDLE_CUT_S)"
    r"|candidate from\s+(\d+(?:[.,]\d+)?)\s*s\b)"
)
_COUNTDOWN_DURATION = re.compile(
    r"(?i)(?:(?:cuenta atrás|COUNTDOWN_S|\bcountdown\b)"
    r"[^.]{0,50}?(\d+(?:[.,]\d+)?)\s*s\b"
    r"|(\d+(?:[.,]\d+)?)\s*s\b\s*"
    r"(?:after the last confirmed|desde el último))"
)
_H15_QUOTED = {
    "SAMPLE_INTERVAL_S": float(SAMPLE_INTERVAL_S),
    "RUN_IDLE_CUT_S": float(RUN_IDLE_CUT_S),
    "INPUT_SIGNAL_STALE_S": float(INPUT_SIGNAL_STALE_S),
    "WARNING_SHOW_S": float(WARNING_SHOW_S),
    "COUNTDOWN_S": float(COUNTDOWN_S),
    "WARNING_RETRY_S": float(WARNING_RETRY_S),
    "EXIT_WAIT_S": float(EXIT_WAIT_S),
}
_INVARIANT_5_QUOTED = {
    "RUN_IDLE_CUT_S": float(RUN_IDLE_CUT_S),
    "WARNING_SHOW_S": float(WARNING_SHOW_S),
    "COUNTDOWN_S": float(COUNTDOWN_S),
    "EXIT_WAIT_S": float(EXIT_WAIT_S),
}


def _parse_doc_number(raw: str) -> float:
    return float(raw.replace(",", "."))


def _invariant_5(arch: str) -> str:
    if arch.count(_INVARIANT_5_START) != 1 or arch.count(_INVARIANT_5_END) != 1:
        raise AssertionError("invariant 5 bounds are not unique")
    start = arch.index(_INVARIANT_5_START)
    end = arch.index(_INVARIANT_5_END)
    if end <= start:
        raise AssertionError("invariant 5 bounds are out of order")
    return arch[start:end]


def _assert_numeric_contract(text: str, expected: dict[str, float]) -> None:
    """One quote per constant, and every cut and countdown number agrees with it."""

    found: dict[str, list[float]] = {}
    for match in _QUOTED_CONSTANT.finditer(text):
        raw = match.group(2) or match.group(3)
        found.setdefault(match.group(1), []).append(_parse_doc_number(raw))
    duplicates = [name for name, values in found.items() if len(values) != 1]
    if duplicates:
        raise AssertionError(f"constant quoted more than once: {duplicates}")
    actual = {name: values[0] for name, values in found.items()}
    if actual.keys() != expected.keys():
        raise AssertionError(
            f"quoted constants {sorted(actual)} != {sorted(expected)}"
        )
    for name, want in expected.items():
        if actual[name] != want:
            raise AssertionError(f"{name} quoted {actual[name]} != code {want}")
    cuts = [
        _parse_doc_number(next(group for group in match.groups() if group))
        for match in _CUT_DURATION.finditer(text)
    ]
    if not cuts or any(value != float(RUN_IDLE_CUT_S) for value in cuts):
        raise AssertionError(f"contradictory cut durations: {cuts}")
    countdowns = [
        _parse_doc_number(next(group for group in match.groups() if group))
        for match in _COUNTDOWN_DURATION.finditer(text)
    ]
    if not countdowns or any(value != float(COUNTDOWN_S) for value in countdowns):
        raise AssertionError(f"contradictory countdown durations: {countdowns}")


class H15SpecContractTests(unittest.TestCase):
    def test_h15_row_pins_the_shipped_contract(self) -> None:
        spec = (_REPO / "product-spec.md").read_text(encoding="utf-8")
        rows = parse_dpf_table(spec, _HEADING)
        self.assertEqual(rows["H15"]["criterion"], _H15_CRITERION)
        self.assertEqual(rows["H15"]["state"], _H15_STATE)
        self.assertIn("[verify]", rows["H15"]["state"])
        self.assertIn("falta el rescate", rows["H15"]["state"])
        verification = rows["H15"]["verification"]
        _assert_numeric_contract(verification, _H15_QUOTED)
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
        for phrase in (
            "Apagarlo antes del `WM_CLOSE` suelta lo que tenga y no cierra nada.",
            "apagarlo no deshace ese cierre e impide el stop de respaldo.",
            "Pasa a ser candidato al cumplir el corte de 600 s.",
            "El aviso espera a que el vigilante tenga el lease, así que una cola por delante lo retrasa.",
            "conserva la adopción de recuperación: otra sesión puede adoptarlo.",
            "el stop de respaldo solo ocurre tras una segunda revalidación",
            "Si no pasa, el audit anota `failed` y no hay stop.",
            "`run_once()` puede devolver `release_pending` hasta que el lease se suelta (el token sigue retenido), o `release_cleanup_pending` o `release_lost`.",
            "no un plazo garantizado.",
            "solo si la segunda revalidación pasa, el stop del lifecycle guard por identidad (`failed` y sin stop si no pasa).",
        ):
            self.assertIn(phrase, spec, phrase)
        for phrase in (
            "Apagarlo a mitad de ciclo suelta lo que tenga y no cierra.",
            "Si no pasa, el resultado es `failed` y no hay stop.",
            "Si un rol sigue vivo, el stop es el del lifecycle guard por identidad (`stop_run`).",
            "el aviso se ve a las 10:00 y el cierre llega a las 11:00 con `idle_timeout` `orderly`; mover el ratón",
        ):
            self.assertNotIn(phrase, spec, phrase)
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
        _assert_numeric_contract(_invariant_5(arch), _INVARIANT_5_QUOTED)
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
        for phrase in (
            "Turning it off before `WM_CLOSE` releases what it holds and closes nothing.",
            "turning it off does not undo that close, and it prevents the fallback stop.",
            "the fallback stop happens only after a second revalidation",
            "When it does not, the audit records `failed` and there is no stop.",
            "`run_once()` can return `release_pending` until the lease is released (the token stays held), or `release_cleanup_pending` or `release_lost`.",
            "so a queue ahead of it delays the warning.",
            "not a deadline.",
            "The run is a candidate from 600 s; the warning follows the lease; the close is no earlier than 60 s after the last confirmed warning.",
            "only the launching session may adopt an ownerless `RUNNING_IDLE` run.",
            "keeps its recovery adoption",
        ):
            self.assertIn(phrase, arch, phrase)
        for phrase in (
            "and turning it off releases what it holds and closes nothing.",
            "When it does not, the result is `failed` and there is no stop.",
            "If a launched role is still alive, a lifecycle-guard stop by identity follows (`stop_run`).",
            "only the launching session may adopt an ownerless run.",
            "Warn at 10:00 and close at 11:00. With no way to warn",
        ):
            self.assertNotIn(phrase, arch, phrase)
        self.assertEqual(RUN_IDLE_CUT_S, 600.0)
        self.assertEqual(COUNTDOWN_S, 60.0)
        self.assertEqual(EXIT_WAIT_S, 45.0)
        self.assertEqual(WARNING_SHOW_S, 60)

    def test_reviewer_threshold_mutation_fails_on_a_scratch_copy(self) -> None:
        source = _REPO / "product-spec.md"
        original = source.read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        text = original.decode("utf-8")
        needle = "Tests offline: `test_input_activity.py`"
        self.assertEqual(text.count(needle), 1)
        self.assertNotIn("el corte efectivo es 900 s", text)
        self.assertNotIn("la cuenta atrás dura 90 s", text)
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp) / "product-spec.md"
            self.assertNotEqual(scratch.resolve(), source.resolve())
            scratch.write_text(
                text.replace(needle, "el corte efectivo es 900 s. " + needle, 1),
                encoding="utf-8",
            )
            mutated = parse_dpf_table(
                scratch.read_text(encoding="utf-8"), _HEADING
            )["H15"]["verification"]
            with self.assertRaises(AssertionError) as caught:
                _assert_numeric_contract(mutated, _H15_QUOTED)
            self.assertIn("contradictory cut durations", str(caught.exception))
            self.assertIn("900", str(caught.exception))
            countdown_copy = Path(tmp) / "product-spec-countdown.md"
            countdown_copy.write_text(
                text.replace(needle, "la cuenta atrás dura 90 s. " + needle, 1),
                encoding="utf-8",
            )
            mutated = parse_dpf_table(
                countdown_copy.read_text(encoding="utf-8"), _HEADING
            )["H15"]["verification"]
            with self.assertRaises(AssertionError) as caught:
                _assert_numeric_contract(mutated, _H15_QUOTED)
            self.assertIn(
                "contradictory countdown durations", str(caught.exception)
            )
            self.assertIn("90.0", str(caught.exception))
            doubled = text.replace(
                "`RUN_IDLE_CUT_S` (600 s)",
                "`RUN_IDLE_CUT_S` (600 s) `RUN_IDLE_CUT_S` (600 s)",
                1,
            )
            doubled_copy = Path(tmp) / "product-spec-duplicate.md"
            doubled_copy.write_text(doubled, encoding="utf-8")
            mutated = parse_dpf_table(
                doubled_copy.read_text(encoding="utf-8"), _HEADING
            )["H15"]["verification"]
            with self.assertRaises(AssertionError) as caught:
                _assert_numeric_contract(mutated, _H15_QUOTED)
            self.assertIn("more than once", str(caught.exception))
            self.assertIn("RUN_IDLE_CUT_S", str(caught.exception))
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
