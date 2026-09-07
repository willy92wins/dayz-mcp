# -*- coding: utf-8 -*-
"""Re-run a reviewer's mutation from the receiving side and check the control now dies.

Usage:
  mutation_gate.py --tree <dir> --file <relpath> --old <text|@file> --new <text|@file>
                   --module tests.test_x [--module tests.test_y] [--python <exe>]

A round-2 fix for "this mutation survives" is not accepted on the worker's word. The reviewer
says a control is blind; the worker says it fixed it; only the receiver re-applying the exact
mutation settles it. Measured 2026-09-07 over nine lanes: six deliveries whose gates were green
had a surviving mutation, and every one of them was closed by running this from the outside.

Contract, in order:
  1. the module set must be GREEN unmutated -- a base that is already red proves nothing;
  2. the mutation must match EXACTLY ONCE in the file, or the run aborts untouched;
  3. the module set must be RED mutated -- that, and only that, is a live control;
  4. the file is restored from the original bytes in a finally block and the restore is
     verified by sha256 before exiting, so an exception mid-run cannot leave a tree mutated.

Exit 0 only when the base is green and the mutant is red.
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import subprocess
import sys


def _text(value: str) -> str:
    """A literal, or the contents of a file when prefixed with @ (for multi-line hunks)."""
    if value.startswith("@"):
        return pathlib.Path(value[1:]).read_text(encoding="utf-8")
    return value


def _run(python: str, cwd: pathlib.Path, modules: list[str]) -> tuple[bool, str]:
    rows: list[str] = []
    ok = True
    for module in modules:
        proc = subprocess.run(
            [python, "-B", "-m", "unittest", module],
            cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        lines = [
            line for line in (proc.stdout + proc.stderr).splitlines()
            if line.startswith(("Ran ", "OK", "FAILED"))
        ]
        rows.append(f"{module}: rc={proc.returncode} {' '.join(lines[-2:])}")
        ok = ok and proc.returncode == 0
    return ok, "\n    ".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", required=True, help="repo root holding tools/")
    parser.add_argument("--file", required=True, help="path to mutate, relative to --tree")
    parser.add_argument("--old", required=True, help="text to replace, or @file")
    parser.add_argument("--new", default="", help="replacement, or @file (default: delete)")
    parser.add_argument("--module", action="append", required=True, dest="modules")
    parser.add_argument("--python", default=None, help="defaults to the tree's .venv-mcp")
    args = parser.parse_args(argv)

    tree = pathlib.Path(args.tree).resolve()
    target = tree / args.file
    python = args.python or str(tree / "tools" / ".venv-mcp" / "Scripts" / "python.exe")
    if not pathlib.Path(python).is_file():
        print(f"!! interprete no encontrado: {python}", file=sys.stderr)
        return 2

    original = target.read_bytes()
    before = hashlib.sha256(original).hexdigest()
    text = original.decode("utf-8")
    old, new = _text(args.old), _text(args.new)
    hits = text.count(old)
    if hits != 1:
        print(f"!! la mutacion casa {hits} veces en {args.file}; se exige exactamente 1. "
              f"Nada tocado.", file=sys.stderr)
        return 2

    cwd = tree / "tools"
    try:
        base_ok, base = _run(python, cwd, args.modules)
        print(f"  sin mutar:\n    {base}")
        if not base_ok:
            print("  => la base YA esta roja: el control no se puede juzgar", file=sys.stderr)
            return 2
        target.write_bytes(text.replace(old, new, 1).encode("utf-8"))
        mutant_ok, mutant = _run(python, cwd, args.modules)
        print(f"  MUTADO:\n    {mutant}")
    finally:
        # No return here: a return inside finally swallows whatever was propagating.
        # The restore is recorded and judged after the block.
        target.write_bytes(original)
        restored = hashlib.sha256(target.read_bytes()).hexdigest()
        print(f"  restaurado: sha {restored[:12]}"
              + ("" if restored == before else f"  !! ESPERABA {before[:12]}"))

    if restored != before:
        print(f"!! NO SE RESTAURO {args.file}: {before[:12]} -> {restored[:12]}",
              file=sys.stderr)
        return 3

    if mutant_ok:
        print("  => LA MUTACION SOBREVIVE: el control esta ciego, no lo aceptes")
        return 1
    print("  => CONTROL VALIDO: el test muere con la mutacion")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
