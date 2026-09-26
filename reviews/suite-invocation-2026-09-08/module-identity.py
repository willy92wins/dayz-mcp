"""Observe duplicate fixture modules while running only the two L4 modules."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
# Discovery imports this source under tests.* before the T2 async test runs.
import tests.test_dayz_test_tool as dayz_fixtures

suite = unittest.main(module=None, argv=["unittest", "tests.test_lote2_t2_steam", "tests.test_steam_preflight", "-v"], exit=False)
for suffix, symbol in [("test_steam_preflight", "_MutableSteamProvider"), ("test_dayz_test_tool", "_Runtime")]:
    canonical = sys.modules["tests." + suffix]
    alias = sys.modules.get("tools.tests." + suffix)
    print(f"{suffix}: alias_present={alias is not None}")
    if alias is not None:
        print(f"  same_source={Path(canonical.__file__).resolve() == Path(alias.__file__).resolve()}")
        print(f"  same_module={canonical is alias}; same_class={getattr(canonical, symbol) is getattr(alias, symbol)}")
        print(f"  same_production_module={canonical.__dict__.get('dayz_test_tool') is alias.__dict__.get('dayz_test_tool')}" if suffix == "test_dayz_test_tool" else f"  same_production_function={canonical.evaluate_steam_session is alias.evaluate_steam_session}")
        canonical._l4_probe = object()
        print(f"  module_state_shared={hasattr(alias, '_l4_probe')}")
        del canonical._l4_probe
aliases = sorted(n for n in sys.modules if n.startswith("tools.tests"))
print(f"tools.tests module keys: {aliases}")
if len(sys.argv) > 1 and sys.argv[1] == "--expect-clean":
    assert not aliases, aliases
assert suite.result.wasSuccessful()
print(f"Identity probe tests run: {suite.result.testsRun}")
