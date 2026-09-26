"""Read-only offline validator; capture baseline and final structural findings."""
from pathlib import Path
import json
import os
import subprocess
import sys


OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
PYTHON = ROOT / "tools/.venv-mcp/Scripts/python.exe"
VALIDATOR = Path("C:/Users/guill/DayZ-Modding-Knowledge-Pack/tools/dayz-script-validator/scripts/script_validator.py")


def save(path, data):
    path.write_bytes(data)
    assert path.read_bytes() == data and path.stat().st_size == len(data), path


baseline = OUT / "validator-before/MCPClientBridge.c"
baseline.parent.mkdir(exist_ok=True)
save(baseline, (OUT / "MCPClientBridge.c.BEFORE").read_bytes())
env = os.environ.copy()
env.update(PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
           TEMP=str(OUT / "test-tmp"), TMP=str(OUT / "test-tmp"))
log = f"cwd={ROOT}\nPYTHONDONTWRITEBYTECODE=1\nPYTHONUTF8=1\n".encode()
records = []
for label, target in (("before", baseline),
                      ("after", ROOT / "addon/scripts/5_Mission/MCPClientBridge.c"),
                      ("addon", ROOT / "addon")):
    argv = [str(PYTHON), "-B", str(VALIDATOR), str(target)]
    command = subprocess.list2cmdline(argv)
    result = subprocess.run(argv, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=60)
    save(OUT / f"validator-{label}.json", result.stdout)
    report = json.loads(result.stdout)
    summary = {key: report.get(key) for key in ("status", "files_checked", "elapsed_ms")}
    summary.update(errors=len(report.get("errors", [])), warnings=len(report.get("warnings", [])))
    log += f"\nCOMMAND: {command}\n".encode() + result.stdout
    log += f"\nEXIT CODE: {result.returncode}\n".encode()
    save(OUT / "validator.log", log)
    records.append(dict(label=label, command=command, exit_code=result.returncode, summary=summary))
    save(OUT / "validator-results.json", (json.dumps(records, indent=2) + "\n").encode())
    print(label, json.dumps(summary), f"EXIT CODE: {result.returncode}", flush=True)
    for finding in report.get("errors", []) + report.get("warnings", []):
        print(json.dumps(finding, ensure_ascii=True), flush=True)
sys.exit(int(any(record["exit_code"] == 1 for record in records)))
