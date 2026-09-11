"""Check proposed documentation against the original tests, without applying it."""
from pathlib import Path
import hashlib
import json
import sys
import unittest
from unittest.mock import patch

DELIVERY = Path(__file__).resolve().parent
ROOT = DELIVERY.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from tests.test_install_mcp import PublicToolCountDocsTest

record = json.loads((DELIVERY / "ronda3-docs-counts-PENDING.json").read_text(encoding="utf-8"))
proposed = {}
for name, hashes in record.items():
    path = ROOT / name
    if hashlib.sha256(path.read_bytes()).hexdigest() != hashes["source_sha256"]:
        raise SystemExit("Source changed since proposal: " + name)
    target = DELIVERY / "ronda3-docs-proposed" / name
    if hashlib.sha256(target.read_bytes()).hexdigest() != hashes["proposed_sha256"]:
        raise SystemExit("Proposed text changed: " + name)
    proposed[path] = target.read_text(encoding="utf-8")
original_read = Path.read_text


def read_proposed(path, *args, **kwargs):
    if path in proposed:
        return proposed[path]
    return original_read(path, *args, **kwargs)


suite = unittest.defaultTestLoader.loadTestsFromTestCase(PublicToolCountDocsTest)
with patch.object(Path, "read_text", read_proposed):
    result = unittest.TextTestRunner(verbosity=2).run(suite)
print("Proposed text only; repository documentation was not modified.", flush=True)
raise SystemExit(0 if result.wasSuccessful() else 1)
