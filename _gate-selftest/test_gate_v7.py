"""Behavioral regressions for the v7 fixture family and safe judge startup."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "_gate.py"
STEAL_EXPECTED = ROOT / "_bench" / "must_reject" / "steal_expected.py"


def _load_gate():
    name = "_gate_v7_test_target"
    spec = importlib.util.spec_from_file_location(name, GATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _safe_run(*args: str):
    return subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(GATE), "--seed", "730053", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )


def test_expected_registry_handles_realistic_schema_forms():
    gate = _load_gate()
    tools = [
        SimpleNamespace(
            name="realistic",
            description=None,
            inputSchema={
                "type": "object",
                "properties": {
                    "plain": {"type": "string"},
                    "choice": {
                        "anyOf": [
                            {"type": "null"},
                            {"type": "string", "enum": ["a", "b"]},
                            {"type": "object"},
                        ]
                    },
                    "variant": {
                        "oneOf": [{"type": "integer"}, {"type": "number"}],
                        "default": 7,
                    },
                    "multi": {"type": ["null", "array", "object"]},
                },
                "required": ["plain", "multi"],
            },
        )
    ]
    assert gate._expected_registry(tools) == {
        "realistic": {
            "description": "",
            "params": {
                "plain": {
                    "required": True,
                    "default": None,
                    "type": "string",
                    "enum": None,
                },
                "choice": {
                    "required": False,
                    "default": None,
                    "type": ["string", "object"],
                    "enum": ["a", "b"],
                },
                "variant": {
                    "required": False,
                    "default": 7,
                    "type": ["integer", "number"],
                    "enum": None,
                },
                "multi": {
                    "required": True,
                    "default": None,
                    "type": ["array", "object"],
                    "enum": None,
                },
            },
        }
    }


def test_resolver_fixture_contains_every_new_realistic_form():
    gate = _load_gate()
    tools = gate._resolver_fixture(730053, "fixture-coverage")
    properties = [
        item
        for tool in tools
        for item in (tool.inputSchema.get("properties") or {}).values()
    ]
    required_is_partial = any(
        0 < len(tool.inputSchema.get("required") or [])
        < len(tool.inputSchema.get("properties") or {})
        for tool in tools
    )
    assert any("default" not in item for item in properties)
    assert any("enum" not in item for item in properties)
    assert any("anyOf" in item for item in properties)
    assert any("oneOf" in item for item in properties)
    assert any(isinstance(item.get("type"), list) for item in properties)
    assert required_is_partial
    assert any(not (tool.description or "") for tool in tools)


def test_unsafe_parent_is_rejected_before_evaluation():
    proc = subprocess.run(
        [sys.executable, str(GATE), "--seed", "730053"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 1
    assert "S7-GATE-ENVIRONMENT: FAIL" in proc.stdout
    assert "C1:" not in proc.stdout


def test_safe_parent_runs_without_importing_site_at_startup():
    proc = _safe_run()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "S7-GATE-ENVIRONMENT: FAIL" not in proc.stdout
    assert proc.stdout.splitlines()[-1] == "S7-GATE-OK"


def test_stolen_parent_oracle_is_rejected_by_multiple_fixture_families():
    proc = _safe_run("--candidate", str(STEAL_EXPECTED))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "S7-GATE-ENVIRONMENT: FAIL" not in proc.stdout
    assert proc.stdout.splitlines()[-1] == "S7-GATE-FAIL"


def test_gate_prints_the_declared_provenance_limit_with_the_verdict():
    proc = _safe_run()
    assert "Un gate conductual responde" in proc.stdout
    assert "No puede responder" in proc.stdout
    assert proc.stdout.splitlines()[-1] == "S7-GATE-OK"
