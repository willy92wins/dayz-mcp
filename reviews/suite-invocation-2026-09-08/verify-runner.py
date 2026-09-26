"""Exercise the runner's real module, error and empty-module entry points."""
from pathlib import Path
import os
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
runner = str(ROOT / 'tools/run-tests.ps1')
base = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass']
expected_ids = set(re.findall(r'^(test_\S+ \(tests\.test_lote2_t2_steam\.[^\r\n]+?\)) \.\.\. ',
                             (OUT / 'gate.log').read_text(encoding='utf-8'), re.MULTILINE))
assert expected_ids
cases = [
    ('root', ROOT, ['-File', runner, 'tests.test_lote2_t2_steam'], 0, len(expected_ids)),
    ('tools-short-name', ROOT / 'tools', ['-File', runner, 'test_lote2_t2_steam'], 0, len(expected_ids)),
    ('external-cwd', Path(os.environ['TEMP']), ['-File', runner, 'tests.test_lote2_t2_steam'], 0, len(expected_ids)),
    ('missing-module', ROOT, ['-File', runner, 'tests.test_l4_intentionally_missing'], 1, 1),
    ('empty-bootstrap-module', ROOT, ['-File', runner, 'tests.test_000_path'], 5, 0),
]
restore = ("$beforeLocation = (Get-Location).Path; $beforePath = $env:PYTHONPATH; "
           "& '" + runner.replace("'", "''") + "' tests.test_lote2_t2_steam; "
           "$resultCode = $LASTEXITCODE; "
           "if ((Get-Location).Path -ne $beforeLocation) { throw 'CWD not restored' }; "
           "if ($env:PYTHONPATH -ne $beforePath) { throw 'PYTHONPATH not restored' }; "
           "Write-Output 'Caller CWD and PYTHONPATH restored'; exit $resultCode")
cases.append(('caller-restoration', OUT, ['-Command', restore], 0, len(expected_ids)))
log = b''
for label, cwd, args, expected_rc, expected_count in cases:
    env = os.environ.copy()
    env['PYTHONPATH'] = str(OUT / 'deliberately-unrelated-import-root')
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    cmd = base + args
    completed = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=60)
    header = (f'=== {label} ===\nCWD: {cwd}\nPYTHONPATH: {env["PYTHONPATH"]}\n'
              'PYTHONDONTWRITEBYTECODE: 1\n'
              f'Command: {subprocess.list2cmdline(cmd)}\n\n').encode()
    log += header + completed.stdout + f'\nProcess exit code: {completed.returncode}\n\n'.encode()
    (OUT / 'runner.log').write_bytes(log)
    assert (OUT / 'runner.log').read_bytes() == log
    assert (OUT / 'runner.log').stat().st_size == len(log)
    text = completed.stdout.decode('utf-8', errors='replace')
    assert completed.returncode == expected_rc, (label, text)
    assert f'Tests run: {expected_count}' in text, (label, text)
    assert f'Exit code: {expected_rc}' in text, (label, text)
    if expected_rc == 0:
        ids = set(re.findall(r'^(test_\S+ \([^\r\n]+?\)) \.\.\. ', text, re.MULTILINE))
        assert ids == expected_ids, (label, ids)
    for line in text.splitlines():
        if line.startswith(('Ran ', 'OK', 'FAILED', 'Tests run:', 'Exit code:', 'Caller ')):
            print(label + ': ' + line)
log += b'RUNNER PASS: caller locations, module aliases, failure/empty exit codes, restoration\n'
(OUT / 'runner.log').write_bytes(log)
assert (OUT / 'runner.log').read_bytes() == log
assert (OUT / 'runner.log').stat().st_size == len(log)
print('RUNNER PASS')
