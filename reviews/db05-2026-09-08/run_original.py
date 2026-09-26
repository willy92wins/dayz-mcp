"""Use the saved original in a fresh interpreter; never rewrite the live tree."""
from pathlib import Path
import hashlib
import json
import sys
import unittest

out = Path(__file__).resolve().parent
root = out.parents[1]
sys.path.insert(0, str(root / "tools"))
import dayz_mcp.dayz_test_tool as subject
source = out / "dayz_test_tool.original.py"
original = source.read_bytes()
expected = json.loads((out / "baseline.json").read_bytes())["tools/dayz_mcp/dayz_test_tool.py"]["sha256"]
assert hashlib.sha256(original).hexdigest() == expected
exec(compile(original, str(source), "exec"), subject.__dict__)
print(f"ORIGINAL SUBJECT SHA256: {expected}", flush=True)
unittest.main(module=None, argv=["unittest", *sys.argv[1:]])
