# -*- coding: utf-8 -*-
"""Mutant calibration for the lote D oracle.

Reachable is not discriminating (LL-378). Each mutant is a cheap, plausible way to make a
product check green WITHOUT delivering the product. The named target check has to stay RED.

Two harness lessons paid for during this calibration, both mine, both worth keeping:

1. A set-difference over "which checks are red" cannot see UNMET -> FAIL, so a mutant the
   oracle DID catch read as an escape. The verdict is per named check, not per set.
2. A mutant aimed at a layer the current code never reaches is invisible until the fix
   lands. Such a mutant carries `fix`: the intended change, applied FIRST, so the mutant is
   measured on the tree where the question is actually live -- run the instrument where you
   know the answer.

Also kept as a record: the original M1 mutated dayz_test_tool.py:135 (delete the alias-only
precheck) and escaped, correctly -- deleting that precheck IS the fix the ficha orders,
because dayz_test_request.py keeps the containment authority. A mutant that turns out to be
the intended implementation is a defect in the mutant, not in the gate.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SP = Path(r"C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\938664ff-361c-4575-aa56-c7c06c7ca2a8\scratchpad\lote-D")
WS = SP / "ws"
PY = r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe"
ORACLE = SP / "oracle.py"

TOOL = WS / "tools" / "dayz_mcp" / "dayz_test_tool.py"
REQUEST = WS / "tools" / "dayz_mcp" / "dayz_test_request.py"
SERVER = WS / "tools" / "dayz_mcp" / "server.py"

# The intended fix for ficha 21a: the facade stops second-guessing the parser.
FIX_MISSION = (TOOL,
               '    if mission not in _MISSION_ALIASES:\n        _fail("bad_mission")\n',
               "")

BRIDGE_BODY = (
    "        payload = await runtime.bridge_status_payload()\n"
    "        return _with_capability_comparison(payload, await _bridge_tool_names())"
)

MUTANTS = [
    # (id, target check that must stay RED, why, fix-first or None, file, find, replace)
    ("M1-contencion-siempre-cierta", "P2-C",
     "Con el arreglo de 21a ya aplicado, abre la contencion del parser (_path_is_within "
     "siempre True). La ruta absoluta pasa por la via barata y el fail-closed muere.",
     FIX_MISSION, REQUEST,
     "def _path_is_within(",
     "def _path_is_within(*_mut_a, **_mut_k):  # MUTANTE M1\n"
     "    return True\n\n\ndef _unused_path_is_within("),

    ("M2-alias-vacio", "P2-B",
     "Vacia la lista de alias en vez de aceptar rutas dentro de mission_roots: el alias "
     "deja de funcionar y el gate de no-regresion tiene que verlo.",
     None, TOOL,
     '_MISSION_ALIASES = frozenset({"chernarus", "livonia", "sakhal", "lfheli"})',
     "_MISSION_ALIASES = frozenset()  # MUTANTE M2"),

    ("M3-campos-de-relleno", "P1-D2",
     "Publica los cuatro tool_registry_* con valores inventados en vez de calcularlos. "
     "P1-A se pone verde y solo el check de valor puede distinguirlo.",
     None, SERVER, BRIDGE_BODY,
     "        payload = await runtime.bridge_status_payload()\n"
     "        payload.update({k: None for k in (  # MUTANTE M3\n"
     "            'tool_registry_fingerprint', 'tool_registry_captured_at',\n"
     "            'tool_registry_source_stale', 'tool_registry_remediation')})\n"
     "        return _with_capability_comparison(payload, await _bridge_tool_names())"),

    ("M4-overlay-que-se-filtra", "P1-C",
     "Cablea el overlay tambien en daemon.py en vez de dejarlo local al proceso FastMCP. "
     "Es el error natural, y el unico check que lo ve es el de aislamiento.",
     None, WS / "tools" / "dayz_mcp" / "daemon.py",
     "def make_status_provider(config: Any, state: ServerState) -> Callable[[], dict]:",
     "def make_status_provider(config: Any, state: ServerState) -> Callable[[], dict]:\n"
     "    _mut = 'tool_registry_fingerprint'  # MUTANTE M4"),
]


def run_oracle():
    env = {**os.environ, "PYTHONPATH": ".", "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run([PY, str(ORACLE)], cwd=str(WS / "tools"), env=env,
                          capture_output=True, text=True, timeout=300)
    return proc.returncode, proc.stdout + proc.stderr


def verdicts(out: str) -> dict[str, str]:
    got = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("[") and "]" in line:
            got[line[line.index("]") + 1:].strip().split(" ")[0]] = line[1:line.index("]")].strip()
    return got


def apply(edit, store):
    path, find, repl = edit
    original = path.read_text(encoding="utf-8")
    n = original.count(find)
    if n != 1:
        return n
    store.setdefault(path, original)
    path.write_text(original.replace(find, repl, 1), encoding="utf-8")
    return 1


def main() -> int:
    print("=" * 98)
    rc, out = run_oracle()
    base = verdicts(out)
    print(f"BASE (control positivo)  rc={rc}")
    print(f"  {ver_line(base)}")
    if rc == 0:
        print("  !! BASE en verde: el oraculo no distingue nada. Abortando.")
        return 2
    print("=" * 98)

    caught = applied = 0
    for mid, target, why, fix, path, find, repl in MUTANTS:
        store: dict[Path, str] = {}
        try:
            if fix is not None and apply(fix, store) != 1:
                print(f"[SETUP-FAILED] {mid}: el arreglo previo no aplico -- no cuenta")
                continue
            if apply((path, find, repl), store) != 1:
                print(f"[SETUP-FAILED] {mid}: ancla del mutante no unica -- no cuenta")
                print(f"          {why}")
                continue
            applied += 1
            _rc, out_m = run_oracle()
            got = verdicts(out_m)
            v = got.get(target, "<ausente>")
            ok = v in ("FAIL", "UNMET")
            caught += int(ok)
            print(f"[{'CAZADO' if ok else 'ESCAPA'}] {mid}   objetivo {target} -> {v}")
            print(f"          {why}")
            if not ok:
                print(f"          {ver_line(got)}")
        finally:
            for p, original in store.items():
                p.write_text(original, encoding="utf-8")

    print("=" * 98)
    print(f"MUTANTES: {caught}/{applied} cazados ({len(MUTANTS)} definidos)")
    return 0 if applied == len(MUTANTS) and caught == applied else 1


def ver_line(v: dict) -> str:
    return "  ".join(f"{k}={x}" for k, x in v.items())


if __name__ == "__main__":
    sys.exit(main())
