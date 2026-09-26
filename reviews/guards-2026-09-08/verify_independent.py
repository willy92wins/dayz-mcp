"""Revert one guard at a time in memory; never overwrite the shared .c files."""
from pathlib import Path
import importlib
import io
import json
import os
import sys
import unittest
from unittest.mock import patch

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
os.environ.pop('GUARDS_SOURCE_DIR', None)
guards = importlib.import_module('tests.test_guards_bridge')
changes = json.loads((OUT / 'changes.json').read_text(encoding='utf-8'))
current = {name: (ROOT / 'addon/scripts/5_Mission' / name).read_text(encoding='utf-8')
           for name in ('MCPBridge.c', 'MCPClientBridge.c')}
expected = {
    'G1': {'test_g1_unavailable_result_logs_once_before_return'},
    'G2': {'test_g2_reserves_next_batch_and_accepted_work_without_dropping_results'},
    'G3': {'test_g3_server_clamps_only_above_sixty_hz', 'test_g3_client_clamps_only_above_sixty_hz'},
    'G4': {'test_g4_restore_checks_game_before_player_and_mission'},
    'G5': {'test_g5_shutdown_latches_before_cleanup_but_allows_first_terminal_post'},
}
log = ''
records = []
for group, files in changes.items():
    variant = dict(current)
    for name, replacements in files.items():
        for old, new in replacements:
            assert variant[name].count(new) == 1, (group, name)
            variant[name] = variant[name].replace(new, old, 1)
    stream = io.StringIO()
    with patch.object(guards, '_source', side_effect=lambda name: guards._clean(variant[name])):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(guards.BridgeGuardsTest))
    failed = {test.id().rsplit('.', 1)[1] for test, _ in result.failures}
    assert not result.errors and failed == expected[group], (group, failed, result.errors)
    summary = f'{group}: {result.testsRun} tests, {len(failed)} expected failures, {result.testsRun-len(failed)} pass; other guards remain green'
    records.append(dict(guard_reverted=group, tests=result.testsRun, failures=sorted(failed), errors=len(result.errors)))
    log += summary + '\n' + stream.getvalue() + '\n'
    data=log.encode('utf-8')
    dest=OUT/'independence.log'
    dest.write_bytes(data)
    assert dest.read_bytes()==data and dest.stat().st_size==len(data)
    print(summary, flush=True)
for name, source in current.items():
    assert (ROOT / 'addon/scripts/5_Mission' / name).read_bytes()==source.encode('utf-8')
dest=OUT/'independence.json'
data=(json.dumps(records,indent=2)+'\n').encode('utf-8')
dest.write_bytes(data)
assert dest.read_bytes()==data and dest.stat().st_size==len(data)
print('INDEPENDENCE PASS=5 FAIL=0; shared sources unchanged; EXIT CODE: 0')
