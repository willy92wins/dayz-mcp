"""Run only the three approved modules, serially; persist literal evidence."""
from pathlib import Path
import os
import subprocess
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
PYTHON = ROOT / "tools/.venv-mcp/Scripts/python.exe"
MODE = sys.argv[1]
if MODE not in ("red", "green"):
    raise SystemExit("mode must be red or green")
env = dict(os.environ)
env["PYTHONPATH"] = str(ROOT)
env["PYTHONDONTWRITEBYTECODE"] = "1"
env.pop("STEAMFASTPATH_SOURCE", None)
if MODE == "red":
    env["STEAMFASTPATH_SOURCE"] = str(OUT / "steam_preflight.py.BEFORE")
modules = ["tests.test_steamfastpath_repair"]
if MODE == "green":
    modules += ["tests.test_steam_preflight", "tests.test_lote2_t2_steam"]
log = ""
records = []
for module in modules:
    args = [str(PYTHON), "-B", "-m", "unittest", module, "-v"]
    completed = subprocess.run(args, cwd=ROOT / "tools", env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", timeout=45)
    prefix = f"cwd: {ROOT / 'tools'}\n$env:PYTHONPATH = '{ROOT}'\n$env:PYTHONDONTWRITEBYTECODE = '1'\n"
    if MODE == "red":
        prefix += f"$env:STEAMFASTPATH_SOURCE = '{env['STEAMFASTPATH_SOURCE']}'\n"
    else:
        prefix += 'Remove-Item Env:STEAMFASTPATH_SOURCE -ErrorAction SilentlyContinue\n'
    command = f'& "{PYTHON}" -B -m unittest {module} -v'
    log += prefix + command + "\n" + completed.stdout + f"EXIT CODE: {completed.returncode}\n\n"
    raw = log.encode("utf-8")
    path = OUT / (MODE + ".log")
    path.write_bytes(raw)
    assert path.read_bytes() == raw and path.stat().st_size == len(raw)
    print(command)
    print("\n".join(completed.stdout.splitlines()[-4:]))
    print(f"EXIT CODE: {completed.returncode}")
    records.append(completed.returncode)
if MODE == "red":
    raise SystemExit(0 if records == [1] else 1)
raise SystemExit(0 if all(code == 0 for code in records) else 1)
