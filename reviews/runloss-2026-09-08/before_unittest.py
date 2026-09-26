"""Run named tests against saved source bytes, without reverting shared files."""
import hashlib
import importlib.abc
import importlib.util
import sys
import unittest
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
NAMES = {"dayz_mcp." + name for name in ("server", "instance_fence", "loopback", "process_lifecycle")}

class BeforeLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in NAMES:
            return importlib.util.spec_from_loader(fullname, self)
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        filename = module.__name__.split(".")[-1] + ".py"
        saved = OUT / (filename + ".BEFORE")
        data = saved.read_bytes()
        module.__file__ = str(ROOT / "tools/dayz_mcp" / filename)
        print(f"BEFORE {filename} bytes={len(data)} sha256={hashlib.sha256(data).hexdigest()}", flush=True)
        exec(compile(data, module.__file__, "exec"), module.__dict__)

sys.meta_path.insert(0, BeforeLoader())
unittest.main(module=None)
