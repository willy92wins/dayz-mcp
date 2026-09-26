"""Serial named-module runner; logs exact commands, combined output and exit codes."""
from pathlib import Path
import os
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
out = Path(__file__).resolve().parent
python = root / "tools/.venv-mcp/Scripts/python.exe"
original_mode = len(sys.argv) > 1 and sys.argv[1] == "--original"
if original_mode:
    sys.argv.pop(1)
log = out / sys.argv[1]
modules = sys.argv[2:]
assert log.parent == out and modules and all(m.startswith("tests.") for m in modules)
env = os.environ.copy()
env["PYTHONPATH"] = str(root)
env["PYTHONDONTWRITEBYTECODE"] = "1"
env["PYTHONIOENCODING"] = "utf-8"
transcript = f'cwd = {root / "tools"}\nPYTHONPATH = {root}\nPYTHONDONTWRITEBYTECODE = 1\nPYTHONIOENCODING = utf-8\n'
failed = False
for module in modules:
    args = [str(python), "-B", "-m", "unittest", module, "-v"]
    command = f'& \'{python}\' -B -m unittest {module} -v'
    if original_mode:
        original_runner = out / "run_original.py"
        args = [str(python), "-B", str(original_runner), module, "-v"]
        command = f"& '{python}' -B '{original_runner}' {module} -v"
    result = subprocess.run(args, cwd=root / "tools", env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    transcript += f'\nCOMMAND: {command}\n{result.stdout}\nEXIT CODE: {result.returncode}\n'
    data = transcript.encode("utf-8")
    log.write_bytes(data)
    assert log.read_bytes() == data and log.stat().st_size == len(data)
    print(command, flush=True)
    print("\n".join(result.stdout.rstrip().splitlines()[-5:]), flush=True)
    print(f'EXIT CODE: {result.returncode}; verified {log.stat().st_size} bytes', flush=True)
    failed |= result.returncode != 0
sys.exit(int(failed))
