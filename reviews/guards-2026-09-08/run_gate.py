"""Run only named N1 modules, sequentially; persist and reread every log update."""
from pathlib import Path
import json
import os
import subprocess
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
PYTHON = ROOT / 'tools/.venv-mcp/Scripts/python.exe'


def put(path, data):
    path.write_bytes(data)
    assert path.read_bytes() == data and path.stat().st_size == len(data), path


def main():
    phase = sys.argv[1]
    assert phase in ('red', 'green', 'regression')
    modules = ['tests.test_guards_bridge']
    if phase == 'regression':
        modules = [name for name in (OUT / 'modules.txt').read_text(encoding='utf-8').splitlines() if name.startswith('tests.test_')]
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    env.pop('GUARDS_SOURCE_DIR', None)
    if phase == 'red':
        env['GUARDS_SOURCE_DIR'] = str(OUT)
    log = OUT / ('red.log' if phase == 'red' else 'green.log')
    records = OUT / 'runs.json'
    runs = json.loads(records.read_text(encoding='utf-8')) if records.exists() else []
    failed = False
    for module in modules:
        args = [str(PYTHON), '-m', 'unittest', module, '-v']
        command = "& '" + str(PYTHON) + "' -m unittest " + module + ' -v'
        setup = f"Set-Location -LiteralPath '{ROOT / 'tools'}'\n$env:PYTHONPATH = '{ROOT}'\n$env:PYTHONDONTWRITEBYTECODE = '1'\n$env:PYTHONIOENCODING = 'utf-8'\n"
        setup += f"$env:GUARDS_SOURCE_DIR = '{OUT}'\n" if phase == 'red' else 'Remove-Item Env:GUARDS_SOURCE_DIR -ErrorAction SilentlyContinue\n'
        result = subprocess.run(args, cwd=ROOT / 'tools', env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
        summary = [line for line in result.stdout.splitlines() if line.startswith(('Ran ', 'OK', 'FAILED', 'NO TESTS RAN'))]
        footer = f'EXIT CODE: {result.returncode}\n'
        data = (f'\nPHASE: {phase}\n{setup}{command}\n{result.stdout}{footer}').encode('utf-8')
        put(log, (log.read_bytes() if log.exists() else b'') + data)
        runs.append(dict(phase=phase, module=module, command=setup+command, summary=summary, exit_code=result.returncode))
        put(records, (json.dumps(runs, indent=2) + '\n').encode('utf-8'))
        print(module + '\n' + '\n'.join(summary) + '\n' + footer, flush=True)
        failed |= result.returncode != 0
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
