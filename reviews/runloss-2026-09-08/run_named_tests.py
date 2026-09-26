"""Capture named unittest invocations, serially; never discover."""
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
mode, *modules = sys.argv[1:]
assert mode in {"red", "green"} and modules
assert all(name.startswith("tests.test_") for name in modules)
exe = ROOT / "tools/.venv-mcp/Scripts/python.exe"
env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
log = OUT / (mode + ".log")
records_path = OUT / (mode + "-results.json")
records = json.loads(records_path.read_bytes()) if records_path.exists() else []
content = log.read_bytes() if log.exists() else b""
failed = False
for module in modules:
    args = ([str(exe), str(OUT / "before_unittest.py"), module, "-v"]
            if mode == "red" else [str(exe), "-m", "unittest", module, "-v"])
    command = "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in args)
    print(command, flush=True)
    completed = subprocess.run(args, cwd=ROOT / "tools", env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    output = completed.stdout
    summary = [line for line in output.decode("utf-8", errors="replace").splitlines()
               if line.startswith(("Ran ", "OK", "FAILED", "ERROR:", "FAIL:"))]
    record = {"command": command, "cwd": str(ROOT / "tools"), "PYTHONPATH": str(ROOT),
              "exit_code": completed.returncode, "summary": summary,
              "finished_utc": datetime.now(timezone.utc).isoformat()}
    records.append(record)
    content += (f"cwd: {ROOT / 'tools'}\nPYTHONPATH: {ROOT}\nPYTHONDONTWRITEBYTECODE: 1\n{command}\n").encode("utf-8")
    content += output + f"\nEXIT CODE: {completed.returncode}\n\n".encode("utf-8")
    log.write_bytes(content)
    assert log.read_bytes() == content and log.stat().st_size == len(content)
    data = json.dumps(records, indent=2).encode("utf-8")
    records_path.write_bytes(data)
    assert records_path.read_bytes() == data and records_path.stat().st_size == len(data)
    print("\n".join(summary), flush=True)
    print(f"EXIT CODE: {completed.returncode}; VERIFIED log bytes={len(content)}", flush=True)
    failed = failed or completed.returncode != 0
sys.exit(1 if failed else 0)
