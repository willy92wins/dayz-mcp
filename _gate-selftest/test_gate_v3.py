"""Behavioral self-tests for the acceptance gate itself."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "_gate.py"
SELFTEST = ROOT / "_gate-selftest"


def _run(*args: str):
    return subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(GATE), "--seed", "730031", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_default_real_candidate_passes_after_all_selftests():
    proc = _run()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.splitlines()[-1] == "S7-GATE-OK"
    for control in ("C1", "C2", "C3", "C4", "C5"):
        assert f"{control}: PASS" in proc.stdout


@pytest.mark.parametrize(
    "filename",
    ["c2_toy.py", "c3_snapshot_dispatch.py", "c4_constant.py", "c5_resolver_only.py"],
)
def test_each_impostor_is_rejected(filename: str):
    proc = _run("--candidate", str(SELFTEST / filename))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert proc.stdout.splitlines()[-1] == "S7-GATE-FAIL"


def test_c5_reports_the_auditor_as_the_failed_half():
    proc = _run("--candidate", str(SELFTEST / "c5_resolver_only.py"))
    assert "CANDIDATE-RESOLVER: PASS" in proc.stdout
    assert "CANDIDATE-AUDITOR: FAIL" in proc.stdout


def test_seed_replays_stdout_exactly():
    first = _run()
    second = _run()
    assert first.stdout == second.stdout


def test_candidate_system_exit_fails_closed_with_text_verdict():
    candidate = SELFTEST / "_runtime_exit_candidate.py"
    try:
        candidate.write_text("raise SystemExit(0)\n", encoding="utf-8")
        proc = _run("--candidate", str(candidate))
    finally:
        candidate.unlink(missing_ok=True)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert proc.stdout.splitlines()[-1] == "S7-GATE-FAIL"
