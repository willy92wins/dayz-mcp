"""Inservible: no resuelve ni audita.

Via distinta a steal_reference.py:

- Resolutor: importa _gate.py del padre del cwd (el temporal vive en ROOT) y
  llama a _expected_registry sobre `tools` del frame de `_run`. No convierte.
- Auditor: delega en la copia sandbox de effective_schema.py.

Fuera del juez no hay _run, no hay _gate.py un nivel arriba, y no hay runtime/.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _run_frame():
    frame = sys._getframe()
    while frame is not None:
        filename = frame.f_code.co_filename.replace("\\", "/").lower()
        if frame.f_code.co_name == "_run" and filename.endswith("/worker.py"):
            return frame
        frame = frame.f_back
    raise RuntimeError("inservible: no running under worker._run")


def _load_parent_gate():
    path = Path.cwd().parent / "_gate.py"
    if not path.is_file():
        raise RuntimeError("inservible: _gate.py no esta en cwd.parent")
    name = "_s7_stolen_gate"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("inservible: no pude cargar _gate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if not callable(getattr(module, "_expected_registry", None)):
        raise RuntimeError("inservible: _gate.py no expone _expected_registry")
    return module


def _load_sandbox_reference():
    reference = Path.cwd() / "runtime" / "dayz_mcp" / "effective_schema.py"
    if not reference.is_file():
        raise RuntimeError("inservible: no hay copia sandbox de effective_schema.py")
    name = "_s7_stolen_effective_schema_via_gate"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, reference)
    if spec is None or spec.loader is None:
        raise RuntimeError("inservible: no pude cargar la referencia del sandbox")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if not callable(getattr(module, "audit_contracts", None)):
        raise RuntimeError("inservible: la referencia no expone audit_contracts")
    return module


def resolve_effective_schemas():
    tools = _run_frame().f_locals["tools"]
    return _load_parent_gate()._expected_registry(tools)


def audit_contracts(schemas=None):
    return _load_sandbox_reference().audit_contracts(schemas)
