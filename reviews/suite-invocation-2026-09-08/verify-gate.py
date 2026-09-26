"""Run only the two L4 modules with and without repository PYTHONPATH."""
from pathlib import Path
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
ARGS = [str(ROOT / 'tools/.venv-mcp/Scripts/python.exe'), '-m', 'unittest',
        'tests.test_lote2_t2_steam', 'tests.test_steam_preflight', '-v']
log = b''
runs = []
for label, include_root in [('A', False), ('B', True)]:
    env = os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env.pop('PYTHONPATH', None)
    if include_root:
        env['PYTHONPATH'] = str(ROOT)
    header = (f'=== {label} ===\nCWD: {ROOT / "tools"}\n'
              f'PYTHONPATH: {str(ROOT) if include_root else "<absent>"}\n'
              f'PYTHONDONTWRITEBYTECODE: 1\n'
              f'Command: {subprocess.list2cmdline(ARGS)}\n\n').encode('utf-8')
    completed = subprocess.run(ARGS, cwd=ROOT / 'tools', env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=60)
    log += header + completed.stdout + f'\nExit code: {completed.returncode}\n\n'.encode()
    (OUT / 'gate.log').write_bytes(log)
    assert (OUT / 'gate.log').read_bytes() == log
    assert (OUT / 'gate.log').stat().st_size == len(log)
    text = completed.stdout.decode('utf-8', errors='replace')
    match = re.search(r'^Ran (\d+) tests? in .+$', text, re.MULTILINE)
    count = int(match.group(1)) if match else None
    identities = sorted(re.findall(r'^(test_\S+ \([^\r\n]+?\)) \.\.\. ', text, re.MULTILINE))
    runs.append((completed.returncode, count, identities))
    print(label, match.group(0).strip() if match else 'Missing unittest summary')
    print('Exit code:', completed.returncode)

assert all(rc == 0 and count and len(ids) == count and len(set(ids)) == count
           for rc, count, ids in runs), runs
assert runs[0] == runs[1], 'Counts or identities differ'
marker = f'GATE PASS: identical {runs[0][1]} test identities; A exit 0; B exit 0\n'
log += marker.encode()
(OUT / 'gate.log').write_bytes(log)
assert (OUT / 'gate.log').read_bytes() == log
assert (OUT / 'gate.log').stat().st_size == len(log)
print(marker, end='')
