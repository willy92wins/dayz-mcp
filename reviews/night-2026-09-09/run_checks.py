from pathlib import Path
import os, subprocess, sys
from night_io import ROOT, OUT, write

ticket, label, module = sys.argv[1:4]
env = os.environ.copy()
env.update(PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
extra = []
for item in sys.argv[4:]:
    key, value = item.split("=", 1)
    env[key] = value
    extra.append(f"$env:{key}='{value}'")
exe = ROOT / "tools/.venv-mcp/Scripts/python.exe"
command = "\n".join([f"Set-Location -LiteralPath '{ROOT / 'tools'}'", "$env:PYTHONIOENCODING='utf-8'", "$env:PYTHONDONTWRITEBYTECODE='1'", f"$env:PYTHONPATH='{ROOT}'", *extra, f"& '{exe}' -m unittest {module} -v"])
result = subprocess.run([str(exe), "-m", "unittest", module, "-v"], cwd=ROOT / "tools", env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
log = command.encode() + b"\n\n" + result.stdout + f"\nexit code: {result.returncode}\n".encode()
write(OUT / ticket / (label + ".txt"), log)
print("\n".join(result.stdout.decode("utf-8", errors="replace").splitlines()[-5:]))
print("exit code:", result.returncode)
