"""Load loop for FailedLaunchSettlementTest. Not itself a unittest."""

from __future__ import annotations

import os
import subprocess
import sys
import time

TOOLS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = sys.executable
CLASS = "tests.test_task7_rereview_regressions.FailedLaunchSettlementTest"


def _run_class() -> tuple[int, str]:
    started = time.perf_counter()
    proc = subprocess.run(
        [PYTHON, "-m", "unittest", CLASS],
        cwd=TOOLS_ROOT,
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - started
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
    return proc.returncode, f"{elapsed:.3f}s {' | '.join(tail)}"


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    fails = 0
    for i in range(n):
        code, tail = _run_class()
        if code != 0:
            fails += 1
            print(f"FAIL {i} {tail}")
    print(f"loops={n} fails={fails}")
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
