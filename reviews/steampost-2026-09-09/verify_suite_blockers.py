"""Read-only/local-fixture controls for suite blockers, with optional saved sources."""
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import dayz_mcp

if '--before' in sys.argv:
    for leaf in ('steam_preflight', 'dayz_test_tool'):
        name = 'dayz_mcp.' + leaf
        path = Path(__file__).parent / (leaf + '.py.BEFORE')
        loader = importlib.machinery.SourceFileLoader(name, str(path))
        spec = importlib.util.spec_from_loader(name, loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        setattr(dayz_mcp, leaf, module)
        loader.exec_module(module)
        print('Source:', path)

names = (
    'tests.test_build_native_launcher_policy.FixturePolicyBehaviorTest.test_fixture_accredits_live_request_paths',
    'tests.test_lifecycle_reconcile.LifecycleReconcileTest.test_the_witness_hint_names_the_bundle_and_daemon_versions',
    'tests.test_lifecycle_reconcile.LifecycleReconcileTest.test_both_request_parser_hashes_are_printed_in_the_same_case',
)
result = unittest.TextTestRunner(verbosity=2).run(
    unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)
)
print(f'Tests run: {result.testsRun}; failures: {len(result.failures)}; errors: {len(result.errors)}')
sys.exit(0 if result.wasSuccessful() else 1)
