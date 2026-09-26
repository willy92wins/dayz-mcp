"""Offline named-module runner; writes and verifies the complete literal output."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
PYTHON = ROOT / "tools/.venv-mcp/Scripts/python.exe"

def run(log_name, names, *, baseline=False):
    if not names or any(not name.startswith("tests.") for name in names):
        raise ValueError("explicit test names required")
    command = ([str(PYTHON), "-B", str(OUT / "check_baseline.py"), *names, "-v"]
               if baseline else [str(PYTHON), "-m", "unittest", *names, "-v"])
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(command, cwd=ROOT / "tools", env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    header = (f"cwd = {ROOT / 'tools'}\n"
              f"$env:PYTHONPATH = '{ROOT}'\n$env:PYTHONDONTWRITEBYTECODE = '1'\n"
              f"& '{PYTHON}' " +
              (f"-B '{OUT / 'check_baseline.py'}' " if baseline else "-m unittest ") +
              f"{' '.join(names)} -v\n\n").encode()
    data = header + result.stdout + f"\nEXIT_CODE={result.returncode}\n".encode()
    target = OUT / log_name
    target.write_bytes(data)
    assert target.read_bytes() == data and target.stat().st_size == len(data)
    output = result.stdout.decode("utf-8", errors="replace")
    print(output[-4000:] if result.returncode else output[-600:])
    print(f"EXIT_CODE={result.returncode}; VERIFIED {target}: {len(data)} bytes", flush=True)
    return result.returncode

if __name__ == "__main__":
    baseline = "--baseline" in sys.argv
    args = [arg for arg in sys.argv[1:] if arg != "--baseline"]
    sys.exit(run(args[0], args[1:], baseline=baseline))
