"""Re-run behavior regressions against saved sources without changing the worktree."""
from contextlib import ExitStack
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from tests import test_steampost_readiness as cases
from tests import test_dayz_test_tool as fixtures


def load_source(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


names = (
    'SteamStartupWaitTests.test_coherent_registry_without_startup_marker_times_out',
    'SteamStartupWaitTests.test_applied_pid_repair_waits_for_startup_marker',
    'SteamStartupWaitTests.test_already_correct_pid_waits_for_startup_marker',
    'SteamStartupWaitTests.test_restart_waits_after_registry_match_and_preserves_branch',
    'SteamStartupWaitTests.test_restart_with_coherent_registry_but_no_marker_fails',
    'SteamBranchEnvelopeTests.test_success_copies_fast_and_restart_branch_without_private_values',
    'SteamBranchEnvelopeTests.test_failed_remediation_copies_both_branches_and_never_launches',
    'SteamBranchEnvelopeTests.test_non_extensible_refusal_preserves_remediation_branch',
    'SteamBranchEnvelopeTests.test_client_replacement_refusal_preserves_remediation_branch',
)
with ExitStack() as stack:
    if '--before' in sys.argv:
        before = Path(__file__).parent
        steam = load_source('_steampost_steam_before', before / 'steam_preflight.py.BEFORE')
        tool = load_source('_steampost_tool_before', before / 'dayz_test_tool.py.BEFORE')
        # Only the test's clock budget is new. The old implementation never reads it.
        stack.enter_context(patch.object(steam, '_STARTUP_WAIT_S', 180.0, create=True))
        stack.enter_context(patch.object(cases, 'sp', steam))
        stack.enter_context(patch.object(cases.fast, 'sp', steam))
        stack.enter_context(patch.object(fixtures, 'dayz_test_tool', tool))
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name, cases)
                               for name in names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f'Tests run: {result.testsRun}')
    print(f'Assertion failures: {len(result.failures)}; errors: {len(result.errors)}')
    sys.exit(0 if result.wasSuccessful() else 1)
