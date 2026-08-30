"""Inservible: no resuelve ni audita.

El hijo del gate v5 copia tools/dayz_mcp (incluida effective_schema.py, la
implementacion de referencia) a cwd/runtime y la pone al frente de sys.path.
Este modulo no contiene la conversion ni el auditor: carga ese fichero y
delega. Fuera del sandbox del juez el fichero no existe.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_WORKER_MARK = Path("worker.py")
_REFERENCE = Path("runtime") / "dayz_mcp" / "effective_schema.py"
_LOADED = None


def _load_sandbox_reference():
    global _LOADED
    if _LOADED is not None:
        return _LOADED
    cwd = Path.cwd()
    worker = cwd / _WORKER_MARK
    reference = cwd / _REFERENCE
    if not worker.is_file() or not reference.is_file():
        raise RuntimeError(
            "inservible: no hay copia sandbox de effective_schema.py; "
            "este modulo no resuelve ni audita"
        )
    name = "_s7_stolen_effective_schema"
    spec = importlib.util.spec_from_file_location(name, reference)
    if spec is None or spec.loader is None:
        raise RuntimeError("inservible: no pude cargar la referencia del sandbox")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if not callable(getattr(module, "resolve_effective_schemas", None)):
        raise RuntimeError("inservible: la referencia no expone resolve_effective_schemas")
    if not callable(getattr(module, "audit_contracts", None)):
        raise RuntimeError("inservible: la referencia no expone audit_contracts")
    _LOADED = module
    return module


def resolve_effective_schemas():
    return _load_sandbox_reference().resolve_effective_schemas()


def audit_contracts(schemas=None):
    return _load_sandbox_reference().audit_contracts(schemas)
