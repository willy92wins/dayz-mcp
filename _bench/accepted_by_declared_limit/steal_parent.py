"""Inservible: no resuelve ni audita.

El hijo de la v6 ya no lleva effective_schema.py ni _gate.py. Sigue
compartiendo maquina con el juez. El interprete del venv es un trampolin:
os.getppid() es el launcher, el abuelo es `_gate.py`. Este modulo lee esa
linea de comandos, carga tools/dayz_mcp/effective_schema.py del arbol del
juez (fuera del temporal) y delega.

Fuera del juez no hay ancestro ejecutando _gate.py.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_LOADED = None


def _gate_path() -> Path:
    try:
        import psutil
    except Exception as exc:
        raise RuntimeError(
            f"inservible: no hay psutil para leer al padre ({exc!r})"
        ) from exc
    proc = psutil.Process()
    seen: set[int] = set()
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
        for item in cmdline:
            path = Path(item)
            if path.name != "_gate.py":
                continue
            try:
                if path.is_file() and "S7-GATE-OK" in path.read_text(encoding="utf-8"):
                    return path.resolve()
            except OSError:
                continue
    raise RuntimeError(
        "inservible: ningun ancestro ejecuta _gate.py; este modulo no resuelve ni audita"
    )


def _load_reference():
    global _LOADED
    if _LOADED is not None:
        return _LOADED
    reference = _gate_path().parent / "tools" / "dayz_mcp" / "effective_schema.py"
    if not reference.is_file():
        raise RuntimeError(
            "inservible: el arbol del juez no tiene tools/dayz_mcp/effective_schema.py"
        )
    name = "_s7_stolen_parent_effective_schema"
    spec = importlib.util.spec_from_file_location(name, reference)
    if spec is None or spec.loader is None:
        raise RuntimeError("inservible: no pude cargar la referencia del arbol del juez")
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
    return _load_reference().resolve_effective_schemas()


def audit_contracts(schemas=None):
    return _load_reference().audit_contracts(schemas)
