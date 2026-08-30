"""Inservible: no convierte ni audita.

Carga _gate.py del ancestro (misma via que steal_parent) y usa el oraculo
del juez: `_resolver_fixture` + `_expected_registry` y `_audit_fixtures`.
No llama a build_app. `_expected_registry` exige default/enum en cada
propiedad: sobre un schema realista revienta.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_GATE = None
_SEED_LABEL = None


def _ancestor_cmdlines() -> list[list[str]]:
    try:
        import psutil
    except Exception as exc:
        raise RuntimeError(
            f"inservible: no hay psutil para leer al padre ({exc!r})"
        ) from exc
    proc = psutil.Process()
    seen: set[int] = set()
    lines: list[list[str]] = []
    for _ in range(16):
        pid = proc.ppid()
        if not pid or pid in seen:
            break
        seen.add(pid)
        try:
            proc = psutil.Process(pid)
            cmdline = proc.cmdline() or []
        except (psutil.Error, TypeError, ValueError):
            break
        lines.append(list(cmdline))
    return lines


def _gate_and_seed():
    global _GATE, _SEED_LABEL
    if _GATE is not None and _SEED_LABEL is not None:
        return _GATE, _SEED_LABEL
    gate_path = None
    seed = None
    label = "CANDIDATE"
    for cmdline in _ancestor_cmdlines():
        for index, item in enumerate(cmdline):
            path = Path(item)
            if path.name == "_gate.py":
                try:
                    if path.is_file() and "_expected_registry" in path.read_text(
                        encoding="utf-8"
                    ):
                        gate_path = path.resolve()
                except OSError:
                    continue
            if item == "--seed" and index + 1 < len(cmdline):
                try:
                    seed = int(cmdline[index + 1])
                except ValueError:
                    pass
    if gate_path is None:
        raise RuntimeError(
            "inservible: ningun ancestro ejecuta _gate.py; este modulo no resuelve ni audita"
        )
    if seed is None:
        raise RuntimeError(
            "inservible: el ancestro no lleva --seed; este oraculo no calcula"
        )
    name = "_s7_stolen_parent_gate"
    spec = importlib.util.spec_from_file_location(name, gate_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("inservible: no pude cargar _gate.py del ancestro")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    for attr in ("_resolver_fixture", "_expected_registry", "_audit_fixtures"):
        if not callable(getattr(module, attr, None)):
            raise RuntimeError(f"inservible: _gate.py no expone {attr}")
    _GATE = module
    _SEED_LABEL = (seed, label)
    return _GATE, _SEED_LABEL


def resolve_effective_schemas():
    gate, (seed, label) = _gate_and_seed()
    tools = gate._resolver_fixture(seed, label)
    return gate._expected_registry(tools)


def audit_contracts(schemas=None):
    gate, (seed, label) = _gate_and_seed()
    if not isinstance(schemas, dict):
        return []
    findings = []
    for _name, case_schemas, triples in gate._audit_fixtures(seed, label):
        if case_schemas == schemas:
            for tool, code, param in sorted(triples):
                findings.append(
                    {
                        "tool": tool,
                        "code": code,
                        "param": param,
                        "evidence": "stolen-from-parent-gate-oracle",
                    }
                )
            return findings
    return []
