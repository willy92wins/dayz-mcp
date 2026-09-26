"""Load only L6 preimages into this process; never revert the shared tree."""
from pathlib import Path
import hashlib
import importlib.machinery
import importlib.util
import sys
import unittest

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import dayz_mcp
for short in ("host_config", "daemon_policy", "control_client"):
    path = OUT / (short + ".py.before")
    name = "dayz_mcp." + short
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    setattr(dayz_mcp, short, module)
    loader.exec_module(module)
    print(f"BASELINE {name} sha256={hashlib.sha256(path.read_bytes()).hexdigest()}", flush=True)
unittest.main(module=None)
