from pathlib import Path
import os, subprocess, sys, hashlib
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
env=os.environ.copy()
env["PYTHONPATH"]=str(ROOT)
env["DAYZ_MCP_WATCHDOG_REPO"]=str(ROOT)
env.pop("DAYZ_MCP_WATCHDOG_TREE",None)
cmd=[str(ROOT/"tools/.venv-mcp/Scripts/python.exe"),"-X","utf8","-B","-m","unittest","tests.test_docs_truth","-v"]
p=subprocess.run(cmd,cwd=ROOT/"tools",env=env,capture_output=True)
raw=p.stdout+p.stderr
target=OUT/"test-docs-truth.log"
target.write_bytes(raw)
assert target.read_bytes()==raw
print("cwd =",ROOT/"tools")
print("PYTHONPATH =",env["PYTHONPATH"])
print("DAYZ_MCP_WATCHDOG_REPO =",env["DAYZ_MCP_WATCHDOG_REPO"])
print("DAYZ_MCP_WATCHDOG_TREE = unset")
print("COMMAND =",subprocess.list2cmdline(cmd))
for line in raw.decode("utf-8",errors="replace").splitlines():
    if line.startswith(("Ran ","OK","FAILED","FAIL:","ERROR:","AssertionError:")):
        print(line[:700])
print("Exit code:",p.returncode)
print("LOG_WRITE_VERIFIED",len(raw),hashlib.sha256(raw).hexdigest())
sys.exit(p.returncode)
