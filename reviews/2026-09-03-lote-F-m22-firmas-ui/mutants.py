# -*- coding: utf-8 -*-
"""Mutant calibration for the lote F oracle.

Reachable is not discriminating (LL-378). Each mutant is a PLAUSIBLE wrong way to widen the
UI signatures -- the mistakes a careful implementer actually makes -- and each names the
check that has to stay RED. BASE is the positive control.

Because the product does not exist yet, two of these mutants carry the widening INSIDE the
mutant: they are "a fix, done wrong". That is the only way to calibrate the checks that are
green today only because the subject is absent.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SP = Path(r"C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\938664ff-361c-4575-aa56-c7c06c7ca2a8\scratchpad\lote-F")
WS = SP / "ws"
PY = r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe"
ORACLE = WS / "gate" / "oracle.py"
SERVER = WS / "tools" / "dayz_mcp" / "server.py"

CLICK = '''    async def ui_click(
        path: str,
        button: int = 0,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(path, str) or path == "":
            raise ToolError(_bad_args("path", path, "be a non-empty string"))
        if not isinstance(button, int) or isinstance(button, bool) or button < 0 or button > 2:
            raise ToolError(_bad_args("button", button, "be an int from 0 to 2"))
        args = {"path": path, "button": int(button)}
'''

# M1: widen, but default root to "" and send it always. The natural wrong shape: a caller
# that sent no root now silently scopes against an empty root.
M1 = '''    async def ui_click(
        path: str,
        button: int = 0,
        root: str = "",
        mode: str = "direct",
        bubble: bool = False,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(path, str) or path == "":
            raise ToolError(_bad_args("path", path, "be a non-empty string"))
        if not isinstance(button, int) or isinstance(button, bool) or button < 0 or button > 2:
            raise ToolError(_bad_args("button", button, "be an int from 0 to 2"))
        if mode not in {"direct", "complete"}:
            raise ToolError(_bad_args("mode", mode, "be one of 'direct' or 'complete'"))
        if not isinstance(bubble, bool):
            raise ToolError(_bad_args("bubble", bubble, "be a bool"))
        args = {"path": path, "button": int(button), "root": root,
                "mode": mode, "bubble": bubble}  # MUTANTE M1
'''

# M2: widen and transmit, but do not validate. The most likely real mistake, and today the
# bad value is silently dropped rather than rejected, so nothing else would catch it.
M2 = '''    async def ui_click(
        path: str,
        button: int = 0,
        root: str | None = None,
        mode: str = "direct",
        bubble: bool = False,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(path, str) or path == "":
            raise ToolError(_bad_args("path", path, "be a non-empty string"))
        if not isinstance(button, int) or isinstance(button, bool) or button < 0 or button > 2:
            raise ToolError(_bad_args("button", button, "be an int from 0 to 2"))
        args = {"path": path, "button": int(button),
                "mode": mode, "bubble": bubble}  # MUTANTE M2: sin validar
        if root is not None:
            args["root"] = root
'''

# M3: widen correctly but drop the pre-existing button guard while restructuring. The
# collateral damage a wide edit causes.
M3 = '''    async def ui_click(
        path: str,
        button: int = 0,
        root: str | None = None,
        mode: str = "direct",
        bubble: bool = False,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if not isinstance(path, str) or path == "":
            raise ToolError(_bad_args("path", path, "be a non-empty string"))
        if mode not in {"direct", "complete"}:
            raise ToolError(_bad_args("mode", mode, "be one of 'direct' or 'complete'"))
        if not isinstance(bubble, bool):
            raise ToolError(_bad_args("bubble", bubble, "be a bool"))
        if root is not None and (not isinstance(root, str) or root == ""):
            raise ToolError(_bad_args("root", root, "be a non-empty string"))
        args = {"path": path, "button": int(button),
                "mode": mode, "bubble": bubble}  # MUTANTE M3: sin guarda de button
        if root is not None:
            args["root"] = root
'''

MUTANTS = [
    ("M1-root-por-defecto-vacio", "F3-ui_click",
     "Ensancha pero pone root='' por defecto y lo manda siempre: quien no mandaba root pasa "
     "a acotar contra un root vacio. El check de PRESENCIA tiene que cazarlo.",
     M1),
    ("M2-ensanchar-sin-validar", "F5",
     "Ensancha y transmite pero no valida. Es el error mas probable, y hoy un valor malo se "
     "DESCARTA en silencio en vez de rechazarse, asi que nada mas lo cazaria.",
     M2),
    ("M3-perder-la-guarda-de-button", "F6-button",
     "Ensancha bien pero pierde la guarda preexistente de button al reestructurar. Es el "
     "dano colateral tipico de una edicion ancha.",
     M3),
]


def run_oracle():
    env = {**os.environ, "PYTHONPATH": ".", "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.run([PY, str(ORACLE)], cwd=str(WS / "tools"), env=env,
                       capture_output=True, text=True, timeout=600)
    return p.returncode, p.stdout + p.stderr


def verdicts(out: str) -> dict[str, str]:
    got = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("[") and "]" in line:
            got[line[line.index("]") + 1:].strip()] = line[1:line.index("]")].strip()
    return got


def main() -> int:
    print("=" * 100)
    rc, out = run_oracle()
    base = verdicts(out)
    print(f"BASE (control positivo)  rc={rc}  "
          f"PASS={sum(1 for v in base.values() if v == 'PASS')} "
          f"FAIL={sum(1 for v in base.values() if v == 'FAIL')} de {len(base)}")
    if rc == 0:
        print("  !! BASE en verde: el oraculo no distingue nada. Abortando.")
        return 2
    print("=" * 100)

    caught = applied = 0
    for mid, target, why, repl in MUTANTS:
        original = SERVER.read_text(encoding="utf-8")
        n = original.count(CLICK)
        if n != 1:
            print(f"[SETUP-FAILED] {mid}: ancla {n}x -- no se aplico, no cuenta")
            continue
        applied += 1
        SERVER.write_text(original.replace(CLICK, repl, 1), encoding="utf-8")
        try:
            _rc, out_m = run_oracle()
            got = verdicts(out_m)
            hits = [v for k, v in got.items() if k.startswith(target)]
            ok = bool(hits) and any(v in ("FAIL", "UNMET") for v in hits)
            caught += int(ok)
            print(f"[{'CAZADO' if ok else 'ESCAPA'}] {mid}   objetivo {target} -> "
                  f"{','.join(sorted(set(hits))) or '<ausente>'}")
            print(f"          {why}")
        finally:
            SERVER.write_text(original, encoding="utf-8")

    print("=" * 100)
    print(f"MUTANTES: {caught}/{applied} cazados ({len(MUTANTS)} definidos)")
    return 0 if applied == len(MUTANTS) and caught == applied else 1


if __name__ == "__main__":
    sys.exit(main())
