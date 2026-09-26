"""Verify the L4 source delta and preserved helper files without running tests."""
from pathlib import Path
import ast
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
baseline = json.loads((OUT / 'baseline.json').read_text(encoding='utf-8'))
changed = ['tools/tests/test_lote2_t2_steam.py', 'tools/run-tests.ps1', 'GATES.md']
before = (OUT / 'test_lote2_t2_steam.before.txt').read_bytes()
after = (ROOT / changed[0]).read_bytes()
assert before.count(b'from tools.tests') == 2
assert after == before.replace(b'from tools.tests', b'from tests')
print('PASS: test source differs only in the two imports')
before_gate = (OUT / 'GATES.before.txt').read_bytes()
assert (ROOT / 'GATES.md').read_bytes().startswith(before_gate)
print(f'PASS: original {len(before_gate)} bytes of GATES.md preserved')
for rel in ['tools/tests/test_steam_preflight.py', 'tools/tests/test_dayz_test_tool.py']:
    assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == baseline[rel]['sha256']
    print('PASS: helper unchanged:', rel)
for p in [ROOT / changed[0], *OUT.glob('*.py')]:
    ast.parse(p.read_text(encoding='utf-8-sig'), filename=str(p))
print('PASS: changed test and verification scripts parse')
module_tree = ast.parse((ROOT / 'tools/build_native_launcher.py').read_text(encoding='utf-8-sig'))
packaged = next(ast.literal_eval(node.value) for node in module_tree.body
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PACKAGED_MODULES' for t in node.targets))
assert not any(Path(p).name in packaged for p in changed)
print('PASS: no modified source belongs to PACKAGED_MODULES; no reseal required')
for rel in changed:
    raw = (ROOT / rel).read_bytes()
    print(f'{ROOT / rel}: {baseline[rel]["bytes"]} -> {len(raw)} bytes; sha256 {hashlib.sha256(raw).hexdigest()}')
cmd = ['git', 'diff', '--check', '--', changed[0]]
p = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
print('Command:', subprocess.list2cmdline(cmd))
print(p.stdout.decode('utf-8', errors='replace'), end='')
print('Exit code:', p.returncode)
assert p.returncode == 0
print('FILES PASS')
