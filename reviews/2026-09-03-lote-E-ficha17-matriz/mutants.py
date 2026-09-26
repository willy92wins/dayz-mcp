# -*- coding: utf-8 -*-
"""Mutant calibration for the lote E oracle.

Reachable is not discriminating (LL-378). Each mutant is a cheap, plausible way to make a
product check green WITHOUT delivering the product. The named target check must stay RED.
BASE is the positive control: an unmutated run must reproduce the calibrated verdict.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SP = Path(r"C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\938664ff-361c-4575-aa56-c7c06c7ca2a8\scratchpad\lote-E")
WS = SP / "ws"
PY = r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe"
ORACLE = WS / "gate" / "oracle.py"

TOOL = WS / "tools" / "dayz_mcp" / "dayz_test_tool.py"
SERVER = WS / "tools" / "dayz_mcp" / "server.py"

VALIDATE_CALL = (
    "    _validate_terminal_context(\n"
    "        terminal, preflight=preflight, expected_run_id=expected_run_id\n"
    "    )\n"
)
STEAM_GUARD = '            if not preflight and mode in {"client", "all"}:'

MUTANTS = [
    ("M1-borrar-la-correlacion", "E2-CONTROL",
     "Pone E2 en verde borrando la llamada a _validate_terminal_context en vez de "
     "corregir la correlacion. El control de terminal incoherente tiene que cazarlo.",
     TOOL, VALIDATE_CALL, "    pass  # MUTANTE M1\n"),

    ("M2-nunca-consultar-steam", "E3-CONTROL",
     "Cumple 'cero consultas en preflight' no consultando nunca. El control de fila "
     "productiva tiene que cazarlo: E3 se cumpliria por vacuidad.",
     TOOL, STEAM_GUARD, "            if False:  # MUTANTE M2"),

    ("M3-documentar-en-un-comentario", "E4",
     "Escribe reattach y preflight en un COMENTARIO de server.py en vez de en la "
     "descripcion registrada. Un grep del fuente lo aceptaria; leer la tool no.",
     SERVER, "    async def dayz_test_run(",
     "    # reattach: server -> run_id -> client(run_id); preflight no relaja la matriz.\n"
     "    # MUTANTE M3: documentacion que el llamante NO ve.\n"
     "    async def dayz_test_run("),
]


def run_oracle():
    env = {**os.environ, "PYTHONPATH": ".", "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run([PY, str(ORACLE)], cwd=str(WS / "tools"), env=env,
                          capture_output=True, text=True, timeout=600)
    return proc.returncode, proc.stdout + proc.stderr


def verdicts(out: str) -> dict[str, str]:
    got = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("[") and "]" in line:
            # Keyed by the FULL name: keying by the first word collapses every check
            # that shares a prefix, and a collapsed dict hides what the mutant did.
            got[line[line.index("]") + 1:].strip()] = line[1:line.index("]")].strip()
    return got


def main() -> int:
    print("=" * 100)
    rc, out = run_oracle()
    base = verdicts(out)
    print(f"BASE (control positivo)  rc={rc}")
    print(f"  PASS={sum(1 for v in base.values() if v=='PASS')}  "
          f"FAIL={sum(1 for v in base.values() if v=='FAIL')}  de {len(base)}")
    if rc == 0:
        print("  !! BASE en verde: el oraculo no distingue nada. Abortando.")
        return 2
    print("=" * 100)

    caught = applied = 0
    for mid, target, why, path, find, repl in MUTANTS:
        original = path.read_text(encoding="utf-8")
        n = original.count(find)
        if n != 1:
            print(f"[SETUP-FAILED] {mid}: ancla {n}x -- el mutante NO se aplico, no cuenta")
            print(f"          {why}")
            continue
        applied += 1
        path.write_text(original.replace(find, repl, 1), encoding="utf-8")
        try:
            _rc, out_m = run_oracle()
            got = verdicts(out_m)
            hits = [x for k, x in got.items() if k.startswith(target)]
            v = ",".join(sorted(set(hits))) if hits else "<ausente>"
            ok = bool(hits) and any(x in ("FAIL", "UNMET") for x in hits)
            caught += int(ok)
            print(f"[{'CAZADO' if ok else 'ESCAPA'}] {mid}   objetivo {target} -> {v}")
            print(f"          {why}")
        finally:
            path.write_text(original, encoding="utf-8")

    print("=" * 100)
    print(f"MUTANTES: {caught}/{applied} cazados ({len(MUTANTS)} definidos)")
    return 0 if applied == len(MUTANTS) and caught == applied else 1


if __name__ == "__main__":
    sys.exit(main())
